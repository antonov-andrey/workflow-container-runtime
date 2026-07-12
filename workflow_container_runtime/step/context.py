"""Execution context for one workflow step instance."""

from pathlib import Path
from typing import Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, model_validator

from workflow_container_runtime.capability import WorkflowRuntimeCapability
from workflow_container_runtime.instance import instance_path_validate
from workflow_container_runtime.step.file import input_path_get

InputSourceT = TypeVar("InputSourceT", bound=BaseModel)


class WorkflowStepExecutionContext(BaseModel):
    """Filesystem and capability context of one step instance."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    result_dir: Path
    runtime_capability: WorkflowRuntimeCapability
    step_instance_dir: Path
    workflow_input_path: Path

    @model_validator(mode="after")
    def path_validate(self) -> Self:
        """Keep the step and its workflow input identity inside the result root.

        Returns:
            Validated step execution context.
        """

        instance_path_validate(
            instance_dir=self.step_instance_dir,
            result_dir=self.result_dir,
            role="step_instance_dir",
        )
        if self.workflow_input_path.is_absolute() or ".." in self.workflow_input_path.parts:
            raise ValueError("workflow_input_path must be relative to result_dir")
        if self.step_instance_dir.parent.name != "step":
            raise ValueError("step_instance_dir must be inside one workflow step directory")
        workflow_input_path = self.result_dir / self.workflow_input_path
        expected_workflow_input_path = input_path_get(self.step_instance_dir.parent.parent).relative_to(self.result_dir)
        if self.workflow_input_path != expected_workflow_input_path:
            raise ValueError("workflow_input_path must identify the current workflow input")
        try:
            workflow_input_path.resolve().relative_to(self.result_dir.resolve())
        except ValueError as exc:
            raise ValueError("workflow_input_path must resolve inside result_dir") from exc
        return self


class WorkflowStepInvocation(BaseModel, Generic[InputSourceT]):
    """Bind one independent step context to its selected public input source."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    execution_context: WorkflowStepExecutionContext
    input_source: InputSourceT
