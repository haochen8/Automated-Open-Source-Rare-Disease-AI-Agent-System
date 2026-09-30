"""Verified global allele/transcript assembly for bounded, disjoint autosomal shards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.tools.variants.ingest import (
    ingest_vep,
    sample_names,
    vcf_rows,
    verify_split_genotypes,
)
from rare_disease_agent.workflows.partitioning import MAX_HEADER, MAX_OUTPUT, VERSION, _private
from rare_disease_agent.workflows.phase5 import code_checksum

TABLES = ("alleles", "consequences", "candidate_map", "variants")
ASSEMBLY_VERSION = "verified-shard-assembly-v1"


def read_json(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > MAX_HEADER:
        raise ValueError("Metadata exceeds bounded file contract")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Expected metadata object")
    return value


def inventory(directory: Path) -> dict[str, str]:
    result = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symbolic links are not immutable stage artifacts")
        if path.is_file() and path != directory / "integrity_manifest.json":
            result[str(path.relative_to(directory))] = sha256(path)
    return result


def seal(directory: Path):
    atomic_json(directory / "integrity_manifest.json", inventory(directory))


def verified(directory: Path) -> dict:
    _private(directory)
    expected = read_json(directory / "integrity_manifest.json")
    if inventory(directory) != expected:
        raise ValueError("Artifact inventory or checksum mismatch")
    return expected


def plan_identity(plan: Path) -> dict:
    _private(plan)
    manifest = read_json(plan / "manifest.json")
    if manifest["version"] != VERSION or set(manifest["outputs"]) != {
        "header.txt",
        "segments.jsonl",
    }:
        raise ValueError("Unsupported coverage plan")
    for name, digest in manifest["outputs"].items():
        limit = MAX_HEADER + 1024 if name == "header.txt" else MAX_OUTPUT
        if (
            (plan / name).is_symlink()
            or (plan / name).stat().st_size > limit
            or sha256(plan / name) != digest
        ):
            raise ValueError("Coverage plan checksum mismatch")
    return manifest


def verify_partition(plan: Path, shard: Path) -> tuple[dict, dict]:
    """Bind actual tagged rows to the source plan, not merely to a supplied receipt."""
    _private(plan, shard)
    manifest = plan_identity(plan)
    receipt = read_json(shard / "receipt.json")
    match = None
    with (plan / "segments.jsonl").open() as stream:
        while line := stream.readline(MAX_HEADER + 1):
            if len(line) > MAX_HEADER:
                raise ValueError("Coverage segment exceeds metadata budget")
            segment = json.loads(line)
            if segment["shard"] == receipt["shard"]:
                if match is not None:
                    raise ValueError("Repeated coverage segment")
                match = segment
    if (
        match is None
        or match["shard"] is None
        or match["disposition"] != "eligible_autosomal_sequence"
        or not 1 <= match["source_records"] <= 500
        or any(receipt.get(k) != v for k, v in match.items())
        or receipt["source_sha256"] != manifest["source_sha256"]
        or sha256(shard / "subset.vcf") != receipt["subset_sha256"]
    ):
        raise ValueError("Partition does not match coverage plan")
    header = []
    digest = hashlib.sha256()
    count = header_size = 0
    for row in vcf_rows(shard / "subset.vcf"):
        if row.startswith("#") and count == 0:
            header.append(row)
            header_size += len(row.encode()) + 1
            if header_size > MAX_HEADER:
                raise ValueError("Partition header exceeds budget")
        else:
            digest.update((row + "\n").encode())
            count += 1
    if (
        count != match["source_records"]
        or digest.hexdigest() != match["records_sha256"]
        or "\n".join(header) + "\n" != (plan / "header.txt").read_text()
    ):
        raise ValueError("Partition source content mismatch")
    return manifest, match


def validate_shard(plan: Path, shard: Path, annotation: Path, output: Path):
    """Write tables and source-bound proof; caller publishes the stage atomically."""
    _private(output)
    manifest, segment = verify_partition(plan, shard)
    before = verified(annotation)
    required = {
        "subset.vcf",
        "normalized.vcf",
        "annotated.vcf",
        "run_manifest.json",
        "normalization.receipt.json",
        "annotation.receipt.json",
    }
    if not required <= before.keys():
        raise ValueError("Incomplete annotation bundle")
    prior = read_json(annotation / "run_manifest.json")
    if (
        prior["status"] != "completed"
        or prior["source_sha256"] != manifest["source_sha256"]
        or before["subset.vcf"] != sha256(shard / "subset.vcf")
    ):
        raise ValueError("Annotation source identity mismatch")
    contexts = []
    for name, provider, incoming, outgoing in (
        ("normalization", "bcftools", "subset.vcf", "normalized.vcf"),
        ("annotation", "vep", "normalized.vcf", "annotated.vcf"),
    ):
        receipt = read_json(annotation / (name + ".receipt.json"))
        config = receipt["configuration"]
        if (
            receipt["provider"] != provider
            or receipt["returncode"] != 0
            or receipt["input_sha256"] != before[incoming]
            or receipt["output_sha256"] != before[outgoing]
            or config["genome_build"] != "GRCh38"
            or receipt["reference_sha256"] != config["reference_sha256"]
            or receipt["tool_version"] != config["tool_version"]
            or receipt["data_version"] != config["data_version"]
        ):
            raise ValueError("Annotation receipt linkage mismatch")
        contexts.append(
            {
                k: config[k]
                for k in (
                    "provider",
                    "tool_version",
                    "data_version",
                    "genome_build",
                    "reference_sha256",
                    "cache_path",
                    "annotation_profile",
                )
            }
            | {"executable_sha256": receipt["executable_sha256"]}
        )
    if (
        contexts[1]["data_version"] != "116"
        or contexts[1]["tool_version"].split(".")[0] != "116"
        or contexts[1]["annotation_profile"] != "track1"
    ):
        raise ValueError("Unsupported annotation context for current ranking")
    if contexts[0]["reference_sha256"] != contexts[1]["reference_sha256"]:
        raise ValueError("Normalization and annotation reference mismatch")
    counts = verify_split_genotypes(annotation / "subset.vcf", annotation / "normalized.vcf")
    if (counts["source_records"], counts["normalized_alleles"]) != (
        segment["source_records"],
        segment["alternate_alleles"],
    ):
        raise ValueError("Reconciliation does not match source coverage")
    ingest_vep(annotation / "annotated.vcf", annotation / "normalized.vcf", output / "tables")
    if verified(annotation) != before or verify_partition(plan, shard) != (manifest, segment):
        raise ValueError("Inputs changed during validation")
    atomic_json(
        output / "proof.json",
        {
            "version": ASSEMBLY_VERSION,
            "code_sha256": code_checksum(),
            "source_sha256": manifest["source_sha256"],
            "plan_manifest_sha256": sha256(plan / "manifest.json"),
            "segment": segment,
            "context": contexts,
            "reconciliation": counts,
            "sample_identity_sha256": hashlib.sha256(
                json.dumps(sample_names(annotation / "subset.vcf")).encode()
            ).hexdigest(),
            "annotation_integrity": before,
        },
    )
    seal(output)


def assemble_verified(plan: Path, validations: list[Path], output: Path):
    """Stream every table, rejecting duplicate alleles before any global ranking."""
    _private(output, *validations)
    if not 1 <= len(validations) <= 8 or len(set(validations)) != len(validations):
        raise ValueError("Assembly requires one to eight distinct verified shards")
    manifest = plan_identity(plan)
    before = [verified(path) for path in validations]
    proofs = [read_json(path / "proof.json") for path in validations]
    order = sorted(range(len(proofs)), key=lambda i: proofs[i]["segment"]["first_source_row"])
    validations, proofs, before = (
        [items[i] for i in order] for items in (validations, proofs, before)
    )
    previous = 0
    common = (
        "version",
        "code_sha256",
        "source_sha256",
        "plan_manifest_sha256",
        "context",
        "sample_identity_sha256",
    )
    for proof in proofs:
        segment = proof["segment"]
        if (
            any(proof[key] != proofs[0][key] for key in common)
            or proof["version"] != ASSEMBLY_VERSION
            or proof["code_sha256"] != code_checksum()
            or proof["source_sha256"] != manifest["source_sha256"]
            or proof["plan_manifest_sha256"] != sha256(plan / "manifest.json")
            or segment["first_source_row"] <= previous
        ):
            raise ValueError("Mixed context, repeated shard, or overlapping source intervals")
        previous = segment["last_source_row"]
    tables = output / "tables"
    tables.mkdir()
    metrics = {name: 0 for name in TABLES}
    metrics.update(missing_population_frequency=0, deferred_alleles=0)
    for name in TABLES:
        schema = pq.read_schema(validations[0] / "tables" / (name + ".parquet"))
        with pq.ParquetWriter(tables / (name + ".parquet"), schema, compression="zstd") as writer:
            for path in validations:
                source = pq.ParquetFile(path / "tables" / (name + ".parquet"))
                if source.schema_arrow != schema:
                    raise ValueError("Mixed ingestion table schema")
                for batch in source.iter_batches(batch_size=256):
                    writer.write_batch(batch)
                    metrics[name] += batch.num_rows
    for path in validations:
        counts = read_json(path / "tables" / "manifest.json")
        for name in ("missing_population_frequency", "deferred_alleles"):
            metrics[name] += counts[name]
    with duckdb.connect() as db:
        db.execute("SET memory_limit='256MB'")
        db.execute("SET threads=1")
        db.execute("SET temp_directory=''")  # Fail closed instead of spilling private rows.
        db.from_parquet(str(tables / "alleles.parquet")).create_view("alleles")
        for columns in ("allele_id", "chromosome, position, reference, alternate"):
            if db.execute(
                f"SELECT 1 FROM alleles GROUP BY {columns} HAVING count(*)>1 LIMIT 1"
            ).fetchone():
                raise ValueError("Duplicate source or normalized allele requires explicit mapping")
        db.from_parquet(str(tables / "variants.parquet")).create_view("variants")
        if db.execute(
            "SELECT 1 FROM variants GROUP BY variant_id HAVING count(*)>1 LIMIT 1"
        ).fetchone():
            raise ValueError("Duplicate global candidate")
    if [verified(path) for path in validations] != before or plan_identity(plan) != manifest:
        raise ValueError("Assembly inputs changed")
    atomic_json(
        tables / "manifest.json",
        metrics
        | {
            "candidate_unit": "source allele and gene",
            "representative_policy": (
                "highest configured consequence score per gene; retain all CSQ"
            ),
            "sample_alias": "sample_1",
            "outputs": inventory(tables),
        },
    )
    included_records = sum(p["segment"]["source_records"] for p in proofs)
    included_alleles = sum(p["segment"]["alternate_alleles"] for p in proofs)
    if metrics["alleles"] != included_alleles:
        raise ValueError("Assembled allele coverage mismatch")
    atomic_json(
        output / "reconciliation.json",
        {
            "source_records": included_records,
            "normalized_alleles": included_alleles,
            "genotype_reconciliation": True,
        },
    )
    atomic_json(
        output / "run_manifest.json",
        {
            "format": ASSEMBLY_VERSION,
            "status": "completed",
            "code_sha256": code_checksum(),
            "source_sha256": manifest["source_sha256"],
            "context": proofs[0]["context"],
            "source_plan": manifest,
            "shard_proofs": proofs,
            "validation_integrity": before,
            "coverage": {
                "included_source_records": included_records,
                "included_alternate_alleles": included_alleles,
                "unprocessed_source_records": manifest["source_records"] - included_records,
                "unprocessed_alternate_alleles": manifest["alternate_alleles"] - included_alleles,
                "source_dispositions": manifest["counts"],
                "whole_genome_analysis_complete": False,
            },
        },
    )
    seal(output)


def verify_assembly(directory: Path) -> dict:
    contents = verified(directory)
    manifest = read_json(directory / "run_manifest.json")
    required = {"run_manifest.json", "reconciliation.json", "tables/manifest.json"} | {
        "tables/" + table + ".parquet" for table in TABLES
    }
    if (
        set(contents) != required
        or manifest.get("format") != ASSEMBLY_VERSION
        or manifest["status"] != "completed"
        or manifest["code_sha256"] != code_checksum()
        or manifest["coverage"]["whole_genome_analysis_complete"] is not False
    ):
        raise ValueError("Unsupported or stale global assembly")
    table_manifest = read_json(directory / "tables" / "manifest.json")
    expected_tables = {name + ".parquet" for name in TABLES}
    if set(table_manifest["outputs"]) != expected_tables or any(
        contents["tables/" + name] != digest for name, digest in table_manifest["outputs"].items()
    ):
        raise ValueError("Assembly table linkage mismatch")
    return manifest
