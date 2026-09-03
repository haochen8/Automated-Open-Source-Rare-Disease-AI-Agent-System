from importlib.resources import as_file, files
from pathlib import Path

import pytest

from rare_disease_agent.storage.parquet import write_variants_parquet
from rare_disease_agent.tools.variants.vcf import parse_vcf


@pytest.fixture
def phase2_parquet(tmp_path: Path) -> Path:
    output = tmp_path / "phase2.parquet"
    resource = files("rare_disease_agent.resources").joinpath("synthetic_phase2.vcf.txt")
    with as_file(resource) as fixture:
        assert write_variants_parquet(parse_vcf(fixture), output) == 18
    return output
