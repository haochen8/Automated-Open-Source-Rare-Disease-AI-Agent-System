"""Versioned prompt loading and hashing."""

from __future__ import annotations

import hashlib
from importlib.resources import files

PROMPT_VERSION = "variant-filtering-v1"


def load_variant_filtering_prompt() -> str:
    return (
        files("rare_disease_agent.prompts")
        .joinpath("variant_filtering_v1.txt")
        .read_text(encoding="utf-8")
    )


def variant_filtering_prompt_hash() -> str:
    return hashlib.sha256(load_variant_filtering_prompt().encode()).hexdigest()
