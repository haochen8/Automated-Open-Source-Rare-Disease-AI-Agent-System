#!/usr/bin/env python3
"""Repository privacy guard used by pre-commit and CI."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rare_disease_agent.privacy.guards import scan_paths  # noqa: E402


def _staged_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def _repository_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--staged", action="store_true", help="scan staged files")
    scope.add_argument("--all", action="store_true", help="scan tracked and unignored files")
    args = parser.parse_args()
    paths = _staged_paths() if args.staged else _repository_paths()
    findings = scan_paths(paths, repository_root=ROOT)
    if not findings:
        print(f"Privacy guard passed ({len(paths)} files scanned).")
        return 0
    print("Privacy guard rejected the following files:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.path}: {finding.reason}", file=sys.stderr)
    print("Move restricted data outside Git; never bypass this guard.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
