from pathlib import Path

import pyarrow.parquet as pq

from rare_disease_agent.storage.duckdb import VariantDataset
from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.tools.variants.filtering import VariantFilter
from rare_disease_agent.tools.variants.vcf import parse_vcf

FIXTURE = Path("tests/fixtures/synthetic/variants.vcf.txt")


def test_vcf_to_parquet_to_duckdb_filter(tmp_path: Path) -> None:
    parquet_path = tmp_path / "variants.parquet"

    count = write_variants_parquet(parse_vcf(FIXTURE), parquet_path, batch_size=2)

    assert count == 4
    assert pq.read_metadata(parquet_path).num_rows == 4
    with VariantDataset(parquet_path) as dataset:
        assert dataset.count() == 4
        candidates = dataset.filter(
            VariantFilter(max_allele_frequency=0.01, min_cadd_score=15), limit=10
        )
    assert len(candidates) == 3
    assert {candidate["gene"] for candidate in candidates} == {"SYN1", "SYN3"}


def test_clinvar_rescue_preserves_pathogenic_common_variant(tmp_path: Path) -> None:
    parquet_path = tmp_path / "variants.parquet"
    write_variants_parquet(parse_vcf(FIXTURE), parquet_path)

    with VariantDataset(parquet_path) as dataset:
        rescued = dataset.filter(VariantFilter(max_allele_frequency=0.00001), limit=10)
        strict = dataset.filter(
            VariantFilter(max_allele_frequency=0.00001, preserve_pathogenic_clinvar=False),
            limit=10,
        )

    assert [row["gene"] for row in rescued] == ["SYN1"]
    assert strict == []
