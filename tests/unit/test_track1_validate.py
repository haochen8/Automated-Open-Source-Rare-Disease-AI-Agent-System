"""Standalone format checks use deliberately synthetic, non-person candidate rows."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/track1_validate.py"
SPEC = importlib.util.spec_from_file_location("standalone_track1_validate", SCRIPT)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)
PROBAND = "SYNTHETIC_CASE_A"


def candidate(*, position="101", epcr="0.8", second=False, finding="primary"):
    return [
        PROBAND,
        "chrSYNTHETIC",
        position,
        "a",
        "t",
        *(["chrSYNTHETIC", "202", "g", "c"] if second else ["", "", "", ""]),
        epcr,
        finding,
        "Deliberately synthetic rationale",
    ]


def csv_bytes(rows, *, headers=validator.HEADERS):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def supplied(tmp_path, rows=None, *, raw=None):
    path = tmp_path.resolve() / "synthetic-input.csv"
    path.write_bytes(raw if raw is not None else csv_bytes(rows or [candidate()]))
    return path


def arguments(path, *, proband=PROBAND, policy=validator.ORDER_POLICY):
    return ["--input", str(path), "--expected-proband", proband, "--order-policy", policy]


def check_refusal(path, capsys, **kwargs):
    assert validator.main(arguments(path, **kwargs)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == validator.REFUSAL


def test_success_is_fixed_and_input_preserved_with_single_pair_secondary_and_ties(tmp_path, capsys):
    rows = [candidate(), candidate(position="103", second=True, finding="secondary")]
    rows[0][-1] = "Synthetic notes, including a quoted\nsecond line"
    path = supplied(tmp_path, rows)
    original = path.read_bytes()
    assert validator.main(arguments(path)) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "contract": "track1-provisional-local-v1",
        "format_validation": "valid",
        "official_contract": "unverified",
        "source_binding": "not_assessed",
        "coverage": "not_assessed",
        "submission_readiness": "not_assessed",
    }
    assert not captured.err
    assert PROBAND not in captured.out and "chrSYNTHETIC" not in captured.out
    assert path.read_bytes() == original


@pytest.mark.parametrize("count", [1, 10])
def test_accepts_row_boundaries_and_does_not_require_probabilities_to_sum_to_one(tmp_path, count):
    path = supplied(tmp_path, [candidate(position=str(100 + i), epcr="1") for i in range(count)])
    validator.validate_file(str(path), PROBAND, validator.ORDER_POLICY)


@pytest.mark.parametrize("count", [0, 11])
def test_rejects_empty_or_excess_rows(tmp_path, capsys, count):
    path = supplied(tmp_path, raw=csv_bytes([candidate(position=str(i + 1)) for i in range(count)]))
    check_refusal(path, capsys)


@pytest.mark.parametrize("mutation", ["reordered", "missing", "duplicate", "extra", "bom"])
def test_exact_header_is_required(tmp_path, capsys, mutation):
    headers = list(validator.HEADERS)
    if mutation == "reordered":
        headers[0], headers[1] = headers[1], headers[0]
    elif mutation == "missing":
        headers.pop()
    elif mutation == "duplicate":
        headers[1] = headers[0]
    elif mutation == "extra":
        headers.append("SYNTHETIC_EXTRA")
    else:
        headers[0] = "\ufeff" + headers[0]
    check_refusal(supplied(tmp_path, raw=csv_bytes([candidate()], headers=headers)), capsys)


@pytest.mark.parametrize("epcr", ["0", "-0.1", "1.01", "nan", "inf", "-inf", "1e999", "1e-999", ""])
def test_invalid_probabilities_refused_without_echo(tmp_path, capsys, epcr):
    check_refusal(supplied(tmp_path, [candidate(epcr=epcr)]), capsys)


def test_ascending_probability_refused_without_sorting(tmp_path, capsys):
    path = supplied(tmp_path, [candidate(epcr=".2"), candidate(position="102", epcr=".8")])
    original = path.read_bytes()
    check_refusal(path, capsys)
    assert path.read_bytes() == original


@pytest.mark.parametrize("position", ["0", "000", "-1", "+1", "1.0", "1e2", "１", ""])
def test_positive_ascii_integer_positions_required(tmp_path, capsys, position):
    check_refusal(supplied(tmp_path, [candidate(position=position)]), capsys)


@pytest.mark.parametrize("index", [1, 2, 3, 4, 5, 6, 7, 8])
def test_missing_coordinate_fields_are_not_silently_ignored(tmp_path, capsys, index):
    row = candidate(second=True)
    row[index] = ""
    check_refusal(supplied(tmp_path, [row]), capsys)


@pytest.mark.parametrize("finding", ["", "PRIMARY", "incidental", " secondary"])
def test_exact_finding_type_required(tmp_path, capsys, finding):
    check_refusal(supplied(tmp_path, [candidate(finding=finding)]), capsys)


@pytest.mark.parametrize("mutation", ["same_endpoint", "same_singleton", "reversed_pair"])
def test_parser_identity_detects_duplicates_without_rewriting(tmp_path, capsys, mutation):
    first = candidate(second=mutation != "same_singleton")
    if mutation == "same_endpoint":
        first[5:9] = [first[1], "000101", "A", "T"]
        rows = [first]
    elif mutation == "same_singleton":
        other = candidate(position="000101", finding="secondary")
        other[3:5] = ["A", "T"]
        rows = [first, other]
    else:
        other = first.copy()
        other[1:5], other[5:9] = first[5:9], first[1:5]
        rows = [first, other]
    path = supplied(tmp_path, rows)
    original = path.read_bytes()
    check_refusal(path, capsys)
    assert path.read_bytes() == original


def test_chromosomes_are_literal_and_no_unrequested_allele_science_is_inferred(tmp_path):
    first = candidate()
    second = candidate()
    second[1] = "SYNTHETIC"
    third = candidate(position="103")
    third[3:5] = ["N", "<SYNTHETIC>"]
    fourth = candidate(position="104")
    fourth[3:5] = ["N", "N"]
    path = supplied(tmp_path, [first, second, third, fourth])
    validator.validate_file(str(path), PROBAND, validator.ORDER_POLICY)


@pytest.mark.parametrize(
    "mutation",
    ["wrong_proband", "whitespace", "extra_cell", "missing_cell", "nul", "utf8", "quotes"],
)
def test_malformed_rows_refused_privately(tmp_path, capsys, mutation):
    row = candidate()
    raw = None
    if mutation == "wrong_proband":
        row[0] = "SYNTHETIC_OTHER_CASE"
    elif mutation == "whitespace":
        row[1] += " "
    elif mutation == "extra_cell":
        row.append("SYNTHETIC_EXTRA")
    elif mutation == "missing_cell":
        row.pop()
    elif mutation == "nul":
        row[-1] += "\0"
    elif mutation == "utf8":
        raw = csv_bytes([row]) + b"\xff"
    else:
        raw = csv_bytes([]) + b'"unterminated'
    check_refusal(supplied(tmp_path, [row], raw=raw), capsys)


@pytest.mark.parametrize("size", [validator.MAX_FIELD_BYTES, validator.MAX_FIELD_BYTES + 1])
def test_field_limit_is_utf8_bytes(tmp_path, capsys, size):
    row = candidate()
    row[-1] = "é" * (size // 2) + "x" * (size % 2)
    path = supplied(tmp_path, [row])
    if size == validator.MAX_FIELD_BYTES:
        validator.validate_file(str(path), PROBAND, validator.ORDER_POLICY)
    else:
        check_refusal(path, capsys)


def test_file_size_limit_checked_before_parsing(tmp_path, capsys, monkeypatch):
    path = supplied(tmp_path, raw=b"x" * (validator.MAX_INPUT_BYTES + 1))
    monkeypatch.setattr(validator, "validate_contents", lambda *_: pytest.fail("must not parse"))
    check_refusal(path, capsys)


@pytest.mark.parametrize(
    "kind", ["symlink", "parent_symlink", "directory", "fifo", "missing", "parent_traversal"]
)
def test_only_explicit_regular_nofollow_input_is_opened(tmp_path, capsys, kind):
    path = supplied(tmp_path)
    other = tmp_path.resolve() / "other"
    if kind == "symlink":
        other.symlink_to(path)
        path = other
    elif kind == "parent_symlink":
        other.symlink_to(tmp_path.resolve(), target_is_directory=True)
        path = other / path.name
    elif kind == "directory":
        path = tmp_path.resolve()
    elif kind == "fifo":
        os.mkfifo(other)
        path = other
    elif kind == "missing":
        path = other
    else:
        path = tmp_path.resolve() / ".." / tmp_path.name / path.name
    check_refusal(path, capsys)


@pytest.mark.parametrize("mutation", ["contents", "replacement", "symlink_replacement"])
def test_changes_during_validation_refuse_success(tmp_path, capsys, monkeypatch, mutation):
    path = supplied(tmp_path)
    original = validator.validate_contents

    def change(raw, expected, policy):
        original(raw, expected, policy)
        if mutation == "contents":
            path.write_bytes(raw.replace(b"0.8", b"0.7"))
        else:
            replacement = path.with_name("synthetic-replacement.csv")
            replacement.write_bytes(raw)
            if mutation == "replacement":
                os.replace(replacement, path)
            else:
                path.unlink()
                path.symlink_to(replacement)

    monkeypatch.setattr(validator, "validate_contents", change)
    check_refusal(path, capsys)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--unknown", "SYNTHETIC_SECRET"],
        [
            "--input",
            "SYNTHETIC_SECRET",
            "--expected-proband",
            PROBAND,
            "--order-policy",
            "SYNTHETIC_SECRET",
        ],
    ],
)
def test_argument_errors_never_echo_values(capsys, args):
    assert validator.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == validator.REFUSAL


def test_explicit_expected_proband_and_order_policy_are_enforced(tmp_path, capsys):
    path = supplied(tmp_path)
    check_refusal(path, capsys, proband="SYNTHETIC_WRONG")
    check_refusal(path, capsys, policy="SYNTHETIC_UNDECLARED")


@pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x1b", "\x7f"])
@pytest.mark.parametrize("column", [0, 1])
def test_internal_structural_controls_are_refused(tmp_path, capsys, control, column):
    row = candidate()
    row[column] += control + "SYNTHETIC_SUFFIX"
    path = supplied(tmp_path, [row])
    check_refusal(path, capsys, proband=row[0])


def test_isolated_cli_needs_no_package_import_and_has_fixed_failure_output(tmp_path):
    path = supplied(tmp_path)
    for supplied_path, expected in ((path, 0), (path.with_name("SYNTHETIC_SECRET.csv"), 2)):
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPT), *arguments(supplied_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert completed.returncode == expected
        if expected == 0:
            assert json.loads(completed.stdout) == validator.SUCCESS and not completed.stderr
        else:
            assert completed.stdout == "" and completed.stderr == validator.REFUSAL
