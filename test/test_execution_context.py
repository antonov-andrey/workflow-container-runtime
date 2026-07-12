"""Behavior tests for workflow and step execution contexts."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from workflow_container_runtime.capability import BrowserRuntimeCapability
from workflow_container_runtime.step.context import WorkflowStepExecutionContext
from workflow_container_runtime.workflow.context import WorkflowExecutionContext, WorkflowRuntimeCapability


def test_workflow_context_builds_deterministic_child_directories(tmp_path: Path) -> None:
    """Derive child workflow and step directories below the current owner."""

    runtime_capability = WorkflowRuntimeCapability(browser=BrowserRuntimeCapability(mcp_url="http://browser/mcp"))
    context = WorkflowExecutionContext(
        result_dir=tmp_path,
        runtime_capability=runtime_capability,
        workflow_instance_dir=tmp_path / "workflow" / "run",
    )

    child = context.for_child_workflow(
        runtime_capability=runtime_capability,
        workflow_instance_key="brand_defacto",
    )
    step = child.for_step(runtime_capability=runtime_capability, step_instance_key="source_discover")

    assert child.workflow_instance_dir == tmp_path / "workflow" / "run" / "workflow" / "brand_defacto"
    assert step.step_instance_dir == child.workflow_instance_dir / "step" / "source_discover"
    assert step.workflow_input_path == Path("workflow/run/workflow/brand_defacto/input.json")
    assert not step.workflow_input_path.is_absolute()


def test_workflow_context_rejects_paths_outside_result_root(tmp_path: Path) -> None:
    """Keep every owner directory inside its run result root."""

    with pytest.raises(ValidationError, match="workflow_instance_dir must be inside result_dir"):
        WorkflowExecutionContext(
            result_dir=tmp_path / "inside",
            runtime_capability=WorkflowRuntimeCapability(browser=None),
            workflow_instance_dir=tmp_path / "outside",
        )


def test_workflow_context_rejects_symlink_escape(tmp_path: Path) -> None:
    """Resolve existing ancestors before accepting an instance directory."""

    result_dir = tmp_path / "result"
    outside_dir = tmp_path / "outside"
    result_dir.mkdir()
    outside_dir.mkdir()
    (result_dir / "workflow").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(ValidationError, match="workflow_instance_dir must be inside result_dir"):
        WorkflowExecutionContext(
            result_dir=result_dir,
            runtime_capability=WorkflowRuntimeCapability(browser=None),
            workflow_instance_dir=result_dir / "workflow" / "run",
        )


def test_step_context_rejects_unrelated_workflow_input_path(tmp_path: Path) -> None:
    """Require a step to bind only the input of its containing workflow instance."""

    with pytest.raises(ValidationError, match="current workflow input"):
        WorkflowStepExecutionContext(
            result_dir=tmp_path,
            runtime_capability=WorkflowRuntimeCapability(browser=None),
            step_instance_dir=tmp_path / "workflow" / "run" / "step" / "source_discover",
            workflow_input_path=Path("workflow/unrelated/input.json"),
        )


def test_step_context_rejects_symlinked_workflow_input_escape(tmp_path: Path) -> None:
    """Reject the expected input path when its file target escapes the result root."""

    outside_input_path = tmp_path.parent / "outside-input.json"
    outside_input_path.write_text("{}", encoding="utf-8")
    workflow_instance_dir = tmp_path / "workflow" / "run"
    workflow_instance_dir.mkdir(parents=True)
    (workflow_instance_dir / "input.json").symlink_to(outside_input_path)

    with pytest.raises(ValidationError, match="workflow_input_path must resolve inside result_dir"):
        WorkflowStepExecutionContext(
            result_dir=tmp_path,
            runtime_capability=WorkflowRuntimeCapability(browser=None),
            step_instance_dir=workflow_instance_dir / "step" / "source_discover",
            workflow_input_path=Path("workflow/run/input.json"),
        )


@pytest.mark.parametrize("instance_key", ["", ".", "../escape", "nested/key"])
def test_workflow_context_rejects_unsafe_instance_keys(tmp_path: Path, instance_key: str) -> None:
    """Accept one filesystem segment as an instance identity."""

    context = WorkflowExecutionContext(
        result_dir=tmp_path,
        runtime_capability=WorkflowRuntimeCapability(browser=None),
        workflow_instance_dir=tmp_path / "workflow" / "run",
    )

    with pytest.raises(ValueError, match="instance key"):
        context.for_step(runtime_capability=context.runtime_capability, step_instance_key=instance_key)
