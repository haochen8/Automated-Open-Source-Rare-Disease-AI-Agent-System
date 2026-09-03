"""Provider-neutral language-model interfaces and local backends."""

from rare_disease_agent.llm.base import LLMBackend
from rare_disease_agent.llm.mock import MockLLMBackend
from rare_disease_agent.llm.ollama import OllamaBackend
from rare_disease_agent.llm.registry import BackendRegistry, default_registry
from rare_disease_agent.llm.schemas import GenerationRequest, GenerationResponse

__all__ = [
    "BackendRegistry",
    "GenerationRequest",
    "GenerationResponse",
    "LLMBackend",
    "MockLLMBackend",
    "OllamaBackend",
    "default_registry",
]
