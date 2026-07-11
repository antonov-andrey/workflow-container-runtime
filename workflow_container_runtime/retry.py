"""Low-level execution retry policy shared by Codex lifecycle owners."""

from pydantic import BaseModel, ConfigDict, Field


class CodexExecutionRetryPolicy(BaseModel):
    """Limit low-level retries before one structured Codex output exists."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    attempt_limit: int = Field(ge=1)
