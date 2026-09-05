import json

from typer.testing import CliRunner

from rare_disease_agent.cli import app
from rare_disease_agent.models.hardware import HardwareInfo

runner = CliRunner()


def test_models_recommend_json_does_not_download(monkeypatch) -> None:
    hardware = HardwareInfo(
        operating_system="macOS",
        architecture="arm64",
        cpu="Apple M2",
        total_memory_gb=16,
        available_memory_gb=10,
        unified_memory_gb=16,
        gpu="Apple integrated GPU (Metal)",
        metal_supported=True,
        free_disk_gb=100,
    )
    monkeypatch.setattr("rare_disease_agent.cli.inspect_hardware", lambda: hardware)

    result = runner.invoke(app, ["models", "recommend", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["local_parameter_ceiling_billion"] == 8
    assert max(candidate["parameters_billion"] for candidate in payload["candidates"]) <= 8


def test_variants_prepare_command(tmp_path) -> None:
    output = tmp_path / "variants.parquet"

    result = runner.invoke(
        app,
        ["variants", "prepare", "tests/fixtures/synthetic/variants.vcf.txt", str(output)],
    )

    assert result.exit_code == 0
    assert output.is_file()
    assert "Wrote 4 variant records" in result.stdout


def test_agent_filter_mock_cli_runs_end_to_end(tmp_path) -> None:
    output = tmp_path / "agent-run"

    result = runner.invoke(
        app,
        [
            "agent-filter",
            "--input",
            "synthetic",
            "--backend",
            "mock",
            "--output-dir",
            str(output),
            "--run-id",
            "cli-synthetic",
        ],
    )

    assert result.exit_code == 0, result.output
    metrics = json.loads((output / "metrics.json").read_text())
    assert metrics["causal_variant_preserved"] is True
    assert metrics["final_branch"] == "ensemble"
    assert (output / "audit_log.jsonl").is_file()


def test_agent_filter_rejects_non_synthetic_input(tmp_path) -> None:
    source = tmp_path / "unmarked.txt"
    source.write_text("##fileformat=VCFv4.3\n#CHROM\tPOS\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["agent-filter", "--input", str(source), "--output-dir", str(tmp_path / "run")],
    )

    assert result.exit_code != 0
    assert "explicitly marked synthetic inputs" in result.output
    assert "patient data is out of scope" in result.output


def test_models_check_reports_refusal(monkeypatch) -> None:
    hardware = HardwareInfo(
        operating_system="macOS",
        architecture="arm64",
        cpu="Apple M2",
        total_memory_gb=16,
        available_memory_gb=1,
        unified_memory_gb=16,
        gpu="Apple integrated GPU (Metal)",
        metal_supported=True,
        free_disk_gb=100,
    )
    monkeypatch.setattr("rare_disease_agent.cli.inspect_hardware", lambda: hardware)

    result = runner.invoke(app, ["models", "check", "qwen2.5:7b-instruct-q4_K_M", "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["allowed"] is False


def test_track1_synthetic_cli_reports_causal_rank(tmp_path) -> None:
    output = tmp_path / "track1"

    result = runner.invoke(
        app,
        [
            "track1-synthetic",
            "--case",
            "de-novo",
            "--output-dir",
            str(output),
            "--run-id",
            "cli-phase3",
        ],
    )

    assert result.exit_code == 0, result.output
    metrics = json.loads((output / "track1_metrics.json").read_text())
    assert metrics["causal_variant_rank"] == 1
    assert metrics["top_1"] is True
    assert (output / "provenance.json").is_file()


def test_benchmark_synthetic_cli_runs_all_cases(tmp_path) -> None:
    output = tmp_path / "benchmark"

    result = runner.invoke(app, ["benchmark-synthetic", "--output-dir", str(output)])

    assert result.exit_code == 0, result.output
    summary = json.loads((output / "benchmark_summary.json").read_text())
    assert len(summary["cases"]) == 6
    assert summary["all_causal_variants_preserved"] is True
    assert summary["top_5_rate"] == 1
    assert summary["inheritance_model_accuracy"] == 1
