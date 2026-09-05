"""Typed phenotype evidence facade exposed to the planning workflow."""

from __future__ import annotations

from rare_disease_agent.reporting.provenance import SoftwareIdentity, evidence_provenance
from rare_disease_agent.tools.phenotype.associations import PhenotypeAssociationStore
from rare_disease_agent.tools.phenotype.normalize import normalize_hpo_terms
from rare_disease_agent.tools.phenotype.schemas import (
    DiseaseAssociationsResult,
    GeneAssociationsResult,
    GenePhenotypeSummary,
    PatientHPOSummary,
    PhenotypeScore,
)
from rare_disease_agent.tools.phenotype.similarity import PhenotypeScorer

TOOL_VERSION = "phenotype-tools-v1"


class PhenotypeToolbox:
    def __init__(
        self,
        store: PhenotypeAssociationStore,
        *,
        patient_hpo: list[str],
        run_id: str,
        software: SoftwareIdentity | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.software = software
        self.normalized = normalize_hpo_terms(patient_hpo, store.ontology)
        self.scorer = PhenotypeScorer(store, run_id=run_id, software=software)

    @property
    def patient_terms(self) -> list[str]:
        return [term.hpo_id for term in self.normalized.terms]

    def get_patient_hpo_summary(self) -> PatientHPOSummary:
        return PatientHPOSummary(
            normalized=self.normalized,
            term_count=len(self.normalized.terms),
            data_version=self.store.data_version,
            provenance=evidence_provenance(
                run_id=self.run_id,
                tool_version=TOOL_VERSION,
                data_version=self.store.data_version,
                method="identifier_normalization",
                parameters={"submitted_term_count": len(self.normalized.terms)},
                software=self.software,
            ),
        )

    def get_gene_phenotype_score(self, gene: str) -> PhenotypeScore:
        return self.scorer.score_gene_phenotype(self.patient_terms, gene)

    def rank_genes_by_phenotype(
        self, genes: list[str], *, limit: int = 10
    ) -> list[GenePhenotypeSummary]:
        unique_genes = sorted({gene.upper() for gene in genes if gene})
        scores = [self.get_gene_phenotype_score(gene) for gene in unique_genes]
        scores.sort(key=lambda item: (-item.score, item.gene))
        return [
            GenePhenotypeSummary(
                gene=score.gene,
                score=score.score,
                matched_terms=score.matched_terms,
                association_known=score.association_count > 0,
            )
            for score in scores[:limit]
        ]

    def get_gene_hpo_associations(self, gene: str) -> GeneAssociationsResult:
        return GeneAssociationsResult(
            gene=gene.upper(),
            associations=self.store.gene_associations(gene),
            data_version=self.store.data_version,
            provenance=evidence_provenance(
                run_id=self.run_id,
                tool_version=TOOL_VERSION,
                data_version=self.store.data_version,
                method="gene_association_lookup",
                parameters={"gene": gene.upper()},
                software=self.software,
            ),
        )

    def get_disease_hpo_associations(self, disease_id: str) -> DiseaseAssociationsResult:
        return DiseaseAssociationsResult(
            disease_id=disease_id,
            associations=self.store.disease_associations(disease_id),
            data_version=self.store.data_version,
            provenance=evidence_provenance(
                run_id=self.run_id,
                tool_version=TOOL_VERSION,
                data_version=self.store.data_version,
                method="disease_association_lookup",
                parameters={"disease_id": disease_id},
                software=self.software,
            ),
        )
