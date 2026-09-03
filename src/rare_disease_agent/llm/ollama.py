"""Optional local-only Ollama backend with mandatory memory preflight."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from rare_disease_agent.llm.errors import (
    LLMUnavailableError,
    StructuredOutputError,
    UnsafeModelError,
)
from rare_disease_agent.llm.schemas import GenerationRequest, GenerationResponse
from rare_disease_agent.models import check_model_suitability, inspect_hardware
from rare_disease_agent.models.hardware import HardwareInfo

StructuredT = TypeVar("StructuredT", bound=BaseModel)
HardwareProvider = Callable[[], HardwareInfo]


class OllamaBackend:
    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 120,
        allow_oversized: bool = False,
        hardware_provider: HardwareProvider = inspect_hardware,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("Phase 2 Ollama backend permits local loopback endpoints only.")
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._allow_oversized = allow_oversized
        self._hardware_provider = hardware_provider

    @property
    def backend_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    def _preflight(self) -> None:
        suitability = check_model_suitability(
            self.model_name,
            self._hardware_provider(),
            allow_oversized=self._allow_oversized,
        )
        if not suitability.allowed:
            raise UnsafeModelError(
                "Local model preflight refused execution: " + " ".join(suitability.reasons)
            )

    def _call(
        self, request: GenerationRequest, *, response_model: type[BaseModel] | None = None
    ) -> GenerationResponse:
        self._preflight()
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": request.as_messages(),
            "stream": False,
            "keep_alive": 0,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        if response_model is not None:
            payload["format"] = response_model.model_json_schema()
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(f"{self._base_url}/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMUnavailableError(
                "Local Ollama request failed; ensure Ollama is running and the model is installed."
            ) from exc
        try:
            content = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMUnavailableError("Ollama returned an unexpected response shape.") from exc
        return GenerationResponse(
            content=content,
            model=str(body.get("model", self.model_name)),
            backend=self.backend_name,
            prompt_tokens=body.get("prompt_eval_count"),
            completion_tokens=body.get("eval_count"),
        )

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        return self._call(request)

    def generate_structured(
        self, request: GenerationRequest, response_model: type[StructuredT]
    ) -> StructuredT:
        response = self._call(request, response_model=response_model)
        try:
            return response_model.model_validate_json(response.content)
        except (ValidationError, ValueError) as exc:
            raise StructuredOutputError(
                f"Ollama response failed {response_model.__name__} validation."
            ) from exc

    async def agenerate(self, request: GenerationRequest) -> GenerationResponse:
        return await asyncio.to_thread(self.generate, request)

    async def agenerate_structured(
        self, request: GenerationRequest, response_model: type[StructuredT]
    ) -> StructuredT:
        return await asyncio.to_thread(self.generate_structured, request, response_model)
