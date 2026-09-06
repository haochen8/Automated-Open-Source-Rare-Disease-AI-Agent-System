"""Replaceable, versioned gene and disease phenotype association storage."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from rare_disease_agent.tools.phenotype.hpo import HPOOntology
from rare_disease_agent.tools.phenotype.schemas import (
    GeneAssociation,
    PhenotypeDatasetProvenance,
)


class PhenotypeAssociationStore:
    def __init__(
        self,
        *,
        ontology: HPOOntology,
        associations: list[GeneAssociation],
        provenance: PhenotypeDatasetProvenance,
    ) -> None:
        self.ontology = ontology
        self.provenance = provenance
        self._by_gene: dict[str, list[GeneAssociation]] = {}
        self._by_disease: dict[str, list[GeneAssociation]] = {}
        for association in associations:
            if ontology.get(association.hpo_id) is None:
                raise ValueError(f"Association uses unknown HPO term: {association.hpo_id}")
            self._by_gene.setdefault(association.gene.upper(), []).append(association)
            if association.disease_id:
                self._by_disease.setdefault(association.disease_id, []).append(association)

    @classmethod
    def from_files(
        cls,
        *,
        ontology_path: Path | str,
        associations_path: Path | str,
        manifest_path: Path | str,
        verify_checksum: bool = True,
    ) -> PhenotypeAssociationStore:
        association_file = Path(associations_path)
        manifest = PhenotypeDatasetProvenance.model_validate_json(
            Path(manifest_path).read_text(encoding="utf-8")
        )
        checksum = hashlib.sha256(association_file.read_bytes()).hexdigest()
        if verify_checksum and checksum != manifest.checksum:
            raise ValueError(
                f"Phenotype association checksum mismatch: expected {manifest.checksum}, "
                f"observed {checksum}"
            )
        ontology = HPOOntology.from_obo(ontology_path)
        associations = _read_associations(association_file)
        return cls(ontology=ontology, associations=associations, provenance=manifest)

    @property
    def data_version(self) -> str:
        return self.provenance.release

    @property
    def genes(self) -> list[str]:
        return sorted(self._by_gene)

    def gene_associations(self, gene: str) -> list[GeneAssociation]:
        return list(self._by_gene.get(gene.upper(), []))

    def disease_associations(self, disease_id: str) -> list[GeneAssociation]:
        return list(self._by_disease.get(disease_id, []))

    @property
    def gene_count(self):
        return len(self._by_gene)

    def iter_gene_terms(self):
        for gene, associations in self._by_gene.items():
            yield gene, {association.hpo_id for association in associations}

    def all_gene_terms(self) -> dict[str, set[str]]:
        return {
            gene: {association.hpo_id for association in associations}
            for gene, associations in self._by_gene.items()
        }

    def manifest_json(self) -> str:
        return json.dumps(self.provenance.model_dump(mode="json"), sort_keys=True)


def _read_associations(path: Path) -> list[GeneAssociation]:
    with path.open(encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
    reader = csv.DictReader(lines, delimiter="\t")
    if reader.fieldnames is None:
        raise ValueError(f"Phenotype association file has no header: {path}")

    def first(row: dict[str, str], *names: str) -> str | None:
        lowered = {key.lower(): value for key, value in row.items()}
        for name in names:
            value = lowered.get(name.lower())
            if value:
                return value.strip()
        return None

    result: list[GeneAssociation] = []
    for row in reader:
        gene = first(row, "gene_symbol", "gene", "ncbi_gene_symbol")
        hpo_id = first(row, "hpo_id", "hpo-id", "term_id")
        if not gene or not hpo_id:
            raise ValueError("Each phenotype association row requires gene_symbol and hpo_id")
        result.append(
            GeneAssociation(
                gene=gene.upper(),
                hpo_id=hpo_id,
                disease_id=first(row, "disease_id", "disease-id"),
                evidence=first(row, "evidence", "frequency"),
            )
        )
    return result
