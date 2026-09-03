import pytest
from pydantic import BaseModel

from rare_disease_agent.llm.errors import LLMUnavailableError, UnsafeModelError
from rare_disease_agent.llm.ollama import OllamaBackend
from rare_disease_agent.llm.schemas import GenerationRequest
from rare_disease_agent.models.hardware import HardwareInfo


def hardware(*, available: float) -> HardwareInfo:
    return HardwareInfo(
        operating_system="macOS",
        architecture="arm64",
        cpu="Apple M2",
        total_memory_gb=16,
        available_memory_gb=available,
        unified_memory_gb=16,
        gpu="Apple integrated GPU (Metal)",
        metal_supported=True,
        free_disk_gb=100,
    )


def generation_request() -> GenerationRequest:
    return GenerationRequest(system_prompt="system", prompt="prompt")


class StructuredAnswer(BaseModel):
    action: str


def test_ollama_refuses_too_little_memory_before_http() -> None:
    backend = OllamaBackend(
        model="qwen2.5:7b-instruct-q4_K_M",
        hardware_provider=lambda: hardware(available=2),
    )

    with pytest.raises(UnsafeModelError, match="preflight refused"):
        backend.generate(generation_request())


def test_ollama_permits_loopback_only() -> None:
    with pytest.raises(ValueError, match="loopback"):
        OllamaBackend(model="qwen2.5:7b-instruct-q4_K_M", base_url="https://example.com")


def test_ollama_unavailable_is_typed(monkeypatch) -> None:
    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            import httpx

            raise httpx.ConnectError("synthetic unavailable")

    monkeypatch.setattr("rare_disease_agent.llm.ollama.httpx.Client", FailingClient)
    backend = OllamaBackend(
        model="qwen2.5:7b-instruct-q4_K_M",
        hardware_provider=lambda: hardware(available=12),
    )

    with pytest.raises(LLMUnavailableError, match="ensure Ollama is running"):
        backend.generate(generation_request())


def test_ollama_sends_schema_and_validates_structured_response(monkeypatch) -> None:
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "model": "qwen2.5:7b-instruct-q4_K_M",
                "message": {"content": '{"action":"stop"}'},
                "prompt_eval_count": 10,
                "eval_count": 4,
            }

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json):
            captured["url"] = url
            captured["payload"] = json
            return Response()

    monkeypatch.setattr("rare_disease_agent.llm.ollama.httpx.Client", Client)
    backend = OllamaBackend(
        model="qwen2.5:7b-instruct-q4_K_M",
        hardware_provider=lambda: hardware(available=12),
    )

    answer = backend.generate_structured(generation_request(), StructuredAnswer)

    assert answer.action == "stop"
    assert captured["url"].endswith("/api/chat")
    assert captured["payload"]["format"] == StructuredAnswer.model_json_schema()
    assert captured["payload"]["stream"] is False


def test_ollama_rechecks_live_memory_before_each_invocation(monkeypatch) -> None:
    available_memory = iter((12.0, 1.0))
    request_count = 0

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "ok"}}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            nonlocal request_count
            request_count += 1
            return Response()

    monkeypatch.setattr("rare_disease_agent.llm.ollama.httpx.Client", Client)
    backend = OllamaBackend(
        model="qwen2.5:7b-instruct-q4_K_M",
        hardware_provider=lambda: hardware(available=next(available_memory)),
    )

    assert backend.generate(generation_request()).content == "ok"
    with pytest.raises(UnsafeModelError, match="preflight refused"):
        backend.generate(generation_request())
    assert request_count == 1
