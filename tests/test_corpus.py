"""Synthetic corpus integration: complete retention, global work and immutable recovery."""

import hashlib
import json
import math
import os
import random
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pyarrow.parquet as pq
import pytest

from rare_disease_agent.resource_management import atomic_json, sha256
from rare_disease_agent.resource_management.hpo import normalize_hpo
from rare_disease_agent.tools.variants.adapters import ToolJob
from rare_disease_agent.tools.variants.ingest import info_fields
from rare_disease_agent.workflows import assembly as a
from rare_disease_agent.workflows import coverage_batch as batch
from rare_disease_agent.workflows import partitioning as p
from rare_disease_agent.workflows.phase5 import Phase5Input, code_checksum, phase5_run
from tests.unit.test_phase4_resources import normalized_fixture
from tests.unit.test_phase5_ingestion import synthetic_docx

FIELDS = "Allele|Consequence|SYMBOL|Gene|Feature|ALLELE_NUM|gnomADe_AF|gnomADg_AF"
HEADER = (
    "##fileformat=VCFv4.2\n"
    "##synthetic=true\n"
    f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {FIELDS}">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsynthetic\n"
)


@pytest.fixture(autouse=True)
def resources(monkeypatch):
    monkeypatch.setattr(
        p.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(available=4 * 1024**3, total=8 * 1024**3),
    )
    monkeypatch.setattr(p.psutil, "disk_usage", lambda _: SimpleNamespace(free=30 * 1024**3))


def source_row(number, *, multiallelic=False, unknown=False, chromosome="1"):
    alternate = "G,T" if multiallelic else "G"
    genotype = "1|2" if multiallelic else "./." if unknown else "0/1"
    quality = 8 if number == 9 else 90
    return (
        f"{chromosome}\t{100 + number}\t.\tA\t{alternate}\t50\tPASS\tAF=0.8"
        f"\tGT:GQ\t{genotype}:{quality}\n"
    )


def normalized_rows(path, *, dense=False):
    normalized, annotated = [], []
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            continue
        original = line.split("\t")
        number = int(info_fields(original[7])["RDA_SOURCE_ROW"])
        for ordinal, alternate in enumerate(original[4].split(","), 1):
            fields = original.copy()
            fields[4] = alternate
            fields[7] = f"RDA_SOURCE_ROW={number};RDA_ALT_IDX={ordinal};AF=0.8"
            genotype, quality = original[9].split(":")
            separator = "|" if "|" in genotype else "/"
            calls = genotype.split(separator)
            fields[9] = (
                separator.join(
                    "." if call == "." else str(int(int(call) == ordinal)) for call in calls
                )
                + ":"
                + quality
            )
            normalized.append("\t".join(fields) + "\n")
            if number != 8 or dense:
                gene = "SYN_NOVEL" if number == 6 and not dense else "SYN_CAUSAL"
                frequency = "0.001" if number == 2 and not dense else ""
                csq = f"{alternate}|missense_variant|{gene}|{gene}|T{number}a|1|{frequency}|"
                if number == 2 and not dense:
                    csq += f",{alternate}|intron_variant|{gene}|{gene}|T2b|1|{frequency}|"
                    csq += f",{alternate}|synonymous_variant|SYN_NOVEL|SYN_NOVEL|T2c|1|{frequency}|"
                fields[7] += ";CSQ=" + csq
            annotated.append("\t".join(fields) + "\n")
    return normalized, annotated


def annotation_bundle(directory, subset, source, tools, *, dense=False):
    """Write explicit synthetic receipts; no annotation executable is invoked."""
    directory.mkdir()
    (directory / "subset.vcf").write_bytes(subset.read_bytes())
    normalized, annotated = normalized_rows(subset, dense=dense)
    (directory / "normalized.vcf").write_text(HEADER + "".join(normalized))
    (directory / "annotated.vcf").write_text(HEADER + "".join(annotated))
    operations = []
    for name, incoming, outgoing, tool in (
        ("normalization", "subset.vcf", "normalized.vcf", tools[0]),
        ("annotation", "normalized.vcf", "annotated.vcf", tools[1]),
    ):
        job = tool.job.model_copy(
            update={"input_path": directory / incoming, "output_path": directory / outgoing}
        )
        atomic_json(
            directory / (name + ".receipt.json"),
            {
                "configuration": job.model_dump(mode="json"),
                "provider": job.provider,
                "tool_version": job.tool_version,
                "data_version": job.data_version,
                "reference_sha256": job.reference_sha256,
                "executable_sha256": tool.executable_sha256,
                "input_sha256": sha256(directory / incoming),
                "output_sha256": sha256(directory / outgoing),
                "returncode": 0,
            },
        )
        operations.append(job.model_dump(mode="json"))
    atomic_json(
        directory / "run_manifest.json",
        {
            "status": "completed",
            "source_sha256": sha256(source),
            "authorized_scope": {"operations": operations},
            "whole_genome_analysis_complete": False,
        },
    )
    a.seal(directory)


@dataclass
class CorpusFixture:
    root: Path
    plan: Path
    partitions: Path
    annotations: list[Path]
    tools: tuple
    ranking: Phase5Input


def corpus_fixture(tmp_path, *, dense=False):
    source = tmp_path / "synthetic-source.vcf"
    count = 633 if dense else 9
    source.write_text(
        HEADER
        + "".join(
            source_row(n, multiallelic=n == 3 and not dense, unknown=n == 7 and not dense)
            for n in range(1, count + 1)
        )
        + source_row(count + 1, chromosome="X")
    )
    plan = tmp_path / "plan"
    p.prepare_coverage(
        p.CoverageInput(
            source=source,
            source_sha256=sha256(source),
            output=plan,
            records_per_shard=math.ceil(count / 9),
            confirmed_local_research_use=True,
        )
    )
    partitions = tmp_path / "partitions"
    p.materialize_partitions(plan, partitions, first=1, last=9, authorized=True)
    reference = tmp_path / "synthetic-reference.fa"
    reference.write_text("synthetic reference; never used by an external tool\n")
    cache = tmp_path / "annotation-cache"
    cache.mkdir()
    tools = []
    for provider, version, data in (("bcftools", "1.22", "1"), ("vep", "116.0", "116")):
        executable = tmp_path / provider
        executable.write_text("synthetic placeholder; never executed\n")
        executable.chmod(0o700)
        tools.append(
            batch.PinnedTool(
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
    annotations = []
    for n in range(1, 10):
        directory = tmp_path / f"annotation-{n}"
        annotation_bundle(
            directory, partitions / f"shard_{n:06d}" / "subset.vcf", source, tools, dense=dense
        )
        annotations.append(directory)
    resource_dir = tmp_path / "phenotype-resources"
    resource_dir.mkdir()
    manager, locks = normalized_fixture(resource_dir)
    hpo = tmp_path / "hpo"
    normalize_hpo(manager, locks, hpo)
    document = tmp_path / "synthetic.docx"
    synthetic_docx(document)
    source_index = tmp_path / "synthetic.index"
    source_index.write_text("synthetic index; source pairing is separately tested\n")
    output = tmp_path / "corpus-run"
    ranking = Phase5Input(
        original_vcf=source,
        original_index=source_index,
        phenotype_docx=document,
        annotation_rehearsal=output / "assembly",
        hpo_directory=hpo,
        output=output / "ranking" / "run",
        confirmed_local_research_use=True,
        confirmed_affected_sample=False,
        run_id="synthetic-corpus-equivalence",
    )
    return CorpusFixture(tmp_path, plan, partitions, annotations, tuple(tools), ranking)


def reference_spec(fixture, *, selected=None):
    from rare_disease_agent.workflows.corpus import CorpusInput

    selected = list(range(1, 10)) if selected is None else selected
    references = [
        {
            "shard": n,
            "directory": str(fixture.annotations[n - 1]),
            "integrity_sha256": sha256(fixture.annotations[n - 1] / "integrity_manifest.json"),
        }
        for n in selected
    ]
    random.Random(17).shuffle(references)
    manifest = fixture.root / "references.jsonl"
    manifest.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in references))
    return CorpusInput(
        plan=fixture.plan,
        partitions=fixture.partitions,
        reference_manifest=manifest,
        reference_manifest_sha256=sha256(manifest),
        normalization=fixture.tools[0],
        annotation=fixture.tools[1],
        ranking=fixture.ranking,
        output=fixture.root / "corpus-run",
        confirmed_local_research_use=True,
    )


def inline_stages(monkeypatch):
    """Exercise real scientific stages; supervisor boundaries have separate tests."""
    from rare_disease_agent.workflows import corpus

    monkeypatch.setattr(corpus, "supervise_stage", corpus.run_stage)
    return corpus


def control_ranking(fixture):
    subset = fixture.root / "control-subset.vcf"
    subset.write_text(
        (fixture.plan / "header.txt").read_text()
        + "".join(
            line + "\n"
            for annotation in fixture.annotations
            for line in (annotation / "subset.vcf").read_text().splitlines()
            if not line.startswith("#")
        )
    )
    annotation = fixture.root / "control-annotation"
    annotation_bundle(annotation, subset, fixture.ranking.original_vcf, fixture.tools)
    spec = fixture.ranking.model_copy(
        update={"annotation_rehearsal": annotation, "output": fixture.root / "control-run"}
    )
    phase5_run(spec)
    return spec.output


QUERIES = {
    "variants": "SELECT * FROM variants ORDER BY _row_id",
    "branches": "SELECT * FROM candidate_branches ORDER BY branch",
    "membership": "SELECT * FROM candidate_membership ORDER BY branch,variant_id",
    "evidence": "SELECT * FROM evidence ORDER BY variant_id,gene,method,data_version",
    "pairs": "SELECT * FROM compound_pairs ORDER BY variant_a,variant_b",
    "ranking": "SELECT * FROM ranked_evidence ORDER BY mode,rank",
}


def scientific_rows(run):
    database = run / "analysis/workflow/pipeline/candidate_membership.duckdb"
    with duckdb.connect(str(database), read_only=True) as connection:
        return {name: connection.execute(sql).fetchall() for name, sql in QUERIES.items()}


def test_nine_shard_corpus_matches_complete_unpartitioned_analysis(tmp_path, monkeypatch):
    corpus = inline_stages(monkeypatch)
    fixture = corpus_fixture(tmp_path)
    spec = reference_spec(fixture)
    corpus.run_corpus(spec)
    control = control_ranking(fixture)
    for table in a.TABLES:
        actual = pq.read_table(spec.output / "assembly/tables" / f"{table}.parquet").to_pylist()
        expected = pq.read_table(control / "ingest/tables" / f"{table}.parquet").to_pylist()
        assert actual == expected
    actual, expected = scientific_rows(spec.ranking.output), scientific_rows(control)
    assert actual == expected
    assert len({row[3] for row in actual["ranking"]}) == 28
    assert any("r1_a1" in row[1] and "r9_a1" in row[2] for row in actual["pairs"])
    for filename in (
        "private_candidate_ranking.parquet",
        "private_candidate_features.parquet",
        "private_branch_membership.parquet",
    ):
        actual_export = pq.read_table(spec.ranking.output / "deliverables" / filename).to_pylist()
        expected_export = pq.read_table(control / "deliverables" / filename).to_pylist()
        if filename != "private_candidate_ranking.parquet":
            # Existing evidence/membership exports use SELECT *
            # without ORDER BY. Compare their full rows and multiplicity;
            # ranking and all four input tables retain exact order assertions.
            actual_export = sorted(json.dumps(row, sort_keys=True) for row in actual_export)
            expected_export = sorted(json.dumps(row, sort_keys=True) for row in expected_export)
        assert actual_export == expected_export
    variants = pq.read_table(spec.output / "assembly/tables/variants.parquet").to_pylist()
    assert any(row["genotype"] == "./." for row in variants)
    assert any(row["gene"] == "UNKNOWN" for row in variants)
    assert any(row["allele_frequency"] is None for row in variants)
    assert all(row["allele_frequency"] != 0.8 for row in variants)
    alleles = pq.read_table(spec.output / "assembly/tables/alleles.parquet").to_pylist()
    assert {row["allele_id"] for row in alleles if row["source_row"] == "3"} == {"r3_a1", "r3_a2"}
    metrics = a.read_json(spec.ranking.output / "deliverables/sanitized_metrics.json")
    assert metrics["branch_counts"]["conservative"] == metrics["gene_level_candidates"]
    assert metrics["branch_counts"]["ensemble"] == metrics["gene_level_candidates"]
    assert metrics["branch_counts"]["novel-gene-rescue"] > 0
    assert metrics["whole_genome_analysis_complete"] is False
    assert metrics["causal_accuracy_assessed"] is False
    assert metrics["sample_phenotype_linkage_confirmed"] is False
    assert metrics["coverage"]["included_source_records"] == 9
    assert metrics["coverage"]["included_alternate_alleles"] == 10
    assert metrics["coverage"]["unprocessed_source_records"] == 1


def test_pair_budget_refusal_preserves_committed_union_and_work_report(tmp_path, monkeypatch):
    corpus = inline_stages(monkeypatch)
    fixture = corpus_fixture(tmp_path, dense=True)
    spec = reference_spec(fixture)
    with pytest.raises(corpus.CorpusPairBudgetExceeded):
        corpus.run_corpus(spec)
    assembly = spec.output / "assembly"
    before = sha256(assembly / "integrity_manifest.json")
    pair = a.read_json(spec.output / "pair_preflight/pair_workload.json")
    assert pair["pair_work_budget"] == 200_000
    assert pair["eligible_candidate_pairs"] == 200_028 and pair["within_budget"] is False
    journal = a.read_json(spec.output / "run.json")
    assert {"assembly", "pair_preflight"} <= journal["stages"].keys()
    assert "ranking" not in journal["stages"] and journal["ended_at"] is None
    assert not spec.ranking.output.exists()
    with pytest.raises(corpus.CorpusPairBudgetExceeded):
        corpus.run_corpus(spec)
    assert sha256(assembly / "integrity_manifest.json") == before
    assert not list(spec.output.rglob("candidate_membership.duckdb"))


def update_plan(fixture, segments):
    path = fixture.plan / "segments.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in segments))
    manifest = a.read_json(fixture.plan / "manifest.json")
    manifest["outputs"]["segments.jsonl"] = sha256(path)
    atomic_json(fixture.plan / "manifest.json", manifest)
    rebind_materialization(fixture)


def rebind_materialization(fixture):
    # Rebind the legitimate materialization journal too: these cases exercise
    # independent source-ledger validation, not an earlier stale-hash refusal.
    journal = a.read_json(fixture.partitions / "run.json")
    journal["configuration"]["manifest_sha256"] = sha256(fixture.plan / "manifest.json")
    journal["configuration_hash"] = hashlib.sha256(
        json.dumps(journal["configuration"], sort_keys=True).encode()
    ).hexdigest()
    atomic_json(fixture.partitions / "run.json", journal)


def test_self_consistent_overlap_is_globally_rejected(tmp_path, monkeypatch):
    import shutil

    corpus = inline_stages(monkeypatch)
    fixture = corpus_fixture(tmp_path)
    segments = [
        json.loads(line) for line in (fixture.plan / "segments.jsonl").read_text().splitlines()
    ]
    segments[1] = dict(segments[0], shard=2)
    update_plan(fixture, segments)
    first = fixture.partitions / "shard_000001"
    second = fixture.partitions / "shard_000002"
    (second / "subset.vcf").write_bytes((first / "subset.vcf").read_bytes())
    receipt = a.read_json(first / "receipt.json")
    atomic_json(second / "receipt.json", dict(receipt, shard=2))
    for path in fixture.annotations[0].iterdir():
        shutil.copyfile(path, fixture.annotations[1] / path.name)
    journal = a.read_json(fixture.partitions / "run.json")
    journal["stages"]["shard_000002"]["outputs"] = a.inventory(second)
    atomic_json(fixture.partitions / "run.json", journal)
    # Each reference is independently consistent with its resealed plan entry;
    # only the global interval/source accounting exposes the overlap.
    assert a.verify_partition(fixture.plan, first)[1]["first_source_row"] == 1
    assert a.verify_partition(fixture.plan, second)[1]["first_source_row"] == 1
    spec = reference_spec(fixture)
    with pytest.raises(ValueError, match="intervals do not reconcile"):
        corpus.run_corpus(spec)
    assert not (spec.output / "assembly").exists()


def test_source_ledger_keeps_unprocessed_and_deferred_records_distinct(tmp_path, monkeypatch):
    corpus = inline_stages(monkeypatch)
    fixture = corpus_fixture(tmp_path)
    spec = reference_spec(fixture, selected=list(range(1, 9)))
    corpus.run_corpus(spec)
    coverage = a.read_json(spec.output / "assembly/run_manifest.json")["coverage"]
    assert coverage["included_source_records"] == 8
    assert coverage["unprocessed_source_records"] == 2
    assert coverage["source_dispositions"]["eligible_autosomal_sequence"]["source_records"] == 9
    assert sum(v["source_records"] for v in coverage["source_dispositions"].values()) == 10
    assert coverage["whole_genome_analysis_complete"] is False


def test_missing_deferred_ledger_entry_fails_even_with_new_manifest_hash(tmp_path, monkeypatch):
    corpus = inline_stages(monkeypatch)
    fixture = corpus_fixture(tmp_path)
    segments = [
        json.loads(line) for line in (fixture.plan / "segments.jsonl").read_text().splitlines()
    ]
    assert segments[-1]["shard"] is None
    update_plan(fixture, segments[:-1])
    with pytest.raises(ValueError, match="full source ledger"):
        corpus.run_corpus(reference_spec(fixture))
    assert not (fixture.root / "corpus-run/assembly").exists()


def test_source_allele_total_must_reconcile_with_complete_ledger(tmp_path, monkeypatch):
    corpus = inline_stages(monkeypatch)
    fixture = corpus_fixture(tmp_path)
    manifest = a.read_json(fixture.plan / "manifest.json")
    manifest["alternate_alleles"] += 1
    atomic_json(fixture.plan / "manifest.json", manifest)
    rebind_materialization(fixture)
    spec = reference_spec(fixture)
    with pytest.raises(ValueError, match="full source ledger"):
        corpus.run_corpus(spec)
    assert not (spec.output / "assembly").exists()
    assert not spec.ranking.output.exists()


@pytest.mark.parametrize("bound", ["line", "manifest", "references"])
def test_manifest_admission_bounds_refuse_without_assembly(tmp_path, monkeypatch, bound):
    corpus = inline_stages(monkeypatch)
    fixture = corpus_fixture(tmp_path)
    spec = reference_spec(fixture)
    assert corpus.MAX_REFERENCES == 100_000
    assert corpus.MAX_MANIFEST_BYTES == 64 * 1024**2
    assert corpus.MAX_LINE_BYTES == 64 * 1024
    if bound == "line":
        spec.reference_manifest.write_text(" " * (corpus.MAX_LINE_BYTES + 1) + "\n")
        spec = spec.model_copy(
            update={"reference_manifest_sha256": sha256(spec.reference_manifest)}
        )
    elif bound == "manifest":
        monkeypatch.setattr(
            corpus, "MAX_MANIFEST_BYTES", spec.reference_manifest.stat().st_size - 1
        )
    else:
        monkeypatch.setattr(corpus, "MAX_REFERENCES", 8)
    with pytest.raises((ValueError, RuntimeError)):
        corpus.run_corpus(spec)
    assert not (spec.output / "assembly").exists()


def test_reference_bundle_bytes_count_toward_combined_storage(tmp_path, monkeypatch):
    corpus = inline_stages(monkeypatch)
    fixture = corpus_fixture(tmp_path)
    spec = reference_spec(fixture)
    referenced = sum(
        path.stat().st_size
        for directory in fixture.annotations
        for path in directory.rglob("*")
        if path.is_file()
    )
    assert corpus.MAX_OUTPUT_BYTES == 2 * 1024**3 and referenced > 0
    monkeypatch.setattr(corpus, "MAX_OUTPUT_BYTES", referenced - 1)
    with pytest.raises(RuntimeError):
        corpus.run_corpus(spec)
    assert not (spec.output / "assembly").exists()


def test_reference_mutation_before_union_prevents_publication(tmp_path, monkeypatch):
    from rare_disease_agent.workflows import corpus

    fixture = corpus_fixture(tmp_path)
    spec = reference_spec(fixture)

    def mutate_after_validation(spec, stage, output):
        corpus.run_stage(spec, stage, output)
        if stage == "validation":
            with (fixture.annotations[-1] / "annotated.vcf").open("a") as stream:
                stream.write("##synthetic-mutation=true\n")

    monkeypatch.setattr(corpus, "supervise_stage", mutate_after_validation)
    with pytest.raises(ValueError):
        corpus.run_corpus(spec)
    assert not (spec.output / "assembly").exists()
    assert not spec.ranking.output.exists()


def test_interruption_resume_preserves_committed_corpus_and_rejects_changed_manifest(
    tmp_path, monkeypatch
):
    from rare_disease_agent.workflows import corpus

    fixture = corpus_fixture(tmp_path)
    spec = reference_spec(fixture)
    calls = []

    def interrupt_once(spec, stage, output):
        calls.append(stage)
        if stage == "ranking" and calls.count(stage) == 1:
            raise InterruptedError("SYNTHETIC_PRIVATE_SENTINEL")
        corpus.run_stage(spec, stage, output)

    monkeypatch.setattr(corpus, "supervise_stage", interrupt_once)
    with pytest.raises(InterruptedError):
        corpus.run_corpus(spec)
    assembly = spec.output / "assembly/integrity_manifest.json"
    before = (sha256(assembly), assembly.stat().st_mtime_ns)
    assert a.read_json(spec.output / "run.json")["failure_reason"] == "InterruptedError"
    corpus.run_corpus(spec)
    assert before == (sha256(assembly), assembly.stat().st_mtime_ns)
    assert (
        calls.count("validation") == calls.count("assembly") == calls.count("pair_preflight") == 1
    )
    assert calls.count("ranking") == 2
    corpus.run_corpus(spec)
    assert calls.count("ranking") == 2
    with spec.reference_manifest.open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError):
        corpus.run_corpus(spec)
    assert before == (sha256(assembly), assembly.stat().st_mtime_ns)


def test_existing_foreign_output_and_symlink_reference_are_not_adopted(tmp_path, monkeypatch):
    corpus = inline_stages(monkeypatch)
    fixture = corpus_fixture(tmp_path)
    spec = reference_spec(fixture)
    spec.output.mkdir()
    sentinel = spec.output / "foreign.txt"
    sentinel.write_text("SYNTHETIC_FOREIGN_CONTENT")
    with pytest.raises(ValueError):
        corpus.run_corpus(spec)
    assert sentinel.read_text() == "SYNTHETIC_FOREIGN_CONTENT"
    assert not (spec.output / "assembly").exists()
    sentinel.unlink()
    for path in spec.output.iterdir():
        path.unlink()
    spec.output.rmdir()
    alias = fixture.root / "annotation-alias"
    alias.symlink_to(fixture.annotations[-1], target_is_directory=True)
    values = [json.loads(line) for line in spec.reference_manifest.read_text().splitlines()]
    next(item for item in values if item["shard"] == 9)["directory"] = str(alias)
    spec.reference_manifest.write_text("".join(json.dumps(item) + "\n" for item in values))
    spec = spec.model_copy(update={"reference_manifest_sha256": sha256(spec.reference_manifest)})
    with pytest.raises(ValueError):
        corpus.run_corpus(spec)
    assert not (spec.output / "assembly").exists()


def test_journal_limit_refuses_stage_before_publication(tmp_path, monkeypatch):
    from rare_disease_agent.workflows import corpus

    spec = reference_spec(corpus_fixture(tmp_path))
    assert corpus.MAX_ROOT_BYTES == 4 * 1024**2

    def bounded_validation(spec, stage, output):
        if stage == "validation":
            used = (spec.output / "run.json").stat().st_size
            monkeypatch.setattr(corpus, "MAX_ROOT_BYTES", used + 16 * 1024 + 10)
        corpus.run_stage(spec, stage, output)

    monkeypatch.setattr(corpus, "supervise_stage", bounded_validation)
    with pytest.raises(RuntimeError, match="journal metadata budget"):
        corpus.run_corpus(spec)
    journal = a.read_json(spec.output / "run.json")
    assert set(journal["stages"]) == {"index"}
    assert not (spec.output / "validation").exists()
    assert not (spec.output / "assembly").exists()


def test_resume_rejects_changed_phenotype_resource_without_rewriting_corpus(tmp_path, monkeypatch):
    from rare_disease_agent.workflows import corpus

    spec = reference_spec(corpus_fixture(tmp_path))

    def interrupt_ranking(spec, stage, output):
        if stage == "ranking":
            raise InterruptedError("deliberately synthetic interruption")
        corpus.run_stage(spec, stage, output)

    monkeypatch.setattr(corpus, "supervise_stage", interrupt_ranking)
    with pytest.raises(InterruptedError):
        corpus.run_corpus(spec)
    committed = spec.output / "assembly/integrity_manifest.json"
    before = (sha256(committed), committed.stat().st_mtime_ns)
    resource = spec.ranking.hpo_directory / "manifest.json"
    manifest = a.read_json(resource)
    manifest["synthetic_mutation"] = True
    atomic_json(resource, manifest)
    with pytest.raises(ValueError, match="configuration/resource identity mismatch"):
        corpus.run_corpus(spec)
    assert before == (sha256(committed), committed.stat().st_mtime_ns)
    assert not spec.ranking.output.exists()


@pytest.mark.parametrize(
    "failure", ["child_memory", "inspection", "retained_storage", "entry_memory"]
)
def test_supervisor_enforces_process_family_and_combined_storage(tmp_path, monkeypatch, failure):
    from rare_disease_agent.workflows import corpus

    fixture = corpus_fixture(tmp_path)
    spec = reference_spec(fixture)
    spec.output.mkdir()
    output = spec.output / "assembly.partial"
    output.mkdir()
    killed, launched = [], []
    referenced = corpus.reference_storage_bytes(spec)
    assert all(not path.is_relative_to(spec.output) for path in fixture.annotations)

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
    real_process = corpus.psutil.Process

    def launch(command, **kwargs):
        launched.append(command)
        assert command[:3] == [sys.executable, "-m", "rare_disease_agent.workflows.corpus_worker"]
        assert kwargs["cwd"] == output and kwargs["start_new_session"] is True
        assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        environment = kwargs["env"]
        assert set(environment) == {
            "PATH",
            "PYTHONPATH",
            "LANG",
            "LANGSMITH_TRACING",
            "LANGCHAIN_TRACING_V2",
        }
        assert environment["LANGSMITH_TRACING"] == environment["LANGCHAIN_TRACING_V2"] == "false"
        assert "SYNTHETIC_SECRET" not in environment
        if failure == "retained_storage":
            # References alone fit. Worker output alone fits. Their total does not.
            monkeypatch.setattr(corpus, "MAX_OUTPUT_BYTES", referenced + 32 * 1024)
            (output / "synthetic-worker-output").write_bytes(b"x" * (40 * 1024))
        return process

    def inspect(pid):
        if pid == os.getpid():
            return real_process(pid)
        if failure == "inspection":
            raise corpus.psutil.AccessDenied(pid=pid, name="SYNTHETIC_PRIVATE_SENTINEL")
        child = SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=2 * 1024**3))
        return SimpleNamespace(
            children=lambda recursive: [child] if failure == "child_memory" else [],
            memory_info=lambda: SimpleNamespace(
                rss=2 * 1024**3 if failure == "child_memory" else 100
            ),
        )

    monkeypatch.setenv("SYNTHETIC_SECRET", "must-not-inherit")
    monkeypatch.setattr(corpus.subprocess, "Popen", launch)
    monkeypatch.setattr(corpus.psutil, "Process", inspect)
    monkeypatch.setattr(corpus.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    if failure == "entry_memory":
        monkeypatch.setattr(corpus.psutil, "virtual_memory", lambda: SimpleNamespace(available=0))
    with pytest.raises((RuntimeError, corpus.psutil.AccessDenied)):
        corpus.supervise_stage(spec, "assembly", output)
    assert killed == ([] if failure == "entry_memory" else [(123, signal.SIGKILL)])
    assert len(launched) == (0 if failure == "entry_memory" else 1)
    if failure == "entry_memory":
        assert not (spec.output / "attempts").exists()
    else:
        (measurement,) = (spec.output / "attempts").glob("*.measurement.json")
        record = a.read_json(measurement)
        assert record["status"] == "failed" and record["referenced_bundle_bytes"] == referenced
        assert "SYNTHETIC_PRIVATE_SENTINEL" not in measurement.read_text()


@pytest.mark.parametrize("failure", ["code_identity", "unknown_stage", "output_identity"])
def test_real_worker_rejects_untrusted_dispatch_without_raw_output(tmp_path, failure):
    from rare_disease_agent.workflows import corpus
    from rare_disease_agent.workflows.recovery import RestartableRun

    spec = reference_spec(corpus_fixture(tmp_path))
    spec.output.mkdir()
    if failure == "unknown_stage":
        # Supply a legitimate committed index so dispatch is the first failing
        # contract, rather than an earlier missing-input integrity check.
        runner = RestartableRun(
            spec.output, run_id=spec.ranking.run_id, configuration=corpus.corpus_identity(spec)
        )
        runner.stage("index", lambda output: corpus.run_stage(spec, "index", output))
    stage = "SYNTHETIC_PRIVATE_SENTINEL" if failure == "unknown_stage" else "index"
    output = spec.output / (stage + ".partial")
    output.mkdir()
    request = spec.output / "synthetic.request.json"
    atomic_json(
        request,
        {
            "specification": spec.model_dump(mode="json"),
            "stage": stage,
            "output": str(output if failure != "output_identity" else spec.output / "elsewhere"),
            "code_sha256": "0" * 64 if failure == "code_identity" else code_checksum(),
        },
    )
    completed = subprocess.run(
        [sys.executable, "-m", "rare_disease_agent.workflows.corpus_worker", str(request)],
        env={
            "PATH": os.defpath,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        },
        cwd=output,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 1
    assert completed.stdout == completed.stderr == b""
    assert a.read_json(request.with_suffix(".failure.json")) == {"type": "ValueError"}
    assert not list(output.iterdir())
    assert (spec.output / "index").exists() == (failure == "unknown_stage")


def test_coordinator_guard_interrupts_admission_and_restores_signal_handler(tmp_path, monkeypatch):
    from rare_disease_agent.workflows import corpus

    spec = reference_spec(corpus_fixture(tmp_path))
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    assert previous_timer == (0.0, 0.0)

    def expensive_admission(*args, **kwargs):
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0
        # Admission already started below the boundary; cross it while the
        # synchronous operation is still running to exercise periodic checking.
        monkeypatch.setattr(
            corpus.psutil,
            "Process",
            lambda _: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=4 * 1024**3)),
        )
        time.sleep(0.6)
        pytest.fail("Coordinator did not interrupt the bounded synthetic admission")

    monkeypatch.setattr(corpus, "corpus_identity", expensive_admission)
    with pytest.raises(RuntimeError, match="coordinator resource boundary"):
        corpus.run_corpus(spec)
    assert signal.getsignal(signal.SIGALRM) == previous_handler
    assert signal.getitimer(signal.ITIMER_REAL) == previous_timer
    assert not spec.output.exists()


def test_live_storage_retries_publication_rename_without_undercounting(tmp_path, monkeypatch):
    from rare_disease_agent.workflows import corpus

    spec = SimpleNamespace(output=tmp_path / "owned-output")
    partial = spec.output / "assembly.partial"
    partial.mkdir(parents=True)
    (partial / "payload").write_bytes(b"synthetic")
    original = corpus._files
    calls = []

    def publish_during_walk(directory):
        calls.append(directory)
        if len(calls) == 1:
            paths = list(original(directory))
            partial.rename(spec.output / "assembly")
            yield from paths
        else:
            yield from original(directory)

    monkeypatch.setattr(corpus, "_files", publish_during_walk)
    assert corpus.corpus_storage_bytes(spec, referenced_bytes=123, live=True) == 132
    assert len(calls) == 2

    def repeated_churn(directory):
        raise FileNotFoundError("deliberately synthetic concurrent rename")

    monkeypatch.setattr(corpus, "_files", repeated_churn)
    with pytest.raises(FileNotFoundError):
        corpus.corpus_storage_bytes(spec, referenced_bytes=123, live=True)
    with pytest.raises(FileNotFoundError):
        corpus.corpus_storage_bytes(spec, referenced_bytes=123)


def test_sealed_annotation_with_untracked_directory_is_rejected(tmp_path):
    from rare_disease_agent.workflows import corpus

    fixture = corpus_fixture(tmp_path)
    spec = reference_spec(fixture)
    ref = next(ref for ref in corpus.reference_records(spec) if ref.shard == 1)
    before = a.verified(ref.directory)
    (ref.directory / "unexpected-empty-directory").mkdir()
    assert a.verified(ref.directory) == before  # Ordinary file hashes alone cannot expose it.
    prepared = (
        a.plan_identity(fixture.plan),
        json.loads((fixture.plan / "segments.jsonl").read_text().splitlines()[0]),
        (fixture.plan / "header.txt").read_text(),
    )
    with pytest.raises(ValueError, match="Unsupported corpus annotation inventory"):
        corpus._verify_annotation(spec, ref, prepared)


def test_pretty_json_budget_refuses_before_touching_publication(tmp_path, monkeypatch):
    from rare_disease_agent.workflows import corpus

    value = {str(n): {"synthetic": True, "nested": [0, 1, 2]} for n in range(5)}
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    assert len(json.dumps(value).encode()) < len(encoded) - 1
    monkeypatch.setattr(corpus, "MAX_ROOT_BYTES", len(encoded) - 1)
    fresh = tmp_path / "fresh.json"
    with pytest.raises(ValueError):
        corpus.bounded_json(fresh, value)
    assert not fresh.exists() and not fresh.with_suffix(".json.partial").exists()
    existing = tmp_path / "existing.json"
    existing.write_text("SYNTHETIC_EXISTING_CONTENT")
    with pytest.raises(ValueError):
        corpus.bounded_json(existing, value)
    assert existing.read_text() == "SYNTHETIC_EXISTING_CONTENT"
    monkeypatch.setattr(corpus, "MAX_ROOT_BYTES", len(encoded))
    corpus.bounded_json(fresh, value)
    assert fresh.read_bytes() == encoded
