"""Synthetic accounting matches actual pair joins, including missing context."""

import duckdb
import pytest

from rare_disease_agent.tools.variants.pair_workload import PAIR_WORK_BUDGET, count_pair_work


@pytest.mark.parametrize("membership", [None, ("synthetic", "conservative")])
def test_count_matches_explicit_enumeration(membership):
    with duckdb.connect() as db:
        db.execute(
            "CREATE TABLE variants(variant_id INT, gene VARCHAR, "
            "chromosome VARCHAR, genotype VARCHAR)"
        )
        values = [
            ("A", "1", "0/1"),
            ("a", "1", "1|0"),
            ("A", "1", "0|1"),
            ("A", "2", "1/0"),
            (" A ", "1", "0/1"),
            ("A", "1", "1/1"),
            ("UNKNOWN", "1", "0/1"),
            ("UNKNOWN", "1", "0/1"),
            (None, "1", "0/1"),
            (None, "1", "0/1"),
            ("A", None, "0/1"),
            ("A", None, "0/1"),
            (".", "1", "0/1"),
            ("", "1", "0/1"),
            ("A", "1", "./."),
        ]
        db.executemany(
            "INSERT INTO variants VALUES (?,?,?,?)", [(i, *v) for i, v in enumerate(values)]
        )
        db.execute(
            "CREATE TABLE candidate_membership AS SELECT variant_id, 'synthetic' run_id, "
            "'conservative' branch, variant_id<>2 active FROM variants"
        )
        expected = db.execute(
            """
            SELECT count(*) FROM variants a JOIN variants b
            ON a.variant_id<b.variant_id AND upper(a.gene)=upper(b.gene)
            AND a.chromosome=b.chromosome
            WHERE upper(trim(a.gene)) NOT IN ('','.','UNKNOWN','UNASSIGNED')
            AND a.genotype IN ('0/1','1/0','0|1','1|0')
            AND b.genotype IN ('0/1','1/0','0|1','1|0')
        """
            + (" AND a.variant_id<>2 AND b.variant_id<>2" if membership else "")
        ).fetchone()[0]
        assert count_pair_work(db, membership=membership) == expected == (1 if membership else 3)


@pytest.mark.parametrize("n,expected", [(0, 0), (1, 0), (447, 99681), (448, 100128)])
def test_budget_boundary_without_pair_materialization(n, expected):
    with duckdb.connect() as db:
        db.execute(
            "CREATE TABLE variants AS SELECT i variant_id, 'GENE' gene, '1' chromosome, "
            "'0/1' genotype FROM range(?) t(i)",
            [n],
        )
        assert count_pair_work(db) == expected
        assert (expected <= PAIR_WORK_BUDGET) == (n < 448)
