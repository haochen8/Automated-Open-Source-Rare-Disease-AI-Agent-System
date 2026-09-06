"""Normalize official HPO OBO, genes_to_phenotype and phenotype.hpoa offline."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

import duckdb
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field

from rare_disease_agent.resource_management import ResourceLock, ResourceManager, sha256
from rare_disease_agent.tools.phenotype.hpo import HPOOntology
from rare_disease_agent.tools.phenotype.schemas import HPO_ID_PATTERN


class AssociationRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gene: str | None = None
    gene_id: str | None = None
    disease_name: str | None = None
    hpo_name: str | None = None
    modifier: str | None = None
    hpo_id: str = Field(pattern=HPO_ID_PATTERN)
    disease_id: str | None = None
    evidence: str | None = None
    frequency: str | None = None
    qualifier: str | None = None
    reference: str | None = None
    onset: str | None = None
    sex: str | None = None
    aspect: str | None = None
    biocuration: str | None = None
    release: str
    license_reference: str
    source_checksum: str
    parser_version: str = "hpo-normalizer-v1"


ASSOCIATION_SCHEMA = pa.schema([(name, pa.string()) for name in AssociationRow.model_fields])


def association_rows(path: Path, lock: ResourceLock) -> Iterator[dict]:
    with path.open() as stream:
        header = None
        for line in stream:
            stripped = line.lstrip("#").strip()
            if "\t" in stripped and "hpo_id" in stripped.lower().split("\t"):
                header = stripped.split("\t")
                break
        if header is None:
            raise ValueError("Official association header missing")
        reader = csv.DictReader(
            (line for line in stream if line.strip() and not line.startswith("#")),
            fieldnames=header,
            delimiter="\t",
        )
        for raw in reader:
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError("Malformed association row")
            row = {key.lower(): value or None for key, value in raw.items()}
            gene = row.get("gene_symbol") or row.get("ncbi_gene_symbol")
            disease = row.get("database_id") or row.get("disease_id")
            if lock.kind == "genes" and not gene or lock.kind == "diseases" and not disease:
                raise ValueError("Association identity missing")
            yield AssociationRow(
                gene=gene.upper() if gene else None,
                gene_id=row.get("ncbi_gene_id"),
                disease_name=row.get("disease_name"),
                hpo_name=row.get("hpo_name"),
                modifier=row.get("modifier"),
                hpo_id=row["hpo_id"],
                disease_id=disease,
                evidence=row.get("evidence"),
                frequency=row.get("frequency"),
                qualifier=row.get("qualifier"),
                reference=row.get("reference"),
                onset=row.get("onset"),
                sex=row.get("sex"),
                aspect=row.get("aspect"),
                biocuration=row.get("biocuration"),
                release=lock.release,
                license_reference=lock.license_reference,
                source_checksum=lock.expected_sha256,
            ).model_dump()


def _normalize_hpo(
    manager: ResourceManager, locks: list[ResourceLock], output: Path, *, batch_size: int = 1024
) -> Path:
    if not 1 <= batch_size <= 10000:
        raise ValueError("Invalid batch size")
    if {lock.kind for lock in locks} != {"obo", "genes", "diseases"} or len(locks) != 3:
        raise ValueError("Exactly one ontology, gene and disease resource required")
    if len({lock.release for lock in locks}) != 1:
        raise ValueError("HPO releases must match")
    for lock in locks:
        manager.verify(lock)
    if output.exists():
        raise ValueError("Normalized output already exists; use a new version directory")
    required_memory = 256 * 1024**2 + 8 * next(
        lock.expected_size for lock in locks if lock.kind == "obo"
    )
    if psutil.virtual_memory().available < required_memory:
        raise RuntimeError("Insufficient live memory for ontology import")
    if (
        psutil.disk_usage(output.parent).free
        < 4 * sum(lock.expected_size for lock in locks) + 64 * 1024**2
    ):
        raise RuntimeError("Insufficient live disk for HPO import")
    output.mkdir(parents=True)
    ontology_lock = next(lock for lock in locks if lock.kind == "obo")
    ontology = HPOOntology.from_obo(manager.paths(ontology_lock)[0])
    terms = [
        dict(
            **term.model_dump(),
            release=ontology_lock.release,
            license_reference=ontology_lock.license_reference,
            source_checksum=ontology_lock.expected_sha256,
        )
        for term in ontology.terms
    ]
    pq.write_table(pa.Table.from_pylist(terms), output / "terms.parquet")
    for lock in locks:
        if lock.kind == "obo":
            continue
        batch = []
        with pq.ParquetWriter(output / f"{lock.kind}.parquet", ASSOCIATION_SCHEMA) as writer:
            for row in association_rows(manager.paths(lock)[0], lock):
                if ontology.get(row["hpo_id"]) is None:
                    raise ValueError("Association references unknown ontology term")
                batch.append(row)
                if len(batch) == batch_size:
                    writer.write_table(pa.Table.from_pylist(batch, schema=ASSOCIATION_SCHEMA))
                    batch.clear()
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=ASSOCIATION_SCHEMA))
    database = output / "hpo.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("SET memory_limit='512MB'")
        connection.execute("SET threads=2")
        for name in ("terms", "genes", "diseases"):
            connection.from_parquet(str(output / f"{name}.parquet")).create_view("incoming")
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM incoming")
            connection.execute(f"CREATE INDEX {name}_hpo ON {name}(hpo_id)")
            connection.execute(f"CREATE VIEW normalized_{name} AS SELECT * FROM {name}")
        connection.execute("CREATE INDEX gene_lookup ON genes(gene)")
        connection.execute("CREATE INDEX disease_lookup ON diseases(disease_id)")
        connection.execute("DROP VIEW incoming")
    from rare_disease_agent.resource_management import atomic_json, lock_hash

    atomic_json(
        output / "manifest.json",
        {
            "schema_version": 1,
            "parser_version": "hpo-normalizer-v1",
            "locks": [lock.model_dump(mode="json") for lock in locks],
            "lock_hashes": [lock_hash(lock) for lock in locks],
            "receipts": [
                manager.verify(lock)
                .model_copy(
                    update={
                        "output_checksum": sha256(
                            output
                            / ("terms.parquet" if lock.kind == "obo" else f"{lock.kind}.parquet")
                        )
                    }
                )
                .model_dump(mode="json")
                for lock in locks
            ],
            "outputs": {path.name: sha256(path) for path in output.iterdir() if path.is_file()},
        },
    )
    return database


class IndexedPhenotypeStore:
    """Association lookup over verified normalized tables, without full association lists."""

    def __init__(self, directory: Path):
        import json
        from datetime import UTC, datetime

        from rare_disease_agent.tools.phenotype.schemas import HPOTerm, PhenotypeDatasetProvenance

        manifest = json.loads((directory / "manifest.json").read_text())
        if (
            manifest.get("schema_version") != 1
            or manifest.get("parser_version") != "hpo-normalizer-v1"
        ):
            raise ValueError("Incompatible normalized HPO schema")
        if set(manifest["outputs"]) != {
            "terms.parquet",
            "genes.parquet",
            "diseases.parquet",
            "hpo.duckdb",
        }:
            raise ValueError("Incomplete normalized HPO manifest")
        for name, checksum in manifest["outputs"].items():
            if Path(name).name != name or sha256(directory / name) != checksum:
                raise ValueError("Normalized HPO checksum mismatch")
        self.connection = duckdb.connect(str(directory / "hpo.duckdb"), read_only=True)
        self.ontology = HPOOntology(
            [
                HPOTerm.model_validate({name: row[name] for name in HPOTerm.model_fields})
                for row in pq.read_table(directory / "terms.parquet").to_pylist()
            ]
        )
        locks = [ResourceLock.model_validate(lock) for lock in manifest["locks"]]
        gene_lock = next(lock for lock in locks if lock.kind == "genes")
        self.provenance = PhenotypeDatasetProvenance(
            source=gene_lock.source_url,
            release=gene_lock.release,
            downloaded_at=datetime.now(UTC),
            checksum=gene_lock.expected_sha256,
            license_reference=gene_lock.license_reference,
        )

    @property
    def data_version(self):
        return self.provenance.release

    @property
    def gene_count(self):
        return self.connection.execute(
            "SELECT count(DISTINCT gene) FROM genes WHERE qualifier IS DISTINCT FROM 'NOT'"
        ).fetchone()[0]

    def iter_gene_terms(self):
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT DISTINCT gene FROM genes ORDER BY gene")
            while batch := cursor.fetchmany(256):
                for row in batch:
                    yield row[0], {item.hpo_id for item in self.gene_associations(row[0])}
        finally:
            cursor.close()

    def gene_associations(self, gene):
        from rare_disease_agent.tools.phenotype.schemas import GeneAssociation

        rows = self.connection.execute(
            (
                "SELECT gene, hpo_id, disease_id, evidence FROM genes WHERE gene=? AND "
                "qualifier IS DISTINCT FROM 'NOT' LIMIT 10001"
            ),
            [gene.upper()],
        ).fetchall()
        if len(rows) > 10000:
            raise ValueError("Per-gene association budget exceeded")
        results = []
        for row in rows:
            term = self.ontology.get(row[1])
            hpo_id = term.replaced_by if term.obsolete and term.replaced_by else row[1]
            results.append(
                GeneAssociation(gene=row[0], hpo_id=hpo_id, disease_id=row[2], evidence=row[3])
            )
        return results

    def disease_associations(self, disease_id):
        from rare_disease_agent.tools.phenotype.schemas import GeneAssociation

        rows = self.connection.execute(
            (
                "SELECT hpo_id,evidence FROM diseases WHERE disease_id=? AND qualifier IS "
                "DISTINCT FROM 'NOT' LIMIT 10001"
            ),
            [disease_id],
        ).fetchall()
        if len(rows) > 10000:
            raise ValueError("Per-disease association budget exceeded")
        return [
            GeneAssociation(gene="", hpo_id=row[0], disease_id=disease_id, evidence=row[1])
            for row in rows
        ]

    def close(self):
        self.connection.close()


def normalize_hpo(manager, locks, output: Path, *, batch_size: int = 1024) -> Path:
    """Publish only a completely verified conversion; failed staging is removed."""
    import tempfile

    if output.exists():
        raise ValueError("Normalized output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hpo-", dir=output.parent) as temporary:
        staged = Path(temporary) / "normalized"
        _normalize_hpo(manager, locks, staged, batch_size=batch_size)
        staged.rename(output)
    return output / "hpo.duckdb"
