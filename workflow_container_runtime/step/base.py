"""Durable deterministic and Codex-backed step lifecycles."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Generic, TypeVar, cast, final

from pydantic import BaseModel

from workflow_container_runtime.artifact.materializer import ArtifactMaterializer
from workflow_container_runtime.artifact.writer import JsonArtifactWriter
from workflow_container_runtime.codex.runner import CodexRunner
from workflow_container_runtime.model import model_snapshot_get, strict_model_contract_validate
from workflow_container_runtime.prompt.renderer import PromptRenderer
from workflow_container_runtime.step.codex import WorkflowStepCodexConfig, WorkflowStepCodexState
from workflow_container_runtime.step.context import WorkflowStepExecutionContext
from workflow_container_runtime.step.file import input_path_get, result_path_get, state_path_get, verification_path_get
from workflow_container_runtime.verification import VerificationDecision, VerificationResult

ActionOutputT = TypeVar("ActionOutputT", bound=BaseModel)
InputSourceT = TypeVar("InputSourceT", bound=BaseModel)
InputT = TypeVar("InputT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)


class StepResultValidationError(RuntimeError):
    """Report actionable mechanical feedback for one step result."""

    def __init__(self, *, feedback_list: list[str]) -> None:
        """Store non-empty validation feedback.

        Args:
            feedback_list: Concrete corrections for the current result.

        Raises:
            ValueError: If no feedback is supplied.
        """

        if not feedback_list:
            raise ValueError("step result validation feedback must not be empty")
        super().__init__("; ".join(feedback_list))
        self.feedback_list = feedback_list


class WorkflowStepBase(ABC, Generic[InputSourceT, InputT, ResultT]):
    """Own immutable input publication and dispatch one step lifecycle."""

    result_model: ClassVar[type[ResultT]]

    def __init__(self, *, artifact_writer: JsonArtifactWriter) -> None:
        """Store the reusable standard-file writer.

        Args:
            artifact_writer: Atomic writer for standard step files.
        """

        self._artifact_writer = artifact_writer

    @final
    def run(
        self,
        execution_context: WorkflowStepExecutionContext,
        input_source: InputSourceT,
    ) -> ResultT:
        """Publish input and execute or recover the concrete lifecycle.

        Args:
            execution_context: Current step execution context.
            input_source: Public dependencies selected by the DBOS wrapper.

        Returns:
            Accepted public step result.
        """

        strict_model_contract_validate(input_source, model_role="step input source")
        input_source = model_snapshot_get(input_source)
        execution_context.step_instance_dir.mkdir(parents=True, exist_ok=True)
        step_input = self.input_build(execution_context, input_source)
        strict_model_contract_validate(step_input, model_role="step input")
        step_input = model_snapshot_get(step_input)
        self._input_publish(execution_context=execution_context, step_input=step_input)
        return self._lifecycle_run(execution_context=execution_context, step_input=step_input)

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

    @abstractmethod
    def _lifecycle_run(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
    ) -> ResultT:
        """Execute or recover the concrete step lifecycle."""

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


class WorkflowStepCodexBase(
    WorkflowStepBase[InputSourceT, InputT, ResultT],
    Generic[InputSourceT, InputT, ActionOutputT, ResultT],
):
    """Own Codex action attempts, verification, correction, and recovery."""

    action_output_model: ClassVar[type[ActionOutputT]]
    state_model: ClassVar[type[WorkflowStepCodexState]]
    step_key: ClassVar[str]

    def __init__(
        self,
        *,
        artifact_materializer: ArtifactMaterializer,
        artifact_writer: JsonArtifactWriter,
        codex_runner: CodexRunner,
        config: WorkflowStepCodexConfig,
        prompt_renderer: PromptRenderer,
    ) -> None:
        """Store reusable Codex lifecycle dependencies.

        Args:
            artifact_materializer: External artifact tree materializer.
            artifact_writer: Atomic writer for standard files.
            codex_runner: Low-level structured Codex execution boundary.
            config: Explicit correction and execution policy.
            prompt_renderer: Strict project/runtime prompt renderer.
        """

        super().__init__(artifact_writer=artifact_writer)
        self._artifact_materializer = artifact_materializer
        self._codex_runner = codex_runner
        self._config = config
        self._prompt_renderer = prompt_renderer

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
        execution_context: WorkflowStepExecutionContext,
        step_input: InputT,
    ) -> ResultT:
        """Execute or recover the Codex correction state machine."""

        self._step_key_validate()
        state = self._state_get(execution_context=execution_context, step_input=step_input)
        while True:
            recovered_result = self._recovered_result_get(
                execution_context=execution_context,
                state=state,
                step_input=step_input,
            )
            if recovered_result is not None:
                return recovered_result
            if state.state == "verification_failed":
                self._retry_prepare(execution_context=execution_context, state=state)
            if state.state != "ready":
                raise RuntimeError(f"Codex step has inconsistent state without accepted result: {state.state}")
            self.artifact_prepare(execution_context, step_input)
            action_output = self._action_output_get(
                execution_context=execution_context,
                state=state,
            )
            self._artifact_materializer.materialize(
                policy=self._config.artifact_materialization_policy,
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
        state: WorkflowStepCodexState,
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
        return cast(
            ActionOutputT,
            self._codex_runner.run(
                diagnostic_dir=(
                    execution_context.step_instance_dir / "diagnostics" / f"attempt_{state.attempt_index}" / "action"
                ),
                output_model=self.action_output_model,
                prompt=prompt,
                retry_policy=self._config.execution_retry_policy,
                runtime_capability=execution_context.runtime_capability,
                working_directory=execution_context.result_dir,
            ),
        )

    def _recovered_result_get(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        state: WorkflowStepCodexState,
        step_input: InputT,
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

    def _result_verification_get(
        self,
        *,
        execution_context: WorkflowStepExecutionContext,
        state: WorkflowStepCodexState,
        step_input: InputT,
        result: ResultT,
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
            prompt = self._prompt_renderer.render(
                template_name=f"{self.step_key}_verify.md.j2",
                variable_by_name_map={
                    "input_path": self._relative_path_get(
                        path=input_path_get(execution_context.step_instance_dir),
                        result_dir=execution_context.result_dir,
                    ),
                    "step_result_path": self._relative_path_get(
                        path=result_path_get(execution_context.step_instance_dir),
                        result_dir=execution_context.result_dir,
                    ),
                },
            )
            decision = cast(
                VerificationDecision,
                self._codex_runner.run(
                    diagnostic_dir=(
                        execution_context.step_instance_dir
                        / "diagnostics"
                        / f"attempt_{state.attempt_index}"
                        / "verification"
                    ),
                    output_model=VerificationDecision,
                    prompt=prompt,
                    retry_policy=self._config.execution_retry_policy,
                    runtime_capability=execution_context.runtime_capability,
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
        if state.attempt_index >= self._config.attempt_limit:
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
