"""Exact compound-pair work accounting shared by preflight and execution."""

from __future__ import annotations

import duckdb

PAIR_WORK_BUDGET = 100_000


def count_pair_work(
    connection: duckdb.DuckDBPyConnection, *, membership: tuple[str, str] | None = None
) -> int:
    """Count the enumeration workload, not retained pairs or scientific evidence.

    The fixed ``variants`` relation must contain unique candidates. A membership
    selection uses the same active branch and gene/chromosome grouping as execution.
    NULL chromosomes cannot join; gene whitespace is preserved for grouping.
    """
    join = ""
    selection = ""
    parameters = []
    if membership is not None:
        join = " JOIN candidate_membership m USING(variant_id)"
        selection = "m.run_id=? AND m.branch=? AND m.active AND "
        parameters = list(membership)
    return int(
        connection.execute(
            "SELECT coalesce(sum((n::HUGEINT*(n-1))//2),0) FROM ("
            "SELECT count(*) n FROM variants v"
            + join
            + " WHERE "
            + selection
            + "v.genotype IN ('0/1','1/0','0|1','1|0') AND v.chromosome IS NOT NULL "
            "AND upper(trim(v.gene)) NOT IN ('','.', 'UNKNOWN','UNASSIGNED') "
            "GROUP BY upper(v.gene), v.chromosome)",
            parameters,
        ).fetchone()[0]
    )
