"""Saved-selection regressions with deliberately synthetic evidence only."""

import csv
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import duckdb
import pytest


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
policy = module("track1_submission_policy", SCRIPTS / "track1_submission_policy.py")
x = module("track1_saved_evidence", SCRIPTS / "track1_saved_evidence.py")
ANALYSIS_HEAD = "1" * 40


class Budget:
    db = None

    def check(self):
        pass


def provenance(method="synthetic"):
    return {
        "run_id": "synthetic-saved-run",
        "git_commit": ANALYSIS_HEAD,
        "git_dirty": False,
        "method": method,
        "data_version": "synthetic-v1",
    }


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def saved(tmp_path):
    db = duckdb.connect(str(tmp_path / "saved.duckdb"), config=x.CONFIG)
    db.execute("BEGIN TRANSACTION")
    db.execute(
        "CREATE TABLE variants (_row_id BIGINT,variant_id VARCHAR,gene VARCHAR,"
        "chromosome VARCHAR,position BIGINT,reference VARCHAR,alternate VARCHAR,"
        "genotype VARCHAR,genotype_calls_json VARCHAR)"
    )
    db.execute(
        "CREATE TABLE source_map (variant_id VARCHAR,allele_id VARCHAR,gene_id VARCHAR,"
        "gene_symbol VARCHAR,selected_consequence_ordinal VARCHAR)"
    )
    db.execute(
        "CREATE TABLE source_alleles (allele_id VARCHAR,identity VARCHAR,source_row VARCHAR,"
        "source_alt VARCHAR,chromosome VARCHAR,position VARCHAR,reference VARCHAR,"
        "alternate VARCHAR,filter VARCHAR,genotype_calls_json VARCHAR,disposition VARCHAR)"
    )
    db.execute(
        "CREATE TABLE source_consequences (allele_id VARCHAR,ordinal BIGINT,fields_json VARCHAR)"
    )
    db.execute(
        "CREATE TABLE ranked_evidence (run_id VARCHAR,variant_id VARCHAR,gene VARCHAR,"
        "mode VARCHAR,rank BIGINT,raw_score DOUBLE,features VARCHAR,provenance VARCHAR)"
    )
    db.execute(
        "CREATE TABLE evidence (run_id VARCHAR,variant_id VARCHAR,gene VARCHAR,"
        "method VARCHAR,data_version VARCHAR,score DOUBLE,known BOOLEAN,payload VARCHAR)"
    )
    db.execute(
        "CREATE TABLE compound_pairs (run_id VARCHAR,variant_a VARCHAR,"
        "variant_b VARCHAR,payload VARCHAR)"
    )
    calls = json.dumps({"sample_1": {"genotype": "0/1", "quality": None}})
    variants = [
        (n + 1, f"v{n:03d}", "GENE1", "1", n, "A", "G", "0/1", calls) for n in range(1, 136)
    ]
    variants.insert(0, (1, "v001extra", "GENE2", "1", 1, "A", "G", "0/1", calls))
    db.executemany("INSERT INTO variants VALUES (?,?,?,?,?,?,?,?,?)", variants)
    db.executemany(
        "INSERT INTO source_map VALUES (?,?,?,?,?)",
        [
            (row[1], f"r{row[4]}_a{2 if row[4] == 1 else 1}", row[2], row[2], "0")
            for row in variants
        ],
    )
    db.executemany(
        "INSERT INTO source_alleles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                f"r{n}_a{2 if n == 1 else 1}",
                f"identity{n}",
                str(n),
                "2" if n == 1 else "1",
                "1",
                str(n),
                "A",
                "G",
                "PASS",
                calls,
                "included",
            )
            for n in range(1, 136)
        ],
    )
    db.executemany(
        "INSERT INTO source_consequences VALUES (?,?,?)",
        [
            (f"r{n}_a{2 if n == 1 else 1}", 0, json.dumps({"Feature": f"synthetic_transcript_{n}"}))
            for n in range(1, 136)
        ],
    )
    for row in variants:
        db.execute(
            "INSERT INTO ranked_evidence VALUES (?,?,?,?,?,?,?,?)",
            [
                "synthetic-saved-run",
                row[1],
                row[2],
                x.BASE_MODE,
                row[0],
                0.5,
                json.dumps(
                    dict(quality=0.0, rarity=0.1, consequence=0.2, phenotype=0.0, inheritance=0.5)
                ),
                json.dumps(provenance()),
            ],
        )
        payload = {
            "variant_id": row[1],
            "gene": row[2],
            "model": "de_novo",
            "fit": 0.4,
            "proband_genotype": "0/1",
            "mother_genotype": None,
            "father_genotype": None,
            "quality_checks_passed": True,
            "evidence": [],
            "warnings": ["Synthetic affected status and parental phase remain unknown."],
            "provenance": provenance("de_novo"),
        }
        db.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)",
            [
                "synthetic-saved-run",
                row[1],
                row[2],
                "de_novo",
                "synthetic-v1",
                0.4,
                True,
                json.dumps(payload),
            ],
        )
    for n in range(2, 136):
        payload = {
            "gene": "GENE1",
            "variant_a": "v001",
            "variant_b": f"v{n:03d}",
            "confidence": 0.99 if n == 130 else 0.55,
            "phase": "unknown",
            "evidence": [],
            "warnings": ["Synthetic phase unknown."],
            "provenance": provenance("compound_heterozygous"),
        }
        db.execute(
            "INSERT INTO compound_pairs VALUES (?,?,?,?)",
            ["synthetic-saved-run", "v001", f"v{n:03d}", json.dumps(payload)],
        )
    db.execute("CREATE TABLE source_variants AS SELECT * EXCLUDE (_row_id) FROM variants")
    context = {
        "run_id": "synthetic-saved-run",
        "analysis_head": ANALYSIS_HEAD,
        "expected_proband": "SYNTHETIC_PROBAND",
        "source_sha256": "a" * 64,
        "plan_manifest_sha256": "b" * 64,
        "assembly_context": [
            {"provider": provider, "genome_build": "GRCh38", "reference_sha256": "c" * 64}
            for provider in ("bcftools", "vep")
        ],
        "saved_run_declarations": {
            "sample_phenotype_linkage_confirmed": False,
            "confirmed_affected_sample": False,
            "identity_reverified_by_this_packet": False,
        },
        "coverage": {"whole_genome_analysis_complete": False, "unprocessed_source_records": 8},
        "source_binding": {"run_id": "synthetic-saved-run", "database_sha256": "d" * 64},
    }
    db.execute("COMMIT")
    yield db, context, tmp_path
    db.close()


def reader(saved):
    db, context, _ = saved
    return x.SavedEvidence(x.Queries(db, Budget()), context)


def test_full_saved_pair_order_and_distinct_source_anchor_window(saved):
    data = reader(saved)
    raw, audit = data.extract()
    # Frozen output for this deliberately synthetic fixture; detects changes
    # to selection, source-normalized coordinates, notes and exact EPCR bytes.
    assert hashlib.sha256(raw).hexdigest() == (
        "ef480cd385955d79039d58b827d7539cb8af2bc65e7de0b87ca7bc79e1b7010d"
    )
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    assert len(rows) == 10
    assert audit["scope"]["anchors_considered"] == 100
    assert audit["anchor_order"][-1] == "r100_a1"
    assert audit["hypotheses"][0]["second"]["source_id"] == "r130_a1"
    assert rows[0]["pos_2"] == "130"
    assert rows[0]["finding_type"] == "primary"
    assert all(row["proband_id"] == "SYNTHETIC_PROBAND" for row in rows)
    first = audit["allele_evidence"]["r1_a2"]
    assert first["candidate"]["genotype"] == "heterozygous"
    assert len(first["all_gene_transcript_mappings"]) == 2
    assert first["normalized_allele"]["source_alt"] == "2"
    assert first["all_gene_transcript_mappings"][0]["selected_consequence"]["fields"] == {
        "Feature": "synthetic_transcript_1"
    }
    assert audit["hypotheses"][0]["phase_status"] == "unknown"
    assert audit["hypotheses"][0]["saved_pair_witness"]["payload"]["confidence"] == 0.99
    assert audit["incident_pair_audit"]["r1_a2"]["saved_incident_row_count"] == 134
    assert audit["incident_pair_audit"]["r1_a2"]["examined_distinct_source_pairs"] == 1
    assert len(audit["allele_evidence"]) == 11
    assert audit["saved_run_declarations"]["confirmed_affected_sample"] is False
    assert audit["expert_review_performed"] is False
    assert audit["epcr"]["renormalized"] is False
    assert len(x.json_bytes(audit)) < x.MAX_FILE


def test_incident_pairs_beyond_first_page_and_source_pair_dedup(saved):
    db, _, _ = saved
    # A second saved gene row for one source pair cannot multiply the pair stream.
    db.execute(
        "INSERT INTO variants SELECT 137,'v130extra','GENE2',chromosome,position,"
        "reference,alternate,genotype,genotype_calls_json FROM variants WHERE variant_id='v130'"
    )
    db.execute(
        "INSERT INTO source_variants SELECT * EXCLUDE (_row_id) FROM variants "
        "WHERE variant_id='v130extra'"
    )
    db.execute("INSERT INTO source_map VALUES ('v130extra','r130_a1','GENE2','GENE2','0')")
    db.execute(
        "INSERT INTO compound_pairs SELECT run_id,'v001extra','v130extra',"
        "json_merge_patch(payload,?) FROM compound_pairs WHERE variant_b='v130'",
        [
            json.dumps(
                {
                    "confidence": 0.4,
                    "gene": "GENE2",
                    "variant_a": "v001extra",
                    "variant_b": "v130extra",
                }
            )
        ],
    )
    data = reader(saved)
    anchor, _ = data.candidate("r1_a2")
    pairs = list(data.pairs(anchor))
    assert len(pairs) == 134
    assert pairs[0].second.source_id == "r130_a1"
    assert pairs[-1].second.source_id == "r135_a1"
    assert data.pair_stats[anchor.source_id]["saved_incident_row_count"] == 135
    assert data.pair_stats[anchor.source_id]["distinct_source_pair_count"] == 134


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE variants SET genotype='1/1' WHERE variant_id='v001'",
        "UPDATE source_alleles SET reference='T' WHERE allele_id='r1_a2'",
        "UPDATE source_variants SET genotype_calls_json='{}' WHERE variant_id='v001'",
        'UPDATE evidence SET payload=json_merge_patch(payload,\'{"proband_genotype":"1/1"}\') '
        "WHERE variant_id='v001'",
    ],
)
def test_reconciliation_mismatch_fails_closed(saved, mutation):
    saved[0].execute(mutation)
    with pytest.raises(ValueError):
        reader(saved).anchors()


def test_unknown_build_fails_closed(saved):
    _, context, _ = saved
    context["assembly_context"][0]["genome_build"] = "GRCh37"
    with pytest.raises(ValueError):
        reader(saved)


def test_native_pair_budget_fails_closed(saved):
    db, _, _ = saved
    db.execute(
        "INSERT INTO compound_pairs SELECT 'synthetic-saved-run','x','y','{}' FROM range(200000)"
    )
    with pytest.raises(ValueError):
        reader(saved)


def test_saved_de_novo_requires_both_reference_parents_quality_on_same_row(saved):
    db, _, _ = saved
    data = reader(saved)
    assert data.candidate("r1_a2")[0].de_novo_positive is False
    db.execute(
        "UPDATE evidence SET payload=json_merge_patch(payload,?) WHERE variant_id='v001'",
        [
            json.dumps(
                {
                    "fit": 0.5,
                    "mother_genotype": "0/0",
                    "father_genotype": "0/0",
                    "quality_checks_passed": True,
                }
            )
        ],
    )
    data = reader(saved)
    candidate, _ = data.candidate("r1_a2")
    assert candidate.de_novo_positive and candidate.both_parents_called_reference
    _, audit = data.extract()
    assert audit["hypotheses"][0]["second"] is None
    assert "de_novo" in audit["hypotheses"][0]["reason"]


@pytest.mark.parametrize(
    ("gene_id", "gene"), [("ENSG_SYNTHETIC", "ENSG_SYNTHETIC"), ("unassigned", "UNKNOWN")]
)
def test_gene_symbol_missing_uses_exact_ingester_fallback(saved, gene_id, gene):
    db, _, _ = saved
    db.execute(
        "UPDATE source_map SET gene_symbol=NULL,gene_id=? WHERE allele_id='r2_a1'", [gene_id]
    )
    for table in ("variants", "source_variants", "ranked_evidence"):
        db.execute(f"UPDATE {table} SET gene=? WHERE variant_id='v002'", [gene])
    db.execute(
        "UPDATE evidence SET gene=?,payload=json_merge_patch(payload,?) WHERE variant_id='v002'",
        [gene, json.dumps({"gene": gene})],
    )
    candidate, detail = reader(saved).candidate("r2_a1")
    assert candidate.genes == (() if gene == "UNKNOWN" else (gene,))
    assert detail["all_gene_transcript_mappings"][0]["source_mapping"]["gene_symbol"] is None


def test_mapping_details_fail_closed_at_existing_json_budget(saved, monkeypatch):
    monkeypatch.setattr(x, "MAX_FILE", 100)
    with pytest.raises(ValueError):
        reader(saved).candidate("r1_a2")


def test_unranked_partner_retains_absent_rank_with_sorting_sentinel(saved):
    db, _, _ = saved
    db.execute("DELETE FROM ranked_evidence WHERE variant_id='v135'")
    db.execute(
        "UPDATE compound_pairs SET payload=json_merge_patch(payload,?) WHERE variant_b='v135'",
        [json.dumps({"confidence": 1.0})],
    )
    _, audit = reader(saved).extract()
    assert audit["hypotheses"][0]["second"]["source_id"] == "r135_a1"
    detail = audit["allele_evidence"]["r135_a1"]
    assert detail["saved_minimum_base_rank"] is None
    assert detail["sort_rank_is_unranked_sentinel"] is True
    assert detail["candidate"]["rank"] == audit["scope"]["unranked_partner_sort_sentinel"]


def seal_saved(saved):
    db, context, tmp_path = saved
    paths = {"database": tmp_path / "saved.duckdb"}
    for name, relation in (
        ("candidate_map", "source_map"),
        ("alleles", "source_alleles"),
        ("variants", "source_variants"),
        ("consequences", "source_consequences"),
    ):
        paths[name] = tmp_path / (name + ".parquet")
        db.execute(f"COPY {relation} TO ? (FORMAT PARQUET)", [str(paths[name])])
        db.execute(f"DROP TABLE {relation}")
    db.execute(
        "CREATE TABLE run_identity (run_id VARCHAR,source_hash VARCHAR,schema_version INTEGER)"
    )
    db.execute(
        "INSERT INTO run_identity VALUES (?,?,1)", [context["run_id"], sha(paths["variants"])]
    )
    db.close()
    context["paths"] = paths
    return paths


def test_readonly_database_source_identity_and_no_mutation(saved):
    paths = seal_saved(saved)
    context = saved[1]
    before = {path: sha(path) for path in paths.values()}
    raw, _ = x.read_data(context, Budget(), sha(paths["variants"]))
    assert raw.startswith(b"proband_id,")
    assert before == {path: sha(path) for path in paths.values()}


def test_readonly_database_wrong_source_identity_fails_closed(saved):
    paths = seal_saved(saved)
    context = saved[1]
    with pytest.raises(ValueError):
        x.read_data(context, Budget(), "e" * 64)
    assert not paths["database"].with_suffix(".duckdb.wal").exists()


@pytest.mark.parametrize(
    ("table", "column", "update"),
    [
        ("ranked_evidence", "provenance", {"git_commit": "2" * 40}),
        ("ranked_evidence", "provenance", {"git_dirty": True}),
        ("ranked_evidence", "provenance", {"run_id": "other-synthetic-run"}),
        ("evidence", "payload", {"provenance": {"git_commit": "2" * 40}}),
        ("evidence", "payload", {"provenance": {"git_dirty": True}}),
        ("compound_pairs", "payload", {"provenance": {"git_dirty": True}}),
    ],
)
def test_saved_evidence_provenance_is_bound_to_supplied_clean_analysis(
    saved, table, column, update
):
    db = saved[0]
    db.execute(f"UPDATE {table} SET {column}=json_merge_patch({column},?)", [json.dumps(update)])
    data = reader(saved)
    with pytest.raises(x.SavedEvidenceRefusal):
        candidate, _ = data.candidate("r1_a2")
        if table == "compound_pairs":
            next(data.pairs(candidate))


def test_supplied_analysis_head_must_match_saved_provenance(saved):
    saved[1]["analysis_head"] = "2" * 40
    with pytest.raises(x.SavedEvidenceRefusal):
        reader(saved).candidate("r1_a2")


def test_saved_selection_keeps_fixed_policy_bounds(saved, monkeypatch):
    monkeypatch.setattr(x.policy, "MAX_HYPOTHESES", 11)
    with pytest.raises(x.SavedEvidenceRefusal):
        reader(saved)


@pytest.mark.parametrize(
    "mutation",
    [
        "INSERT INTO source_map SELECT * FROM source_map WHERE variant_id='v001'",
        "INSERT INTO source_alleles SELECT * FROM source_alleles WHERE allele_id='r1_a2'",
        "UPDATE ranked_evidence SET rank=1 WHERE variant_id='v001'",
        "DELETE FROM source_map WHERE variant_id='v001'",
    ],
)
def test_mapping_and_rank_identity_refusals_are_independent(saved, mutation):
    saved[0].execute(mutation)
    with pytest.raises(x.SavedEvidenceRefusal):
        reader(saved)


def test_de_novo_flags_cannot_be_combined_across_saved_rows(saved):
    db = saved[0]
    # One gene row supplies reference parents but failed quality; the other
    # supplies quality with unknown parents. Neither is sufficient on its own.
    db.execute(
        "UPDATE evidence SET payload=json_merge_patch(payload,?) WHERE variant_id='v001'",
        [
            json.dumps(
                {"mother_genotype": "0/0", "father_genotype": "0/0", "quality_checks_passed": False}
            )
        ],
    )
    candidate, detail = reader(saved).candidate("r1_a2")
    assert len(detail["all_gene_transcript_mappings"]) == 2
    assert not candidate.de_novo_positive
    assert not candidate.both_parents_called_reference
    assert not candidate.de_novo_quality_pass


@pytest.mark.parametrize(
    ("known", "fit", "mother", "father", "quality"),
    [
        (False, 0.5, "0/0", "0/0", True),
        (True, 0.0, "0/0", "0/0", True),
        (True, 0.5, "0/.", "0/0", True),
        (True, 0.5, "0/0", None, True),
        (True, 0.5, "0/0", "0/0", False),
    ],
)
def test_de_novo_requires_each_saved_condition(saved, known, fit, mother, father, quality):
    payload = json.loads(
        saved[0].execute("SELECT payload FROM evidence WHERE variant_id='v001'").fetchone()[0]
    )
    payload.update(
        fit=fit, mother_genotype=mother, father_genotype=father, quality_checks_passed=quality
    )
    saved[0].execute(
        "UPDATE evidence SET known=?,payload=? WHERE variant_id='v001'",
        [known, json.dumps(payload)],
    )
    candidate, _ = reader(saved).candidate("r1_a2")
    assert not candidate.de_novo_positive


def test_pair_witness_must_match_its_exact_gene_rows(saved):
    saved[0].execute(
        "UPDATE compound_pairs SET payload=json_merge_patch(payload,?) WHERE variant_b='v130'",
        [json.dumps({"gene": "GENE2"})],
    )
    data = reader(saved)
    candidate, _ = data.candidate("r1_a2")
    assert candidate.genes == ("GENE1", "GENE2")
    with pytest.raises(x.SavedEvidenceRefusal):
        next(data.pairs(candidate))


@pytest.mark.parametrize(
    "query",
    [
        "SELECT range AS value FROM range(129)",
        "SELECT repeat('x', 8388609) AS value",
    ],
)
def test_query_row_and_byte_limits_refuse_before_payload_fetch(query):
    with duckdb.connect(config=x.CONFIG) as db, pytest.raises(x.SavedEvidenceRefusal):
        x.Queries(db, Budget()).rows(query)


def test_scalar_byte_limit_is_also_admitted_before_fetch():
    with duckdb.connect(config=x.CONFIG) as db, pytest.raises(x.SavedEvidenceRefusal):
        x.Queries(db, Budget()).scalar("SELECT repeat('x', 8388609)")


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', "[]"])
def test_saved_json_duplicate_or_nonfinite_or_nonobject_refused(raw):
    with pytest.raises(x.SavedEvidenceRefusal):
        x.strict_json(raw)


def test_final_audit_limit_includes_caller_metadata(saved):
    saved[1]["coverage"]["synthetic_padding"] = "x" * x.MAX_FILE
    with pytest.raises(x.SavedEvidenceRefusal):
        reader(saved).extract()


def test_readonly_connection_limits_external_access_to_exact_inputs(saved, monkeypatch):
    paths = seal_saved(saved)
    context = saved[1]
    budget = Budget()

    class Inspect:
        def __init__(self, queries, _context):
            db = queries.db
            assert db.execute("SELECT current_setting('threads')").fetchone() == (1,)
            for setting in (
                "enable_external_access",
                "autoload_known_extensions",
                "autoinstall_known_extensions",
            ):
                assert db.execute("SELECT current_setting(?)", [setting]).fetchone() == (False,)
            assert db.execute("SELECT current_setting('temp_directory')").fetchone() == ("",)
            # DuckDB adds its own attached-database recovery companions even
            # on a readonly connection. No directories or remote paths are allowed.
            assert set(db.execute("SELECT current_setting('allowed_paths')").fetchone()[0]) == (
                {str(path) for path in paths.values()}
                | {
                    str(paths["database"]) + suffix
                    for suffix in (".wal", ".wal.recovery", ".wal.checkpoint")
                }
            )
            with pytest.raises(duckdb.InvalidInputException):
                db.execute("SET threads=2")
            with pytest.raises(duckdb.PermissionException):
                db.read_parquet(str(saved[2] / "unlisted.parquet"))

        def extract(self):
            return b"synthetic", {}

    monkeypatch.setattr(x, "SavedEvidence", Inspect)
    assert x.read_data(context, budget, sha(paths["variants"])) == (b"synthetic", {})
    assert budget.db is None


def test_budget_refusal_closes_readonly_connection(saved):
    paths = seal_saved(saved)

    class Refuse(Budget):
        def check(self):
            if self.db is not None:
                raise RuntimeError("synthetic_budget_refusal")

    budget = Refuse()
    with pytest.raises(RuntimeError, match="synthetic_budget_refusal"):
        x.read_data(saved[1], budget, sha(paths["variants"]))
    assert budget.db is None


@pytest.mark.parametrize(
    ("gt", "expected"),
    [
        ("0/1", "heterozygous"),
        ("1|0", "heterozygous"),
        ("1/1", "homozygous_alt"),
        ("1", "haploid_alt"),
        ("0", "reference"),
        ("0/.", "unknown"),
        (None, "unknown"),
    ],
)
def test_saved_normalized_genotypes(gt, expected):
    assert x.genotype_state(gt) == expected


def test_non_normalized_genotype_fails_closed():
    with pytest.raises(ValueError):
        x.genotype_state("0/2")
