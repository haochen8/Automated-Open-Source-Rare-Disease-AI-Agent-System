import json

import duckdb
import pytest

from rare_disease_agent.tools.variants.ingest import ingest_vep

FIELDS = "Allele|Consequence|SYMBOL|Gene|Feature|ALLELE_NUM|gnomADe_AF|gnomADg_AF"
HEAD = (
    "##fileformat=VCFv4.2\n"
    + f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {FIELDS}">\n'
    + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsynthetic\n"
)
ROW = "1\t100\t.\tA\tG\t50\tPASS\tRDA_SOURCE_ROW=1;RDA_ALT_IDX=1;AF=0.8\tGT:GQ\t0/1:90\n"
CSQ = (
    "G|intron_variant|SYN1|GENE1|T1|1||,"
    "G|missense_variant&splice_region_variant|SYN1|GENE1|T2|1||,"
    "G|synonymous_variant|SYN2|GENE2|T3|1||"
)


def inputs(tmp_path):
    normalized = tmp_path / "normalized.vcf"
    normalized.write_text(HEAD + ROW)
    annotated = tmp_path / "annotated.vcf"
    annotated.write_text(HEAD + ROW.replace("\tGT:GQ", ";CSQ=" + CSQ + "\tGT:GQ"))
    return annotated, normalized


def test_preserve_transcripts_and_overlapping_genes_and_unknown_frequency(tmp_path):
    annotated, normalized = inputs(tmp_path)
    output = tmp_path / "import"
    result = ingest_vep(annotated, normalized, output)
    assert (result["alleles"], result["consequences"], result["variants"]) == (1, 3, 2)
    with duckdb.connect() as db:
        rows = db.from_parquet(str(output / "variants.parquet")).order("gene").fetchall()
        names = db.from_parquet(str(output / "variants.parquet")).columns
        rows = [dict(zip(names, row, strict=True)) for row in rows]
        assert [row["gene"] for row in rows] == ["SYN1", "SYN2"]
        assert rows[0]["consequence"] == "missense_variant"
        assert rows[0]["transcript"] == "T2"
        assert all(row["allele_frequency"] is None for row in rows)
        assert all(row["genotype"] == "0/1" for row in rows)
        assert set(json.loads(rows[0]["genotype_calls_json"])) == {"sample_1"}


@pytest.mark.parametrize("mutation", ["genotype", "provenance", "duplicate", "schema"])
def test_ingest_rejects_changes_without_partial_publication(tmp_path, mutation):
    annotated, normalized = inputs(tmp_path)
    text = annotated.read_text()
    if mutation == "genotype":
        text = text.replace("0/1:90", "1/1:90")
    elif mutation == "provenance":
        text = text.replace("RDA_SOURCE_ROW=1", "RDA_SOURCE_ROW=2")
    elif mutation == "duplicate":
        text += text.splitlines()[-1] + "\n"
    else:
        text = text.replace("GENE1", "GENE1|PRIVATE_SENTINEL")
    annotated.write_text(text)
    output = tmp_path / "failed"
    with pytest.raises(ValueError) as exc:
        ingest_vep(annotated, normalized, output)
    assert "PRIVATE_SENTINEL" not in str(exc.value)
    assert not output.exists()
    assert not list(tmp_path.glob("vep-import-*"))


def test_unsupported_contigs_are_retained_as_deferred(tmp_path):
    annotated, normalized = inputs(tmp_path)
    for path in (annotated, normalized):
        path.write_text(path.read_text().replace("\n1\t", "\nGL_SYNTHETIC\t"))
    result = ingest_vep(annotated, normalized, tmp_path / "import")
    assert result["alleles"] == result["deferred_alleles"] == 1
    assert result["consequences"] == 3
    assert result["variants"] == 0


def test_split_reconciliation_tracks_alt_ordinals_and_phasing(tmp_path):
    from rare_disease_agent.tools.variants.ingest import verify_split_genotypes

    subset = tmp_path / "subset.vcf"
    normalized = tmp_path / "normalized.vcf"
    subset.write_text(
        HEAD
        + ROW.replace("\tG\t", "\tG,T\t")
        .replace("RDA_ALT_IDX=1;", "RDA_ALT_IDX=1,2;")
        .replace("0/1:90", "1|2:90")
    )
    normalized.write_text(
        HEAD
        + ROW.replace("0/1:90", "1|0:90")
        + ROW.replace("\tG\t", "\tT\t")
        .replace("RDA_ALT_IDX=1;", "RDA_ALT_IDX=2;")
        .replace("0/1:90", "0|1:90")
    )
    result = verify_split_genotypes(subset, normalized)
    assert result["normalized_alleles"] == 2
    normalized.write_text(normalized.read_text().replace("0|1:90", "1|0:90"))
    with pytest.raises(ValueError, match="reconciliation"):
        verify_split_genotypes(subset, normalized)


def synthetic_docx(path, row_text="HP:0001250"):
    from zipfile import ZipFile

    with ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>'
            + row_text
            + "</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>",
        )


def test_explicit_hpo_extraction_refuses_ambiguous_negation(tmp_path):
    from rare_disease_agent.workflows.phase5 import extract_explicit_hpo

    path = tmp_path / "synthetic.docx"
    synthetic_docx(path)
    assert extract_explicit_hpo(path) == ["HP:0001250"]
    synthetic_docx(path, "absent HP:0001250")
    with pytest.raises(ValueError, match="polarity"):
        extract_explicit_hpo(path)


@pytest.mark.parametrize("has_high_impact", [True, False])
def test_phase5_synthetic_end_to_end_resume_and_input_change(tmp_path, has_high_impact):
    from test_phase4_resources import normalized_fixture

    from rare_disease_agent.resource_management import atomic_json, sha256
    from rare_disease_agent.resource_management.hpo import normalize_hpo
    from rare_disease_agent.workflows.phase5 import Phase5Input, phase5_run

    manager, locks = normalized_fixture(tmp_path)
    hpo = tmp_path / "hpo"
    normalize_hpo(manager, locks, hpo)
    rehearsal = tmp_path / "annotation"
    rehearsal.mkdir()
    annotated, normalized = inputs(rehearsal)
    if not has_high_impact:
        annotated.write_text(annotated.read_text().replace("missense_variant", "intron_variant"))
    subset = rehearsal / "subset.vcf"
    subset.write_text(normalized.read_text())
    original = tmp_path / "original.vcf"
    original.write_text(normalized.read_text())
    index = tmp_path / "synthetic.index"
    index.write_text("synthetic placeholder; preflight pairing tested separately")
    document = tmp_path / "synthetic.docx"
    synthetic_docx(document)
    for name, incoming, outgoing in [
        ("normalization", subset, normalized),
        ("annotation", normalized, annotated),
    ]:
        atomic_json(
            rehearsal / (name + ".receipt.json"),
            {"input_sha256": sha256(incoming), "output_sha256": sha256(outgoing), "returncode": 0},
        )
    atomic_json(
        rehearsal / "integrity_manifest.json",
        {p.name: sha256(p) for p in rehearsal.iterdir() if p.is_file()},
    )
    atomic_json(
        rehearsal / "run_manifest.json",
        {
            "status": "completed",
            "source_sha256": sha256(original),
            "authorized_scope": {"operations": [{"reference_sha256": "a" * 64}]},
        },
    )
    spec = Phase5Input(
        original_vcf=original,
        original_index=index,
        phenotype_docx=document,
        annotation_rehearsal=rehearsal,
        hpo_directory=hpo,
        output=tmp_path / "run",
        confirmed_affected_sample=True,
        confirmed_local_research_use=True,
        run_id="synthetic-phase5",
    )
    with pytest.raises(RuntimeError, match="InterruptedError"):
        phase5_run(spec, interrupt_after="prepare")
    result = phase5_run(spec)
    assert result["status"] == "completed_bounded_dry_run"
    assert result["gene_level_candidates"] == 2
    assert result["branch_counts"]["pathogenicity-priority"] == int(has_high_impact)
    assert result["whole_genome_analysis_complete"] is False
    assert {"conservative", "novel-gene-rescue", "ensemble"} <= result["branch_counts"].keys()
    assert result["ranking_counts"]["conservative_filtering_phenotype_inheritance"] == 2
    assert len(result["ranking_counts"]) == 28
    before = sha256(
        spec.output / "analysis" / "workflow" / "pipeline" / "candidate_membership.duckdb"
    )
    assert phase5_run(spec) == result
    assert before == sha256(
        spec.output / "analysis" / "workflow" / "pipeline" / "candidate_membership.duckdb"
    )
    from rare_disease_agent.workflows.phase5_cli import supervise

    unknown_spec = spec.model_copy(
        update={"output": tmp_path / "supervised", "confirmed_affected_sample": False}
    )
    config = tmp_path / "supervised.json"
    config.write_text(unknown_spec.model_dump_json())
    assert supervise(config)["process_returncode"] == 0
    unknown_metrics = json.loads(
        (unknown_spec.output / "deliverables" / "sanitized_metrics.json").read_text()
    )
    assert unknown_metrics["sample_phenotype_linkage_confirmed"] is False
    document.write_bytes(b"changed synthetic input")
    with pytest.raises(RuntimeError):
        phase5_run(spec)


def test_phase5_rejects_git_outputs_and_missing_authorization(tmp_path):
    from rare_disease_agent.workflows.phase5 import Phase5Input, phase5_run

    git = tmp_path / "git"
    git.mkdir()
    (git / ".git").mkdir()
    spec = Phase5Input(
        original_vcf=tmp_path / "x",
        original_index=tmp_path / "i",
        phenotype_docx=tmp_path / "p",
        annotation_rehearsal=tmp_path / "a",
        hpo_directory=tmp_path / "h",
        output=git / "private_runs",
        confirmed_affected_sample=False,
        confirmed_local_research_use=False,
        run_id="synthetic-scope",
    )
    with pytest.raises(ValueError, match="outside"):
        phase5_run(spec)
    with pytest.raises(RuntimeError, match="ValueError"):
        phase5_run(spec.model_copy(update={"output": tmp_path / "external"}))
    assert not (tmp_path / "external" / "run.json").exists()


def test_annotation_sample_identity_change_is_rejected(tmp_path):
    annotated, normalized = inputs(tmp_path)
    annotated.write_text(annotated.read_text().replace("\tsynthetic\n", "\tsynthetic_other\n"))
    with pytest.raises(ValueError, match="sample identities"):
        ingest_vep(annotated, normalized, tmp_path / "import")


@pytest.mark.parametrize("genotype,count", [("1/1", 1), ("0/1", 2)])
def test_unknown_affected_status_caps_inheritance_fit(genotype, count):
    from rare_disease_agent.tools.inheritance.evaluator import InheritanceEvaluator
    from rare_disease_agent.tools.inheritance.schemas import (
        GenotypeCall,
        Individual,
        Pedigree,
        VariantGenotypes,
    )

    pedigree = Pedigree(proband_id="sample_1", individuals=[Individual(id="sample_1")])
    variant = VariantGenotypes(
        variant_id="synthetic",
        gene="SYNTHETIC",
        chromosome="1",
        calls=[GenotypeCall(individual_id="sample_1", genotype=genotype, quality=90)],
    )
    variants = [variant.model_copy(update={"variant_id": f"synthetic-{i}"}) for i in range(count)]
    result = InheritanceEvaluator(pedigree, run_id="synthetic").evaluate(variants)
    assert len(result.compound_heterozygous_pairs) == count - 1
    assert all(e.fit <= 0.5 for e in result.evidence)
    assert all(
        any("affected status is unknown" in warning for warning in e.warnings)
        for e in result.evidence
    )


def test_split_reconciliation_handles_larger_batches_with_bounded_memory(tmp_path):
    from rare_disease_agent.tools.variants.ingest import verify_split_genotypes

    source = tmp_path / "synthetic.vcf"
    source.write_text(
        HEAD
        + "".join(ROW.replace("RDA_SOURCE_ROW=1;", f"RDA_SOURCE_ROW={i + 1};") for i in range(2500))
    )
    result = verify_split_genotypes(source, source)
    assert result["source_records"] == result["normalized_alleles"] == 2500
