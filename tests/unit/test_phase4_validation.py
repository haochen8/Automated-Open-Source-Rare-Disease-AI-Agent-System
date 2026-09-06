import hashlib
import json
from pathlib import Path

import pytest

from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.synthetic.cases import load_synthetic_case
from rare_disease_agent.tools.variants.adapters import ToolJob, execute_job
from rare_disease_agent.tools.variants.preflight import GenomicInput, validate_parquet
from rare_disease_agent.tools.variants.vcf import parse_vcf


def specification(**updates):
    case = load_synthetic_case("de-novo")
    values = dict(
        genome_build="GRCh38",
        annotation_build="GRCh38",
        normalized_biallelic=True,
        reference_checksum="a" * 64,
        transcript_release="synthetic-1",
        sample_ids=[item.id for item in case.pedigree.individuals],
        pedigree=case.pedigree,
        par_intervals={"X": [], "Y": []},
        allow_missing_annotations=True,
    )
    return GenomicInput(**{**values, **updates})


def rows():
    case = load_synthetic_case("de-novo")
    return list(parse_vcf(Path(str(case.vcf_resource))))


@pytest.mark.parametrize(
    "updates",
    [
        {"annotation_build": "GRCh37"},
        {"genome_build": "unknown"},
        {"normalized_biallelic": False},
        {"sample_ids": ["wrong"]},
        {"par_intervals": {}},
        {"transcript_release": "latest"},
    ],
)
def test_incompatible_genomic_context_fails_closed(updates):
    with pytest.raises(ValueError):
        specification(**updates)


@pytest.mark.parametrize(
    "fault", ["duplicate", "contig", "ploidy", "allele", "sample", "gq", "annotation", "sex"]
)
def test_variant_preflight_rejects_ambiguity(tmp_path, fault):
    records = rows()
    first = records[0]
    spec = specification()
    if fault == "duplicate":
        records.append(first)
    if fault == "contig":
        first.chromosome = "chrUn"
    if fault == "allele":
        first.alternate = "A,C"
    if fault in {"sample", "ploidy", "gq", "sex"}:
        calls = json.loads(first.genotype_calls_json)
        if fault == "sample":
            calls.pop("FATHER")
        if fault == "ploidy":
            calls["PROBAND"]["genotype"] = "1"
        if fault == "gq":
            calls["PROBAND"]["quality"] = None
        if fault == "sex":
            first.chromosome = "X"
            spec.pedigree.individuals[0].sex = "unknown"
        first.genotype_calls_json = json.dumps(calls)
    if fault == "annotation":
        spec.allow_missing_annotations = False
    path = tmp_path / "variants.parquet"
    write_variants_parquet(records, path)
    with pytest.raises(ValueError):
        validate_parquet(path, spec)


def job(tmp_path, provider="bcftools"):
    source = tmp_path / "source.txt"
    source.write_text("synthetic input")
    reference = tmp_path / "ref.txt"
    reference.write_text("synthetic reference")
    cache = tmp_path / "vep-cache"
    cache.mkdir(exist_ok=True)
    return ToolJob(
        provider=provider,
        tool_version="115" if provider == "vep" else "1.22",
        data_version="115" if provider == "vep" else "2026-01",
        genome_build="GRCh38",
        input_path=source,
        output_path=tmp_path / "output.txt",
        reference_path=reference,
        reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        cache_path=cache,
        memory_mb=128,
        output_limit_mb=1,
    )


@pytest.mark.parametrize("provider", ["vep", "bcftools"])
def test_adapter_preview_and_mock_execution(tmp_path, provider):
    value = job(tmp_path, provider)
    command = value.preview()
    assert command[0] == provider
    assert ("--offline" in command) == (provider == "vep")
    with pytest.raises(PermissionError):
        execute_job(value)
    seen = []

    def mock(command, job):
        seen.append(command)
        job.output_path.write_text("synthetic annotated output")
        return 0

    result = execute_job(
        value, authorized=True, runner=mock, observed_tool_version=value.tool_version
    )
    assert len(seen) == 1 and result["output_sha256"]
    assert result["configuration"]["timeout_seconds"] == 300


def test_adapter_version_failure_and_process_failure_cleanup(tmp_path):
    value = job(tmp_path)
    with pytest.raises(ValueError, match="version"):
        execute_job(
            value, authorized=True, observed_tool_version="wrong", runner=lambda command, job: 0
        )

    def fail(command, job):
        job.output_path.write_text("partial")
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        execute_job(value, authorized=True, runner=fail, observed_tool_version=value.tool_version)
    assert not value.output_path.exists()


def test_adapter_supervisor_kills_process_group_on_memory_limit(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from rare_disease_agent.tools.variants import adapters

    value = job(tmp_path)
    killed = []

    class Process:
        pid = 12345

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def poll(self):
            return None

        def wait(self):
            return -9

    monkeypatch.setattr(adapters.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(
        adapters.psutil,
        "Process",
        lambda pid: SimpleNamespace(
            children=lambda recursive: [],
            is_running=lambda: True,
            memory_info=lambda: SimpleNamespace(rss=1024**3),
        ),
    )
    monkeypatch.setattr(adapters.os, "killpg", lambda pid, sig: killed.append(pid))
    with pytest.raises(RuntimeError, match="limit"):
        adapters.bounded_process(value.preview(), value)
    assert killed == [12345]
