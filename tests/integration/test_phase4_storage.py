import duckdb
import pytest

from rare_disease_agent.reporting.provenance import software_identity
from rare_disease_agent.storage.evidence import EvidenceStore
from rare_disease_agent.storage.membership import CandidateMembershipStore
from rare_disease_agent.synthetic.cases import load_synthetic_phenotype_store
from rare_disease_agent.synthetic.stress import stress_benchmark
from rare_disease_agent.tools.phenotype.tools import PhenotypeToolbox


def test_generated_evidence_is_bounded_and_ranked_on_disk(tmp_path):
    result = stress_benchmark(tmp_path, count=3000)
    assert result["evidence_rows"] == 15100
    assert result["max_batch"] <= 256
    assert result["observation_bytes"] < 20000
    assert result["top_k"] == 20
    assert result["peak_rss_mb"] - result["initial_rss_mb"] < 256


def test_evidence_run_mismatch_and_batch_limits():
    with duckdb.connect() as connection:
        store = EvidenceStore(connection, "expected")
        phenotype = PhenotypeToolbox(
            load_synthetic_phenotype_store(), patient_hpo=[], run_id="different"
        )
        with pytest.raises(ValueError, match="run ID"):
            store.write([phenotype.get_gene_phenotype_score("SYN_CAUSAL")])
        with pytest.raises(ValueError, match="batch"):
            store.write([], batch_size=100000)


def test_reopening_membership_preserves_branches_and_rejects_identity(phase2_parquet, tmp_path):
    database = tmp_path / "membership.duckdb"
    store = CandidateMembershipStore(phase2_parquet, run_id="resume", database_path=database)
    store.copy_branch(source="all", target="rescue", operation="rescue")
    count = store.count("rescue")
    store.close()
    reopened = CandidateMembershipStore(phase2_parquet, run_id="resume", database_path=database)
    assert reopened.count("rescue") == count
    reopened.close()
    with pytest.raises(ValueError, match="mismatch"):
        CandidateMembershipStore(phase2_parquet, run_id="wrong", database_path=database)


def test_evidence_generator_failure_rolls_back_batches():
    with duckdb.connect() as connection:
        store = EvidenceStore(connection, "atomic")
        phenotype = PhenotypeToolbox(
            load_synthetic_phenotype_store(), patient_hpo=[], run_id="atomic"
        )

        def interrupted():
            yield phenotype.get_gene_phenotype_score("SYN_CAUSAL")
            raise InterruptedError("synthetic fault")

        with pytest.raises(InterruptedError):
            store.write(interrupted(), batch_size=1)
        assert connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
        store.write([phenotype.get_gene_phenotype_score("SYN_CAUSAL")])
        item = phenotype.get_gene_phenotype_score("SYN_OTHER")
        item.provenance.data_version = "stale"
        with pytest.raises(ValueError, match="version"):
            store.write([item])


def test_published_membership_is_self_contained_after_stage_rename(phase2_parquet, tmp_path):
    import shutil

    staged = tmp_path / "stage.partial"
    staged.mkdir()
    source = staged / "source.parquet"
    shutil.copyfile(phase2_parquet, source)
    store = CandidateMembershipStore(source, run_id="portable", database_path=staged / "run.duckdb")
    count = store.count("all")
    store.close()
    final = tmp_path / "stage"
    staged.rename(final)
    with duckdb.connect(str(final / "run.duckdb"), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM variants").fetchone()[0] == count


def test_evidence_batch_duplicates_match_sequential_maximum_and_last_tie():
    results = []
    for batch_size in (1, 256):
        with duckdb.connect() as connection:
            store = EvidenceStore(connection, "duplicates")
            phenotype = PhenotypeToolbox(
                load_synthetic_phenotype_store(), patient_hpo=[], run_id="duplicates"
            )
            item = phenotype.get_gene_phenotype_score("SYN_CAUSAL")
            values = [
                item.model_copy(update={"score": score, "association_count": i})
                for i, score in enumerate((0.2, 0.9, 0.4, 0.9))
            ]
            store.write(values, batch_size=batch_size)
            results.append(connection.execute("SELECT score,payload FROM evidence").fetchall())
    assert results[0] == results[1]
    assert results[0][0][0] == 0.9


def test_compound_pair_failure_rolls_back_pair_table_and_pair_evidence(tmp_path, monkeypatch):
    import json

    from rare_disease_agent.agents.schemas import EvaluateInheritanceParameters
    from rare_disease_agent.storage.parquet import write_variants_parquet
    from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
    from rare_disease_agent.tools.inheritance.schemas import Individual, Pedigree
    from rare_disease_agent.tools.variants.agent_tools import VariantToolbox
    from rare_disease_agent.tools.variants.vcf import VariantRecord

    source = tmp_path / "synthetic.parquet"
    write_variants_parquet(
        [
            VariantRecord(
                chromosome="1",
                position=i + 1,
                reference="A",
                alternate="G",
                variant_id=f"synthetic-{i}",
                gene="SYN_GENE",
                genotype="0/1",
                genotype_calls_json=json.dumps({"sample": {"genotype": "0/1", "quality": 90}}),
            )
            for i in range(24)
        ],
        source,
    )
    evaluator = InheritanceEvaluator(
        Pedigree(proband_id="sample", individuals=[Individual(id="sample")]),
        run_id="pairs",
        software=software_identity(),
    )
    toolbox = VariantToolbox(
        source,
        run_id="pairs",
        membership_database=tmp_path / "pairs.duckdb",
        inheritance_evaluator=evaluator,
    )
    original = evaluator.evaluate_pair
    calls = 0

    def interrupted(first, second):
        nonlocal calls
        calls += 1
        if calls == 140:
            assert (
                toolbox.membership._connection.execute(
                    "SELECT count(*) FROM compound_pairs"
                ).fetchone()[0]
                == 128
            )
            raise RuntimeError("synthetic pair interruption")
        return original(first, second)

    monkeypatch.setattr(evaluator, "evaluate_pair", interrupted)
    try:
        with pytest.raises(RuntimeError, match="synthetic pair"):
            toolbox.evaluate_inheritance(EvaluateInheritanceParameters(branch="all"))
        db = toolbox.membership._connection
        assert db.execute("SELECT count(*) FROM compound_pairs").fetchone()[0] == 0
        assert (
            db.execute(
                "SELECT count(*) FROM evidence WHERE method='compound_heterozygous'"
            ).fetchone()[0]
            == 0
        )
        monkeypatch.setattr(evaluator, "evaluate_pair", original)
        toolbox.evaluate_inheritance(EvaluateInheritanceParameters(branch="all"))
        assert db.execute("SELECT count(*) FROM compound_pairs").fetchone()[0] == 276
        assert (
            db.execute(
                "SELECT count(*) FROM evidence WHERE method='compound_heterozygous'"
            ).fetchone()[0]
            == 24
        )
    finally:
        toolbox.membership.close()


def test_dense_gene_pair_storage_stays_bounded_and_replaces_existing_pairs(tmp_path):
    import json

    from rare_disease_agent.agents.schemas import EvaluateInheritanceParameters
    from rare_disease_agent.storage.parquet import write_variants_parquet
    from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
    from rare_disease_agent.tools.inheritance.schemas import Individual, Pedigree
    from rare_disease_agent.tools.variants.agent_tools import VariantToolbox
    from rare_disease_agent.tools.variants.vcf import VariantRecord

    source = tmp_path / "synthetic-dense-gene.parquet"
    write_variants_parquet(
        (
            VariantRecord(
                chromosome="1",
                position=i + 1,
                reference="A",
                alternate="G",
                variant_id=f"synthetic-{i:03d}",
                gene="SYN_DENSE",
                genotype="0/1",
                genotype_calls_json=json.dumps({"sample": {"genotype": "0/1", "quality": 90}}),
            )
            for i in range(40)
        ),
        source,
    )
    evaluator = InheritanceEvaluator(
        Pedigree(proband_id="sample", individuals=[Individual(id="sample")]),
        run_id="dense-pairs",
        software=software_identity(),
    )
    toolbox = VariantToolbox(
        source,
        run_id="dense-pairs",
        membership_database=tmp_path / "dense.duckdb",
        inheritance_evaluator=evaluator,
    )
    try:
        db = toolbox.membership._connection
        # A stricter synthetic stress limit, not a change to production resource safeguards.
        db.execute("SET memory_limit='64MB'")
        db.execute("SET threads=1")
        db.execute("SET temp_directory=''")
        for _ in range(2):
            toolbox.evaluate_inheritance(EvaluateInheritanceParameters(branch="all"))
            assert db.execute("SELECT count(*) FROM compound_pairs").fetchone()[0] == 780
            assert (
                db.execute(
                    "SELECT count(*) FROM evidence WHERE method='compound_heterozygous'"
                ).fetchone()[0]
                == 40
            )
            assert (
                db.execute(
                    "SELECT max(score) FROM evidence WHERE method='compound_heterozygous'"
                ).fetchone()[0]
                <= 0.5
            )
        payload = json.loads(
            db.execute(
                "SELECT payload FROM compound_pairs "
                "WHERE variant_a='synthetic-000' AND variant_b='synthetic-039'"
            ).fetchone()[0]
        )
        assert payload["phase"] == "unknown" and payload["warnings"]
    finally:
        toolbox.membership.close()
