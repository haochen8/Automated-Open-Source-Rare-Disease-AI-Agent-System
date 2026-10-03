"""Synthetic disjoint assembly and isolated-batch recovery regressions."""

import shutil
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from test_partitioning import HEADER
from test_phase5_ingestion import CSQ, HEAD, phase5_fixture
from typer.testing import CliRunner

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.tools.variants.adapters import ToolJob
from rare_disease_agent.tools.variants.ingest import ingest_vep
from rare_disease_agent.workflows import assembly as a
from rare_disease_agent.workflows import coverage_batch as c
from rare_disease_agent.workflows import partitioning as p
from rare_disease_agent.workflows.phase5 import phase5_run
from rare_disease_agent.workflows.phase5_cli import app


@pytest.fixture(autouse=True)
def resources(monkeypatch):
    monkeypatch.setattr(
        c.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(available=4 * 1024**3, total=8 * 1024**3),
    )
    monkeypatch.setattr(c.psutil, "disk_usage", lambda _: SimpleNamespace(free=30 * 1024**3))


def setup(tmp_path, duplicate=False):
    base = tmp_path / "base"
    base.mkdir()
    ranking = phase5_fixture(base)
    source = tmp_path / "source.vcf"
    source.write_text(
        HEADER
        + "".join(
            f"1\t{100 if duplicate else pos}\t.\tA\tG\t50\tPASS\t.\tGT:GQ\t0/1:90\n"
            for pos in (100, 101)
        )
    )
    plan = tmp_path / "plan"
    p.prepare_coverage(
        p.CoverageInput(
            source=source,
            source_sha256=sha256(source),
            output=plan,
            records_per_shard=1,
            confirmed_local_research_use=True,
        )
    )
    partitions = tmp_path / "partitions"
    p.materialize_partitions(plan, partitions, first=1, last=2, authorized=True)
    reference = tmp_path / "reference"
    reference.write_text("synthetic reference")
    cache = tmp_path / "cache"
    cache.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tools = []
    for provider, version, data in (("bcftools", "1.22", "1"), ("vep", "116.0", "116")):
        executable = bin_dir / provider
        executable.write_text("synthetic placeholder; never executed")
        executable.chmod(0o700)
        tools.append(
            c.PinnedTool(
                executable=executable,
                executable_sha256=sha256(executable),
                job=ToolJob(
                    provider=provider,
                    tool_version=version,
                    data_version=data,
                    genome_build="GRCh38",
                    input_path=source,
                    output_path=tmp_path / "unused",
                    reference_path=reference,
                    reference_sha256=sha256(reference),
                    cache_path=cache,
                    annotation_profile="track1",
                    threads=1,
                    buffer_size=1,
                    memory_mb=128,
                    output_limit_mb=1,
                ),
            )
        )
    output = tmp_path / "batch"
    return c.CoverageBatchInput(
        plan=plan,
        partitions=partitions,
        shards=[1, 2],
        normalization=tools[0],
        annotation=tools[1],
        ranking=ranking.model_copy(
            update={
                "original_vcf": source,
                "annotation_rehearsal": output / "assembly",
                "output": output / "ranking" / "run",
                "confirmed_affected_sample": False,
            }
        ),
        output=output,
        confirmed_local_research_use=True,
        confirmed_annotation_execution=True,
    )


def fake_execute(job, *, authorized, observation, isolate_process_group):
    assert authorized and not isolate_process_group
    rows = [x for x in job.input_path.read_text().splitlines() if not x.startswith("#")]
    if job.provider == "vep":
        rows = [x.replace("\tGT:GQ", ";CSQ=" + CSQ + "\tGT:GQ") for x in rows]
    job.output_path.write_text(HEAD + "\n".join(rows) + "\n")
    return {
        "provider": job.provider,
        "configuration": job.model_dump(mode="json"),
        "tool_version": job.tool_version,
        "data_version": job.data_version,
        "reference_sha256": job.reference_sha256,
        "executable_sha256": sha256(Path(shutil.which(job.provider))),
        "input_sha256": sha256(job.input_path),
        "output_sha256": sha256(job.output_path),
        "returncode": 0,
    }


def validated(spec, monkeypatch):
    monkeypatch.setattr(c, "execute_job", fake_execute)
    spec.output.mkdir()
    validations = []
    for number in spec.shards:
        for prefix in ("annotation", "validation"):
            output = spec.output / f"{prefix}_{number:06d}"
            output.mkdir()
            c.run_stage(spec, f"{prefix}_{number:06d}", output)
        validations.append(output)
    return validations


def test_global_tables_match_unpartitioned_control_and_rank_cross_shard_pairs(
    tmp_path, monkeypatch
):
    spec = setup(tmp_path)
    validations = validated(spec, monkeypatch)
    output = spec.output / "assembly"
    output.mkdir()
    a.assemble_verified(spec.plan, list(reversed(validations)), output)
    manifest = a.verify_assembly(output)
    assert manifest["coverage"]["included_source_records"] == 2
    assert manifest["coverage"]["whole_genome_analysis_complete"] is False
    control = tmp_path / "control"
    for kind in ("normalized", "annotated"):
        rows = [
            line
            for n in spec.shards
            for line in (spec.output / f"annotation_{n:06d}" / f"{kind}.vcf")
            .read_text()
            .splitlines()
            if not line.startswith("#")
        ]
        (tmp_path / f"{kind}.vcf").write_text(HEAD + "\n".join(rows) + "\n")
    ingest_vep(tmp_path / "annotated.vcf", tmp_path / "normalized.vcf", control)
    with duckdb.connect() as db:
        for table in a.TABLES:
            assert sorted(
                db.from_parquet(str(output / "tables" / f"{table}.parquet")).fetchall()
            ) == sorted(db.from_parquet(str(control / f"{table}.parquet")).fetchall())
    metrics = phase5_run(spec.ranking)
    assert metrics["gene_level_candidates"] == 4
    assert len(metrics["ranking_counts"]) == 28
    assert metrics["coverage"] == manifest["coverage"]
    assert metrics["sample_phenotype_linkage_confirmed"] is False
    database = (
        spec.ranking.output / "analysis" / "workflow" / "pipeline" / "candidate_membership.duckdb"
    )
    with duckdb.connect(str(database), read_only=True) as db:
        pairs = db.sql("SELECT payload FROM compound_pairs").fetchall()
        assert len(pairs) == 2
        assert all("r1_a1" in pair[0] and "r2_a1" in pair[0] for pair in pairs)


@pytest.mark.parametrize(
    "mutation", ["duplicate", "overlap", "context", "sample", "corrupt", "code"]
)
def test_assembly_refuses_ambiguous_or_changed_inputs(tmp_path, monkeypatch, mutation):
    spec = setup(tmp_path, duplicate=mutation == "duplicate")
    validations = validated(spec, monkeypatch)
    if mutation in {"context", "sample", "code", "overlap"}:
        proof = a.read_json(validations[1] / "proof.json")
        if mutation == "context":
            proof["context"][1]["reference_sha256"] = "b" * 64
        elif mutation == "sample":
            proof["sample_identity_sha256"] = "b" * 64
        elif mutation == "code":
            proof["code_sha256"] = "b" * 64
        else:
            proof["segment"] = a.read_json(validations[0] / "proof.json")["segment"]
        atomic_json(validations[1] / "proof.json", proof)
        a.seal(validations[1])
    elif mutation == "corrupt":
        with (validations[1] / "tables" / "variants.parquet").open("ab") as stream:
            stream.write(b"SYNTHETIC_PRIVATE_SENTINEL")
    output = spec.output / "assembly"
    output.mkdir()
    with pytest.raises(ValueError):
        a.assemble_verified(spec.plan, validations, output)
    assert not (output / "integrity_manifest.json").exists()


def test_resume_preserves_annotation_validation_and_assembly_after_ranking_failure(
    tmp_path, monkeypatch
):
    spec = setup(tmp_path)
    monkeypatch.setattr(c, "execute_job", fake_execute)
    calls = []

    def execute(spec, stage, output):
        calls.append(stage)
        if stage == "ranking" and calls.count("ranking") == 1:
            raise RuntimeError("SYNTHETIC_PRIVATE_SENTINEL")
        c.run_stage(spec, stage, output)

    monkeypatch.setattr(c, "supervise_stage", execute)
    with pytest.raises(RuntimeError):
        c.run_batch(spec)
    before = (spec.output / "assembly" / "integrity_manifest.json").read_bytes()
    assert a.read_json(spec.output / "run.json")["failure_reason"] == "RuntimeError"
    c.run_batch(spec)
    assert calls.count("annotation_000001") == calls.count("annotation_000002") == 1
    assert calls.count("ranking") == 2
    assert before == (spec.output / "assembly" / "integrity_manifest.json").read_bytes()
    assert a.read_json(spec.output / "run.json")["ended_at"]
    c.run_batch(spec)
    assert calls.count("ranking") == 2
    with (spec.partitions / "shard_000001" / "subset.vcf").open("a") as stream:
        stream.write("corrupt\n")
    with pytest.raises(ValueError):
        c.run_batch(spec)


def test_authorization_cli_redaction_and_partition_receipt_cannot_hide_mutation(tmp_path):
    spec = setup(tmp_path)
    config = tmp_path / "synthetic-config.json"
    config.write_text(
        spec.model_copy(update={"confirmed_annotation_execution": False}).model_dump_json()
    )
    result = CliRunner().invoke(app, ["coverage-batch", str(config)])
    assert result.exit_code == 1 and "PermissionError" in result.output
    assert not spec.output.exists() and str(tmp_path) not in result.output
    shard = spec.partitions / "shard_000001"
    subset = shard / "subset.vcf"
    subset.write_text(subset.read_text().replace("0/1:90", "1/1:90"))
    receipt = a.read_json(shard / "receipt.json")
    receipt["subset_sha256"] = sha256(subset)
    atomic_json(shard / "receipt.json", receipt)
    with pytest.raises(ValueError, match="source content"):
        a.verify_partition(spec.plan, shard)


def test_real_isolated_validation_worker_and_private_failure_checkpoint(tmp_path, monkeypatch):
    spec = setup(tmp_path)
    monkeypatch.setattr(c, "execute_job", fake_execute)
    spec.output.mkdir()
    annotated = spec.output / "annotation_000001"
    annotated.mkdir()
    c.annotate_shard(spec, 1, annotated)
    output = spec.output / "validation_000001.partial"
    output.mkdir()
    monkeypatch.setenv("SYNTHETIC_SECRET", "must-not-inherit")
    c.supervise_stage(spec, "validation_000001", output)
    assert (output / "proof.json").is_file()
    measurements = list((spec.output / "attempts").glob("*.measurement.json"))
    assert len(measurements) == 1
    assert a.read_json(measurements[0])["status"] == "completed"
    # A fixed-stage dispatcher error must be checkpointed without exception text.
    failed = spec.output / "invalid.partial"
    failed.mkdir()
    with pytest.raises(RuntimeError, match="worker failed"):
        c.supervise_stage(spec, "invalid", failed)
    receipts = [a.read_json(x) for x in (spec.output / "attempts").glob("*.measurement.json")]
    assert sorted(x["status"] for x in receipts) == ["completed", "failed"]
    failures = list((spec.output / "attempts").glob("*.failure.json"))
    assert [a.read_json(x) for x in failures] == [{"type": "ValueError"}]


@pytest.mark.parametrize("failure", ["inspection", "memory", "output", "entry_memory"])
def test_supervisor_stops_worker_and_checkpoints_resource_or_inspection_failure(
    tmp_path, monkeypatch, failure
):
    spec = setup(tmp_path)
    spec.output.mkdir()
    output = spec.output / "assembly.partial"
    output.mkdir()
    killed = []
    seen = []

    class Process:
        pid = 123
        returncode = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def poll(self):
            return self.returncode

        def wait(self):
            self.returncode = -9

    process = Process()

    def launch(command, **kwargs):
        seen.append(command)
        assert kwargs["cwd"] == output
        assert kwargs["env"]["LANGSMITH_TRACING"] == "false"
        assert "SYNTHETIC_SECRET" not in kwargs["env"]
        assert command[:3] == [
            c.sys.executable,
            "-m",
            "rare_disease_agent.workflows.coverage_worker",
        ]
        return process

    def inspect(pid):
        if failure == "inspection":
            raise c.psutil.AccessDenied(pid=pid, name="SYNTHETIC_PRIVATE_SENTINEL")
        return SimpleNamespace(
            children=lambda recursive: [],
            memory_info=lambda: SimpleNamespace(rss=4 * 1024**3 if failure == "memory" else 100),
        )

    monkeypatch.setenv("SYNTHETIC_SECRET", "must-not-inherit")
    monkeypatch.setattr(c.subprocess, "Popen", launch)
    monkeypatch.setattr(c.psutil, "Process", inspect)
    monkeypatch.setattr(c.os, "killpg", lambda pid, sig: killed.append(pid))
    if failure == "output":
        monkeypatch.setattr(c, "output_size", lambda path: 3 * 1024**3)
    if failure == "entry_memory":
        monkeypatch.setattr(c.psutil, "virtual_memory", lambda: SimpleNamespace(available=0))
    with pytest.raises((RuntimeError, c.psutil.AccessDenied)):
        c.supervise_stage(spec, "assembly", output)
    assert killed == ([] if failure == "entry_memory" else [123])
    assert len(seen) == (0 if failure == "entry_memory" else 1)
    (measurement,) = (spec.output / "attempts").glob("*.measurement.json")
    assert a.read_json(measurement)["status"] == "failed"
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in measurement.read_text()


def test_batch_refuses_relative_paths_before_changing_worker_directory(tmp_path):
    spec = setup(tmp_path)
    raw = spec.model_dump()
    raw["partitions"] = Path("synthetic-relative-input")
    with pytest.raises(ValueError, match="absolute local paths"):
        c.CoverageBatchInput.model_validate(raw)


def reused_spec(spec, tmp_path):
    output = tmp_path / "reuse-batch"
    return spec.model_copy(
        update={
            "output": output,
            "ranking": spec.ranking.model_copy(
                update={
                    "output": output / "ranking" / "run",
                    "annotation_rehearsal": output / "assembly",
                }
            ),
            "confirmed_annotation_execution": False,
            "reuse_annotations": {
                n: c.ReusedAnnotation(
                    directory=spec.output / f"annotation_{n:06d}",
                    integrity_sha256=sha256(
                        spec.output / f"annotation_{n:06d}" / "integrity_manifest.json"
                    ),
                )
                for n in spec.shards
            },
        }
    )


def test_import_annotations_without_execution_and_revalidate_current_tables(tmp_path, monkeypatch):
    spec = setup(tmp_path)
    validated(spec, monkeypatch)
    reused = reused_spec(spec, tmp_path)
    originals = {n: a.inventory(item.directory) for n, item in reused.reuse_annotations.items()}
    monkeypatch.setattr(c, "execute_job", lambda *a, **kw: pytest.fail("Unexpected tool execution"))
    monkeypatch.setattr(c, "supervise_stage", c.run_stage)
    c.run_batch(reused)
    journal = a.read_json(reused.output / "run.json")
    assert journal["ended_at"] and "pair_preflight" in journal["stages"]
    for n, before in originals.items():
        assert a.inventory(reused.reuse_annotations[n].directory) == before
        assert a.inventory(reused.output / f"annotation_{n:06d}") == before
        assert (reused.output / f"validation_{n:06d}" / "proof.json").is_file()
    report = a.read_json(reused.output / "pair_preflight" / "pair_workload.json")
    assert report["eligible_candidate_pairs"] == 2
    assert report["within_budget"] and not report["retained_pair_count_assessed"]
    c.run_batch(reused)
    assert a.read_json(reused.output / "run.json") == journal


def test_supervised_reuse_recovers_between_stages_without_rewriting_commits(tmp_path, monkeypatch):
    spec = setup(tmp_path)
    validated(spec, monkeypatch)
    reused = reused_spec(spec, tmp_path)
    assert reused.confirmed_annotation_execution is False
    originals = {n: a.inventory(item.directory) for n, item in reused.reuse_annotations.items()}
    supervise = c.supervise_stage

    def interrupt_before_ranking(spec, stage, output):
        if stage == "ranking":
            raise InterruptedError("Synthetic between-stage interruption")
        return supervise(spec, stage, output)

    monkeypatch.setattr(c, "supervise_stage", interrupt_before_ranking)
    with pytest.raises(InterruptedError, match="Synthetic between-stage interruption"):
        c.run_batch(reused)
    interrupted = a.read_json(reused.output / "run.json")
    assert interrupted["failure_reason"] == "InterruptedError"
    assert interrupted["ended_at"] is None
    assert "pair_preflight" in interrupted["stages"] and "ranking" not in interrupted["stages"]
    committed = {name: a.inventory(reused.output / name) for name in interrupted["stages"]}

    monkeypatch.setattr(c, "supervise_stage", supervise)
    c.run_batch(reused)
    completed = a.read_json(reused.output / "run.json")
    assert completed["ended_at"] and completed["failure_reason"] is None
    assert "ranking" in completed["stages"]
    for name, before in committed.items():
        assert a.inventory(reused.output / name) == before
        assert completed["stages"][name] == interrupted["stages"][name]
    for number, before in originals.items():
        assert a.inventory(reused.reuse_annotations[number].directory) == before
        assert a.inventory(reused.output / f"annotation_{number:06d}") == before
    measurements = [
        a.read_json(path) for path in (reused.output / "attempts").glob("*.measurement.json")
    ]
    assert {item["stage"] for item in measurements} == set(completed["stages"])
    assert len(measurements) == len(completed["stages"])
    assert all(
        item["status"] == "completed" and item["process_returncode"] == 0 for item in measurements
    )

    attempts = a.inventory(reused.output / "attempts")
    c.run_batch(reused)
    assert a.read_json(reused.output / "run.json") == completed
    assert a.inventory(reused.output / "attempts") == attempts


@pytest.mark.parametrize(
    "mutation",
    ["pin", "bytes", "source", "tool", "configuration", "receipt", "extra", "symlink", "overlap"],
)
def test_reuse_rejects_wrong_or_mutated_annotation_before_batch_creation(
    tmp_path, monkeypatch, mutation
):
    spec = setup(tmp_path)
    validated(spec, monkeypatch)
    reused = reused_spec(spec, tmp_path)
    item = reused.reuse_annotations[1]
    source = item.directory
    if mutation == "pin":
        reused.reuse_annotations[1] = item.model_copy(update={"integrity_sha256": "0" * 64})
    elif mutation == "bytes":
        (source / "annotated.vcf").write_text("SYNTHETIC_PRIVATE_SENTINEL")
    elif mutation == "symlink":
        link = tmp_path / "link"
        link.symlink_to(source, target_is_directory=True)
        reused.reuse_annotations[1] = item.model_copy(update={"directory": link})
    elif mutation == "overlap":
        reused = reused.model_copy(update={"output": source / "child"})
    else:
        if mutation == "extra":
            (source / "extra.txt").write_text("synthetic")
        elif mutation == "source":
            path = source / "run_manifest.json"
            data = a.read_json(path)
            data["source_sha256"] = "0" * 64
            atomic_json(path, data)
        else:
            path = source / "annotation.receipt.json"
            data = a.read_json(path)
            if mutation == "tool":
                data["executable_sha256"] = "0" * 64
            elif mutation == "configuration":
                data["configuration"]["buffer_size"] = 2
            else:
                data["input_sha256"] = "0" * 64
            atomic_json(path, data)
        a.seal(source)
        reused.reuse_annotations[1] = item.model_copy(
            update={"integrity_sha256": sha256(source / "integrity_manifest.json")}
        )
    with pytest.raises(ValueError):
        c.batch_identity(reused)
    assert not reused.output.exists()


def test_partial_reuse_requires_authorization_and_only_executes_new_shard(tmp_path, monkeypatch):
    spec = setup(tmp_path)
    validated(spec, monkeypatch)
    reused = reused_spec(spec, tmp_path)
    reused.reuse_annotations.pop(2)
    with pytest.raises(PermissionError):
        c.batch_identity(reused)
    reused = reused.model_copy(update={"confirmed_annotation_execution": True})
    calls = []

    def execute(job, **kwargs):
        calls.append(job.input_path.parent.name)
        return fake_execute(job, **kwargs)

    monkeypatch.setattr(c, "execute_job", execute)
    monkeypatch.setattr(c, "supervise_stage", c.run_stage)
    c.run_batch(reused)
    assert calls == ["annotation_000002.partial"] * 2


def test_over_budget_global_preflight_persists_and_blocks_ranking_on_resume(tmp_path, monkeypatch):
    spec = setup(tmp_path)
    monkeypatch.setattr(c, "execute_job", fake_execute)
    monkeypatch.setattr(c, "supervise_stage", c.run_stage)
    monkeypatch.setattr(
        c, "PAIR_WORK_BUDGET", 1
    )  # Two real cross-shard pairs; small synthetic cap.
    monkeypatch.setattr(c, "phase5_run", lambda *_: pytest.fail("Ranking must not start"))
    for _ in range(2):
        with pytest.raises(ValueError, match="preflight blocks"):
            c.run_batch(spec)
        journal = a.read_json(spec.output / "run.json")
        assert "pair_preflight" in journal["stages"] and "ranking" not in journal["stages"]
        assert journal["ended_at"] is None
        report = a.read_json(spec.output / "pair_preflight" / "pair_workload.json")
        assert report["eligible_candidate_pairs"] == 2 and report["within_budget"] is False
        assert (
            a.verify_assembly(spec.output / "assembly")["coverage"]["included_source_records"] == 2
        )


def test_reuse_worker_does_not_require_annotation_execution_permission(tmp_path, monkeypatch):
    spec = setup(tmp_path)
    validated(spec, monkeypatch)
    reused = reused_spec(spec, tmp_path)
    reused.output.mkdir()
    output = reused.output / "annotation_000001.partial"
    output.mkdir()
    c.supervise_stage(reused, "annotation_000001", output)
    assert a.verified(output) == a.verified(reused.reuse_annotations[1].directory)


def test_reuse_detects_source_change_during_copy(tmp_path, monkeypatch):
    spec = setup(tmp_path)
    validated(spec, monkeypatch)
    reused = reused_spec(spec, tmp_path)
    output = tmp_path / "import"
    output.mkdir()
    copy = shutil.copyfile

    def mutate(source, target):
        copy(source, target)
        if source.name == "annotated.vcf":
            source.write_text("SYNTHETIC_PRIVATE_SENTINEL")

    monkeypatch.setattr(c.shutil, "copyfile", mutate)
    with pytest.raises(ValueError):
        c.import_annotation(reused, 1, output)
