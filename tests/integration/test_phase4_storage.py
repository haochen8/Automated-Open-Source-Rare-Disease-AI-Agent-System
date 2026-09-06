import duckdb
import pytest

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
