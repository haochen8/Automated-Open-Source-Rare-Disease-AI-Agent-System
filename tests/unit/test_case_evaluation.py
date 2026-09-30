import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from rare_disease_agent.config import Track1Config
from rare_disease_agent.ranking.case_evaluation import EvaluationManifest, evaluate_cases
from rare_disease_agent.resource_management import sha256
from rare_disease_agent.workflows.phase5_cli import app
from rare_disease_agent.workflows.recovery import RestartableRun

MODE = "filtering_phenotype_inheritance"


def snapshot(root, source_hash="a" * 64, *, complete=True):
    """Invented committed outputs with overlapping genes, a pair, and deferred/filtered alleles."""
    run = RestartableRun(
        root,
        run_id="synthetic",
        configuration={
            "inputs": {"challenge_track1_vcf": {"sha256": source_hash}},
            "code_sha256": "b" * 64,
            "hpo_manifest_sha256": "c" * 64,
        },
    )

    def ingest(path):
        tables = path / "tables"
        tables.mkdir()
        alleles = [
            {
                "allele_id": f"a{i}",
                "source_row": str(i),
                "source_alt": "1",
                "disposition": "deferred_contig_context" if i == 3 else "included",
            }
            for i in range(1, 6)
        ]
        mapping = [
            {"variant_id": key, "allele_id": allele, "gene_id": gene, "gene_symbol": gene}
            for key, allele, gene in [
                ("decoy", "a5", "D"),
                ("first", "a1", "G"),
                ("overlap", "a1", "X"),
                ("second", "a2", "G"),
                ("filtered", "a4", "G"),
            ]
        ]
        variants = [
            {
                "variant_id": row["variant_id"],
                "allele_frequency": None,
                "consequence": "missense_variant",
            }
            for row in mapping
        ]
        for name, rows in [
            ("alleles", alleles),
            ("candidate_map", mapping),
            ("variants", variants),
        ]:
            pq.write_table(pa.Table.from_pylist(rows), tables / f"{name}.parquet")

    def deliver(path):
        weights = Track1Config().preliminary_scoring.model_dump()
        rows, memberships, evidence = [], [], []
        for rank, (key, gene) in enumerate(
            [("decoy", "D"), ("first", "G"), ("overlap", "X"), ("second", "G"), ("filtered", "G")],
            1,
        ):
            memberships.append(
                {"run_id": "synthetic", "variant_id": key, "branch": "conservative", "active": True}
            )
            if key != "filtered":
                score = 1 - rank / 10
                rows.append(
                    {
                        "run_id": "synthetic",
                        "variant_id": key,
                        "mode": MODE,
                        "rank": rank,
                        "raw_score": score,
                        "features": json.dumps(dict.fromkeys(weights, score)),
                        "provenance": json.dumps(
                            {"parameters": {"weights": weights, "included_features": list(weights)}}
                        ),
                    }
                )
                memberships.append(
                    {"run_id": "synthetic", "variant_id": key, "branch": "ensemble", "active": True}
                )
            if gene == "G":
                memberships.append(
                    {
                        "run_id": "synthetic",
                        "variant_id": key,
                        "branch": "novel-gene-rescue",
                        "active": True,
                    }
                )
            evidence.append(
                {
                    "run_id": "synthetic",
                    "variant_id": key,
                    "gene": gene,
                    "method": "autosomal_dominant",
                    "score": 0.5,
                    "known": True,
                    "payload": json.dumps({"warnings": ["Synthetic sparse pedigree"]}),
                }
            )
        evidence.append(
            {
                "run_id": "synthetic",
                "variant_id": "",
                "gene": "G",
                "method": "resnik_bma",
                "score": 0.0,
                "known": False,
                "payload": "{}",
            }
        )
        for name, data in [
            ("private_candidate_ranking", rows),
            ("private_branch_membership", memberships),
            ("private_candidate_features", evidence),
        ]:
            pq.write_table(pa.Table.from_pylist(data), path / f"{name}.parquet")

    run.stage("ingest", ingest)
    if complete:
        run.stage("deliverables", deliver)
        run.complete()
    return root


def label(run, **updates):
    return {
        "case_id": "synthetic",
        "source_sha256": "a" * 64,
        "truth_reference": "invented",
        "expected_gene_id": "G",
        "alleles": [{"source_row": 1, "source_alt": 1}],
        "run_directory": str(run),
        **updates,
    }


def manifest(tmp_path, cases, **updates):
    path = tmp_path / "truth.json"
    path.write_text(json.dumps({"confirmed_local_research_use": True, "cases": cases, **updates}))
    return path


def contents(root):
    return {str(path.relative_to(root)): sha256(path) for path in root.rglob("*") if path.is_file()}


def test_pair_requires_both_alleles_and_deduplicates_gene_and_allele_ranks(tmp_path):
    run = snapshot(tmp_path / "run")
    before = contents(run)
    truth = label(run, alleles=[{"source_row": i, "source_alt": 1} for i in (1, 2)])
    path = manifest(tmp_path, [truth])
    evaluate_cases(path, tmp_path / "evaluation")
    result = json.loads((tmp_path / "evaluation/evaluation.json").read_text())
    case = result["cases"][0]
    assert (case["gene_rank"], case["allele_rank"], case["allele_gene_rank"]) == (2, 3, 4)
    assert result["metrics"]["allele"]["mrr"] == pytest.approx(1 / 3)
    assert result["metrics"]["allele_gene"]["mrr"] == 0.25
    detail = case["targets"][0]["candidate"]
    assert sum(detail["contributions"].values()) == pytest.approx(detail["score"])
    assert detail["phenotype_association_available"] is False
    assert detail["population_frequency_available"] is False
    assert detail["inheritance_warnings"] == ["Synthetic sparse pedigree"]
    assert detail["rescue_member"] is True and detail["rescue_required"] is None
    assert (tmp_path / "evaluation/cases.tsv").is_file()
    assert contents(run) == before
    evaluate_cases(path, tmp_path / "repeat")
    assert contents(tmp_path / "evaluation") == contents(tmp_path / "repeat")


@pytest.mark.parametrize(
    "row,reason,prepared,filtered",
    [
        (3, "deferred_contig_context", True, None),
        (4, "filtered_out", True, False),
        (99, "outside_ingested_subset", False, None),
    ],
)
def test_missing_second_allele_is_a_miss_and_reason_is_preserved(
    tmp_path, row, reason, prepared, filtered
):
    run = snapshot(tmp_path / "run")
    path = manifest(
        tmp_path, [label(run, alleles=[{"source_row": i, "source_alt": 1} for i in (1, row)])]
    )
    evaluate_cases(path, tmp_path / "out")
    result = json.loads((tmp_path / "out/evaluation.json").read_text())
    case = result["cases"][0]
    assert case["allele_rank"] is None and case["allele_gene_rank"] is None
    assert result["metrics"]["allele"]["top_5_recall"] == 0
    target = case["targets"][1]
    assert target["failure_reason"] == reason
    assert target["retained_after_preparation"] is prepared
    assert target["retained_after_filtering"] is filtered


def test_failed_incomplete_and_gene_only_cases_have_explicit_denominators(tmp_path):
    run = snapshot(tmp_path / "run")
    partial = snapshot(tmp_path / "partial", "b" * 64, complete=False)
    cases = [
        label(run, alleles=[]),
        label(partial, case_id="partial", source_sha256="b" * 64),
        label(
            None,
            case_id="failed",
            source_sha256="c" * 64,
            run_directory=None,
            unavailable_reason="annotation_failed",
        ),
    ]
    evaluate_cases(manifest(tmp_path, cases), tmp_path / "out")
    result = json.loads((tmp_path / "out/evaluation.json").read_text())
    assert result["case_count"] == 3 and result["allele_labeled_case_count"] == 2
    assert result["metrics"]["gene"]["mrr"] == pytest.approx(1 / 6)
    assert result["metrics"]["allele"]["mrr"] == 0
    assert [case["status"] for case in result["cases"]] == [
        "completed",
        "incomplete",
        "annotation_failed",
    ]
    assert result["cases"][1]["targets"][0]["retained_after_preparation"] is True
    assert result["cases"][2]["targets"][0]["retained_after_preparation"] is None


def test_gene_mapping_mismatch_does_not_hide_successful_allele_retrieval(tmp_path):
    run = snapshot(tmp_path / "run")
    evaluate_cases(
        manifest(tmp_path, [label(run, expected_gene_id="NOT_MAPPED")]), tmp_path / "out"
    )
    case = json.loads((tmp_path / "out/evaluation.json").read_text())["cases"][0]
    assert (
        case["allele_rank"] == 2 and case["gene_rank"] is None and case["allele_gene_rank"] is None
    )
    assert case["targets"][0]["failure_reason"] == "expected_gene_not_mapped"


@pytest.mark.parametrize("mutation", ["checksum", "source", "symlink", "mode"])
def test_invalid_evidence_refuses_publication_without_mutating_run(tmp_path, mutation):
    run = snapshot(tmp_path / "run")
    case = label(run)
    settings = {}
    if mutation == "checksum":
        with (run / "ingest/tables/alleles.parquet").open("ab") as stream:
            stream.write(b"changed")
    elif mutation == "source":
        case["source_sha256"] = "e" * 64
    elif mutation == "symlink":
        target = run / "ingest/tables/alleles.parquet"
        moved = tmp_path / "moved.parquet"
        target.rename(moved)
        target.symlink_to(moved)
    else:
        settings["mode"] = "phenotype-heavy_filtering_only"
    before = contents(run)
    with pytest.raises(ValueError):
        evaluate_cases(manifest(tmp_path, [case], **settings), tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert contents(run) == before


def test_private_paths_tracing_and_cli_do_not_expose_case_values(tmp_path, monkeypatch):
    run = snapshot(tmp_path / "run")
    truth = manifest(tmp_path, [label(run, case_id="PRIVATE_SENTINEL")])
    runner = CliRunner()
    result = runner.invoke(app, ["evaluate", str(truth), str(tmp_path / "out")])
    assert result.exit_code == 0 and "PRIVATE_SENTINEL" not in result.output
    result = runner.invoke(app, ["evaluate", str(truth), str(tmp_path / "out")])
    assert result.exit_code == 1 and "PRIVATE_SENTINEL" not in result.output
    with pytest.raises(ValueError, match="existing run"):
        evaluate_cases(truth, run / "evaluation")
    git = tmp_path / "git"
    git.mkdir()
    (git / ".git").mkdir()
    with pytest.raises(ValueError, match="outside"):
        evaluate_cases(truth, git / "out")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with pytest.raises(RuntimeError, match="tracing"):
        evaluate_cases(truth, tmp_path / "traced")


def test_manifest_rejects_ambiguous_truth_and_unbounded_inputs(tmp_path):
    case = label(tmp_path / "run")
    for cases in [
        [case, case],
        [case, {**case, "case_id": "renamed"}],
        [{**case, "alleles": case["alleles"] * 2}],
        [{**case, "alleles": [{"source_row": True, "source_alt": 1}]}],
        [{**case, "run_directory": None}],
    ]:
        with pytest.raises(ValueError):
            EvaluationManifest(confirmed_local_research_use=True, cases=cases)


@pytest.mark.parametrize("fault", ["score", "rank", "run_identity"])
def test_inconsistent_persisted_results_are_not_treated_as_accuracy(tmp_path, fault):
    run = snapshot(tmp_path / "run")
    name = (
        "private_candidate_features.parquet"
        if fault == "run_identity"
        else "private_candidate_ranking.parquet"
    )
    path = run / "deliverables" / name
    rows = pq.read_table(path).to_pylist()
    if fault == "score":
        rows[1]["raw_score"] = 0.0
    elif fault == "rank":
        rows[1]["rank"] = rows[0]["rank"]
    else:
        rows[0]["run_id"] = "other-synthetic-run"
    pq.write_table(pa.Table.from_pylist(rows), path)
    # Model a producer with internally inconsistent data, not a changed checksum.
    journal = json.loads((run / "run.json").read_text())
    journal["stages"]["deliverables"]["outputs"][name] = sha256(path)
    (run / "run.json").write_text(json.dumps(journal))
    with pytest.raises(ValueError):
        evaluate_cases(manifest(tmp_path, [label(run)]), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_low_memory_refuses_evaluation_without_publishing(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from rare_disease_agent.ranking import case_evaluation

    path = manifest(tmp_path, [label(None, run_directory=None, unavailable_reason="not_run")])
    monkeypatch.setattr(
        case_evaluation.psutil, "virtual_memory", lambda: SimpleNamespace(available=1)
    )
    with pytest.raises(RuntimeError, match="memory"):
        evaluate_cases(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_actual_phase5_outputs_can_be_evaluated_without_changing_them(tmp_path):
    from test_phase5_ingestion import phase5_fixture

    from rare_disease_agent.workflows.phase5 import phase5_run

    spec = phase5_fixture(tmp_path)
    phase5_run(spec)
    before = contents(spec.output)
    case = label(spec.output, source_sha256=sha256(spec.original_vcf), expected_gene_id="GENE1")
    evaluate_cases(manifest(tmp_path, [case]), tmp_path / "evaluation")
    result = json.loads((tmp_path / "evaluation/evaluation.json").read_text())
    target = result["cases"][0]["targets"][0]
    assert target["retained_after_preparation"] and target["retained_after_filtering"]
    assert target["candidate"]["rank"] is not None
    assert contents(spec.output) == before
