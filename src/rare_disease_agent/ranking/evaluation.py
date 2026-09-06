"""Reproducible calibration/evaluation split and case-bootstrap intervals."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from rare_disease_agent.config import load_settings
from rare_disease_agent.resource_management import atomic_json
from rare_disease_agent.synthetic.cases import SYNTHETIC_CASE_NAMES
from rare_disease_agent.synthetic.phase4 import EDGE_CASES
from rare_disease_agent.workflows.phase4 import phase4_run


def ranking_metrics(ranks: list[int | None]) -> dict[str, float]:
    if not ranks or any(
        rank is not None and (isinstance(rank, bool) or rank < 1) for rank in ranks
    ):
        raise ValueError("Ranks must be positive or missing, with at least one case")
    return {
        "mrr": sum(1 / rank if rank else 0 for rank in ranks) / len(ranks),
        **{
            f"top_{k}_recall": sum(rank is not None and rank <= k for rank in ranks) / len(ranks)
            for k in (1, 5, 10)
        },
    }


def bootstrap_intervals(ranks: list[int | None], *, seed: int, samples: int = 1000) -> dict:
    if not 100 <= samples <= 10000:
        raise ValueError("Bootstrap budget outside bounds")
    ranking_metrics(ranks)
    rng = random.Random(seed)
    values = {name: [] for name in ranking_metrics(ranks)}
    for _ in range(samples):
        for name, value in ranking_metrics(rng.choices(ranks, k=len(ranks))).items():
            values[name].append(value)
    return {
        name: [
            sorted(series)[int(samples * 0.025)],
            sorted(series)[min(samples - 1, int(samples * 0.975))],
        ]
        for name, series in values.items()
    }


def benchmark_phase4(
    output: Path, *, weight_sets: dict[str, dict] | None = None, seed: int = 17
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    defaults = load_settings().track1.preliminary_scoring.model_dump()
    weights = weight_sets or {
        "baseline": defaults,
        "phenotype_emphasis": {**defaults, "phenotype": 0.4, "inheritance": 0.15},
    }
    if not weights or len(weights) > 10 or any(not name.isidentifier() for name in weights):
        raise ValueError("Invalid weight set names/count")
    split = {
        "calibration": list(SYNTHETIC_CASE_NAMES),
        "evaluation": list(EDGE_CASES),
        "seed": seed,
        "limitation": (
            "Disjoint case names share synthetic templates; not independent clinical validation."
        ),
    }
    atomic_json(output / "split.json", split)
    calibration = {}
    for name, configuration in weights.items():
        ranks = []
        for case in split["calibration"]:
            directory = output / name / case
            phase4_run(
                directory,
                case_name=case,
                run_id=f"calibration-{name}-{case}",
                weights=configuration,
                seed=seed,
            )
            ranks.append(
                json.loads((directory / "pipeline/track1_metrics.json").read_text())[
                    "causal_variant_rank"
                ]
            )
        calibration[name] = ranking_metrics(ranks)
    selected = min(weights, key=lambda name: (-calibration[name]["mrr"], name))
    # Selection is complete before any held-out evaluation case is inspected.
    atomic_json(
        output / "selection.json",
        {"selected": selected, "weights": weights, "calibration": calibration, "seed": seed},
    )
    cases = []
    for case in split["evaluation"]:
        directory = output / "evaluation" / case
        started = time.monotonic()
        phase4_run(
            directory,
            case_name=case,
            run_id=f"evaluation-{case}",
            weights=weights[selected],
            seed=seed,
        )
        metrics = json.loads((directory / "pipeline/track1_metrics.json").read_text())
        # Evaluate uncertainty on the expected model, separately from all-model false positives.
        import duckdb

        with duckdb.connect(
            str(directory / "pipeline/candidate_membership.duckdb"), read_only=True
        ) as connection:
            fit = (
                connection.execute(
                    (
                        "SELECT max(score) FROM evidence WHERE variant_id='SYNTH-CAUSAL-001' AND "
                        "method=?"
                    ),
                    [metrics["expected_inheritance_model"]],
                ).fetchone()[0]
                or 0
            )
            branches = dict(
                connection.execute(
                    "SELECT b.branch, count(m.variant_id) FROM candidate_branches b "
                    "LEFT JOIN candidate_membership m ON m.run_id=b.run_id AND m.branch=b.branch "
                    "AND m.variant_id='SYNTH-CAUSAL-001' GROUP BY b.branch ORDER BY b.branch"
                ).fetchall()
            )
            rescue_total, rescue_retained = connection.execute(
                "SELECT count(*), count(r.variant_id) FROM candidate_membership m "
                "LEFT JOIN ranked_evidence r ON r.run_id=m.run_id AND r.variant_id=m.variant_id "
                "AND r.mode='filtering_phenotype_inheritance' WHERE m.branch='novel-gene-rescue'"
            ).fetchone()
        uncertain_truth = case not in {"affected-sibling", "annotation-missing"}
        cases.append(
            {
                "case": case,
                "rank": metrics["causal_variant_rank"],
                "branch_recall": int(metrics["causal_variant_preserved"]),
                "rescue_recall": rescue_retained / rescue_total if rescue_total else 1.0,
                "causal_survival_by_branch": branches,
                "inheritance_correct": int(metrics["true_inheritance_model_identified"]),
                "false_positive_models": metrics["false_positive_inheritance_models"],
                "uncertain_truth": uncertain_truth,
                "uncertain_predicted": 0 < fit < 0.8,
                "uncertainty_brier": ((1 - fit) - int(uncertain_truth)) ** 2,
                "runtime_seconds": round(time.monotonic() - started, 3),
                "rss_mb_end": metrics["process_rss_mb_end"],
                "ablations": metrics["ablations"],
            }
        )
    ranks = [case["rank"] for case in cases]
    result = {
        "schema_version": 1,
        "seed": seed,
        "split": split,
        "selected_weights": selected,
        "configuration": weights,
        "cases": cases,
        "metrics": ranking_metrics(ranks),
        "confidence_intervals_95": bootstrap_intervals(ranks, seed=seed),
        "branch_recall": sum(case["branch_recall"] for case in cases) / len(cases),
        "rescue_recall": sum(case["rescue_recall"] for case in cases) / len(cases),
        "inheritance_accuracy": sum(case["inheritance_correct"] for case in cases) / len(cases),
        "false_positive_inheritance_models": sum(
            len(case["false_positive_models"]) for case in cases
        ),
        "uncertainty_accuracy": sum(
            case["uncertain_truth"] == case["uncertain_predicted"] for case in cases
        )
        / len(cases),
        "uncertainty_brier": sum(case["uncertainty_brier"] for case in cases) / len(cases),
        "uncertainty_limitation": (
            "Brier diagnostic treats heuristic fit as a proxy; it is not a calibrated probability."
        ),
    }
    atomic_json(output / "benchmark.json", result)
    return result
