"""Explicit runtime capabilities shared by workflows and steps."""

from pydantic import BaseModel, ConfigDict, Field


class BrowserRuntimeCapability(BaseModel):
    """Configured browser runtime endpoint available to one call."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    mcp_url: str = Field(min_length=1)


class WorkflowRuntimeCapability(BaseModel):
    """Explicit optional capabilities available to one workflow call."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    browser: BrowserRuntimeCapability | None
