"""Private fixed-stage worker; never forward exception text or patient-bearing output."""

import os
import sys
from pathlib import Path

from rare_disease_agent.resource_management import atomic_json
from rare_disease_agent.workflows.assembly import read_json
from rare_disease_agent.workflows.coverage_batch import CoverageBatchInput, run_stage
from rare_disease_agent.workflows.partitioning import _private
from rare_disease_agent.workflows.phase5 import code_checksum

if __name__ == "__main__":
    os.umask(0o077)
    request = Path(sys.argv[1])
    try:
        _private(request)
        data = read_json(request)
        spec = CoverageBatchInput.model_validate(data["specification"])
        output = Path(data["output"])
        if (
            not (spec.confirmed_local_research_use and spec.confirmed_annotation_execution)
            or data["code_sha256"] != code_checksum()
            or output != spec.output / (data["stage"] + ".partial")
        ):
            raise ValueError("Stage authorization or identity mismatch")
        _private(output)
        run_stage(spec, data["stage"], output)
    except Exception as exc:
        # Only a class name, never a raw tool exception, source row or identifier.
        atomic_json(request.with_suffix(".failure.json"), {"type": type(exc).__name__})
        sys.exit(1)
