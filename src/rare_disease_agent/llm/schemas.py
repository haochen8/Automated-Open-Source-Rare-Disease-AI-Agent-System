"""Shared generation request/response schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    prompt: str
    messages: list[Message] = Field(default_factory=list)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=16_384)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_messages(self) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(message.model_dump() for message in self.messages)
        messages.append({"role": "user", "content": self.prompt})
        return messages


class GenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    model: str
    backend: str
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
