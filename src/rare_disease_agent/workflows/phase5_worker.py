"""Supervised private worker. Never print source records or exception details."""

import os
import sys
import traceback
from pathlib import Path
from uuid import UUID

from rare_disease_agent.resource_management import atomic_json
from rare_disease_agent.workflows.authorized import _outside_git
from rare_disease_agent.workflows.phase5 import phase5_run
from rare_disease_agent.workflows.phase5_cli import load_spec
from rare_disease_agent.workflows.phase5_diagnostics import (
    WORKER_IDENTITY_MISMATCH,
    WORKER_STAGE_INTEGRITY,
    Phase5IdentityMismatch,
    Phase5StageIntegrityError,
)

if __name__ == "__main__":
    os.umask(0o077)
    spec = None
    try:
        # Canonical UUID hex is safe in a filename; never use an unchecked command argument.
        invocation_id = UUID(sys.argv[2]).hex
        spec = load_spec(Path(sys.argv[1]))
        phase5_run(spec)
    except Exception as exc:
        diagnostic = exc.codes if isinstance(exc, Phase5IdentityMismatch) else None
        stage_error = (
            Phase5StageIntegrityError(exc.stage, exc.reason)
            if isinstance(exc, Phase5StageIntegrityError)
            else None
        )
        if spec is not None:
            _outside_git(spec.output.resolve())
            chain = []
            while exc is not None:
                chain.append(
                    {
                        "type": type(exc).__name__,
                        "frames": [
                            {
                                "module": Path(frame.filename).name,
                                "function": frame.name,
                                "line": frame.lineno,
                            }
                            for frame in traceback.extract_tb(exc.__traceback__)
                        ],
                    }
                )
                exc = exc.__context__
            receipt = {"invocation_id": invocation_id, "errors": chain}
            if diagnostic is not None:
                receipt["identity_mismatch"] = Phase5IdentityMismatch(diagnostic).codes
            if stage_error is not None:
                receipt["stage_integrity"] = {
                    "stage": stage_error.stage,
                    "reason": stage_error.reason,
                }
            atomic_json(spec.output.parent / ("phase5-" + invocation_id + "-failure.json"), receipt)
        sys.exit(
            WORKER_IDENTITY_MISMATCH
            if diagnostic is not None
            else WORKER_STAGE_INTEGRITY
            if stage_error is not None
            else 1
        )
