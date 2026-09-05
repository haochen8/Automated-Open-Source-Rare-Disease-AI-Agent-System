"""Typed deterministic tools available to the variant planning agent."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from rare_disease_agent.agents.schemas import (
    AgentDecision,
    BranchParameters,
    CandidateSetSummary,
    CreateEvidenceBranchesParameters,
    CreateRescueBranchParameters,
    EvaluateInheritanceParameters,
    FilterClinvarParameters,
    FilterConsequenceParameters,
    FilterFrequencyParameters,
    FilterGeneParameters,
    FilterQualityParameters,
    GetPatientHPOSummaryParameters,
    MergeBranchesParameters,
    RankGenesByPhenotypeParameters,
    SampleCandidatesParameters,
    StopParameters,
    ToolObservation,
)
from rare_disease_agent.storage.membership import CandidateMembershipStore
from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
from rare_disease_agent.tools.inheritance.schemas import GenotypeCall, VariantGenotypes
from rare_disease_agent.tools.phenotype.tools import PhenotypeToolbox


class VariantToolError(RuntimeError):
    pass


class VariantToolbox:
    """Typed deterministic tools over persistent DuckDB branch membership."""

    def __init__(
        self,
        parquet_path: Path | str,
        *,
        run_id: str = "toolbox",
        membership_database: Path | str | None = None,
        phenotype_toolbox: PhenotypeToolbox | None = None,
        inheritance_evaluator: InheritanceEvaluator | None = None,
        phenotype_priority_threshold: float = 0.35,
        inheritance_priority_threshold: float = 0.8,
    ) -> None:
        self.membership = CandidateMembershipStore(
            parquet_path, run_id=run_id, database_path=membership_database
        )
        self.phenotype_toolbox = phenotype_toolbox
        self.inheritance_evaluator = inheritance_evaluator
        self.phenotype_priority_threshold = phenotype_priority_threshold
        self.inheritance_priority_threshold = inheritance_priority_threshold
        self.phenotype_scores: dict[str, float] = {}
        self.phenotype_evidence = []
        self.inheritance_scores: dict[str, float] = {}
        self.inheritance_evaluation = None

    @property
    def patient_hpo_count(self) -> int:
        return len(self.phenotype_toolbox.patient_terms) if self.phenotype_toolbox else 0

    @property
    def pedigree_available(self) -> bool:
        return self.inheritance_evaluator is not None

    def _require_branch(self, branch: str) -> int:
        try:
            return self.membership.count(branch)
        except KeyError as exc:
            raise VariantToolError(f"Unknown candidate branch: {branch}") from exc

    def delete_branch(self, branch: str) -> None:
        try:
            self.membership.delete_branch(branch)
        except ValueError as exc:
            raise VariantToolError(str(exc)) from exc

    def candidate_summaries(self) -> list[CandidateSetSummary]:
        return self.membership.summaries()

    def candidate_ids(self, branch: str) -> list[str]:
        try:
            return self.membership.candidate_ids(branch)
        except KeyError as exc:
            raise VariantToolError(f"Unknown candidate branch: {branch}") from exc

    def candidate_rows(self, branch: str) -> list[dict[str, Any]]:
        try:
            return self.membership.candidate_rows(branch)
        except KeyError as exc:
            raise VariantToolError(f"Unknown candidate branch: {branch}") from exc

    def count_variants(self, parameters: BranchParameters) -> ToolObservation:
        count = self._require_branch(parameters.branch)
        return ToolObservation(
            action="count_variants",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback=f"Branch {parameters.branch!r} contains {count} candidates.",
            data={"count": count},
        )

    def get_variant_statistics(self, parameters: BranchParameters) -> ToolObservation:
        rows = self.candidate_rows(parameters.branch)
        qualities = [float(row["quality"]) for row in rows if row["quality"] is not None]
        frequencies = [
            float(row["allele_frequency"]) for row in rows if row["allele_frequency"] is not None
        ]
        consequence_counts = Counter(str(row["consequence"] or "missing") for row in rows)
        clinvar_counts = Counter(str(row["clinvar_classification"] or "missing") for row in rows)
        data = {
            "count": len(rows),
            "quality": {
                "missing": sum(row["quality"] is None for row in rows),
                "minimum": min(qualities) if qualities else None,
                "maximum": max(qualities) if qualities else None,
            },
            "population_frequency": {
                "missing": sum(row["allele_frequency"] is None for row in rows),
                "minimum": min(frequencies) if frequencies else None,
                "maximum": max(frequencies) if frequencies else None,
            },
            "consequences": dict(sorted(consequence_counts.items())),
            "clinvar": dict(sorted(clinvar_counts.items())),
        }
        count = len(rows)
        return ToolObservation(
            action="inspect_statistics",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback="Aggregate branch statistics returned; no candidates were changed.",
            data=data,
        )

    def sample_candidate_summary(self, parameters: SampleCandidatesParameters) -> ToolObservation:
        rows = self.candidate_rows(parameters.branch)
        samples = [
            {
                "gene": row["gene"],
                "consequence": row["consequence"],
                "quality": row["quality"],
                "allele_frequency": row["allele_frequency"],
                "clinvar_classification": row["clinvar_classification"],
            }
            for row in rows[: parameters.limit]
        ]
        count = len(rows)
        return ToolObservation(
            action="sample_candidates",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback=f"Returned {len(samples)} compact, non-coordinate candidate summaries.",
            data={"samples": samples},
        )

    def _filter_branch(
        self,
        *,
        source: str,
        target: str,
        operation: str,
        predicate_sql: str,
        predicate_parameters: list[object],
        action: str,
    ) -> ToolObservation:
        before_count = self._require_branch(source)
        try:
            self.membership.filter_branch(
                source=source,
                target=target,
                predicate_sql=predicate_sql,
                predicate_parameters=predicate_parameters,
                operation=operation,
            )
        except (KeyError, ValueError) as exc:
            raise VariantToolError(str(exc)) from exc
        after_count = self.membership.count(target)
        return ToolObservation.model_validate(
            {
                "action": action,
                "branch": target,
                "before_count": before_count,
                "after_count": after_count,
                "feedback": (
                    f"Created reversible branch {target!r} from {source!r}; "
                    f"retained {after_count} of {before_count} candidates."
                ),
                "data": {"source_branch": source},
            }
        )

    def filter_by_quality(self, parameters: FilterQualityParameters) -> ToolObservation:
        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation=f"quality>={parameters.minimum_quality:g}",
            predicate_sql="v.quality IS NULL OR v.quality >= ?",
            predicate_parameters=[parameters.minimum_quality],
            action="filter_quality",
        )

    def filter_by_population_frequency(
        self, parameters: FilterFrequencyParameters
    ) -> ToolObservation:
        predicate = "v.allele_frequency IS NULL OR v.allele_frequency <= ?"
        if parameters.preserve_pathogenic_clinvar:
            predicate += " OR lower(coalesce(v.clinvar_classification, '')) LIKE '%pathogenic%'"
        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation=f"af<={parameters.maximum_allele_frequency:g}",
            predicate_sql=predicate,
            predicate_parameters=[parameters.maximum_allele_frequency],
            action="filter_frequency",
        )

    def filter_by_consequence(self, parameters: FilterConsequenceParameters) -> ToolObservation:
        allowed = set(parameters.allowed_consequences)
        placeholders = ", ".join("?" for _ in allowed)
        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation="consequence_in:" + ",".join(sorted(allowed)),
            predicate_sql=f"v.consequence IN ({placeholders})",
            predicate_parameters=sorted(allowed),
            action="filter_consequence",
        )

    def filter_by_gene(self, parameters: FilterGeneParameters) -> ToolObservation:
        genes = {gene.upper() for gene in parameters.genes}
        placeholders = ", ".join("?" for _ in genes)
        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation="gene_in:" + ",".join(sorted(genes)),
            predicate_sql=f"upper(coalesce(v.gene, '')) IN ({placeholders})",
            predicate_parameters=sorted(genes),
            action="filter_gene",
        )

    def filter_by_clinvar(self, parameters: FilterClinvarParameters) -> ToolObservation:
        classifications = {value.lower() for value in parameters.classifications}
        placeholders = ", ".join("?" for _ in classifications)
        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation="clinvar_in:" + ",".join(sorted(classifications)),
            predicate_sql=f"lower(coalesce(v.clinvar_classification, '')) IN ({placeholders})",
            predicate_parameters=sorted(classifications),
            action="filter_clinvar",
        )

    def create_rescue_branch(self, parameters: CreateRescueBranchParameters) -> ToolObservation:
        count = self._require_branch(parameters.source_branch)
        try:
            self.membership.copy_branch(
                source=parameters.source_branch,
                target=parameters.target_branch,
                operation="rescue_copy",
            )
        except (KeyError, ValueError) as exc:
            raise VariantToolError(str(exc)) from exc
        return ToolObservation(
            action="create_rescue_branch",
            branch=parameters.target_branch,
            before_count=count,
            after_count=count,
            feedback=(
                f"Created reversible rescue branch {parameters.target_branch!r} "
                f"with all {count} source candidates."
            ),
            data={"source_branch": parameters.source_branch},
        )

    def combine_candidate_sets(self, parameters: MergeBranchesParameters) -> ToolObservation:
        before_count = sum(self._require_branch(branch) for branch in parameters.branches)
        try:
            self.membership.union_branches(
                branches=parameters.branches,
                target=parameters.target_branch,
                operation="union:" + ",".join(parameters.branches),
            )
        except (KeyError, ValueError) as exc:
            raise VariantToolError(str(exc)) from exc
        after_count = self.membership.count(parameters.target_branch)
        return ToolObservation(
            action="merge_branches",
            branch=parameters.target_branch,
            before_count=before_count,
            after_count=after_count,
            feedback=(
                f"Unioned {len(parameters.branches)} branches into "
                f"{parameters.target_branch!r} with {after_count} unique candidates."
            ),
            data={"source_branches": parameters.branches},
        )

    def get_patient_hpo_summary(
        self, parameters: GetPatientHPOSummaryParameters
    ) -> ToolObservation:
        del parameters
        if self.phenotype_toolbox is None:
            raise VariantToolError("No phenotype evidence is available for this run.")
        summary = self.phenotype_toolbox.get_patient_hpo_summary()
        count = self._require_branch("all")
        return ToolObservation(
            action="get_patient_hpo_summary",
            branch="all",
            before_count=count,
            after_count=count,
            feedback="Returned validated HPO identifiers and normalization warnings.",
            data=summary.model_dump(mode="json"),
        )

    def rank_genes_by_phenotype(
        self, parameters: RankGenesByPhenotypeParameters
    ) -> ToolObservation:
        if self.phenotype_toolbox is None:
            raise VariantToolError("No phenotype evidence is available for this run.")
        rows = self.candidate_rows(parameters.branch)
        genes = sorted({str(row["gene"]).upper() for row in rows if row.get("gene")})
        scores = [self.phenotype_toolbox.get_gene_phenotype_score(gene) for gene in genes]
        self.phenotype_evidence = scores
        self.phenotype_scores = {score.gene: score.score for score in scores}
        summaries = self.phenotype_toolbox.rank_genes_by_phenotype(genes, limit=parameters.limit)
        count = len(rows)
        return ToolObservation(
            action="rank_genes_by_phenotype",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback="Ranked genes deterministically using ontology-aware phenotype evidence.",
            data={
                "top_genes": [item.model_dump(mode="json") for item in summaries],
                "data_version": self.phenotype_toolbox.store.data_version,
                "method": "resnik_bma",
                "provenance": (scores[0].provenance.model_dump(mode="json") if scores else None),
            },
        )

    def _variant_genotypes(self, row: dict[str, Any]) -> VariantGenotypes:
        calls: list[GenotypeCall] = []
        raw_calls = row.get("genotype_calls_json")
        if raw_calls:
            parsed = json.loads(str(raw_calls))
            calls = [
                GenotypeCall(
                    individual_id=individual_id,
                    genotype=value.get("genotype"),
                    quality=value.get("quality"),
                )
                for individual_id, value in parsed.items()
            ]
        elif self.inheritance_evaluator is not None:
            calls = [
                GenotypeCall(
                    individual_id=self.inheritance_evaluator.pedigree.proband_id,
                    genotype=row.get("genotype"),
                )
            ]
        return VariantGenotypes(
            variant_id=str(row["variant_id"]),
            gene=str(row.get("gene") or "UNKNOWN"),
            chromosome=str(row.get("chromosome") or ""),
            calls=calls,
        )

    def evaluate_inheritance(self, parameters: EvaluateInheritanceParameters) -> ToolObservation:
        if self.inheritance_evaluator is None:
            raise VariantToolError("No pedigree evidence is available for this run.")
        rows = self.candidate_rows(parameters.branch)
        variants = [self._variant_genotypes(row) for row in rows]
        evaluation = self.inheritance_evaluator.evaluate(variants)
        self.inheritance_evaluation = evaluation
        scores: dict[str, float] = {}
        for item in evaluation.evidence:
            scores[item.variant_id] = max(scores.get(item.variant_id, 0), item.fit)
        self.inheritance_scores = scores
        count = len(rows)
        return ToolObservation(
            action="evaluate_inheritance",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback=(
                "Evaluated deterministic inheritance hypotheses; uncertain phase was retained."
            ),
            data={
                "summaries": [item.model_dump(mode="json") for item in evaluation.summaries],
                "compound_heterozygous_pairs": [
                    item.model_dump(mode="json")
                    for item in evaluation.compound_heterozygous_pairs[:10]
                ],
            },
        )

    def create_evidence_branches(
        self, parameters: CreateEvidenceBranchesParameters
    ) -> ToolObservation:
        if self.phenotype_toolbox is None or not self.phenotype_scores:
            raise VariantToolError("Phenotype evidence must be evaluated before branch creation.")
        if self.inheritance_evaluator is None or not self.inheritance_scores:
            raise VariantToolError("Inheritance evidence must be evaluated before branch creation.")
        rows = self.candidate_rows(parameters.branch)
        phenotype_ids = [
            str(row["variant_id"])
            for row in rows
            if self.phenotype_scores.get(str(row.get("gene") or "").upper(), 0)
            >= self.phenotype_priority_threshold
        ]
        inheritance_ids = [
            str(row["variant_id"])
            for row in rows
            if self.inheritance_scores.get(str(row["variant_id"]), 0)
            >= self.inheritance_priority_threshold
        ]
        novel_ids = [
            str(row["variant_id"])
            for row in rows
            if not self.phenotype_toolbox.store.gene_associations(str(row.get("gene") or "UNKNOWN"))
        ]
        try:
            with self.membership.transaction():
                self.membership.branch_from_ids(
                    source=parameters.branch,
                    target=parameters.phenotype_branch,
                    variant_ids=phenotype_ids,
                    operation="phenotype_score_priority",
                )
                self.membership.branch_from_ids(
                    source=parameters.branch,
                    target=parameters.inheritance_branch,
                    variant_ids=inheritance_ids,
                    operation="inheritance_fit_priority",
                )
                self.membership.branch_from_ids(
                    source=parameters.branch,
                    target=parameters.novel_gene_branch,
                    variant_ids=novel_ids,
                    operation="no_known_hpo_association_rescue",
                )
        except (KeyError, ValueError) as exc:
            raise VariantToolError(str(exc)) from exc
        count = len(rows)
        return ToolObservation(
            action="create_evidence_branches",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback=(
                "Created phenotype, inheritance, and non-destructive novel-gene rescue branches."
            ),
            data={
                "phenotype_branch": parameters.phenotype_branch,
                "phenotype_count": len(phenotype_ids),
                "inheritance_branch": parameters.inheritance_branch,
                "inheritance_count": len(inheritance_ids),
                "novel_gene_branch": parameters.novel_gene_branch,
                "novel_gene_count": len(novel_ids),
            },
        )

    def stop(self, parameters: StopParameters) -> ToolObservation:
        count = self._require_branch(parameters.branch)
        return ToolObservation(
            action="stop",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback=parameters.reason,
        )

    def execute(self, decision: AgentDecision) -> ToolObservation:
        parameters = decision.typed_parameters()
        if isinstance(parameters, SampleCandidatesParameters):
            return self.sample_candidate_summary(parameters)
        if decision.action == "inspect_statistics":
            return self.get_variant_statistics(BranchParameters.model_validate(parameters))
        if decision.action == "count_variants":
            return self.count_variants(BranchParameters.model_validate(parameters))
        if isinstance(parameters, FilterQualityParameters):
            return self.filter_by_quality(parameters)
        if isinstance(parameters, FilterFrequencyParameters):
            return self.filter_by_population_frequency(parameters)
        if isinstance(parameters, FilterConsequenceParameters):
            return self.filter_by_consequence(parameters)
        if isinstance(parameters, FilterGeneParameters):
            return self.filter_by_gene(parameters)
        if isinstance(parameters, FilterClinvarParameters):
            return self.filter_by_clinvar(parameters)
        if isinstance(parameters, CreateRescueBranchParameters):
            return self.create_rescue_branch(parameters)
        if isinstance(parameters, GetPatientHPOSummaryParameters):
            return self.get_patient_hpo_summary(parameters)
        if isinstance(parameters, RankGenesByPhenotypeParameters):
            return self.rank_genes_by_phenotype(parameters)
        if isinstance(parameters, EvaluateInheritanceParameters):
            return self.evaluate_inheritance(parameters)
        if isinstance(parameters, CreateEvidenceBranchesParameters):
            return self.create_evidence_branches(parameters)
        if isinstance(parameters, MergeBranchesParameters):
            return self.combine_candidate_sets(parameters)
        if isinstance(parameters, StopParameters):
            return self.stop(parameters)
        raise VariantToolError(f"Unsupported typed action: {decision.action}")
