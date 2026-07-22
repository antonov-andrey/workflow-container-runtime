"""Explicit runtime capabilities shared by workflows and steps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from workflow_container_contract import network_proxy_name_validate


class BrowserRuntimeCapability(BaseModel):
    """Expose run-local browser routing and profile publication endpoints."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    mcp_playwright_profile_source: str = Field(min_length=1)
    mcp_playwright_profile_writeback_candidate_url: str = Field(min_length=1)
    mcp_url: str = Field(min_length=1)


class NetworkProxyRuntimeCapability(BaseModel):
    """Expose exact run-local SOCKS endpoints by stable public proxy name."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        validate_default=True,
    )

    proxy_by_name_map: dict[str, str]

    @field_validator("proxy_by_name_map")
    @classmethod
    def proxy_by_name_map_validate(cls, proxy_by_name_map: dict[str, str]) -> dict[str, str]:
        """Validate every exact name and run-local SOCKS5 endpoint.

        Args:
            proxy_by_name_map: Candidate stable-name to endpoint map.

        Returns:
            Independently copied validated map.

        Raises:
            ValueError: If one name or endpoint is outside the runtime contract.
        """

        for network_proxy_name, proxy_url in proxy_by_name_map.items():
            network_proxy_name_validate(network_proxy_name)
            split_proxy_url = urlsplit(proxy_url)
            try:
                proxy_port = split_proxy_url.port
            except ValueError as exc:
                raise ValueError("network proxy URLs must contain a valid port") from exc
            if (
                split_proxy_url.scheme != "socks5"
                or split_proxy_url.hostname is None
                or proxy_port is None
                or split_proxy_url.username is not None
                or split_proxy_url.password is not None
                or split_proxy_url.path not in {"", "/"}
                or split_proxy_url.query
                or split_proxy_url.fragment
            ):
                raise ValueError("network proxy URLs must be credential-free socks5://host:port endpoints")
        return dict(proxy_by_name_map)

    def proxy_url_get(self, network_proxy_name: str | None) -> str | None:
        """Return the exact endpoint selected explicitly by one consumer.

        Args:
            network_proxy_name: Exact source-owned proxy name, or `None` for direct egress.

        Returns:
            Exact SOCKS5 URL, or `None` for direct egress.

        Raises:
            ValueError: If the supplied non-null name is invalid or unavailable.
        """

        if network_proxy_name is None:
            return None
        network_proxy_name_validate(network_proxy_name)
        try:
            return self.proxy_by_name_map[network_proxy_name]
        except KeyError as exc:
            raise ValueError(f"network proxy is unavailable: {network_proxy_name}") from exc


class WorkflowRuntimeCapability(BaseModel):
    """Explicit optional capabilities available to one workflow call."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    browser: BrowserRuntimeCapability | None
    network_proxy: NetworkProxyRuntimeCapability

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
        unsupported_name_set = set(payload).difference({"browser_runtime", "network_proxy"})
        if unsupported_name_set:
            raise ValueError("unsupported workflow runtime capabilities: " + ", ".join(sorted(unsupported_name_set)))
        network_proxy_payload = payload.get("network_proxy")
        if not isinstance(network_proxy_payload, dict):
            raise ValueError("network_proxy config must contain one JSON object")
        browser_runtime_payload = payload.get("browser_runtime")
        if browser_runtime_payload is None:
            browser = None
        else:
            if not isinstance(browser_runtime_payload, dict) or set(browser_runtime_payload) != {"browser"}:
                raise ValueError("browser_runtime config must contain exactly one browser object")
            browser_payload = browser_runtime_payload["browser"]
            if not isinstance(browser_payload, dict):
                raise ValueError("browser_runtime browser config must contain one JSON object")
            browser = BrowserRuntimeCapability.model_validate(browser_payload)
        return cls(
            browser=browser,
            network_proxy=NetworkProxyRuntimeCapability.model_validate(network_proxy_payload),
        )
