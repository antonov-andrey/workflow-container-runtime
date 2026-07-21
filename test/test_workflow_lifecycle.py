"""Behavior tests for the common workflow publication lifecycle."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict
from workflow_container_contract import WorkflowResult, WorkflowRunContext

from workflow_container_runtime.artifact.writer import JsonArtifactWriter
from workflow_container_runtime.step.file import input_path_get, result_path_get, verification_path_get
from workflow_container_runtime.verification import VerificationDecision, VerificationResult
from workflow_container_runtime.workflow.base import WorkflowBase, WorkflowResultValidationError
from workflow_container_runtime.workflow.context import WorkflowExecutionContext, WorkflowRuntimeCapability
from workflow_container_runtime.data import WorkflowDataPath


class ExampleWorkflowInput(BaseModel):
    """Strict workflow input used by lifecycle tests."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    value: str


class ExampleWorkflowResult(WorkflowResult):
    """Closed concrete workflow result used by lifecycle tests."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    output: str


class ExampleWorkflow(WorkflowBase[ExampleWorkflowInput, ExampleWorkflowResult]):
    """Workflow with one optional result invariant."""

    def result_validate(
        self,
        execution_context: WorkflowExecutionContext,
        workflow_input: ExampleWorkflowInput,
        workflow_result: ExampleWorkflowResult,
    ) -> None:
        """Reject a result that does not derive from the input."""

        _ = execution_context
        if workflow_result.output != workflow_input.value.upper():
            raise WorkflowResultValidationError(feedback_list=["Return the uppercase input value."])


def _context_get(tmp_path: Path) -> WorkflowExecutionContext:
    """Return one root workflow context."""

    return WorkflowExecutionContext(
        data_path=WorkflowDataPath(
            result_path=(tmp_path / "data-result").resolve(),
            workspace_path=(tmp_path / "data-workspace").resolve(),
        ),
        result_dir=tmp_path,
        run_context=WorkflowRunContext(
            interface_major_version=2,
            version=1,
            workflow_id="workflow-id",
            workflow_name="example",
            workflow_run_id="20260719123456789",
            workflow_run_timestamp=datetime(2026, 7, 19, 12, 34, 56, 789000, tzinfo=UTC),
            workflow_source_id="source-id",
            workflow_source_version_id="source-version-id",
        ),
        runtime_capability=WorkflowRuntimeCapability(browser=None),
        workflow_instance_dir=tmp_path / "workflow" / "example",
    )


def test_workflow_base_publishes_input_result_and_verification(tmp_path: Path) -> None:
    """Publish one complete workflow recovery bundle."""

    context = _context_get(tmp_path)
    workflow = ExampleWorkflow(artifact_writer=JsonArtifactWriter())
    workflow_input = ExampleWorkflowInput(value="text")
    workflow_result = ExampleWorkflowResult(
        status="success",
        error_list=[],
        warning_list=[],
        output="TEXT",
    )

    workflow._input_write(context, workflow_input)
    returned_result = workflow._result_write(context, workflow_input, workflow_result)

    assert returned_result == workflow_result
    assert (
        ExampleWorkflowInput.model_validate_json(
            input_path_get(context.workflow_instance_dir).read_text(encoding="utf-8")
        )
        == workflow_input
    )
    assert (
        ExampleWorkflowResult.model_validate_json(
            result_path_get(context.workflow_instance_dir).read_text(encoding="utf-8")
        )
        == workflow_result
    )
    assert VerificationResult.model_validate_json(
        verification_path_get(context.workflow_instance_dir).read_text(encoding="utf-8")
    ) == VerificationResult.from_decision(
        decision=VerificationDecision(status="success", feedback_list=[]),
        result=workflow_result,
        result_revision_index=1,
    )


def test_workflow_base_rejects_changed_input_for_same_instance(tmp_path: Path) -> None:
    """Keep input immutable inside one workflow instance directory."""

    context = _context_get(tmp_path)
    workflow = ExampleWorkflow(artifact_writer=JsonArtifactWriter())
    workflow._input_write(context, ExampleWorkflowInput(value="first"))

    with pytest.raises(RuntimeError, match="workflow input does not match existing input.json"):
        workflow._input_write(context, ExampleWorkflowInput(value="second"))


@pytest.mark.parametrize(
    "existing_relative_path",
    (Path("result.json"), Path("verification.json"), Path("artifact/evidence.json")),
    ids=("result", "verification", "declared-artifact"),
)
def test_workflow_base_rejects_missing_input_for_started_instance(
    tmp_path: Path,
    existing_relative_path: Path,
) -> None:
    """Do not assign a new input identity to an already-started workflow instance."""

    context = _context_get(tmp_path)
    existing_path = context.workflow_instance_dir / existing_relative_path
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text("existing\n", encoding="utf-8")
    workflow = ExampleWorkflow(artifact_writer=JsonArtifactWriter())

    with pytest.raises(RuntimeError):
        workflow._input_write(context, ExampleWorkflowInput(value="text"))

    assert not input_path_get(context.workflow_instance_dir).exists()
    assert existing_path.read_text(encoding="utf-8") == "existing\n"


def test_workflow_base_publishes_failed_verification_for_invalid_result(tmp_path: Path) -> None:
    """Persist actionable feedback before propagating a result-contract error."""

    context = _context_get(tmp_path)
    workflow = ExampleWorkflow(artifact_writer=JsonArtifactWriter())
    workflow_input = ExampleWorkflowInput(value="text")
    workflow_result = ExampleWorkflowResult(status="success", error_list=[], warning_list=[], output="wrong")
    workflow._input_write(context, workflow_input)

    with pytest.raises(WorkflowResultValidationError):
        workflow._result_write(context, workflow_input, workflow_result)

    assert VerificationResult.model_validate_json(
        verification_path_get(context.workflow_instance_dir).read_text(encoding="utf-8")
    ) == VerificationResult.from_decision(
        decision=VerificationDecision(status="failed", feedback_list=["Return the uppercase input value."]),
        result=workflow_result,
        result_revision_index=1,
    )


def test_workflow_result_publication_requires_pre_orchestration_input(tmp_path: Path) -> None:
    """Do not let result publication silently create a missing workflow input."""

    context = _context_get(tmp_path)
    workflow = ExampleWorkflow(artifact_writer=JsonArtifactWriter())

    with pytest.raises(RuntimeError, match="workflow input.json must exist before result publication"):
        workflow._result_write(
            context,
            ExampleWorkflowInput(value="text"),
            ExampleWorkflowResult(status="success", error_list=[], warning_list=[], output="TEXT"),
        )

    assert not input_path_get(context.workflow_instance_dir).exists()
