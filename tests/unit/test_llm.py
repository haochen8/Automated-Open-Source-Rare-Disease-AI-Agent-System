import asyncio

import pytest
from pydantic import BaseModel

from rare_disease_agent.llm.base import LLMBackend
from rare_disease_agent.llm.errors import (
    BackendNotFoundError,
    LLMUnavailableError,
    StructuredOutputError,
)
from rare_disease_agent.llm.mock import MockLLMBackend
from rare_disease_agent.llm.registry import BackendRegistry
from rare_disease_agent.llm.schemas import GenerationRequest


class Answer(BaseModel):
    value: int


def request() -> GenerationRequest:
    return GenerationRequest(system_prompt="system", prompt="prompt")


def test_mock_backend_satisfies_protocol_and_validates_structured_output() -> None:
    backend = MockLLMBackend(structured_responses=[{"value": 7}])

    assert isinstance(backend, LLMBackend)
    assert backend.generate_structured(request(), Answer) == Answer(value=7)
    assert len(backend.requests) == 1


def test_mock_backend_rejects_malformed_json() -> None:
    backend = MockLLMBackend(structured_responses=["not-json"])

    with pytest.raises(StructuredOutputError, match="Answer"):
        backend.generate_structured(request(), Answer)


def test_mock_backend_reports_exhausted_script() -> None:
    with pytest.raises(LLMUnavailableError, match="no scripted"):
        MockLLMBackend().generate_structured(request(), Answer)


def test_mock_async_structured_interface() -> None:
    backend = MockLLMBackend(structured_responses=[{"value": 9}])

    result = asyncio.run(backend.agenerate_structured(request(), Answer))

    assert result.value == 9


def test_backend_registry_create_and_errors() -> None:
    registry = BackendRegistry()
    registry.register("mock", MockLLMBackend)

    assert isinstance(registry.create("MOCK"), MockLLMBackend)
    assert registry.available() == ("mock",)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("mock", MockLLMBackend)
    with pytest.raises(BackendNotFoundError, match="available backends: mock"):
        registry.create("missing")
