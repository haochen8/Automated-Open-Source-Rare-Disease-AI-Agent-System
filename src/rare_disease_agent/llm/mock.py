"""Deterministic scripted backend used by every automated agent test."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from rare_disease_agent.llm.errors import LLMUnavailableError, StructuredOutputError
from rare_disease_agent.llm.schemas import GenerationRequest, GenerationResponse

StructuredT = TypeVar("StructuredT", bound=BaseModel)


class MockLLMBackend:
    def __init__(
        self,
        structured_responses: Iterable[BaseModel | dict[str, Any] | str | Exception] = (),
        text_responses: Iterable[str | Exception] = (),
        *,
        model_name: str = "mock-variant-planner-v1",
    ) -> None:
        self._structured_responses = list(structured_responses)
        self._text_responses = list(text_responses)
        self._model_name = model_name
        self.requests: list[GenerationRequest] = []

    @property
    def backend_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.requests.append(request)
        if not self._text_responses:
            raise LLMUnavailableError("Mock backend has no scripted text response remaining.")
        response = self._text_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return GenerationResponse(
            content=response, model=self.model_name, backend=self.backend_name
        )

    def generate_structured(
        self, request: GenerationRequest, response_model: type[StructuredT]
    ) -> StructuredT:
        self.requests.append(request)
        if not self._structured_responses:
            raise LLMUnavailableError("Mock backend has no scripted structured response remaining.")
        response = self._structured_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        try:
            if isinstance(response, str):
                return response_model.model_validate_json(response)
            if isinstance(response, BaseModel):
                return response_model.model_validate(response.model_dump())
            return response_model.model_validate(response)
        except (ValidationError, ValueError) as exc:
            raise StructuredOutputError(
                f"Mock response failed {response_model.__name__} validation."
            ) from exc

    async def agenerate(self, request: GenerationRequest) -> GenerationResponse:
        return await asyncio.to_thread(self.generate, request)

    async def agenerate_structured(
        self, request: GenerationRequest, response_model: type[StructuredT]
    ) -> StructuredT:
        return await asyncio.to_thread(self.generate_structured, request, response_model)
