"""Strict model selection for low-level Codex execution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class CodexRunnerConfig(BaseModel):
    """Require one explicit model and reasoning effort for every Codex call."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    model: Literal["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"]
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"]
