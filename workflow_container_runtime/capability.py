"""Explicit runtime capabilities shared by workflows and steps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self

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

    @classmethod
    def from_platform_config_path(cls, path: Path) -> Self:
        """Load supported typed capabilities from the platform config document.

        Args:
            path: Immutable JSON file named by `WORKFLOW_CAPABILITY_CONFIG_PATH`.

        Returns:
            Explicit supported runtime capability object.

        Raises:
            ValueError: If the document or supported capability payload is malformed.
        """

        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("workflow capability config must contain one JSON object")
        unsupported_name_set = set(payload).difference({"browser_vpn_runtime"})
        if unsupported_name_set:
            raise ValueError("unsupported workflow runtime capabilities: " + ", ".join(sorted(unsupported_name_set)))
        browser_vpn_runtime_payload = payload.get("browser_vpn_runtime")
        if browser_vpn_runtime_payload is None:
            return cls(browser=None)
        if not isinstance(browser_vpn_runtime_payload, dict):
            raise ValueError("browser_vpn_runtime config must contain one JSON object")
        return cls.model_validate(browser_vpn_runtime_payload)
