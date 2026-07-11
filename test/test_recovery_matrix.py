"""Behavior tests for crash recovery and standard-artifact identity guards."""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
from dbos import DBOS, DBOSConfig
from pydantic import BaseModel, ConfigDict
from workflow_container_contract import WorkflowResult

from workflow_container_runtime.artifact import (
    ArtifactMaterializationPolicy,
    ArtifactMaterializer,
    JsonArtifactWriter,
)
from workflow_container_runtime.prompt.renderer import PromptRenderer
from workflow_container_runtime.step import (
    CodexExecutionRetryPolicy,
    StepResultValidationError,
    WorkflowStepCodexBase,
    WorkflowStepCodexConfig,
    WorkflowStepCodexState,
    WorkflowStepExecutionContext,
)
from workflow_container_runtime.step.file import input_path_get, result_path_get, state_path_get, verification_path_get
from workflow_container_runtime.state import SqliteStateStore, SqliteStateTable, state_database_path_get
from workflow_container_runtime.verification import VerificationDecision, VerificationResult
from workflow_container_runtime.workflow import (
    WorkflowBase,
    WorkflowExecutionContext,
    WorkflowResultValidationError,
    WorkflowRuntimeCapability,
)


class RecoveryModel(BaseModel):
    """Provide one strict model contract for recovery fixtures."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)


class RecoveryWorkflowInput(RecoveryModel):
    """Carry one immutable workflow input value."""

    value: str


class RecoveryWorkflowResult(WorkflowResult):
    """Carry one workflow result through publication recovery."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    output: str


class RecoveryStepInputSource(RecoveryModel):
    """Carry one public dependency into a recoverable Codex step."""

    value: str


class RecoveryStepInput(RecoveryModel):
    """Persist one recoverable Codex step input."""

    value: str


class RecoveryActionOutput(RecoveryModel):
    """Represent one typed fake-Codex action response."""

    output: str


class RecoveryStepResult(RecoveryModel):
    """Represent one public Codex step result."""

    output: str


class RecoveryRecord(RecoveryModel):
    """Represent one current recovery row."""

    record_key: str
    value: str


class RecordingJsonArtifactWriter(JsonArtifactWriter):
    """Record standard-file mutations while retaining real durable writes."""

    def __init__(self) -> None:
        """Initialize an empty operation list."""

        self.operation_list: list[tuple[str, str, dict[str, object] | None]] = []

    def write(self, path: Path, value: BaseModel) -> None:
        """Record an immutable payload snapshot and publish it."""

        self.operation_list.append(("write", path.name, value.model_dump(mode="python")))
        super().write(path, value)


class RecoveryWorkflow(WorkflowBase[RecoveryWorkflowInput, RecoveryWorkflowResult]):
    """Validate persisted workflow results against one current invariant."""

    def __init__(self, *, accepted_output: str | None, artifact_writer: JsonArtifactWriter) -> None:
        """Store the current invariant and validation count."""

        super().__init__(artifact_writer=artifact_writer)
        self.result_validate_count = 0
        self._accepted_output = accepted_output

    def result_validate(
        self,
        execution_context: WorkflowExecutionContext,
        workflow_input: RecoveryWorkflowInput,
        workflow_result: RecoveryWorkflowResult,
    ) -> None:
        """Revalidate the current persisted result without orchestration."""

        _ = execution_context
        _ = workflow_input
        self.result_validate_count += 1
        if self._accepted_output is not None and workflow_result.output != self._accepted_output:
            raise WorkflowResultValidationError(feedback_list=["Return the accepted output."])


class ScriptedCodexRunner:
    """Return scripted typed responses or raise one scripted crash."""

    def __init__(self, output_list: list[BaseModel | BaseException]) -> None:
        """Store queued outcomes and initialize the call log."""

        self.call_list: list[dict[str, object]] = []
        self._output_list = list(output_list)

    def run(
        self,
        *,
        diagnostic_dir: Path,
        output_model: type[BaseModel],
        prompt: str,
        retry_policy: CodexExecutionRetryPolicy,
        runtime_capability: WorkflowRuntimeCapability,
        working_directory: Path,
    ) -> BaseModel:
        """Record one external call and return its scripted outcome."""

        self.call_list.append(
            {
                "diagnostic_dir": diagnostic_dir,
                "output_model": output_model,
                "prompt": prompt,
                "retry_policy": retry_policy,
                "runtime_capability": runtime_capability,
                "working_directory": working_directory,
            }
        )
        output = self._output_list.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output


class RecoveryCodexStep(
    WorkflowStepCodexBase[RecoveryStepInputSource, RecoveryStepInput, RecoveryActionOutput, RecoveryStepResult]
):
    """Exercise the generic Codex recovery FSM with scripted responses."""

    action_output_model: ClassVar[type[RecoveryActionOutput]] = RecoveryActionOutput
    result_model: ClassVar[type[RecoveryStepResult]] = RecoveryStepResult
    state_model: ClassVar[type[WorkflowStepCodexState]] = WorkflowStepCodexState
    step_key: ClassVar[str] = "recovery"

    def input_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
    ) -> RecoveryStepInput:
        """Build the persisted step input."""

        _ = execution_context
        return RecoveryStepInput(value=input_source.value)

    def result_from_action_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: RecoveryStepInput,
        action_output: RecoveryActionOutput,
    ) -> RecoveryStepResult:
        """Build the public result from one scripted action output."""

        _ = execution_context
        _ = step_input
        return RecoveryStepResult(output=action_output.output)

    def result_validate(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: RecoveryStepInput,
        result: RecoveryStepResult,
    ) -> None:
        """Reject one explicit mechanically invalid result."""

        _ = execution_context
        _ = step_input
        if result.output == "invalid":
            raise StepResultValidationError(feedback_list=["Replace the invalid output."])


@pytest.fixture(scope="module", autouse=True)
def dbos_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Configure the official DBOS run-step boundary for workflow tests."""

    DBOS.destroy(destroy_registry=True)
    database_path = tmp_path_factory.mktemp("recovery_dbos") / "system.sqlite"
    config: DBOSConfig = {
        "name": "runtime_recovery_test",
        "system_database_url": f"sqlite:///{database_path}",
    }
    DBOS(config=config)
    yield
    DBOS.destroy(destroy_registry=True)


def _codex_step_get(
    *,
    codex_runner: ScriptedCodexRunner,
    template_dir: Path,
    writer: JsonArtifactWriter,
) -> RecoveryCodexStep:
    """Build one Codex step with deterministic local templates."""

    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "recovery.md.j2").write_text("input={{ input_path }}", encoding="utf-8")
    (template_dir / "recovery_verify.md.j2").write_text(
        "input={{ input_path }} result={{ step_result_path }}",
        encoding="utf-8",
    )
    return RecoveryCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=writer,
        codex_runner=codex_runner,
        config=WorkflowStepCodexConfig(
            artifact_materialization_policy=ArtifactMaterializationPolicy(artifact_root_tuple=()),
            attempt_limit=3,
            execution_retry_policy=CodexExecutionRetryPolicy(attempt_limit=1),
        ),
        prompt_renderer=PromptRenderer(template_dir=template_dir),
    )


def _step_context_get(tmp_path: Path) -> WorkflowStepExecutionContext:
    """Build one stable Codex step execution context."""

    return WorkflowStepExecutionContext(
        result_dir=tmp_path,
        runtime_capability=WorkflowRuntimeCapability(browser=None),
        step_instance_dir=tmp_path / "workflow" / "run" / "step" / "recovery",
    )


def _workflow_context_get(tmp_path: Path, *, instance_key: str) -> WorkflowExecutionContext:
    """Build one stable workflow execution context."""

    return WorkflowExecutionContext(
        result_dir=tmp_path,
        runtime_capability=WorkflowRuntimeCapability(browser=None),
        workflow_instance_dir=tmp_path / "workflow" / instance_key,
    )


def test_workflow_result_without_verdict_is_revalidated_without_republication(tmp_path: Path) -> None:
    """Recover a published workflow result by recreating only its verdict."""

    writer = RecordingJsonArtifactWriter()
    workflow = RecoveryWorkflow(accepted_output="TEXT", artifact_writer=writer)
    context = _workflow_context_get(tmp_path, instance_key="missing_verdict")
    workflow_input = RecoveryWorkflowInput(value="text")
    workflow_result = RecoveryWorkflowResult(status="success", error_list=[], warning_list=[], output="TEXT")
    workflow.input_write_step(context, workflow_input)
    workflow.result_write_step(context, workflow_input, workflow_result)
    verification_path_get(context.workflow_instance_dir).unlink()
    writer.operation_list.clear()

    recovered_result = workflow.result_write_step(
        context,
        workflow_input,
        workflow_result.model_copy(deep=True),
    )

    assert recovered_result == workflow_result
    assert workflow.result_validate_count == 2
    assert not any(operation[0:2] == ("write", "result.json") for operation in writer.operation_list)
    assert VerificationResult.model_validate_json(
        verification_path_get(context.workflow_instance_dir).read_text(encoding="utf-8")
    ) == VerificationResult.from_decision(
        decision=VerificationDecision(status="success", feedback_list=[]),
        result=workflow_result,
        result_revision_index=1,
    )


def test_workflow_failed_verdict_is_revalidated_with_current_validator(tmp_path: Path) -> None:
    """Replace a failed verdict when the current validator accepts the stored result."""

    writer = RecordingJsonArtifactWriter()
    context = _workflow_context_get(tmp_path, instance_key="failed_verdict")
    workflow_input = RecoveryWorkflowInput(value="text")
    workflow_result = RecoveryWorkflowResult(status="success", error_list=[], warning_list=[], output="stored")
    rejecting_workflow = RecoveryWorkflow(accepted_output="different", artifact_writer=writer)
    rejecting_workflow.input_write_step(context, workflow_input)

    with pytest.raises(WorkflowResultValidationError):
        rejecting_workflow.result_write_step(context, workflow_input, workflow_result)

    assert VerificationResult.model_validate_json(
        verification_path_get(context.workflow_instance_dir).read_text(encoding="utf-8")
    ) == VerificationResult.from_decision(
        decision=VerificationDecision(status="failed", feedback_list=["Return the accepted output."]),
        result=workflow_result,
        result_revision_index=1,
    )
    writer.operation_list.clear()
    accepting_workflow = RecoveryWorkflow(accepted_output="stored", artifact_writer=writer)

    recovered_result = accepting_workflow.result_write_step(context, workflow_input, workflow_result)

    assert recovered_result == workflow_result
    assert accepting_workflow.result_validate_count == 1
    assert not any(operation[0:2] == ("write", "result.json") for operation in writer.operation_list)
    assert VerificationResult.model_validate_json(
        verification_path_get(context.workflow_instance_dir).read_text(encoding="utf-8")
    ) == VerificationResult.from_decision(
        decision=VerificationDecision(status="success", feedback_list=[]),
        result=workflow_result,
        result_revision_index=1,
    )


def test_codex_result_published_without_verdict_runs_only_verification(tmp_path: Path) -> None:
    """Verify an existing Codex result without repeating its action."""

    writer = RecordingJsonArtifactWriter()
    codex_runner = ScriptedCodexRunner([VerificationDecision(status="success", feedback_list=[])])
    step = _codex_step_get(codex_runner=codex_runner, template_dir=tmp_path / "template", writer=writer)
    context = _step_context_get(tmp_path)
    input_source = RecoveryStepInputSource(value="text")
    context.step_instance_dir.mkdir(parents=True)
    writer.write(input_path_get(context.step_instance_dir), RecoveryStepInput(value="text"))
    writer.write(result_path_get(context.step_instance_dir), RecoveryStepResult(output="stored"))
    writer.write(
        state_path_get(context.step_instance_dir),
        WorkflowStepCodexState(attempt_index=1, state="result_published"),
    )
    writer.operation_list.clear()

    recovered_result = step.run(context, input_source)

    assert recovered_result == RecoveryStepResult(output="stored")
    assert [call["output_model"] for call in codex_runner.call_list] == [VerificationDecision]
    assert not any(operation[0:2] == ("write", "result.json") for operation in writer.operation_list)
    assert WorkflowStepCodexState.model_validate_json(
        state_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ) == WorkflowStepCodexState(attempt_index=1, state="completed")


@pytest.mark.parametrize("started_bundle", ("result", "verification", "artifact", "exhausted"))
def test_codex_rejects_missing_state_for_started_bundle(tmp_path: Path, started_bundle: str) -> None:
    """Do not reset Codex attempt identity or budget when private state is missing."""

    writer = RecordingJsonArtifactWriter()
    codex_runner = ScriptedCodexRunner([])
    step = _codex_step_get(codex_runner=codex_runner, template_dir=tmp_path / "template", writer=writer)
    context = _step_context_get(tmp_path)
    input_source = RecoveryStepInputSource(value="text")
    result = RecoveryStepResult(output="stored")
    context.step_instance_dir.mkdir(parents=True)
    writer.write(input_path_get(context.step_instance_dir), RecoveryStepInput(value="text"))
    if started_bundle in {"result", "exhausted"}:
        writer.write(result_path_get(context.step_instance_dir), result)
    if started_bundle in {"verification", "exhausted"}:
        writer.write(
            verification_path_get(context.step_instance_dir),
            VerificationResult.from_decision(
                decision=VerificationDecision(status="failed", feedback_list=["Attempt limit reached."]),
                result=result,
                result_revision_index=3,
            ),
        )
    if started_bundle == "artifact":
        artifact_path = context.step_instance_dir / "artifact/evidence.txt"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("existing\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        step.run(context, input_source)

    assert not state_path_get(context.step_instance_dir).exists()
    assert codex_runner.call_list == []


def test_codex_stale_failed_verdict_verifies_new_result_without_action(tmp_path: Path) -> None:
    """Recover a replaced result by ignoring the failed verdict bound to its predecessor."""

    writer = RecordingJsonArtifactWriter()
    codex_runner = ScriptedCodexRunner([VerificationDecision(status="success", feedback_list=[])])
    step = _codex_step_get(codex_runner=codex_runner, template_dir=tmp_path / "template", writer=writer)
    context = _step_context_get(tmp_path)
    input_source = RecoveryStepInputSource(value="text")
    new_result = RecoveryStepResult(output="new")
    context.step_instance_dir.mkdir(parents=True)
    writer.write(input_path_get(context.step_instance_dir), RecoveryStepInput(value="text"))
    writer.write(result_path_get(context.step_instance_dir), new_result)
    writer.write(
        verification_path_get(context.step_instance_dir),
        VerificationResult.from_decision(
            decision=VerificationDecision(status="failed", feedback_list=["Replace the old result."]),
            result=RecoveryStepResult(output="old"),
            result_revision_index=1,
        ),
    )
    writer.write(
        state_path_get(context.step_instance_dir),
        WorkflowStepCodexState(attempt_index=2, state="ready"),
    )
    writer.operation_list.clear()

    recovered_result = step.run(context, input_source)

    assert recovered_result == new_result
    assert [call["output_model"] for call in codex_runner.call_list] == [VerificationDecision]
    assert not any(operation[0:2] == ("write", "result.json") for operation in writer.operation_list)
    assert VerificationResult.model_validate_json(
        verification_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ).is_bound_to(new_result, result_revision_index=2)


def test_codex_ready_previous_revision_verdict_reverifies_same_result_after_artifact_change(tmp_path: Path) -> None:
    """Bind acceptance to the ready attempt after its artifacts changed but result bytes did not."""

    writer = RecordingJsonArtifactWriter()
    codex_runner = ScriptedCodexRunner([VerificationDecision(status="success", feedback_list=[])])
    step = _codex_step_get(codex_runner=codex_runner, template_dir=tmp_path / "template", writer=writer)
    context = _step_context_get(tmp_path)
    input_source = RecoveryStepInputSource(value="text")
    result = RecoveryStepResult(output="same")
    context.step_instance_dir.mkdir(parents=True)
    writer.write(input_path_get(context.step_instance_dir), RecoveryStepInput(value="text"))
    writer.write(result_path_get(context.step_instance_dir), result)
    declared_artifact_path = context.step_instance_dir / "evidence.txt"
    declared_artifact_path.write_text("attempt-1", encoding="utf-8")
    writer.write(
        verification_path_get(context.step_instance_dir),
        VerificationResult.from_decision(
            decision=VerificationDecision(status="success", feedback_list=[]),
            result=result,
            result_revision_index=1,
        ),
    )
    declared_artifact_path.write_text("attempt-2", encoding="utf-8")
    writer.write(
        state_path_get(context.step_instance_dir),
        WorkflowStepCodexState(attempt_index=2, state="ready"),
    )
    result_bytes = result_path_get(context.step_instance_dir).read_bytes()
    writer.operation_list.clear()

    recovered_result = step.run(context, input_source)

    assert recovered_result == result
    assert [call["output_model"] for call in codex_runner.call_list] == [VerificationDecision]
    assert not any(operation[0:2] == ("write", "result.json") for operation in writer.operation_list)
    assert declared_artifact_path.read_text(encoding="utf-8") == "attempt-2"
    assert result_path_get(context.step_instance_dir).read_bytes() == result_bytes
    assert VerificationResult.model_validate_json(
        verification_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ).is_bound_to(result, result_revision_index=2)


def test_codex_ready_revision_probe_failure_runs_same_attempt_action(tmp_path: Path) -> None:
    """Run the ready attempt when changed artifacts still fail a same-result revision probe."""

    writer = RecordingJsonArtifactWriter()
    codex_runner = ScriptedCodexRunner(
        [
            VerificationDecision(status="failed", feedback_list=["Complete the artifacts."]),
            RecoveryActionOutput(output="same"),
            VerificationDecision(status="success", feedback_list=[]),
        ]
    )
    step = _codex_step_get(codex_runner=codex_runner, template_dir=tmp_path / "template", writer=writer)
    context = _step_context_get(tmp_path)
    input_source = RecoveryStepInputSource(value="text")
    result = RecoveryStepResult(output="same")
    context.step_instance_dir.mkdir(parents=True)
    writer.write(input_path_get(context.step_instance_dir), RecoveryStepInput(value="text"))
    writer.write(result_path_get(context.step_instance_dir), result)
    declared_artifact_path = context.step_instance_dir / "evidence.txt"
    declared_artifact_path.write_text("attempt-1", encoding="utf-8")
    writer.write(
        verification_path_get(context.step_instance_dir),
        VerificationResult.from_decision(
            decision=VerificationDecision(status="failed", feedback_list=["Complete the artifacts."]),
            result=result,
            result_revision_index=1,
        ),
    )
    declared_artifact_path.write_text("attempt-2-incomplete", encoding="utf-8")
    writer.write(
        state_path_get(context.step_instance_dir),
        WorkflowStepCodexState(attempt_index=2, state="ready"),
    )
    writer.operation_list.clear()

    recovered_result = step.run(context, input_source)

    assert recovered_result == result
    assert [call["output_model"] for call in codex_runner.call_list] == [
        VerificationDecision,
        RecoveryActionOutput,
        VerificationDecision,
    ]
    assert declared_artifact_path.read_text(encoding="utf-8") == "attempt-2-incomplete"
    assert VerificationResult.model_validate_json(
        verification_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ).is_bound_to(result, result_revision_index=2)
    assert WorkflowStepCodexState.model_validate_json(
        state_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ) == WorkflowStepCodexState(attempt_index=2, state="completed")


def test_codex_stale_success_verdict_is_reverified_before_acceptance(tmp_path: Path) -> None:
    """Accept success only when its digest and revision match the current publication."""

    writer = RecordingJsonArtifactWriter()
    codex_runner = ScriptedCodexRunner([VerificationDecision(status="success", feedback_list=[])])
    step = _codex_step_get(codex_runner=codex_runner, template_dir=tmp_path / "template", writer=writer)
    context = _step_context_get(tmp_path)
    input_source = RecoveryStepInputSource(value="text")
    current_result = RecoveryStepResult(output="current")
    context.step_instance_dir.mkdir(parents=True)
    writer.write(input_path_get(context.step_instance_dir), RecoveryStepInput(value="text"))
    writer.write(result_path_get(context.step_instance_dir), current_result)
    writer.write(
        verification_path_get(context.step_instance_dir),
        VerificationResult.from_decision(
            decision=VerificationDecision(status="success", feedback_list=[]),
            result=RecoveryStepResult(output="previous"),
            result_revision_index=1,
        ),
    )
    writer.write(
        state_path_get(context.step_instance_dir),
        WorkflowStepCodexState(attempt_index=2, state="result_published"),
    )
    writer.operation_list.clear()

    recovered_result = step.run(context, input_source)

    assert recovered_result == current_result
    assert [call["output_model"] for call in codex_runner.call_list] == [VerificationDecision]
    assert VerificationResult.model_validate_json(
        verification_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ).is_bound_to(current_result, result_revision_index=2)


def test_codex_matching_success_verdict_recovers_without_external_call(tmp_path: Path) -> None:
    """Accept a successful verdict bound to the current parsed result."""

    writer = RecordingJsonArtifactWriter()
    codex_runner = ScriptedCodexRunner([])
    step = _codex_step_get(codex_runner=codex_runner, template_dir=tmp_path / "template", writer=writer)
    context = _step_context_get(tmp_path)
    input_source = RecoveryStepInputSource(value="text")
    result = RecoveryStepResult(output="accepted")
    context.step_instance_dir.mkdir(parents=True)
    writer.write(input_path_get(context.step_instance_dir), RecoveryStepInput(value="text"))
    writer.write(result_path_get(context.step_instance_dir), result)
    writer.write(
        verification_path_get(context.step_instance_dir),
        VerificationResult.from_decision(
            decision=VerificationDecision(status="success", feedback_list=[]),
            result=result,
            result_revision_index=2,
        ),
    )
    writer.write(
        state_path_get(context.step_instance_dir),
        WorkflowStepCodexState(attempt_index=2, state="result_published"),
    )

    assert step.run(context, input_source) == result
    assert codex_runner.call_list == []
    assert WorkflowStepCodexState.model_validate_json(
        state_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ) == WorkflowStepCodexState(attempt_index=2, state="completed")


def test_codex_verification_failed_retry_transition_is_recorded_once(tmp_path: Path) -> None:
    """Do not increment the retry attempt again after a crash in ready state."""

    writer = RecordingJsonArtifactWriter()
    codex_runner = ScriptedCodexRunner(
        [
            RuntimeError("simulated action crash"),
            VerificationDecision(status="failed", feedback_list=["Retry the current attempt."]),
            RecoveryActionOutput(output="recovered"),
            VerificationDecision(status="success", feedback_list=[]),
        ]
    )
    step = _codex_step_get(codex_runner=codex_runner, template_dir=tmp_path / "template", writer=writer)
    context = _step_context_get(tmp_path)
    input_source = RecoveryStepInputSource(value="text")
    context.step_instance_dir.mkdir(parents=True)
    writer.write(input_path_get(context.step_instance_dir), RecoveryStepInput(value="text"))
    writer.write(result_path_get(context.step_instance_dir), RecoveryStepResult(output="rejected"))
    writer.write(
        verification_path_get(context.step_instance_dir),
        VerificationResult.from_decision(
            decision=VerificationDecision(status="failed", feedback_list=["Try again."]),
            result=RecoveryStepResult(output="rejected"),
            result_revision_index=1,
        ),
    )
    writer.write(
        state_path_get(context.step_instance_dir),
        WorkflowStepCodexState(attempt_index=1, state="verification_failed"),
    )
    writer.operation_list.clear()

    with pytest.raises(RuntimeError, match="simulated action crash"):
        step.run(context, input_source)

    assert WorkflowStepCodexState.model_validate_json(
        state_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ) == WorkflowStepCodexState(attempt_index=2, state="ready")

    recovered_result = step.run(context, input_source)

    assert recovered_result == RecoveryStepResult(output="recovered")
    assert WorkflowStepCodexState.model_validate_json(
        state_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ) == WorkflowStepCodexState(attempt_index=2, state="completed")
    state_payload_list = [
        operation[2] for operation in writer.operation_list if operation[0:2] == ("write", "state.json")
    ]
    assert state_payload_list.count({"attempt_index": 2, "state": "ready"}) == 1
    action_call_list = [call for call in codex_runner.call_list if call["output_model"] is RecoveryActionOutput]
    assert len(action_call_list) == 2
    assert [call["output_model"] for call in codex_runner.call_list] == [
        RecoveryActionOutput,
        VerificationDecision,
        RecoveryActionOutput,
        VerificationDecision,
    ]
    assert all(
        isinstance(call["diagnostic_dir"], Path) and call["diagnostic_dir"].parent.name == "attempt_2"
        for call in action_call_list
    )


def test_sqlite_recovery_reopens_current_rows_after_a_new_store_instance(tmp_path: Path) -> None:
    """Recover one current row from the same durable SQLite state database."""

    path = state_database_path_get(tmp_path)
    table = SqliteStateTable[RecoveryRecord](
        name="recovery_record",
        primary_key_field_name_tuple=("record_key",),
        record_model=RecoveryRecord,
    )
    record = RecoveryRecord(record_key="record_one", value="complete")
    first_store = SqliteStateStore()
    first_store.initialize(path, [table])
    first_store.upsert(path, table, record)

    assert SqliteStateStore().get(path, table, ("record_one",)) == record


def test_workflow_rejects_changed_input_identity(tmp_path: Path) -> None:
    """Reject a second input value for one workflow instance."""

    workflow = RecoveryWorkflow(accepted_output=None, artifact_writer=JsonArtifactWriter())
    context = _workflow_context_get(tmp_path, instance_key="changed_input")
    workflow.input_write_step(context, RecoveryWorkflowInput(value="first"))

    with pytest.raises(RuntimeError, match="workflow input does not match existing input.json"):
        workflow.input_write_step(context, RecoveryWorkflowInput(value="second"))


def test_workflow_rejects_changed_result_identity(tmp_path: Path) -> None:
    """Reject a second result value for one workflow instance."""

    workflow = RecoveryWorkflow(accepted_output=None, artifact_writer=JsonArtifactWriter())
    context = _workflow_context_get(tmp_path, instance_key="changed_result")
    workflow_input = RecoveryWorkflowInput(value="text")
    first_result = RecoveryWorkflowResult(status="success", error_list=[], warning_list=[], output="first")
    workflow.input_write_step(context, workflow_input)
    workflow.result_write_step(context, workflow_input, first_result)

    with pytest.raises(RuntimeError, match="workflow result does not match existing result.json"):
        workflow.result_write_step(
            context,
            workflow_input,
            RecoveryWorkflowResult(status="success", error_list=[], warning_list=[], output="second"),
        )

    assert (
        RecoveryWorkflowResult.model_validate_json(
            result_path_get(context.workflow_instance_dir).read_text(encoding="utf-8")
        )
        == first_result
    )
