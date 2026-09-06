"""Typed deterministic tools available to the variant planning agent."""

from __future__ import annotations

import json
from itertools import islice
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
from rare_disease_agent.storage.evidence import EvidenceStore
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
        self.evidence_store = EvidenceStore(self.membership._connection, run_id)
        self.phenotype_scores = self.evidence_store.score_mapping(True)
        self.inheritance_scores = self.evidence_store.score_mapping(False)

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
        count = self._require_branch(parameters.branch)
        connection = self.membership._connection
        source = (
            " FROM variants v JOIN candidate_membership m USING(variant_id) "
            "WHERE m.run_id=? AND m.branch=? AND m.active"
        )
        arguments = [self.membership.run_id, parameters.branch]
        data = {"count": count}
        for column, label in (("quality", "quality"), ("allele_frequency", "population_frequency")):
            missing, minimum, maximum = connection.execute(
                f"SELECT count(*) FILTER(WHERE {column} IS NULL), min({column}), max({column})"
                + source,
                arguments,
            ).fetchone()
            data[label] = {"missing": missing, "minimum": minimum, "maximum": maximum}
        for column, label in (
            ("consequence", "consequences"),
            ("clinvar_classification", "clinvar"),
        ):
            data[label] = dict(
                connection.execute(
                    f"SELECT coalesce({column},'missing') AS category, count(*) AS n"
                    + source
                    + " GROUP BY category ORDER BY n DESC, category LIMIT 30",
                    arguments,
                ).fetchall()
            )
        return ToolObservation(
            action="inspect_statistics",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback="Aggregate branch statistics returned; no candidates were changed.",
            data=data,
        )

    def sample_candidate_summary(self, parameters: SampleCandidatesParameters) -> ToolObservation:
        rows = list(islice(self.membership.iter_rows(parameters.branch), parameters.limit))
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
        count = self._require_branch(parameters.branch)
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
        cursor = self.membership._connection.cursor()
        try:
            cursor.execute(
                (
                    "SELECT DISTINCT upper(v.gene) FROM variants v JOIN candidate_membership m "
                    "USING(variant_id) WHERE m.run_id=? AND m.branch=? AND v.gene IS NOT NULL "
                    "ORDER BY 1"
                ),
                [self.membership.run_id, parameters.branch],
            )

            def scores():
                while batch := cursor.fetchmany(256):
                    for row in batch:
                        yield self.phenotype_toolbox.get_gene_phenotype_score(row[0])

            self.evidence_store.write(scores())
        finally:
            cursor.close()
        top = self.membership._connection.execute(
            (
                "SELECT payload FROM evidence WHERE run_id=? AND method='resnik_bma' ORDER "
                "BY score DESC, gene LIMIT ?"
            ),
            [self.membership.run_id, parameters.limit],
        ).fetchall()
        scores = [json.loads(row[0]) for row in top]
        summaries = [
            {
                "gene": item["gene"],
                "score": item["score"],
                "matched_terms": item["matched_terms"],
                "association_known": item["association_count"] > 0,
            }
            for item in scores
        ]
        count = self._require_branch(parameters.branch)
        return ToolObservation(
            action="rank_genes_by_phenotype",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback="Persisted complete phenotype evidence; returned bounded top genes.",
            data={
                "top_genes": summaries,
                "data_version": self.phenotype_toolbox.store.data_version,
                "method": "resnik_bma",
                "provenance": scores[0]["provenance"] if scores else None,
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
                    alternate_fraction=value.get("alternate_fraction"),
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

        def single_evidence():
            for row in self.membership.iter_rows(parameters.branch):
                yield from self.inheritance_evaluator.evaluate(
                    [self._variant_genotypes(row)]
                ).evidence

        self.evidence_store.write(single_evidence())
        # Pair candidates are joined on disk. Even a very large gene never becomes a Python list.
        connection = self.membership._connection
        connection.execute(
            "CREATE TABLE IF NOT EXISTS compound_pairs (run_id VARCHAR, variant_a "
            "VARCHAR, variant_b VARCHAR, payload VARCHAR, PRIMARY KEY(run_id, variant_a,"
            " variant_b))"
        )
        potential_pairs = connection.execute(
            "SELECT coalesce(sum(n*(n-1)/2),0) FROM ("
            "SELECT count(*) n FROM variants v JOIN candidate_membership m USING(variant_id) "
            "WHERE m.run_id=? AND m.branch=? AND v.genotype IN ('0/1','1/0','0|1','1|0') "
            "GROUP BY upper(v.gene), v.chromosome)",
            [self.membership.run_id, parameters.branch],
        ).fetchone()[0]
        if potential_pairs > 100000:
            raise VariantToolError("Compound-pair work budget exceeded; refine candidates first")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT a.variant_id, b.variant_id
                FROM variants a JOIN variants b ON upper(a.gene)=upper(b.gene) AND
a._row_id<b._row_id
                JOIN candidate_membership ma ON ma.variant_id=a.variant_id
                JOIN candidate_membership mb ON mb.variant_id=b.variant_id
                WHERE ma.run_id=? AND mb.run_id=? AND ma.branch=? AND mb.branch=?
                AND a.genotype IN ('0/1','1/0','0|1','1|0')
                AND b.genotype IN ('0/1','1/0','0|1','1|0')
                ORDER BY a._row_id, b._row_id""",
                [
                    self.membership.run_id,
                    self.membership.run_id,
                    parameters.branch,
                    parameters.branch,
                ],
            )
            while pairs := cursor.fetchmany(128):
                for first, second in pairs:
                    rows = connection.execute(
                        "SELECT * FROM variants WHERE variant_id IN (?, ?) ORDER BY _row_id",
                        [first, second],
                    )
                    columns = [item[0] for item in rows.description]
                    variants = [
                        self._variant_genotypes(dict(zip(columns, row, strict=True)))
                        for row in rows.fetchall()
                    ]
                    result = self.inheritance_evaluator.evaluate(variants)
                    for pair in result.compound_heterozygous_pairs:
                        connection.execute(
                            "INSERT OR REPLACE INTO compound_pairs VALUES (?, ?, ?, ?)",
                            [
                                self.membership.run_id,
                                pair.variant_a,
                                pair.variant_b,
                                pair.model_dump_json(),
                            ],
                        )
                    self.evidence_store.write(
                        item
                        for item in result.evidence
                        if item.model in {"compound_heterozygous", "autosomal_recessive"}
                    )
        finally:
            cursor.close()
        summaries = []
        for model, strong, uncertain in connection.execute(
            (
                "SELECT method, count(*) FILTER (WHERE score>=0.8), count(*) FILTER(WHERE "
                "score>=0.4 AND score<0.8) FROM evidence WHERE run_id=? AND "
                "method<>'resnik_bma' GROUP BY method ORDER BY method"
            ),
            [self.membership.run_id],
        ).fetchall():
            top = connection.execute(
                (
                    "SELECT variant_id FROM evidence WHERE run_id=? AND method=? AND score>0 "
                    "ORDER BY score DESC, variant_id LIMIT 5"
                ),
                [self.membership.run_id, model],
            ).fetchall()
            summaries.append(
                {
                    "model": model,
                    "strong_matches": strong,
                    "uncertain_matches": uncertain,
                    "top_variant_ids": [row[0] for row in top],
                }
            )
        pairs = connection.execute(
            (
                "SELECT payload FROM compound_pairs WHERE run_id=? ORDER BY variant_a, "
                "variant_b LIMIT 10"
            ),
            [self.membership.run_id],
        ).fetchall()
        count = self._require_branch(parameters.branch)
        return ToolObservation(
            action="evaluate_inheritance",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback=(
                "Persisted inheritance evidence and uncertain pairs; returned bounded summaries."
            ),
            data={
                "summaries": summaries,
                "compound_heterozygous_pairs": [json.loads(row[0]) for row in pairs],
            },
        )

    def create_evidence_branches(
        self, parameters: CreateEvidenceBranchesParameters
    ) -> ToolObservation:
        if not self.phenotype_scores or not self.inheritance_scores:
            raise VariantToolError(
                "Phenotype and inheritance evidence must be evaluated before branch creation."
            )
        run_id = self.membership.run_id
        specifications = [
            (
                parameters.phenotype_branch,
                "phenotype_score_priority",
                (
                    "EXISTS (SELECT 1 FROM evidence e WHERE e.run_id=? AND e.method='resnik_bma'"
                    " AND e.gene=upper(v.gene) AND e.score>=?)"
                ),
                [run_id, self.phenotype_priority_threshold],
            ),
            (
                parameters.inheritance_branch,
                "inheritance_fit_priority",
                (
                    "EXISTS (SELECT 1 FROM evidence e WHERE e.run_id=? AND "
                    "e.method<>'resnik_bma' AND e.variant_id=v.variant_id AND e.score>=?)"
                ),
                [run_id, self.inheritance_priority_threshold],
            ),
            (
                parameters.novel_gene_branch,
                "no_known_hpo_association_rescue",
                (
                    "NOT EXISTS (SELECT 1 FROM evidence e WHERE e.run_id=? AND "
                    "e.method='resnik_bma' AND e.gene=upper(v.gene) AND e.known)"
                ),
                [run_id],
            ),
        ]
        try:
            with self.membership.transaction():
                for target, operation, predicate, arguments in specifications:
                    self.membership.filter_branch(
                        source=parameters.branch,
                        target=target,
                        operation=operation,
                        predicate_sql=predicate,
                        predicate_parameters=arguments,
                    )
        except (KeyError, ValueError) as exc:
            raise VariantToolError(str(exc)) from exc
        count = self._require_branch(parameters.branch)
        return ToolObservation(
            action="create_evidence_branches",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback="Created persistent evidence and novel-gene rescue branches.",
            data={
                "phenotype_branch": parameters.phenotype_branch,
                "phenotype_count": self.membership.count(parameters.phenotype_branch),
                "inheritance_branch": parameters.inheritance_branch,
                "inheritance_count": self.membership.count(parameters.inheritance_branch),
                "novel_gene_branch": parameters.novel_gene_branch,
                "novel_gene_count": self.membership.count(parameters.novel_gene_branch),
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
