from pathlib import Path

import pytest

from rare_disease_agent.privacy.guards import inspect_path
from rare_disease_agent.tools.variants.adapters import ToolJob
from rare_disease_agent.tools.variants.vep import VEPLayout, contig_plan, population_frequency

FIELDS = "Allele|Consequence|SYMBOL|Gene|Feature|ALLELE_NUM|gnomADe_AF|gnomADg_AF|AF|PICK"
HEADER = f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {FIELDS}">'


def test_csq_preserves_all_genes_and_transcripts_without_callset_frequency_fallback():
    layout = VEPLayout.from_header(HEADER)
    annotations = layout.decode(
        "G|missense_variant|SYN1|ENSG1|ENST1|1|0.001|0.002|0.5|1,"
        "G|splice_region_variant|SYN2|ENSG2|ENST2|1|0.001|0.002|0.5|,"
        "T|intron_variant|SYN3|ENSG3|ENST3|2|||1|1",
        alternate_count=2,
    )
    assert len(annotations) == 3
    assert [a.value("Gene") for a in annotations] == ["ENSG1", "ENSG2", "ENSG3"]
    assert annotations[0].value("AF") == "0.5"  # Retained but never used as a fallback.
    assert population_frequency(annotations, allele_number=1) == 0.002
    assert population_frequency(annotations, allele_number=2) is None


@pytest.mark.parametrize("value", ["NaN", "inf", "-0.1", "1.1", "PRIVATE_SENTINEL"])
def test_invalid_population_frequency_fails_without_exposing_values(value):
    with pytest.raises(ValueError) as exc:
        VEPLayout.from_header(HEADER).decode(
            f"G|missense_variant|SYN1|ENSG1|ENST1|1|{value}||0.5|", alternate_count=1
        )
    assert str(exc.value) == "Invalid external population frequency"


@pytest.mark.parametrize("raw", ["G|too_few", "G|intron_variant|SYN1|ENSG1|ENST1|2|||0.5|"])
def test_csq_cardinality_and_allele_correspondence_are_required(raw):
    with pytest.raises(ValueError):
        VEPLayout.from_header(HEADER).decode(raw, alternate_count=1)


def test_missing_external_frequency_schema_is_rejected():
    with pytest.raises(ValueError):
        VEPLayout.from_header(HEADER.replace("gnomADe_AF|gnomADg_AF|", ""))


def test_missing_annotation_is_not_a_zero_frequency():
    layout = VEPLayout.from_header(HEADER)
    assert population_frequency(layout.decode(".", alternate_count=1), allele_number=1) is None
    result = layout.decode("G|intron_variant|SYN1|ENSG1|ENST1|1|0||1|", alternate_count=1)
    assert population_frequency(result, allele_number=1) == 0


def test_consequence_work_budget_fails_closed(monkeypatch):
    from rare_disease_agent.tools.variants import vep

    layout = VEPLayout.from_header(HEADER)
    monkeypatch.setattr(vep, "MAX_CONSEQUENCES", 1)
    row = "G|intron_variant|SYN1|ENSG1|ENST1|1|||0.5|"
    with pytest.raises(ValueError, match="work budget"):
        layout.decode(row + "," + row, alternate_count=1)
    monkeypatch.setattr(vep, "MAX_CSQ_BYTES", 10)
    with pytest.raises(ValueError, match="work budget"):
        layout.decode(row, alternate_count=1)


def test_nonstandard_contigs_are_retained_as_pending_and_alias_collisions_fail():
    assert contig_plan(("chr1", "chrM", "SYN_ALT")) == {
        "chr1": "1",
        "chrM": "MT",
        "SYN_ALT": None,
    }
    with pytest.raises(ValueError, match="collide"):
        contig_plan(("M", "MT"))
    with pytest.raises(ValueError, match="Ambiguous"):
        contig_plan(("1", "1"))


def test_track1_vep_preview_requests_external_annotations_without_dropping_transcripts(tmp_path):
    job = ToolJob(
        provider="vep",
        tool_version="116",
        data_version="116",
        genome_build="GRCh38",
        input_path=tmp_path / "synthetic.vcf",
        output_path=tmp_path / "synthetic.annotated.vcf",
        reference_path=tmp_path / "synthetic.fa",
        reference_sha256="a" * 64,
        cache_path=tmp_path / "cache",
        annotation_profile="track1",
    )
    command = job.preview()
    assert {"--offline", "--allele_number", "--af_gnomade", "--af_gnomadg"} <= set(command)
    assert "--flag_pick_allele_gene" in command
    assert not {"--pick", "--pick_allele", "--database", "--filter_common"} & set(command)
    assert command[command.index("--fork") + 1] == "2"
    assert "--fork" not in job.model_copy(update={"threads": 1}).preview()
    small = ToolJob.model_validate({**job.model_dump(), "threads": 1, "buffer_size": 25})
    assert small.preview()[small.preview().index("--buffer_size") + 1] == "25"
    assert not list(tmp_path.iterdir())  # Preview neither creates artifacts nor executes tools.


@pytest.mark.parametrize(
    "relative", ["private_runs/report.json", "private_cache/hpo.txt", "x.vcf.gz.tbi"]
)
def test_private_phase5_paths_and_index_are_blocked(tmp_path: Path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic placeholder")
    assert inspect_path(path, repository_root=tmp_path)


def test_isolated_launcher_environment_keeps_helpers_without_inheriting_secrets(monkeypatch):
    import os

    from rare_disease_agent.tools.variants.adapters import _tool_environment

    monkeypatch.setenv("PHASE5_SECRET", "hidden")
    env = _tool_environment("/isolated/bin/vep")
    assert env["PATH"].split(os.pathsep)[0] == "/isolated/bin"
    assert "PHASE5_SECRET" not in env
    assert env["LANGSMITH_TRACING"] == "false"


def test_supervisor_accepts_tool_exit_during_memory_inspection(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from rare_disease_agent.tools.variants import adapters

    class Process:
        pid = 123
        returncode = 0
        polls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0

    def exited():
        raise adapters.psutil.ZombieProcess(123)

    monkeypatch.setattr(adapters.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(
        adapters.psutil,
        "Process",
        lambda pid: SimpleNamespace(children=lambda recursive: [], memory_info=exited),
    )
    monkeypatch.setattr(adapters.time, "sleep", lambda seconds: None)
    job = SimpleNamespace(
        output_path=tmp_path / "unused", timeout_seconds=1, memory_mb=128, output_limit_mb=1
    )
    assert adapters.bounded_process(["/isolated/bin/bcftools"], job) == 0


def test_vep_version_probe_uses_help_and_preserves_environment_launcher(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from rare_disease_agent.tools.variants import adapters

    binary = tmp_path / "bin" / "vep"
    binary.parent.mkdir()
    target = tmp_path / "vep-source"
    target.write_text("synthetic executable")
    binary.symlink_to(target)
    source = tmp_path / "input"
    source.write_text("synthetic input")
    reference = tmp_path / "reference"
    reference.write_text("synthetic reference")
    job = ToolJob(
        provider="vep",
        tool_version="116.0",
        data_version="116",
        genome_build="GRCh38",
        input_path=source,
        output_path=tmp_path / "output",
        reference_path=reference,
        reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        cache_path=tmp_path,
        memory_mb=128,
        output_limit_mb=1,
    )
    seen = []

    def probe(command, **kwargs):
        seen.append(command)
        assert kwargs["env"]["PATH"].startswith(str(binary.parent))
        return SimpleNamespace(stdout="ensembl-vep : 116.0\n")

    def runner(command, job):
        job.output_path.write_text("synthetic output")
        return 0

    monkeypatch.setattr(adapters.shutil, "which", lambda name: str(binary))
    monkeypatch.setattr(adapters.subprocess, "run", probe)
    monkeypatch.setattr(adapters, "bounded_process", runner)
    monkeypatch.setattr(
        adapters.psutil, "virtual_memory", lambda: SimpleNamespace(available=4 * 1024**3)
    )
    adapters.execute_job(job, authorized=True, runner=runner)
    assert seen == [[str(binary), "--help"]]


def test_batch_owned_tool_group_preserves_parent_cleanup_on_timeout(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from rare_disease_agent.tools.variants import adapters

    killed = []
    seen = []

    class Process:
        pid = 123
        returncode = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def poll(self):
            return self.returncode

        def kill(self):
            killed.append("child")
            self.returncode = -9

        def wait(self):
            return self.returncode

    def launch(*args, **kwargs):
        seen.append(kwargs["start_new_session"])
        return Process()

    monkeypatch.setattr(adapters.subprocess, "Popen", launch)
    monkeypatch.setattr(
        adapters.psutil,
        "Process",
        lambda pid: SimpleNamespace(
            children=lambda recursive: [], memory_info=lambda: SimpleNamespace(rss=1024**3)
        ),
    )
    job = SimpleNamespace(
        output_path=tmp_path / "unused", memory_mb=128, output_limit_mb=1, timeout_seconds=1
    )
    with pytest.raises(RuntimeError, match="resource limit"):
        adapters.bounded_process(["/synthetic/tool"], job, isolate_process_group=False)
    assert seen == [False] and killed == ["child"]
