#!/usr/bin/env python3
"""Select a sealed Track 1 draft from an explicitly pinned completed local corpus.

Saved scientific processing is authenticated, never resumed or rerun. Original
input/resource paths recorded in journals are provenance, not read authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path, PurePosixPath

import psutil
import track1_export as secure
import track1_saved_evidence as core
import track1_submission_policy as policy

MAX_FILES = 10_000
MAX_INPUT_BYTES = 4 * 1024**3
MAX_METADATA_BYTES = 4 * 1024**2
MAX_OUTPUT_BYTES = 100 * 1024**2
MAX_SECONDS = 600
MAX_RSS = 1024**3
MIN_AVAILABLE = 1024**3
MIN_DISK = 2 * 1024**3
CORPUS_STAGES = ("index", "validation", "assembly", "pair_preflight", "ranking")
RANKING_STAGES = ("prepare", "ingest", "analysis", "deliverables")
WORKFLOW_STAGES = ("preflight", "pipeline", "report")
TABLES = ("alleles", "candidate_map", "consequences", "variants")
REFUSAL = "TRACK1_SELECT_REFUSED\n"
require = secure.require


def digest(value: str, length: int = 64) -> bool:
    return type(value) is str and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None


def relative(value: str) -> Path:
    require(type(value) is str and bool(value))
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value)
    require(all(part not in {".", ".."} for part in path.parts))
    return Path(*path.parts)


class Budget:
    """A live monitor can interrupt a long DuckDB query between Python checks."""

    def __init__(self, output_parent: Path):
        self.output_parent = output_parent
        self.started = time.monotonic()
        self.db = None
        self.failed = threading.Event()
        self.stopped = threading.Event()
        self.process = psutil.Process()
        self.thread = threading.Thread(target=self._monitor, daemon=True)

    def _sample(self):
        require(time.monotonic() - self.started <= MAX_SECONDS)
        require(self.process.memory_info().rss <= MAX_RSS)
        require(psutil.virtual_memory().available >= MIN_AVAILABLE)
        require(psutil.disk_usage(self.output_parent).free >= MIN_DISK)

    def _monitor(self):
        while not self.stopped.wait(0.1):
            try:
                self._sample()
            except Exception:
                self.failed.set()
                if self.db is not None:
                    with suppress(Exception):
                        self.db.interrupt()
                return

    def check(self):
        require(not self.failed.is_set())
        try:
            self._sample()
        except Exception:
            self.failed.set()
            raise

    def __enter__(self):
        self.check()
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.stopped.set()
        self.thread.join(timeout=1)


class Snapshot:
    """One bounded inventory; all scientific stage bytes are checked again at publication."""

    def __init__(self, budget: Budget):
        self.budget = budget
        self.files = {}
        self.bytes = 0
        self.inventories = {}

    def _hash(self, path: Path, remaining: int = MAX_INPUT_BYTES) -> tuple[str, tuple, int]:
        self.budget.check()
        with secure.directory(path.parent) as parent:
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_INPUT_BYTES)
                require(before.st_size <= remaining)
                identity = secure.validator.signature(before)
                checksum = hashlib.sha256()
                count = 0
                while block := stream.read(1024**2):
                    count += len(block)
                    require(count <= before.st_size)
                    checksum.update(block)
                    self.budget.check()
                require(count == before.st_size)
                require(identity == secure.validator.signature(os.fstat(stream.fileno())))
                require(
                    identity
                    == secure.validator.signature(
                        os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                    )
                )
        return checksum.hexdigest(), identity, count

    def add(self, path: Path, expected: str | None = None) -> str:
        if path not in self.files:
            require(len(self.files) < MAX_FILES)
            observed = self._hash(path, MAX_INPUT_BYTES - self.bytes)
            self.bytes += observed[2]
            require(self.bytes <= MAX_INPUT_BYTES)
            self.files[path] = observed
        result = self.files[path][0]
        require(expected is None or result == expected)
        return result

    def metadata(self, path: Path) -> dict:
        require(path in self.files and self.files[path][2] <= MAX_METADATA_BYTES)
        raw = secure.read_file(path)
        require(secure.sha256(raw) == self.files[path][0])
        return secure.strict_json(raw)

    def inventory(self, base: Path) -> dict[str, str]:
        result = {}
        pending = [base]
        directories = 0
        while pending:
            current = pending.pop()
            directories += 1
            require(directories <= MAX_FILES)
            with secure.directory(current) as descriptor, os.scandir(descriptor) as entries:
                for entry in entries:
                    self.budget.check()
                    require(not entry.is_symlink())
                    path = current / entry.name
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                        require(len(pending) + directories <= MAX_FILES)
                    else:
                        require(entry.is_file(follow_symlinks=False))
                        result[path.relative_to(base).as_posix()] = self.add(path)
        self.inventories[base] = set(result)
        return result

    def recheck(self):
        for base, expected in list(self.inventories.items()):
            require(set(self.inventory(base)) == expected)
        for path, previous in self.files.items():
            require(self._hash(path) == previous)


def software(value: dict, expected_head: str):
    require(type(value) is dict)
    require(value.get("git_commit") == expected_head and value.get("git_dirty") is False)


def journal(snapshot: Snapshot, root: Path, stages: tuple[str, ...], expected_head: str) -> dict:
    value = snapshot.metadata(root / "run.json")
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1)
    require(type(value.get("run_id")) is str and bool(value["run_id"]))
    require(type(value.get("ended_at")) is str and bool(value["ended_at"]))
    require(value.get("failure_reason") is None)
    configuration = value["configuration"]
    require(type(configuration) is dict)
    require(
        value["configuration_hash"]
        == secure.sha256(json.dumps(configuration, sort_keys=True, allow_nan=False).encode())
    )
    software(value["provenance"]["software"], expected_head)
    require(type(value["stages"]) is dict and set(value["stages"]) == set(stages))
    for ordinal, name in enumerate(stages):
        record = value["stages"][name]
        require(type(record) is dict and type(record["ordinal"]) is int)
        require(record["ordinal"] == ordinal and bool(record["ended_at"]))
        outputs = record["outputs"]
        require(type(outputs) is dict and bool(outputs))
        for filename, checksum in outputs.items():
            require(digest(checksum))
            path = root / name / relative(filename)
            require(path in snapshot.files and snapshot.files[path][0] == checksum)
        actual = {
            path.relative_to(root / name).as_posix(): record[0]
            for path, record in snapshot.files.items()
            if path.is_relative_to(root / name)
        }
        require(actual == outputs)
    return value


def authenticate(config: dict, snapshot: Snapshot) -> dict:
    root = Path(config["corpus_directory"])
    snapshot.add(root / "run.json", config["corpus_journal_sha256"])
    pinned = snapshot.metadata(root / "run.json")
    require(set(pinned["stages"]) == set(CORPUS_STAGES))
    for name in CORPUS_STAGES:
        require(snapshot.inventory(root / name) == pinned["stages"][name]["outputs"])
    outer = journal(snapshot, root, CORPUS_STAGES, config["expected_analysis_head"])
    run = root / "ranking" / "run"
    ranked = journal(snapshot, run, RANKING_STAGES, config["expected_analysis_head"])
    workflow = journal(
        snapshot, run / "analysis" / "workflow", WORKFLOW_STAGES, config["expected_analysis_head"]
    )
    require(outer["run_id"] == ranked["run_id"] == workflow["run_id"])
    original = outer["configuration"]
    ranking = ranked["configuration"]
    analysis = workflow["configuration"]
    require(original["version"] == "sealed-annotation-corpus-v1")
    require(digest(original["code_sha256"]))
    require(original["code_sha256"] == ranking["code_sha256"])
    require(digest(original["source_sha256"]) and digest(original["plan_manifest_sha256"]))
    require(original["specification"]["confirmed_local_research_use"] is True)
    declaration = ranking["specification"]
    require(declaration["confirmed_local_research_use"] is True)
    require(type(declaration["confirmed_affected_sample"]) is bool)
    require(declaration["run_id"] == outer["run_id"])
    require(
        {key: value for key, value in declaration.items() if key != "output"}
        == {
            key: value
            for key, value in original["specification"]["ranking"].items()
            if key != "output"
        }
    )
    for key, input_name in (
        ("source_sha256", "challenge_track1_vcf"),
        ("index_sha256", "challenge_index"),
        ("phenotype_sha256", "challenge_phenotype"),
    ):
        require(digest(original[key]) and ranking["inputs"][input_name]["sha256"] == original[key])
    require(original["hpo_manifest_sha256"] == ranking["hpo_manifest_sha256"])
    require(digest(ranking["hpo_manifest_sha256"]))
    assembly = root / "assembly"
    seal = snapshot.metadata(assembly / "integrity_manifest.json")
    expected_seal = {
        path.relative_to(assembly).as_posix(): record[0]
        for path, record in snapshot.files.items()
        if path.is_relative_to(assembly) and path != assembly / "integrity_manifest.json"
    }
    require(seal == expected_seal)
    assembly_manifest = snapshot.metadata(assembly / "run_manifest.json")
    require(assembly_manifest["format"] == "verified-corpus-assembly-v1")
    require(assembly_manifest["status"] == "completed")
    for key in ("source_sha256", "code_sha256", "plan_manifest_sha256"):
        require(assembly_manifest[key] == original[key])
    require(assembly_manifest["source_plan"]["source_sha256"] == original["source_sha256"])
    require(assembly_manifest["coverage"]["whole_genome_analysis_complete"] is False)
    require(
        ranking["rehearsal_integrity_sha256"]
        == snapshot.files[assembly / "integrity_manifest.json"][0]
    )
    contexts = assembly_manifest["context"]
    require(type(contexts) is list and len(contexts) == 2)
    context_fields = {
        "provider",
        "tool_version",
        "data_version",
        "genome_build",
        "reference_sha256",
        "cache_path",
        "annotation_profile",
    }
    for context, name, provider in zip(
        contexts, ("normalization", "annotation"), ("bcftools", "vep"), strict=True
    ):
        tool = original["specification"][name]
        require(
            context
            == {key: tool["job"][key] for key in context_fields}
            | {"executable_sha256": tool["executable_sha256"]}
        )
        require(context["provider"] == provider and context["genome_build"] == "GRCh38")
        require(digest(context["reference_sha256"]) and digest(context["executable_sha256"]))
    require(contexts[0]["reference_sha256"] == contexts[1]["reference_sha256"])
    require(contexts[1]["data_version"] == "116")
    require(contexts[1]["tool_version"].split(".")[0] == "116")
    require(contexts[1]["annotation_profile"] == "track1")
    tables = snapshot.metadata(assembly / "tables" / "manifest.json")
    require(set(tables["outputs"]) == {name + ".parquet" for name in TABLES})
    for name, checksum in tables["outputs"].items():
        require(seal["tables/" + name] == checksum)
        require(snapshot.files[run / "ingest" / "tables" / name][0] == checksum)
    require(snapshot.metadata(run / "ingest" / "tables" / "manifest.json") == tables)
    require(snapshot.metadata(run / "prepare" / "run_manifest.json") == ranking)
    require(snapshot.metadata(run / "prepare" / "annotation_provenance.json") == assembly_manifest)
    require(
        snapshot.files[run / "prepare" / "reconciliation.json"][0]
        == snapshot.files[assembly / "reconciliation.json"][0]
    )
    require(
        snapshot.files[run / "prepare" / "shard_proofs.jsonl"][0]
        == snapshot.files[assembly / "shard_proofs.jsonl"][0]
        == assembly_manifest["proof_index"]["sha256"]
    )
    require(assembly_manifest["proof_index"]["path"] == "shard_proofs.jsonl")
    pair = snapshot.metadata(root / "pair_preflight" / "pair_workload.json")
    require(pair["assembly_integrity_sha256"] == ranking["rehearsal_integrity_sha256"])
    require(pair["within_budget"] is True and pair["pair_work_budget"] == 200_000)
    require(type(pair["eligible_candidate_pairs"]) is int)
    require(0 <= pair["eligible_candidate_pairs"] <= 200_000)
    rank_seal = snapshot.metadata(run / "integrity_manifest.json")
    require(set(rank_seal) == {"configuration_hash", "outputs", "run_id", "scope"})
    require(rank_seal["run_id"] == ranked["run_id"])
    require(rank_seal["configuration_hash"] == ranked["configuration_hash"])
    require(
        rank_seal["outputs"]
        == {
            f"{stage}/{name}": checksum
            for stage, record in ranked["stages"].items()
            for name, checksum in record["outputs"].items()
        }
    )
    software(analysis["software"], config["expected_analysis_head"])
    require(analysis["phase5_strategies"] is True and analysis["mode"] == "authorized-local-mock")
    require(analysis["resource_lock_hash"] == ranking["hpo_manifest_sha256"])
    require(analysis["input_sha256"] == tables["outputs"]["variants.parquet"])
    metrics = snapshot.metadata(run / "deliverables" / "sanitized_metrics.json")
    require(metrics["status"] == "completed_bounded_dry_run")
    require(metrics["coverage"] == assembly_manifest["coverage"])
    require(metrics["whole_genome_analysis_complete"] is False)
    linkage = metrics["sample_phenotype_linkage_confirmed"]
    require(type(linkage) is bool and linkage == declaration["confirmed_affected_sample"])
    require(
        analysis["hpo_terms_sha256"]
        == secure.sha256(
            json.dumps(
                snapshot.metadata(run / "prepare" / "phenotype.json")["explicit_hpo_ids"]
            ).encode()
        )
    )
    return {
        "paths": {
            **{name: run / "ingest" / "tables" / (name + ".parquet") for name in TABLES},
            "database": run / "analysis" / "workflow" / "pipeline" / "candidate_membership.duckdb",
        },
        "run_id": ranked["run_id"],
        "analysis_head": config["expected_analysis_head"],
        "expected_proband": config["expected_proband"],
        "source_sha256": original["source_sha256"],
        "plan_manifest_sha256": original["plan_manifest_sha256"],
        "assembly_context": contexts,
        "coverage": assembly_manifest["coverage"],
        "saved_run_declarations": {
            "confirmed_affected_sample": declaration["confirmed_affected_sample"],
            "sample_phenotype_linkage_confirmed": linkage,
            "sample_phenotype_linkage_independently_verified": False,
            "dataset_revision": declaration["dataset_revision"],
        },
        "source_binding": {
            "corpus_journal_sha256": config["corpus_journal_sha256"],
            "assembly_integrity_sha256": ranking["rehearsal_integrity_sha256"],
            "analysis_journal_sha256": snapshot.files[run / "run.json"][0],
            "source_sha256": original["source_sha256"],
            "plan_manifest_sha256": original["plan_manifest_sha256"],
            "original_inputs_reopened": False,
            "authenticated_completed_stage_artifacts": True,
        },
    }


def select(config_path: Path) -> None:
    require(
        all(
            os.environ.get(key, "").lower() in {"", "false", "0"}
            for key in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING")
        )
    )
    secure.outside_git(config_path)
    config_raw = secure.read_file(config_path)
    config = secure.strict_json(config_raw)
    require(
        set(config)
        == {
            "schema_version",
            "corpus_directory",
            "corpus_journal_sha256",
            "expected_analysis_head",
            "expected_proband",
            "output_directory",
            "confirmed_local_research_use",
        }
    )
    require(type(config["schema_version"]) is int and config["schema_version"] == 1)
    require(config["confirmed_local_research_use"] is True)
    require(
        digest(config["corpus_journal_sha256"]) and digest(config["expected_analysis_head"], 40)
    )
    require(type(config["expected_proband"]) is str and bool(config["expected_proband"]))
    source, output = (Path(config[key]) for key in ("corpus_directory", "output_directory"))
    for path in (source, output):
        require(path.is_absolute() and ".." not in path.parts)
        secure.outside_git(path)
    require(not source.is_relative_to(output) and not output.is_relative_to(source))
    require(not os.path.lexists(output))
    with secure.directory(source), secure.directory(output.parent):
        pass
    with Budget(output.parent) as budget:
        snapshot = Snapshot(budget)
        context = authenticate(config, snapshot)
        code = {
            Path(module.__file__).resolve() for module in (core, policy, secure, secure.validator)
        }
        code.add(Path(__file__).resolve())
        code_inputs = {path: secure.read_file(path) for path in code}
        csv_raw, audit = core.read_data(
            context, budget, snapshot.files[context["paths"]["variants"]][0]
        )
        require(audit["version"] == core.VERSION and audit["status"] == core.STATUS)
        secure.validator.validate_contents(
            csv_raw, config["expected_proband"], secure.validator.ORDER_POLICY
        )
        outputs = {"draft.csv": csv_raw, "audit.json": secure.json_bytes(audit)}
        manifest = {
            "schema_version": 1,
            "version": core.VERSION,
            "status": core.STATUS,
            "policy_version": policy.POLICY_VERSION,
            "artifact_sha256": {name: secure.sha256(raw) for name, raw in outputs.items()},
            "configuration_sha256": secure.sha256(config_raw),
            "source_binding": context["source_binding"],
            "expected_analysis_head": config["expected_analysis_head"],
            "code_sha256": {path.name: secure.sha256(raw) for path, raw in code_inputs.items()},
            "input_receipt": {
                "stage_artifact_count": len(snapshot.files),
                "stage_artifact_bytes": snapshot.bytes,
                "all_committed_stage_artifacts_authenticated": True,
            },
            "scientific_limits": {
                "whole_case_coverage_established": False,
                "causal_or_clinical_validity_established": False,
                "original_source_inputs_reread": False,
                "annotation_or_evidence_recomputed": False,
                "cache_supply_chain_independently_verified": False,
            },
        }
        outputs["manifest.json"] = secure.json_bytes(manifest)
        outputs["integrity_manifest.json"] = secure.json_bytes(
            {name: secure.sha256(raw) for name, raw in outputs.items()}
        )
        require(sum(map(len, outputs.values())) <= MAX_OUTPUT_BYTES)
        require(all(0 < len(raw) <= MAX_METADATA_BYTES for raw in outputs.values()))
        staging = Path(tempfile.mkdtemp(prefix=".track1-select-", dir=output.parent))
        try:
            for name, raw in outputs.items():
                descriptor = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            with secure.directory(staging) as descriptor:
                os.fsync(descriptor)
            snapshot.recheck()
            require(secure.read_file(config_path) == config_raw)
            for path, raw in code_inputs.items():
                require(secure.read_file(path) == raw)
            budget.check()
            secure.outside_git(output)
            with secure.directory(output.parent) as parent:
                secure.publish(staging, output)
                os.fsync(parent)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def main(argv: list[str] | None = None) -> int:
    parser = secure.PrivateArgumentParser(
        prog="track1_select.py", description=__doc__, allow_abbrev=False
    )
    parser.add_argument("--config", required=True, metavar="JSON")
    try:
        args = parser.parse_args(argv)
        select(Path(args.config))
        print(json.dumps({"status": core.STATUS, "analysis_rerun": False}, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt):
        sys.stderr.write(REFUSAL)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
