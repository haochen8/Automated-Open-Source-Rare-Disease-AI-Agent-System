"""Fixed private corpus worker; no annotation execution or raw exception output."""

import os
import sys
from pathlib import Path

from rare_disease_agent.resource_management import atomic_json
from rare_disease_agent.workflows.assembly import read_json
from rare_disease_agent.workflows.corpus import CorpusInput, run_stage, safe_path
from rare_disease_agent.workflows.partitioning import _private
from rare_disease_agent.workflows.phase5 import code_checksum

if __name__ == "__main__":
    os.umask(0o077)
    request = Path(sys.argv[1])
    try:
        _private(request)
        safe_path(request)
        data = read_json(request)
        spec = CorpusInput.model_validate(data["specification"])
        output = Path(data["output"])
        if data["code_sha256"] != code_checksum() or output != spec.output / (
            data["stage"] + ".partial"
        ):
            raise ValueError("Corpus worker identity mismatch")
        run_stage(spec, data["stage"], output)
    except Exception as exc:
        atomic_json(request.with_suffix(".failure.json"), {"type": type(exc).__name__})
        sys.exit(1)
