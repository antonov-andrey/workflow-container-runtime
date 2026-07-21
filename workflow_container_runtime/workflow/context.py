"""Execution context and runtime capability for workflow owners."""

from pathlib import Path
from pydantic import BaseModel, ConfigDict, model_validator
from workflow_container_contract import WorkflowRunContext

from workflow_container_runtime.capability import WorkflowRuntimeCapability
from workflow_container_runtime.instance import instance_key_validate, instance_path_validate
from workflow_container_runtime.step.context import WorkflowStepExecutionContext
from workflow_container_runtime.step.file import input_path_get
from workflow_container_runtime.data import WorkflowDataPath


class WorkflowExecutionContext(BaseModel):
    """Filesystem and capability context of one workflow instance."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    data_path: WorkflowDataPath
    result_dir: Path
    run_context: WorkflowRunContext
    runtime_capability: WorkflowRuntimeCapability
    workflow_instance_dir: Path

    @model_validator(mode="after")
    def path_validate(self) -> Self:
        """Keep the workflow instance inside the result root.

        Returns:
            Validated workflow execution context.
        """

        instance_path_validate(
            instance_dir=self.workflow_instance_dir,
            result_dir=self.result_dir,
            role="workflow_instance_dir",
        )
        return self

    def for_child_workflow(
        self,
        *,
        runtime_capability: WorkflowRuntimeCapability,
        workflow_instance_key: str,
    ) -> WorkflowExecutionContext:
        """Build one deterministic child-workflow context.

        Args:
            runtime_capability: Capabilities explicitly granted to the child.
            workflow_instance_key: Stable child workflow identity.

        Returns:
            Child workflow execution context.
        """

        instance_key_validate(workflow_instance_key)
        return WorkflowExecutionContext(
            data_path=self.data_path,
            result_dir=self.result_dir,
            run_context=self.run_context,
            runtime_capability=runtime_capability,
            workflow_instance_dir=self.workflow_instance_dir / "workflow" / workflow_instance_key,
        )

    def for_step(
        self,
        *,
        runtime_capability: WorkflowRuntimeCapability,
        step_instance_key: str,
    ) -> WorkflowStepExecutionContext:
        """Build one deterministic child-step context.

        Args:
            runtime_capability: Capabilities explicitly granted to the step.
            step_instance_key: Stable step identity.

        Returns:
            Child step execution context.
        """

        instance_key_validate(step_instance_key)
        return WorkflowStepExecutionContext(
            data_path=self.data_path,
            result_dir=self.result_dir,
            run_context=self.run_context,
            runtime_capability=runtime_capability,
            step_instance_dir=self.workflow_instance_dir / "step" / step_instance_key,
            workflow_input_path=input_path_get(self.workflow_instance_dir).relative_to(self.result_dir),
        )
