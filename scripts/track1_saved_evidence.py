"""Bounded selection from authenticated local saved evidence.

The caller authenticates input journals and immutable paths, supervises resources,
and publishes private outputs. This module performs no discovery, downloads,
annotation, inference, new ranking, or publication. Import reads no input data.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
from decimal import Decimal
from pathlib import Path

import duckdb
import track1_submission_policy as policy

VERSION = "portable-saved-selection-v1"
STATUS = "completed_saved_computational_submission_draft"
BASE_MODE = "filtering_phenotype_inheritance"
MAX_ANCHORS = 100
MAX_PAIRS = 200_000
MAX_FILE = 4 * 1024**2
FETCH_ROWS = 128
QUERY_BYTES = 8 * 1024**2
CONFIG = {
    "memory_limit": "256MB",
    "threads": 1,
    "temp_directory": "",
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
}
LIMITATIONS = [
    "Computational research draft from authenticated saved evidence only; no expert review.",
    "The first 100 distinct source alleles in saved BASE_MODE order form a selection bound.",
    "Partners use the full persisted incident-pair relation, not a truncated review page.",
    "Saved pair confidence is an ordinal ordering key, not a causal probability or EPCR.",
    "Unknown or possible phase, genotype quality, linkage and affected-status uncertainty remain.",
    "The proband label is not an assertion of biological identity or affected status.",
    "Coordinates are saved source-normalized GRCh38 alleles, not reconstructed original records.",
    "The underlying corpus remains incomplete; deferred and unprocessed records are outside it.",
    "EPCR uses subjective q=.50 and decay=.80 with exact Decimal residual, without normalization.",
    "No new annotation, inference, inheritance computation, ranking, upload or submission.",
    "Causal/clinical correctness, whole-case coverage and official acceptance are not established.",
]


class SavedEvidenceRefusal(ValueError):
    """Refusals carry no supplied paths, identifiers, genotypes, or evidence."""


def require(value):
    if not value:
        raise SavedEvidenceRefusal


def same(first, second):
    require(
        json.dumps(first, sort_keys=True, allow_nan=False)
        == json.dumps(second, sort_keys=True, allow_nan=False)
    )


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum)
    return value


def text(value):
    require(type(value) is str and 0 < len(value.encode()) <= 4096)
    return value


def strict_json(raw):
    require(type(raw) is str and len(raw.encode()) <= MAX_FILE)

    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    def reject(_):
        raise SavedEvidenceRefusal

    def floating(raw_number):
        value = float(raw_number)
        require(math.isfinite(value))
        return value

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject, parse_float=floating)
    require(type(value) is dict)
    return value


class Queries:
    """Every payload result has a SQL row+byte admission before execute."""

    def __init__(self, db, budget):
        self.db, self.budget = db, budget

    def execute(self, sql, parameters=()):
        self.budget.check()
        self.budget.db = self.db
        result = self.db.execute(sql, parameters)
        self.budget.check()
        return result

    def scalar(self, sql, parameters=()):
        rows = self.rows(
            "SELECT * FROM (" + sql + ") AS scalar_result(value)", parameters, maximum=1
        )
        require(len(rows) == 1 and len(rows[0]) == 1)
        return next(iter(rows[0].values()))

    def rows(self, sql, parameters=(), *, maximum=128):
        require(type(maximum) is int and 0 < maximum <= FETCH_ROWS)
        schema = self.execute("DESCRIBE (" + sql + ")", parameters).fetchmany(65)
        require(0 < len(schema) <= 64)
        columns = [row[0] for row in schema]
        require(len(columns) == len(set(columns)))
        require(all(re.fullmatch("[A-Za-z_][A-Za-z_0-9]*", col) for col in columns))
        sizes = "+".join(
            f'coalesce(octet_length(encode(CAST("{col}" AS VARCHAR))),0)' for col in columns
        )
        count, size = self.execute(
            f"SELECT count(*),coalesce(sum({sizes}),0) FROM ({sql})", parameters
        ).fetchone()
        require(0 <= count <= maximum and 0 <= size <= QUERY_BYTES)
        cursor = self.execute(f"SELECT * FROM ({sql}) LIMIT {maximum}", parameters)
        rows = cursor.fetchmany(FETCH_ROWS)
        require(len(rows) == count)
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def one(self, sql, parameters=(), *, optional=False):
        rows = self.rows(sql, parameters, maximum=1)
        require(bool(rows) or optional)
        return rows[0] if rows else None


def provenance(value, run_id, analysis_head):
    require(
        type(value) is dict
        and value.get("run_id") == run_id
        and value.get("git_commit") == analysis_head
        and value.get("git_dirty") is False
    )


def decode_row(row, run_id, analysis_head):
    require(row.get("run_id") == run_id)
    row = dict(row)
    for name in ("payload", "provenance", "features"):
        if name in row:
            row[name] = strict_json(row[name])
            if name != "features":
                provenance(
                    row[name] if name == "provenance" else row[name]["provenance"],
                    run_id,
                    analysis_head,
                )
    return row


def coordinate_build(context):
    if (
        type(context) is list
        and len(context) == 2
        and all(type(item) is dict for item in context)
        and {item.get("provider") for item in context} == {"bcftools", "vep"}
        and all(item.get("genome_build") == "GRCh38" for item in context)
        and context[0].get("reference_sha256") == context[1].get("reference_sha256")
        and type(context[0].get("reference_sha256")) is str
        and re.fullmatch("[0-9a-f]{64}", context[0]["reference_sha256"])
    ):
        return "GRCh38"
    return None


def source_mapping(q, variant_id, source_sha256, build=None):
    mapping = q.one("SELECT * FROM source_map WHERE variant_id=?", [variant_id])
    require(mapping["variant_id"] == variant_id)
    allele = q.one("SELECT * FROM source_alleles WHERE allele_id=?", [text(mapping["allele_id"])])
    require(allele["allele_id"] == mapping["allele_id"])
    for name in ("source_row", "source_alt", "position"):
        require(type(allele[name]) is str and re.fullmatch("[0-9]+", allele[name]) is not None)
        require(int(allele[name]) > 0)
    require(allele["allele_id"] == f"r{allele['source_row']}_a{allele['source_alt']}")
    require(allele["disposition"] == "included")
    for name in ("chromosome", "reference", "alternate"):
        text(allele[name])
    strict_json(allele["genotype_calls_json"])
    return {
        "variant_id": variant_id,
        "source_mapping": mapping
        | {
            "source_sha256": source_sha256,
            "coordinate_interpretation": "normalized_"
            + (build or "build_unknown")
            + "_not_original_coordinates",
        },
        "normalized_allele": allele,
    }


def consequence_row(row):
    integer(row["ordinal"])
    return {
        "allele_id": row["allele_id"],
        "ordinal": row["ordinal"],
        "fields": strict_json(row["fields_json"]),
    }


def genotype_state(value):
    """Represent the saved target-ALT call; do not infer pedigree or inheritance."""
    if value is None or value in {".", "./.", ".|."}:
        return "unknown"
    require(type(value) is str and re.fullmatch(r"[01.](?:[/|][01.])?", value) is not None)
    parts = re.split(r"[/|]", value)
    if "." in parts:
        return "unknown"
    alleles = [int(part) for part in parts]
    if 1 not in alleles:
        return "reference"
    if len(alleles) == 1:
        return "haploid_alt"
    if alleles[0] == alleles[1] == 1:
        return "homozygous_alt"
    return "heterozygous"


def called_reference(value):
    return type(value) is str and re.fullmatch(r"0(?:[/|]0)?", value) is not None


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def candidate_dict(candidate):
    return dataclasses.asdict(candidate)


class SavedEvidence:
    """Bounded projections over the full authenticated saved database."""

    def __init__(self, q, context):
        self.q, self.context = q, context
        require(policy.MAX_ANCHORS == MAX_ANCHORS and policy.MAX_HYPOTHESES == 10)
        self.analysis_head = context["analysis_head"]
        require(
            type(self.analysis_head) is str
            and re.fullmatch("[0-9a-f]{40}", self.analysis_head) is not None
        )
        self.proband_label = text(context["expected_proband"])
        self.run_id = context["run_id"]
        self.build = coordinate_build(context["assembly_context"])
        require(self.build == "GRCh38")
        self.ranked_count = integer(
            q.scalar(
                "SELECT count(*) FROM ranked_evidence WHERE run_id=? AND mode=?",
                [self.run_id, BASE_MODE],
            ),
            1,
        )
        require(
            q.scalar(
                "SELECT count(*) FROM ranked_evidence r LEFT JOIN source_map m USING(variant_id) "
                "WHERE r.run_id=? AND r.mode=? AND m.allele_id IS NULL",
                [self.run_id, BASE_MODE],
            )
            == 0
        )
        for relation, key in (("source_map", "variant_id"), ("source_alleles", "allele_id")):
            require(
                q.scalar(
                    f"SELECT count(*) FROM (SELECT {key} FROM {relation} GROUP BY {key} "
                    f"HAVING {key} IS NULL OR count(*)<>1)"
                )
                == 0
            )
        require(
            q.scalar(
                "SELECT count(*) FROM (SELECT rank FROM ranked_evidence WHERE run_id=? AND mode=? "
                "GROUP BY rank HAVING rank IS NULL OR rank<1 OR count(*)<>1)",
                [self.run_id, BASE_MODE],
            )
            == 0
        )
        require(
            q.scalar(
                "SELECT max(rank) FROM ranked_evidence WHERE run_id=? AND mode=?",
                [self.run_id, BASE_MODE],
            )
            == self.ranked_count
        )
        self.pair_count = integer(
            q.scalar("SELECT count(*) FROM compound_pairs WHERE run_id=?", [self.run_id])
        )
        require(self.pair_count <= MAX_PAIRS)
        self.rank_sql = (
            "SELECT m.allele_id,min(r.rank) AS base_rank FROM ranked_evidence r "
            "JOIN source_map m USING(variant_id) WHERE r.run_id=? AND r.mode=? GROUP BY m.allele_id"
        )
        self.cache = {}
        self.pair_stats = {}
        self.last_witness = {}

    def pages(self, sql, parameters):
        total = integer(self.q.scalar("SELECT count(*) FROM (" + sql + ")", parameters))
        for offset in range(0, total, FETCH_ROWS):
            page = self.q.rows(sql + " LIMIT 128 OFFSET ?", [*parameters, offset])
            require(len(page) == min(FETCH_ROWS, total - offset))
            yield from page

    def candidate(self, source_id, *, retain=False):
        mapped = self.pages(
            "SELECT variant_id FROM source_map WHERE allele_id=? ORDER BY variant_id", [source_id]
        )
        min_rank = self.q.scalar(
            "SELECT min(base_rank) FROM (" + self.rank_sql + ") WHERE allele_id=?",
            [self.run_id, BASE_MODE, source_id],
        )
        # An absent base rank is a sorting sentinel only, never a newly assigned scientific rank.
        rank = self.ranked_count + 1 if min_rank is None else integer(min_rank, 1)
        details, genes, calls_seen, de_novo = [], set(), [], []
        detail_bytes = 0
        allele = None
        for key in mapped:
            variant_id = text(key["variant_id"])
            item = source_mapping(self.q, variant_id, self.context["source_sha256"], self.build)
            require(item["source_mapping"]["allele_id"] == source_id)
            current = item["normalized_allele"]
            if allele is None:
                allele = current
            else:
                same(allele, current)
            source = self.q.one("SELECT * FROM source_variants WHERE variant_id=?", [variant_id])
            variant = self.q.one("SELECT * FROM variants WHERE variant_id=?", [variant_id])
            same({k: v for k, v in variant.items() if k != "_row_id"}, source)
            for name in ("chromosome", "reference", "alternate"):
                require(source[name] == allele[name])
            require(
                type(source["position"]) is int and source["position"] == int(allele["position"])
            )
            source_calls = strict_json(source["genotype_calls_json"])
            same(source_calls, strict_json(allele["genotype_calls_json"]))
            require("genotype" in source)
            genotype = source["genotype"]
            require(genotype is None or type(genotype) is str)
            # All saved sample calls remain in the audit; no sample key is selected by inference.
            require(
                not source_calls
                or any(
                    type(call) is dict and call.get("genotype") == genotype
                    for call in source_calls.values()
                )
            )
            calls_seen.append(genotype)
            gene = str(source.get("gene") or "").strip().upper()
            mapping = item["source_mapping"]
            mapping_gene = (
                str(mapping.get("gene_symbol") or mapping.get("gene_id") or "").strip().upper()
            )
            if mapping_gene == "UNASSIGNED":
                mapping_gene = "UNKNOWN"
            require(gene == mapping_gene)
            if gene not in {"", ".", "UNKNOWN", "UNASSIGNED"}:
                genes.add(gene)
            evidence = self.q.rows(
                "SELECT * FROM evidence WHERE run_id=? AND variant_id=? AND method<>'resnik_bma' "
                "ORDER BY gene,method,data_version",
                [self.run_id, variant_id],
                maximum=32,
            )
            evidence = [decode_row(row, self.run_id, self.analysis_head) for row in evidence]
            for row in evidence:
                payload = row["payload"]
                require(payload["variant_id"] == variant_id)
                require(row["gene"] == payload["gene"] == gene)
                require(row["method"] == payload["model"])
                require(payload["proband_genotype"] == genotype)
                require(type(row["known"]) is bool and type(row["score"]) is float)
                require(
                    math.isfinite(row["score"]) and type(payload["quality_checks_passed"]) is bool
                )
                require(row["data_version"] == payload["provenance"]["data_version"])
                if row["method"] == "de_novo":
                    require(type(payload["fit"]) in (int, float) and math.isfinite(payload["fit"]))
                    de_novo.append(
                        (
                            row["known"] and payload["fit"] > 0,
                            called_reference(payload["mother_genotype"])
                            and called_reference(payload["father_genotype"]),
                            payload["quality_checks_passed"],
                        )
                    )
            phenotype = [
                decode_row(row, self.run_id, self.analysis_head)
                for row in self.q.rows(
                    "SELECT * FROM evidence WHERE run_id=? AND gene=? AND method='resnik_bma' "
                    "ORDER BY variant_id,data_version",
                    [self.run_id, gene],
                    maximum=8,
                )
            ]
            for row in phenotype:
                require(type(row["known"]) is bool and type(row["score"]) is float)
                require(math.isfinite(row["score"]) and row["gene"] == gene)
                require(row["data_version"] == row["payload"]["provenance"]["data_version"])
            ordinal = item["source_mapping"]["selected_consequence_ordinal"]
            consequence = None
            if ordinal is not None:
                require(type(ordinal) is str and re.fullmatch("[0-9]+", ordinal) is not None)
                consequence = consequence_row(
                    self.q.one(
                        "SELECT * FROM source_consequences WHERE allele_id=? AND ordinal=?",
                        [source_id, int(ordinal)],
                    )
                )
            ranked = self.q.one(
                "SELECT * FROM ranked_evidence WHERE run_id=? AND mode=? AND variant_id=?",
                [self.run_id, BASE_MODE, variant_id],
                optional=True,
            )
            if ranked is not None:
                ranked = decode_row(ranked, self.run_id, self.analysis_head)
                require(ranked["gene"] == gene)
                require(type(ranked["raw_score"]) is float and math.isfinite(ranked["raw_score"]))
                require(
                    set(ranked["features"])
                    == {"quality", "rarity", "consequence", "phenotype", "inheritance"}
                )
                require(
                    all(
                        type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
                        for value in ranked["features"].values()
                    )
                )
            saved_row = {
                "source_mapping": item["source_mapping"],
                "variant": source,
                "selected_consequence": consequence,
                "saved_base_rank": ranked,
                "inheritance_evidence": evidence,
                "phenotype_evidence": phenotype,
            }
            detail_bytes += len(json_bytes(saved_row))
            require(detail_bytes <= MAX_FILE)
            details.append(saved_row)
        require(bool(details))
        require(all(value == calls_seen[0] for value in calls_seen))
        state = genotype_state(calls_seen[0])
        require(all(re.fullmatch("[ACGT]+", allele[key]) for key in ("reference", "alternate")))
        require(allele["reference"] != allele["alternate"])
        # Flags must be supported by the SAME saved de-novo evidence row.
        positive = any(all(flags) for flags in de_novo)
        candidate = policy.Candidate(
            policy.Allele(
                allele["chromosome"],
                int(allele["position"]),
                allele["reference"],
                allele["alternate"],
            ),
            rank,
            tuple(sorted(genes)),
            state,
            source_id,
            positive,
            positive,
            positive,
        )
        detail = {
            "candidate": candidate_dict(candidate),
            "normalized_allele": allele,
            "saved_minimum_base_rank": min_rank,
            "sort_rank_is_unranked_sentinel": min_rank is None,
            "all_gene_transcript_mappings": details,
        }
        if retain:
            self.cache[source_id] = candidate
        return candidate, detail

    def anchors(self):
        rows = self.q.rows(
            self.rank_sql + " ORDER BY base_rank,allele_id LIMIT 100",
            [self.run_id, BASE_MODE],
            maximum=MAX_ANCHORS,
        )
        require(bool(rows) and len({row["allele_id"] for row in rows}) == len(rows))
        anchors = []
        for row in rows:
            candidate, _ = self.candidate(row["allele_id"], retain=True)
            require(candidate.rank == row["base_rank"])
            anchors.append(candidate)
        return anchors

    def pairs(self, anchor):
        """Order the complete saved incident relation before bounded payload reads."""
        incident = (
            "SELECT p.*,ma.allele_id AS source_a,mb.allele_id AS source_b,"
            "CAST(json_extract_string(p.payload,'$.confidence') AS DOUBLE) AS saved_confidence,"
            "coalesce(CASE WHEN ma.allele_id=? THEN rb.base_rank ELSE ra.base_rank END,?) "
            "AS partner_rank,least(ma.allele_id,mb.allele_id) AS pair_first,"
            "greatest(ma.allele_id,mb.allele_id) AS pair_second "
            "FROM compound_pairs p LEFT JOIN source_map ma ON ma.variant_id=p.variant_a "
            "LEFT JOIN source_map mb ON mb.variant_id=p.variant_b "
            "LEFT JOIN (" + self.rank_sql + ") ra ON ra.allele_id=ma.allele_id "
            "LEFT JOIN (" + self.rank_sql + ") rb ON rb.allele_id=mb.allele_id "
            "WHERE p.run_id=? AND (ma.allele_id=? OR mb.allele_id=?)"
        )
        params = [
            anchor.source_id,
            self.ranked_count + 1,
            self.run_id,
            BASE_MODE,
            self.run_id,
            BASE_MODE,
            self.run_id,
            anchor.source_id,
            anchor.source_id,
        ]
        total = integer(self.q.scalar("SELECT count(*) FROM (" + incident + ")", params))
        require(total <= MAX_PAIRS)
        require(
            self.q.scalar(
                "SELECT count(*) FROM ("
                + incident
                + ") WHERE source_a IS NULL OR source_b IS NULL "
                "OR saved_confidence IS NULL OR NOT isfinite(saved_confidence) "
                "OR saved_confidence<0 OR saved_confidence>1",
                params,
            )
            == 0
        )
        # Highest-confidence witness wins per unordered SOURCE pair, after endpoint mapping.
        dedup = (
            "SELECT * EXCLUDE (duplicate_number) FROM (SELECT *,row_number() OVER "
            "(PARTITION BY pair_first,pair_second ORDER BY saved_confidence DESC,partner_rank,"
            "variant_a,variant_b) AS duplicate_number FROM (" + incident + ")) "
            "WHERE duplicate_number=1"
        )
        distinct = integer(self.q.scalar("SELECT count(*) FROM (" + dedup + ")", params))
        stats = {
            "saved_incident_row_count": total,
            "distinct_source_pair_count": distinct,
            "examined_distinct_source_pairs": 0,
            "ordering": "confidence_desc_partner_min_base_rank_source_ids_variant_ids",
            "full_saved_relation_ordered_before_paging": True,
            "page_rows": FETCH_ROWS,
        }
        self.pair_stats[anchor.source_id] = stats
        ordered = (
            dedup + " ORDER BY saved_confidence DESC,partner_rank,pair_first,pair_second,"
            "variant_a,variant_b"
        )
        for raw in self.pages(ordered, params):
            row = decode_row(raw, self.run_id, self.analysis_head)
            payload = row["payload"]
            require(
                payload["variant_a"] == row["variant_a"]
                and payload["variant_b"] == row["variant_b"]
            )
            require(type(payload["confidence"]) in (int, float))
            require(payload["confidence"] == row["saved_confidence"])
            require(type(payload["phase"]) is str)
            partner_id = row["source_b"] if row["source_a"] == anchor.source_id else row["source_a"]
            if partner_id in self.cache:
                partner = self.cache[partner_id]
            else:
                partner, _ = self.candidate(partner_id)
            require(partner.rank == row["partner_rank"])
            gene = text(payload["gene"]).strip().upper()
            # The exact gene-specific row witnesses must agree, not just the allele's gene union.
            for variant_id in (row["variant_a"], row["variant_b"]):
                row_gene = self.q.scalar(
                    "SELECT gene FROM source_variants WHERE variant_id=?", [variant_id]
                )
                require(str(row_gene or "").strip().upper() == gene)
            contradiction = payload.get("observed_contradiction", False)
            require(type(contradiction) is bool)
            pair = policy.Pair(
                anchor,
                partner,
                gene,
                Decimal(str(payload["confidence"])),
                payload["phase"],
                contradiction,
            )
            stats["examined_distinct_source_pairs"] += 1
            self.last_witness[anchor.source_id] = {
                "variant_a": row["variant_a"],
                "variant_b": row["variant_b"],
                "source_a": row["source_a"],
                "source_b": row["source_b"],
            }
            yield pair

    def extract(self):
        anchors = self.anchors()
        selection = policy.select_hypotheses(anchors, self.pairs)
        require(bool(selection.hypotheses))
        chosen = []
        selected_details = {}
        selected_bytes = 0
        for number, hypothesis in enumerate(selection.hypotheses, 1):
            witness = None
            _, detail = self.candidate(hypothesis.first.source_id)
            selected_details[hypothesis.first.source_id] = detail
            selected_bytes += len(json_bytes(detail))
            require(selected_bytes <= MAX_FILE)
            if hypothesis.second is not None:
                source_id = hypothesis.second.source_id
                if source_id not in selected_details:
                    _, detail = self.candidate(source_id)
                    selected_details[source_id] = detail
                    selected_bytes += len(json_bytes(detail))
                    require(selected_bytes <= MAX_FILE)
                key = self.last_witness[hypothesis.first.source_id]
                require(
                    {key["source_a"], key["source_b"]} == {hypothesis.first.source_id, source_id}
                )
                witness = decode_row(
                    self.q.one(
                        "SELECT * FROM compound_pairs WHERE run_id=? "
                        "AND variant_a=? AND variant_b=?",
                        [self.run_id, key["variant_a"], key["variant_b"]],
                    ),
                    self.run_id,
                    self.analysis_head,
                )
            chosen.append(
                {
                    "submission_rank": number,
                    "first": candidate_dict(hypothesis.first),
                    "second": candidate_dict(hypothesis.second) if hypothesis.second else None,
                    "reason": hypothesis.reason,
                    "phase_status": hypothesis.phase_status,
                    "gene": hypothesis.gene,
                    "epcr": str(policy.rank_epcr(number)),
                    "saved_pair_witness": witness,
                }
            )
        audit = {
            "schema_version": 1,
            "version": VERSION,
            "status": STATUS,
            "policy_version": policy.POLICY_VERSION,
            "expert_review_performed": False,
            "proband_label": self.proband_label,
            "proband_label_is_biological_identity": False,
            "scope": {
                "base_mode": BASE_MODE,
                "saved_ranked_rows": self.ranked_count,
                "anchor_bound_distinct_source_alleles": MAX_ANCHORS,
                "anchors_considered": len(anchors),
                "hypothesis_limit": 10,
                "saved_global_pair_count": self.pair_count,
                "unranked_partner_sort_sentinel": self.ranked_count + 1,
                "all_gene_transcript_mappings_preserved": True,
                "selection_is_new_computational_policy": True,
            },
            "source_binding": self.context["source_binding"],
            "coverage": self.context["coverage"],
            "saved_run_declarations": self.context["saved_run_declarations"],
            "assembly_context": self.context["assembly_context"],
            "resource_provenance": self.context["assembly_context"],
            "source_sha256": self.context["source_sha256"],
            "source_plan_sha256": self.context["plan_manifest_sha256"],
            "anchor_order": [candidate.source_id for candidate in anchors],
            "hypotheses": chosen,
            "decisions": [dataclasses.asdict(decision) for decision in selection.audit],
            "incident_pair_audit": self.pair_stats,
            "allele_evidence": selected_details,
            "epcr": {
                "q": ".50",
                "decay": ".80",
                "subjective_uncalibrated": True,
                "renormalized": False,
                "unallocated_mass": str(policy.residual_mass(len(chosen))),
            },
            "limitations": LIMITATIONS,
        }
        raw = policy.render_csv(selection, self.proband_label)
        require(len(raw) <= MAX_FILE and len(json_bytes(audit)) <= MAX_FILE)
        return raw, audit


def read_data(context, budget, variants_sha256):
    """Read authenticated local files and return CSV bytes plus a bounded audit.

    ``context`` supplies paths for database, candidate_map, alleles, variants and
    consequences; run_id, analysis_head, source_sha256, plan_manifest_sha256,
    assembly_context, coverage, saved_run_declarations, source_binding and
    expected_proband. ``budget.check()`` must fail on exhausted resources;
    ``budget.db`` exposes the active connection for supervisor interruption.
    The caller verifies input immutability before publishing these return values.
    """
    paths = {
        name: Path(context["paths"][name])
        for name in ("database", "candidate_map", "alleles", "variants", "consequences")
    }
    require(
        all(path.is_absolute() and path.resolve(strict=True) == path for path in paths.values())
    )
    database = paths["database"]
    require(not os.path.lexists(str(database) + ".wal"))
    budget.check()
    db = duckdb.connect(str(database), read_only=True, config=CONFIG)
    budget.db = db
    try:
        # Install the exact local-file allowlist before disabling external access.
        # Extension auto-install/load is already disabled when the database opens.
        db.execute("SET allowed_paths=?", [[str(path) for path in paths.values()]])
        db.execute("SET enable_external_access=false")
        db.execute("SET enable_progress_bar=false")
        db.execute("SET lock_configuration=true")
        for name, alias in (
            ("candidate_map", "source_map"),
            ("alleles", "source_alleles"),
            ("variants", "source_variants"),
            ("consequences", "source_consequences"),
        ):
            budget.check()
            # DuckDB's relation API binds the filename without interpolated SQL.
            db.register(alias, db.read_parquet(str(paths[name])))
        q = Queries(db, budget)
        identity = q.one("SELECT * FROM run_identity")
        require(identity["run_id"] == context["run_id"])
        require(type(identity["schema_version"]) is int and identity["schema_version"] == 1)
        require(identity["source_hash"] == variants_sha256)
        result = SavedEvidence(q, context).extract()
        budget.check()
    finally:
        budget.db = None
        db.close()
    require(not os.path.lexists(str(database) + ".wal"))
    return result
