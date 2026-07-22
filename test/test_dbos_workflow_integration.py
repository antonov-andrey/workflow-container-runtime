"""Integration coverage for a concrete DBOS-configured workflow instance."""

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from dbos import DBOS, DBOSConfig, DBOSConfiguredInstance, pydantic_args_validator
from pydantic import BaseModel, ConfigDict
from workflow_container_contract import WorkflowResult, WorkflowRunContext

from workflow_container_runtime.artifact import JsonArtifactWriter
from workflow_container_runtime.capability import NetworkProxyRuntimeCapability
from workflow_container_runtime.step import (
    WorkflowStepDeterministicBase,
    WorkflowStepExecutionContext,
)
from workflow_container_runtime.step.file import input_path_get, result_path_get, verification_path_get
from workflow_container_runtime.verification import VerificationDecision, VerificationResult
from workflow_container_runtime.workflow import WorkflowBase, WorkflowExecutionContext, WorkflowRuntimeCapability
from workflow_container_runtime.data import WorkflowDataPath


class IntegrationModel(BaseModel):
    """Provide the strict model contract used by integration payloads."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)


class IntegrationWorkflowInput(IntegrationModel):
    """Carry one value into the configured workflow."""

    value: str


class IntegrationStepInputSource(IntegrationModel):
    """Carry the workflow-selected dependency into one DBOS step."""

    value: str


class IntegrationStepInput(IntegrationModel):
    """Persist the deterministic step input."""

    value: str


class IntegrationStepResult(IntegrationModel):
    """Expose the deterministic step output."""

    output: str


class IntegrationWorkflowResult(WorkflowResult):
    """Expose the concrete workflow result through DBOS."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    output: str


class IntegrationDeterministicStep(
    WorkflowStepDeterministicBase[IntegrationStepInputSource, IntegrationStepInput, IntegrationStepResult]
):
    """Uppercase one value through the shared deterministic lifecycle."""

    result_model: ClassVar[type[IntegrationStepResult]] = IntegrationStepResult

    def input_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: IntegrationStepInputSource,
    ) -> IntegrationStepInput:
        """Build the persisted input from the DBOS wrapper argument."""

        _ = execution_context
        return IntegrationStepInput(value=input_source.value)

    def result_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: IntegrationStepInput,
    ) -> IntegrationStepResult:
        """Build the deterministic uppercase result."""

        _ = execution_context
        return IntegrationStepResult(output=step_input.value.upper())


def test_configured_dbos_workflow_publishes_typed_standard_bundle(tmp_path: Path) -> None:
    """Run one stateless configured workflow in two independent result trees."""

    database_path = tmp_path / "dbos.sqlite"
    config: DBOSConfig = {
        "name": "runtime_integration_test",
        "run_admin_server": False,
        "system_database_url": f"sqlite:///{database_path}",
    }

    DBOS.destroy(destroy_registry=True)
    DBOS(config=config)
    try:

        @DBOS.dbos_class()
        class IntegrationWorkflow(
            WorkflowBase[IntegrationWorkflowInput, IntegrationWorkflowResult],
            DBOSConfiguredInstance,
        ):
            """Own one reusable workflow and its deterministic DBOS step boundary."""

            def __init__(self) -> None:
                """Register the instance and construct reusable lifecycle dependencies."""

                WorkflowBase.__init__(self, artifact_writer=JsonArtifactWriter())
                DBOSConfiguredInstance.__init__(self, config_name="integration")
                self._step = IntegrationDeterministicStep(artifact_writer=JsonArtifactWriter())

            @DBOS.step()
            def uppercase_step(
                self,
                execution_context: WorkflowStepExecutionContext,
                input_source: IntegrationStepInputSource,
            ) -> IntegrationStepResult:
                """Run the shared deterministic lifecycle inside one DBOS step."""

                return self._step.run(execution_context, input_source)

            @DBOS.workflow(validate_args=pydantic_args_validator)
            async def run(
                self,
                execution_context: WorkflowExecutionContext,
                workflow_input: IntegrationWorkflowInput,
            ) -> IntegrationWorkflowResult:
                """Publish workflow input, orchestrate one step, and publish the result."""

                await self.input_write_step(execution_context, workflow_input)
                step_result = self.uppercase_step(
                    execution_context.for_step(
                        runtime_capability=WorkflowRuntimeCapability(
                            browser=None,
                            network_proxy=NetworkProxyRuntimeCapability(proxy_by_name_map={}),
                        ),
                        step_instance_key="uppercase",
                    ),
                    IntegrationStepInputSource(value=workflow_input.value),
                )
                workflow_result = IntegrationWorkflowResult(
                    status="success",
                    error_list=[],
                    warning_list=[],
                    output=step_result.output,
                )
                return await self.result_write_step(
                    execution_context,
                    workflow_input,
                    workflow_result,
                )

        workflow = IntegrationWorkflow()
        DBOS.launch()

        case_list = [("first", "typed"), ("second", "independent")]
        for case_name, input_value in case_list:
            result_dir = tmp_path / case_name / "result"
            workflow_instance_dir = result_dir / "workflow" / "integration"
            execution_context = WorkflowExecutionContext(
                data_path=WorkflowDataPath(
                    result_path=(tmp_path / case_name / "data-result").resolve(),
                    workspace_path=(tmp_path / case_name / "data-workspace").resolve(),
                ),
                result_dir=result_dir,
                run_context=WorkflowRunContext(
                    interface_major_version=2,
                    version=1,
                    workflow_id="workflow-id",
                    workflow_name="integration",
                    workflow_run_id="20260719123456789",
                    workflow_run_timestamp=datetime(2026, 7, 19, 12, 34, 56, 789000, tzinfo=UTC),
                    workflow_source_id="source-id",
                    workflow_source_version_id="source-version-id",
                ),
                runtime_capability=WorkflowRuntimeCapability(
                    browser=None,
                    network_proxy=NetworkProxyRuntimeCapability(proxy_by_name_map={}),
                ),
                workflow_instance_dir=workflow_instance_dir,
            )
            workflow_input = IntegrationWorkflowInput(value=input_value)

            workflow_handle = DBOS.start_workflow(workflow.run, execution_context, workflow_input)
            workflow_result = workflow_handle.get_result()
            persisted_workflow_result = DBOS.retrieve_workflow(workflow_handle.workflow_id).get_result()
            expected_workflow_result = IntegrationWorkflowResult(
                status="success",
                error_list=[],
                warning_list=[],
                output=input_value.upper(),
            )

            assert isinstance(workflow_result, IntegrationWorkflowResult)
            assert isinstance(persisted_workflow_result, IntegrationWorkflowResult)
            assert persisted_workflow_result == workflow_result == expected_workflow_result

            step_instance_dir = workflow_instance_dir / "step" / "uppercase"
            expected_path_set = {
                input_path_get(workflow_instance_dir),
                result_path_get(workflow_instance_dir),
                verification_path_get(workflow_instance_dir),
                input_path_get(step_instance_dir),
                result_path_get(step_instance_dir),
                verification_path_get(step_instance_dir),
            }
            assert {path for path in result_dir.rglob("*") if path.is_file()} == expected_path_set
            assert (
                IntegrationWorkflowInput.model_validate_json(
                    input_path_get(workflow_instance_dir).read_text(encoding="utf-8")
                )
                == workflow_input
            )
            assert (
                IntegrationWorkflowResult.model_validate_json(
                    result_path_get(workflow_instance_dir).read_text(encoding="utf-8")
                )
                == workflow_result
            )
            assert IntegrationStepInput.model_validate_json(
                input_path_get(step_instance_dir).read_text(encoding="utf-8")
            ) == IntegrationStepInput(value=input_value)
            step_result = IntegrationStepResult(output=input_value.upper())
            assert (
                IntegrationStepResult.model_validate_json(
                    result_path_get(step_instance_dir).read_text(encoding="utf-8")
                )
                == step_result
            )
            for instance_dir, result in (
                (workflow_instance_dir, workflow_result),
                (step_instance_dir, step_result),
            ):
                assert VerificationResult.model_validate_json(
                    verification_path_get(instance_dir).read_text(encoding="utf-8")
                ) == VerificationResult.from_decision(
                    decision=VerificationDecision(status="success", feedback_list=[]),
                    result=result,
                    result_revision_index=1,
                )
    finally:
        DBOS.destroy(destroy_registry=True)
