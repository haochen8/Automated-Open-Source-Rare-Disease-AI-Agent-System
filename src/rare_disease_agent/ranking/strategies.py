"""Fixed, auditable Phase 5 sensitivity strategies; these are not calibrated probabilities."""

from rare_disease_agent.ranking.persistent import rank_persistent
from rare_disease_agent.ranking.scoring import PreliminaryRanker

STRATEGIES = {
    "baseline": (0.10, 0.35, 0.35, 0.10, 0.10),
    "phenotype-heavy": (0.10, 0.15, 0.10, 0.55, 0.10),
    "inheritance-heavy": (0.10, 0.15, 0.10, 0.10, 0.55),
    "pathogenicity-heavy": (0.10, 0.20, 0.50, 0.10, 0.10),
    "ensemble": (0.10, 0.20, 0.15, 0.30, 0.25),
    "conservative": (0.10, 0.20, 0.15, 0.30, 0.25),
}


def rank_strategies(toolbox, ranker: PreliminaryRanker, annotation_version: str):
    names = ("quality", "rarity", "consequence", "phenotype", "inheritance")
    for strategy, values in STRATEGIES.items():
        selected = PreliminaryRanker(
            weights=dict(zip(names, values, strict=True)),
            run_id=ranker.run_id,
            software=ranker.software,
        )
        rank_persistent(
            toolbox,
            selected,
            "conservative" if strategy == "conservative" else "ensemble",
            prefix=strategy + "_",
            annotation_version=annotation_version,
        )
