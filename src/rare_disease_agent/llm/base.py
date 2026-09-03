"""Provider-neutral LLM protocol."""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from rare_disease_agent.llm.schemas import GenerationRequest, GenerationResponse

StructuredT = TypeVar("StructuredT", bound=BaseModel)


@runtime_checkable
class LLMBackend(Protocol):
    """Minimal interface consumed by scientific agents."""

    @property
    def backend_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def generate(self, request: GenerationRequest) -> GenerationResponse: ...

    def generate_structured(
        self, request: GenerationRequest, response_model: type[StructuredT]
    ) -> StructuredT: ...

    async def agenerate(self, request: GenerationRequest) -> GenerationResponse: ...

    async def agenerate_structured(
        self, request: GenerationRequest, response_model: type[StructuredT]
    ) -> StructuredT: ...
