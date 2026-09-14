"""Supervised private worker. Never print source records or exception details."""

import os
import sys
import traceback
from pathlib import Path

from rare_disease_agent.resource_management import atomic_json
from rare_disease_agent.workflows.authorized import _outside_git
from rare_disease_agent.workflows.phase5 import phase5_run
from rare_disease_agent.workflows.phase5_cli import load_spec

if __name__ == "__main__":
    os.umask(0o077)
    spec = None
    try:
        spec = load_spec(Path(sys.argv[1]))
        phase5_run(spec)
    except Exception as exc:
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
            atomic_json(spec.output.parent / (spec.run_id + "-failure.json"), {"errors": chain})
        sys.exit(1)
