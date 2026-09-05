"""Minimal deterministic HPO OBO loader and ontology traversal."""

from __future__ import annotations

from pathlib import Path

from rare_disease_agent.tools.phenotype.schemas import HPOTerm


class HPOOntology:
    def __init__(self, terms: list[HPOTerm]) -> None:
        self._terms = {term.hpo_id: term for term in terms}
        if len(self._terms) != len(terms):
            raise ValueError("HPO ontology contains duplicate identifiers")

    @classmethod
    def from_obo(cls, path: Path | str) -> HPOOntology:
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"HPO OBO file does not exist: {source}")
        terms: list[HPOTerm] = []
        current: dict[str, object] | None = None

        def finish() -> None:
            nonlocal current
            if current and current.get("id") and current.get("name"):
                terms.append(
                    HPOTerm(
                        hpo_id=str(current["id"]),
                        label=str(current["name"]),
                        parents=list(current.get("parents", [])),
                        obsolete=bool(current.get("obsolete", False)),
                        replaced_by=(
                            str(current["replaced_by"]) if current.get("replaced_by") else None
                        ),
                    )
                )
            current = None

        with source.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if line == "[Term]":
                    finish()
                    current = {"parents": []}
                    continue
                if line.startswith("["):
                    finish()
                    continue
                if current is None or not line or line.startswith("!"):
                    continue
                key, separator, value = line.partition(": ")
                if not separator:
                    continue
                if key == "id":
                    current["id"] = value
                elif key == "name":
                    current["name"] = value
                elif key == "is_a":
                    parents = current.setdefault("parents", [])
                    assert isinstance(parents, list)
                    parents.append(value.split(" ! ", 1)[0])
                elif key == "is_obsolete":
                    current["obsolete"] = value.lower() == "true"
                elif key == "replaced_by":
                    current["replaced_by"] = value
        finish()
        if not terms:
            raise ValueError(f"No HPO terms found in {source}")
        return cls(terms)

    def get(self, hpo_id: str) -> HPOTerm | None:
        return self._terms.get(hpo_id)

    def ancestors(self, hpo_id: str, *, include_self: bool = True) -> set[str]:
        if hpo_id not in self._terms:
            return set()
        result = {hpo_id} if include_self else set()
        pending = list(self._terms[hpo_id].parents)
        while pending:
            parent = pending.pop()
            if parent in result:
                continue
            result.add(parent)
            term = self._terms.get(parent)
            if term:
                pending.extend(term.parents)
        return result

    @property
    def terms(self) -> list[HPOTerm]:
        return list(self._terms.values())
