"""Behavior tests for crash recovery and standard-artifact identity guards."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
from dbos import DBOS, DBOSConfig
from pydantic import BaseModel, ConfigDict, ValidationError
from workflow_container_contract import WorkflowResult

from workflow_container_runtime.artifact import (
    ArtifactMaterializationPolicy,
    ArtifactMaterializer,
    JsonArtifactWriter,
)
from workflow_container_runtime.capability import BrowserRuntimeCapability
from workflow_container_runtime.codex import CodexExecutionError
from workflow_container_runtime.mcp_playwright_profile import McpPlaywrightProfileRuntime
from workflow_container_runtime.prompt.renderer import PromptRenderer
from workflow_container_runtime.step import (
    CodexExecutionRetryPolicy,
    StepResultValidationError,
    WorkflowStepCodexBase,
    WorkflowStepCodexConcurrentBase,
    WorkflowStepCodexConcurrentConfigBase,
    WorkflowStepCodexConfigBase,
    WorkflowStepCodexRuntimePolicy,
    WorkflowStepCodexState,
    WorkflowStepExecutionContext,
    WorkflowStepInvocation,
    WorkflowStepInvocationOutcome,
)
from workflow_container_runtime.step.file import input_path_get, result_path_get, state_path_get, verification_path_get
from workflow_container_runtime.state import SqliteStateStore, SqliteStateTable, state_database_path_get
from workflow_container_runtime.verification import VerificationDecision, VerificationResult
from workflow_container_runtime.workflow import (
    WorkflowBase,
    WorkflowConfigBase,
    WorkflowExecutionContext,
    WorkflowInputBase,
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
    workflow_input_path: Path


class RecoveryStepConfig(WorkflowStepCodexConfigBase):
    """Provide one exact user-owned config for the recovery step."""


class RecoveryConcurrentStepConfig(WorkflowStepCodexConcurrentConfigBase):
    """Provide one exact user-owned config for concurrent recovery work."""


class RecoveryStepConfigMap(RecoveryModel):
    """Expose the closed recovery-step config map."""

    recovery: RecoveryStepConfig


class RecoveryCodexWorkflowConfig(WorkflowConfigBase):
    """Provide the complete public workflow configuration for Codex recovery."""

    step_map: RecoveryStepConfigMap


class RecoveryCodexWorkflowInput(WorkflowInputBase[RecoveryStepInputSource, RecoveryCodexWorkflowConfig]):
    """Bind the recovery request and config into the persisted workflow input."""


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


RECOVERY_STEP_CONFIG = RecoveryStepConfig(
    correction_attempt_limit=2,
    instruction="",
    mcp_playwright_profile=None,
    mcp_playwright_profile_source=None,
    model="gpt-5.6-terra",
    reasoning_effort="high",
)
RECOVERY_RUNTIME_POLICY = WorkflowStepCodexRuntimePolicy(
    artifact_materialization_policy=ArtifactMaterializationPolicy(artifact_root_tuple=()),
    execution_retry_policy=CodexExecutionRetryPolicy(attempt_limit=1),
)


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
        config: object,
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
                "config": config,
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
    WorkflowStepCodexBase[
        RecoveryStepInputSource,
        RecoveryStepInput,
        RecoveryStepConfig,
        RecoveryActionOutput,
        RecoveryStepResult,
    ]
):
    """Exercise the generic Codex recovery FSM with scripted responses."""

    action_output_model: ClassVar[type[RecoveryActionOutput]] = RecoveryActionOutput
    config_model: ClassVar[type[RecoveryStepConfig]] = RecoveryStepConfig
    result_model: ClassVar[type[RecoveryStepResult]] = RecoveryStepResult
    state_model: ClassVar[type[WorkflowStepCodexState]] = WorkflowStepCodexState
    step_key: ClassVar[str] = "recovery"

    def input_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
    ) -> RecoveryStepInput:
        """Build the persisted step input."""

        return RecoveryStepInput(value=input_source.value, workflow_input_path=execution_context.workflow_input_path)

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


class RecoveryConcurrentCodexStep(
    WorkflowStepCodexConcurrentBase[
        RecoveryStepInputSource,
        RecoveryStepInput,
        RecoveryConcurrentStepConfig,
        RecoveryActionOutput,
        RecoveryStepResult,
    ]
):
    """Provide the concrete type contract required by the concurrent scheduler."""

    action_output_model: ClassVar[type[RecoveryActionOutput]] = RecoveryActionOutput
    config_model: ClassVar[type[RecoveryConcurrentStepConfig]] = RecoveryConcurrentStepConfig
    result_model: ClassVar[type[RecoveryStepResult]] = RecoveryStepResult
    state_model: ClassVar[type[WorkflowStepCodexState]] = WorkflowStepCodexState
    step_key: ClassVar[str] = "recovery"

    def input_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
    ) -> RecoveryStepInput:
        """Build the persisted input for one independently scheduled step."""

        return RecoveryStepInput(value=input_source.value, workflow_input_path=execution_context.workflow_input_path)

    def result_from_action_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: RecoveryStepInput,
        action_output: RecoveryActionOutput,
    ) -> RecoveryStepResult:
        """Build one public result from the action output."""

        _ = execution_context
        _ = step_input
        return RecoveryStepResult(output=action_output.output)


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
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=PromptRenderer(template_dir=template_dir),
        runtime_policy=RECOVERY_RUNTIME_POLICY,
    )


def _step_context_get(tmp_path: Path) -> WorkflowStepExecutionContext:
    """Build one stable Codex step execution context."""

    workflow_instance_dir = tmp_path / "workflow" / "run"
    JsonArtifactWriter().write(
        input_path_get(workflow_instance_dir),
        RecoveryCodexWorkflowInput(
            config=RecoveryCodexWorkflowConfig(
                instruction="",
                step_map=RecoveryStepConfigMap(recovery=RECOVERY_STEP_CONFIG),
            ),
            request=RecoveryStepInputSource(value="workflow"),
        ),
    )
    return WorkflowStepExecutionContext(
        result_dir=tmp_path,
        runtime_capability=WorkflowRuntimeCapability(browser=None),
        step_instance_dir=workflow_instance_dir / "step" / "recovery",
        workflow_input_path=Path("workflow/run/input.json"),
    )


def _workflow_context_get(tmp_path: Path, *, instance_key: str) -> WorkflowExecutionContext:
    """Build one stable workflow execution context."""

    return WorkflowExecutionContext(
        result_dir=tmp_path,
        runtime_capability=WorkflowRuntimeCapability(browser=None),
        workflow_instance_dir=tmp_path / "workflow" / instance_key,
    )


def _concurrent_step_get(tmp_path: Path) -> RecoveryConcurrentCodexStep:
    """Build one concurrent step with its ordinary runtime dependencies.

    Args:
        tmp_path: Test-owned directory for prompt templates.

    Returns:
        Concurrent step ready for DBOS boundary fakes.
    """

    return RecoveryConcurrentCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=ScriptedCodexRunner([]),
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=PromptRenderer(template_dir=tmp_path / "template"),
        runtime_policy=RECOVERY_RUNTIME_POLICY,
    )


def _concurrent_config_get() -> RecoveryConcurrentStepConfig:
    """Build the exact concurrent configuration required by recovery fixtures.

    Returns:
        Concurrent configuration with a two-invocation scheduler bound.
    """

    return RecoveryConcurrentStepConfig(
        concurrency=2,
        correction_attempt_limit=0,
        instruction="",
        mcp_playwright_profile=None,
        mcp_playwright_profile_source=None,
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )


def _concurrent_invocation_list_get(
    context: WorkflowStepExecutionContext,
    value_list: list[str],
) -> list[WorkflowStepInvocation[RecoveryStepInputSource]]:
    """Build independently addressable invocations under one workflow context.

    Args:
        context: Base step context with one shared workflow identity.
        value_list: Ordered fake source values and step-instance keys.

    Returns:
        Invocation list whose contexts differ only by step instance key.
    """

    return [
        WorkflowStepInvocation(
            execution_context=context.model_copy(
                update={"step_instance_dir": context.step_instance_dir.parent / value}
            ),
            input_source=RecoveryStepInputSource(value=value),
        )
        for value in value_list
    ]


@pytest.mark.parametrize(
    ("result", "validation_feedback_tuple"),
    [
        (None, ()),
        (RecoveryStepResult(output="accepted"), ("unexpected feedback",)),
    ],
)
def test_workflow_step_invocation_outcome_rejects_ambiguous_state(
    result: RecoveryStepResult | None,
    validation_feedback_tuple: tuple[str, ...],
) -> None:
    """Require each public concurrent outcome to be either success or correction exhaustion."""

    with pytest.raises(ValueError):
        WorkflowStepInvocationOutcome(
            result=result,
            validation_feedback_tuple=validation_feedback_tuple,
        )


def test_workflow_step_invocation_outcome_keeps_validation_feedback_deeply_immutable() -> None:
    """Reject in-place feedback mutation that would invalidate an exhausted outcome."""

    outcome = WorkflowStepInvocationOutcome(
        result=None,
        validation_feedback_tuple=("Correct the output.",),
    )

    with pytest.raises(AttributeError):
        outcome.validation_feedback_tuple.append("Add another correction.")
    with pytest.raises(ValidationError):
        outcome.validation_feedback_tuple += ("Add another correction.",)

    assert outcome.result is None
    assert outcome.validation_feedback_tuple == ("Correct the output.",)


def test_codex_concurrent_step_outcome_list_preserves_order_feedback_and_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep successful results and exhausted feedback in input order under the scheduler bound."""

    active_count = 0
    max_active_count = 0

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Return one success or exhausted correction after a deliberate completion delay."""

        nonlocal active_count, max_active_count
        _ = options
        _ = func
        _ = execution_context
        _ = workflow_step_config
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0.02 if input_source.value == "first" else 0)
        active_count -= 1
        if input_source.value == "invalid":
            raise StepResultValidationError(feedback_list=["Replace invalid output."])
        return RecoveryStepResult(output=input_source.value)

    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)
    outcome_list = asyncio.run(
        _concurrent_step_get(tmp_path).run_outcome_list(
            _concurrent_invocation_list_get(_step_context_get(tmp_path), ["first", "invalid", "third"]),
            _concurrent_config_get(),
        )
    )

    assert [outcome.result for outcome in outcome_list] == [
        RecoveryStepResult(output="first"),
        None,
        RecoveryStepResult(output="third"),
    ]
    assert [outcome.validation_feedback_tuple for outcome in outcome_list] == [(), ("Replace invalid output.",), ()]
    assert max_active_count == 2


def test_codex_concurrent_step_run_list_raises_rebuilt_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Recreate the runtime validation error from public feedback for the ordinary API."""

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Raise one exhausted correction through the real scheduler boundary."""

        _ = options
        _ = func
        _ = execution_context
        _ = input_source
        _ = workflow_step_config
        raise StepResultValidationError(feedback_list=["Correct this result."])

    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)

    with pytest.raises(StepResultValidationError, match="Correct this result") as error:
        asyncio.run(
            _concurrent_step_get(tmp_path).run_list(
                _concurrent_invocation_list_get(_step_context_get(tmp_path), ["invalid"]),
                _concurrent_config_get(),
            )
        )

    assert error.value.feedback_list == ["Correct this result."]


@pytest.mark.parametrize("error_type", [CodexExecutionError, RuntimeError])
def test_codex_concurrent_step_outcome_list_propagates_infrastructure_error(
    error_type: type[RuntimeError],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not convert non-validation scheduler failures into public outcomes."""

    completed_value_list: list[str] = []

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Complete peer work before one planned infrastructure failure propagates."""

        _ = options
        _ = func
        _ = execution_context
        _ = workflow_step_config
        if input_source.value == "failure":
            await asyncio.sleep(0)
            completed_value_list.append(input_source.value)
            raise error_type("infrastructure failure")
        await asyncio.sleep(0.01)
        completed_value_list.append(input_source.value)
        return RecoveryStepResult(output=input_source.value)

    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)

    with pytest.raises(error_type, match="infrastructure failure"):
        asyncio.run(
            _concurrent_step_get(tmp_path).run_outcome_list(
                _concurrent_invocation_list_get(_step_context_get(tmp_path), ["failure", "completed"]),
                _concurrent_config_get(),
            )
        )

    assert completed_value_list == ["failure", "completed"]


def test_codex_concurrent_step_cancellation_stops_lane_before_next_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Propagate lane cancellation without dispatching its remaining invocations."""

    dispatched_value_list: list[str] = []
    first_dispatch_started = asyncio.Event()

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Block the first dispatch until the enclosing scheduler is cancelled."""

        _ = options
        _ = func
        _ = execution_context
        _ = workflow_step_config
        _ = mcp_playwright_profile
        dispatched_value_list.append(input_source.value)
        if input_source.value == "first":
            first_dispatch_started.set()
            await asyncio.Event().wait()
        return RecoveryStepResult(output=input_source.value)

    async def cancellation_run() -> None:
        """Cancel the scheduler after its first lane dispatch starts."""

        scheduler_task = asyncio.create_task(
            _concurrent_step_get(tmp_path).run_outcome_list(
                _concurrent_invocation_list_get(_step_context_get(tmp_path), ["first", "second"]),
                _concurrent_config_get().model_copy(update={"concurrency": 1}),
            )
        )
        await first_dispatch_started.wait()
        scheduler_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await scheduler_task

    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)

    asyncio.run(cancellation_run())

    assert dispatched_value_list == ["first"]


@pytest.mark.parametrize("error_type", [CodexExecutionError, RuntimeError])
@pytest.mark.parametrize("method_name", ["run_list", "run_outcome_list"])
def test_codex_concurrent_public_methods_prioritize_infrastructure_error_over_validation_outcome(
    error_type: type[RuntimeError],
    method_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Finish the mixed group and raise its infrastructure failure instead of an earlier validation outcome."""

    completed_value_list: list[str] = []
    started_value_list: list[str] = []

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Complete one peer, exhaust one validation, or raise one planned infrastructure failure."""

        _ = options
        _ = func
        _ = execution_context
        _ = workflow_step_config
        started_value_list.append(input_source.value)
        if input_source.value == "validation":
            await asyncio.sleep(0.02)
            completed_value_list.append(input_source.value)
            raise StepResultValidationError(feedback_list=["Correct the result."])
        if input_source.value == "infrastructure":
            await asyncio.sleep(0)
            completed_value_list.append(input_source.value)
            raise error_type("infrastructure failure")
        await asyncio.sleep(0.01)
        completed_value_list.append(input_source.value)
        return RecoveryStepResult(output=input_source.value)

    value_list = ["validation", "infrastructure", "success"]
    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)

    with pytest.raises(error_type, match="infrastructure failure"):
        asyncio.run(
            getattr(_concurrent_step_get(tmp_path), method_name)(
                _concurrent_invocation_list_get(_step_context_get(tmp_path), value_list),
                _concurrent_config_get(),
            )
        )

    assert started_value_list == value_list
    assert set(completed_value_list) == set(value_list)


def test_codex_concurrent_step_preserves_validation_outcomes_and_raises_lowest_index_feedback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep validation-only feedback ordered after concurrent completion and re-raise the first invocation feedback."""

    completed_value_list: list[str] = []
    started_value_list: list[str] = []

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Complete two validation failures in reverse input order and one successful peer."""

        _ = options
        _ = func
        _ = execution_context
        _ = workflow_step_config
        started_value_list.append(input_source.value)
        if input_source.value == "validation_first":
            await asyncio.sleep(0.02)
            completed_value_list.append(input_source.value)
            raise StepResultValidationError(feedback_list=["Correct the first result."])
        if input_source.value == "validation_second":
            await asyncio.sleep(0)
            completed_value_list.append(input_source.value)
            raise StepResultValidationError(feedback_list=["Correct the second result."])
        await asyncio.sleep(0.01)
        completed_value_list.append(input_source.value)
        return RecoveryStepResult(output=input_source.value)

    value_list = ["validation_first", "validation_second", "success"]
    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)
    outcome_list = asyncio.run(
        _concurrent_step_get(tmp_path).run_outcome_list(
            _concurrent_invocation_list_get(_step_context_get(tmp_path), value_list),
            _concurrent_config_get(),
        )
    )

    assert [outcome.validation_feedback_tuple for outcome in outcome_list] == [
        ("Correct the first result.",),
        ("Correct the second result.",),
        (),
    ]
    assert started_value_list == value_list
    assert set(completed_value_list) == set(value_list)

    with pytest.raises(StepResultValidationError, match="Correct the first result") as error:
        asyncio.run(
            _concurrent_step_get(tmp_path).run_list(
                _concurrent_invocation_list_get(_step_context_get(tmp_path), value_list),
                _concurrent_config_get(),
            )
        )

    assert error.value.feedback_list == ["Correct the first result."]
    assert started_value_list == value_list * 2
    assert all(completed_value_list.count(value) == 2 for value in value_list)


@pytest.mark.parametrize("method_name", ["run_list", "run_outcome_list"])
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("empty", "invocation_list must not be empty"),
        ("config", "workflow_step_config is RecoveryStepConfig"),
        ("result_dir", "all invocations must use one result_dir"),
        ("workflow_input_path", "all invocations must use one workflow_input_path"),
        ("duplicate_step_instance_dir", "invocations must use unique step_instance_dir values"),
        ("outside_step_instance_dir", "every step_instance_dir must be inside result_dir"),
    ],
)
def test_codex_concurrent_public_methods_validate_invocation_group(
    case: str,
    message: str,
    method_name: str,
    tmp_path: Path,
) -> None:
    """Apply one exact invocation-group contract through both public concurrent methods."""

    context = _step_context_get(tmp_path)
    invocation_list = _concurrent_invocation_list_get(context, ["first", "second"])
    workflow_step_config: RecoveryStepConfig | RecoveryConcurrentStepConfig = _concurrent_config_get()
    if case == "empty":
        invocation_list = []
    elif case == "config":
        workflow_step_config = RECOVERY_STEP_CONFIG
    elif case == "result_dir":
        invocation_list[1] = invocation_list[1].model_copy(
            update={
                "execution_context": invocation_list[1].execution_context.model_copy(
                    update={"result_dir": tmp_path / "other"}
                )
            }
        )
    elif case == "workflow_input_path":
        invocation_list[1] = invocation_list[1].model_copy(
            update={
                "execution_context": invocation_list[1].execution_context.model_copy(
                    update={"workflow_input_path": Path("workflow/other/input.json")}
                )
            }
        )
    elif case == "duplicate_step_instance_dir":
        invocation_list[1] = invocation_list[1].model_copy(
            update={"execution_context": invocation_list[0].execution_context}
        )
    else:
        invocation_list[1] = invocation_list[1].model_copy(
            update={
                "execution_context": invocation_list[1].execution_context.model_copy(
                    update={"step_instance_dir": tmp_path.parent}
                )
            }
        )

    with pytest.raises((TypeError, ValueError), match=message):
        asyncio.run(getattr(_concurrent_step_get(tmp_path), method_name)(invocation_list, workflow_step_config))


def test_codex_concurrent_step_returns_input_order_with_bounded_dbos_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run every independent invocation and return its accepted result in input order."""

    active_count = 0
    max_active_count = 0

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Model one checkpointed DBOS step with deliberately different completion order."""

        nonlocal active_count, max_active_count
        _ = options
        _ = func
        _ = execution_context
        _ = workflow_step_config
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0.02 if input_source.value == "first" else 0)
        active_count -= 1
        return RecoveryStepResult(output=input_source.value)

    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)
    context = _step_context_get(tmp_path)
    step = RecoveryConcurrentCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=ScriptedCodexRunner([]),
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=PromptRenderer(template_dir=tmp_path / "template"),
        runtime_policy=RECOVERY_RUNTIME_POLICY,
    )
    config = RecoveryConcurrentStepConfig(
        concurrency=2,
        correction_attempt_limit=0,
        instruction="",
        mcp_playwright_profile=None,
        mcp_playwright_profile_source=None,
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )
    invocation_list = [
        WorkflowStepInvocation(
            execution_context=context.model_copy(
                update={"step_instance_dir": context.step_instance_dir.parent / step_instance_key}
            ),
            input_source=RecoveryStepInputSource(value=step_instance_key),
        )
        for step_instance_key in ["first", "second", "third"]
    ]

    result_list = asyncio.run(step.run_list(invocation_list, config))

    assert result_list == [
        RecoveryStepResult(output="first"),
        RecoveryStepResult(output="second"),
        RecoveryStepResult(output="third"),
    ]
    assert max_active_count == 2


def test_codex_concurrent_step_does_not_serialize_one_shared_browser_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allow fixed lanes to use distinct profiles through one shared router URL."""

    active_count = 0
    max_active_count = 0

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Record simultaneous DBOS dispatch for one browser endpoint."""

        nonlocal active_count, max_active_count
        _ = options
        _ = func
        _ = execution_context
        _ = workflow_step_config
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0.01)
        active_count -= 1
        return RecoveryStepResult(output=input_source.value)

    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)
    context = _step_context_get(tmp_path).model_copy(
        update={
            "runtime_capability": WorkflowRuntimeCapability(
                browser=BrowserRuntimeCapability(
                    mcp_playwright_profile_source="data-source-profile",
                    mcp_playwright_profile_writeback_candidate_url="http://platform/candidate",
                    mcp_url="http://browser-mcp:8931/mcp",
                )
            )
        }
    )
    invocation_list = _concurrent_invocation_list_get(context, ["first", "second"])

    asyncio.run(_concurrent_step_get(tmp_path).run_list(invocation_list, _concurrent_config_get()))

    assert max_active_count == 2


def test_concurrent_step_uses_fixed_profile_lanes_in_original_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Assign round-robin physical profiles and run each fixed lane sequentially."""

    active_by_profile_map: dict[str, int] = {}
    max_active_by_profile_map: dict[str, int] = {}
    profile_by_value_map: dict[str, str] = {}

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Record the physical profile bound into one lane-owned DBOS callable."""

        _ = options
        _ = execution_context
        _ = workflow_step_config
        assert getattr(func, "__self__", None) is step
        assert getattr(func, "__name__", None) == "_run_with_profile"
        profile = mcp_playwright_profile
        profile_by_value_map[input_source.value] = profile
        active_by_profile_map[profile] = active_by_profile_map.get(profile, 0) + 1
        max_active_by_profile_map[profile] = max(
            max_active_by_profile_map.get(profile, 0),
            active_by_profile_map[profile],
        )
        await asyncio.sleep(0.01 if input_source.value in {"first", "second"} else 0)
        active_by_profile_map[profile] -= 1
        return RecoveryStepResult(output=input_source.value)

    step = _concurrent_step_get(tmp_path)
    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)
    config = RecoveryConcurrentStepConfig(
        concurrency=2,
        correction_attempt_limit=0,
        instruction="",
        mcp_playwright_profile="target",
        mcp_playwright_profile_source=None,
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )
    value_list = ["first", "second", "third", "fourth", "fifth"]

    result_list = asyncio.run(
        step.run_list(
            _concurrent_invocation_list_get(_step_context_get(tmp_path), value_list),
            config,
        )
    )

    assert [result.output for result in result_list] == value_list
    assert profile_by_value_map == {
        "first": "target-1",
        "second": "target-2",
        "third": "target-1",
        "fourth": "target-2",
        "fifth": "target-1",
    }
    assert max_active_by_profile_map == {"target-1": 1, "target-2": 1}


def test_concurrent_step_rejects_source_colliding_with_derived_lane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject one explicit completed source that is also a derived physical target."""

    call_count = 0

    async def run_step_async(*args: object) -> RecoveryStepResult:
        """Count any forbidden DBOS dispatch after failed group validation."""

        nonlocal call_count
        call_count += 1
        return RecoveryStepResult(output="unexpected")

    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)
    config = RecoveryConcurrentStepConfig(
        concurrency=2,
        correction_attempt_limit=0,
        instruction="",
        mcp_playwright_profile="target",
        mcp_playwright_profile_source="target-1",
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )

    with pytest.raises(ValueError, match="collides with derived physical target"):
        asyncio.run(
            _concurrent_step_get(tmp_path).run_outcome_list(
                _concurrent_invocation_list_get(_step_context_get(tmp_path), ["first", "second"]),
                config,
            )
        )

    assert call_count == 0


def test_codex_concurrent_step_waits_for_all_work_before_raising_lowest_index_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Raise the lowest-index failure only after every scheduled invocation completes."""

    completed_value_list: list[str] = []
    started_value_list: list[str] = []

    async def run_step_async(
        options: dict[str, str],
        func: object,
        execution_context: WorkflowStepExecutionContext,
        input_source: RecoveryStepInputSource,
        workflow_step_config: RecoveryConcurrentStepConfig,
        mcp_playwright_profile: str | None,
    ) -> RecoveryStepResult:
        """Complete one item after a value-specific delay or raise its planned failure."""

        _ = options
        _ = func
        _ = execution_context
        _ = workflow_step_config
        started_value_list.append(input_source.value)
        if input_source.value == "higher_index_failure":
            await asyncio.sleep(0)
            completed_value_list.append(input_source.value)
            raise RuntimeError("higher index failure")
        if input_source.value == "lower_index_failure":
            await asyncio.sleep(0.02)
            completed_value_list.append(input_source.value)
            raise RuntimeError("lower index failure")
        await asyncio.sleep(0.01)
        completed_value_list.append(input_source.value)
        return RecoveryStepResult(output=input_source.value)

    monkeypatch.setattr(DBOS, "run_step_async", run_step_async)
    context = _step_context_get(tmp_path)
    step = RecoveryConcurrentCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=ScriptedCodexRunner([]),
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=PromptRenderer(template_dir=tmp_path / "template"),
        runtime_policy=RECOVERY_RUNTIME_POLICY,
    )
    config = RecoveryConcurrentStepConfig(
        concurrency=3,
        correction_attempt_limit=0,
        instruction="",
        mcp_playwright_profile=None,
        mcp_playwright_profile_source=None,
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )
    value_list = ["lower_index_failure", "higher_index_failure", "completed"]
    invocation_list = [
        WorkflowStepInvocation(
            execution_context=context.model_copy(
                update={"step_instance_dir": context.step_instance_dir.parent / value}
            ),
            input_source=RecoveryStepInputSource(value=value),
        )
        for value in value_list
    ]

    with pytest.raises(RuntimeError, match="lower index failure"):
        asyncio.run(step.run_list(invocation_list, config))

    assert started_value_list == value_list
    assert set(completed_value_list) == set(value_list)


def test_workflow_result_without_verdict_is_revalidated_without_republication(tmp_path: Path) -> None:
    """Recover a published workflow result by recreating only its verdict."""

    writer = RecordingJsonArtifactWriter()
    workflow = RecoveryWorkflow(accepted_output="TEXT", artifact_writer=writer)
    context = _workflow_context_get(tmp_path, instance_key="missing_verdict")
    workflow_input = RecoveryWorkflowInput(value="text")
    workflow_result = RecoveryWorkflowResult(status="success", error_list=[], warning_list=[], output="TEXT")
    workflow._input_write(context, workflow_input)
    workflow._result_write(context, workflow_input, workflow_result)
    verification_path_get(context.workflow_instance_dir).unlink()
    writer.operation_list.clear()

    recovered_result = workflow._result_write(
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
    rejecting_workflow._input_write(context, workflow_input)

    with pytest.raises(WorkflowResultValidationError):
        rejecting_workflow._result_write(context, workflow_input, workflow_result)

    assert VerificationResult.model_validate_json(
        verification_path_get(context.workflow_instance_dir).read_text(encoding="utf-8")
    ) == VerificationResult.from_decision(
        decision=VerificationDecision(status="failed", feedback_list=["Return the accepted output."]),
        result=workflow_result,
        result_revision_index=1,
    )
    writer.operation_list.clear()
    accepting_workflow = RecoveryWorkflow(accepted_output="stored", artifact_writer=writer)

    recovered_result = accepting_workflow._result_write(context, workflow_input, workflow_result)

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
    writer.write(
        input_path_get(context.step_instance_dir),
        RecoveryStepInput(value="text", workflow_input_path=context.workflow_input_path),
    )
    writer.write(result_path_get(context.step_instance_dir), RecoveryStepResult(output="stored"))
    writer.write(
        state_path_get(context.step_instance_dir),
        WorkflowStepCodexState(attempt_index=1, state="result_published"),
    )
    writer.operation_list.clear()

    recovered_result = step.run(context, input_source, RECOVERY_STEP_CONFIG)

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
    writer.write(
        input_path_get(context.step_instance_dir),
        RecoveryStepInput(value="text", workflow_input_path=context.workflow_input_path),
    )
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
        step.run(context, input_source, RECOVERY_STEP_CONFIG)

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
    writer.write(
        input_path_get(context.step_instance_dir),
        RecoveryStepInput(value="text", workflow_input_path=context.workflow_input_path),
    )
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

    recovered_result = step.run(context, input_source, RECOVERY_STEP_CONFIG)

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
    writer.write(
        input_path_get(context.step_instance_dir),
        RecoveryStepInput(value="text", workflow_input_path=context.workflow_input_path),
    )
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

    recovered_result = step.run(context, input_source, RECOVERY_STEP_CONFIG)

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
    writer.write(
        input_path_get(context.step_instance_dir),
        RecoveryStepInput(value="text", workflow_input_path=context.workflow_input_path),
    )
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

    recovered_result = step.run(context, input_source, RECOVERY_STEP_CONFIG)

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
    writer.write(
        input_path_get(context.step_instance_dir),
        RecoveryStepInput(value="text", workflow_input_path=context.workflow_input_path),
    )
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

    recovered_result = step.run(context, input_source, RECOVERY_STEP_CONFIG)

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
    writer.write(
        input_path_get(context.step_instance_dir),
        RecoveryStepInput(value="text", workflow_input_path=context.workflow_input_path),
    )
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

    assert step.run(context, input_source, RECOVERY_STEP_CONFIG) == result
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
    writer.write(
        input_path_get(context.step_instance_dir),
        RecoveryStepInput(value="text", workflow_input_path=context.workflow_input_path),
    )
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
        step.run(context, input_source, RECOVERY_STEP_CONFIG)

    assert WorkflowStepCodexState.model_validate_json(
        state_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ) == WorkflowStepCodexState(attempt_index=2, state="ready")

    recovered_result = step.run(context, input_source, RECOVERY_STEP_CONFIG)

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
    workflow._input_write(context, RecoveryWorkflowInput(value="first"))

    with pytest.raises(RuntimeError, match="workflow input does not match existing input.json"):
        workflow._input_write(context, RecoveryWorkflowInput(value="second"))


def test_workflow_rejects_changed_result_identity(tmp_path: Path) -> None:
    """Reject a second result value for one workflow instance."""

    workflow = RecoveryWorkflow(accepted_output=None, artifact_writer=JsonArtifactWriter())
    context = _workflow_context_get(tmp_path, instance_key="changed_result")
    workflow_input = RecoveryWorkflowInput(value="text")
    first_result = RecoveryWorkflowResult(status="success", error_list=[], warning_list=[], output="first")
    workflow._input_write(context, workflow_input)
    workflow._result_write(context, workflow_input, first_result)

    with pytest.raises(RuntimeError, match="workflow result does not match existing result.json"):
        workflow._result_write(
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
