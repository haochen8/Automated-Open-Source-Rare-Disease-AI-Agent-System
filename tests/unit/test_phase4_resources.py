import hashlib
import json
from datetime import UTC, datetime
from importlib.resources import files

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from rare_disease_agent.cli import app
from rare_disease_agent.resource_management import (
    ResourceLock,
    ResourceManager,
    ResourceReceipt,
    lock_hash,
    safe_url,
)
from rare_disease_agent.resource_management.hpo import IndexedPhenotypeStore, normalize_hpo
from rare_disease_agent.tools.phenotype.tools import PhenotypeToolbox


def lock_for(data=b"synthetic resource", kind="obo"):
    return ResourceLock(
        name=kind,
        kind=kind,
        source_url=f"https://raw.githubusercontent.com/obophenotype/human-phenotype-ontology/v2026-09-03/{kind}",
        release="v2026-09-03",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        expected_size=len(data),
        license_reference="Synthetic fixture MIT",
        citation="Synthetic test; not official HPO data",
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a",
        "https://evil.example/a",
        "https://github.com/latest/a",
        "https://github.com/a?token=x",
        "https://user:secret@github.com/a",
        "https://github.com/a#x",
        "https://github.com:444/a",
        "https://github.com/%0afoo",
        "https://github.com/%6catest/a",
        "https://raw.githubusercontent.com/a/main/b",
    ],
)
def test_unsafe_urls_rejected(url):
    with pytest.raises(ValueError):
        safe_url(url)


def test_lock_requires_pin_release_license_and_expected_size():
    value = lock_for().model_dump()
    for key, replacement in [
        ("expected_sha256", ""),
        ("release", "latest"),
        ("license_reference", ""),
        ("expected_size", 0),
        ("source_url", "https://github.com/someone/repo/v2026-09-03/file"),
    ]:
        with pytest.raises(ValueError):
            ResourceLock.model_validate({**value, key: replacement})


def test_fetch_checksum_redirect_and_offline_status(tmp_path):
    data = b"synthetic resource"
    lock = lock_for(data)
    manager = ResourceManager(tmp_path)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(
                302, headers={"location": "https://raw.githubusercontent.com/redirected"}
            )
        return httpx.Response(200, content=data)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        manager.fetch(lock, client=client)
    assert len(seen) == 2
    assert manager.verify(lock).observed_sha256 == lock.expected_sha256
    assert manager.status(lock)["status"] == "verified"
    manager.paths(lock)[0].write_bytes(b"damaged")
    assert manager.status(lock)["status"] == "invalid"


@pytest.mark.parametrize("mode", ["redirect", "checksum", "oversized", "loop", "compressed"])
def test_fetch_rejects_without_leaving_artifact(tmp_path, mode):
    lock = lock_for()
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if mode in {"redirect", "loop"}:
            return httpx.Response(
                302,
                headers={
                    "location": "https://evil.example/data"
                    if mode == "redirect"
                    else lock.source_url
                },
            )
        if mode == "compressed":
            return httpx.Response(200, headers={"content-encoding": "gzip"})
        return httpx.Response(200, content=b"x" * (lock.expected_size + (mode == "oversized")))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises((ValueError, httpx.HTTPError)),
    ):
        ResourceManager(tmp_path).fetch(lock, client=client)
    assert not list(tmp_path.glob("*.resource"))
    assert not list(tmp_path.glob("*.partial"))
    if mode == "redirect":
        assert len(seen) == 1


def normalized_fixture(tmp_path):
    resource = files("rare_disease_agent.resources.phenotype")
    data = {
        "obo": resource.joinpath("synthetic_hp.obo").read_bytes(),
        "genes": b"#ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id\n"
        b"1\tSYN_CAUSAL\tHP:0001250\tSynthetic seizure\t1/2\tSYNTH:1\n"
        b"2\tSYN_OTHER\tHP:0004322\tSynthetic growth\t1/1\tSYNTH:2\n",
        "diseases": b"#description: synthetic official-shaped fixture\n"
        b"#database_id\tdisease_name\tqualifier\thpo_id\treference\tevidence"
        b"\tonset\tfrequency\tsex\tmodifier\taspect\tbiocuration\n"
        b"SYNTH:1\tSynthetic\tNOT\tHP:0001250\tSYNTH:REF\tIEA\t\t1/2\t\t\tP\tSYNTH:CURATOR\n",
    }
    manager = ResourceManager(tmp_path / "cache")
    manager.directory.mkdir()
    locks = []
    for kind, content in data.items():
        lock = lock_for(content, kind)
        artifact, receipt_path = manager.paths(lock)
        artifact.write_bytes(content)
        receipt = ResourceReceipt(
            lock=lock,
            lock_hash=lock_hash(lock),
            retrieved_at=datetime.now(UTC),
            observed_sha256=lock.expected_sha256,
            size=len(content),
        )
        receipt_path.write_text(receipt.model_dump_json())
        locks.append(lock)
    return manager, locks


def test_hpo_conversion_preserves_evidence_and_drives_scoring(tmp_path):
    manager, locks = normalized_fixture(tmp_path)
    output = tmp_path / "normalized"
    normalize_hpo(manager, locks, output, batch_size=1)
    with duckdb.connect(str(output / "hpo.duckdb"), read_only=True) as connection:
        assert connection.execute(
            "SELECT qualifier, evidence, frequency FROM diseases"
        ).fetchone() == ("NOT", "IEA", "1/2")
        assert connection.execute(
            "SELECT obsolete,replaced_by FROM terms WHERE hpo_id='HP:0009999'"
        ).fetchone() == (True, "HP:0001250")
        assert connection.execute("SELECT count(*) FROM duckdb_indexes()").fetchone()[0] == 5
    store = IndexedPhenotypeStore(output)
    try:
        toolbox = PhenotypeToolbox(store, patient_hpo=["HP:0001250"], run_id="normalized")
        assert (
            toolbox.get_gene_phenotype_score("SYN_CAUSAL").score
            > toolbox.get_gene_phenotype_score("SYN_OTHER").score
        )
        assert store.disease_associations("SYNTH:1") == []
        from rare_disease_agent.config import load_settings
        from rare_disease_agent.llm.mock import MockLLMBackend
        from rare_disease_agent.workflows.mock_strategy import phase3_mock_decisions
        from rare_disease_agent.workflows.track1_evidence import Track1EvidenceWorkflow

        result = Track1EvidenceWorkflow(
            case_name="de-novo",
            backend=MockLLMBackend(structured_responses=phase3_mock_decisions()),
            settings=load_settings(),
            run_directory=tmp_path / "ranking",
            run_id="normalized-ranking",
            phenotype_store=store,
        ).run()
        assert result.metrics.top_5
        assert result.metrics.provenance.hpo_data_version == "v2026-09-03"
    finally:
        store.close()
    (output / "genes.parquet").write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum"):
        IndexedPhenotypeStore(output)


def test_resource_cli_defaults_offline_and_verifies_without_network(tmp_path):
    manager, locks = normalized_fixture(tmp_path)
    path = tmp_path / "locks.json"
    path.write_text(json.dumps([lock.model_dump(mode="json") for lock in locks]))
    runner = CliRunner()
    assert "Offline mode" in runner.invoke(app, ["resources"]).stdout
    assert runner.invoke(app, ["resources", "plan", str(path)]).exit_code == 0
    assert (
        runner.invoke(
            app, ["resources", "verify", str(path), "--cache", str(manager.directory)]
        ).exit_code
        == 0
    )


def test_authorized_rehearsal_uses_exact_local_scope_and_normalized_hpo(tmp_path):
    from rare_disease_agent.resource_management import sha256
    from rare_disease_agent.storage.parquet import write_variants_parquet
    from rare_disease_agent.synthetic.cases import load_synthetic_case
    from rare_disease_agent.tools.variants.preflight import GenomicInput
    from rare_disease_agent.tools.variants.vcf import parse_vcf
    from rare_disease_agent.workflows.authorized import LocalAuthorization, authorized_dry_run

    manager, locks = normalized_fixture(tmp_path)
    hpo_directory = tmp_path / "hpo"
    normalize_hpo(manager, locks, hpo_directory)
    case = load_synthetic_case("de-novo")
    source = tmp_path / "synthetic.parquet"
    from pathlib import Path

    write_variants_parquet(parse_vcf(Path(str(case.vcf_resource))), source)
    spec = GenomicInput(
        genome_build="GRCh38",
        annotation_build="GRCh38",
        normalized_biallelic=True,
        reference_checksum="a" * 64,
        transcript_release="synthetic-1",
        sample_ids=[person.id for person in case.pedigree.individuals],
        pedigree=case.pedigree,
        par_intervals={"X": [], "Y": []},
        allow_missing_annotations=True,
    )
    authorization = LocalAuthorization(
        confirmed_local_research_use=True,
        input_path=source,
        input_sha256=sha256(source),
        output_directory=tmp_path / "rehearsal",
    )
    result = authorized_dry_run(
        authorization=authorization,
        specification=spec,
        hpo_directory=hpo_directory,
        hpo_terms=case.hpo_terms,
        run_id="authorized-synthetic-test",
    )
    assert json.loads(result.read_text())["critic"]["passed"]
    with pytest.raises(ValueError, match="checksum"):
        authorized_dry_run(
            authorization=authorization.model_copy(update={"input_sha256": "b" * 64}),
            specification=spec,
            hpo_directory=hpo_directory,
            hpo_terms=case.hpo_terms,
            run_id="bad",
        )


def test_hpo_conversion_failure_does_not_publish_partial_output(tmp_path):
    manager, locks = normalized_fixture(tmp_path)
    manager.paths(locks[1])[0].write_text("corrupted resource")
    with pytest.raises(ValueError):
        normalize_hpo(manager, locks, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob("hpo-*"))
