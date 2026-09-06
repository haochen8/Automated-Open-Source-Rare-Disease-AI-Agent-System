"""Bounded generated evidence stress benchmark; checks live resources before generation."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import psutil

from rare_disease_agent.agents.schemas import (
    EvaluateInheritanceParameters,
    RankGenesByPhenotypeParameters,
)
from rare_disease_agent.config import load_settings
from rare_disease_agent.ranking.persistent import rank_persistent, ranking_rows
from rare_disease_agent.ranking.scoring import PreliminaryRanker
from rare_disease_agent.reporting.provenance import software_identity
from rare_disease_agent.resource_management import atomic_json
from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.synthetic.cases import load_synthetic_case, load_synthetic_phenotype_store
from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
from rare_disease_agent.tools.phenotype.tools import PhenotypeToolbox
from rare_disease_agent.tools.variants.agent_tools import VariantToolbox
from rare_disease_agent.tools.variants.vcf import parse_vcf


def stress_benchmark(output: Path, *, count: int = 20000) -> dict:
    if not 100 <= count <= 200000:
        raise ValueError("Stress size outside supported development bounds")
    output.mkdir(parents=True, exist_ok=True)
    if (
        psutil.virtual_memory().available < 768 * 1024**2 + count * 1024
        or psutil.disk_usage(output).free < 2 * 1024**3
    ):
        raise RuntimeError("Insufficient live memory/disk for synthetic stress case")
    process = psutil.Process()
    initial = process.memory_info().rss
    peak = [initial]
    stop = threading.Event()

    def monitor():
        while not stop.wait(0.02):
            peak[0] = max(peak[0], process.memory_info().rss)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    started = time.monotonic()
    toolbox = None
    try:
        case = load_synthetic_case("de-novo")
        template = next(parse_vcf(Path(str(case.vcf_resource))))
        calls = json.loads(template.genotype_calls_json)
        for value in calls.values():
            value["genotype"] = "0/0"

        def records():
            for index in range(count):
                yield template.model_copy(
                    update={
                        "variant_id": f"SYN-STRESS-{index:08d}",
                        "position": index + 1,
                        "gene": f"SYN_GENE_{index % 100}",
                        "genotype": "0/0",
                        "genotype_calls_json": json.dumps(calls),
                    }
                )

        path = output / "stress.parquet"
        write_variants_parquet(records(), path, batch_size=256)
        software = software_identity()
        phenotype = PhenotypeToolbox(
            load_synthetic_phenotype_store(),
            patient_hpo=case.hpo_terms,
            run_id="stress",
            software=software,
        )
        inheritance = InheritanceEvaluator(case.pedigree, run_id="stress", software=software)
        toolbox = VariantToolbox(
            path,
            run_id="stress",
            membership_database=output / "stress.duckdb",
            phenotype_toolbox=phenotype,
            inheritance_evaluator=inheritance,
        )
        phenotype_observation = toolbox.rank_genes_by_phenotype(
            RankGenesByPhenotypeParameters(branch="all")
        )
        inheritance_observation = toolbox.evaluate_inheritance(
            EvaluateInheritanceParameters(branch="all")
        )
        ranker = PreliminaryRanker(
            weights=load_settings().track1.preliminary_scoring.model_dump(),
            run_id="stress",
            software=software,
        )
        rank_persistent(toolbox, ranker, "all")
        top = list(
            ranking_rows(
                toolbox.membership._connection,
                "stress",
                "filtering_phenotype_inheritance",
                limit=20,
            )
        )
        evidence_count = toolbox.membership._connection.execute(
            "SELECT count(*) FROM evidence"
        ).fetchone()[0]
        result = {
            "variants": count,
            "evidence_rows": evidence_count,
            "max_batch": toolbox.evidence_store.max_batch_observed,
            "top_k": len(top),
            "observation_bytes": len(phenotype_observation.model_dump_json())
            + len(inheritance_observation.model_dump_json()),
            "initial_rss_mb": initial / 1024**2,
            "peak_rss_mb": max(peak[0], process.memory_info().rss) / 1024**2,
            "runtime_seconds": time.monotonic() - started,
        }
        atomic_json(output / "stress_metrics.json", result)
        return result
    finally:
        stop.set()
        thread.join()
        if toolbox:
            toolbox.membership.close()
