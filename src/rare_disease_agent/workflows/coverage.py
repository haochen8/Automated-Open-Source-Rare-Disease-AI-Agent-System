"""Read-only accounting from existing index statistics and local reference metadata."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from rare_disease_agent.tools.variants.ingest import vcf_rows
from rare_disease_agent.tools.variants.vep import contig_plan


def synonym_reference_plan(
    statistics: list[str],
    reference_lengths: Mapping[str, int],
    cache_contigs: set[str],
    synonyms: Iterable[str],
    *,
    consider_name_prefixes: bool = False,
) -> dict:
    """Plan exact publisher-supplied synonym relationships, with no sequence rewriting.

    The caller must pin the local synonym file and reference metadata. Multiple
    reference targets or colliding source names are not resolved heuristically.
    """
    assess_contig_coverage(statistics, reference_lengths, cache_contigs)
    parents: dict[str, str] = {}

    def root(name):
        parents.setdefault(name, name)
        while parents[name] != name:
            parents[name] = parents[parents[name]]
            name = parents[name]
        return name

    for ordinal, line in enumerate(synonyms):
        if ordinal >= 100000 or len(line) > 4096:
            raise ValueError("Synonym resource exceeds bounded work budget")
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) != 2 or any(not f or any(c.isspace() for c in f) for f in fields):
            raise ValueError("Invalid local synonym resource schema")
        left, right = map(root, fields)
        parents[right] = left
        if consider_name_prefixes:
            for name in fields:
                if name.startswith("chr") and len(name) > 3:
                    parents[root(name[3:])] = root(name)
    if consider_name_prefixes and "MT" in reference_lengths:
        parents[root("M")] = root("MT")
    targets: dict[str, set[str]] = {}
    for name in reference_lengths:
        targets.setdefault(root(name), set()).add(name)
    categories = (
        "exact_reference_available",
        "synonym_reference_available",
        "reference_missing",
        "reference_length_mismatch",
        "ambiguous_reference",
        "annotation_cache_missing",
    )
    counts = dict.fromkeys(categories, 0)
    contigs = dict.fromkeys(categories, 0)
    rows = []
    assigned = {}
    for line in statistics:
        source, length, count = line.rstrip("\n").split("\t")
        length, count = int(length), int(count)
        matches = {source} if source in reference_lengths else targets.get(root(source), set())
        target = next(iter(matches)) if len(matches) == 1 else None
        if not matches:
            status = "reference_missing"
        elif target is None:
            status = "ambiguous_reference"
        elif reference_lengths[target] != length:
            status = "reference_length_mismatch"
        elif target not in cache_contigs:
            status = "annotation_cache_missing"
        else:
            status = (
                "exact_reference_available" if source == target else "synonym_reference_available"
            )
            if target in assigned:
                raise ValueError(
                    "Source contigs collide after synonym mapping; reconcile explicitly"
                )
            assigned[target] = source
        counts[status] += count
        contigs[status] += 1
        rows.append(
            {
                "source": source,
                "target": target,
                "length": length,
                "records": count,
                "status": status,
            }
        )
    return {
        "records": sum(counts.values()),
        "contigs": len(rows),
        "record_counts": counts,
        "contig_counts": contigs,
        "contig_plan": rows,
        "renaming_performed": False,
        "sequence_equivalence_verified": False,
        "name_prefixes_considered": consider_name_prefixes,
        "scope": "local publisher synonym and length-based reference availability plan only",
    }


def standard_reference_aliases(
    statistics: list[str], reference_lengths: Mapping[str, int], cache_contigs: set[str]
) -> dict:
    """Propose standard-name aliases only; never remap alternate sequences or coordinates."""
    rows = [line.rstrip("\n").split("\t") for line in statistics]
    # Reuse strict schema/count validation before interpreting aliases.
    assess_contig_coverage(statistics, reference_lengths, cache_contigs)
    mapping = contig_plan(tuple(row[0] for row in rows))
    proposals = {}
    records = 0
    for name, length, count in rows:
        target = mapping[name]
        if (
            target is not None
            and target != name
            and name not in reference_lengths
            and reference_lengths.get(target) == int(length)
            and target in cache_contigs
        ):
            proposals[name] = target
            records += int(count)
    return {
        "proposed_aliases": proposals,
        "affected_records": records,
        "coordinates_changed": False,
        "renaming_performed": False,
        "sequence_equivalence_verified": False,
        "scope": "standard-name proposal with matching reference length and cache presence",
    }


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
