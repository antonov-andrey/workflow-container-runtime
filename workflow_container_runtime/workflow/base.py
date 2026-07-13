"""Durable publication lifecycle shared by concrete workflows."""

from typing import Generic, TypeVar, final

from dbos import DBOS
from pydantic import BaseModel
from workflow_container_contract import WorkflowResult

from workflow_container_runtime.artifact.writer import JsonArtifactWriter
from workflow_container_runtime.model import model_snapshot_get, strict_model_contract_validate
from workflow_container_runtime.step.file import input_path_get, result_path_get, verification_path_get
from workflow_container_runtime.verification import VerificationDecision, VerificationResult
from workflow_container_runtime.workflow.context import WorkflowExecutionContext

WorkflowInputT = TypeVar("WorkflowInputT", bound=BaseModel)
WorkflowResultT = TypeVar("WorkflowResultT", bound=WorkflowResult)


class WorkflowResultValidationError(RuntimeError):
    """Report actionable mechanical feedback for one workflow result."""

    def __init__(self, *, feedback_list: list[str]) -> None:
        """Store non-empty validation feedback.

        Args:
            feedback_list: Concrete workflow result corrections.

        Raises:
            ValueError: If no feedback is supplied.
        """

        if not feedback_list:
            raise ValueError("workflow result validation feedback must not be empty")
        super().__init__("; ".join(feedback_list))
        self.feedback_list = feedback_list


class WorkflowBase(Generic[WorkflowInputT, WorkflowResultT]):
    """Own standard workflow input, result, verification, and recovery."""

    def __init__(self, *, artifact_writer: JsonArtifactWriter) -> None:
        """Store the reusable artifact writer.

        Args:
            artifact_writer: Atomic writer for standard workflow files.
        """

        self._artifact_writer = artifact_writer

    @final
    async def input_write_step(
        self,
        execution_context: WorkflowExecutionContext,
        workflow_input: WorkflowInputT,
    ) -> None:
        """Publish immutable workflow input through one durable DBOS step.

        Args:
            execution_context: Current workflow execution context.
            workflow_input: Strict public workflow input.
        """

        await DBOS.run_step_async(
            {"name": f"{type(self).__name__}.input_write"},
            self._input_write,
            execution_context,
            workflow_input,
        )

    @final
    async def result_write_step(
        self,
        execution_context: WorkflowExecutionContext,
        workflow_input: WorkflowInputT,
        workflow_result: WorkflowResultT,
    ) -> WorkflowResultT:
        """Publish and validate workflow result through one durable DBOS step.

        Args:
            execution_context: Current workflow execution context.
            workflow_input: Immutable public workflow input.
            workflow_result: Concrete public workflow result.

        Returns:
            The accepted workflow result.
        """

        return await DBOS.run_step_async(
            {"name": f"{type(self).__name__}.result_write"},
            self._result_write,
            execution_context,
            workflow_input,
            workflow_result,
        )

    def result_validate(
        self,
        execution_context: WorkflowExecutionContext,
        workflow_input: WorkflowInputT,
        workflow_result: WorkflowResultT,
    ) -> None:
        """Validate optional workflow-level mechanical invariants.

        Args:
            execution_context: Current workflow execution context.
            workflow_input: Immutable workflow input.
            workflow_result: Candidate workflow result.
        """

        _ = execution_context
        _ = workflow_input
        _ = workflow_result

    def _input_write(
        self,
        execution_context: WorkflowExecutionContext,
        workflow_input: WorkflowInputT,
    ) -> None:
        """Implement immutable workflow input publication.

        Args:
            execution_context: Current workflow execution context.
            workflow_input: Strict public workflow input.

        Raises:
            RuntimeError: If the instance already belongs to another input.
        """

        strict_model_contract_validate(workflow_input, model_role="workflow input")
        workflow_input = model_snapshot_get(workflow_input)
        execution_context.workflow_instance_dir.mkdir(parents=True, exist_ok=True)
        if self._input_match(execution_context=execution_context, workflow_input=workflow_input):
            return
        if any(execution_context.workflow_instance_dir.iterdir()):
            raise RuntimeError("workflow instance contains lifecycle data without input.json")
        self._artifact_writer.write(input_path_get(execution_context.workflow_instance_dir), workflow_input)

    def _input_match(
        self,
        *,
        execution_context: WorkflowExecutionContext,
        workflow_input: WorkflowInputT,
    ) -> bool:
        """Validate an existing immutable input when present.

        Args:
            execution_context: Current workflow execution context.
            workflow_input: Candidate immutable workflow input.

        Returns:
            Whether input.json already exists with the same value.

        Raises:
            RuntimeError: If the existing input belongs to another invocation.
        """

        path = input_path_get(execution_context.workflow_instance_dir)
        if not path.exists():
            return False
        existing_input = type(workflow_input).model_validate_json(path.read_text(encoding="utf-8"))
        if existing_input != workflow_input:
            raise RuntimeError("workflow input does not match existing input.json")
        return True

    def _result_write(
        self,
        execution_context: WorkflowExecutionContext,
        workflow_input: WorkflowInputT,
        workflow_result: WorkflowResultT,
    ) -> WorkflowResultT:
        """Implement workflow result publication and recovery.

        Args:
            execution_context: Current workflow execution context.
            workflow_input: Immutable public workflow input.
            workflow_result: Candidate workflow result.

        Returns:
            Accepted persisted workflow result.

        Raises:
            RuntimeError: If replay produces a different result for the same owner.
            WorkflowResultValidationError: If the result violates a workflow invariant.
        """

        strict_model_contract_validate(workflow_input, model_role="workflow input")
        workflow_input = model_snapshot_get(workflow_input)
        if not self._input_match(execution_context=execution_context, workflow_input=workflow_input):
            raise RuntimeError("workflow input.json must exist before result publication")
        strict_model_contract_validate(workflow_result, model_role="workflow result")
        workflow_result = model_snapshot_get(workflow_result)
        instance_dir = execution_context.workflow_instance_dir
        result_path = result_path_get(instance_dir)
        verification_path = verification_path_get(instance_dir)
        result_to_validate = workflow_result
        if result_path.exists():
            existing_result = type(workflow_result).model_validate_json(result_path.read_text(encoding="utf-8"))
            if existing_result != workflow_result:
                raise RuntimeError("workflow result does not match existing result.json")
            result_to_validate = existing_result
            if verification_path.exists():
                verification = VerificationResult.model_validate_json(verification_path.read_text(encoding="utf-8"))
                if verification.status == "success" and verification.is_bound_to(
                    result_to_validate,
                    result_revision_index=1,
                ):
                    return result_to_validate
        else:
            self._artifact_writer.write(result_path, workflow_result)

        try:
            self.result_validate(execution_context, workflow_input, result_to_validate)
        except WorkflowResultValidationError as exc:
            self._artifact_writer.write(
                verification_path,
                VerificationResult.from_decision(
                    decision=VerificationDecision(status="failed", feedback_list=exc.feedback_list),
                    result=result_to_validate,
                    result_revision_index=1,
                ),
            )
            raise
        self._artifact_writer.write(
            verification_path,
            VerificationResult.from_decision(
                decision=VerificationDecision(status="success", feedback_list=[]),
                result=result_to_validate,
                result_revision_index=1,
            ),
        )
        return result_to_validate
