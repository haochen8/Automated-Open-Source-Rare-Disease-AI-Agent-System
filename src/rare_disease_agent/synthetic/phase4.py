"""Generated synthetic edge cases, disjoint from the Phase 3 calibration cases."""

from __future__ import annotations

from pathlib import Path

from rare_disease_agent.synthetic.cases import load_synthetic_case
from rare_disease_agent.tools.inheritance.schemas import Individual

EDGE_CASES = (
    "incomplete-penetrance",
    "affected-sibling",
    "missing-parent",
    "female-x",
    "unphased-compound",
    "mosaic",
    "annotation-missing",
)


def generate_case(name: str, directory: Path):
    if name not in EDGE_CASES:
        return load_synthetic_case(name)
    base = (
        "compound-het"
        if name == "unphased-compound"
        else "x-linked"
        if name == "female-x"
        else "de-novo"
    )
    case = load_synthetic_case(base)
    lines = []
    for line in case.vcf_resource.read_text().splitlines():
        if line.startswith("#CHROM") and name == "affected-sibling":
            line += "\tSIBLING"
        if not line.startswith("#"):
            values = line.split("\t")
            causal = values[2].startswith("SYNTH-CAUSAL")
            values[7] += ";TRANSCRIPT=SYN_TRANSCRIPT_1"
            if name == "incomplete-penetrance" and causal:
                values[11] = "0/1:60"
            if name == "affected-sibling":
                values.append(values[9])
            if name in {"missing-parent", "unphased-compound"} and causal:
                values[11] = "./.:50"
                if name == "unphased-compound":
                    values[10] = "./.:50"
            if name == "female-x" and values[0] == "X":
                values[9] = "0/1:60"
            if name == "mosaic" and causal:
                values[8] += ":VAF"
                values[9] += ":0.12"
                values[10] += ":0"
                values[11] += ":0"
            if name == "annotation-missing" and causal:
                values[7] = ";".join(
                    field
                    for field in values[7].split(";")
                    if not field.startswith(("TRANSCRIPT=", "AF=", "CONSEQUENCE="))
                )
            line = "\t".join(values)
        lines.append(line)
    path = directory / "edge.vcf.txt"
    path.write_text("\n".join(lines) + "\n")
    pedigree = case.pedigree.model_copy(deep=True)
    if name == "affected-sibling":
        pedigree.individuals.append(Individual(id="SIBLING", sex="male", affected=True))
    if name == "female-x":
        pedigree.individuals[0].sex = "female"
    expected = (
        "autosomal_dominant"
        if name in {"incomplete-penetrance", "affected-sibling"}
        else case.expected_model
    )
    return case.model_copy(
        update={
            "name": name,
            "vcf_resource": path,
            "pedigree": pedigree,
            "expected_model": expected,
        }
    )
