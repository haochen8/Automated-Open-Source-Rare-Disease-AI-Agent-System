"""Batch deterministic features, SQL ordering, and bounded result summaries."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pyarrow as pa

from rare_disease_agent.ranking.schemas import RankedVariant
from rare_disease_agent.ranking.scoring import MODE_FEATURES, PreliminaryRanker
from rare_disease_agent.reporting.provenance import evidence_provenance


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
    connection.execute("""CREATE TABLE IF NOT EXISTS ranked_evidence (
        run_id VARCHAR, variant_id VARCHAR, gene VARCHAR, mode VARCHAR, rank BIGINT,
        raw_score DOUBLE, features VARCHAR, provenance VARCHAR,
        PRIMARY KEY(run_id, variant_id, mode))""")
    connection.execute(
        "CREATE TEMP TABLE feature_batch (variant_id VARCHAR, gene VARCHAR, quality "
        "DOUBLE, rarity DOUBLE, consequence DOUBLE, phenotype DOUBLE, inheritance "
        "DOUBLE)"
    )
    try:
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
                        {"variant_id": row["variant_id"], "gene": gene, **feature.model_dump()}
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
            connection.execute(
                f"""INSERT OR REPLACE INTO ranked_evidence
                SELECT ?, variant_id, gene, ?, row_number() OVER(ORDER BY score DESC, variant_id),
                score,
                to_json(struct_pack(quality:=quality, rarity:=rarity, consequence:=consequence,
                phenotype:=phenotype, inheritance:=inheritance)), ?
                FROM (SELECT *, round(({expression})/?, 6) score FROM feature_batch)""",
                [
                    ranker.run_id,
                    prefix + mode,
                    provenance.model_dump_json(),
                    *[ranker.weights[name] for name in included],
                    total,
                ],
            )
    finally:
        connection.execute("DROP TABLE feature_batch")


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
