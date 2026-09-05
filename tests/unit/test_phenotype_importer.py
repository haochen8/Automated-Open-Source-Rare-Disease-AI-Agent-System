import hashlib
import json

import pytest

from rare_disease_agent.tools.phenotype.importer import import_public_hpo_file


def test_public_importer_rejects_non_allowlisted_or_tokenized_url(tmp_path) -> None:
    with pytest.raises(ValueError, match="allow-listed"):
        import_public_hpo_file(
            url="https://example.com/hp.obo",
            destination=tmp_path / "hp.obo",
            release="v1",
            license_reference="license",
        )
    with pytest.raises(ValueError, match="query-tokenized"):
        import_public_hpo_file(
            url="https://github.com/hp.obo?token=secret",
            destination=tmp_path / "hp.obo",
            release="v1",
            license_reference="license",
        )


def test_public_importer_verifies_checksum_and_writes_manifest(monkeypatch, tmp_path) -> None:
    payload = b"public synthetic test artifact"

    class Response:
        url = "https://github.com/obophenotype/example/releases/download/v1/hp.obo"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield payload

    monkeypatch.setattr(
        "rare_disease_agent.tools.phenotype.importer.httpx.stream",
        lambda *args, **kwargs: Response(),
    )
    destination = tmp_path / "hp.obo"
    expected = hashlib.sha256(payload).hexdigest()

    result = import_public_hpo_file(
        url="https://github.com/obophenotype/example/releases/download/v1/hp.obo",
        destination=destination,
        release="v1",
        license_reference="public license",
        expected_sha256=expected,
    )

    manifest = json.loads((tmp_path / "hp.obo.manifest.json").read_text())
    assert destination.read_bytes() == payload
    assert result.checksum == expected
    assert manifest["release"] == "v1"
