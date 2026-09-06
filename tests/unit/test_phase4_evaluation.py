import pytest

from rare_disease_agent.ranking.evaluation import bootstrap_intervals, ranking_metrics


def test_metrics_count_missing_ranks_as_misses_and_bootstrap_is_seeded():
    metrics = ranking_metrics([1, 2, 10, None])
    assert metrics["mrr"] == 0.4
    assert metrics["top_1_recall"] == 0.25
    assert metrics["top_5_recall"] == 0.5
    assert metrics["top_10_recall"] == 0.75
    assert bootstrap_intervals([1, 2, 10, None], seed=17) == bootstrap_intervals(
        [1, 2, 10, None], seed=17
    )
    with pytest.raises(ValueError):
        ranking_metrics([])
    with pytest.raises(ValueError):
        ranking_metrics([0])


def test_weight_selection_finishes_before_held_out_evaluation(tmp_path, monkeypatch):
    import json

    import duckdb

    from rare_disease_agent.ranking import evaluation

    observed = []

    def mock_run(directory, *, case_name, run_id, weights, seed):
        observed.append(run_id)
        pipeline = directory / "pipeline"
        pipeline.mkdir(parents=True)
        (pipeline / "track1_metrics.json").write_text(
            json.dumps(
                {
                    "causal_variant_rank": 1 if weights["marker"] == "best" else 2,
                    "expected_inheritance_model": "de_novo",
                    "causal_variant_preserved": True,
                    "novel_gene_rescue_preserved": True,
                    "true_inheritance_model_identified": True,
                    "false_positive_inheritance_models": [],
                    "process_rss_mb_end": 100,
                    "ablations": [],
                }
            )
        )
        with duckdb.connect(str(pipeline / "candidate_membership.duckdb")) as connection:
            connection.execute(
                "CREATE TABLE evidence AS SELECT 'SYNTH-CAUSAL-001' variant_id, "
                "'de_novo' AS method, CAST(1.0 AS DOUBLE) score"
            )
            connection.execute(
                "CREATE TABLE candidate_branches AS SELECT 'r' run_id, 'novel-gene-rescue' branch"
            )
            connection.execute(
                "CREATE TABLE candidate_membership AS SELECT 'r' run_id, "
                "'novel-gene-rescue' branch, "
                "'SYNTH-CAUSAL-001' variant_id"
            )
            connection.execute(
                "CREATE TABLE ranked_evidence AS SELECT 'r' run_id, 'SYNTH-CAUSAL-001' variant_id, "
                "'filtering_phenotype_inheritance' AS mode"
            )

    monkeypatch.setattr(evaluation, "phase4_run", mock_run)
    result = evaluation.benchmark_phase4(
        tmp_path, weight_sets={"best": {"marker": "best"}, "worse": {"marker": "worse"}}
    )
    assert result["selected_weights"] == "best"
    split = result["split"]
    assert set(split["calibration"]).isdisjoint(split["evaluation"])
    first_evaluation = next(
        index for index, value in enumerate(observed) if value.startswith("evaluation-")
    )
    assert first_evaluation == 2 * len(split["calibration"])
    assert all(value.startswith("evaluation-") for value in observed[first_evaluation:])
