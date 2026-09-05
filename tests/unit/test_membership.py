from pathlib import Path

import duckdb
import pytest

from rare_disease_agent.agents.schemas import FilterFrequencyParameters
from rare_disease_agent.tools.variants.agent_tools import VariantToolbox


def test_candidate_membership_is_persisted_in_duckdb(phase2_parquet: Path, tmp_path: Path) -> None:
    database = tmp_path / "membership.duckdb"
    toolbox = VariantToolbox(
        phase2_parquet, run_id="persistent-membership", membership_database=database
    )
    toolbox.filter_by_population_frequency(
        FilterFrequencyParameters(
            source_branch="all", target_branch="rare", maximum_allele_frequency=0.01
        )
    )
    toolbox.membership.close()

    with duckdb.connect(str(database), read_only=True) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('candidate_membership')").fetchall()
        }
        count = connection.execute(
            "SELECT count(*) FROM candidate_membership WHERE branch = 'rare' AND active"
        ).fetchone()[0]

    assert {"variant_id", "branch", "stage", "active", "reason_code", "run_id"} <= columns
    assert count == 9


def test_membership_transaction_rolls_back_partial_branch(
    phase2_parquet: Path, tmp_path: Path
) -> None:
    toolbox = VariantToolbox(
        phase2_parquet,
        run_id="transaction",
        membership_database=tmp_path / "transaction.duckdb",
    )

    with pytest.raises(RuntimeError, match="synthetic rollback"), toolbox.membership.transaction():
        toolbox.membership.copy_branch(source="all", target="partial", operation="test_copy")
        raise RuntimeError("synthetic rollback")

    assert toolbox.membership.has_branch("partial") is False
