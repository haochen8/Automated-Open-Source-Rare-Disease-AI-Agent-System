"""Deterministic ontology-aware gene–phenotype similarity."""

from __future__ import annotations

import math

from rare_disease_agent.reporting.provenance import SoftwareIdentity, evidence_provenance
from rare_disease_agent.tools.phenotype.associations import PhenotypeAssociationStore
from rare_disease_agent.tools.phenotype.schemas import PhenotypeScore

TOOL_VERSION = "phenotype-similarity-v1"


class PhenotypeScorer:
    def __init__(
        self,
        store: PhenotypeAssociationStore,
        *,
        run_id: str,
        software: SoftwareIdentity | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.software = software
        self._information_content = self._build_information_content()
        self._maximum_ic = max(self._information_content.values(), default=1.0) or 1.0

    def _build_information_content(self) -> dict[str, float]:
        gene_terms = self.store.all_gene_terms()
        total_genes = max(1, len(gene_terms))
        annotated_genes: dict[str, set[str]] = {}
        for gene, terms in gene_terms.items():
            expanded: set[str] = set()
            for term in terms:
                expanded.update(self.store.ontology.ancestors(term))
            for term in expanded:
                annotated_genes.setdefault(term, set()).add(gene)
        return {
            term: -math.log((len(genes) + 1) / (total_genes + 1))
            for term, genes in annotated_genes.items()
        }

    def term_similarity(self, first: str, second: str) -> float:
        common = self.store.ontology.ancestors(first) & self.store.ontology.ancestors(second)
        if not common:
            return 0.0
        return min(
            1.0,
            max(self._information_content.get(term, 0.0) for term in common) / self._maximum_ic,
        )

    def score_gene_phenotype(self, patient_hpo: list[str], gene: str) -> PhenotypeScore:
        patient = list(dict.fromkeys(patient_hpo))
        associations = self.store.gene_associations(gene)
        gene_terms = sorted({association.hpo_id for association in associations})
        if patient and gene_terms:
            patient_best = [
                max(self.term_similarity(term, gene_term) for gene_term in gene_terms)
                for term in patient
            ]
            gene_best = [
                max(self.term_similarity(gene_term, term) for term in patient)
                for gene_term in gene_terms
            ]
            score = (sum(patient_best) + sum(gene_best)) / (len(patient_best) + len(gene_best))
        else:
            score = 0.0
        direct_matches = sorted(set(patient) & set(gene_terms))
        return PhenotypeScore(
            gene=gene.upper(),
            score=round(score, 6),
            matched_terms=direct_matches,
            unmatched_patient_terms=sorted(set(patient) - set(direct_matches)),
            data_version=self.store.data_version,
            association_count=len(associations),
            provenance=evidence_provenance(
                run_id=self.run_id,
                tool_version=TOOL_VERSION,
                data_version=self.store.data_version,
                method="resnik_bma",
                parameters={"patient_hpo_count": len(patient)},
                software=self.software,
            ),
        )
