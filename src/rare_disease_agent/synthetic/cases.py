"""Versioned synthetic case definitions and packaged resource access."""

from __future__ import annotations

import json
from contextlib import ExitStack
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable

from pydantic import BaseModel, ConfigDict

from rare_disease_agent.tools.inheritance.schemas import Individual, Pedigree
from rare_disease_agent.tools.phenotype.associations import PhenotypeAssociationStore

SYNTHETIC_CASE_NAMES = (
    "de-novo",
    "recessive",
    "compound-het",
    "x-linked",
    "phenotype-incomplete",
    "novel-gene",
)


class SyntheticCase(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str
    vcf_resource: Traversable
    hpo_terms: list[str]
    causal_variant_id: str
    causal_gene: str
    expected_model: str
    pedigree: Pedigree


def synthetic_pedigree() -> Pedigree:
    return Pedigree(
        proband_id="PROBAND",
        mother_id="MOTHER",
        father_id="FATHER",
        individuals=[
            Individual(id="PROBAND", sex="male", affected=True),
            Individual(id="MOTHER", sex="female", affected=False),
            Individual(id="FATHER", sex="male", affected=False),
        ],
    )


def load_synthetic_case(name: str) -> SyntheticCase:
    if name not in SYNTHETIC_CASE_NAMES:
        raise ValueError(
            f"Unknown synthetic case {name!r}; choose one of {', '.join(SYNTHETIC_CASE_NAMES)}"
        )
    package = files("rare_disease_agent.resources.synthetic_cases")
    cases = json.loads(package.joinpath("cases.json").read_text(encoding="utf-8"))
    selected = cases[name]
    return SyntheticCase(
        name=name,
        vcf_resource=package.joinpath(selected["vcf"]),
        hpo_terms=selected["hpo_terms"],
        causal_variant_id=selected["causal_variant_id"],
        causal_gene=selected["causal_gene"],
        expected_model=selected["expected_model"],
        pedigree=synthetic_pedigree(),
    )


def load_synthetic_phenotype_store() -> PhenotypeAssociationStore:
    package = files("rare_disease_agent.resources.phenotype")
    with ExitStack() as stack:
        ontology = stack.enter_context(as_file(package.joinpath("synthetic_hp.obo")))
        associations = stack.enter_context(
            as_file(package.joinpath("synthetic_genes_to_phenotype.tsv"))
        )
        manifest = stack.enter_context(as_file(package.joinpath("synthetic_manifest.json")))
        return PhenotypeAssociationStore.from_files(
            ontology_path=ontology,
            associations_path=associations,
            manifest_path=manifest,
        )
