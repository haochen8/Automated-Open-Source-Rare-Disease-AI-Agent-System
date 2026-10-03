import json
from pathlib import Path

import pytest

from rare_disease_agent.resource_management import sha256
from rare_disease_agent.tools.variants.ingest import info_fields, vcf_rows
from rare_disease_agent.workflows import benchmark
from rare_disease_agent.workflows.benchmark import select_autosomal_benchmark

HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsynthetic\n"


def row(chromosome, position, alternate="C"):
    return f"{chromosome}\t{position}\t.\tA\t{alternate}\t50\tPASS\t.\tGT:GQ\t0/1:90\n"


def test_selection_spreads_across_contigs_and_preserves_global_provenance(tmp_path):
    source = tmp_path / "original.vcf"
    original = (
        HEADER + row("X", 1) + "".join(row("1", i, "C,G") for i in range(1, 11)) + row("2", 1)
    )
    source.write_text(original)
    output = tmp_path / "benchmark"
    metrics = select_autosomal_benchmark(source, output, per_autosome=2)
    selected = [x.split("\t") for x in vcf_rows(output / "subset.vcf") if not x.startswith("#")]
    assert [int(info_fields(x[7])["RDA_SOURCE_ROW"]) for x in selected] == [4, 9, 12]
    assert info_fields(selected[0][7])["RDA_ALT_IDX"] == "1,2"
    assert [x[9] for x in selected] == ["0/1:90"] * 3
    assert metrics["source_records"] == 12
    assert metrics["selected_records"] == 3
    assert metrics["unselected_eligible_records"] == 8
    assert metrics["deferred_nonstandard_or_nonsequence_records"] == 1
    assert metrics["selected_multiallelic_records"] == 2
    assert json.loads((output / "selection.receipt.json").read_text()) == metrics
    assert metrics["subset_sha256"] == sha256(output / "subset.vcf")
    assert output.stat().st_mode & 0o777 == 0o700
    assert source.read_text() == original
    second = tmp_path / "repeat"
    assert select_autosomal_benchmark(source, second, per_autosome=2) == metrics
    assert (second / "subset.vcf").read_bytes() == (output / "subset.vcf").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        select_autosomal_benchmark(source, output, per_autosome=2)


@pytest.mark.parametrize(
    "records",
    [row("1", 1) + row("chr1", 2), row("1", 1).replace("\t.\tGT", "\tRDA_SOURCE_ROW=9\tGT")],
)
def test_selection_refuses_ambiguous_provenance_before_writing(tmp_path, records):
    source = tmp_path / "original.vcf"
    source.write_text(HEADER + records)
    output = tmp_path / "benchmark"
    with pytest.raises(ValueError):
        select_autosomal_benchmark(source, output)
    assert not output.exists()


def test_symbolic_records_stay_accounted_and_budget_is_bounded(tmp_path):
    source = tmp_path / "original.vcf"
    source.write_text(HEADER + row("1", 1, "<DEL>") + row("1", 2))
    with pytest.raises(ValueError, match="budget"):
        select_autosomal_benchmark(source, tmp_path / "oversize", per_autosome=501)
    metrics = select_autosomal_benchmark(source, tmp_path / "sample")
    assert (
        metrics["selected_records"] == metrics["deferred_nonstandard_or_nonsequence_records"] == 1
    )


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        ("second_scan", KeyboardInterrupt),
        ("source_check", ValueError),
        ("subset_hash", OSError),
        ("receipt", OSError),
        ("publication", OSError),
    ],
)
def test_failed_selection_leaves_no_final_output_and_can_retry(
    tmp_path, monkeypatch, failure, error
):
    source = tmp_path / "original.vcf"
    source.write_text(HEADER + row("1", 1) + row("1", 2))
    original = source.read_bytes()
    output = tmp_path / "benchmark"
    unrelated = tmp_path / ".benchmark-unrelated"
    unrelated.mkdir()
    marker = unrelated / "keep"
    marker.write_bytes(b"unrelated")
    before = set(tmp_path.iterdir())
    real_rename = Path.rename
    scans = hashes = 0

    def scan(path):
        nonlocal scans
        scans += 1
        for line in vcf_rows(path):
            yield line
            if failure == "second_scan" and scans == 2 and not line.startswith("#"):
                raise KeyboardInterrupt

    def checksum(path):
        nonlocal hashes
        if path == source:
            hashes += 1
            if failure == "source_check" and hashes == 2:
                return "0" * 64
        elif failure == "subset_hash":
            raise OSError("synthetic checksum failure")
        return sha256(path)

    def receipt(path, metrics):
        path.with_suffix(".json.partial").write_text("{")
        raise OSError("synthetic receipt failure")

    def publish(path, target):
        if target == output:
            assert not output.exists()
            assert path.parent == output.parent
            assert path.stat().st_mode & 0o777 == 0o700
            assert {entry.name for entry in path.iterdir()} == {
                "subset.vcf",
                "selection.receipt.json",
            }
            metrics = json.loads((path / "selection.receipt.json").read_text())
            assert metrics["subset_sha256"] == sha256(path / "subset.vcf")
            raise OSError("synthetic publication failure")
        return real_rename(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(benchmark, "vcf_rows", scan)
        patch.setattr(benchmark, "sha256", checksum)
        if failure == "receipt":
            patch.setattr(benchmark, "atomic_json", receipt)
        if failure == "publication":
            patch.setattr(Path, "rename", publish)
        with pytest.raises(error):
            select_autosomal_benchmark(source, output)
    assert not output.exists()
    assert set(tmp_path.iterdir()) == before
    assert marker.read_bytes() == b"unrelated"
    assert source.read_bytes() == original
    metrics = select_autosomal_benchmark(source, output)
    assert metrics["selected_records"] == 2
    assert json.loads((output / "selection.receipt.json").read_text()) == metrics
    assert metrics["subset_sha256"] == sha256(output / "subset.vcf")
    assert set(tmp_path.iterdir()) == before | {output}


def test_interruption_after_publication_preserves_complete_output(tmp_path, monkeypatch):
    source = tmp_path / "original.vcf"
    source.write_text(HEADER + row("1", 1))
    output = tmp_path / "benchmark"
    real_rename = Path.rename
    interruption = KeyboardInterrupt()

    def publish(path, target):
        result = real_rename(path, target)
        if target == output:
            raise interruption
        return result

    monkeypatch.setattr(Path, "rename", publish)
    with pytest.raises(KeyboardInterrupt) as caught:
        select_autosomal_benchmark(source, output)
    assert caught.value is interruption
    assert set(tmp_path.iterdir()) == {source, output}
    assert {entry.name for entry in output.iterdir()} == {"subset.vcf", "selection.receipt.json"}
    metrics = json.loads((output / "selection.receipt.json").read_text())
    assert metrics["subset_sha256"] == sha256(output / "subset.vcf")
    assert metrics["source_sha256"] == sha256(source)
    assert metrics["selected_records"] == 1


@pytest.mark.parametrize("kind", ["file", "directory", "empty_directory", "symlink", "dangling"])
@pytest.mark.parametrize("timing", ["before", "receipt"])
def test_existing_destination_is_preserved(tmp_path, monkeypatch, kind, timing):
    source = tmp_path / "original.vcf"
    source.write_text(HEADER + row("1", 1))
    output = tmp_path / "benchmark"
    target = tmp_path / "unrelated"
    target.mkdir()
    marker = target / "keep"
    marker.write_bytes(b"unrelated")
    real_receipt = benchmark.atomic_json
    identity = None

    def create_destination():
        nonlocal identity
        if kind == "file":
            output.write_bytes(b"existing")
        elif kind in {"directory", "empty_directory"}:
            output.mkdir()
            if kind == "directory":
                (output / "keep").write_bytes(b"existing")
        else:
            output.symlink_to(target if kind == "symlink" else tmp_path / "missing")
        stat = output.lstat()
        identity = (stat.st_ino, stat.st_mode, stat.st_mtime_ns)

    def receipt(path, metrics):
        real_receipt(path, metrics)
        create_destination()

    if timing == "before":
        create_destination()
    else:
        monkeypatch.setattr(benchmark, "atomic_json", receipt)
    with pytest.raises(ValueError, match="^Benchmark output already exists$"):
        select_autosomal_benchmark(source, output)
    stat = output.lstat()
    assert (stat.st_ino, stat.st_mode, stat.st_mtime_ns) == identity
    if kind == "file":
        assert output.read_bytes() == b"existing"
    elif kind == "directory":
        assert (output / "keep").read_bytes() == b"existing"
        assert {entry.name for entry in output.iterdir()} == {"keep"}
    elif kind == "empty_directory":
        assert not list(output.iterdir())
    else:
        assert output.readlink() == (target if kind == "symlink" else tmp_path / "missing")
    assert marker.read_bytes() == b"unrelated"
    assert set(tmp_path.iterdir()) == {source, output, target}


def test_benchmark_cli_requires_local_authorization(tmp_path):
    from typer.testing import CliRunner

    from rare_disease_agent.workflows.phase5_cli import app

    source = tmp_path / "original.vcf"
    source.write_text(HEADER + row("1", 1))
    output = tmp_path / "benchmark"
    specification = {
        "original_vcf": str(source),
        "original_index": str(tmp_path / "index"),
        "phenotype_docx": str(tmp_path / "phenotype.docx"),
        "annotation_rehearsal": str(output),
        "hpo_directory": str(tmp_path / "hpo"),
        "output": str(tmp_path / "ranking"),
        "run_id": "synthetic-benchmark",
        "confirmed_affected_sample": False,
        "confirmed_local_research_use": False,
    }
    config = tmp_path / "config.json"
    config.write_text(json.dumps(specification))
    runner = CliRunner()
    result = runner.invoke(app, ["benchmark-select", str(config)])
    assert result.exit_code == 1
    assert "PermissionError" in result.output
    assert not output.exists()
    specification["confirmed_local_research_use"] = True
    config.write_text(json.dumps(specification))
    result = runner.invoke(app, ["benchmark-select", str(config), "--per-autosome", "2"])
    assert result.exit_code == 0
    assert json.loads(result.output)["selected_records"] == 1
