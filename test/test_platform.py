"""Behavior tests for the standard workflow platform adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
import json
from pathlib import Path
from types import TracebackType
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from pydantic import ValidationError
from workflow_container_contract import (
    WorkflowControlFinalRequest,
    WorkflowControlManifestRequest,
    WorkflowControlSafepointRequest,
    WorkflowResult,
    WorkflowRunContext,
)

from workflow_container_runtime.capability import NetworkProxyRuntimeCapability, WorkflowRuntimeCapability
from workflow_container_runtime.platform import (
    WorkflowControlClient,
    WorkflowControlRequestError,
    WorkflowPlatformRuntimeConfig,
)
import workflow_container_runtime.platform as platform_module


class HttpResponseStub:
    """Return one fixed HTTP response and retain its body."""

    def __init__(self, *, payload: bytes = b"", status: int) -> None:
        """Store the fixed payload and status.

        Args:
            payload: Complete response body.
            status: HTTP response status.
        """

        self._payload = payload
        self.status = status

    def __enter__(self) -> "HttpResponseStub":
        """Enter the response context.

        Returns:
            This response.
        """

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Leave the response context."""

    def read(self) -> bytes:
        """Return the fixed response body.

        Returns:
            Complete response bytes.
        """

        return self._payload


def test_platform_runtime_config_loads_only_standard_environment(tmp_path: Path) -> None:
    """Normalize the six platform values and exact immutable run provenance.

    Args:
        tmp_path: Isolated immutable input directory.
    """

    run_context_path = tmp_path / "run-context.json"
    run_context = WorkflowRunContext(
        interface_major_version=2,
        version=1,
        workflow_id="workflow-id",
        workflow_name="sample_workflow",
        workflow_run_id="20260719123456789",
        workflow_run_timestamp=datetime(2026, 7, 19, 12, 34, 56, 789000, tzinfo=UTC),
        workflow_source_id="source-id",
        workflow_source_version_id="source-version-id",
    )
    run_context_path.write_text(run_context.model_dump_json(), encoding="utf-8")

    config = WorkflowPlatformRuntimeConfig.from_environment(
        {
            "WORKFLOW_CAPABILITY_CONFIG_PATH": "/input/capability.json",
            "WORKFLOW_CONTROL_URL": "http://control:8080/v2/",
            "WORKFLOW_INPUT_PATH": "/input/input.json",
            "WORKFLOW_RUN_CONTEXT_PATH": str(run_context_path),
            "WORKFLOW_RUN_ID": "20260719123456789",
            "WORKFLOW_RUNTIME_PATH": "/runtime",
        }
    )

    assert config.model_dump(mode="python") == {
        "capability_config_path": Path("/input/capability.json"),
        "control_url": "http://control:8080/v2",
        "input_path": Path("/input/input.json"),
        "run_context": run_context.model_dump(mode="python"),
        "runtime_path": Path("/runtime"),
    }


def test_platform_runtime_config_rejects_missing_or_relative_values() -> None:
    """Fail before runtime bootstrap when the standard environment is incomplete or unsafe."""

    with pytest.raises(RuntimeError, match="capability_config_path"):
        WorkflowPlatformRuntimeConfig.from_environment({})
    with pytest.raises(ValueError, match="absolute"):
        WorkflowPlatformRuntimeConfig(
            capability_config_path=Path("capability.json"),
            control_url="http://control/v2",
            input_path=Path("/input/input.json"),
            run_context=WorkflowRunContext(
                interface_major_version=2,
                version=1,
                workflow_id="workflow-id",
                workflow_name="sample_workflow",
                workflow_run_id="20260719123456789",
                workflow_run_timestamp=datetime(2026, 7, 19, 12, 34, 56, 789000, tzinfo=UTC),
                workflow_source_id="source-id",
                workflow_source_version_id="source-version-id",
            ),
            runtime_path=Path("/runtime"),
        )


def test_runtime_capability_loads_typed_browser_config(tmp_path: Path) -> None:
    """Select the supported capability by its platform-owned versioned name."""

    capability_path = tmp_path / "capability.json"
    capability_path.write_text(
        json.dumps(
            {
                "browser_runtime": {
                    "browser": {
                        "mcp_playwright_profile_source": "source",
                        "mcp_playwright_profile_writeback_candidate_url": "http://browser/candidate",
                        "mcp_url": "http://browser/mcp",
                    }
                },
                "network_proxy": {
                    "proxy_by_name_map": {
                        "328193012345678901/tr": "socks5://workflow-run-vpn-tr:1080",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    capability = WorkflowRuntimeCapability.from_platform_config_path(capability_path)

    assert capability.browser is not None
    assert capability.browser.mcp_url == "http://browser/mcp"
    assert capability.network_proxy.proxy_url_get("328193012345678901/tr") == ("socks5://workflow-run-vpn-tr:1080")


def test_runtime_capability_requires_an_explicit_empty_network_proxy_map(tmp_path: Path) -> None:
    """Represent absent browser infrastructure and direct network egress explicitly."""

    capability_path = tmp_path / "capability.json"
    capability_path.write_text('{"network_proxy":{"proxy_by_name_map":{}}}', encoding="utf-8")

    capability = WorkflowRuntimeCapability.from_platform_config_path(capability_path)

    assert capability.browser is None
    assert capability.network_proxy.proxy_url_get(None) is None


def test_runtime_capability_rejects_missing_network_proxy_document(tmp_path: Path) -> None:
    """Reject a capability document that omits the standard environment map.

    Args:
        tmp_path: Isolated capability document directory.
    """

    capability_path = tmp_path / "capability.json"
    capability_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="network_proxy"):
        WorkflowRuntimeCapability.from_platform_config_path(capability_path)


def test_network_proxy_capability_performs_only_exact_lookup() -> None:
    """Return only the caller-named endpoint and reject unknown or malformed values."""

    capability = NetworkProxyRuntimeCapability(
        proxy_by_name_map={
            "328193012345678901/tr": "socks5://workflow-run-vpn-tr:1080",
            "328193012345678901/us": "socks5://workflow-run-vpn-us:1080",
        }
    )

    assert capability.proxy_url_get(None) is None
    assert capability.proxy_url_get("328193012345678901/us") == "socks5://workflow-run-vpn-us:1080"
    assert not hasattr(capability, "select")
    with pytest.raises(ValueError, match="unavailable"):
        capability.proxy_url_get("328193012345678901/missing")
    with pytest.raises(ValueError, match="zitadel_user_id"):
        capability.proxy_url_get("missing-owner-prefix")
    with pytest.raises(ValidationError, match="credential-free"):
        NetworkProxyRuntimeCapability(proxy_by_name_map={"328193012345678901/tr": "socks5://login:password@proxy:1080"})


def test_control_client_sends_exact_typed_protocol_requests() -> None:
    """Send registration, safepoint, final, and cancellation through the current proxy."""

    captured_request_list: list[Request] = []
    response_list = [
        HttpResponseStub(status=204),
        HttpResponseStub(status=204),
        HttpResponseStub(status=202),
        HttpResponseStub(payload=b'{"is_cancellation_requested":true}', status=200),
    ]

    def urlopen_stub(request: Request, *, timeout: float) -> HttpResponseStub:
        """Capture one request and return the next fixed response.

        Args:
            request: Complete control request.
            timeout: Configured request timeout.

        Returns:
            Next fixed response.
        """

        assert timeout == 12.0
        captured_request_list.append(request)
        return response_list.pop(0)

    client = WorkflowControlClient(
        control_url="http://control/v2/",
        http_timeout_seconds=12.0,
        urlopen=urlopen_stub,
    )
    manifest_request_list = [
        WorkflowControlManifestRequest(
            manifest_key="result",
            path_parameter_by_name_map={"item_key": "item-1"},
        )
    ]

    client.registration_send(workflow_run_id="run-1")
    client.safepoint_send(
        request=WorkflowControlSafepointRequest(
            manifest_request_list=manifest_request_list,
            step_identity="brand/one",
            step_key="brand_complete",
            transition_identity="brand/one/completed",
        )
    )
    client.final_send(
        request=WorkflowControlFinalRequest(
            manifest_request_list=manifest_request_list,
            transition_identity="run/completed",
            workflow_result=WorkflowResult(error_list=[], status="success", warning_list=[]),
        )
    )

    assert client.cancellation_get() is True
    assert [request.full_url for request in captured_request_list] == [
        "http://control/v2/registration",
        "http://control/v2/safepoint",
        "http://control/v2/final",
        "http://control/v2/cancellation",
    ]
    assert json.loads(captured_request_list[0].data) == {"workflow_run_id": "run-1"}
    assert json.loads(captured_request_list[1].data)["step_identity"] == "brand/one"
    assert json.loads(captured_request_list[2].data)["workflow_result"]["status"] == "success"
    assert captured_request_list[3].get_method() == "GET"


def test_control_client_preserves_structured_rejection_detail() -> None:
    """Expose the concrete platform error instead of a generic transport wrapper."""

    def urlopen_stub(request: Request, *, timeout: float) -> HttpResponseStub:
        """Return one stable structured conflict response.

        Args:
            request: Complete control request.
            timeout: Configured request timeout.

        Returns:
            Fixed conflict response.
        """

        _ = request
        _ = timeout
        return HttpResponseStub(
            payload=(b'{"error_code":"workflow_control_conflict",' b'"error_detail":"transition payload changed"}'),
            status=409,
        )

    client = WorkflowControlClient(control_url="http://control/v2", urlopen=urlopen_stub)

    with pytest.raises(WorkflowControlRequestError, match="transition payload changed"):
        client.registration_send(workflow_run_id="run-1")


def test_control_client_retries_transient_platform_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep one control operation pending across transport and server outages."""

    response_or_error_list = [
        URLError("control unavailable"),
        HTTPError(
            url="http://control/v2/registration",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"error_code":"workflow_control_unavailable","error_detail":"retry"}'),
        ),
        HttpResponseStub(status=502),
        HttpResponseStub(status=204),
    ]
    sleep_seconds_list: list[float] = []

    def urlopen_stub(request: Request, *, timeout: float) -> HttpResponseStub:
        """Return the next transient failure or accepted response.

        Args:
            request: Complete control request.
            timeout: Configured request timeout.

        Returns:
            Next accepted or rejected response.

        Raises:
            HTTPError: For one transient HTTP failure.
            URLError: For one transient transport failure.
        """

        _ = request
        _ = timeout
        response_or_error = response_or_error_list.pop(0)
        if isinstance(response_or_error, HTTPError | URLError):
            raise response_or_error
        return response_or_error

    monkeypatch.setattr(platform_module, "sleep", sleep_seconds_list.append)
    client = WorkflowControlClient(control_url="http://control/v2", urlopen=urlopen_stub)

    client.registration_send(workflow_run_id="run-1")

    assert sleep_seconds_list == [1.0, 1.0, 1.0]
    assert response_or_error_list == []
