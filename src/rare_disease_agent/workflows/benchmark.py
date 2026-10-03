"""Deterministic, bounded autosomal sampling for private resource benchmarks.

Sampling is evenly spaced by eligible record ordinal within each autosome. It is
not a random or disease-enriched sample and cannot establish case-wide coverage.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.tools.variants.ingest import info_fields, vcf_rows
from rare_disease_agent.workflows.authorized import _outside_git

TAGS = ("RDA_SOURCE_ROW", "RDA_ALT_IDX")
AUTOSOMES = frozenset(str(i) for i in range(1, 23))


def _eligible(columns: list[str]) -> str | None:
    if len(columns) != 10:
        raise ValueError("Benchmark requires a single-sample VCF")
    chromosome = columns[0].removeprefix("chr")
    if chromosome not in AUTOSOMES:
        return None
    if not all(re.fullmatch("[ACGT]+", allele) for allele in [columns[3], *columns[4].split(",")]):
        return None
    return chromosome


def select_autosomal_benchmark(source: Path, output: Path, *, per_autosome: int = 100) -> dict:
    """Keep global source-row/ALT provenance; publish no raw data to stdout or Git."""
    if not 1 <= per_autosome <= 500:
        raise ValueError("Autosomal sample size exceeds benchmark budget")
    for path in (source, output):
        _outside_git(path.resolve())
    if output.exists() or output.is_symlink():
        raise ValueError("Benchmark output already exists")
    fingerprint = sha256(source)
    counts: Counter = Counter()
    aliases: dict[str, str] = {}
    total = deferred = headers = 0
    for line in vcf_rows(source):
        if line.startswith("#"):
            if line.startswith("#CHROM\t"):
                headers += 1
                if len(line.split("\t")) != 10:
                    raise ValueError("Benchmark requires one sample")
            if any(line.startswith("##INFO=<ID=" + tag + ",") for tag in TAGS):
                raise ValueError("Reserved provenance field already present")
            continue
        if headers != 1:
            raise ValueError("Invalid VCF header ordering")
        columns = line.split("\t")
        total += 1
        chromosome = _eligible(columns)
        if any(tag in info_fields(columns[7]) for tag in TAGS):
            raise ValueError("Reserved provenance value already present")
        if chromosome is None:
            deferred += 1
            continue
        if aliases.setdefault(chromosome, columns[0]) != columns[0]:
            raise ValueError("Ambiguous autosomal contig aliases")
        counts[chromosome] += 1
    if not counts:
        raise ValueError("No eligible autosomal records")
    targets = {
        key: {
            (2 * i + 1) * count // (2 * min(count, per_autosome))
            for i in range(min(count, per_autosome))
        }
        for key, count in counts.items()
    }
    staging = Path(tempfile.mkdtemp(prefix=".benchmark-", dir=output.parent))
    selected = multiallelic = global_row = 0
    observed: Counter = Counter()
    temporary = staging / "subset.vcf"
    try:
        with temporary.open("x") as stream:
            for line in vcf_rows(source):
                if line.startswith("#"):
                    if line.startswith("#CHROM\t"):
                        stream.write(
                            "##INFO=<ID=RDA_SOURCE_ROW,Number=1,Type=Integer,"
                            'Description="Original global record ordinal">\n'
                        )
                        stream.write(
                            "##INFO=<ID=RDA_ALT_IDX,Number=A,Type=Integer,"
                            'Description="Original alternate ordinal">\n'
                        )
                    stream.write(line + "\n")
                    continue
                global_row += 1
                columns = line.split("\t")
                chromosome = _eligible(columns)
                if chromosome is None:
                    continue
                ordinal = observed[chromosome]
                observed[chromosome] += 1
                if ordinal not in targets[chromosome]:
                    continue
                alternate_count = len(columns[4].split(","))
                tags = f"RDA_SOURCE_ROW={global_row};RDA_ALT_IDX=" + ",".join(
                    str(i + 1) for i in range(alternate_count)
                )
                columns[7] = tags if columns[7] == "." else columns[7] + ";" + tags
                stream.write("\t".join(columns) + "\n")
                selected += 1
                multiallelic += alternate_count > 1
        if (
            sha256(source) != fingerprint
            or observed != counts
            or global_row != total
            or selected != sum(len(x) for x in targets.values())
        ):
            raise ValueError("Benchmark source changed or sampling accounting failed")
        metrics = {
            "source_sha256": fingerprint,
            "selection_method": "evenly spaced eligible record ordinals per autosome",
            "source_records": total,
            "eligible_autosomal_records": sum(counts.values()),
            "deferred_nonstandard_or_nonsequence_records": deferred,
            "selected_records": selected,
            "unselected_eligible_records": sum(counts.values()) - selected,
            "autosomes_sampled": len(counts),
            "maximum_records_per_autosome": per_autosome,
            "selected_multiallelic_records": multiallelic,
            "whole_genome_analysis_complete": False,
            "subset_sha256": sha256(temporary),
        }
        atomic_json(staging / "selection.receipt.json", metrics)
        if output.exists() or output.is_symlink():
            raise ValueError("Benchmark output already exists")
        staging.rename(output)
        return metrics
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
