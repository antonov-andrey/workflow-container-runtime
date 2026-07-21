"""Standard environment and HTTP control adapter for workflow source images."""

from __future__ import annotations

from collections.abc import Mapping
import json
from math import isfinite
from pathlib import Path
from time import sleep
from types import TracebackType
from typing import Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator
from workflow_container_contract import (
    WorkflowControlCancellationResponse,
    WorkflowControlErrorResponse,
    WorkflowControlFinalRequest,
    WorkflowControlRegistrationRequest,
    WorkflowControlSafepointRequest,
    WorkflowRunContext,
)

_CONTROL_RETRY_INTERVAL_SECONDS = 1.0


class _HttpResponse(Protocol):
    """Describe the standard-library HTTP response used by the control adapter."""

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

    def read(self) -> bytes:
        """Return the complete response body."""


class _HttpOpen(Protocol):
    """Describe the bounded standard-library HTTP call used by the adapter."""

    def __call__(self, request: Request, *, timeout: float) -> _HttpResponse:
        """Open one request with an explicit timeout.

        Args:
            request: Complete control request.
            timeout: Finite positive timeout in seconds.

        Returns:
            Control response context.
        """


class WorkflowControlRequestError(RuntimeError):
    """Report a rejected or unavailable workflow control request."""


class WorkflowPlatformRuntimeConfig(BaseModel):
    """Carry the versioned image-visible platform runtime environment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    capability_config_path: Path
    control_url: str = Field(min_length=1)
    input_path: Path
    run_context: WorkflowRunContext
    runtime_path: Path

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> Self:
        """Build the runtime config from the six standard environment values.

        Args:
            environment: Process environment mapping.

        Returns:
            Validated platform runtime configuration.

        Raises:
            RuntimeError: If one required platform value is absent.
        """

        value_by_field_name_map = {
            "capability_config_path": environment.get("WORKFLOW_CAPABILITY_CONFIG_PATH", ""),
            "control_url": environment.get("WORKFLOW_CONTROL_URL", ""),
            "input_path": environment.get("WORKFLOW_INPUT_PATH", ""),
            "run_id": environment.get("WORKFLOW_RUN_ID", ""),
            "run_context_path": environment.get("WORKFLOW_RUN_CONTEXT_PATH", ""),
            "runtime_path": environment.get("WORKFLOW_RUNTIME_PATH", ""),
        }
        missing_field_name_list = [
            field_name for field_name, value in value_by_field_name_map.items() if not value.strip()
        ]
        if missing_field_name_list:
            raise RuntimeError(
                "Workflow platform environment is incomplete: " + ", ".join(sorted(missing_field_name_list))
            )
        run_context_path = Path(value_by_field_name_map["run_context_path"])
        if not run_context_path.is_absolute():
            raise RuntimeError("WORKFLOW_RUN_CONTEXT_PATH must be absolute")
        try:
            run_context = WorkflowRunContext.model_validate_json(run_context_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Workflow run context is invalid: {exc}") from exc
        if run_context.workflow_run_id != value_by_field_name_map["run_id"]:
            raise RuntimeError("WORKFLOW_RUN_ID does not match the immutable workflow run context")
        return cls(
            capability_config_path=Path(value_by_field_name_map["capability_config_path"]),
            control_url=value_by_field_name_map["control_url"],
            input_path=Path(value_by_field_name_map["input_path"]),
            run_context=run_context,
            runtime_path=Path(value_by_field_name_map["runtime_path"]),
        )

    @field_validator("capability_config_path", "input_path", "runtime_path")
    @classmethod
    def _absolute_path_validate(cls, value: Path) -> Path:
        """Require an absolute image-visible interface path.

        Args:
            value: Candidate platform path.

        Returns:
            Validated absolute path.
        """

        if not value.is_absolute():
            raise ValueError("workflow platform paths must be absolute")
        return value

    @field_validator("control_url")
    @classmethod
    def _control_url_validate(cls, value: str) -> str:
        """Require an HTTP control base URL without query or fragment.

        Args:
            value: Candidate control URL.

        Returns:
            Normalized control base URL.
        """

        split_url = urlsplit(value)
        if split_url.scheme not in {"http", "https"} or not split_url.netloc or split_url.query or split_url.fragment:
            raise ValueError("WORKFLOW_CONTROL_URL must be an HTTP base URL without query or fragment")
        if not split_url.path.rstrip("/").endswith("/v2"):
            raise ValueError("WORKFLOW_CONTROL_URL must select the WorkflowSourceInterface v2 control surface")
        return value.rstrip("/")


class WorkflowControlClient:
    """Send typed requests to the current execution-local control proxy."""

    def __init__(
        self,
        *,
        control_url: str,
        http_timeout_seconds: float = 30.0,
        urlopen: _HttpOpen = urlopen,
    ) -> None:
        """Initialize the control endpoint and bounded HTTP transport.

        Args:
            control_url: Current execution-local versioned proxy base URL.
            http_timeout_seconds: Finite positive request timeout in seconds.
            urlopen: Standard-library-compatible HTTP request callable.

        Raises:
            ValueError: If the URL or timeout is invalid.
        """

        split_url = urlsplit(control_url)
        if split_url.scheme not in {"http", "https"} or not split_url.netloc or split_url.query or split_url.fragment:
            raise ValueError("workflow control URL must be an HTTP base URL without query or fragment")
        if not split_url.path.rstrip("/").endswith("/v2"):
            raise ValueError("workflow control URL must select the WorkflowSourceInterface v2 control surface")
        if not isfinite(http_timeout_seconds) or http_timeout_seconds <= 0:
            raise ValueError("workflow control HTTP timeout must be finite positive seconds")
        self._control_url = control_url.rstrip("/")
        self._http_timeout_seconds = http_timeout_seconds
        self._urlopen = urlopen

    def cancellation_get(self) -> bool:
        """Return whether the platform currently requests cooperative cancellation.

        Returns:
            Current cancellation flag.
        """

        payload = self._request(operation_name="cancellation", request_payload=None, expected_status=200)
        return WorkflowControlCancellationResponse.model_validate_json(payload).is_cancellation_requested

    def registration_send(self, *, workflow_run_id: str) -> None:
        """Register the current source process against its exact logical run.

        Args:
            workflow_run_id: Exact platform-provided run identity.
        """

        self._request(
            operation_name="registration",
            request_payload=WorkflowControlRegistrationRequest(workflow_run_id=workflow_run_id),
            expected_status=204,
        )

    def safepoint_send(self, *, request: WorkflowControlSafepointRequest) -> None:
        """Atomically accept one durable step transition and its publications.

        Args:
            request: Canonical stable safepoint request.
        """

        self._request(operation_name="safepoint", request_payload=request, expected_status=204)

    def final_send(self, *, request: WorkflowControlFinalRequest) -> None:
        """Persist one end-of-work intent before the source process exits.

        Args:
            request: Canonical final result and manifest request.
        """

        self._request(operation_name="final", request_payload=request, expected_status=202)

    def _request(
        self,
        *,
        operation_name: str,
        request_payload: BaseModel | None,
        expected_status: int,
    ) -> bytes:
        """Send one request and require its exact protocol response status.

        Args:
            operation_name: Versioned operation path segment.
            request_payload: Optional typed JSON request payload.
            expected_status: Exact successful response status.

        Returns:
            Complete successful response body.

        Raises:
            WorkflowControlRequestError: If transport or protocol acceptance fails.
        """

        payload = None if request_payload is None else request_payload.model_dump_json().encode()
        request = Request(
            f"{self._control_url}/{operation_name}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="GET" if request_payload is None else "POST",
        )
        while True:
            try:
                with self._urlopen(request, timeout=self._http_timeout_seconds) as response:
                    response_payload = response.read()
                    if response.status == expected_status:
                        return response_payload
                    if response.status >= 500:
                        sleep(_CONTROL_RETRY_INTERVAL_SECONDS)
                        continue
                    raise WorkflowControlRequestError(
                        self._error_detail_get(
                            operation_name=operation_name, payload=response_payload, status=response.status
                        )
                    )
            except HTTPError as error:
                response_payload = error.read()
                if error.code >= 500:
                    sleep(_CONTROL_RETRY_INTERVAL_SECONDS)
                    continue
                raise WorkflowControlRequestError(
                    self._error_detail_get(
                        operation_name=operation_name,
                        payload=response_payload,
                        status=error.code,
                    )
                ) from error
            except TimeoutError, URLError, OSError:
                sleep(_CONTROL_RETRY_INTERVAL_SECONDS)

    def _error_detail_get(self, *, operation_name: str, payload: bytes, status: int) -> str:
        """Return one concrete stable control failure diagnostic.

        Args:
            operation_name: Failed operation path segment.
            payload: Complete response body.
            status: HTTP response status.

        Returns:
            Concrete protocol or fallback diagnostic.
        """

        try:
            error_response = WorkflowControlErrorResponse.model_validate_json(payload)
        except ValueError, json.JSONDecodeError:
            return f"Workflow control {operation_name} returned HTTP {status}."
        return (
            f"Workflow control {operation_name} returned HTTP {status}: "
            f"{error_response.error_code}: {error_response.error_detail}"
        )
