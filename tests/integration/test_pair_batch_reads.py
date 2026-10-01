"""Batched pair reads preserve complete legacy evidence and traversal semantics."""

import json

import pytest

from rare_disease_agent.agents.schemas import EvaluateInheritanceParameters
from rare_disease_agent.ranking.persistent import rank_persistent
from rare_disease_agent.ranking.scoring import PreliminaryRanker
from rare_disease_agent.reporting.provenance import SoftwareIdentity
from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
from rare_disease_agent.tools.inheritance.schemas import Individual, Pedigree
from rare_disease_agent.tools.variants.agent_tools import VariantToolbox, VariantToolError
from rare_disease_agent.tools.variants.vcf import VariantRecord

IDENTITY = SoftwareIdentity(
    git_commit="synthetic-controlled-provenance",
    git_dirty=False,
    python_version="synthetic",
    dependencies={},
)


class PerPairReads(VariantToolbox):
    """Frozen reference for the former lookup path; not a production fallback."""

    def _pair_genotypes(self, pairs):
        for first, second in pairs:
            rows = self.membership._connection.execute(
                "SELECT * FROM variants WHERE variant_id IN (?, ?) ORDER BY _row_id",
                [first, second],
            )
            columns = [item[0] for item in rows.description]
            yield [
                self._variant_genotypes(dict(zip(columns, row, strict=True)))
                for row in rows.fetchall()
            ]


def source_file(tmp_path, count=40, diverse=True):
    variants = []
    for i in range(count):
        gt = ["0/1", "1/0", "0/1", "0/1", "0|1", "1|0", "0/1", "0/1"][i % 8]
        calls = {
            "sample": {
                "genotype": gt,
                "quality": 8 if i % 8 == 2 else 90,
                "alternate_fraction": 0.1 if i % 8 == 6 else 0.5,
            }
        }
        if i % 8 in {0, 1, 2}:
            calls.update(
                {
                    "mother": {"genotype": "0/1" if i % 8 != 1 else "0/0", "quality": 90},
                    "father": {"genotype": "0/1" if i % 8 == 1 else "0/0", "quality": 90},
                }
            )
        record = VariantRecord(
            chromosome="1",
            position=i + 1,
            reference="A",
            alternate="G",
            variant_id=f"synthetic-{count - i:03d}'quoted",
            gene="SYN_DENSE",
            genotype=gt,
            quality=90,
            consequence="missense_variant",
            genotype_calls_json=json.dumps(calls),
        )
        if diverse:
            if i == 0:
                record.genotype_calls_json = None
            if i == 1:
                record.genotype_calls_json = json.dumps(
                    {"sample": {"genotype": "1/1", "quality": 90}}
                )
            if i == 2:
                record.genotype = "0/0"  # Row/JSON discrepancy must not broaden SQL eligibility.
            if i == 3:
                record.genotype = "1/2"
            if i in {4, 5}:
                record.gene = " SYN_DENSE "
            if i in {6, 7}:
                record.chromosome = "chr1"
            if i == 8:
                record.gene = None
            if i in {9, 10}:
                record.gene = "UNKNOWN"
            if i == 11:
                record.gene = "syn_dense"
        variants.append(record)
    path = tmp_path / "synthetic.parquet"
    write_variants_parquet(variants, path)
    return path


def toolbox(source, database, cls=VariantToolbox, affected=None):
    evaluator = InheritanceEvaluator(
        Pedigree(
            proband_id="sample",
            mother_id="mother",
            father_id="father",
            individuals=[
                Individual(id="sample", affected=affected),
                Individual(id="mother"),
                Individual(id="father"),
            ],
        ),
        run_id="synthetic-batched",
        software=IDENTITY,
    )
    box = cls(
        source,
        run_id="synthetic-batched",
        membership_database=database,
        inheritance_evaluator=evaluator,
    )
    if cls is PerPairReads:
        # Keep the full evaluator as the oracle when production uses its pair path.
        evaluator.evaluate_pair = lambda first, second: evaluator.evaluate([first, second])
    return box


def snapshot(box):
    rank_persistent(
        box,
        PreliminaryRanker(
            run_id="synthetic-batched",
            software=IDENTITY,
            weights={
                key: 1 for key in ["quality", "rarity", "consequence", "phenotype", "inheritance"]
            },
        ),
        "all",
    )
    db = box.membership._connection
    return {
        name: db.execute(query).fetchall()
        for name, query in {
            "pairs": "SELECT * FROM compound_pairs ORDER BY variant_a,variant_b",
            "evidence": "SELECT * FROM evidence ORDER BY variant_id,gene,method,data_version",
            "membership": "SELECT * FROM candidate_membership ORDER BY branch,variant_id",
            "ranking": "SELECT * FROM ranked_evidence ORDER BY mode,rank",
        }.items()
    }


@pytest.mark.parametrize("affected", [True, None, False])
def test_full_pair_payload_evidence_order_and_ranking_match_reference(tmp_path, affected):
    source = source_file(tmp_path)
    results = []
    for cls in [PerPairReads, VariantToolbox]:
        box = toolbox(source, tmp_path / (cls.__name__ + ".duckdb"), cls, affected)
        db = box.membership._connection
        db.execute("SET memory_limit='64MB'")
        db.execute("SET threads=1")
        db.execute("SET temp_directory=''")
        # Keep the full source and a rescue branch; assess one declared active subset.
        box.membership.copy_branch(source="all", target="rescue", operation="synthetic rescue")
        box.membership.copy_branch(
            source="all", target="assessed", operation="synthetic active subset"
        )
        db.execute(
            "UPDATE candidate_membership SET active=false WHERE branch='assessed' AND variant_id=?",
            ["synthetic-001'quoted"],
        )
        observed = []
        evaluate = box.inheritance_evaluator.evaluate_pair

        def record(first, second, observed=observed, evaluate=evaluate):
            observed.append([v.model_dump() for v in [first, second]])
            return evaluate(first, second)

        box.inheritance_evaluator.evaluate_pair = record
        try:
            observation = box.evaluate_inheritance(EvaluateInheritanceParameters(branch="assessed"))
            first = snapshot(box)
            results.append((observed.copy(), observation.model_dump(), first))
            assert len(observed) > 128  # Cross the read-cache boundary and equal-score reductions.
            box.evaluate_inheritance(EvaluateInheritanceParameters(branch="assessed"))
            assert snapshot(box) == first
        finally:
            box.membership.close()
    assert results[0] == results[1]


def test_pair_read_cache_is_bounded_and_does_not_survive_batch(tmp_path, monkeypatch):
    source = source_file(tmp_path, count=256, diverse=False)
    box = toolbox(source, tmp_path / "bounded.duckdb")
    identifiers = box.membership._connection.execute(
        "SELECT variant_id FROM variants ORDER BY _row_id"
    ).fetchall()
    pairs = [(identifiers[i][0], identifiers[i + 1][0]) for i in range(0, 256, 2)]
    decode = box._variant_genotypes
    decoded = []

    def record(row):
        decoded.append(row["variant_id"])
        return decode(row)

    monkeypatch.setattr(box, "_variant_genotypes", record)
    try:
        assert len(list(box._pair_genotypes(pairs))) == 128 and len(decoded) == 256
        assert len(list(box._pair_genotypes(pairs[:1]))) == 1 and len(decoded) == 258
        assert list(box._pair_genotypes([])) == []
        with pytest.raises(ValueError, match="batch exceeds"):
            list(box._pair_genotypes(pairs + [pairs[0]]))
        with pytest.raises(VariantToolError, match="incomplete"):
            list(box._pair_genotypes([(pairs[0][0], "synthetic-missing")]))
    finally:
        box.membership.close()


def test_read_failure_after_flush_rolls_back_and_retry_matches_reference(tmp_path, monkeypatch):
    source = source_file(tmp_path, count=24, diverse=False)
    box = toolbox(source, tmp_path / "failure.duckdb")
    original = box._pair_genotypes
    calls = 0

    def fail(pairs):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert (
                box.membership._connection.execute(
                    "SELECT count(*) FROM compound_pairs"
                ).fetchone()[0]
                == 128
            )
            raise RuntimeError("synthetic batch read failure")
        yield from original(pairs)

    monkeypatch.setattr(box, "_pair_genotypes", fail)
    try:
        with pytest.raises(RuntimeError, match="batch read failure"):
            box.evaluate_inheritance(EvaluateInheritanceParameters(branch="all"))
        assert (
            box.membership._connection.execute("SELECT count(*) FROM compound_pairs").fetchone()[0]
            == 0
        )
        assert (
            box.membership._connection.execute(
                "SELECT count(*) FROM evidence WHERE method='compound_heterozygous'"
            ).fetchone()[0]
            == 0
        )
        monkeypatch.setattr(box, "_pair_genotypes", original)
        box.evaluate_inheritance(EvaluateInheritanceParameters(branch="all"))
        retried = snapshot(box)
    finally:
        box.membership.close()
    reference = toolbox(source, tmp_path / "reference.duckdb", PerPairReads)
    try:
        reference.evaluate_inheritance(EvaluateInheritanceParameters(branch="all"))
        assert snapshot(reference) == retried
    finally:
        reference.membership.close()
