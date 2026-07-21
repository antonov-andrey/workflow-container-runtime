"""Run-local Playwright profile routing, leasing, and candidate publication."""

from collections.abc import Generator
from contextlib import contextmanager
from math import isfinite
import re
from threading import Lock
from types import TracebackType
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from weakref import WeakValueDictionary

from pydantic import BaseModel, ConfigDict
from workflow_container_contract import (
    McpPlaywrightProfileWritebackCandidateRequest,
    McpPlaywrightProfileWritebackPolicy,
)

from workflow_container_runtime.capability import BrowserRuntimeCapability, WorkflowRuntimeCapability
from workflow_container_runtime.platform import WorkflowControlClient, WorkflowControlRequestError
from workflow_container_runtime.request import WorkflowControlRequestBuilder

_MCP_PLAYWRIGHT_PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?")


def mcp_playwright_profile_name_validate(value: str) -> str:
    """Require the exact safe single-component name accepted by the browser router.

    Args:
        value: Candidate logical or physical profile name.

    Returns:
        The accepted unchanged profile name.

    Raises:
        ValueError: If the browser router would reject the name.
    """

    if _MCP_PLAYWRIGHT_PROFILE_NAME_PATTERN.fullmatch(value) is None:
        raise ValueError("profile value must be a safe profile name")
    return value


class _HttpResponse(Protocol):
    """Describe the standard-library response surface used by candidate publication."""

    status: int

    def __enter__(self) -> "_HttpResponse":
        """Enter the response context."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Leave the response context."""


class _HttpOpen(Protocol):
    """Describe the bounded standard-library HTTP call used for publication."""

    def __call__(self, request: Request, *, timeout: float) -> _HttpResponse:
        """Open one request with an explicit transport timeout.

        Args:
            request: Candidate publication request.
            timeout: Finite positive timeout in seconds.

        Returns:
            Candidate publication response context.
        """


class McpPlaywrightProfileRoute(BaseModel):
    """Carry phase-specific capabilities under one physical profile lease."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    action_runtime_capability: WorkflowRuntimeCapability
    mcp_playwright_profile: str | None
    verification_runtime_capability: WorkflowRuntimeCapability


class McpPlaywrightProfileRuntime:
    """Own synchronous run-local profile leases and platform candidate publication."""

    def __init__(
        self,
        *,
        mcp_playwright_profile_writeback_candidate_http_timeout_seconds: float = 30.0,
        urlopen: _HttpOpen = urlopen,
        workflow_control_client: WorkflowControlClient | None = None,
        workflow_control_request_builder: WorkflowControlRequestBuilder | None = None,
    ) -> None:
        """Initialize an empty run-profile lock registry and HTTP request boundary.

        Args:
            mcp_playwright_profile_writeback_candidate_http_timeout_seconds: Runtime control-call timeout in seconds.
            urlopen: Standard-library-compatible HTTP request callable.
            workflow_control_client: Current execution control adapter required by `working` writeback.
            workflow_control_request_builder: Exact source request builder required by `working` writeback.

        Raises:
            ValueError: If the candidate HTTP timeout is not finite and positive.
        """

        if (
            not isfinite(mcp_playwright_profile_writeback_candidate_http_timeout_seconds)
            or mcp_playwright_profile_writeback_candidate_http_timeout_seconds <= 0
        ):
            raise ValueError("Playwright profile writeback candidate HTTP timeout must be finite positive seconds")
        self._lock_by_runtime_profile_map: WeakValueDictionary[tuple[str, str], Lock] = WeakValueDictionary()
        self._lock_map_guard = Lock()
        self._mcp_playwright_profile_writeback_candidate_http_timeout_seconds = (
            mcp_playwright_profile_writeback_candidate_http_timeout_seconds
        )
        self._urlopen = urlopen
        self._workflow_control_client = workflow_control_client
        self._workflow_control_request_builder = workflow_control_request_builder

    @contextmanager
    def lease(
        self,
        *,
        mcp_playwright_profile: str | None,
        mcp_playwright_profile_source: str | None,
        runtime_capability: WorkflowRuntimeCapability,
    ) -> Generator[McpPlaywrightProfileRoute]:
        """Lease one physical target and expose action and verification routes.

        Args:
            mcp_playwright_profile: Exact physical target profile, or `None` for isolated execution.
            mcp_playwright_profile_source: Exact physical source copied before each action connection.
            runtime_capability: Complete capability supplied for the current workflow run.

        Yields:
            Phase-specific capabilities protected by the target lease.

        Raises:
            RuntimeError: If a named profile has no browser capability.
            ValueError: If the source and target relationship is invalid.
        """

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
        split_mcp_url = urlsplit(browser.mcp_url)
        runtime_profile_key = (
            urlunsplit((split_mcp_url.scheme, split_mcp_url.netloc, split_mcp_url.path, "", "")),
            mcp_playwright_profile,
        )
        with self._lock_map_guard:
            profile_lock = self._lock_by_runtime_profile_map.get(runtime_profile_key)
            if profile_lock is None:
                profile_lock = Lock()
                self._lock_by_runtime_profile_map[runtime_profile_key] = profile_lock
        with profile_lock:
            yield route

    def writeback_candidate_stage(
        self,
        route: McpPlaywrightProfileRoute,
        *,
        policy: McpPlaywrightProfileWritebackPolicy,
        step_identity: str,
        step_key: str,
        transition_identity: str,
    ) -> None:
        """Stage one policy-selected profile and accept its required working safepoint.

        Args:
            route: Current leased profile route after successful semantic verification.
            policy: Exact run-owned profile writeback policy.
            step_identity: Stable owning workflow step identity.
            step_key: Source-declared workflow step key.
            transition_identity: Stable owning step-completion transition identity.

        Raises:
            RuntimeError: If a required platform endpoint is unavailable or rejects the request.
        """

        profile_name = route.mcp_playwright_profile
        if (
            not policy.workflow_run_status_list
            or profile_name is None
            or not profile_name.startswith(policy.mcp_playwright_profile_name_prefix)
        ):
            return
        browser = route.action_runtime_capability.browser
        if browser is None:
            raise RuntimeError("configured Playwright profile requires a browser capability")
        candidate_url = self._url_get(
            base_url=browser.mcp_playwright_profile_writeback_candidate_url,
            mcp_playwright_profile=profile_name,
            mcp_playwright_profile_source=None,
        )
        candidate_request = McpPlaywrightProfileWritebackCandidateRequest(
            step_identity=step_identity,
            transition_identity=transition_identity,
        )
        request = Request(
            candidate_url,
            data=candidate_request.model_dump_json().encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._urlopen(
                request,
                timeout=self._mcp_playwright_profile_writeback_candidate_http_timeout_seconds,
            ) as response:
                if response.status != 204:
                    raise WorkflowControlRequestError(
                        f"Playwright profile candidate endpoint returned HTTP {response.status}; expected 204."
                    )
        except HTTPError as error:
            raise WorkflowControlRequestError(
                f"Playwright profile candidate endpoint returned HTTP {error.code}."
            ) from error
        except (TimeoutError, URLError, OSError) as error:
            raise WorkflowControlRequestError(
                f"Playwright profile candidate endpoint transport failed: {error}"
            ) from error
        if "working" in policy.workflow_run_status_list:
            if self._workflow_control_client is None or self._workflow_control_request_builder is None:
                raise RuntimeError("working profile writeback requires workflow control client and request builder")
            self._workflow_control_client.safepoint_send(
                request=self._workflow_control_request_builder.safepoint_build(
                    manifest_request_list=[],
                    step_identity=step_identity,
                    step_key=step_key,
                    transition_identity=transition_identity,
                )
            )

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
