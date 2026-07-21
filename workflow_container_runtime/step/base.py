"""Durable deterministic and Codex-backed step lifecycles."""

import asyncio
import json

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Generic, Self, TypeVar, cast, final

from dbos import DBOS
from pydantic import BaseModel, ConfigDict, model_validator
from workflow_container_contract import McpPlaywrightProfileWritebackPolicy

from workflow_container_runtime.artifact.materializer import ArtifactMaterializer
from workflow_container_runtime.artifact.writer import JsonArtifactWriter
from workflow_container_runtime.capability import WorkflowRuntimeCapability
from workflow_container_runtime.codex.config import CodexRunnerConfig
from workflow_container_runtime.codex.runner import CodexRunner
from workflow_container_runtime.mcp_playwright_profile import McpPlaywrightProfileRuntime
from workflow_container_runtime.model import model_snapshot_get, strict_model_contract_validate
from workflow_container_runtime.prompt.renderer import PromptRenderer
from workflow_container_runtime.step.codex import (
    WorkflowStepCodexConcurrentConfigBase,
    WorkflowStepCodexConfigBase,
    WorkflowStepCodexRuntimePolicy,
    WorkflowStepCodexState,
)
from workflow_container_runtime.step.context import WorkflowStepExecutionContext, WorkflowStepInvocation
from workflow_container_runtime.step.file import input_path_get, result_path_get, state_path_get, verification_path_get
from workflow_container_runtime.verification import VerificationDecision, VerificationResult

ActionOutputT = TypeVar("ActionOutputT", bound=BaseModel)
InputSourceT = TypeVar("InputSourceT", bound=BaseModel)
InputT = TypeVar("InputT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
WorkflowStepCodexConfigT = TypeVar("WorkflowStepCodexConfigT", bound=WorkflowStepCodexConfigBase)
WorkflowStepCodexConcurrentConfigT = TypeVar(
    "WorkflowStepCodexConcurrentConfigT", bound=WorkflowStepCodexConcurrentConfigBase
)


class StepResultValidationError(RuntimeError):
    """Report actionable mechanical feedback for one step result."""

    def __init__(self, feedback_list: list[str]) -> None:
        """Store non-empty validation feedback.

        Args:
            feedback_list: Concrete corrections for the current result.

        Raises:
            ValueError: If no feedback is supplied.
        """

        if not feedback_list:
            raise ValueError("step result validation feedback must not be empty")
        super().__init__(feedback_list)
        self.feedback_list = feedback_list

    def __str__(self) -> str:
        """Render validation feedback as one actionable error message.

        Returns:
            Semicolon-separated validation feedback.
        """

        return "; ".join(self.feedback_list)


class WorkflowStepInvocationOutcome(BaseModel, Generic[ResultT]):
    """Represent one concurrent invocation success or exhausted validation failure."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    result: ResultT | None
    validation_feedback_tuple: tuple[str, ...]

    @model_validator(mode="after")
    def state_validate(self) -> Self:
        """Require exactly one public outcome state.

        Returns:
            Validated success or exhausted-correction outcome.

        Raises:
            ValueError: If result and validation feedback describe an ambiguous state.
        """

        if self.result is None and not self.validation_feedback_tuple:
            raise ValueError("validation feedback is required when no result is available")
        if self.result is not None and self.validation_feedback_tuple:
            raise ValueError("validation feedback is forbidden when a result is available")
        return self


class WorkflowStepBase(ABC, Generic[InputSourceT, InputT, ResultT]):
    """Own immutable input publication shared by distinct step lifecycles."""

    result_model: ClassVar[type[ResultT]]

    def __init__(self, *, artifact_writer: JsonArtifactWriter) -> None:
        """Store the reusable standard-file writer.

        Args:
            artifact_writer: Atomic writer for standard step files.
        """

        self._artifact_writer = artifact_writer

    def _step_input_get(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: InputSourceT,
    ) -> InputT:
        """Publish and return immutable public input for one step.

        Args:
            execution_context: Current step execution context.
            input_source: Public dependencies selected by the DBOS wrapper.

        Returns:
            Accepted public step input.
        """

        strict_model_contract_validate(input_source, model_role="step input source")
        input_source = model_snapshot_get(input_source)
        execution_context.step_instance_dir.mkdir(parents=True, exist_ok=True)
        step_input = self.input_build(execution_context, input_source)
        strict_model_contract_validate(step_input, model_role="step input")
        step_input = model_snapshot_get(step_input)
        self._input_publish(execution_context=execution_context, step_input=step_input)
        return step_input

    def artifact_prepare(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
    ) -> None:
        """Prepare idempotent declared artifacts before domain work.

        Args:
            execution_context: Current step execution context.
            step_input: Persisted step input.
        """

        _ = execution_context
        _ = step_input

    @abstractmethod
    def input_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: InputSourceT,
    ) -> InputT:
        """Build the persisted step input from selected public dependencies.

        Args:
            execution_context: Current step execution context.
            input_source: Public dependencies selected by the DBOS wrapper.

        Returns:
            Strict persisted step input.
        """

    def result_validate(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
        result: ResultT,
    ) -> None:
        """Validate optional mechanical result invariants.

        Args:
            execution_context: Current step execution context.
            step_input: Persisted step input.
            result: Candidate public result.
        """

        _ = execution_context
        _ = step_input
        _ = result

    def _input_publish(self, *, execution_context: WorkflowStepExecutionContext, step_input: InputT) -> None:
        """Publish immutable input or verify the existing identity.

        Args:
            execution_context: Current step execution context.
            step_input: Strict persisted step input.

        Raises:
            RuntimeError: If the instance already belongs to another input.
        """

        path = input_path_get(execution_context.step_instance_dir)
        if path.exists():
            existing_input = type(step_input).model_validate_json(path.read_text(encoding="utf-8"))
            if existing_input != step_input:
                raise RuntimeError("step input does not match existing input.json")
            return
        if any(execution_context.step_instance_dir.iterdir()):
            raise RuntimeError("step instance contains lifecycle data without input.json")
        self._artifact_writer.write(path, step_input)

    def _result_read(self, execution_context: WorkflowStepExecutionContext) -> ResultT:
        """Read the exact configured public result model.

        Args:
            execution_context: Current step execution context.

        Returns:
            Parsed public result.
        """

        return cast(
            ResultT,
            self.result_model.model_validate_json(
                result_path_get(execution_context.step_instance_dir).read_text(encoding="utf-8")
            ),
        )

    def _result_snapshot_get(self, result: ResultT, *, producer_name: str) -> ResultT:
        """Return an exact independently validated result snapshot.

        Args:
            result: Candidate result returned by concrete domain code.
            producer_name: Concrete producer used in an actionable error.

        Returns:
            Canonical snapshot used for publication and later lifecycle work.

        Raises:
            TypeError: If the concrete producer returned another model type.
        """

        if type(result) is not self.result_model:
            raise TypeError(f"{producer_name} returned {type(result).__name__}; expected {self.result_model.__name__}")
        strict_model_contract_validate(result, model_role="step result")
        return model_snapshot_get(result)


class WorkflowStepDeterministicBase(
    WorkflowStepBase[InputSourceT, InputT, ResultT],
    Generic[InputSourceT, InputT, ResultT],
):
    """Own publication, validation, and recovery for deterministic work."""

    @final
    def run(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: InputSourceT,
    ) -> ResultT:
        """Publish input and execute or recover one deterministic step.

        Args:
            execution_context: Current step execution context.
            input_source: Public dependencies selected by the DBOS wrapper.

        Returns:
            Accepted public step result.
        """

        step_input = self._step_input_get(execution_context, input_source)
        return self._lifecycle_run(execution_context=execution_context, step_input=step_input)

    @abstractmethod
    def result_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
    ) -> ResultT:
        """Build one deterministic public result.

        Args:
            execution_context: Current step execution context.
            step_input: Persisted step input.

        Returns:
            Candidate public result.
        """

    @final
    def _lifecycle_run(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
    ) -> ResultT:
        """Execute or recover deterministic result publication."""

        instance_dir = execution_context.step_instance_dir
        result_path = result_path_get(instance_dir)
        verification_path = verification_path_get(instance_dir)
        if result_path.exists():
            result = self._result_read(execution_context)
            if verification_path.exists():
                verification = VerificationResult.model_validate_json(verification_path.read_text(encoding="utf-8"))
                if verification.status == "success" and verification.is_bound_to(
                    result,
                    result_revision_index=1,
                ):
                    return result
        else:
            self.artifact_prepare(execution_context, step_input)
            result = self.result_build(execution_context, step_input)
            result = self._result_snapshot_get(result, producer_name="result_build")
            self._artifact_writer.write(result_path, result)
        try:
            self.result_validate(execution_context, step_input, result)
        except StepResultValidationError as exc:
            self._artifact_writer.write(
                verification_path,
                VerificationResult.from_decision(
                    decision=VerificationDecision(status="failed", feedback_list=exc.feedback_list),
                    result=result,
                    result_revision_index=1,
                ),
            )
            raise
        self._artifact_writer.write(
            verification_path,
            VerificationResult.from_decision(
                decision=VerificationDecision(status="success", feedback_list=[]),
                result=result,
                result_revision_index=1,
            ),
        )
        return result


def _mcp_playwright_profile_writeback_policy_get(
    execution_context: WorkflowStepExecutionContext,
) -> McpPlaywrightProfileWritebackPolicy:
    """Load the exact persisted workflow-level profile writeback policy.

    Args:
        execution_context: Current step execution context.

    Returns:
        Validated run-owned profile writeback policy.

    Raises:
        RuntimeError: If the persisted workflow input lacks a valid policy.
    """

    workflow_input_path = execution_context.result_dir / execution_context.workflow_input_path
    try:
        workflow_input_value = json.loads(workflow_input_path.read_text(encoding="utf-8"))
        policy_value = workflow_input_value["config"]["mcp_playwright_profile_writeback_policy"]
        return McpPlaywrightProfileWritebackPolicy.model_validate_json(json.dumps(policy_value))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise RuntimeError("workflow input does not contain a valid Playwright profile writeback policy") from exc


class WorkflowStepCodexBase(
    WorkflowStepBase[InputSourceT, InputT, ResultT],
    Generic[InputSourceT, InputT, WorkflowStepCodexConfigT, ActionOutputT, ResultT],
):
    """Own Codex action attempts, verification, correction, and recovery."""

    action_output_model: ClassVar[type[ActionOutputT]]
    config_model: ClassVar[type[WorkflowStepCodexConfigT]]
    state_model: ClassVar[type[WorkflowStepCodexState]]
    step_key: ClassVar[str]

    def __init__(
        self,
        *,
        artifact_materializer: ArtifactMaterializer,
        artifact_writer: JsonArtifactWriter,
        codex_runner: CodexRunner,
        mcp_playwright_profile_runtime: McpPlaywrightProfileRuntime,
        prompt_renderer: PromptRenderer,
        runtime_policy: WorkflowStepCodexRuntimePolicy,
    ) -> None:
        """Store reusable Codex lifecycle dependencies.

        Args:
            artifact_materializer: External artifact tree materializer.
            artifact_writer: Atomic writer for standard files.
            codex_runner: Low-level structured Codex execution boundary.
            mcp_playwright_profile_runtime: Run-local browser profile routing and lease owner.
            prompt_renderer: Strict project/runtime prompt renderer.
            runtime_policy: Source-owned materialization and transport retry policy.
        """

        super().__init__(artifact_writer=artifact_writer)
        self._artifact_materializer = artifact_materializer
        self._codex_runner = codex_runner
        self._mcp_playwright_profile_runtime = mcp_playwright_profile_runtime
        self._prompt_renderer = prompt_renderer
        self._runtime_policy = runtime_policy

    @final
    def run(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: InputSourceT,
        workflow_step_config: WorkflowStepCodexConfigT,
    ) -> ResultT:
        """Publish input and execute or recover one configured Codex step.

        Args:
            execution_context: Current step execution context.
            input_source: Public dependencies selected by the DBOS wrapper.
            workflow_step_config: Exact run-owned configuration selected by the workflow.

        Returns:
            Accepted public step result.
        """

        return self._run_with_profile(
            execution_context=execution_context,
            input_source=input_source,
            mcp_playwright_profile=workflow_step_config.mcp_playwright_profile,
            workflow_step_config=workflow_step_config,
        )

    def _run_with_profile(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: InputSourceT,
        workflow_step_config: WorkflowStepCodexConfigT,
        mcp_playwright_profile: str | None,
    ) -> ResultT:
        """Run one validated lifecycle with an exact physical target profile."""

        execution_context = WorkflowStepExecutionContext.model_validate(execution_context.model_dump(mode="python"))
        self._step_key_validate()
        self._workflow_step_config_type_validate(workflow_step_config)
        self._workflow_step_config_input_validate(
            execution_context=execution_context,
            workflow_step_config=workflow_step_config,
        )
        step_input = self._step_input_get(execution_context, input_source)
        with self._mcp_playwright_profile_runtime.lease(
            mcp_playwright_profile=mcp_playwright_profile,
            mcp_playwright_profile_source=workflow_step_config.mcp_playwright_profile_source,
            runtime_capability=execution_context.runtime_capability,
        ) as route:
            result = self._lifecycle_run(
                action_runtime_capability=route.action_runtime_capability,
                execution_context=execution_context,
                step_input=step_input,
                verification_runtime_capability=route.verification_runtime_capability,
                workflow_step_config=workflow_step_config,
            )
            if mcp_playwright_profile is not None:
                step_identity = execution_context.step_instance_dir.relative_to(execution_context.result_dir).as_posix()
                self._mcp_playwright_profile_runtime.writeback_candidate_stage(
                    route,
                    policy=_mcp_playwright_profile_writeback_policy_get(execution_context),
                    step_identity=step_identity,
                    step_key=self.step_key,
                    transition_identity=f"{step_identity}/completed",
                )
            return result

    @abstractmethod
    def result_from_action_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
        action_output: ActionOutputT,
    ) -> ResultT:
        """Build the public result from input and action-owned output.

        Args:
            execution_context: Current step execution context.
            step_input: Persisted step input.
            action_output: Exact structured Codex action output.

        Returns:
            Candidate public step result.
        """

    def state_build(
        self,
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
    ) -> WorkflowStepCodexState:
        """Build initial private state, allowing domain model defaults.

        Args:
            execution_context: Current step execution context.
            step_input: Persisted step input.

        Returns:
            Initial private Codex state.
        """

        _ = execution_context
        _ = step_input
        return self.state_model(attempt_index=1, state="ready")

    @final
    def _lifecycle_run(
        self,
        *,
        action_runtime_capability: WorkflowRuntimeCapability,
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
        verification_runtime_capability: WorkflowRuntimeCapability,
        workflow_step_config: WorkflowStepCodexConfigT,
    ) -> ResultT:
        """Execute or recover the Codex correction state machine."""

        state = self._state_get(execution_context=execution_context, step_input=step_input)
        while True:
            recovered_result = self._recovered_result_get(
                execution_context=execution_context,
                state=state,
                step_input=step_input,
                verification_runtime_capability=verification_runtime_capability,
                workflow_step_config=workflow_step_config,
            )
            if recovered_result is not None:
                return recovered_result
            if state.state == "verification_failed":
                self._retry_prepare(
                    execution_context=execution_context,
                    state=state,
                    workflow_step_config=workflow_step_config,
                )
            if state.state != "ready":
                raise RuntimeError(f"Codex step has inconsistent state without accepted result: {state.state}")
            self.artifact_prepare(execution_context, step_input)
            action_output = self._action_output_get(
                execution_context=execution_context,
                runtime_capability=action_runtime_capability,
                state=state,
                workflow_step_config=workflow_step_config,
            )
            self._artifact_materializer.materialize(
                policy=self._runtime_policy.artifact_materialization_policy,
                result_dir=execution_context.result_dir,
                step_instance_dir=execution_context.step_instance_dir,
            )
            result = self.result_from_action_build(execution_context, step_input, action_output)
            result = self._result_snapshot_get(result, producer_name="result_from_action_build")
            self._artifact_writer.write(result_path_get(execution_context.step_instance_dir), result)
            state.state = "result_published"
            self._state_write(execution_context=execution_context, state=state)
            verification = self._result_verification_get(
                execution_context=execution_context,
                state=state,
                step_input=step_input,
                result=result,
                runtime_capability=verification_runtime_capability,
                workflow_step_config=workflow_step_config,
            )
            if verification.status == "success":
                state.state = "completed"
                self._state_write(execution_context=execution_context, state=state)
                return result
            state.state = "verification_failed"
            self._state_write(execution_context=execution_context, state=state)

    def _action_output_get(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        runtime_capability: WorkflowRuntimeCapability,
        state: WorkflowStepCodexState,
        workflow_step_config: WorkflowStepCodexConfigT,
    ) -> ActionOutputT:
        """Render and execute one action attempt.

        Args:
            execution_context: Current step execution context.
            state: Current private correction state.

        Returns:
            Exact structured action output.
        """

        variable_by_name_map = {
            "input_path": self._relative_path_get(
                path=input_path_get(execution_context.step_instance_dir),
                result_dir=execution_context.result_dir,
            )
        }
        if state.attempt_index > 1:
            variable_by_name_map.update(
                {
                    "previous_attempt_result_path": self._relative_path_get(
                        path=result_path_get(execution_context.step_instance_dir),
                        result_dir=execution_context.result_dir,
                    ),
                    "previous_attempt_verification_path": self._relative_path_get(
                        path=verification_path_get(execution_context.step_instance_dir),
                        result_dir=execution_context.result_dir,
                    ),
                }
            )
        prompt = self._prompt_renderer.render(
            template_name=f"{self.step_key}.md.j2",
            variable_by_name_map=variable_by_name_map,
        )
        prompt = self._instruction_context_prompt_get(
            input_path=variable_by_name_map["input_path"],
            prompt=prompt,
        )
        return cast(
            ActionOutputT,
            self._codex_runner.run(
                config=CodexRunnerConfig(
                    model=workflow_step_config.model,
                    reasoning_effort=workflow_step_config.reasoning_effort,
                ),
                diagnostic_dir=(
                    execution_context.step_instance_dir / "diagnostics" / f"attempt_{state.attempt_index}" / "action"
                ),
                output_model=self.action_output_model,
                prompt=prompt,
                retry_policy=self._runtime_policy.execution_retry_policy,
                runtime_capability=runtime_capability,
                working_directory=execution_context.result_dir,
            ),
        )

    def _recovered_result_get(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        state: WorkflowStepCodexState,
        step_input: InputT,
        verification_runtime_capability: WorkflowRuntimeCapability,
        workflow_step_config: WorkflowStepCodexConfigT,
    ) -> ResultT | None:
        """Recover or classify one previously published candidate.

        Args:
            execution_context: Current step execution context.
            state: Current private correction state.
            step_input: Persisted step input.

        Returns:
            Accepted recovered result, or `None` when another action is required.

        Raises:
            RuntimeError: If state claims files that do not exist.
        """

        result_path = result_path_get(execution_context.step_instance_dir)
        verification_path = verification_path_get(execution_context.step_instance_dir)
        if not result_path.exists():
            if state.state != "ready":
                raise RuntimeError(f"Codex step state {state.state} requires result.json")
            return None
        result = self._result_read(execution_context)
        if not verification_path.exists():
            if state.state == "ready":
                state.state = "result_published"
                self._state_write(execution_context=execution_context, state=state)
            if state.state != "result_published":
                raise RuntimeError(f"Codex step state {state.state} is inconsistent with unverified result.json")
            verification = self._result_verification_get(
                execution_context=execution_context,
                state=state,
                step_input=step_input,
                result=result,
                runtime_capability=verification_runtime_capability,
                workflow_step_config=workflow_step_config,
            )
            if verification.status == "success":
                state.state = "completed"
                self._state_write(execution_context=execution_context, state=state)
                return result
            state.state = "verification_failed"
            self._state_write(execution_context=execution_context, state=state)
            return None
        verification = VerificationResult.model_validate_json(verification_path.read_text(encoding="utf-8"))
        if not verification.is_bound_to(result, result_revision_index=state.attempt_index):
            have_same_result = verification.has_result_digest(result)
            was_ready = state.state == "ready"
            if not (have_same_result and was_ready) and state.state != "result_published":
                state.state = "result_published"
                self._state_write(execution_context=execution_context, state=state)
            verification = self._result_verification_get(
                execution_context=execution_context,
                state=state,
                step_input=step_input,
                result=result,
                runtime_capability=verification_runtime_capability,
                workflow_step_config=workflow_step_config,
            )
            if verification.status == "success":
                state.state = "completed"
                self._state_write(execution_context=execution_context, state=state)
                return result
            if have_same_result and was_ready:
                return None
            state.state = "verification_failed"
            self._state_write(execution_context=execution_context, state=state)
            return None
        if verification.status == "success":
            state.state = "completed"
            self._state_write(execution_context=execution_context, state=state)
            return result
        if state.state == "result_published":
            state.state = "verification_failed"
            self._state_write(execution_context=execution_context, state=state)
        if state.state == "completed":
            raise RuntimeError("completed Codex step has failed verification.json")
        return None

    def _relative_path_get(self, *, path: Path, result_dir: Path) -> str:
        """Return one public path relative to the run result root.

        Args:
            path: Path inside the result root.
            result_dir: Run result root.

        Returns:
            POSIX result-relative path.
        """

        return path.relative_to(result_dir).as_posix()

    def _instruction_context_prompt_get(self, *, input_path: str, prompt: str) -> str:
        """Prepend the shared step instruction routing context to one domain prompt.

        Args:
            input_path: Result-relative path to the current step input.
            prompt: Rendered domain action or verification prompt.

        Returns:
            Complete prompt with one canonical instruction-routing owner.
        """

        instruction_context = self._prompt_renderer.render(
            template_name="runtime/partial/step_instruction_context.md.j2",
            variable_by_name_map={"input_path": input_path, "step_key": self.step_key},
        )
        return "\n\n".join([instruction_context, prompt])

    def _result_verification_get(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        state: WorkflowStepCodexState,
        step_input: InputT,
        result: ResultT,
        runtime_capability: WorkflowRuntimeCapability,
        workflow_step_config: WorkflowStepCodexConfigT,
    ) -> VerificationResult:
        """Run mechanical validation and then mandatory semantic verification.

        Args:
            execution_context: Current step execution context.
            state: Current private correction state.
            step_input: Persisted step input.
            result: Current published result.

        Returns:
            Published verification verdict.
        """

        verification_path = verification_path_get(execution_context.step_instance_dir)
        try:
            self.result_validate(execution_context, step_input, result)
        except StepResultValidationError as exc:
            decision = VerificationDecision(status="failed", feedback_list=exc.feedback_list)
        else:
            input_path = self._relative_path_get(
                path=input_path_get(execution_context.step_instance_dir),
                result_dir=execution_context.result_dir,
            )
            prompt = self._prompt_renderer.render(
                template_name=f"{self.step_key}_verify.md.j2",
                variable_by_name_map={
                    "input_path": input_path,
                    "step_result_path": self._relative_path_get(
                        path=result_path_get(execution_context.step_instance_dir),
                        result_dir=execution_context.result_dir,
                    ),
                },
            )
            prompt = self._instruction_context_prompt_get(input_path=input_path, prompt=prompt)
            decision = cast(
                VerificationDecision,
                self._codex_runner.run(
                    config=CodexRunnerConfig(
                        model=workflow_step_config.model,
                        reasoning_effort=workflow_step_config.reasoning_effort,
                    ),
                    diagnostic_dir=(
                        execution_context.step_instance_dir
                        / "diagnostics"
                        / f"attempt_{state.attempt_index}"
                        / "verification"
                    ),
                    output_model=VerificationDecision,
                    prompt=prompt,
                    retry_policy=self._runtime_policy.execution_retry_policy,
                    runtime_capability=runtime_capability,
                    working_directory=execution_context.result_dir,
                ),
            )
        verification = VerificationResult.from_decision(
            decision=decision,
            result=result,
            result_revision_index=state.attempt_index,
        )
        self._artifact_writer.write(verification_path, verification)
        return verification

    def _retry_prepare(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        state: WorkflowStepCodexState,
        workflow_step_config: WorkflowStepCodexConfigT,
    ) -> None:
        """Advance one failed verdict to the next ready attempt exactly once.

        Args:
            execution_context: Current step execution context.
            state: Current private correction state.

        Raises:
            StepResultValidationError: If the correction limit is exhausted.
        """

        verification = VerificationResult.model_validate_json(
            verification_path_get(execution_context.step_instance_dir).read_text(encoding="utf-8")
        )
        if state.attempt_index - 1 >= workflow_step_config.correction_attempt_limit:
            raise StepResultValidationError(feedback_list=verification.feedback_list)
        state.attempt_index += 1
        state.state = "ready"
        self._state_write(execution_context=execution_context, state=state)

    def _state_get(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
    ) -> WorkflowStepCodexState:
        """Load existing private state or publish the initial state.

        Args:
            execution_context: Current step execution context.
            step_input: Persisted step input.

        Returns:
            Current private Codex state.
        """

        path = state_path_get(execution_context.step_instance_dir)
        if path.exists():
            return self.state_model.model_validate_json(path.read_text(encoding="utf-8"))
        unexpected_path_list = [
            current_path
            for current_path in execution_context.step_instance_dir.iterdir()
            if current_path != input_path_get(execution_context.step_instance_dir)
        ]
        if unexpected_path_list:
            raise RuntimeError("Codex step contains lifecycle data without state.json")
        state = self.state_build(execution_context, step_input)
        if type(state) is not self.state_model:
            raise TypeError(f"state_build returned {type(state).__name__}; expected {self.state_model.__name__}")
        strict_model_contract_validate(state, model_role="Codex step state")
        state = model_snapshot_get(state)
        self._state_write(execution_context=execution_context, state=state)
        return state

    def _state_write(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        state: WorkflowStepCodexState,
    ) -> None:
        """Atomically publish current private state.

        Args:
            execution_context: Current step execution context.
            state: Current private Codex state.
        """

        self._artifact_writer.write(state_path_get(execution_context.step_instance_dir), state)

    def _step_key_validate(self) -> None:
        """Require one declared action key.

        Raises:
            RuntimeError: If no step key is declared.
        """

        if not self.step_key:
            raise RuntimeError(f"{type(self).__name__} must declare step_key")

    def _workflow_step_config_input_validate(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        workflow_step_config: WorkflowStepCodexConfigT,
    ) -> None:
        """Require the DBOS argument to match the persisted workflow input exactly.

        Args:
            execution_context: Current step execution context.
            workflow_step_config: Exact configuration passed to the DBOS step call.

        Raises:
            RuntimeError: If the persisted input lacks this step config or differs from the argument.
        """

        workflow_input_path = execution_context.result_dir / execution_context.workflow_input_path
        try:
            workflow_input_value = json.loads(workflow_input_path.read_text(encoding="utf-8"))
            config_value = workflow_input_value["config"]["step_map"][self.step_key]
        except (KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"workflow input does not contain config for {self.step_key}") from exc
        persisted_config = self.config_model.model_validate(config_value)
        if persisted_config != workflow_step_config:
            raise RuntimeError(f"workflow step config does not match workflow input for {self.step_key}")

    def _workflow_step_config_type_validate(self, workflow_step_config: WorkflowStepCodexConfigT) -> None:
        """Require the exact concrete config model selected by the step owner.

        Args:
            workflow_step_config: Candidate run-owned Codex configuration.

        Raises:
            TypeError: If the caller supplied another model type.
        """

        if type(workflow_step_config) is not self.config_model:
            raise TypeError(
                f"workflow_step_config is {type(workflow_step_config).__name__}; expected {self.config_model.__name__}"
            )


class WorkflowStepCodexConcurrentBase(
    WorkflowStepCodexBase[
        InputSourceT,
        InputT,
        WorkflowStepCodexConcurrentConfigT,
        ActionOutputT,
        ResultT,
    ],
    Generic[InputSourceT, InputT, WorkflowStepCodexConcurrentConfigT, ActionOutputT, ResultT],
):
    """Schedule bounded independent Codex step invocations in input order."""

    @final
    async def run_list(
        self,
        invocation_list: list[WorkflowStepInvocation[InputSourceT]],
        workflow_step_config: WorkflowStepCodexConcurrentConfigT,
    ) -> list[ResultT]:
        """Run all independent invocation objects with one bounded DBOS scheduler.

        Args:
            invocation_list: Non-empty ordered independent step invocations.
            workflow_step_config: Exact shared concurrent run configuration.

        Returns:
            Accepted results in input order.

        Raises:
            ValueError: If invocation contexts do not describe one concurrent group.
            Exception: The lowest-index non-validation error after all work completes, or the lowest-index
                validation error when no non-validation error occurs.
        """

        outcome_list = await self.run_outcome_list(invocation_list, workflow_step_config)
        for outcome in outcome_list:
            if outcome.validation_feedback_tuple:
                raise StepResultValidationError(feedback_list=list(outcome.validation_feedback_tuple))
        return [cast(ResultT, outcome.result) for outcome in outcome_list]

    @final
    async def run_outcome_list(
        self,
        invocation_list: list[WorkflowStepInvocation[InputSourceT]],
        workflow_step_config: WorkflowStepCodexConcurrentConfigT,
    ) -> list[WorkflowStepInvocationOutcome[ResultT]]:
        """Run a validated concurrent group while preserving exhausted validation failures in order."""

        self._invocation_list_validate(invocation_list, workflow_step_config)
        physical_profile_list = workflow_step_config.mcp_playwright_profile_physical_list_get()
        result_or_error_list: list[object] = [None] * len(invocation_list)

        async def lane_run(lane_index: int) -> None:
            """Run one round-robin lane sequentially through the DBOS step boundary."""

            for invocation_index in range(lane_index, len(invocation_list), workflow_step_config.concurrency):
                invocation = invocation_list[invocation_index]
                try:
                    result_or_error_list[invocation_index] = await DBOS.run_step_async(
                        {"name": f"{type(self).__name__}.run"},
                        self._run_with_profile,
                        invocation.execution_context,
                        invocation.input_source,
                        workflow_step_config,
                        physical_profile_list[lane_index],
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    result_or_error_list[invocation_index] = exc

        lane_count = min(workflow_step_config.concurrency, len(invocation_list))
        await asyncio.gather(*(asyncio.create_task(lane_run(lane_index)) for lane_index in range(lane_count)))
        outcome_list: list[WorkflowStepInvocationOutcome[ResultT]] = []
        for result_or_error in result_or_error_list:
            if isinstance(result_or_error, StepResultValidationError):
                outcome_list.append(
                    WorkflowStepInvocationOutcome(
                        result=None,
                        validation_feedback_tuple=tuple(result_or_error.feedback_list),
                    )
                )
            elif isinstance(result_or_error, Exception):
                raise result_or_error
            else:
                outcome_list.append(
                    WorkflowStepInvocationOutcome(
                        result=cast(ResultT, result_or_error),
                        validation_feedback_tuple=(),
                    )
                )
        return outcome_list

    def _invocation_list_validate(
        self,
        invocation_list: list[WorkflowStepInvocation[InputSourceT]],
        workflow_step_config: WorkflowStepCodexConcurrentConfigT,
    ) -> None:
        """Validate the shared result root, workflow input, and unique step identities."""

        if not invocation_list:
            raise ValueError("invocation_list must not be empty")
        self._workflow_step_config_type_validate(workflow_step_config)
        first_context = invocation_list[0].execution_context
        step_instance_dir_set: set[Path] = set()
        for invocation in invocation_list:
            execution_context = invocation.execution_context
            step_instance_dir = execution_context.step_instance_dir.resolve()
            if execution_context.result_dir != first_context.result_dir:
                raise ValueError("all invocations must use one result_dir")
            if execution_context.workflow_input_path != first_context.workflow_input_path:
                raise ValueError("all invocations must use one workflow_input_path")
            if step_instance_dir in step_instance_dir_set:
                raise ValueError("invocations must use unique step_instance_dir values")
            try:
                step_instance_dir.relative_to(execution_context.result_dir.resolve())
            except ValueError as exc:
                raise ValueError("every step_instance_dir must be inside result_dir") from exc
            step_instance_dir_set.add(step_instance_dir)
