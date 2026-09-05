"""Synthetic Phase 3 filtering, evidence, ranking, and ablation workflow."""

from __future__ import annotations

import gc
from importlib.resources import as_file
from pathlib import Path

import psutil
from pydantic import BaseModel, ConfigDict

from rare_disease_agent.agents.variant_agent import VariantFilteringAgent
from rare_disease_agent.config import Settings
from rare_disease_agent.llm.base import LLMBackend
from rare_disease_agent.ranking.schemas import (
    PhenotypeAblationResult,
    RankedVariant,
    Track1Metrics,
)
from rare_disease_agent.ranking.scoring import PreliminaryRanker
from rare_disease_agent.reporting.audit import AuditWriter
from rare_disease_agent.reporting.provenance import RunProvenance, software_identity
from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.synthetic.cases import (
    SyntheticCase,
    load_synthetic_case,
    load_synthetic_phenotype_store,
)
from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
from rare_disease_agent.tools.phenotype.tools import PhenotypeToolbox
from rare_disease_agent.tools.variants.agent_tools import VariantToolbox
from rare_disease_agent.tools.variants.vcf import parse_vcf
from rare_disease_agent.workflows.track1_filtering import (
    FilteringRunResult,
    VariantFilteringWorkflow,
)


class Track1RunResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    case: SyntheticCase
    filtering: FilteringRunResult
    ranked_variants: list[RankedVariant]
    metrics: Track1Metrics
    metrics_path: Path
    ranking_path: Path
    ablations_path: Path
    evidence_path: Path
    provenance_path: Path


class Track1EvidenceWorkflow:
    def __init__(
        self,
        *,
        case_name: str,
        backend: LLMBackend,
        settings: Settings,
        run_directory: Path | str,
        run_id: str,
    ) -> None:
        self.case = load_synthetic_case(case_name)
        self.backend = backend
        self.settings = settings
        self.run_directory = Path(run_directory)
        self.run_directory.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id

    def run(self) -> Track1RunResult:
        process = psutil.Process()
        rss_start = process.memory_info().rss / (1024**2)
        software = software_identity()
        parquet_path = self.run_directory / "synthetic_variants.parquet"
        with as_file(self.case.vcf_resource) as vcf_path:
            write_variants_parquet(parse_vcf(vcf_path), parquet_path)

        store = load_synthetic_phenotype_store()
        run_provenance = RunProvenance(
            software=software,
            hpo_data_version=store.data_version,
            hpo_source=store.provenance.source,
            hpo_checksum=store.provenance.checksum,
        )
        audit = AuditWriter(self.run_directory)
        audit.write_event(
            "run_provenance",
            run_id=self.run_id,
            data=run_provenance.model_dump(mode="json"),
        )
        phenotype = PhenotypeToolbox(
            store,
            patient_hpo=self.case.hpo_terms,
            run_id=self.run_id,
            software=software,
        )
        inheritance = InheritanceEvaluator(
            self.case.pedigree,
            run_id=self.run_id,
            minimum_genotype_quality=self.settings.track1.minimum_genotype_quality,
            software=software,
        )
        toolbox = VariantToolbox(
            parquet_path,
            run_id=self.run_id,
            membership_database=self.run_directory / "candidate_membership.duckdb",
            phenotype_toolbox=phenotype,
            inheritance_evaluator=inheritance,
            phenotype_priority_threshold=self.settings.track1.phenotype_priority_threshold,
            inheritance_priority_threshold=self.settings.track1.inheritance_priority_threshold,
        )
        filtering_config = self.settings.agents.variant_filtering.model_copy(
            update={"max_iterations": 16, "max_tool_calls": 24, "minimum_candidate_count": 1}
        )
        filtering = VariantFilteringWorkflow(
            parquet_path=parquet_path,
            agent=VariantFilteringAgent(self.backend),
            run_directory=self.run_directory,
            config=filtering_config,
            run_id=self.run_id,
            causal_variant_id=self.case.causal_variant_id,
            toolbox=toolbox,
        ).run()
        rows = toolbox.candidate_rows(filtering.state.current_branch)
        weights = self.settings.track1.preliminary_scoring.model_dump()
        ranker = PreliminaryRanker(weights=weights, run_id=self.run_id, software=software)
        ablations, rankings = ranker.ablations(
            rows,
            phenotype_scores=toolbox.phenotype_scores,
            inheritance_scores=toolbox.inheritance_scores,
            causal_variant_id=self.case.causal_variant_id,
        )
        ranked = rankings["filtering_phenotype_inheritance"]
        score_rows = [
            {
                "variant_id": item.variant_id,
                "mode": item.mode,
                "rank": item.rank,
                "raw_score": item.raw_score,
                "features": item.features.model_dump(mode="json"),
            }
            for values in rankings.values()
            for item in values
        ]
        toolbox.membership.write_scores(score_rows)

        partial_terms = self.case.hpo_terms[:-1]
        partial_phenotype = PhenotypeToolbox(
            store,
            patient_hpo=partial_terms or self.case.hpo_terms,
            run_id=self.run_id,
            software=software,
        )
        genes = sorted({str(row.get("gene") or "").upper() for row in rows})
        partial_scores = {
            gene: partial_phenotype.get_gene_phenotype_score(gene).score for gene in genes
        }
        partial_ranked = ranker.rank(
            rows,
            phenotype_scores=partial_scores,
            inheritance_scores=toolbox.inheritance_scores,
        )
        full_causal = next(
            (item for item in ranked if item.variant_id == self.case.causal_variant_id), None
        )
        partial_causal = next(
            (item for item in partial_ranked if item.variant_id == self.case.causal_variant_id),
            None,
        )
        full_phenotype_score = toolbox.phenotype_scores.get(self.case.causal_gene, 0)
        partial_phenotype_score = partial_scores.get(self.case.causal_gene, 0)
        phenotype_ablation = PhenotypeAblationResult(
            removed_hpo_term=self.case.hpo_terms[-1] if len(self.case.hpo_terms) > 1 else None,
            full_phenotype_causal_rank=full_causal.rank if full_causal else None,
            partial_phenotype_causal_rank=partial_causal.rank if partial_causal else None,
            full_phenotype_score=full_phenotype_score,
            partial_phenotype_score=partial_phenotype_score,
            phenotype_score_delta=round(full_phenotype_score - partial_phenotype_score, 6),
        )
        causal_rank = full_causal.rank if full_causal else None
        filtering.state.causal_variant_rank = causal_rank
        filtering.metrics.causal_variant_rank = causal_rank
        audit.write_metrics(filtering.metrics)
        novel_rescue_ids = (
            toolbox.candidate_ids("novel-gene-rescue")
            if toolbox.membership.has_branch("novel-gene-rescue")
            else []
        )
        causal_inheritance = [
            item
            for item in toolbox.inheritance_evaluation.evidence
            if item.variant_id == self.case.causal_variant_id
        ]
        true_model_identified = any(
            item.model == self.case.expected_model and item.fit >= 0.8
            for item in causal_inheritance
        )
        false_positive_models = sorted(
            {
                item.model
                for item in causal_inheritance
                if item.model != self.case.expected_model and item.fit >= 0.8
            }
        )
        uncertain_models = sorted(
            {item.model for item in causal_inheritance if 0.4 <= item.fit < 0.8}
        )
        toolbox.membership.close()
        gc.collect()
        rss_end = process.memory_info().rss / (1024**2)
        metrics = Track1Metrics(
            run_id=self.run_id,
            case=self.case.name,
            initial_candidates=filtering.metrics.initial_candidates,
            final_candidates=len(ranked),
            causal_variant_preserved=full_causal is not None,
            causal_variant_rank=causal_rank,
            top_1=causal_rank == 1,
            top_5=causal_rank is not None and causal_rank <= 5,
            top_10=causal_rank is not None and causal_rank <= 10,
            novel_gene_rescue_preserved=bool(novel_rescue_ids),
            expected_inheritance_model=self.case.expected_model,
            true_inheritance_model_identified=true_model_identified,
            false_positive_inheritance_models=false_positive_models,
            uncertain_inheritance_models=uncertain_models,
            ablations=ablations,
            phenotype_ablation=phenotype_ablation,
            process_rss_mb_start=round(rss_start, 2),
            process_rss_mb_end=round(rss_end, 2),
            provenance=run_provenance,
        )
        metrics_path = self.run_directory / "track1_metrics.json"
        ranking_path = self.run_directory / "ranked_candidates.json"
        ablations_path = self.run_directory / "ablations.json"
        evidence_path = self.run_directory / "evidence.json"
        provenance_path = self.run_directory / "provenance.json"
        audit._write_json(metrics_path, metrics)
        audit._write_json(ranking_path, [item.model_dump(mode="json") for item in ranked])
        audit._write_json(
            ablations_path,
            {
                "ranking_ablations": [item.model_dump(mode="json") for item in ablations],
                "phenotype_ablation": phenotype_ablation.model_dump(mode="json"),
            },
        )
        audit._write_json(
            evidence_path,
            {
                "phenotype": [item.model_dump(mode="json") for item in toolbox.phenotype_evidence],
                "inheritance": (
                    toolbox.inheritance_evaluation.model_dump(mode="json")
                    if toolbox.inheritance_evaluation
                    else None
                ),
            },
        )
        audit._write_json(provenance_path, run_provenance)
        audit.write_event(
            "phase3_ranking_completed",
            run_id=self.run_id,
            data={
                "case": self.case.name,
                "causal_variant_rank": causal_rank,
                "ranked_candidate_count": len(ranked),
                "hpo_data_version": store.data_version,
            },
        )
        return Track1RunResult(
            case=self.case,
            filtering=filtering,
            ranked_variants=ranked,
            metrics=metrics,
            metrics_path=metrics_path,
            ranking_path=ranking_path,
            ablations_path=ablations_path,
            evidence_path=evidence_path,
            provenance_path=provenance_path,
        )
