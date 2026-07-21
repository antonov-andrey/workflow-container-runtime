"""Behavior tests for run-local Playwright profile routing and publication."""

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
from pathlib import Path
import pickle
from threading import Event
from typing import get_type_hints
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from workflow_container_contract import McpPlaywrightProfileWritebackPolicy, WorkflowDefinition, WorkflowInputSchema

from workflow_container_runtime.capability import BrowserRuntimeCapability, WorkflowRuntimeCapability
from workflow_container_runtime.mcp_playwright_profile import McpPlaywrightProfileRoute, McpPlaywrightProfileRuntime
from workflow_container_runtime.platform import WorkflowControlClient, WorkflowControlRequestError
from workflow_container_runtime.request import WorkflowControlRequestBuilder
from workflow_container_runtime.step import WorkflowStepCodexConcurrentConfigBase, WorkflowStepCodexConfigBase
from workflow_container_runtime.workflow import WorkflowBrowserConfigBase, WorkflowInputBase


class ExampleWorkflowBrowserConfig(WorkflowBrowserConfigBase):
    """Provide one concrete browser-backed workflow config."""


class ExampleWorkflowBrowserRequest(BaseModel):
    """Provide one minimal concrete workflow request."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="Requested browser work.", title="Text")


class ExampleWorkflowBrowserInput(WorkflowInputBase[ExampleWorkflowBrowserRequest, ExampleWorkflowBrowserConfig]):
    """Expose the shared browser config through one generated input schema."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={"$schema": "https://json-schema.org/draft/2020-12/schema"},
        strict=True,
        validate_assignment=True,
        validate_default=True,
    )


class ExampleStepConfig(WorkflowStepCodexConfigBase):
    """Provide one concrete profile-aware step config."""


class ExampleConcurrentStepConfig(WorkflowStepCodexConcurrentConfigBase):
    """Provide one concrete fixed-lane profile-aware step config."""


DONE_WRITEBACK_POLICY = McpPlaywrightProfileWritebackPolicy(
    mcp_playwright_profile_name_prefix="",
    workflow_run_status_list=("done",),
)
DISABLED_WRITEBACK_POLICY = McpPlaywrightProfileWritebackPolicy(
    mcp_playwright_profile_name_prefix="",
    workflow_run_status_list=(),
)


class FakeHttpResponse:
    """Expose one configured HTTP status through a context manager."""

    def __init__(self, status: int) -> None:
        """Store the response status."""

        self.status = status

    def __enter__(self) -> "FakeHttpResponse":
        """Return this response for the request context."""

        return self

    def __exit__(self, *args: object) -> None:
        """Leave the response context without suppressing failures."""

    def read(self) -> bytes:
        """Return one empty successful response body."""

        return b""


def _browser_capability_get(
    *,
    mcp_url: str = "http://browser:8931/mcp",
) -> BrowserRuntimeCapability:
    """Build one complete browser runtime capability."""

    return BrowserRuntimeCapability(
        mcp_playwright_profile_source="data-source-profile",
        mcp_playwright_profile_writeback_candidate_url="http://platform/control/candidate?token=run",
        mcp_url=mcp_url,
    )


def _runtime_capability_get(
    *,
    mcp_url: str = "http://browser:8931/mcp",
) -> WorkflowRuntimeCapability:
    """Build one workflow capability with browser routing enabled."""

    return WorkflowRuntimeCapability(browser=_browser_capability_get(mcp_url=mcp_url))


def _workflow_control_request_builder_get() -> WorkflowControlRequestBuilder:
    """Build one exact source declaration for profile safepoint tests.

    Returns:
        Control request builder declaring the exercised source step.
    """

    return WorkflowControlRequestBuilder(
        workflow_definition=WorkflowDefinition.model_validate(
            {
                "build": {"dockerfile_path": "Dockerfile"},
                "command": ["run"],
                "input_schema_path": "input.schema.json",
                "name": "example",
                "step": {"source_discover": {}},
            }
        )
    )


def test_profile_runtime_exposes_exact_public_method_signatures() -> None:
    """Keep the profile runtime interface aligned with the canonical Appendix A contract."""

    assert list(inspect.signature(McpPlaywrightProfileRuntime.lease).parameters) == [
        "self",
        "mcp_playwright_profile",
        "mcp_playwright_profile_source",
        "runtime_capability",
    ]
    assert get_type_hints(McpPlaywrightProfileRuntime.lease)["return"] == Generator[McpPlaywrightProfileRoute]
    assert list(inspect.signature(McpPlaywrightProfileRuntime.writeback_candidate_stage).parameters) == [
        "self",
        "route",
        "policy",
        "step_identity",
        "step_key",
        "transition_identity",
    ]


def test_browser_capability_and_workflow_config_require_complete_profile_contract() -> None:
    """Reject incomplete browser capabilities and accept one explicit writeback policy."""

    with pytest.raises(ValidationError):
        BrowserRuntimeCapability(mcp_url="http://browser:8931/mcp")

    config = ExampleWorkflowBrowserConfig(
        instruction="",
        mcp_playwright_profile_writeback_policy=McpPlaywrightProfileWritebackPolicy(
            mcp_playwright_profile_name_prefix="source-",
            workflow_run_status_list=("working", "done"),
        ),
    )

    assert config.mcp_playwright_profile_writeback_policy.mcp_playwright_profile_name_prefix == "source-"


def test_browser_workflow_config_generates_valid_input_schema(tmp_path: Path) -> None:
    """Keep inherited browser config fields valid in generated public input schemas.

    Args:
        tmp_path: Isolated schema directory.
    """

    schema_path = tmp_path / "input.schema.json"
    schema_path.write_text(json.dumps(ExampleWorkflowBrowserInput.model_json_schema()), encoding="utf-8")

    WorkflowInputSchema.from_path(schema_path)


@pytest.mark.parametrize(
    "profile_name",
    ["profile name", "profile%name", "profile:name", "a" * 129, "-profile", "_profile", "profile-", "profile_"],
)
def test_step_profile_fields_are_required_nullable_and_validate_relationships(profile_name: str) -> None:
    """Require explicit nullable fields and reject invalid source/target relationships."""

    common = {
        "correction_attempt_limit": 1,
        "instruction": "",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
    }
    with pytest.raises(ValidationError):
        ExampleStepConfig(**common)
    with pytest.raises(ValidationError, match="requires.*target"):
        ExampleStepConfig(
            **common,
            mcp_playwright_profile=None,
            mcp_playwright_profile_source="source-1",
        )
    with pytest.raises(ValidationError, match="collides with physical target"):
        ExampleStepConfig(
            **common,
            mcp_playwright_profile="profile-1",
            mcp_playwright_profile_source="profile-1",
        )
    with pytest.raises(ValidationError, match="safe profile name"):
        ExampleStepConfig(
            **common,
            mcp_playwright_profile=profile_name,
            mcp_playwright_profile_source=None,
        )

    config = ExampleStepConfig(
        **common,
        mcp_playwright_profile=None,
        mcp_playwright_profile_source=None,
    )
    assert config.mcp_playwright_profile is None
    assert config.mcp_playwright_profile_source is None


def test_concurrent_step_config_owns_exact_physical_profile_names() -> None:
    """Validate and expose one deterministic physical profile list for the scheduler."""

    common = {
        "concurrency": 2,
        "correction_attempt_limit": 1,
        "instruction": "",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
    }
    config = ExampleConcurrentStepConfig(
        **common,
        mcp_playwright_profile="target",
        mcp_playwright_profile_source="target",
    )

    assert config.mcp_playwright_profile_physical_list_get() == ["target-1", "target-2"]

    with pytest.raises(ValidationError, match="collides with physical target"):
        ExampleConcurrentStepConfig(
            **common,
            mcp_playwright_profile="target",
            mcp_playwright_profile_source="target-1",
        )
    with pytest.raises(ValidationError, match="safe profile name"):
        ExampleConcurrentStepConfig(
            **common,
            mcp_playwright_profile="a" * 128,
            mcp_playwright_profile_source=None,
        )


def test_profile_runtime_builds_distinct_action_and_verification_capabilities() -> None:
    """Route action through reset source and verification through the continued target."""

    runtime_capability = _runtime_capability_get()

    with McpPlaywrightProfileRuntime().lease(
        mcp_playwright_profile="target",
        mcp_playwright_profile_source="source-1",
        runtime_capability=runtime_capability,
    ) as route:
        action_browser = route.action_runtime_capability.browser
        verification_browser = route.verification_runtime_capability.browser
        assert action_browser is not None
        assert verification_browser is not None
        assert action_browser.mcp_url.endswith("?profile=target&profile_source=source-1")
        assert verification_browser.mcp_url.endswith("?profile=target")
        assert action_browser.mcp_playwright_profile_source == "data-source-profile"
        assert verification_browser.mcp_playwright_profile_source == "data-source-profile"


def test_unprofiled_route_keeps_base_mcp_url() -> None:
    """Keep isolated action and verification on the unchanged base endpoint."""

    runtime_capability = _runtime_capability_get()

    with McpPlaywrightProfileRuntime().lease(
        mcp_playwright_profile=None,
        mcp_playwright_profile_source=None,
        runtime_capability=runtime_capability,
    ) as route:
        assert route.mcp_playwright_profile is None
        assert route.action_runtime_capability.browser is not None
        assert route.verification_runtime_capability.browser is not None
        assert route.action_runtime_capability.browser.mcp_url == "http://browser:8931/mcp"
        assert route.verification_runtime_capability.browser.mcp_url == "http://browser:8931/mcp"


def test_profile_runtime_preserves_existing_query_and_replaces_owned_values() -> None:
    """Preserve caller query values while deterministically replacing router-owned keys."""

    runtime_capability = _runtime_capability_get(
        mcp_url=("http://browser:8931/mcp?token=a%2Fb&profile=old&keep=two&profile_source=old-source#fragment")
    )

    with McpPlaywrightProfileRuntime().lease(
        mcp_playwright_profile="target_one",
        mcp_playwright_profile_source="source-two",
        runtime_capability=runtime_capability,
    ) as route:
        action_url = urlsplit(route.action_runtime_capability.browser.mcp_url)  # type: ignore[union-attr]
        verification_url = urlsplit(route.verification_runtime_capability.browser.mcp_url)  # type: ignore[union-attr]

    assert action_url.fragment == "fragment"
    assert parse_qsl(action_url.query) == [
        ("token", "a/b"),
        ("keep", "two"),
        ("profile", "target_one"),
        ("profile_source", "source-two"),
    ]
    assert "%2F" in action_url.query
    assert parse_qsl(verification_url.query) == [
        ("token", "a/b"),
        ("keep", "two"),
        ("profile", "target_one"),
    ]


def test_profile_runtime_rejects_invalid_routes_and_missing_browser_capability() -> None:
    """Reject invalid relationships and a configured profile without browser infrastructure."""

    runtime = McpPlaywrightProfileRuntime()
    runtime_capability = _runtime_capability_get()
    with pytest.raises(ValueError, match="requires.*target"):
        with runtime.lease(
            mcp_playwright_profile=None,
            mcp_playwright_profile_source="source",
            runtime_capability=runtime_capability,
        ):
            pass
    with pytest.raises(ValueError, match="must differ"):
        with runtime.lease(
            mcp_playwright_profile="same",
            mcp_playwright_profile_source="same",
            runtime_capability=runtime_capability,
        ):
            pass
    with pytest.raises(RuntimeError, match="browser capability"):
        with runtime.lease(
            mcp_playwright_profile="target",
            mcp_playwright_profile_source=None,
            runtime_capability=WorkflowRuntimeCapability(browser=None),
        ):
            pass


@pytest.mark.parametrize(
    "profile_name",
    ["profile name", "profile%name", "profile:name", "a" * 129, "-profile", "_profile", "profile-", "profile_"],
)
def test_profile_runtime_rejects_router_unsafe_direct_physical_names(profile_name: str) -> None:
    """Apply the browser router's exact safe component contract at the direct lease boundary."""

    with pytest.raises(ValueError, match="safe profile name"):
        with McpPlaywrightProfileRuntime().lease(
            mcp_playwright_profile=profile_name,
            mcp_playwright_profile_source=None,
            runtime_capability=_runtime_capability_get(),
        ):
            pass


def test_no_browser_unprofiled_route_and_candidate_are_no_ops() -> None:
    """Allow one non-browser lifecycle without routing or publication work."""

    runtime = McpPlaywrightProfileRuntime()
    runtime_capability = WorkflowRuntimeCapability(browser=None)

    with runtime.lease(
        mcp_playwright_profile=None,
        mcp_playwright_profile_source=None,
        runtime_capability=runtime_capability,
    ) as route:
        assert route.action_runtime_capability == runtime_capability
        assert route.verification_runtime_capability == runtime_capability
        runtime.writeback_candidate_stage(
            route,
            policy=DISABLED_WRITEBACK_POLICY,
            step_identity="step-1",
            step_key="source_discover",
            transition_identity="step-1/completed",
        )


def test_working_writeback_filtered_profile_is_complete_no_op() -> None:
    """Do not create a candidate or empty safepoint for one profile excluded by policy."""

    def urlopen(request: object, *, timeout: float) -> FakeHttpResponse:
        """Fail if one filtered profile reaches either platform endpoint."""

        del request, timeout
        raise AssertionError("filtered profile must not reach platform control")

    runtime = McpPlaywrightProfileRuntime(
        urlopen=urlopen,
        workflow_control_client=WorkflowControlClient(control_url="http://control/v2", urlopen=urlopen),
    )
    with runtime.lease(
        mcp_playwright_profile="other",
        mcp_playwright_profile_source=None,
        runtime_capability=_runtime_capability_get(),
    ) as route:
        runtime.writeback_candidate_stage(
            route,
            policy=McpPlaywrightProfileWritebackPolicy(
                mcp_playwright_profile_name_prefix="target-",
                workflow_run_status_list=("working",),
            ),
            step_identity="step-1",
            step_key="source_discover",
            transition_identity="step-1/completed",
        )


def test_candidate_publication_posts_exact_empty_body_and_requires_204() -> None:
    """Publish the selected physical profile through the platform control endpoint."""

    request_list: list[object] = []
    timeout_list: list[float] = []

    def urlopen(request: object, *, timeout: float) -> FakeHttpResponse:
        """Record one request and return successful candidate publication."""

        request_list.append(request)
        timeout_list.append(timeout)
        return FakeHttpResponse(204)

    runtime = McpPlaywrightProfileRuntime(
        mcp_playwright_profile_writeback_candidate_http_timeout_seconds=12.5,
        urlopen=urlopen,
    )
    with runtime.lease(
        mcp_playwright_profile="target_one",
        mcp_playwright_profile_source=None,
        runtime_capability=_runtime_capability_get(),
    ) as route:
        runtime.writeback_candidate_stage(
            route,
            policy=DONE_WRITEBACK_POLICY,
            step_identity="step-1",
            step_key="source_discover",
            transition_identity="step-1/completed",
        )

    request = request_list[0]
    assert request.get_method() == "POST"  # type: ignore[attr-defined]
    assert json.loads(request.data) == {  # type: ignore[attr-defined]
        "step_identity": "step-1",
        "transition_identity": "step-1/completed",
    }
    assert parse_qsl(urlsplit(request.full_url).query) == [  # type: ignore[attr-defined]
        ("token", "run"),
        ("profile", "target_one"),
    ]
    assert timeout_list == [12.5]

    failing_runtime = McpPlaywrightProfileRuntime(
        mcp_playwright_profile_writeback_candidate_http_timeout_seconds=3.0,
        urlopen=lambda request, *, timeout: FakeHttpResponse(200),
    )
    with failing_runtime.lease(
        mcp_playwright_profile="target",
        mcp_playwright_profile_source=None,
        runtime_capability=_runtime_capability_get(),
    ) as route:
        with pytest.raises(RuntimeError, match="204"):
            failing_runtime.writeback_candidate_stage(
                route,
                policy=DONE_WRITEBACK_POLICY,
                step_identity="step-1",
                step_key="source_discover",
                transition_identity="step-1/completed",
            )


def test_candidate_publication_wraps_http_error_in_serializable_runtime_error() -> None:
    """Keep rejected profile publication durable across DBOS error persistence."""

    def urlopen(request: object, *, timeout: float) -> FakeHttpResponse:
        """Raise the standard-library error produced by one rejected HTTP request."""

        del request, timeout
        raise HTTPError("http://platform/control/candidate", 409, "Conflict", hdrs=None, fp=None)

    runtime = McpPlaywrightProfileRuntime(urlopen=urlopen)
    with runtime.lease(
        mcp_playwright_profile="target",
        mcp_playwright_profile_source=None,
        runtime_capability=_runtime_capability_get(),
    ) as route:
        with pytest.raises(WorkflowControlRequestError, match="returned HTTP 409") as exception_info:
            runtime.writeback_candidate_stage(
                route,
                policy=DONE_WRITEBACK_POLICY,
                step_identity="step-1",
                step_key="source_discover",
                transition_identity="step-1/completed",
            )

    restored_error = pickle.loads(pickle.dumps(exception_info.value))
    assert type(restored_error) is WorkflowControlRequestError
    assert str(restored_error) == "Playwright profile candidate endpoint returned HTTP 409."


def test_working_candidate_accepts_same_identity_safepoint_before_releasing_lease() -> None:
    """Complete policy-selected candidate staging before its empty owning safepoint."""

    request_list: list[object] = []

    def urlopen(request: object, *, timeout: float) -> FakeHttpResponse:
        """Record candidate and safepoint control requests in call order."""

        assert timeout == 30.0
        request_list.append(request)
        return FakeHttpResponse(204)

    runtime = McpPlaywrightProfileRuntime(
        urlopen=urlopen,
        workflow_control_client=WorkflowControlClient(control_url="http://control/v2", urlopen=urlopen),
        workflow_control_request_builder=_workflow_control_request_builder_get(),
    )
    with runtime.lease(
        mcp_playwright_profile="target",
        mcp_playwright_profile_source=None,
        runtime_capability=_runtime_capability_get(),
    ) as route:
        runtime.writeback_candidate_stage(
            route,
            policy=McpPlaywrightProfileWritebackPolicy(
                mcp_playwright_profile_name_prefix="target",
                workflow_run_status_list=("working",),
            ),
            step_identity="step-1",
            step_key="source_discover",
            transition_identity="step-1/completed",
        )

    assert [request.full_url for request in request_list] == [  # type: ignore[attr-defined]
        "http://platform/control/candidate?token=run&profile=target",
        "http://control/v2/safepoint",
    ]
    assert json.loads(request_list[1].data) == {  # type: ignore[attr-defined]
        "manifest_request_list": [],
        "step_identity": "step-1",
        "step_key": "source_discover",
        "transition_identity": "step-1/completed",
    }


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("-inf"), float("nan")])
def test_profile_runtime_rejects_non_positive_or_non_finite_candidate_http_timeout(timeout: float) -> None:
    """Reject candidate control-call timeouts that cannot bound one request.

    Args:
        timeout: Invalid timeout value.
    """

    with pytest.raises(ValueError, match="finite positive"):
        McpPlaywrightProfileRuntime(
            mcp_playwright_profile_writeback_candidate_http_timeout_seconds=timeout,
        )


def test_profile_runtime_serializes_only_the_same_run_local_profile() -> None:
    """Serialize one run-local profile without coupling distinct profiles or WorkflowRuns."""

    runtime_capability = _runtime_capability_get()
    candidate_started = Event()
    release_candidate = Event()
    same_entered = Event()
    distinct_entered = Event()
    distinct_run_entered = Event()

    def urlopen(request: object, *, timeout: float) -> FakeHttpResponse:
        """Block candidate publication while the profile lease remains active."""

        _ = request, timeout
        candidate_started.set()
        release_candidate.wait(timeout=2)
        return FakeHttpResponse(204)

    runtime = McpPlaywrightProfileRuntime(urlopen=urlopen)

    def hold_first() -> None:
        """Publish the first target candidate before releasing its lifecycle lease."""

        with runtime.lease(
            mcp_playwright_profile="target-1",
            mcp_playwright_profile_source=None,
            runtime_capability=runtime_capability,
        ) as route:
            runtime.writeback_candidate_stage(
                route,
                policy=DONE_WRITEBACK_POLICY,
                step_identity="step-1",
                step_key="source_discover",
                transition_identity="step-1/completed",
            )

    def enter(
        profile: str,
        entered: Event,
        competing_runtime_capability: WorkflowRuntimeCapability,
    ) -> None:
        """Record entry into one competing profile lease."""

        candidate_started.wait(timeout=2)
        with runtime.lease(
            mcp_playwright_profile=profile,
            mcp_playwright_profile_source=None,
            runtime_capability=competing_runtime_capability,
        ):
            entered.set()

    with ThreadPoolExecutor(max_workers=4) as executor:
        first_future = executor.submit(hold_first)
        same_future = executor.submit(
            enter,
            "target-1",
            same_entered,
            _runtime_capability_get(mcp_url="http://browser:8931/mcp?transport=alternate"),
        )
        distinct_future = executor.submit(enter, "target-2", distinct_entered, runtime_capability)
        distinct_run_future = executor.submit(
            enter,
            "target-1",
            distinct_run_entered,
            _runtime_capability_get(mcp_url="http://other-run-browser:8931/mcp"),
        )
        assert candidate_started.wait(timeout=1)
        assert distinct_entered.wait(timeout=1)
        assert distinct_run_entered.wait(timeout=1)
        assert not same_entered.wait(timeout=0.05)
        release_candidate.set()
        first_future.result(timeout=2)
        same_future.result(timeout=2)
        distinct_future.result(timeout=2)
        distinct_run_future.result(timeout=2)

    assert same_entered.is_set()
