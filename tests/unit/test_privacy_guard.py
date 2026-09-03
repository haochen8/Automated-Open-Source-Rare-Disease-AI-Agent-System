from pathlib import Path

from rare_disease_agent.privacy.guards import scan_paths


def test_rejects_genomic_extension(tmp_path: Path) -> None:
    path = tmp_path / "patient.vcf"
    path.write_text("not even a real VCF", encoding="utf-8")

    findings = scan_paths([path], repository_root=tmp_path)

    assert any("genomic/data file type" in finding.reason for finding in findings)


def test_rejects_vcf_content_disguised_as_text(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("##fileformat=VCFv4.3\n#CHROM\tPOS\n", encoding="utf-8")

    findings = scan_paths([path], repository_root=tmp_path)

    assert any("VCF content" in finding.reason for finding in findings)


def test_allows_explicit_synthetic_fixture(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "tests" / "fixtures" / "synthetic"
    fixture_dir.mkdir(parents=True)
    path = fixture_dir / "variants.vcf.txt"
    path.write_text("##fileformat=VCFv4.3\n##synthetic=true\n#CHROM\tPOS\n", encoding="utf-8")

    assert scan_paths([path], repository_root=tmp_path) == []


def test_rejects_hugging_face_token(tmp_path: Path) -> None:
    path = tmp_path / "config.txt"
    fake_token = "hf_" + "abcdefghijklmnopqrstuvwxyz123456"
    path.write_text(f"token = {fake_token}", encoding="utf-8")

    findings = scan_paths([path], repository_root=tmp_path)

    assert any("Hugging Face" in finding.reason for finding in findings)


def test_rejects_restricted_directory(tmp_path: Path) -> None:
    path = tmp_path / "private_data" / "table.txt"
    path.parent.mkdir()
    path.write_text("derived rows", encoding="utf-8")

    findings = scan_paths([path], repository_root=tmp_path)

    assert any("restricted/generated directory" in finding.reason for finding in findings)
