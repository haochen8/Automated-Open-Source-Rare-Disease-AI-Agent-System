"""Atomic, bounded VEP ingestion with separate allele and transcript evidence tables."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from collections import defaultdict
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from rare_disease_agent.ranking.scoring import CONSEQUENCE_SCORES
from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.storage.parquet import VARIANT_SCHEMA
from rare_disease_agent.tools.variants.vcf import VariantRecord, _open_text, _zygosity
from rare_disease_agent.tools.variants.vep import VEPLayout, population_frequency

MAX_LINE = 4 * 1024**2


def vcf_rows(path: Path):
    """Read bounded lines; do not include source text in validation errors."""
    with _open_text(path) as stream:
        while line := stream.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE:
                raise ValueError("VCF line exceeds work budget")
            yield line.rstrip("\r\n")


def info_fields(value: str) -> dict[str, str]:
    result = {}
    for token in value.split(";") if value != "." else []:
        name, _, item = token.partition("=")
        if name in result:
            raise ValueError("Duplicate VCF INFO field")
        result[name] = item
    return result


def sample_names(path: Path) -> list[str]:
    for line in vcf_rows(path):
        if line.startswith("#CHROM\t"):
            return line.split("\t")[9:]
    raise ValueError("Missing VCF sample header")


def identity(columns: list[str]) -> str:
    """Includes samples, FILTER and QUAL; annotation may only append INFO fields."""
    info = info_fields(columns[7])
    payload = columns[:7] + columns[8:] + [info.get("RDA_SOURCE_ROW"), info.get("RDA_ALT_IDX")]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


class BatchWriter:
    def __init__(self, path: Path, schema: pa.Schema, batch_size: int = 256):
        self.schema = schema
        self.writer = pq.ParquetWriter(path, schema, compression="zstd")
        self.batch = []
        self.batch_size = batch_size
        self.count = 0

    def append(self, row: dict):
        self.batch.append(row)
        self.count += 1
        if len(self.batch) >= self.batch_size:
            self.flush()

    def flush(self):
        if self.batch:
            self.writer.write_table(pa.Table.from_pylist(self.batch, schema=self.schema))
            self.batch.clear()

    def close(self):
        self.flush()
        self.writer.close()


def _number(value: str | None) -> float | None:
    if value in {None, ".", ""}:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Invalid numeric VCF field")
    return number


def _ingest(source: Path, normalized: Path, output: Path) -> dict:
    output.mkdir()
    allele_schema = pa.schema(
        [
            (x, pa.string())
            for x in (
                "allele_id",
                "identity",
                "source_row",
                "source_alt",
                "chromosome",
                "position",
                "reference",
                "alternate",
                "filter",
                "genotype_calls_json",
                "disposition",
            )
        ]
    )
    consequence_schema = pa.schema(
        [("allele_id", pa.string()), ("ordinal", pa.int64()), ("fields_json", pa.string())]
    )
    mapping_schema = pa.schema(
        [
            (x, pa.string())
            for x in (
                "variant_id",
                "allele_id",
                "gene_id",
                "gene_symbol",
                "selected_consequence_ordinal",
            )
        ]
    )
    writers = {
        "alleles": BatchWriter(output / "alleles.parquet", allele_schema),
        "consequences": BatchWriter(output / "consequences.parquet", consequence_schema),
        "candidate_map": BatchWriter(output / "candidate_map.parquet", mapping_schema),
        "variants": BatchWriter(output / "variants.parquet", VARIANT_SCHEMA),
    }
    layout = None
    samples = None
    missing_frequency = deferred = 0
    try:
        for line in vcf_rows(source):
            if line.startswith("##INFO=<ID=CSQ,"):
                if layout is not None:
                    raise ValueError("Duplicate CSQ schema")
                layout = VEPLayout.from_header(line)
            if line.startswith("#CHROM\t"):
                if samples is not None:
                    raise ValueError("Duplicate VCF sample header")
                samples = line.split("\t")[9:]
                if len(samples) != 1:
                    raise ValueError("This importer requires one explicitly selected sample")
            if not line or line.startswith("#"):
                continue
            if layout is None or samples is None:
                raise ValueError("Missing VEP or sample schema")
            cols = line.split("\t")
            if (
                len(cols) != 10
                or not re.fullmatch("[ACGT]+", cols[3])
                or not re.fullmatch("[ACGT]+", cols[4])
            ):
                raise ValueError("Expected normalized biallelic sequence record")
            info = info_fields(cols[7])
            ordinal, alt = info.get("RDA_SOURCE_ROW", ""), info.get("RDA_ALT_IDX", "")
            if not ordinal.isdigit() or not alt.isdigit() or min(int(ordinal), int(alt)) < 1:
                raise ValueError("Missing source allele provenance")
            allele_id = f"r{ordinal}_a{alt}"
            fields = dict(zip(cols[8].split(":"), cols[9].split(":"), strict=True))
            gt = fields.get("GT")
            if gt is None or not re.fullmatch(r"[01.](?:[/|][01.])?", gt):
                raise ValueError("Invalid normalized genotype")
            calls = json.dumps(
                {"sample_1": {"genotype": gt, "quality": _number(fields.get("GQ"))}}, sort_keys=True
            )
            csqs = layout.decode(info.get("CSQ", "."), alternate_count=1)
            frequency = population_frequency(csqs, allele_number=1)
            missing_frequency += frequency is None
            # This first dry run supports autosomes; sex/PAR and nonstandard coverage stay deferred.
            supported = cols[0] in {str(i) for i in range(1, 23)}
            deferred += not supported
            writers["alleles"].append(
                dict(
                    zip(
                        allele_schema.names,
                        [
                            allele_id,
                            identity(cols),
                            ordinal,
                            alt,
                            cols[0],
                            cols[1],
                            cols[3],
                            cols[4],
                            cols[6],
                            calls,
                            "included" if supported else "deferred_contig_context",
                        ],
                        strict=True,
                    )
                )
            )
            groups = defaultdict(list)
            for index, consequence in enumerate(csqs):
                writers["consequences"].append(
                    {
                        "allele_id": allele_id,
                        "ordinal": index,
                        "fields_json": json.dumps(dict(consequence.fields), sort_keys=True),
                    }
                )
                groups[
                    consequence.value("Gene") or consequence.value("SYMBOL") or "unassigned"
                ].append((index, consequence))
            if not supported:
                continue
            if not groups:
                groups["unassigned"] = []
            for gene_id, annotations in sorted(groups.items()):
                choices = [
                    (index, c, term)
                    for index, c in annotations
                    for term in (c.value("Consequence") or "").split("&")
                ]
                choices.sort(
                    key=lambda x: (
                        -CONSEQUENCE_SCORES.get(x[2], 0.1),
                        x[1].value("Feature") or "",
                        x[2],
                        x[0],
                    )
                )
                selected = choices[0] if choices else None
                symbol = next(
                    (c.value("SYMBOL") for _, c in annotations if c.value("SYMBOL")), None
                )
                key = allele_id + "_g" + hashlib.sha256(gene_id.encode()).hexdigest()[:16]
                record = VariantRecord(
                    chromosome=cols[0],
                    position=int(cols[1]),
                    reference=cols[3],
                    alternate=cols[4],
                    variant_id=key,
                    gene=symbol or (gene_id if gene_id != "unassigned" else "UNKNOWN"),
                    transcript=selected[1].value("Feature") if selected else None,
                    consequence=selected[2] or None if selected else None,
                    genotype=gt,
                    quality=_number(cols[5]),
                    allele_frequency=frequency,
                    zygosity=_zygosity(gt),
                    genotype_calls_json=calls,
                )
                writers["variants"].append(record.model_dump())
                writers["candidate_map"].append(
                    dict(
                        zip(
                            mapping_schema.names,
                            [
                                key,
                                allele_id,
                                gene_id,
                                symbol,
                                str(selected[0]) if selected else None,
                            ],
                            strict=True,
                        )
                    )
                )
    finally:
        for writer in writers.values():
            writer.close()
    if not writers["alleles"].count:
        raise ValueError("Empty annotated input")
    with duckdb.connect() as db:
        db.execute("SET memory_limit='256MB'")
        db.from_parquet(str(output / "alleles.parquet")).create_view("alleles")
        if db.execute(
            "SELECT 1 FROM alleles GROUP BY allele_id HAVING count(*)>1 LIMIT 1"
        ).fetchone():
            raise ValueError("Duplicate source allele provenance")
        if db.execute(
            "SELECT 1 FROM alleles GROUP BY chromosome, position, reference, alternate "
            "HAVING count(*)>1 LIMIT 1"
        ).fetchone():
            raise ValueError("Repeated normalized allele requires explicit deduplication mapping")
        db.execute("CREATE TEMP TABLE expected (identity VARCHAR)")
        batch = []
        for line in vcf_rows(normalized):
            if line and not line.startswith("#"):
                batch.append((identity(line.split("\t")),))
                if len(batch) >= 256:
                    db.executemany("INSERT INTO expected VALUES (?)", batch)
                    batch.clear()
        if batch:
            db.executemany("INSERT INTO expected VALUES (?)", batch)
        if db.execute(
            "SELECT * FROM ((SELECT identity FROM expected EXCEPT ALL SELECT identity FROM alleles)"
            " UNION ALL (SELECT identity FROM alleles EXCEPT ALL SELECT identity FROM expected)) "
            "LIMIT 1"
        ).fetchone():
            raise ValueError("Annotation changed normalized records or genotypes")
    result = {name: w.count for name, w in writers.items()} | {
        "missing_population_frequency": missing_frequency,
        "deferred_alleles": deferred,
        "source_sha256": sha256(source),
        "normalized_sha256": sha256(normalized),
        "sample_alias": "sample_1",
        "candidate_unit": "source allele and gene",
        "representative_policy": "highest configured consequence score per gene; retain all CSQ",
    }
    atomic_json(
        output / "manifest.json",
        result | {"outputs": {p.name: sha256(p) for p in output.iterdir() if p.is_file()}},
    )
    return result


def ingest_vep(source: Path, normalized: Path, output: Path) -> dict:
    """Publish a complete import only. Inputs must be normalized by a verified tool stage."""
    if output.exists():
        raise ValueError("Import output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    before = (sha256(source), sha256(normalized))
    if sample_names(source) != sample_names(normalized):
        raise ValueError("Annotation sample identities changed")
    with tempfile.TemporaryDirectory(prefix="vep-import-", dir=output.parent) as temp:
        try:
            result = _ingest(source, normalized, Path(temp) / "output")
            if before != (sha256(source), sha256(normalized)):
                raise ValueError("Input changed during ingestion")
            (Path(temp) / "output").rename(output)
            return result
        except (ValueError, KeyError, IndexError, TypeError):
            raise ValueError("VEP ingestion failed schema or integrity validation") from None


def verify_split_genotypes(subset: Path, normalized: Path) -> dict:
    """Reconcile every original ALT and its projected GT/GQ using a bounded disk ledger."""
    if sample_names(subset) != sample_names(normalized):
        raise ValueError("Normalization sample identities changed")
    with (
        tempfile.TemporaryDirectory(prefix="allele-ledger-") as temp,
        duckdb.connect(str(Path(temp) / "ledger.duckdb")) as db,
    ):
        db.execute("SET memory_limit='128MB'")
        db.execute("SET threads=1")
        db.execute("CREATE TABLE expected (source_row VARCHAR, source_alt VARCHAR, calls VARCHAR)")
        db.execute("CREATE TABLE observed (source_row VARCHAR, source_alt VARCHAR, calls VARCHAR)")
        # One transaction avoids per-row durable commits and their memory overhead.
        db.execute("BEGIN TRANSACTION")
        count = 0
        for label, path in (("expected", subset), ("observed", normalized)):
            batch = []
            for line in vcf_rows(path):
                if not line or line.startswith("#"):
                    continue
                cols = line.split("\t")
                if len(cols) != 10:
                    raise ValueError("Expected single-sample VCF")
                info = info_fields(cols[7])
                fields = dict(zip(cols[8].split(":"), cols[9].split(":"), strict=True))
                if "GT" not in fields or "GQ" not in fields:
                    raise ValueError("GT/GQ fields required for reconciliation")
                if label == "expected":
                    count += 1
                    alts = cols[4].split(",")
                    if len(alts) > 100 or info["RDA_ALT_IDX"] != ",".join(
                        map(str, range(1, len(alts) + 1))
                    ):
                        raise ValueError("Original ALT provenance mismatch")
                    for index in range(1, len(alts) + 1):
                        projected = "".join(
                            token
                            if token in {"/", "|", "."}
                            else "1"
                            if token == str(index)
                            else "0"
                            for token in re.split(r"([/|])", fields["GT"])
                        )
                        batch.append(
                            (
                                info["RDA_SOURCE_ROW"],
                                str(index),
                                json.dumps([projected, fields["GQ"]]),
                            )
                        )
                else:
                    batch.append(
                        (
                            info["RDA_SOURCE_ROW"],
                            info["RDA_ALT_IDX"],
                            json.dumps([fields["GT"], fields["GQ"]]),
                        )
                    )
                if len(batch) >= 256:
                    db.executemany(f"INSERT INTO {label} VALUES (?, ?, ?)", batch)
                    batch.clear()
            if batch:
                db.executemany(f"INSERT INTO {label} VALUES (?, ?, ?)", batch)
        db.execute("COMMIT")
        if db.execute(
            "SELECT * FROM ((SELECT * FROM expected EXCEPT ALL SELECT * FROM observed) UNION ALL "
            "(SELECT * FROM observed EXCEPT ALL SELECT * FROM expected)) LIMIT 1"
        ).fetchone():
            raise ValueError("Original ALT/genotype reconciliation failed")
        return {
            "source_records": count,
            "normalized_alleles": db.execute("SELECT count(*) FROM observed").fetchone()[0],
            "genotype_reconciliation": True,
        }
