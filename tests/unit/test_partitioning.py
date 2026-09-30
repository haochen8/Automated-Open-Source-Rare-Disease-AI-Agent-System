import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from rare_disease_agent.resource_management import sha256
from rare_disease_agent.tools.variants.ingest import info_fields, vcf_rows
from rare_disease_agent.workflows import partitioning as p
from rare_disease_agent.workflows.phase5_cli import app

HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsynthetic\n"


def row(chrom, pos, alt="C", gt="0/1"):
    return f"{chrom}\t{pos}\t.\tA\t{alt}\t50\tPASS\t.\tGT:GQ\t{gt}:90\n"


@pytest.fixture(autouse=True)
def resources(monkeypatch):
    monkeypatch.setattr(
        p.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(available=2 * 1024**3, total=8 * 1024**3),
    )
    monkeypatch.setattr(p.psutil, "disk_usage", lambda _: SimpleNamespace(free=30 * 1024**3))


def setup(tmp_path, records=None, size=2):
    source = tmp_path / "source.vcf"
    source.write_text(HEADER + (records or row("1", 1) + row("1", 2) + row("1", 3)))
    spec = p.CoverageInput(
        source=source,
        source_sha256=sha256(source),
        output=tmp_path / "plan",
        records_per_shard=size,
        confirmed_local_research_use=True,
    )
    return spec, tmp_path / "materialized"


def records(path):
    return [line.split("\t") for line in vcf_rows(path) if not line.startswith("#")]


def test_disjoint_complete_accounting_multiallelic_boundaries_and_unknown_context(tmp_path):
    text = (
        row("X", 1)
        + row("1", 1)
        + row("1", 2, "C,G", "1|2")
        + row("1", 3)
        + row("1", 4, "<DEL>")
        + row("2", 1)
        + row("MT", 1)
        + row("GL_SYNTHETIC", 1)
        + row("chr3", 1)
        + row("4", 1, gt="1")
    )
    spec, output = setup(tmp_path, text)
    before = spec.source.read_bytes()
    manifest = p.prepare_coverage(spec)
    assert manifest["source_records"] == 10 and manifest["alternate_alleles"] == 11
    assert manifest["shards"] == 3
    assert manifest["counts"]["eligible_autosomal_sequence"] == {
        "source_records": 4,
        "alternate_alleles": 5,
    }
    assert sum(x["source_records"] for x in manifest["counts"].values()) == 10
    p.materialize_partitions(spec.output, output, first=1, last=3, authorized=True)
    selected = []
    for shard in sorted(output.glob("shard_*")):
        selected.extend(records(shard / "subset.vcf"))
    assert [info_fields(x[7])["RDA_SOURCE_ROW"] for x in selected] == ["2", "3", "4", "6"]
    assert info_fields(selected[1][7])["RDA_ALT_IDX"] == "1,2"
    assert selected[1][9] == "1|2:90"
    assert spec.source.read_bytes() == before
    assert json.loads((output / "run.json").read_text())["ended_at"] is not None
    assert manifest["whole_genome_analysis_complete"] is False


def test_interruption_resume_preserves_committed_shard_and_recovers_partial(tmp_path, monkeypatch):
    spec, output = setup(tmp_path, size=1)
    p.prepare_coverage(spec)
    original = p._Budget.write

    def interrupt(self, stream, text):
        if "RDA_SOURCE_ROW=2;" in text:
            raise InterruptedError("synthetic interruption")
        original(self, stream, text)

    monkeypatch.setattr(p._Budget, "write", interrupt)
    with pytest.raises(InterruptedError):
        p.materialize_partitions(spec.output, output, first=1, last=3, authorized=True)
    committed = output / "shard_000001" / "subset.vcf"
    before = (sha256(committed), committed.stat().st_mtime_ns)
    assert not (output / "shard_000002").exists()
    assert json.loads((output / "run.json").read_text())["ended_at"] is None
    monkeypatch.setattr(p._Budget, "write", original)
    p.materialize_partitions(spec.output, output, first=1, last=3, authorized=True)
    assert (sha256(committed), committed.stat().st_mtime_ns) == before
    assert not list(output.glob("*.partial"))
    journal = (output / "run.json").read_bytes()
    p.materialize_partitions(spec.output, output, first=1, last=3, authorized=True)
    assert (output / "run.json").read_bytes() == journal


@pytest.mark.parametrize("mutation", ["source", "plan", "committed", "code"])
def test_resume_refuses_changed_identity_without_repair(tmp_path, monkeypatch, mutation):
    spec, output = setup(tmp_path, size=1)
    p.prepare_coverage(spec)
    p.materialize_partitions(spec.output, output, authorized=True)
    if mutation == "source":
        spec.source.write_text(spec.source.read_text().replace("0/1:90", "1/1:90"))
    elif mutation == "plan":
        with (spec.output / "segments.jsonl").open("a") as stream:
            stream.write("{}\n")
    elif mutation == "committed":
        with (output / "shard_000001" / "subset.vcf").open("a") as stream:
            stream.write("corrupt\n")
    else:
        monkeypatch.setattr(p, "code_checksum", lambda: "changed")
    before = {str(x): sha256(x) for x in output.rglob("*") if x.is_file()}
    with pytest.raises(ValueError):
        p.materialize_partitions(spec.output, output, first=2, last=3, authorized=True)
    assert {str(x): sha256(x) for x in output.rglob("*") if x.is_file()} == before


@pytest.mark.parametrize(
    "text",
    [
        row("1", 1) + row("chr1", 2),
        row("1", 1).replace("\t.\tGT", "\tRDA_ALT_IDX=1\tGT"),
        row("1", 1, gt="0/2"),
        row("1", 1, alt="C,C"),
        row("1", 1).replace("GT:GQ", "GT:GT"),
        row("1", 1) + "#CHROM\tPOS\n",
    ],
)
def test_malformed_or_ambiguous_input_never_publishes_plan(tmp_path, text):
    spec, _ = setup(tmp_path, text)
    with pytest.raises(ValueError):
        p.prepare_coverage(spec)
    assert not spec.output.exists()
    assert not list(tmp_path.glob(".coverage-*"))


def test_nonvariant_and_missing_calls_accounted_without_inference(tmp_path):
    spec, output = setup(tmp_path, row("1", 1, alt=".", gt="0/0") + row("1", 2, gt="./."))
    m = p.prepare_coverage(spec)
    assert m["alternate_alleles"] == 1
    assert m["counts"]["nonsequence_allele_pending"] == {
        "source_records": 1,
        "alternate_alleles": 0,
    }
    p.materialize_partitions(spec.output, output, authorized=True)
    assert records(output / "shard_000001" / "subset.vcf")[0][9] == "./.:90"


def test_changed_source_during_planning_and_budget_failure_are_atomic(tmp_path, monkeypatch):
    spec, _ = setup(tmp_path)
    real = p.sha256
    calls = 0

    def changed(path):
        nonlocal calls
        if path == spec.source:
            calls += 1
            if calls == 2:
                return "0" * 64
        return real(path)

    monkeypatch.setattr(p, "sha256", changed)
    with pytest.raises(ValueError, match="changed"):
        p.prepare_coverage(spec)
    assert not spec.output.exists()
    monkeypatch.setattr(p, "sha256", real)
    monkeypatch.setattr(p, "MAX_OUTPUT", 10)
    with pytest.raises(RuntimeError, match="resource"):
        p.prepare_coverage(spec)
    assert not spec.output.exists() and not list(tmp_path.glob(".coverage-*"))


def test_cli_authorization_privacy_and_resource_refusal(tmp_path, monkeypatch):
    spec, output = setup(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(
        spec.model_copy(update={"confirmed_local_research_use": False}).model_dump_json()
    )
    runner = CliRunner()
    result = runner.invoke(app, ["coverage-plan", str(config)])
    assert result.exit_code == 1 and "PermissionError" in result.output
    config.write_text(spec.model_dump_json())
    result = runner.invoke(app, ["coverage-plan", str(config)])
    assert (
        result.exit_code == 0
        and "synthetic" not in result.output
        and str(tmp_path) not in result.output
    )
    result = runner.invoke(app, ["coverage-materialize", str(spec.output), str(output)])
    assert result.exit_code == 1 and not output.exists()
    monkeypatch.setattr(p.psutil, "virtual_memory", lambda: SimpleNamespace(available=1))
    result = runner.invoke(
        app,
        ["coverage-materialize", str(spec.output), str(output), "--confirmed-local-research-use"],
    )
    assert result.exit_code == 1 and "RuntimeError" in result.output and not output.exists()


def test_trace_and_git_boundaries_and_invalid_range(tmp_path, monkeypatch):
    spec, output = setup(tmp_path)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with pytest.raises(ValueError, match="tracing"):
        p.prepare_coverage(spec)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    (tmp_path / ".git").mkdir()
    with pytest.raises(ValueError, match="Git"):
        p.prepare_coverage(spec)
    (tmp_path / ".git").rmdir()
    p.prepare_coverage(spec)
    with pytest.raises(ValueError, match="budget"):
        p.materialize_partitions(spec.output, output, first=1, last=65, authorized=True)
    assert not output.exists()


def test_partitioned_ingestion_matches_control_and_preserves_cross_shard_pairs(tmp_path):
    import duckdb

    from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
    from rare_disease_agent.tools.inheritance.schemas import (
        GenotypeCall,
        Individual,
        Pedigree,
        VariantGenotypes,
    )
    from rare_disease_agent.tools.variants.ingest import ingest_vep, verify_split_genotypes

    spec, output = setup(tmp_path, row("1", 1, "C,G", "1|2") + row("1", 2), size=1)
    p.prepare_coverage(spec)
    p.materialize_partitions(spec.output, output, first=1, last=2, authorized=True)
    columns = "Allele|Consequence|SYMBOL|Gene|Feature|ALLELE_NUM|gnomADe_AF"
    annotation_header = HEADER.replace(
        "#CHROM", f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {columns}">\n#CHROM'
    )
    all_normalized, all_annotated = [], []
    imports = []
    for index, shard in enumerate(sorted(output.glob("shard_*"))):
        normalized_rows, annotated_rows = [], []
        for original in records(shard / "subset.vcf"):
            for alt_index, alternate in enumerate(original[4].split(","), 1):
                fields = original.copy()
                fields[4] = alternate
                source_row = info_fields(fields[7])["RDA_SOURCE_ROW"]
                fields[7] = f"RDA_SOURCE_ROW={source_row};RDA_ALT_IDX={alt_index}"
                gt = fields[9].split(":")[0]
                sep = "|" if "|" in gt else "/"
                fields[9] = (
                    sep.join("1" if int(x) == alt_index else "0" for x in gt.split(sep)) + ":90"
                )
                normalized_rows.append("\t".join(fields) + "\n")
                fields[7] += (
                    f";CSQ={alternate}|missense_variant|SYN1|SYNGENE1|TX1|1|,"
                    f"{alternate}|intron_variant|SYN1|SYNGENE1|TX2|1|"
                )
                annotated_rows.append("\t".join(fields) + "\n")
        normalized = tmp_path / f"normalized-{index}.vcf"
        annotated = tmp_path / f"annotated-{index}.vcf"
        normalized.write_text(annotation_header + "".join(normalized_rows))
        annotated.write_text(annotation_header + "".join(annotated_rows))
        verify_split_genotypes(shard / "subset.vcf", normalized)
        tables = tmp_path / f"tables-{index}"
        ingest_vep(annotated, normalized, tables)
        imports.append(tables)
        all_normalized.extend(normalized_rows)
        all_annotated.extend(annotated_rows)
    normalized = tmp_path / "control-normalized.vcf"
    annotated = tmp_path / "control-annotated.vcf"
    normalized.write_text(annotation_header + "".join(all_normalized))
    annotated.write_text(annotation_header + "".join(all_annotated))
    control = tmp_path / "control"
    ingest_vep(annotated, normalized, control)
    with duckdb.connect() as db:
        for table in ("alleles", "consequences", "candidate_map", "variants"):
            combined = db.from_parquet([str(path / f"{table}.parquet") for path in imports])
            expected = db.from_parquet(str(control / f"{table}.parquet"))
            assert sorted(combined.fetchall()) == sorted(expected.fetchall())
        combined = db.from_parquet([str(path / "variants.parquet") for path in imports])
        rows = [dict(zip(combined.columns, row, strict=True)) for row in combined.fetchall()]
    variants = [
        VariantGenotypes(
            variant_id=r["variant_id"],
            gene=r["gene"],
            chromosome=r["chromosome"],
            calls=[GenotypeCall(individual_id="synthetic", genotype=r["genotype"], quality=90)],
        )
        for r in rows
    ]
    evaluator = InheritanceEvaluator(
        Pedigree(proband_id="synthetic", individuals=[Individual(id="synthetic", affected=True)]),
        run_id="synthetic-partition-pair",
    )
    pairs = evaluator.find_compound_heterozygous_pairs(variants)
    assert any(
        pair.variant_a.startswith("r1_") and pair.variant_b.startswith("r2_") for pair in pairs
    )
