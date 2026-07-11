"""Strict model selection for low-level Codex execution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CodexRunnerConfig(BaseModel):
    """Require one explicit model and reasoning effort for every Codex call."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    model: str = Field(min_length=1)
    model_reasoning_effort: Literal["low", "medium", "high", "xhigh", "max", "ultra"]
