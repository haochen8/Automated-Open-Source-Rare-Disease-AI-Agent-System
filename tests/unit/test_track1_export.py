"""Final-stage export tests use deliberately synthetic, non-person candidates."""

from __future__ import annotations

import copy
import csv
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/track1_export.py"
SPEC = importlib.util.spec_from_file_location("standalone_track1_export", SCRIPT)
exporter = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPT.parent))
try:
    SPEC.loader.exec_module(exporter)
finally:
    sys.path.pop(0)
PROBAND = "SYNTHETIC_EXPORT_CASE"


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(exporter.validator.HEADERS)
    writer.writerows(rows)
    return stream.getvalue().encode()


def candidate(chrom="1", pos=101, source_id="SYNTHETIC_SOURCE_A", ref="a", alt="t"):
    return {"allele": {"chrom": chrom, "pos": pos, "ref": ref, "alt": alt}, "source_id": source_id}


def saved_selection():
    hypotheses = [
        {
            "submission_rank": 1,
            "first": candidate(),
            "second": candidate("chr1", 202, "SYNTHETIC_SOURCE_B", "g", "c"),
            "epcr": "0.1000",
        },
        {
            "submission_rank": 2,
            "first": candidate("X", 303, "SYNTHETIC_SOURCE_C", "AC", "A"),
            "second": None,
            "epcr": "0.080000",
        },
    ]
    audit = {
        "schema_version": 1,
        "version": exporter.SOURCE_VERSION,
        "status": exporter.SOURCE_STATUS,
        "policy_version": exporter.POLICY_VERSION,
        "proband_label": PROBAND,
        "proband_label_is_biological_identity": False,
        "hypotheses": hypotheses,
    }
    rows = []
    for item in hypotheses:
        first = exporter.endpoint(item["first"])
        second = exporter.endpoint(item["second"]) if item["second"] else ["", "", "", ""]
        rows.append(
            [
                PROBAND,
                *first,
                *second,
                item["epcr"],
                "primary",
                'Synthetic note, "quoted"\nuncertain',
            ]
        )
    return rows, audit


def seal(source, rows, audit):
    source.mkdir(exist_ok=True)
    files = {"draft.csv": csv_bytes(rows), "audit.json": exporter.json_bytes(audit)}
    manifest = {
        "version": exporter.SOURCE_VERSION,
        "status": exporter.SOURCE_STATUS,
        "artifact_sha256": {name: exporter.sha256(raw) for name, raw in files.items()},
    }
    files["manifest.json"] = exporter.json_bytes(manifest)
    files["integrity_manifest.json"] = exporter.json_bytes(
        {name: exporter.sha256(raw) for name, raw in files.items()}
    )
    for name, raw in files.items():
        (source / name).write_bytes(raw)
    return exporter.sha256(files["integrity_manifest.json"])


def supplied(tmp_path):
    root = tmp_path.resolve()
    source = root / "synthetic-sealed-selection"
    output = root / "synthetic-new-export"
    rows, audit = saved_selection()
    config = {
        "schema_version": 1,
        "source_directory": str(source),
        "source_integrity_sha256": seal(source, rows, audit),
        "expected_proband": PROBAND,
        "chromosome_mapping_policy": exporter.MAPPING_POLICY,
        "output_directory": str(output),
    }
    config_path = root / "synthetic-config.json"
    config_path.write_bytes(exporter.json_bytes(config))
    return config_path, config, source, output


def refused(config_path, output, capsys):
    assert exporter.main(["--config", str(config_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == exporter.REFUSAL
    assert not output.exists()
    assert not list(output.parent.glob(".track1-export-*"))


def test_export_preserves_fields_source_bytes_and_exact_epcr_and_records_mapping(tmp_path, capsys):
    config_path, config, source, output = supplied(tmp_path)
    original = {path.name: path.read_bytes() for path in source.iterdir()}
    assert exporter.main(["--config", str(config_path)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == exporter.SUCCESS and captured.err == ""
    result = list(csv.reader(io.StringIO((output / "submission.csv").read_text(), newline="")))
    previous = list(csv.reader(io.StringIO(original["draft.csv"].decode(), newline="")))
    assert result[0] == previous[0]
    assert result[1][1] == result[1][5] == "chr1"
    assert result[2][1] == "chrX" and result[2][5:9] == ["", "", "", ""]
    for actual, before in zip(result[1:], previous[1:], strict=True):
        assert all(actual[index] == before[index] for index in range(12) if index not in {1, 5})
    mapping = json.loads((output / "export_audit.json").read_bytes())
    assert [item["source_allele"]["chrom"] for item in mapping["chromosome_mappings"]] == [
        "1",
        "chr1",
        "X",
    ]
    assert [item["changed"] for item in mapping["chromosome_mappings"]] == [True, False, True]
    assert mapping["source_integrity_sha256"] == config["source_integrity_sha256"]
    assert mapping["source_analysis_rerun"] is False
    assert mapping["source_binding_revalidated"] is False
    assert mapping["hypothesis_selection_rerun"] is False
    assert mapping["epcr_recomputed"] is False
    assert set(mapping["code_sha256"]) == {"track1_export.py", "track1_validate.py"}
    assert {path.name: path.read_bytes() for path in source.iterdir()} == original
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    integrity = json.loads((output / "integrity_manifest.json").read_bytes())
    for name, checksum in integrity.items():
        assert exporter.sha256((output / name).read_bytes()) == checksum
        assert stat.S_IMODE((output / name).stat().st_mode) == 0o600


def test_same_source_produces_identical_csv_in_distinct_output_directories(tmp_path):
    config_path, config, _source, output = supplied(tmp_path)
    exporter.export(config_path)
    config["output_directory"] = str(output.with_name("synthetic-repeat-export"))
    config_path.write_bytes(exporter.json_bytes(config))
    exporter.export(config_path)
    repeated = Path(config["output_directory"])
    assert (repeated / "submission.csv").read_bytes() == (output / "submission.csv").read_bytes()
    assert (repeated / "export_audit.json").read_bytes() != (
        output / "export_audit.json"
    ).read_bytes()


@pytest.mark.parametrize("chrom", ["1", "22", "X", "Y", "chr1", "chr22", "chrX", "chrY"])
def test_explicit_bare_and_prefixed_primary_chromosomes(chrom):
    rows, audit = saved_selection()
    rows = rows[:1]
    audit["hypotheses"] = audit["hypotheses"][:1]
    rows[0][1] = audit["hypotheses"][0]["first"]["allele"]["chrom"] = chrom
    result, mapping = exporter.render_export(csv_bytes(rows), audit, PROBAND)
    expected = chrom if chrom.startswith("chr") else "chr" + chrom
    assert list(csv.reader(io.StringIO(result.decode())))[1][1] == expected
    assert mapping[0]["source_allele"]["chrom"] == chrom


@pytest.mark.parametrize(
    "chrom", ["MT", "M", "chrM", "01", "chr01", "x", "CHRX", "23", "GL000220.1"]
)
def test_unsupported_contigs_are_refused_without_guessing(chrom):
    rows, audit = saved_selection()
    rows[0][1] = audit["hypotheses"][0]["first"]["allele"]["chrom"] = chrom
    with pytest.raises(exporter.ExportRefusal):
        exporter.render_export(csv_bytes(rows), audit, PROBAND)


@pytest.mark.parametrize("field", ["rank", "chrom", "pos", "ref", "alt", "epcr", "second", "label"])
def test_consistently_resealed_draft_still_must_match_saved_audit(tmp_path, capsys, field):
    config_path, config, source, output = supplied(tmp_path)
    rows, audit = saved_selection()
    if field == "rank":
        audit["hypotheses"].reverse()
    elif field in {"chrom", "pos", "ref", "alt"}:
        index = {"chrom": 1, "pos": 2, "ref": 3, "alt": 4}[field]
        rows[0][index] = {"chrom": "2", "pos": "102", "ref": "G", "alt": "C"}[field]
    elif field == "epcr":
        rows[0][9] = "0.10000"  # Numerically equal is insufficient: the saved string is frozen.
    elif field == "second":
        audit["hypotheses"][0]["second"] = None
    else:
        audit["proband_label"] = "SYNTHETIC_OTHER"
    config["source_integrity_sha256"] = seal(source, rows, audit)
    config_path.write_bytes(exporter.json_bytes(config))
    refused(config_path, output, capsys)


@pytest.mark.parametrize("kind", ["source_reuse", "alias_collision"])
def test_reused_or_alias_colliding_endpoints_refused(kind):
    rows, audit = saved_selection()
    candidate_b = audit["hypotheses"][0]["second"]
    if kind == "source_reuse":
        candidate_b["source_id"] = audit["hypotheses"][0]["first"]["source_id"]
    else:
        candidate_b["allele"] = {"chrom": "chr1", "pos": 101, "ref": "A", "alt": "T"}
        rows[0][5:9] = exporter.endpoint(candidate_b)
    with pytest.raises(exporter.ExportRefusal):
        exporter.render_export(csv_bytes(rows), audit, PROBAND)


@pytest.mark.parametrize(
    "name", ["draft.csv", "audit.json", "manifest.json", "integrity_manifest.json"]
)
def test_any_source_byte_change_invalidates_the_pin(tmp_path, capsys, name):
    config_path, _config, source, output = supplied(tmp_path)
    with (source / name).open("ab") as stream:
        stream.write(b" ")
    refused(config_path, output, capsys)


def test_wrong_or_extra_configuration_is_refused(tmp_path, capsys):
    config_path, config, _source, output = supplied(tmp_path)
    config["unrecognized_selection_override"] = True
    config_path.write_bytes(exporter.json_bytes(config))
    refused(config_path, output, capsys)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b"[]"])
def test_ambiguous_nonfinite_or_nonobject_json_is_refused(raw):
    with pytest.raises(exporter.ExportRefusal):
        exporter.strict_json(raw)


@pytest.mark.parametrize("kind", ["file_symlink", "parent_symlink", "fifo", "oversize", "git"])
def test_unsafe_input_location_or_type_refused(tmp_path, capsys, kind):
    config_path, config, source, output = supplied(tmp_path)
    target = source / "audit.json"
    if kind == "file_symlink":
        backup = source / "synthetic-audit-backup.json"
        target.rename(backup)
        target.symlink_to(backup)
    elif kind == "parent_symlink":
        alias = source.with_name("synthetic-symlink")
        alias.symlink_to(source, target_is_directory=True)
        config["source_directory"] = str(alias)
        config_path.write_bytes(exporter.json_bytes(config))
    elif kind == "fifo":
        target.unlink()
        os.mkfifo(target)
    elif kind == "oversize":
        target.write_bytes(b"x" * (exporter.MAX_INPUT_BYTES + 1))
    else:
        (source / ".git").write_text("synthetic marker")
    refused(config_path, output, capsys)


def test_source_change_before_publication_leaves_no_output(tmp_path, capsys, monkeypatch):
    config_path, _config, source, output = supplied(tmp_path)
    original_read = exporter.read_file
    reads = 0

    def changed_read(path):
        nonlocal reads
        if path == source / "draft.csv":
            reads += 1
            if reads == 2:
                path.write_bytes(path.read_bytes() + b"\n")
        return original_read(path)

    monkeypatch.setattr(exporter, "read_file", changed_read)
    refused(config_path, output, capsys)


@pytest.mark.parametrize("late", [False, True])
def test_existing_output_is_never_replaced_even_when_created_at_publication(
    tmp_path, capsys, monkeypatch, late
):
    config_path, _config, _source, output = supplied(tmp_path)
    original_publish = exporter.publish
    if late:

        def raced_publish(staging, destination):
            destination.mkdir()
            original_publish(staging, destination)

        monkeypatch.setattr(exporter, "publish", raced_publish)
    else:
        output.mkdir()
    assert exporter.main(["--config", str(config_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == exporter.REFUSAL
    assert output.is_dir() and not list(output.iterdir())
    assert not list(output.parent.glob(".track1-export-*"))


def test_output_inside_git_and_tracing_are_refused(tmp_path, capsys, monkeypatch):
    config_path, config, _source, output = supplied(tmp_path)
    repository = tmp_path.resolve() / "synthetic-repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config["output_directory"] = str(repository / "synthetic-export")
    config_path.write_bytes(exporter.json_bytes(config))
    refused(config_path, Path(config["output_directory"]), capsys)
    config["output_directory"] = str(output)
    config_path.write_bytes(exporter.json_bytes(config))
    monkeypatch.setenv("LANGCHAIN_TRACING", "yes")
    refused(config_path, output, capsys)


def test_cli_never_echoes_input_derived_error_payload(tmp_path):
    config_path, config, _source, _output = supplied(tmp_path)
    config = copy.deepcopy(config)
    config["source_directory"] = str(tmp_path.resolve() / "SYNTHETIC_PRIVATE_ERROR_PAYLOAD")
    config_path.write_bytes(exporter.json_bytes(config))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config_path)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == exporter.REFUSAL.encode()
