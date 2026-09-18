"""Check this worktree and bind completion evidence to its engineering contents."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = 1
POLICY = 1
EVIDENCE = ".verification"
# Path-only refusal before opening files; the product privacy scanner is unchanged.
BLOCKED_DIRS = {
    "private_data",
    "private_runs",
    "private_cache",
    "cache",
    "models",
    "secrets",
    "tokens",
    "runs",
    "results",
    ".git",
    ".venv",
    "venv",
    ".cache",
    ".ollama",
    "qdrant_storage",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "build",
    "htmlcov",
}
BLOCKED_SUFFIXES = (
    ".vcf",
    ".vcf.gz",
    ".gvcf",
    ".gvcf.gz",
    ".bcf",
    ".bam",
    ".bai",
    ".cram",
    ".crai",
    ".fastq",
    ".fastq.gz",
    ".fq",
    ".fq.gz",
    ".ped",
    ".tbi",
    ".parquet",
    ".duckdb",
    ".duckdb.wal",
    ".resource",
    ".tar.gz",
    ".zip",
    ".docx",
    ".pdf",
    ".pem",
    ".key",
)
ROOT_FILES = {
    "Makefile",
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
    "LICENSE",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".env.example",
}
IMPORT_PROBE = """
import pathlib
import rare_disease_agent
import sys

expected = pathlib.Path(sys.argv[1]).resolve() / "src" / "rare_disease_agent"
origin = pathlib.Path(rare_disease_agent.__file__).resolve()
locations = [pathlib.Path(path).resolve() for path in rare_disease_agent.__path__]
if origin != expected / "__init__.py" or locations != [expected]:
    raise SystemExit("Import identity mismatch: " + str(origin))
print("Verified import: " + str(origin), flush=True)
"""


class IncompleteError(ValueError):
    """Repository state or evidence cannot safely establish completion."""


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", *arguments], cwd=root, check=True, stdout=subprocess.PIPE).stdout


def repository_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    observed = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if observed != root:
        raise IncompleteError("Verifier must be in the repository's scripts directory")
    return root


def allowed_path(name: str) -> None:
    parts = Path(name).parts
    directories = set(parts[:-1])
    if name.startswith("src/rare_disease_agent/models/") and name.endswith(".py"):
        directories.discard("models")
    if (
        not parts
        or Path(name).is_absolute()
        or ".." in parts
        or directories & BLOCKED_DIRS
        or parts[0] in BLOCKED_DIRS | {EVIDENCE}
        or parts[0].startswith(".coverage")
        or (parts[0] == "data" and name != "data/README.md")
        or name.lower().endswith(BLOCKED_SUFFIXES)
        or any(part.startswith(".env") and part != ".env.example" for part in parts)
    ):
        raise IncompleteError(f"Refusing prohibited/generated path: {name!r}")


def relevant_untracked(name: str) -> bool:
    path = Path(name)
    parts = path.parts
    return (
        (parts[0] in {"src", "scripts", "tests"} and path.suffix == ".py")
        or ((len(parts) == 1 or parts[0] == "docs") and path.suffix == ".md")
        or (parts[0] == "configs" and path.suffix in {".yaml", ".yml", ".example"})
        or (name.startswith(".github/workflows/") and path.suffix in {".yaml", ".yml"})
        or name in ROOT_FILES
    )


def file_identity(root: Path, name: str) -> tuple:
    # Check parents as well as the final component; never traverse a directory symlink.
    path = root / name
    parent = root
    for part in Path(name).parts[:-1]:
        parent /= part
        if parent.is_symlink():
            raise IncompleteError(f"Symlink ancestor: {name!r}")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return ("missing",)
    if stat.S_ISLNK(mode):
        return ("symlink", os.readlink(path))
    if not stat.S_ISREG(mode):
        raise IncompleteError(f"Unsupported file type: {name!r}")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return ("file", bool(mode & 0o111), digest)


def fingerprint(root: Path) -> str:
    entries = {}
    for record in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if record:
            entry, name = record.split(b"\t", 1)
            entries.setdefault(os.fsdecode(name), []).append(entry.decode("ascii"))
    untracked = {
        os.fsdecode(name)
        for name in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if name
    }
    untracked = {name for name in untracked if Path(name).parts[0] != EVIDENCE}
    # Validate the entire inventory before opening any of its files.
    for name in entries.keys() | untracked:
        allowed_path(name)
        if name in untracked and not relevant_untracked(name):
            raise IncompleteError(f"Unclassified unignored file: {name!r}")
    manifest = [
        [name, sorted(entries.get(name, [])), file_identity(root, name)]
        for name in sorted(entries.keys() | untracked)
    ]
    payload = json.dumps([POLICY, manifest], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def evidence_path(root: Path, *, create: bool = False) -> Path:
    if git(root, "ls-files", "-z", "--", EVIDENCE):
        raise IncompleteError("Evidence directory must not be tracked")
    directory = root / EVIDENCE
    if directory.is_symlink():
        raise IncompleteError("Evidence directory must not be a symlink")
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    if not directory.is_dir():
        raise IncompleteError("Evidence directory is missing or not a directory")
    path = directory / "evidence.json"
    if path.is_symlink():
        raise IncompleteError("Evidence record must not be a symlink")
    return path


def publish(root: Path, record: dict) -> None:
    path = evidence_path(root, create=True)
    descriptor, temporary = tempfile.mkstemp(prefix="evidence-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(record, output, sort_keys=True, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checks(python: str) -> list[tuple[str, list[str]]]:
    return [
        (
            "pytest with coverage",
            [python, "-m", "pytest", "--cov=rare_disease_agent", "--cov-report=term-missing"],
        ),
        ("Ruff lint", [python, "-m", "ruff", "check", "."]),
        ("Ruff formatting", [python, "-m", "ruff", "format", "--check", "."]),
        ("privacy", [python, "scripts/privacy_guard.py", "--all"]),
        ("dependency consistency", [python, "-m", "pip", "check"]),
        ("package build", [python, "-m", "hatchling", "build"]),
        ("unstaged whitespace", ["git", "diff", "--check"]),
        ("staged whitespace", ["git", "diff", "--cached", "--check"]),
    ]


def execute(command: list[str], root: Path, environment: dict[str, str]) -> None:
    subprocess.run(command, cwd=root, env=environment, check=True)


def now() -> str:
    return datetime.now(UTC).isoformat()


def read_evidence(root: Path) -> dict:
    path = evidence_path(root)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "r") as source:
        record = json.loads(source.read(65537))
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise IncompleteError("Missing or incompatible evidence schema")
    required = {
        "policy",
        "status",
        "branch",
        "head",
        "fingerprint_start",
        "fingerprint_end",
        "python",
        "python_version",
        "import_identity",
        "checks",
        "started_at",
        "ended_at",
        "error",
    }
    if not required <= record.keys() or record["status"] not in {"PASS", "FAIL", "INCOMPLETE"}:
        raise IncompleteError("Malformed evidence")
    if not isinstance(record["python"], str) or not isinstance(record["checks"], list):
        raise IncompleteError("Malformed execution identity")
    if not record["python"] or not isinstance(record["python_version"], str):
        raise IncompleteError("Missing interpreter identity")
    for field in ("branch", "head", "fingerprint_start", "fingerprint_end", "import_identity"):
        if record[field] is not None and not isinstance(record[field], str):
            raise IncompleteError("Malformed repository identity")
    if type(record["policy"]) is not int:
        raise IncompleteError("Malformed fingerprint policy")
    datetime.fromisoformat(record["started_at"])
    if record["ended_at"] is not None:
        datetime.fromisoformat(record["ended_at"])
    expected = checks(record["python"])
    if len(record["checks"]) != len(expected):
        raise IncompleteError("Incomplete check inventory")
    for item, (name, command) in zip(record["checks"], expected, strict=True):
        if (
            not isinstance(item, dict)
            or item.get("name") != name
            or item.get("command") != command
            or item.get("status") not in {"PASS", "FAIL", "INCOMPLETE", "NOT_RUN"}
            or "returncode" not in item
        ):
            raise IncompleteError("Malformed check result")
    if record["status"] == "PASS" and (
        not isinstance(record["fingerprint_start"], str)
        or len(record["fingerprint_start"]) != 64
        or record["fingerprint_start"] != record["fingerprint_end"]
        or not record["head"]
        or not record["ended_at"]
        or record["error"] is not None
        or record["import_identity"] != "src/rare_disease_agent/__init__.py"
        or any(item["status"] != "PASS" or item["returncode"] != 0 for item in record["checks"])
    ):
        raise IncompleteError("PASS is not supported by complete evidence")
    return record


def status(root: Path | None = None) -> int:
    try:
        root = root or repository_root()
        record = read_evidence(root)
        current = fingerprint(root)
        head = os.fsdecode(git(root, "rev-parse", "HEAD")).strip()
        state = record["status"]
        if record["policy"] != POLICY or (
            record["fingerprint_end"] is not None
            and (current != record["fingerprint_end"] or head != record["head"])
        ):
            state = "STALE"
        branch = os.fsdecode(git(root, "rev-parse", "--abbrev-ref", "HEAD")).strip()
        if record["branch"] is not None and branch != record["branch"]:
            print("Branch name changed; contents and HEAD determine validity.")
        print(f"{state}: recorded execution {record['status']}; current fingerprint {current}")
        return 0 if state == "PASS" else 1
    except Exception as exc:
        print(f"INCOMPLETE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    root = None
    record = None
    active = None
    code = 0
    phase = "preflight"
    try:
        if sys.version_info < (3, 11):  # noqa: UP036 - diagnose unsupported selected interpreter
            raise IncompleteError("Python 3.11 or newer is required")
        root = repository_root()
        python = os.path.abspath(sys.executable)  # Preserve virtualenv selection.
        record = {
            "schema": SCHEMA,
            "policy": POLICY,
            "status": "INCOMPLETE",
            "branch": None,
            "head": None,
            "fingerprint_start": None,
            "fingerprint_end": None,
            "python": python,
            "python_version": sys.version.split()[0],
            "import_identity": None,
            "started_at": now(),
            "ended_at": None,
            "error": None,
            "checks": [
                {"name": name, "command": command, "status": "NOT_RUN", "returncode": None}
                for name, command in checks(python)
            ],
        }
        publish(root, record)  # Revoke previous PASS before inventory, imports, or checks.
        record["head"] = os.fsdecode(git(root, "rev-parse", "HEAD")).strip()
        record["branch"] = os.fsdecode(git(root, "rev-parse", "--abbrev-ref", "HEAD")).strip()
        record["fingerprint_start"] = fingerprint(root)
        print(f"Repository: {root}\nPython: {python}\nHEAD: {record['head']}", flush=True)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "src")
        environment["PYTEST_ADDOPTS"] = ""
        phase = "import identity"
        execute([python, "-c", IMPORT_PROBE, str(root)], root, environment)
        record["import_identity"] = "src/rare_disease_agent/__init__.py"
        for active in record["checks"]:
            phase = active["name"]
            print(f"\nChecking {phase}: {shlex.join(active['command'])}", flush=True)
            active["status"] = "INCOMPLETE"
            execute(active["command"], root, environment)
            active.update(status="PASS", returncode=0)
        active = None
        record["status"] = "PASS"
    except BaseException as exc:
        if not isinstance(exc, (Exception, KeyboardInterrupt)):
            raise
        code = 130 if isinstance(exc, KeyboardInterrupt) else 1
        outcome = "INCOMPLETE"
        if isinstance(exc, subprocess.CalledProcessError):
            code = exc.returncode if exc.returncode > 0 else 128 - exc.returncode
            if phase != "preflight" and exc.returncode > 0:
                outcome = "FAIL"
        if record is not None:
            record["status"] = outcome
            record["error"] = {"phase": phase, "kind": type(exc).__name__}
            if active is not None:
                active.update(status=outcome, returncode=code)
        print(f"{outcome}: {phase} ({type(exc).__name__}, exit {code}).", file=sys.stderr)
    finally:
        if record is not None:
            if record["fingerprint_start"] is not None:
                try:
                    record["fingerprint_end"] = fingerprint(root)
                    final_head = os.fsdecode(git(root, "rev-parse", "HEAD")).strip()
                    if (
                        record["fingerprint_end"] != record["fingerprint_start"]
                        or final_head != record["head"]
                    ):
                        record["status"] = "FAIL"
                        record["error"] = {"phase": "final identity", "kind": "RepositoryChanged"}
                        code = code or 1
                        print("Repository contents/index or HEAD changed during verification.")
                    else:
                        print(
                            "Repository contents and index unchanged by verification.", flush=True
                        )
                except Exception as exc:
                    record["status"] = "INCOMPLETE"
                    record["error"] = {"phase": "final identity", "kind": type(exc).__name__}
                    code = code or 1
            record["ended_at"] = now()
            try:
                publish(root, record)
            except Exception as exc:
                code = code or 1
                print(f"INCOMPLETE: evidence publication failed ({type(exc).__name__}).")
    if code:
        print("Verification not complete; no current PASS was established.")
    else:
        print("Verification passed: all 8 checks; current PASS evidence recorded.")
    return code


if __name__ == "__main__":
    if sys.argv[1:] == ["--status"]:
        sys.exit(status())
    if sys.argv[1:]:
        sys.exit("Usage: verify.py [--status]")
    sys.exit(main())
