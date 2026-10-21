"""Portable wrapper authentication uses deliberately synthetic journals and bytes."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "track1_select.py"
SPEC = importlib.util.spec_from_file_location("standalone_track1_select", SCRIPT)
selector = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPT.parent))
try:
    SPEC.loader.exec_module(selector)
finally:
    sys.path.pop(0)

HEAD = "a" * 40
RUN_ID = "synthetic-portable-selection"
PROBAND = "SYNTHETIC_SELECTION_CASE"
SOFTWARE = {"git_commit": HEAD, "git_dirty": False, "python_version": "3", "dependencies": {}}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(selector.secure.json_bytes(value))


def read(path):
    return json.loads(path.read_bytes())


def checksum(path):
    return selector.secure.sha256(path.read_bytes())


def inventory(path):
    return {
        item.relative_to(path).as_posix(): checksum(item)
        for item in path.rglob("*")
        if item.is_file()
    }


def seal_journal(path, configuration, stages):
    value = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "configuration": configuration,
        "configuration_hash": selector.secure.sha256(
            json.dumps(configuration, sort_keys=True).encode()
        ),
        "provenance": {"software": SOFTWARE},
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:01+00:00",
        "failure_reason": None,
        "stages": {
            stage: {
                "ordinal": ordinal,
                "outputs": inventory(path / stage),
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T00:00:01+00:00",
            }
            for ordinal, stage in enumerate(stages)
        },
    }
    write(path / "run.json", value)
    return value


def reseal(fixture):
    ranked = seal_journal(fixture.run, fixture.ranking, selector.RANKING_STAGES)
    write(
        fixture.run / "integrity_manifest.json",
        {
            "configuration_hash": ranked["configuration_hash"],
            "outputs": {
                f"{stage}/{name}": digest
                for stage, record in ranked["stages"].items()
                for name, digest in record["outputs"].items()
            },
            "run_id": RUN_ID,
            "scope": "immutable stage artifacts; supervision receipts are separate",
        },
    )
    seal_journal(fixture.root, fixture.outer, selector.CORPUS_STAGES)
    fixture.config["corpus_journal_sha256"] = checksum(fixture.root / "run.json")
    write(fixture.config_path, fixture.config)


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    root = tmp_path / "corpus"
    run = root / "ranking" / "run"
    assembly = root / "assembly"
    source_sha, code_sha, plan_sha, hpo_sha = (letter * 64 for letter in "bcde")
    contexts = [
        {
            "provider": provider,
            "tool_version": version,
            "data_version": version.split(".")[0],
            "genome_build": "GRCh38",
            "reference_sha256": "f" * 64,
            "cache_path": "/unopened/public/cache",
            "annotation_profile": "track1",
            "executable_sha256": "1" * 64,
        }
        for provider, version in (("bcftools", "1.22"), ("vep", "116.0"))
    ]
    assembly.mkdir(parents=True)
    (assembly / "tables").mkdir()
    for name in selector.TABLES:
        (assembly / "tables" / (name + ".parquet")).write_bytes(b"synthetic " + name.encode())
    write(assembly / "tables" / "manifest.json", {"outputs": inventory(assembly / "tables")})
    (assembly / "shard_proofs.jsonl").write_bytes(b'{"synthetic":true}\n')
    manifest = {
        "format": "verified-corpus-assembly-v1",
        "status": "completed",
        "source_sha256": source_sha,
        "code_sha256": code_sha,
        "plan_manifest_sha256": plan_sha,
        "source_plan": {"source_sha256": source_sha},
        "context": contexts,
        "coverage": {"whole_genome_analysis_complete": False},
        "proof_index": {
            "path": "shard_proofs.jsonl",
            "sha256": checksum(assembly / "shard_proofs.jsonl"),
            "shards": 1,
        },
    }
    write(assembly / "run_manifest.json", manifest)
    write(assembly / "reconciliation.json", {"genotype_reconciliation": True})
    write(assembly / "integrity_manifest.json", inventory(assembly))
    declaration = {
        "confirmed_local_research_use": True,
        "confirmed_affected_sample": False,
        "run_id": RUN_ID,
        "dataset_revision": "synthetic",
        "output": "/historical/corpus/ranking.partial/run",
    }
    ranking = {
        "specification": declaration,
        "code_sha256": code_sha,
        "rehearsal_integrity_sha256": checksum(assembly / "integrity_manifest.json"),
        "hpo_manifest_sha256": hpo_sha,
        "inputs": {
            "challenge_track1_vcf": {"sha256": source_sha},
            "challenge_index": {"sha256": "2" * 64},
            "challenge_phenotype": {"sha256": "3" * 64},
        },
    }
    outer = {
        "version": "sealed-annotation-corpus-v1",
        "code_sha256": code_sha,
        "source_sha256": source_sha,
        "plan_manifest_sha256": plan_sha,
        "index_sha256": "2" * 64,
        "phenotype_sha256": "3" * 64,
        "hpo_manifest_sha256": hpo_sha,
        "specification": {
            "confirmed_local_research_use": True,
            "ranking": declaration | {"output": "/historical/corpus/ranking/run"},
            **{
                name: {
                    "job": {
                        key: value for key, value in context.items() if key != "executable_sha256"
                    },
                    "executable_sha256": context["executable_sha256"],
                }
                for name, context in zip(("normalization", "annotation"), contexts, strict=True)
            },
        },
    }
    for stage in selector.CORPUS_STAGES:
        (root / stage).mkdir(exist_ok=True)
    write(root / "index" / "manifest.json", {"synthetic": True})
    write(root / "validation" / "manifest.json", {"synthetic": True})
    write(
        root / "pair_preflight" / "pair_workload.json",
        {
            "assembly_integrity_sha256": ranking["rehearsal_integrity_sha256"],
            "within_budget": True,
            "pair_work_budget": 200_000,
            "eligible_candidate_pairs": 0,
        },
    )
    for name in selector.TABLES:
        target = run / "ingest" / "tables" / (name + ".parquet")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((assembly / "tables" / target.name).read_bytes())
    write(run / "ingest" / "tables" / "manifest.json", read(assembly / "tables" / "manifest.json"))
    write(run / "prepare" / "run_manifest.json", ranking)
    write(run / "prepare" / "annotation_provenance.json", manifest)
    write(run / "prepare" / "reconciliation.json", read(assembly / "reconciliation.json"))
    (run / "prepare" / "shard_proofs.jsonl").write_bytes(
        (assembly / "shard_proofs.jsonl").read_bytes()
    )
    write(run / "prepare" / "phenotype.json", {"explicit_hpo_ids": ["HP:0000001"]})
    workflow = run / "analysis" / "workflow"
    write(workflow / "preflight" / "validation.json", {"synthetic": True})
    write(workflow / "report" / "research_report.json", {"synthetic": True})
    database = workflow / "pipeline" / "candidate_membership.duckdb"
    database.parent.mkdir()
    database.write_bytes(b"synthetic database bytes; core is mocked at this boundary")
    analysis = {
        "software": SOFTWARE,
        "phase5_strategies": True,
        "mode": "authorized-local-mock",
        "resource_lock_hash": hpo_sha,
        "input_sha256": checksum(run / "ingest" / "tables" / "variants.parquet"),
        "hpo_terms_sha256": selector.secure.sha256(json.dumps(["HP:0000001"]).encode()),
    }
    seal_journal(workflow, analysis, selector.WORKFLOW_STAGES)
    write(run / "deliverables" / "report.json", {"synthetic": True})
    write(
        run / "deliverables" / "sanitized_metrics.json",
        {
            "sample_phenotype_linkage_confirmed": False,
            "status": "completed_bounded_dry_run",
            "coverage": manifest["coverage"],
            "whole_genome_analysis_complete": False,
        },
    )
    fixture = SimpleNamespace(
        root=root,
        run=run,
        outer=outer,
        ranking=ranking,
        config_path=tmp_path / "selection.json",
        config={
            "schema_version": 1,
            "corpus_directory": str(root),
            "corpus_journal_sha256": "0" * 64,
            "expected_analysis_head": HEAD,
            "expected_proband": PROBAND,
            "output_directory": str(tmp_path / "selection"),
            "confirmed_local_research_use": True,
        },
    )
    reseal(fixture)
    monkeypatch.setattr(
        selector.psutil, "virtual_memory", lambda: SimpleNamespace(available=8 * 1024**3)
    )
    monkeypatch.setattr(
        selector.psutil, "disk_usage", lambda _path: SimpleNamespace(free=8 * 1024**3)
    )
    monkeypatch.setattr(selector.core, "read_data", fake_core)
    return fixture


def fake_core(context, budget, variants_sha256):
    budget.check()
    assert context["run_id"] == RUN_ID and context["analysis_head"] == HEAD
    assert variants_sha256 == checksum(context["paths"]["variants"])
    candidate = selector.policy.Candidate(
        selector.policy.Allele("1", 101, "A", "T"),
        1,
        ("SYNTHETIC_GENE",),
        "homozygous_alt",
        "synthetic_source",
    )
    selection = selector.policy.select_hypotheses([candidate], lambda _anchor: ())
    return selector.policy.render_csv(selection, PROBAND), {
        "version": selector.core.VERSION,
        "status": selector.core.STATUS,
        "synthetic": True,
    }


def test_completed_corpus_writes_private_sealed_selection(corpus):
    before = inventory(corpus.root)
    selector.select(corpus.config_path)
    output = Path(corpus.config["output_directory"])
    assert set(inventory(output)) == {
        "draft.csv",
        "audit.json",
        "manifest.json",
        "integrity_manifest.json",
    }
    seal = read(output / "integrity_manifest.json")
    assert all(checksum(output / name) == value for name, value in seal.items())
    assert read(output / "manifest.json")["source_binding"]["original_inputs_reopened"] is False
    assert inventory(corpus.root) == before
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


@pytest.mark.parametrize(
    "mutation", ["missing", "corrupt", "extra", "unsafe", "symlink", "wrong_pin"]
)
def test_untrusted_or_changed_stage_inventory_refused(corpus, mutation):
    artifact = corpus.run / "ingest" / "tables" / "alleles.parquet"
    if mutation == "missing":
        artifact.unlink()
    elif mutation == "corrupt":
        artifact.write_bytes(b"changed")
    elif mutation == "extra":
        (artifact.parent / "unexpected").write_bytes(b"uncommitted")
    elif mutation == "unsafe":
        value = read(corpus.root / "run.json")
        value["stages"]["index"]["outputs"]["../run.json"] = "a" * 64
        write(corpus.root / "run.json", value)
        corpus.config["corpus_journal_sha256"] = checksum(corpus.root / "run.json")
        write(corpus.config_path, corpus.config)
    elif mutation == "symlink":
        destination = artifact.with_suffix(".outside")
        artifact.rename(destination)
        artifact.symlink_to(destination)
    else:
        corpus.config["corpus_journal_sha256"] = "9" * 64
        write(corpus.config_path, corpus.config)
    with pytest.raises(selector.secure.ExportRefusal):
        selector.select(corpus.config_path)
    assert not Path(corpus.config["output_directory"]).exists()


@pytest.mark.parametrize(
    "relationship", ["source", "resource", "code", "table", "unfinished", "dirty"]
)
def test_internally_resealed_inconsistent_corpus_refused(corpus, relationship):
    if relationship in {"source", "resource", "code"}:
        key = {"source": "source_sha256", "resource": "hpo_manifest_sha256", "code": "code_sha256"}[
            relationship
        ]
        corpus.outer[key] = "9" * 64
    elif relationship == "table":
        (corpus.run / "ingest" / "tables" / "variants.parquet").write_bytes(b"wrong table")
    else:
        workflow = corpus.run / "analysis" / "workflow" / "run.json"
        value = read(workflow)
        if relationship == "unfinished":
            value["ended_at"] = None
        else:
            value["provenance"]["software"]["git_dirty"] = True
        write(workflow, value)
    reseal(corpus)
    with pytest.raises(selector.secure.ExportRefusal):
        selector.select(corpus.config_path)
    assert not Path(corpus.config["output_directory"]).exists()


@pytest.mark.parametrize("change", ["replace", "newfile"])
def test_input_mutation_during_selection_prevents_publication(corpus, monkeypatch, change):
    def changing_core(context, budget, digest):
        result = fake_core(context, budget, digest)
        path = context["paths"]["alleles"]
        if change == "newfile":
            path = path.parent / "newfile"
        path.write_bytes(b"changed after authentication")
        return result

    monkeypatch.setattr(selector.core, "read_data", changing_core)
    with pytest.raises(selector.secure.ExportRefusal):
        selector.select(corpus.config_path)
    assert not Path(corpus.config["output_directory"]).exists()
    assert not list(corpus.config_path.parent.glob(".track1-select-*"))


@pytest.mark.parametrize("linkage", [True, "false", 0, None])
def test_saved_linkage_declaration_must_be_boolean_and_consistent(corpus, linkage):
    metrics = read(corpus.run / "deliverables" / "sanitized_metrics.json")
    metrics["sample_phenotype_linkage_confirmed"] = linkage
    write(
        corpus.run / "deliverables" / "sanitized_metrics.json",
        metrics,
    )
    reseal(corpus)
    with pytest.raises(selector.secure.ExportRefusal):
        selector.select(corpus.config_path)
    assert not Path(corpus.config["output_directory"]).exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "incomplete"),
        ("coverage", {"whole_genome_analysis_complete": True}),
        ("whole_genome_analysis_complete", True),
    ],
)
def test_completed_metrics_and_coverage_must_agree_with_assembly(corpus, key, value):
    metrics = read(corpus.run / "deliverables" / "sanitized_metrics.json")
    metrics[key] = value
    write(corpus.run / "deliverables" / "sanitized_metrics.json", metrics)
    reseal(corpus)
    with pytest.raises(selector.secure.ExportRefusal):
        selector.select(corpus.config_path)
    assert not Path(corpus.config["output_directory"]).exists()


def test_live_budget_interrupts_active_database(corpus, monkeypatch):
    budget = selector.Budget(corpus.config_path.parent)
    interrupted = []
    budget.db = SimpleNamespace(interrupt=lambda: interrupted.append(True))
    monkeypatch.setattr(budget, "_sample", lambda: (_ for _ in ()).throw(ValueError()))
    budget._monitor()
    assert interrupted and budget.failed.is_set()
    with pytest.raises(selector.secure.ExportRefusal):
        budget.check()


@pytest.mark.parametrize(
    "bound",
    [
        "MAX_INPUT_BYTES",
        "MAX_METADATA_BYTES",
        "MAX_FILES",
        "MAX_SECONDS",
        "MAX_RSS",
        "MAX_OUTPUT_BYTES",
    ],
)
def test_resource_refusal_is_fixed_and_never_publishes(corpus, monkeypatch, capsys, bound):
    monkeypatch.setattr(selector, bound, 0)
    assert selector.main(["--config", str(corpus.config_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == selector.REFUSAL
    assert not Path(corpus.config["output_directory"]).exists()


@pytest.mark.parametrize("bound", ["MIN_AVAILABLE", "MIN_DISK"])
def test_live_resource_prerequisites_fail_closed(corpus, monkeypatch, bound):
    monkeypatch.setattr(selector, bound, 16 * 1024**3)
    with pytest.raises(selector.secure.ExportRefusal):
        selector.select(corpus.config_path)
    assert not Path(corpus.config["output_directory"]).exists()


def test_existing_destination_and_false_authorization_refused(corpus):
    output = Path(corpus.config["output_directory"])
    output.mkdir()
    (output / "keep").write_bytes(b"preexisting")
    with pytest.raises(selector.secure.ExportRefusal):
        selector.select(corpus.config_path)
    assert (output / "keep").read_bytes() == b"preexisting"
    corpus.config["confirmed_local_research_use"] = False
    write(corpus.config_path, corpus.config)
    with pytest.raises(selector.secure.ExportRefusal):
        selector.select(corpus.config_path)


def test_publication_failure_leaves_no_partial_output(corpus, monkeypatch):
    monkeypatch.setattr(selector.secure, "publish", lambda *_args: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        selector.select(corpus.config_path)
    assert not Path(corpus.config["output_directory"]).exists()
    assert not list(corpus.config_path.parent.glob(".track1-select-*"))


def test_actual_synthetic_corpus_selects_and_exports_without_original_inputs(tmp_path, monkeypatch):
    from rare_disease_agent.reporting.provenance import SoftwareIdentity
    from rare_disease_agent.workflows import authorized, recovery
    from tests import test_corpus as upstream

    # External tools remain synthetic; the real assembly, evidence, ranking,
    # journals, readonly selector and final exporter all run unchanged.
    monkeypatch.setattr(recovery, "software_identity", lambda: SoftwareIdentity(**SOFTWARE))
    monkeypatch.setattr(authorized, "software_identity", lambda: SoftwareIdentity(**SOFTWARE))
    upstream.resources.__wrapped__(monkeypatch)
    tmp_path = tmp_path.resolve()
    workflow = upstream.inline_stages(monkeypatch)
    fixture = upstream.corpus_fixture(tmp_path)
    spec = upstream.reference_spec(fixture)
    workflow.run_corpus(spec)
    config_path = tmp_path / "select.json"
    output = tmp_path / "selection"
    write(
        config_path,
        {
            "schema_version": 1,
            "corpus_directory": str(spec.output),
            "corpus_journal_sha256": checksum(spec.output / "run.json"),
            "expected_analysis_head": HEAD,
            "expected_proband": PROBAND,
            "output_directory": str(output),
            "confirmed_local_research_use": True,
        },
    )
    fixture.ranking.original_vcf.unlink()
    fixture.ranking.phenotype_docx.unlink()
    selector.select(config_path)
    audit = read(output / "audit.json")
    assert audit["hypotheses"]
    assert audit["saved_run_declarations"]["confirmed_affected_sample"] is False
    assert audit["source_binding"]["original_inputs_reopened"] is False
    export_config = tmp_path / "export.json"
    exported = tmp_path / "export"
    write(
        export_config,
        {
            "schema_version": 1,
            "source_directory": str(output),
            "source_integrity_sha256": checksum(output / "integrity_manifest.json"),
            "expected_proband": PROBAND,
            "chromosome_mapping_policy": selector.secure.MAPPING_POLICY,
            "output_directory": str(exported),
        },
    )
    selector.secure.export(export_config)
    assert (exported / "submission.csv").read_bytes().startswith(b"proband_id,")
