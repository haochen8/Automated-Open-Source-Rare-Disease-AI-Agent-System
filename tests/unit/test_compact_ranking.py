import pytest

from rare_disease_agent.ranking.persistent import rank_persistent, ranking_rows
from rare_disease_agent.ranking.scoring import MODE_FEATURES, PreliminaryRanker
from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.tools.variants.agent_tools import VariantToolbox
from rare_disease_agent.tools.variants.vcf import VariantRecord


@pytest.fixture
def ranked(tmp_path):
    source = tmp_path / "synthetic.parquet"
    records = [
        VariantRecord(
            chromosome="1",
            position=i + 1,
            reference="A",
            alternate="G",
            variant_id=f"SYN-{3 - i}",
            gene="SYN_GENE",
            quality=40 if i == 0 else None,
            allele_frequency=None if i == 0 else 0.001,
            consequence="missense_variant",
        )
        for i in range(4)
    ]
    write_variants_parquet(records, source)
    toolbox = VariantToolbox(
        source, run_id="synthetic-compact", membership_database=tmp_path / "ranking.duckdb"
    )
    ranker = PreliminaryRanker(
        weights=dict(quality=0.1, rarity=0.35, consequence=0.35, phenotype=0.1, inheritance=0.1),
        run_id="synthetic-compact",
    )
    try:
        yield toolbox, ranker
    finally:
        toolbox.membership.close()


def test_compact_rankings_match_independent_ranker_and_keep_named_contexts(ranked):
    toolbox, ranker = ranked
    rows = list(toolbox.membership.iter_rows("all"))
    rank_persistent(toolbox, ranker, "all")
    db = toolbox.membership._connection
    for mode in MODE_FEATURES:
        actual = list(ranking_rows(db, ranker.run_id, mode))
        expected = ranker.rank(rows, phenotype_scores={}, inheritance_scores={}, mode=mode)
        assert [(x.variant_id, x.rank, x.raw_score, x.features) for x in actual] == [
            (x.variant_id, x.rank, x.raw_score, x.features) for x in expected
        ]
    rank_persistent(toolbox, ranker, "all", prefix="alternate_")
    assert db.execute("SELECT count(*) FROM ranking_contexts").fetchone()[0] == 8
    assert db.execute("SELECT count(*) FROM ranked_evidence").fetchone()[0] == 32
    assert (
        db.execute(
            "SELECT table_type FROM information_schema.tables WHERE table_name='ranked_evidence'"
        ).fetchone()[0]
        == "VIEW"
    )


def test_failed_reranking_rolls_back_every_mode_and_provenance(ranked, monkeypatch):
    from rare_disease_agent.ranking import persistent

    toolbox, ranker = ranked
    rank_persistent(toolbox, ranker, "all")
    db = toolbox.membership._connection
    before = db.execute("SELECT * FROM ranked_evidence ORDER BY mode,rank").fetchall()
    original = persistent.evidence_provenance
    calls = 0

    def fail(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("synthetic interruption")
        return original(**kwargs)

    monkeypatch.setattr(persistent, "evidence_provenance", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        rank_persistent(toolbox, ranker, "all")
    assert db.execute("SELECT * FROM ranked_evidence ORDER BY mode,rank").fetchall() == before
    monkeypatch.setattr(persistent, "evidence_provenance", original)
    rank_persistent(toolbox, ranker, "all")
    assert db.execute("SELECT count(*) FROM ranked_evidence").fetchone()[0] == 16


def test_reranking_replaces_whole_mode_and_retains_other_contexts(ranked):
    toolbox, ranker = ranked
    toolbox.membership.copy_branch(source="all", target="working", operation="synthetic")
    rank_persistent(toolbox, ranker, "all", prefix="preserved_")
    rank_persistent(toolbox, ranker, "working")
    db = toolbox.membership._connection
    db.execute(
        "UPDATE candidate_membership SET active=false WHERE branch='working' AND variant_id='SYN-0'"
    )
    rank_persistent(toolbox, ranker, "working")
    assert (
        db.execute("SELECT count(*) FROM ranked_evidence WHERE mode LIKE 'preserved_%'").fetchone()[
            0
        ]
        == 16
    )
    assert (
        db.execute(
            "SELECT count(*) FROM ranked_evidence WHERE mode NOT LIKE 'preserved_%'"
        ).fetchone()[0]
        == 12
    )
    assert (
        db.execute(
            "SELECT count(*) FROM ranked_evidence WHERE variant_id='SYN-0' "
            "AND mode NOT LIKE 'preserved_%'"
        ).fetchone()[0]
        == 0
    )


def test_legacy_rankings_are_not_migrated_or_modified(ranked):
    toolbox, ranker = ranked
    db = toolbox.membership._connection
    db.execute("CREATE TABLE ranked_evidence AS SELECT 'keep' AS existing")
    with pytest.raises(ValueError, match="fresh run"):
        rank_persistent(toolbox, ranker, "all")
    assert db.execute("SELECT * FROM ranked_evidence").fetchall() == [("keep",)]


def test_readonly_attached_legacy_database_does_not_block_fresh_storage(ranked, tmp_path):
    import duckdb

    toolbox, ranker = ranked
    legacy = tmp_path / "legacy.duckdb"
    with duckdb.connect(str(legacy)) as connection:
        connection.execute("CREATE TABLE ranked_evidence AS SELECT 'preserved' AS value")
    db = toolbox.membership._connection
    escaped = str(legacy).replace("'", "''")
    db.execute("ATTACH '" + escaped + "' AS legacy (READ_ONLY)")
    rank_persistent(toolbox, ranker, "all")
    assert db.execute("SELECT count(*) FROM main.ranked_evidence").fetchone()[0] == 16
    assert db.execute("SELECT * FROM legacy.ranked_evidence").fetchall() == [("preserved",)]
