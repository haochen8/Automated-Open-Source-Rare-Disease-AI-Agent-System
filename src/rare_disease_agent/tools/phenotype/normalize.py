"""HPO identifier validation and obsolete-term normalization."""

from __future__ import annotations

import re

from rare_disease_agent.tools.phenotype.hpo import HPOOntology
from rare_disease_agent.tools.phenotype.schemas import HPO_ID_PATTERN, NormalizedPhenotypes


def normalize_hpo_terms(terms: list[str], ontology: HPOOntology) -> NormalizedPhenotypes:
    normalized = []
    invalid: list[str] = []
    unknown: list[str] = []
    replacements: dict[str, str] = {}
    warnings: list[str] = []
    seen: set[str] = set()

    for raw_value in terms:
        value = raw_value.strip().upper()
        if not re.fullmatch(HPO_ID_PATTERN, value):
            invalid.append(raw_value)
            warnings.append(f"Invalid HPO identifier retained as a warning: {raw_value!r}.")
            continue
        term = ontology.get(value)
        if term is None:
            unknown.append(value)
            warnings.append(f"Unknown HPO identifier: {value}.")
            continue
        if term.obsolete:
            if term.replaced_by and ontology.get(term.replaced_by):
                replacements[value] = term.replaced_by
                warnings.append(f"Obsolete HPO term {value} replaced by {term.replaced_by}.")
                term = ontology.get(term.replaced_by)
                assert term is not None
            else:
                warnings.append(f"Obsolete HPO term {value} has no supported replacement.")
                continue
        if term.hpo_id in seen:
            warnings.append(f"Duplicate HPO term removed: {term.hpo_id}.")
            continue
        seen.add(term.hpo_id)
        normalized.append(term)

    return NormalizedPhenotypes(
        terms=normalized,
        invalid_ids=invalid,
        unknown_ids=unknown,
        replacements=replacements,
        warnings=warnings,
    )
