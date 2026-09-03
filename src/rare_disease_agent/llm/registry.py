"""Backend registry keeps workflow construction provider-neutral."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rare_disease_agent.llm.base import LLMBackend
from rare_disease_agent.llm.errors import BackendNotFoundError
from rare_disease_agent.llm.mock import MockLLMBackend
from rare_disease_agent.llm.ollama import OllamaBackend

BackendFactory = Callable[..., LLMBackend]


class BackendRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, BackendFactory] = {}

    def register(self, name: str, factory: BackendFactory, *, replace: bool = False) -> None:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("Backend name cannot be empty.")
        if normalized in self._factories and not replace:
            raise ValueError(f"Backend already registered: {normalized}")
        self._factories[normalized] = factory

    def create(self, name: str, **kwargs: Any) -> LLMBackend:
        normalized = name.strip().lower()
        try:
            factory = self._factories[normalized]
        except KeyError as exc:
            available = ", ".join(self.available()) or "none"
            raise BackendNotFoundError(
                f"Unknown LLM backend {name!r}; available backends: {available}."
            ) from exc
        return factory(**kwargs)

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


default_registry = BackendRegistry()
default_registry.register("mock", MockLLMBackend)
default_registry.register("ollama", OllamaBackend)
