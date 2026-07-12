"""Execution context for one workflow step instance."""

from pathlib import Path
from typing import Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, model_validator

from workflow_container_runtime.capability import WorkflowRuntimeCapability
from workflow_container_runtime.instance import instance_path_validate

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
        """Keep the step instance inside the result root.

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
        return self


class WorkflowStepInvocation(BaseModel, Generic[InputSourceT]):
    """Bind one independent step context to its selected public input source."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    execution_context: WorkflowStepExecutionContext
    input_source: InputSourceT
