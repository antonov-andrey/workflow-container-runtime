"""Run-local Playwright profile routing, leasing, and candidate publication."""

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
import re
from threading import Lock
from types import TracebackType
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict

from workflow_container_runtime.capability import BrowserRuntimeCapability, WorkflowRuntimeCapability

MCP_PLAYWRIGHT_PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?")


def mcp_playwright_profile_name_validate(value: str) -> str:
    """Require the exact safe single-component name accepted by the browser router.

    Args:
        value: Candidate logical or physical profile name.

    Returns:
        The accepted unchanged profile name.

    Raises:
        ValueError: If the browser router would reject the name.
    """

    if MCP_PLAYWRIGHT_PROFILE_NAME_PATTERN.fullmatch(value) is None:
        raise ValueError("profile value must be a safe profile name")
    return value


class HttpResponse(Protocol):
    """Describe the standard-library response surface used by candidate publication."""

    status: int

    def __enter__(self) -> "HttpResponse":
        """Enter the response context."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Leave the response context."""


class McpPlaywrightProfileRoute(BaseModel):
    """Carry phase-specific capabilities under one physical profile lease."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    action_runtime_capability: WorkflowRuntimeCapability
    mcp_playwright_profile: str | None
    verification_runtime_capability: WorkflowRuntimeCapability


class McpPlaywrightProfileRuntime:
    """Own synchronous profile leases and platform candidate publication for one run."""

    def __init__(self, *, urlopen: Callable[[Request], HttpResponse] = urlopen) -> None:
        """Initialize an empty profile lock map and HTTP request boundary.

        Args:
            urlopen: Standard-library-compatible HTTP request callable.
        """

        self._lock_by_profile_map: dict[str, Lock] = {}
        self._lock_map_guard = Lock()
        self._urlopen = urlopen

    def lease(
        self,
        *,
        mcp_playwright_profile: str | None,
        mcp_playwright_profile_source: str | None,
        runtime_capability: WorkflowRuntimeCapability,
    ) -> AbstractContextManager[McpPlaywrightProfileRoute]:
        """Lease one physical target and expose action and verification routes.

        Args:
            mcp_playwright_profile: Exact physical target profile, or `None` for isolated execution.
            mcp_playwright_profile_source: Exact physical source copied before each action connection.
            runtime_capability: Complete capability supplied for the current workflow run.

        Returns:
            Context manager yielding phase-specific capabilities protected by the target lease.

        Raises:
            RuntimeError: If a named profile has no browser capability.
            ValueError: If the source and target relationship is invalid.
        """

        return self._lease(
            mcp_playwright_profile=mcp_playwright_profile,
            mcp_playwright_profile_source=mcp_playwright_profile_source,
            runtime_capability=runtime_capability,
        )

    @contextmanager
    def _lease(
        self,
        *,
        mcp_playwright_profile: str | None,
        mcp_playwright_profile_source: str | None,
        runtime_capability: WorkflowRuntimeCapability,
    ) -> Generator[McpPlaywrightProfileRoute]:
        """Implement one physical target lease context."""

        if mcp_playwright_profile_source is not None and mcp_playwright_profile is None:
            raise ValueError("Playwright profile source requires a target profile")
        if mcp_playwright_profile is not None:
            mcp_playwright_profile_name_validate(mcp_playwright_profile)
        if mcp_playwright_profile_source is not None:
            mcp_playwright_profile_name_validate(mcp_playwright_profile_source)
        if mcp_playwright_profile_source == mcp_playwright_profile and mcp_playwright_profile is not None:
            raise ValueError("Playwright profile source and target must differ")
        browser = runtime_capability.browser
        if mcp_playwright_profile is not None and browser is None:
            raise RuntimeError("configured Playwright profile requires a browser capability")
        route = self._route_get(
            browser=browser,
            mcp_playwright_profile=mcp_playwright_profile,
            mcp_playwright_profile_source=mcp_playwright_profile_source,
            runtime_capability=runtime_capability,
        )
        if mcp_playwright_profile is None:
            yield route
            return
        with self._lock_map_guard:
            profile_lock = self._lock_by_profile_map.setdefault(mcp_playwright_profile, Lock())
        with profile_lock:
            yield route

    def writeback_candidate_publish(self, route: McpPlaywrightProfileRoute) -> None:
        """Publish one named profile as the run's latest writeback candidate.

        Args:
            route: Current leased profile route after successful semantic verification.

        Raises:
            RuntimeError: If the platform endpoint does not return HTTP 204.
        """

        if route.mcp_playwright_profile is None:
            return
        browser = route.action_runtime_capability.browser
        if browser is None:
            raise RuntimeError("configured Playwright profile requires a browser capability")
        candidate_url = self._url_get(
            base_url=browser.mcp_playwright_profile_writeback_candidate_url,
            mcp_playwright_profile=route.mcp_playwright_profile,
            mcp_playwright_profile_source=None,
        )
        request = Request(candidate_url, data=b"", method="POST")
        with self._urlopen(request) as response:
            if response.status != 204:
                raise RuntimeError(f"Playwright profile candidate endpoint returned {response.status}; expected 204")

    def _route_get(
        self,
        *,
        browser: BrowserRuntimeCapability | None,
        mcp_playwright_profile: str | None,
        mcp_playwright_profile_source: str | None,
        runtime_capability: WorkflowRuntimeCapability,
    ) -> McpPlaywrightProfileRoute:
        """Build immutable phase-specific capability snapshots."""

        if browser is None or mcp_playwright_profile is None:
            return McpPlaywrightProfileRoute(
                action_runtime_capability=runtime_capability,
                mcp_playwright_profile=mcp_playwright_profile,
                verification_runtime_capability=runtime_capability,
            )
        action_browser = BrowserRuntimeCapability(
            mcp_playwright_profile_source=browser.mcp_playwright_profile_source,
            mcp_playwright_profile_writeback_candidate_url=browser.mcp_playwright_profile_writeback_candidate_url,
            mcp_url=self._url_get(
                base_url=browser.mcp_url,
                mcp_playwright_profile=mcp_playwright_profile,
                mcp_playwright_profile_source=mcp_playwright_profile_source,
            ),
        )
        verification_browser = BrowserRuntimeCapability(
            mcp_playwright_profile_source=browser.mcp_playwright_profile_source,
            mcp_playwright_profile_writeback_candidate_url=browser.mcp_playwright_profile_writeback_candidate_url,
            mcp_url=self._url_get(
                base_url=browser.mcp_url,
                mcp_playwright_profile=mcp_playwright_profile,
                mcp_playwright_profile_source=None,
            ),
        )
        return McpPlaywrightProfileRoute(
            action_runtime_capability=WorkflowRuntimeCapability(browser=action_browser),
            mcp_playwright_profile=mcp_playwright_profile,
            verification_runtime_capability=WorkflowRuntimeCapability(browser=verification_browser),
        )

    def _url_get(
        self,
        *,
        base_url: str,
        mcp_playwright_profile: str,
        mcp_playwright_profile_source: str | None,
    ) -> str:
        """Replace router-owned query values while preserving all caller-owned values."""

        split_url = urlsplit(base_url)
        query_item_list = [
            (name, value)
            for name, value in parse_qsl(split_url.query, keep_blank_values=True)
            if name not in {"profile", "profile_source"}
        ]
        query_item_list.append(("profile", mcp_playwright_profile))
        if mcp_playwright_profile_source is not None:
            query_item_list.append(("profile_source", mcp_playwright_profile_source))
        return urlunsplit(
            (split_url.scheme, split_url.netloc, split_url.path, urlencode(query_item_list), split_url.fragment)
        )
