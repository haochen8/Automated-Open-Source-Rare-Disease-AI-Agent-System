import pytest

from rare_disease_agent.tools.variants.ingest import info_fields, vcf_rows
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


def test_benchmark_cli_requires_local_authorization(tmp_path):
    import json

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
