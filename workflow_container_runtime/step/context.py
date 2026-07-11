"""Execution context for one workflow step instance."""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from workflow_container_runtime.capability import WorkflowRuntimeCapability
from workflow_container_runtime.instance import instance_path_validate


class WorkflowStepExecutionContext(BaseModel):
    """Filesystem and capability context of one step instance."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    result_dir: Path
    runtime_capability: WorkflowRuntimeCapability
    step_instance_dir: Path

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
        return self
