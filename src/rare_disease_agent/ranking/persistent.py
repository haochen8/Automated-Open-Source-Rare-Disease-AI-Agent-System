"""Batch deterministic features, SQL ordering, and bounded result summaries."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pyarrow as pa

from rare_disease_agent.ranking.schemas import RankedVariant
from rare_disease_agent.ranking.scoring import MODE_FEATURES, PreliminaryRanker
from rare_disease_agent.reporting.provenance import evidence_provenance


def _ranking_storage(connection):
    """Compact keys for fresh databases; historical tables are never migrated in place."""
    legacy = connection.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_catalog=current_database() AND table_schema='main' "
        "AND table_name='ranked_evidence'"
    ).fetchone()
    if legacy and legacy[0] == "BASE TABLE":
        raise ValueError(
            "Legacy ranking storage requires a fresh run; existing results stay readable"
        )
    connection.execute("""CREATE TABLE IF NOT EXISTS ranking_contexts (
        context_id BIGINT PRIMARY KEY, run_id VARCHAR NOT NULL, mode VARCHAR NOT NULL,
        provenance VARCHAR NOT NULL, UNIQUE(run_id, mode))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS ranking_values (
        context_id BIGINT, variant_row_id BIGINT, gene VARCHAR, rank BIGINT,
        raw_score DOUBLE, features VARCHAR, PRIMARY KEY(context_id, variant_row_id))""")
    connection.execute("""CREATE VIEW IF NOT EXISTS ranked_evidence AS
        SELECT c.run_id, v.variant_id, r.gene, c.mode, r.rank, r.raw_score,
        r.features, c.provenance FROM ranking_values r
        JOIN ranking_contexts c USING(context_id)
        JOIN variants v ON v._row_id=r.variant_row_id""")


def rank_persistent(
    toolbox,
    ranker: PreliminaryRanker,
    branch: str,
    *,
    prefix: str = "",
    phenotype_override=None,
    annotation_version: str = "synthetic-annotations-v1",
) -> None:
    connection = toolbox.membership._connection
    _ranking_storage(connection)
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            "CREATE TEMP TABLE feature_batch (variant_row_id BIGINT, variant_id VARCHAR, "
            "gene VARCHAR, quality DOUBLE, rarity DOUBLE, consequence DOUBLE, phenotype "
            "DOUBLE, inheritance DOUBLE)"
        )
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT v.*, coalesce(p.score,0) AS phenotype_value,
                coalesce(i.score,0) AS inheritance_value
                FROM variants v JOIN candidate_membership m USING(variant_id)
                LEFT JOIN (SELECT gene, max(score) score FROM evidence WHERE run_id=? AND
method='resnik_bma' GROUP BY gene) p ON upper(v.gene)=p.gene
                LEFT JOIN (SELECT variant_id, max(score) score FROM evidence WHERE
run_id=? AND method<>'resnik_bma' GROUP BY variant_id) i USING(variant_id)
                WHERE m.run_id=? AND m.branch=? AND m.active ORDER BY v._row_id""",
                [ranker.run_id] * 3 + [branch],
            )
            columns = [item[0] for item in cursor.description]
            while batch := cursor.fetchmany(256):
                features = []
                for values in batch:
                    row = dict(zip(columns, values, strict=True))
                    gene = str(row.get("gene") or "").upper()
                    score = (
                        phenotype_override.get_gene_phenotype_score(gene).score
                        if phenotype_override
                        else row["phenotype_value"]
                    )
                    feature = ranker.features_for_row(
                        row,
                        phenotype_scores={gene: score},
                        inheritance_scores={row["variant_id"]: row["inheritance_value"]},
                    )
                    features.append(
                        {
                            "variant_row_id": row["_row_id"],
                            "variant_id": row["variant_id"],
                            "gene": gene,
                            **feature.model_dump(),
                        }
                    )
                connection.register("incoming_features", pa.Table.from_pylist(features))
                connection.execute("INSERT INTO feature_batch SELECT * FROM incoming_features")
                connection.unregister("incoming_features")
        finally:
            cursor.close()
        for mode, included in MODE_FEATURES.items():
            total = sum(ranker.weights[name] for name in included)
            if total <= 0:
                raise ValueError("Ablation weights sum to zero")
            # Names come exclusively from the fixed feature registry; weights are bound values.
            expression = " + ".join(f"{name} * ?" for name in included)
            provenance = evidence_provenance(
                run_id=ranker.run_id,
                tool_version="preliminary-ranking-v1",
                data_version=annotation_version,
                method="weighted_linear_score",
                parameters={"weights": ranker.weights, "included_features": list(included)},
                software=ranker.software,
            )
            name = prefix + mode
            context = connection.execute(
                "SELECT context_id FROM ranking_contexts WHERE run_id=? AND mode=?",
                [ranker.run_id, name],
            ).fetchone()
            if context:
                context_id = context[0]
                connection.execute("DELETE FROM ranking_values WHERE context_id=?", [context_id])
                connection.execute(
                    "UPDATE ranking_contexts SET provenance=? WHERE context_id=?",
                    [provenance.model_dump_json(), context_id],
                )
            else:
                context_id = connection.execute(
                    "INSERT INTO ranking_contexts SELECT coalesce(max(context_id),0)+1, ?, ?, ? "
                    "FROM ranking_contexts RETURNING context_id",
                    [ranker.run_id, name, provenance.model_dump_json()],
                ).fetchone()[0]
            connection.execute(
                f"""INSERT INTO ranking_values
                SELECT ?, variant_row_id, gene,
                row_number() OVER(ORDER BY score DESC, variant_id), score,
                to_json(struct_pack(quality:=quality, rarity:=rarity, consequence:=consequence,
                phenotype:=phenotype, inheritance:=inheritance))
                FROM (SELECT *, round(({expression})/?, 6) score FROM feature_batch)""",
                [context_id, *[ranker.weights[name] for name in included], total],
            )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("DROP TABLE IF EXISTS feature_batch")


def ranking_rows(
    connection, run_id: str, mode: str, *, limit: int | None = None
) -> Iterator[RankedVariant]:
    cursor = connection.cursor()
    try:
        query = (
            "SELECT variant_id, gene, rank, raw_score, features, provenance FROM "
            "ranked_evidence WHERE run_id=? AND mode=? ORDER BY rank"
        )
        params = [run_id, mode]
        if limit is not None:
            if not 1 <= limit <= 100:
                raise ValueError("Invalid top-k limit")
            query += " LIMIT ?"
            params.append(limit)
        cursor.execute(query, params)
        while batch := cursor.fetchmany(256):
            for row in batch:
                yield RankedVariant(
                    variant_id=row[0],
                    gene=row[1],
                    rank=row[2],
                    raw_score=row[3],
                    features=json.loads(row[4]),
                    provenance=json.loads(row[5]),
                    mode=mode.removeprefix("partial_"),
                )
    finally:
        cursor.close()
