"""Explicit runtime capabilities shared by workflows and steps."""

from pydantic import BaseModel, ConfigDict, Field


class BrowserRuntimeCapability(BaseModel):
    """Expose run-local browser routing and profile publication endpoints."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    mcp_playwright_profile_source: str = Field(min_length=1)
    mcp_playwright_profile_writeback_candidate_url: str = Field(min_length=1)
    mcp_url: str = Field(min_length=1)


class WorkflowRuntimeCapability(BaseModel):
    """Explicit optional capabilities available to one workflow call."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    browser: BrowserRuntimeCapability | None
