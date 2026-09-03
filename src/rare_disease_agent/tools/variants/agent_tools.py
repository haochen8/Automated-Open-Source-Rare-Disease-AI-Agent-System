"""Typed deterministic tools available to the variant planning agent."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb

from rare_disease_agent.agents.schemas import (
    AgentDecision,
    BranchParameters,
    CandidateSetSummary,
    CreateRescueBranchParameters,
    FilterClinvarParameters,
    FilterConsequenceParameters,
    FilterFrequencyParameters,
    FilterGeneParameters,
    FilterQualityParameters,
    MergeBranchesParameters,
    SampleCandidatesParameters,
    StopParameters,
    ToolObservation,
)


class VariantToolError(RuntimeError):
    pass


class VariantToolbox:
    """In-memory branch index over a Phase 1 Parquet file.

    Phase 2 is intentionally synthetic-only. Branch membership is held as row indexes; a later
    patient-scale implementation can replace this store with persisted DuckDB tables without
    changing the typed tool API or agent workflow.
    """

    def __init__(self, parquet_path: Path | str) -> None:
        path = Path(parquet_path)
        if not path.is_file():
            raise FileNotFoundError(f"Parquet dataset does not exist: {path}")
        connection = duckdb.connect(":memory:")
        try:
            relation = connection.from_parquet(str(path.resolve()))
            columns = list(relation.columns)
            self._rows = [dict(zip(columns, row, strict=True)) for row in relation.fetchall()]
        finally:
            connection.close()
        self._branches: dict[str, set[int]] = {"all": set(range(len(self._rows)))}
        self._parents: dict[str, str | None] = {"all": None}
        self._operations: dict[str, list[str]] = {"all": []}

    def _require_branch(self, branch: str) -> set[int]:
        try:
            return self._branches[branch]
        except KeyError as exc:
            raise VariantToolError(f"Unknown candidate branch: {branch}") from exc

    def _new_branch(
        self, *, source: str | None, target: str, indexes: set[int], operation: str
    ) -> None:
        if target in self._branches:
            raise VariantToolError(f"Candidate branch already exists: {target}")
        self._branches[target] = indexes
        self._parents[target] = source
        parent_operations = self._operations[source] if source else []
        self._operations[target] = [*parent_operations, operation]

    def delete_branch(self, branch: str) -> None:
        if branch == "all":
            raise VariantToolError("The all branch cannot be deleted.")
        self._branches.pop(branch, None)
        self._parents.pop(branch, None)
        self._operations.pop(branch, None)

    def candidate_summaries(self) -> list[CandidateSetSummary]:
        return [
            CandidateSetSummary(
                branch=name,
                count=len(indexes),
                parent_branch=self._parents[name],
                operations=self._operations[name],
            )
            for name, indexes in sorted(self._branches.items())
        ]

    def candidate_ids(self, branch: str) -> list[str]:
        indexes = self._require_branch(branch)
        return [str(self._rows[index]["variant_id"]) for index in sorted(indexes)]

    def candidate_rows(self, branch: str) -> list[dict[str, Any]]:
        indexes = self._require_branch(branch)
        return [dict(self._rows[index]) for index in sorted(indexes)]

    def count_variants(self, parameters: BranchParameters) -> ToolObservation:
        count = len(self._require_branch(parameters.branch))
        return ToolObservation(
            action="count_variants",
            branch=parameters.branch,
            before_count=count,
            after_count=count,
            feedback=f"Branch {parameters.branch!r} contains {count} candidates.",
            data={"count": count},
        )

    def get_variant_statistics(self, parameters: BranchParameters) -> ToolObservation:
        indexes = self._require_branch(parameters.branch)
        rows = [self._rows[index] for index in indexes]
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
        indexes = sorted(self._require_branch(parameters.branch))[: parameters.limit]
        samples = [
            {
                "gene": self._rows[index]["gene"],
                "consequence": self._rows[index]["consequence"],
                "quality": self._rows[index]["quality"],
                "allele_frequency": self._rows[index]["allele_frequency"],
                "clinvar_classification": self._rows[index]["clinvar_classification"],
            }
            for index in indexes
        ]
        count = len(self._require_branch(parameters.branch))
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
        predicate: Callable[[dict[str, Any]], bool],
        action: str,
    ) -> ToolObservation:
        source_indexes = self._require_branch(source)
        result = {index for index in source_indexes if predicate(self._rows[index])}
        self._new_branch(source=source, target=target, indexes=result, operation=operation)
        return ToolObservation.model_validate(
            {
                "action": action,
                "branch": target,
                "before_count": len(source_indexes),
                "after_count": len(result),
                "feedback": (
                    f"Created reversible branch {target!r} from {source!r}; "
                    f"retained {len(result)} of {len(source_indexes)} candidates."
                ),
                "data": {"source_branch": source},
            }
        )

    def filter_by_quality(self, parameters: FilterQualityParameters) -> ToolObservation:
        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation=f"quality>={parameters.minimum_quality:g}",
            predicate=lambda row: (
                row["quality"] is None or float(row["quality"]) >= parameters.minimum_quality
            ),
            action="filter_quality",
        )

    def filter_by_population_frequency(
        self, parameters: FilterFrequencyParameters
    ) -> ToolObservation:
        def keep(row: dict[str, Any]) -> bool:
            frequency = row["allele_frequency"]
            classification = str(row["clinvar_classification"] or "").lower()
            clinvar_rescue = (
                parameters.preserve_pathogenic_clinvar and "pathogenic" in classification
            )
            return (
                frequency is None
                or float(frequency) <= parameters.maximum_allele_frequency
                or clinvar_rescue
            )

        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation=f"af<={parameters.maximum_allele_frequency:g}",
            predicate=keep,
            action="filter_frequency",
        )

    def filter_by_consequence(self, parameters: FilterConsequenceParameters) -> ToolObservation:
        allowed = set(parameters.allowed_consequences)
        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation="consequence_in:" + ",".join(sorted(allowed)),
            predicate=lambda row: row["consequence"] in allowed,
            action="filter_consequence",
        )

    def filter_by_gene(self, parameters: FilterGeneParameters) -> ToolObservation:
        genes = {gene.upper() for gene in parameters.genes}
        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation="gene_in:" + ",".join(sorted(genes)),
            predicate=lambda row: str(row["gene"] or "").upper() in genes,
            action="filter_gene",
        )

    def filter_by_clinvar(self, parameters: FilterClinvarParameters) -> ToolObservation:
        classifications = {value.lower() for value in parameters.classifications}
        return self._filter_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            operation="clinvar_in:" + ",".join(sorted(classifications)),
            predicate=lambda row: (
                str(row["clinvar_classification"] or "").lower() in classifications
            ),
            action="filter_clinvar",
        )

    def create_rescue_branch(self, parameters: CreateRescueBranchParameters) -> ToolObservation:
        source_indexes = self._require_branch(parameters.source_branch)
        self._new_branch(
            source=parameters.source_branch,
            target=parameters.target_branch,
            indexes=set(source_indexes),
            operation="rescue_copy",
        )
        count = len(source_indexes)
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
        branch_sets = [self._require_branch(branch) for branch in parameters.branches]
        union = set().union(*branch_sets)
        before_count = sum(len(indexes) for indexes in branch_sets)
        self._new_branch(
            source=None,
            target=parameters.target_branch,
            indexes=union,
            operation="union:" + ",".join(parameters.branches),
        )
        return ToolObservation(
            action="merge_branches",
            branch=parameters.target_branch,
            before_count=before_count,
            after_count=len(union),
            feedback=(
                f"Unioned {len(parameters.branches)} branches into "
                f"{parameters.target_branch!r} with {len(union)} unique candidates."
            ),
            data={"source_branches": parameters.branches},
        )

    def stop(self, parameters: StopParameters) -> ToolObservation:
        count = len(self._require_branch(parameters.branch))
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
        if isinstance(parameters, MergeBranchesParameters):
            return self.combine_candidate_sets(parameters)
        if isinstance(parameters, StopParameters):
            return self.stop(parameters)
        raise VariantToolError(f"Unsupported typed action: {decision.action}")
