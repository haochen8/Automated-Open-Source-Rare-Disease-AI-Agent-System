"""Read-only accounting from existing index statistics and local reference metadata."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from rare_disease_agent.tools.variants.ingest import vcf_rows


def scan_vcf_contig_statistics(source: Path) -> list[str]:
    """Fallback for old indexes without counts; no index is created or modified."""
    lengths = {}
    counts: Counter = Counter()
    sample_header = False
    for line in vcf_rows(source):
        if line.startswith("##contig=<"):
            name = re.search(r"(?:<|,)ID=([^,>]+)", line)
            length = re.search(r"(?:<|,)length=(\d+)(?:,|>)", line)
            if name is None or length is None or name[1] in lengths:
                raise ValueError("Missing or ambiguous VCF contig metadata")
            lengths[name[1]] = int(length[1])
        elif line.startswith("#CHROM\t"):
            if sample_header:
                raise ValueError("Duplicate VCF sample header")
            sample_header = True
        elif not line.startswith("#"):
            name = line.partition("\t")[0]
            if not sample_header or name not in lengths:
                raise ValueError("Record lacks declared contig context")
            counts[name] += 1
    return [f"{name}\t{lengths[name]}\t{count}" for name, count in counts.items()]


def assess_contig_coverage(
    statistics: Iterable[str],
    reference_lengths: Mapping[str, int],
    cache_contigs: set[str],
    *,
    statistics_source: str = "existing_index",
) -> dict:
    """Classify every indexed record without implying sequence or ploidy validation.

    Inputs are tab-separated bcftools index --stats rows. Exact reference names and
    lengths are required; alternate contigs are never silently mapped to a primary.
    The result is patient-derived metadata and must stay in a private run directory.
    """
    if statistics_source not in {"existing_index", "sequential_vcf_scan"}:
        raise ValueError("Unknown contig statistics provenance")
    categories = (
        "autosomal_reference_and_cache_available",
        "sex_chromosome_context_pending",
        "mitochondrial_model_pending",
        "nonstandard_context_pending",
        "annotation_cache_missing",
        "reference_missing",
        "reference_length_mismatch",
    )
    counts = dict.fromkeys(categories, 0)
    contigs: Counter = Counter()
    seen = set()
    for line in statistics:
        columns = line.rstrip("\n").split("\t")
        if len(columns) != 3:
            raise ValueError("Invalid existing-index statistics schema")
        name, length, count = columns
        if not name or name in seen or not length.isdecimal() or not count.isdecimal():
            raise ValueError("Ambiguous existing-index statistics")
        seen.add(name)
        length, count = int(length), int(count)
        if length < 1:
            raise ValueError("Missing index contig length")
        canonical = name.removeprefix("chr")
        if name not in reference_lengths:
            category = "reference_missing"
        elif reference_lengths[name] != length:
            category = "reference_length_mismatch"
        elif name not in cache_contigs:
            category = "annotation_cache_missing"
        elif canonical in {str(i) for i in range(1, 23)}:
            category = "autosomal_reference_and_cache_available"
        elif canonical in {"X", "Y"}:
            category = "sex_chromosome_context_pending"
        elif canonical in {"M", "MT"}:
            category = "mitochondrial_model_pending"
        else:
            category = "nonstandard_context_pending"
        counts[category] += count
        contigs[category] += 1
    if not seen:
        raise ValueError("Empty existing-index statistics")
    return {
        "indexed_records": sum(counts.values()),
        "indexed_contigs": len(seen),
        "record_counts": counts,
        "contig_counts": {category: contigs[category] for category in categories},
        "evidence": statistics_source + " and exact local reference/cache metadata",
        "sequence_validation_performed": False,
        "whole_genome_analysis_complete": False,
    }
