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
