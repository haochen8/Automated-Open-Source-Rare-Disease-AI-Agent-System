"""Stable LLM error taxonomy for workflow failure handling."""


class LLMError(RuntimeError):
    """Base class for backend failures safe to handle at workflow boundaries."""


class BackendNotFoundError(LLMError):
    pass


class LLMUnavailableError(LLMError):
    pass


class StructuredOutputError(LLMError):
    pass


class UnsafeModelError(LLMError):
    pass
