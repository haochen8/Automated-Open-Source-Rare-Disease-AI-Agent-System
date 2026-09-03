from pathlib import Path

import pytest

from rare_disease_agent.tools.variants.vcf import VCFParseError, parse_vcf

FIXTURE = Path("tests/fixtures/synthetic/variants.vcf.txt")


def test_parse_normalizes_and_splits_multiallelic_rows() -> None:
    records = list(parse_vcf(FIXTURE))

    assert len(records) == 4
    first = records[0]
    assert first.variant_id == "1-10001-A-G"
    assert first.gene == "SYN1"
    assert first.allele_frequency == pytest.approx(0.0001)
    assert first.zygosity == "heterozygous"
    assert records[1].zygosity == "homozygous_alternate"
    assert records[2].zygosity == "hemizygous"
    assert {record.alternate for record in records[2:]} == {"A", "C"}
    assert [record.allele_frequency for record in records[2:]] == [0.004, 0.006]
    assert [record.cadd_score for record in records[2:]] == [18.5, 19.5]


def test_rejects_variant_before_header(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text("##fileformat=VCFv4.3\n1\t1\t.\tA\tG\t.\tPASS\t.\n", encoding="utf-8")

    with pytest.raises(VCFParseError, match="before #CHROM"):
        list(parse_vcf(path))


def test_rejects_missing_fileformat(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n", encoding="utf-8")

    with pytest.raises(VCFParseError, match="fileformat"):
        list(parse_vcf(path))
