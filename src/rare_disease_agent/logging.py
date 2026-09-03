"""Structured logging with conservative sensitive-field redaction."""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

SENSITIVE_FRAGMENTS = (
    "patient",
    "phenotype",
    "genotype",
    "variant",
    "vcf",
    "token",
    "secret",
    "password",
    "api_key",
)


def redact_sensitive_fields(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Redact values whose keys may contain patient data or credentials."""

    del logger, method_name
    for key in tuple(event_dict):
        normalized = str(key).lower()
        if key != "event" and any(fragment in normalized for fragment in SENSITIVE_FRAGMENTS):
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(*, json_output: bool = True, level: int = logging.INFO) -> None:
    """Configure stdlib and structlog once for command-line execution."""

    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level, force=True)
    renderer = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive_fields,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**context: Any) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger().bind(**context)
