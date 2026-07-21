"""Behavior tests for source-declared workflow control request generation."""

import pytest

from workflow_container_contract import WorkflowDefinition, WorkflowResult
from workflow_container_runtime.request import WorkflowControlRequestBuilder


def _builder_get() -> WorkflowControlRequestBuilder:
    """Return one builder with source-owned manifests and one step.

    Returns:
        Control request builder used by the tests.
    """

    return WorkflowControlRequestBuilder(
        workflow_definition=WorkflowDefinition.model_validate(
            {
                "build": {"dockerfile_path": "Dockerfile"},
                "command": ["run"],
                "data": {
                    "run": {
                        "result": "result/{brand_key}",
                        "workspace": "workspace/{brand_key}",
                    }
                },
                "input_schema_path": "input.schema.json",
                "name": "sample_workflow",
                "step": {"brand_complete": {}},
            }
        )
    )


def test_control_request_builder_generates_exact_source_owned_requests() -> None:
    """Generate manifest, safepoint, and final requests through the saved source."""

    builder = _builder_get()
    result_request = builder.manifest_build(
        manifest_key="result",
        path_parameter_by_name_map={"brand_key": "acme"},
    )
    workspace_request = builder.manifest_build(
        manifest_key="workspace",
        path_parameter_by_name_map={"brand_key": "acme"},
    )
    manifest_request_list = [result_request, workspace_request]

    assert builder.manifest_path_get(result_request) == "result/acme"
    assert builder.safepoint_build(
        manifest_request_list=manifest_request_list,
        step_identity="brand/acme",
        step_key="brand_complete",
        transition_identity="brand/acme/completed",
    ).model_dump(mode="json") == {
        "manifest_request_list": [
            {"manifest_key": "result", "path_parameter_by_name_map": {"brand_key": "acme"}},
            {"manifest_key": "workspace", "path_parameter_by_name_map": {"brand_key": "acme"}},
        ],
        "step_identity": "brand/acme",
        "step_key": "brand_complete",
        "transition_identity": "brand/acme/completed",
    }
    workflow_result = WorkflowResult(error_list=[], status="success", warning_list=[])
    assert (
        builder.final_build(
            manifest_request_list=[],
            transition_identity="run/completed",
            workflow_result=workflow_result,
        ).workflow_result
        == workflow_result
    )


@pytest.mark.parametrize(
    ("manifest_key", "path_parameter_by_name_map", "error_pattern"),
    [
        ("missing", {"brand_key": "acme"}, "declared run manifests"),
        ("result", {}, "exact path parameter set"),
        ("result", {"brand_key": "acme", "extra": "value"}, "exact path parameter set"),
        ("result", {"brand_key": "../acme"}, "safe non-empty POSIX path segments"),
    ],
)
def test_control_request_builder_rejects_unresolved_manifests(
    manifest_key: str,
    path_parameter_by_name_map: dict[str, str],
    error_pattern: str,
) -> None:
    """Reject missing declarations, parameter drift, and unsafe values.

    Args:
        manifest_key: Candidate manifest key.
        path_parameter_by_name_map: Candidate exact template values.
        error_pattern: Expected validation error fragment.
    """

    with pytest.raises(ValueError, match=error_pattern):
        _builder_get().manifest_build(
            manifest_key=manifest_key,
            path_parameter_by_name_map=path_parameter_by_name_map,
        )


def test_control_request_builder_rejects_undeclared_step_and_manifest() -> None:
    """Reject transition requests that bypass the saved source declaration."""

    builder = _builder_get()
    request = builder.manifest_build(
        manifest_key="result",
        path_parameter_by_name_map={"brand_key": "acme"},
    )

    with pytest.raises(ValueError, match="declared workflow steps"):
        builder.safepoint_build(
            manifest_request_list=[request],
            step_identity="brand/acme",
            step_key="missing",
            transition_identity="brand/acme/completed",
        )
