"""Behavior tests for deterministic and Codex-backed workflow steps."""

import inspect
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from workflow_container_contract import McpPlaywrightProfileWritebackPolicy

from workflow_container_runtime.artifact.materializer import ArtifactMaterializationPolicy, ArtifactMaterializer
from workflow_container_runtime.artifact.writer import JsonArtifactWriter
from workflow_container_runtime.capability import BrowserRuntimeCapability
from workflow_container_runtime.codex.config import CodexRunnerConfig
from workflow_container_runtime.mcp_playwright_profile import McpPlaywrightProfileRuntime
from workflow_container_runtime.platform import WorkflowControlRequestError
from workflow_container_runtime.prompt.renderer import PromptRenderer
from workflow_container_runtime.step.base import (
    StepResultValidationError,
    WorkflowStepBase,
    WorkflowStepCodexConcurrentBase,
    WorkflowStepCodexBase,
    WorkflowStepDeterministicBase,
)
from workflow_container_runtime.step.codex import (
    CodexExecutionRetryPolicy,
    WorkflowStepCodexConfigBase,
    WorkflowStepCodexRuntimePolicy,
    WorkflowStepCodexState,
)
from workflow_container_runtime.step.context import WorkflowStepExecutionContext
from workflow_container_runtime.step.file import input_path_get, result_path_get, state_path_get, verification_path_get
from workflow_container_runtime.verification import VerificationDecision, VerificationResult
from workflow_container_runtime.workflow import WorkflowBrowserConfigBase, WorkflowInputBase
from workflow_container_runtime.workflow.context import WorkflowRuntimeCapability


class ExampleModel(BaseModel):
    """Strict base model for step lifecycle tests."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)


class ExampleInputSource(ExampleModel):
    """Ephemeral public dependencies of one step."""

    value: str


class ExampleStepInput(ExampleModel):
    """Persisted step input."""

    source: ExampleInputSource
    workflow_input_path: Path


class ExampleStepConfig(WorkflowStepCodexConfigBase):
    """Provide the exact configurable run contract of the example step."""


class ExampleStepConfigMap(ExampleModel):
    """Expose the closed configurable-step map of the example workflow."""

    example_build: ExampleStepConfig


class ExampleWorkflowConfig(WorkflowBrowserConfigBase):
    """Provide one complete workflow config with the example step selection."""

    step_map: ExampleStepConfigMap


class ExampleWorkflowInput(WorkflowInputBase[ExampleInputSource, ExampleWorkflowConfig]):
    """Bind the example request and complete workflow configuration."""


class ExampleActionOutput(ExampleModel):
    """Codex-owned action output."""

    output: str


class ExampleStepResult(ExampleModel):
    """Public step result."""

    output: str


class ExampleDeterministicStep(WorkflowStepDeterministicBase[ExampleInputSource, ExampleStepInput, ExampleStepResult]):
    """Build one uppercase deterministic result."""

    result_model: ClassVar[type[ExampleStepResult]] = ExampleStepResult

    def __init__(self, *, artifact_writer: JsonArtifactWriter) -> None:
        """Store the common writer and one execution counter."""

        super().__init__(artifact_writer=artifact_writer)
        self.result_build_count = 0

    def input_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: ExampleInputSource,
    ) -> ExampleStepInput:
        """Build the persisted input."""

        return ExampleStepInput(source=input_source, workflow_input_path=execution_context.workflow_input_path)

    def result_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: ExampleStepInput,
    ) -> ExampleStepResult:
        """Build the deterministic result."""

        _ = execution_context
        self.result_build_count += 1
        return ExampleStepResult(output=step_input.source.value.upper())


class FakeCodexRunner:
    """Return queued typed outputs and record runtime routing."""

    def __init__(self, output_list: list[BaseModel]) -> None:
        """Store queued outputs."""

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
        """Return the next queued output."""

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
        return self._output_list.pop(0)


class ExampleCodexStep(
    WorkflowStepCodexBase[
        ExampleInputSource, ExampleStepInput, ExampleStepConfig, ExampleActionOutput, ExampleStepResult
    ]
):
    """Build one semantically verified Codex result."""

    action_output_model: ClassVar[type[ExampleActionOutput]] = ExampleActionOutput
    config_model: ClassVar[type[ExampleStepConfig]] = ExampleStepConfig
    result_model: ClassVar[type[ExampleStepResult]] = ExampleStepResult
    state_model: ClassVar[type[WorkflowStepCodexState]] = WorkflowStepCodexState
    step_key: ClassVar[str] = "example_build"

    def input_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: ExampleInputSource,
    ) -> ExampleStepInput:
        """Build the persisted input."""

        return ExampleStepInput(source=input_source, workflow_input_path=execution_context.workflow_input_path)

    def result_from_action_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: ExampleStepInput,
        action_output: ExampleActionOutput,
    ) -> ExampleStepResult:
        """Build the public result from action-owned output."""

        _ = execution_context
        _ = step_input
        return ExampleStepResult(output=action_output.output)

    def result_validate(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: ExampleStepInput,
        result: ExampleStepResult,
    ) -> None:
        """Reject one known mechanically invalid output."""

        _ = execution_context
        _ = step_input
        if result.output == "bad":
            raise StepResultValidationError(feedback_list=["Replace bad output."])


EXAMPLE_STEP_CONFIG = ExampleStepConfig(
    correction_attempt_limit=2,
    instruction="",
    mcp_playwright_profile=None,
    mcp_playwright_profile_source=None,
    model="gpt-5.6-terra",
    reasoning_effort="high",
)
EXAMPLE_RUNTIME_POLICY = WorkflowStepCodexRuntimePolicy(
    artifact_materialization_policy=ArtifactMaterializationPolicy(artifact_root_tuple=()),
    execution_retry_policy=CodexExecutionRetryPolicy(attempt_limit=1),
)


def _context_get(
    tmp_path: Path,
    *,
    runtime_capability: WorkflowRuntimeCapability | None = None,
    workflow_step_config: ExampleStepConfig = EXAMPLE_STEP_CONFIG,
) -> WorkflowStepExecutionContext:
    """Return one step execution context."""

    workflow_instance_dir = tmp_path / "workflow" / "run"
    JsonArtifactWriter().write(
        input_path_get(workflow_instance_dir),
        ExampleWorkflowInput(
            config=ExampleWorkflowConfig(
                instruction="",
                mcp_playwright_profile_writeback_policy=McpPlaywrightProfileWritebackPolicy(
                    mcp_playwright_profile_name_prefix="",
                    workflow_run_status_list=("done",),
                ),
                step_map=ExampleStepConfigMap(example_build=workflow_step_config),
            ),
            request=ExampleInputSource(value="workflow"),
        ),
    )
    return WorkflowStepExecutionContext(
        result_dir=tmp_path,
        runtime_capability=runtime_capability or WorkflowRuntimeCapability(browser=None),
        step_instance_dir=workflow_instance_dir / "step" / "example_build",
        workflow_input_path=Path("workflow/run/input.json"),
    )


def _prompt_renderer_get(tmp_path: Path) -> PromptRenderer:
    """Create minimal action and verification templates."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "example_build.md.j2").write_text("input={{ input_path }}", encoding="utf-8")
    (tmp_path / "example_build_verify.md.j2").write_text(
        "input={{ input_path }} result={{ step_result_path }} previous={{ previous_attempt_verification_path|default('') }}",
        encoding="utf-8",
    )
    return PromptRenderer(template_dir=tmp_path)


def test_deterministic_step_recovers_result_without_rebuilding(tmp_path: Path) -> None:
    """Return an accepted persisted result without repeating domain work."""

    step = ExampleDeterministicStep(artifact_writer=JsonArtifactWriter())
    context = _context_get(tmp_path)
    input_source = ExampleInputSource(value="text")

    first_result = step.run(context, input_source)
    second_result = step.run(context, input_source)

    assert first_result == second_result == ExampleStepResult(output="TEXT")
    assert step.result_build_count == 1
    assert input_path_get(context.step_instance_dir).is_file()
    assert result_path_get(context.step_instance_dir).is_file()
    assert verification_path_get(context.step_instance_dir).is_file()
    assert not state_path_get(context.step_instance_dir).exists()


def test_step_bases_expose_only_their_exact_final_run_contracts() -> None:
    """Keep deterministic, Codex, and concurrent step entrypoints distinct."""

    assert "run" not in WorkflowStepBase.__dict__
    assert list(inspect.signature(WorkflowStepDeterministicBase.run).parameters) == [
        "self",
        "execution_context",
        "input_source",
    ]
    assert list(inspect.signature(WorkflowStepCodexBase.run).parameters) == [
        "self",
        "execution_context",
        "input_source",
        "workflow_step_config",
    ]
    assert list(inspect.signature(WorkflowStepCodexConcurrentBase.run_list).parameters) == [
        "self",
        "invocation_list",
        "workflow_step_config",
    ]


def test_codex_step_requires_the_exact_persisted_workflow_config(tmp_path: Path) -> None:
    """Reject a DBOS config argument that differs from the workflow input selection."""

    step = ExampleCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=FakeCodexRunner([]),
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
        runtime_policy=EXAMPLE_RUNTIME_POLICY,
    )
    mismatched_config = ExampleStepConfig(
        correction_attempt_limit=1,
        instruction="",
        mcp_playwright_profile=None,
        mcp_playwright_profile_source=None,
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )

    with pytest.raises(RuntimeError, match="does not match workflow input"):
        context = _context_get(tmp_path)
        step.run(context, ExampleInputSource(value="text"), mismatched_config)

    assert not context.step_instance_dir.exists()


def test_codex_step_revalidates_model_copy_context_before_step_side_effects(tmp_path: Path) -> None:
    """Reject a context whose copied workflow input path bypasses its model validator."""

    step = ExampleCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=FakeCodexRunner([]),
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
        runtime_policy=EXAMPLE_RUNTIME_POLICY,
    )
    context = _context_get(tmp_path)
    JsonArtifactWriter().write(
        tmp_path / "foreign" / "input.json",
        ExampleWorkflowInput(
            config=ExampleWorkflowConfig(
                instruction="",
                mcp_playwright_profile_writeback_policy=McpPlaywrightProfileWritebackPolicy(
                    mcp_playwright_profile_name_prefix="",
                    workflow_run_status_list=("done",),
                ),
                step_map=ExampleStepConfigMap(example_build=EXAMPLE_STEP_CONFIG),
            ),
            request=ExampleInputSource(value="foreign"),
        ),
    )
    unvalidated_context = context.model_copy(update={"workflow_input_path": Path("foreign/input.json")})

    with pytest.raises(ValidationError, match="current workflow input"):
        step.run(unvalidated_context, ExampleInputSource(value="text"), EXAMPLE_STEP_CONFIG)

    assert not context.step_instance_dir.exists()


def test_codex_step_retries_mechanical_and_semantic_failures(tmp_path: Path) -> None:
    """Use one correction FSM for mechanical and semantic feedback."""

    fake_runner = FakeCodexRunner(
        [
            ExampleActionOutput(output="bad"),
            ExampleActionOutput(output="first"),
            VerificationDecision(status="failed", feedback_list=["Use the final value."]),
            ExampleActionOutput(output="final"),
            VerificationDecision(status="success", feedback_list=[]),
        ]
    )
    step = ExampleCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=fake_runner,
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
        runtime_policy=EXAMPLE_RUNTIME_POLICY,
    )
    context = _context_get(tmp_path)

    result = step.run(context, ExampleInputSource(value="text"), EXAMPLE_STEP_CONFIG)

    assert result == ExampleStepResult(output="final")
    assert WorkflowStepCodexState.model_validate_json(
        state_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ) == WorkflowStepCodexState(attempt_index=3, state="completed")
    assert VerificationResult.model_validate_json(
        verification_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ) == VerificationResult.from_decision(
        decision=VerificationDecision(status="success", feedback_list=[]),
        result=ExampleStepResult(output="final"),
        result_revision_index=3,
    )
    assert len(fake_runner.call_list) == 5
    assert all(
        call["config"] == CodexRunnerConfig(model="gpt-5.6-terra", reasoning_effort="high")
        for call in fake_runner.call_list
    )


def test_codex_step_recovers_success_without_external_call(tmp_path: Path) -> None:
    """Return the accepted result when all recovery files already exist."""

    fake_runner = FakeCodexRunner(
        [ExampleActionOutput(output="final"), VerificationDecision(status="success", feedback_list=[])]
    )
    step = ExampleCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=fake_runner,
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
        runtime_policy=EXAMPLE_RUNTIME_POLICY,
    )
    context = _context_get(tmp_path)
    input_source = ExampleInputSource(value="text")

    assert step.run(context, input_source, EXAMPLE_STEP_CONFIG) == ExampleStepResult(output="final")
    assert step.run(context, input_source, EXAMPLE_STEP_CONFIG) == ExampleStepResult(output="final")
    assert len(fake_runner.call_list) == 2


def test_codex_step_routes_phases_and_republishes_recovered_candidate(tmp_path: Path) -> None:
    """Route each phase correctly and republish the candidate before recovery returns."""

    request_list: list[object] = []

    class CandidateResponse:
        """Return one successful platform candidate response."""

        status = 204

        def __enter__(self) -> "CandidateResponse":
            """Enter the fake response context."""

            return self

        def __exit__(self, *args: object) -> None:
            """Leave the fake response context."""

    def urlopen(request: object, *, timeout: float) -> CandidateResponse:
        """Record one candidate publication."""

        _ = timeout
        request_list.append(request)
        return CandidateResponse()

    config = ExampleStepConfig(
        correction_attempt_limit=1,
        instruction="",
        mcp_playwright_profile="source-discover",
        mcp_playwright_profile_source="login-completed",
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )
    runtime_capability = WorkflowRuntimeCapability(
        browser=BrowserRuntimeCapability(
            mcp_playwright_profile_source="data-source-profile",
            mcp_playwright_profile_writeback_candidate_url="http://platform/candidate",
            mcp_url="http://browser:8931/mcp",
        )
    )
    fake_runner = FakeCodexRunner(
        [ExampleActionOutput(output="final"), VerificationDecision(status="success", feedback_list=[])]
    )
    step = ExampleCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=fake_runner,
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(urlopen=urlopen),
        prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
        runtime_policy=EXAMPLE_RUNTIME_POLICY,
    )
    context = _context_get(tmp_path, runtime_capability=runtime_capability, workflow_step_config=config)

    assert step.run(context, ExampleInputSource(value="text"), config) == ExampleStepResult(output="final")
    assert step.run(context, ExampleInputSource(value="text"), config) == ExampleStepResult(output="final")

    action_browser = fake_runner.call_list[0]["runtime_capability"].browser
    verification_browser = fake_runner.call_list[1]["runtime_capability"].browser
    assert action_browser.mcp_url.endswith("?profile=source-discover&profile_source=login-completed")
    assert verification_browser.mcp_url.endswith("?profile=source-discover")
    assert len(fake_runner.call_list) == 2
    assert len(request_list) == 2


def test_candidate_timeout_wraps_and_recovery_reacquires_profile_lease(tmp_path: Path) -> None:
    """Wrap one timeout and release its profile lease so recovery can republish the accepted result."""

    candidate_call_count = 0

    class CandidateResponse:
        """Return one successful platform candidate status."""

        status = 204

        def __enter__(self) -> "CandidateResponse":
            """Enter the fake response context."""

            return self

        def __exit__(self, *args: object) -> None:
            """Leave the fake response context."""

    def urlopen(request: object, *, timeout: float) -> CandidateResponse:
        """Raise one transport timeout before allowing recovery publication.

        Args:
            request: Candidate publication request.
            timeout: Bounded candidate HTTP timeout.

        Returns:
            Successful recovery response.

        Raises:
            TimeoutError: On the first candidate publication call.
        """

        nonlocal candidate_call_count
        _ = request
        assert timeout == 4.0
        candidate_call_count += 1
        if candidate_call_count == 1:
            raise TimeoutError("candidate endpoint timed out")
        return CandidateResponse()

    config = ExampleStepConfig(
        correction_attempt_limit=1,
        instruction="",
        mcp_playwright_profile="source-discover",
        mcp_playwright_profile_source=None,
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )
    runtime_capability = WorkflowRuntimeCapability(
        browser=BrowserRuntimeCapability(
            mcp_playwright_profile_source="data-source-profile",
            mcp_playwright_profile_writeback_candidate_url="http://platform/candidate",
            mcp_url="http://browser:8931/mcp",
        )
    )
    fake_runner = FakeCodexRunner(
        [ExampleActionOutput(output="final"), VerificationDecision(status="success", feedback_list=[])]
    )
    step = ExampleCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=fake_runner,
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(
            mcp_playwright_profile_writeback_candidate_http_timeout_seconds=4.0,
            urlopen=urlopen,
        ),
        prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
        runtime_policy=EXAMPLE_RUNTIME_POLICY,
    )
    context = _context_get(tmp_path, runtime_capability=runtime_capability, workflow_step_config=config)

    with pytest.raises(WorkflowControlRequestError, match="transport failed: candidate endpoint timed out"):
        step.run(context, ExampleInputSource(value="text"), config)

    assert step.run(context, ExampleInputSource(value="text"), config) == ExampleStepResult(output="final")
    assert len(fake_runner.call_list) == 2
    assert candidate_call_count == 2


@pytest.mark.parametrize("step_kind", ("deterministic", "codex"))
@pytest.mark.parametrize(
    "existing_relative_path",
    (Path("result.json"), Path("verification.json"), Path("artifact/evidence.json")),
    ids=("result", "verification", "declared-artifact"),
)
def test_step_rejects_missing_input_for_started_instance(
    tmp_path: Path,
    step_kind: str,
    existing_relative_path: Path,
) -> None:
    """Do not assign a new input identity to an already-started step instance."""

    context = _context_get(tmp_path)
    existing_path = context.step_instance_dir / existing_relative_path
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text("existing\n", encoding="utf-8")
    if step_kind == "deterministic":
        step = ExampleDeterministicStep(artifact_writer=JsonArtifactWriter())
    else:
        step = ExampleCodexStep(
            artifact_materializer=ArtifactMaterializer(),
            artifact_writer=JsonArtifactWriter(),
            codex_runner=FakeCodexRunner([]),
            mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
            prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
            runtime_policy=EXAMPLE_RUNTIME_POLICY,
        )

    with pytest.raises(RuntimeError):
        if step_kind == "deterministic":
            step.run(context, ExampleInputSource(value="text"))
        else:
            step.run(context, ExampleInputSource(value="text"), EXAMPLE_STEP_CONFIG)

    assert not input_path_get(context.step_instance_dir).exists()
    assert existing_path.read_text(encoding="utf-8") == "existing\n"


def test_verification_decision_requires_feedback_only_for_failure() -> None:
    """Keep one unambiguous transient verification decision channel."""

    with pytest.raises(ValueError):
        VerificationDecision(status="success", feedback_list=["unexpected"])
    with pytest.raises(ValueError):
        VerificationDecision(status="failed", feedback_list=[])


def test_deterministic_step_keeps_previous_verdict_while_publishing_result(tmp_path: Path) -> None:
    """Keep a stale verdict until the deterministic result is atomically replaced."""

    class RecordingWriter(JsonArtifactWriter):
        """Record publication order and require stale-verdict coexistence."""

        def __init__(self) -> None:
            """Initialize one operation log."""

            self.operation_list: list[tuple[str, str]] = []

        def write(self, path: Path, value: BaseModel) -> None:
            """Require the prior verdict while publishing a replacement result.

            Args:
                path: Artifact path.
                value: Validated model.
            """

            if path.name == "result.json" and not verification_path_get(path.parent).exists():
                raise AssertionError("new result.json must coexist temporarily with old verification.json")
            self.operation_list.append(("write", path.name))
            super().write(path, value)

    writer = RecordingWriter()
    context = _context_get(tmp_path)
    context.step_instance_dir.mkdir(parents=True)
    writer.write(
        input_path_get(context.step_instance_dir),
        ExampleStepInput(source=ExampleInputSource(value="text"), workflow_input_path=context.workflow_input_path),
    )
    writer.write(
        verification_path_get(context.step_instance_dir),
        VerificationResult.from_decision(
            decision=VerificationDecision(status="success", feedback_list=[]),
            result=ExampleStepResult(output="old"),
            result_revision_index=1,
        ),
    )
    writer.operation_list.clear()

    ExampleDeterministicStep(artifact_writer=writer).run(context, ExampleInputSource(value="text"))

    assert VerificationResult.model_validate_json(
        verification_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ).is_bound_to(ExampleStepResult(output="TEXT"), result_revision_index=1)


def test_codex_step_keeps_previous_verdict_while_publishing_result(tmp_path: Path) -> None:
    """Keep a stale verdict until the Codex result is atomically replaced."""

    class RecordingWriter(JsonArtifactWriter):
        """Record publication order and require stale-verdict coexistence."""

        def __init__(self) -> None:
            """Initialize one operation log."""

            self.operation_list: list[tuple[str, str]] = []

        def write(self, path: Path, value: BaseModel) -> None:
            """Require the prior verdict while publishing a replacement result.

            Args:
                path: Artifact path.
                value: Validated model.
            """

            if path.name == "result.json" and not verification_path_get(path.parent).exists():
                raise AssertionError("new result.json must coexist temporarily with old verification.json")
            self.operation_list.append(("write", path.name))
            super().write(path, value)

    writer = RecordingWriter()
    context = _context_get(tmp_path)
    context.step_instance_dir.mkdir(parents=True)
    writer.write(
        input_path_get(context.step_instance_dir),
        ExampleStepInput(source=ExampleInputSource(value="text"), workflow_input_path=context.workflow_input_path),
    )
    writer.write(
        verification_path_get(context.step_instance_dir),
        VerificationResult.from_decision(
            decision=VerificationDecision(status="failed", feedback_list=["Replace the old output."]),
            result=ExampleStepResult(output="old"),
            result_revision_index=1,
        ),
    )
    writer.write(
        state_path_get(context.step_instance_dir),
        WorkflowStepCodexState(attempt_index=1, state="ready"),
    )
    writer.operation_list.clear()
    step = ExampleCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=writer,
        codex_runner=FakeCodexRunner(
            [ExampleActionOutput(output="final"), VerificationDecision(status="success", feedback_list=[])]
        ),
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
        runtime_policy=EXAMPLE_RUNTIME_POLICY,
    )

    step.run(context, ExampleInputSource(value="text"), EXAMPLE_STEP_CONFIG)

    assert VerificationResult.model_validate_json(
        verification_path_get(context.step_instance_dir).read_text(encoding="utf-8")
    ).is_bound_to(ExampleStepResult(output="final"), result_revision_index=1)


def test_deterministic_step_restarts_validation_after_result_publication_without_rebuilding(tmp_path: Path) -> None:
    """Recover a published deterministic result without repeating domain work."""

    class CrashAfterResultStep(ExampleDeterministicStep):
        """Crash exactly once when validating a published result."""

        def __init__(self, *, artifact_writer: JsonArtifactWriter) -> None:
            """Initialize the validation crash switch."""

            super().__init__(artifact_writer=artifact_writer)
            self.result_validate_count = 0

        def result_validate(
            self,
            execution_context: WorkflowStepExecutionContext,
            step_input: ExampleStepInput,
            result: ExampleStepResult,
        ) -> None:
            """Crash once after result publication and then accept recovery validation.

            Args:
                execution_context: Current step execution context.
                step_input: Persisted step input.
                result: Published result.
            """

            _ = execution_context
            _ = step_input
            _ = result
            self.result_validate_count += 1
            if self.result_validate_count == 1:
                raise RuntimeError("injected validation crash")

    step = CrashAfterResultStep(artifact_writer=JsonArtifactWriter())
    context = _context_get(tmp_path)
    input_source = ExampleInputSource(value="text")

    with pytest.raises(RuntimeError, match="injected validation crash"):
        step.run(context, input_source)

    assert result_path_get(context.step_instance_dir).is_file()
    assert not verification_path_get(context.step_instance_dir).exists()
    assert step.run(context, input_source) == ExampleStepResult(output="TEXT")
    assert step.result_build_count == 1
    assert step.result_validate_count == 2


def test_codex_step_restarts_verification_after_result_publication_without_repeating_action(tmp_path: Path) -> None:
    """Recover a published Codex result without rerunning action or result construction."""

    class CrashAfterResultWriter(JsonArtifactWriter):
        """Crash once before the result-published state is durable."""

        def __init__(self) -> None:
            """Initialize the one-time state-write crash switch."""

            self._crash_pending = True

        def write(self, path: Path, value: BaseModel) -> None:
            """Crash after result publication but before its state publication.

            Args:
                path: Artifact path.
                value: Validated model.
            """

            if (
                self._crash_pending
                and path.name == "state.json"
                and isinstance(value, WorkflowStepCodexState)
                and value.state == "result_published"
            ):
                self._crash_pending = False
                raise RuntimeError("injected post-result crash")
            super().write(path, value)

    class CrashRecoveryCodexStep(ExampleCodexStep):
        """Count public result construction across one restart."""

        def result_from_action_build(
            self,
            execution_context: WorkflowStepExecutionContext,
            step_input: ExampleStepInput,
            action_output: ExampleActionOutput,
        ) -> ExampleStepResult:
            """Count and build one result from an action output.

            Args:
                execution_context: Current step execution context.
                step_input: Persisted step input.
                action_output: Typed Codex action output.

            Returns:
                Public result built once per action.
            """

            self.result_from_action_build_count += 1
            return super().result_from_action_build(execution_context, step_input, action_output)

    fake_runner = FakeCodexRunner(
        [ExampleActionOutput(output="final"), VerificationDecision(status="success", feedback_list=[])]
    )
    step = CrashRecoveryCodexStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=CrashAfterResultWriter(),
        codex_runner=fake_runner,
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
        runtime_policy=EXAMPLE_RUNTIME_POLICY,
    )
    step.result_from_action_build_count = 0
    context = _context_get(tmp_path)
    input_source = ExampleInputSource(value="text")

    with pytest.raises(RuntimeError, match="injected post-result crash"):
        step.run(context, input_source, EXAMPLE_STEP_CONFIG)

    assert result_path_get(context.step_instance_dir).is_file()
    assert not verification_path_get(context.step_instance_dir).exists()
    assert step.run(context, input_source, EXAMPLE_STEP_CONFIG) == ExampleStepResult(output="final")
    assert step.result_from_action_build_count == 1
    assert len(fake_runner.call_list) == 2


def test_step_rejects_result_with_wrong_declared_model(tmp_path: Path) -> None:
    """Reject one strict result model that differs from the declared recovery model."""

    class OtherStepResult(ExampleModel):
        """Wrong but otherwise strict result model."""

        output: str

    class WrongResultStep(WorkflowStepDeterministicBase[ExampleInputSource, ExampleStepInput, ExampleStepResult]):
        """Return the wrong strict result type deliberately."""

        result_model: ClassVar[type[ExampleStepResult]] = ExampleStepResult

        def input_build(
            self,
            execution_context: WorkflowStepExecutionContext,
            input_source: ExampleInputSource,
        ) -> ExampleStepInput:
            """Build the persisted input."""

            _ = execution_context
            return ExampleStepInput(source=input_source, workflow_input_path=execution_context.workflow_input_path)

        def result_build(
            self,
            execution_context: WorkflowStepExecutionContext,
            step_input: ExampleStepInput,
        ) -> ExampleStepResult:
            """Return a different strict model through an intentionally false annotation."""

            _ = execution_context
            return OtherStepResult(output=step_input.source.value)

    step = WrongResultStep(artifact_writer=JsonArtifactWriter())

    with pytest.raises(TypeError, match="result_build returned OtherStepResult; expected ExampleStepResult"):
        step.run(_context_get(tmp_path), ExampleInputSource(value="text"))


def test_codex_step_rejects_state_with_wrong_declared_model(tmp_path: Path) -> None:
    """Reject one state model that differs from the declared recovery model."""

    class OtherStepState(WorkflowStepCodexState):
        """Wrong state model with data the declared model cannot recover."""

        marker: str

    class WrongStateStep(ExampleCodexStep):
        """Return the wrong strict state type deliberately."""

        def state_build(
            self,
            execution_context: WorkflowStepExecutionContext,
            step_input: ExampleStepInput,
        ) -> WorkflowStepCodexState:
            """Return a different strict model through an intentionally false annotation.

            Args:
                execution_context: Current step execution context.
                step_input: Persisted step input.

            Returns:
                State whose exact type does not match `state_model`.
            """

            _ = execution_context
            _ = step_input
            return OtherStepState(attempt_index=1, marker="unexpected", state="ready")

    step = WrongStateStep(
        artifact_materializer=ArtifactMaterializer(),
        artifact_writer=JsonArtifactWriter(),
        codex_runner=FakeCodexRunner([]),
        mcp_playwright_profile_runtime=McpPlaywrightProfileRuntime(),
        prompt_renderer=_prompt_renderer_get(tmp_path / "template"),
        runtime_policy=EXAMPLE_RUNTIME_POLICY,
    )

    with pytest.raises(TypeError, match="state_build returned OtherStepState; expected WorkflowStepCodexState"):
        step.run(_context_get(tmp_path), ExampleInputSource(value="text"), EXAMPLE_STEP_CONFIG)
