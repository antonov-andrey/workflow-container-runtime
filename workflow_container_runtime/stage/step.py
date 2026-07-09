"""Workflow stage lifecycle owners."""

from pathlib import Path
from typing import Generic, Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field

from workflow_container_runtime.artifact import ArtifactMaterializationPolicy, ArtifactMaterializer, JsonArtifactWriter
from workflow_container_runtime.codex import CodexStageRunner
from workflow_container_runtime.prompt import PromptRenderer
from workflow_container_runtime.stage.file import (
    stage_input_path_get,
    stage_result_path_get,
    stage_verification_path_get,
)
from workflow_container_runtime.stage.runner import CodexStageRun, MAX_STAGE_ATTEMPT_COUNT

ActionOutputT = TypeVar("ActionOutputT", bound=BaseModel)
InputT = TypeVar("InputT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)


class MechanicalValidate(Protocol[ResultT]):
    """Mechanical validator contract for one public stage result."""

    def __call__(self, result: ResultT) -> None:
        """Validate one result and raise `RuntimeError` for retryable failures.

        Args:
            result: Typed public stage result.

        Raises:
            RuntimeError: If one retryable mechanical validation failure is found.
        """


def _model_contract_validate(model: BaseModel, *, model_name: str) -> None:
    """Validate one stable runtime boundary model contract.

    Args:
        model: Candidate boundary model.
        model_name: User-facing model role name.

    Raises:
        ValueError: If the model does not enforce strict values and forbidden extra fields.
    """

    if model.model_config.get("strict") is not True:
        raise ValueError(f"{model_name} model must use strict=True")
    if model.model_config.get("extra") != "forbid":
        raise ValueError(f"{model_name} model must use extra='forbid'")


def _stage_relative_path_get(*, path: Path, result_dir: Path) -> str:
    """Return one stage artifact path relative to the result directory.

    Args:
        path: Absolute or result-dir-relative artifact path.
        result_dir: Root result directory.

    Returns:
        Stage artifact path relative to the result directory.
    """

    return path.relative_to(result_dir).as_posix()


def _stage_dir_validate(*, result_dir: Path, stage_dir: Path) -> None:
    """Validate that the stage artifact directory stays under the result root.

    Args:
        result_dir: Root result directory.
        stage_dir: Stage artifact directory.

    Raises:
        ValueError: If `stage_dir` is outside `result_dir`.
    """

    try:
        stage_dir.relative_to(result_dir)
    except ValueError as exc:
        raise ValueError(f"stage_dir must be inside result_dir: stage_dir={stage_dir} result_dir={result_dir}") from exc


class StageVerificationResult(BaseModel):
    """Verification result for one completed workflow-container stage."""

    model_config = ConfigDict(extra="forbid", strict=True)

    feedback_list: list[str] = Field(default_factory=list)
    status: Literal["success", "failed"]


class WorkflowBase:
    """Base class for one workflow-family orchestration owner."""


class WorkflowStepBase(WorkflowBase, Generic[InputT, ResultT]):
    """Own standard stage input, result, and verification files."""

    def __init__(
        self, *, artifact_writer: JsonArtifactWriter | None = None, result_dir: Path, stage_dir: Path | None = None
    ) -> None:
        """Store deterministic workflow step dependencies and paths.

        Args:
            artifact_writer: JSON writer for public stage artifacts.
            result_dir: Root result directory.
            stage_dir: Optional stage artifact directory.
        """

        self._artifact_writer = artifact_writer or JsonArtifactWriter()
        self._result_dir = result_dir.resolve()
        self._stage_dir = (stage_dir or result_dir / "stage").resolve()
        _stage_dir_validate(result_dir=self._result_dir, stage_dir=self._stage_dir)

    def input_build(self) -> InputT:
        """Build typed public stage input.

        Returns:
            Typed public stage input.

        Raises:
            NotImplementedError: Always, because subclasses own this boundary.
        """

        raise NotImplementedError

    def result_build(self, stage_input: InputT) -> ResultT:
        """Build public stage result.

        Args:
            stage_input: Typed public stage input.

        Returns:
            Typed public stage result.

        Raises:
            NotImplementedError: Always, because subclasses own this boundary.
        """

        raise NotImplementedError

    def result_validate(self, result: ResultT) -> None:
        """Validate public result.

        Args:
            result: Typed public stage result.

        Raises:
            RuntimeError: If one retryable mechanical validation failure is found.
        """

        _ = result

    def run(self) -> ResultT:
        """Run deterministic step lifecycle and return public result.

        Returns:
            Typed public stage result.
        """

        self._stage_dir.mkdir(parents=True, exist_ok=True)
        stage_input = self.input_build()
        _model_contract_validate(stage_input, model_name="stage input")
        self._artifact_writer.write(stage_input_path_get(self._stage_dir), stage_input)
        result = self.result_build(stage_input)
        self.result_validate(result)
        self._artifact_writer.write(stage_result_path_get(self._stage_dir), result)
        self._artifact_writer.write(
            stage_verification_path_get(self._stage_dir), StageVerificationResult(status="success")
        )
        return result


class WorkflowStepCodexBase(WorkflowStepBase[InputT, ResultT], Generic[InputT, ActionOutputT, ResultT]):
    """Own Codex-backed stage lifecycle."""

    stage_key = ""

    def __init__(
        self,
        *,
        artifact_materialization_policy: ArtifactMaterializationPolicy | None = None,
        artifact_writer: JsonArtifactWriter | None = None,
        browser_runtime_mcp_url: str = "",
        codex_stage_run_callable: CodexStageRun | None = None,
        prompt_renderer: PromptRenderer | None = None,
        result_dir: Path,
        stage_dir: Path | None = None,
    ) -> None:
        """Store Codex-backed workflow step dependencies.

        Args:
            artifact_materialization_policy: Browser artifact materialization policy.
            artifact_writer: JSON writer for public stage artifacts.
            browser_runtime_mcp_url: Browser runtime MCP URL.
            codex_stage_run_callable: Raw Codex stage execution boundary.
            prompt_renderer: Prompt renderer for project and runtime templates.
            result_dir: Root result directory.
            stage_dir: Optional stage artifact directory.
        """

        super().__init__(artifact_writer=artifact_writer, result_dir=result_dir, stage_dir=stage_dir)
        self._artifact_materialization_policy = artifact_materialization_policy or ArtifactMaterializationPolicy()
        self._browser_runtime_mcp_url = browser_runtime_mcp_url
        self._codex_stage_run = codex_stage_run_callable or CodexStageRunner().run
        self._prompt_renderer = prompt_renderer or PromptRenderer()

    def action_output_model_get(self) -> type[ActionOutputT]:
        """Return Codex action output model.

        Returns:
            Typed Codex action output model.

        Raises:
            NotImplementedError: Always, because subclasses own this boundary.
        """

        raise NotImplementedError

    def artifact_prepare(self, stage_input: InputT) -> None:
        """Prepare declared artifact directories and schemas.

        Args:
            stage_input: Typed public stage input.
        """

        _ = stage_input

    def result_build(self, stage_input: InputT, action_output: ActionOutputT) -> ResultT:
        """Build public result from input and action output.

        Args:
            stage_input: Typed public stage input.
            action_output: Typed Codex action output.

        Returns:
            Typed public stage result.

        Raises:
            NotImplementedError: Always, because subclasses own this boundary.
        """

        raise NotImplementedError

    def run(self) -> ResultT:
        """Run deterministic Codex-backed lifecycle and return public result.

        Returns:
            Typed public stage result.

        Raises:
            RuntimeError: If verification does not pass within the retry limit or if
                `result_validate()` raises one retryable mechanical validation failure.
        """

        stage_key = self._stage_key_get()
        feedback_list: list[str] = []
        self._stage_dir.mkdir(parents=True, exist_ok=True)
        stage_input = self.input_build()
        _model_contract_validate(stage_input, model_name="stage input")
        self._artifact_writer.write(stage_input_path_get(self._stage_dir), stage_input)
        self.artifact_prepare(stage_input)
        for attempt_index in range(1, MAX_STAGE_ATTEMPT_COUNT + 1):
            action_output = self._action_output_get(
                attempt_index=attempt_index,
                feedback_list=feedback_list,
                model_class=self.action_output_model_get(),
                stage_key=stage_key,
            )
            ArtifactMaterializer(self._result_dir).stage_artifact_materialize(
                self._stage_dir,
                self._artifact_materialization_policy,
            )
            result = self.result_build(stage_input, action_output)
            try:
                self.result_validate(result)
            except RuntimeError as exc:
                verification = StageVerificationResult(feedback_list=[str(exc)], status="failed")
            else:
                self._artifact_writer.write(stage_result_path_get(self._stage_dir), result)
                verification = self._verification_get(stage_key=stage_key)
            if verification.status == "failed":
                self._artifact_writer.write(stage_result_path_get(self._stage_dir), result)
            self._artifact_writer.write(stage_verification_path_get(self._stage_dir), verification)
            if verification.status == "success":
                return result
            feedback_list = verification.feedback_list
        self._verification_failure_raise(feedback_list=feedback_list, stage_key=stage_key)

    def _action_output_get(
        self,
        *,
        attempt_index: int,
        feedback_list: list[str],
        model_class: type[ActionOutputT],
        stage_key: str,
    ) -> ActionOutputT:
        """Run the action-stage Codex subprocess.

        Args:
            attempt_index: Current attempt index.
            feedback_list: Verification feedback from previous attempts.
            model_class: Typed action output model class.
            stage_key: Stable stage prompt key.

        Returns:
            Validated action-stage output.
        """

        prompt_text = self._prompt_renderer.render(
            f"{stage_key}.md.j2",
            {
                "attempt_index": attempt_index,
                "feedback_list": feedback_list,
                "input_path": _stage_relative_path_get(
                    path=stage_input_path_get(self._stage_dir),
                    result_dir=self._result_dir,
                ),
                "previous_stage_result_path": (
                    _stage_relative_path_get(
                        path=stage_result_path_get(self._stage_dir),
                        result_dir=self._result_dir,
                    )
                    if attempt_index > 1
                    else ""
                ),
                "stage_key": stage_key,
            },
        )
        return cast(
            ActionOutputT,
            self._codex_stage_run(
                browser_runtime_mcp_url=self._browser_runtime_mcp_url,
                model_class=model_class,
                prompt_text=prompt_text,
                result_dir=self._result_dir,
                stage_dir=self._stage_dir,
                stage_name=stage_key,
            ),
        )

    def _stage_key_get(self) -> str:
        """Return the declared stage key.

        Returns:
            Stable stage key.

        Raises:
            RuntimeError: If the subclass does not declare a stage key.
        """

        if not self.stage_key:
            raise RuntimeError(f"{self.__class__.__name__} must declare stage_key")
        return self.stage_key

    def _verification_failure_raise(self, *, feedback_list: list[str], stage_key: str) -> None:
        """Raise the standard exhausted-retry error.

        Args:
            feedback_list: Verification feedback from the last attempt.
            stage_key: Stable stage prompt key.

        Raises:
            RuntimeError: Always, with the standard retry exhaustion message.
        """

        feedback = "; ".join(feedback_list)
        if feedback:
            raise RuntimeError(
                f"Stage {stage_key} did not pass verification after {MAX_STAGE_ATTEMPT_COUNT} attempts: {feedback}"
            )
        raise RuntimeError(f"Stage {stage_key} did not pass verification after {MAX_STAGE_ATTEMPT_COUNT} attempts.")

    def _verification_get(self, *, stage_key: str) -> StageVerificationResult:
        """Run the verification-stage Codex subprocess.

        Args:
            stage_key: Stable stage prompt key.

        Returns:
            Verification-stage result.
        """

        verification_prompt = self._prompt_renderer.render(
            f"{stage_key}_verify.md.j2",
            {
                "input_path": _stage_relative_path_get(
                    path=stage_input_path_get(self._stage_dir),
                    result_dir=self._result_dir,
                ),
                "stage_key": stage_key,
                "stage_result_path": _stage_relative_path_get(
                    path=stage_result_path_get(self._stage_dir),
                    result_dir=self._result_dir,
                ),
            },
        )
        return cast(
            StageVerificationResult,
            self._codex_stage_run(
                browser_runtime_mcp_url=self._browser_runtime_mcp_url,
                model_class=StageVerificationResult,
                prompt_text=verification_prompt,
                result_dir=self._result_dir,
                stage_dir=self._stage_dir,
                stage_name=f"{stage_key}_verify",
            ),
        )
