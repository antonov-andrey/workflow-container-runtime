"""Reusable verified Codex stage lifecycle."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, field_validator

from workflow_container_runtime.artifact import ArtifactMaterializationPolicy, ArtifactMaterializer, JsonArtifactWriter
from workflow_container_runtime.codex import CodexStageRunner
from workflow_container_runtime.prompt import PromptRenderer

MAX_STAGE_ATTEMPT_COUNT = 3
STAGE_RESULT_FILENAME = "result.json"
STAGE_PROMPT_CONTEXT_FILENAME = "prompt_context.json"
STAGE_VERIFICATION_FILENAME = "verification.json"
_ResultModelT = TypeVar("_ResultModelT", bound=BaseModel)
MechanicalValidate = Callable[[_ResultModelT], None]


class CodexStageRun(Protocol):
    """Callable protocol for one Codex-backed stage execution."""

    def __call__(
        self,
        *,
        browser_runtime_mcp_url: str = "",
        model_class: type[BaseModel],
        prompt_text: str,
        result_dir: Path,
        stage_dir: Path,
        stage_name: str,
    ) -> BaseModel:
        """Run one Codex stage and return its validated model."""


def stage_result_path_get(stage_dir: Path) -> Path:
    """Return the standard public action-stage result path.

    Args:
        stage_dir: Stage artifact directory.

    Returns:
        Standard action-stage result path.
    """

    return stage_dir / STAGE_RESULT_FILENAME


def stage_prompt_context_path_get(stage_dir: Path) -> Path:
    """Return the standard prompt-context artifact path.

    Args:
        stage_dir: Stage artifact directory.

    Returns:
        Standard prompt-context artifact path.
    """

    return stage_dir / STAGE_PROMPT_CONTEXT_FILENAME


def stage_verification_path_get(stage_dir: Path) -> Path:
    """Return the standard public verification-stage result path.

    Args:
        stage_dir: Stage artifact directory.

    Returns:
        Standard verification-stage result path.
    """

    return stage_dir / STAGE_VERIFICATION_FILENAME


class StageVerificationResult(BaseModel):
    """Verification result for one completed workflow-container stage."""

    model_config = ConfigDict(extra="forbid", strict=True)

    feedback_list: list[str] = Field(default_factory=list)
    status: Literal["success", "failed"]


class VerifiedCodexStageConfig(BaseModel):
    """Configuration for one verified Codex stage run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    artifact_materialization_policy: ArtifactMaterializationPolicy = Field(
        default_factory=ArtifactMaterializationPolicy
    )
    browser_runtime_mcp_url: str = ""
    prompt_context: SerializeAsAny[BaseModel]
    result_dir: Path
    stage_dir: Path
    stage_key: str

    @field_validator("prompt_context")
    @classmethod
    def prompt_context_validate(cls, prompt_context: BaseModel) -> BaseModel:
        """Validate that prompt context is one strict boundary model.

        Args:
            prompt_context: Candidate prompt context model.

        Returns:
            Validated prompt context model.

        Raises:
            ValueError: If the model does not enforce strict values and forbidden extra fields.
        """

        if prompt_context.model_config.get("strict") is not True:
            raise ValueError("prompt_context model must use strict=True")
        if prompt_context.model_config.get("extra") != "forbid":
            raise ValueError("prompt_context model must use extra='forbid'")
        return prompt_context


def verified_stage_artifact_write(
    *,
    artifact_writer: JsonArtifactWriter | None = None,
    config: VerifiedCodexStageConfig,
    result: BaseModel,
) -> None:
    """Write deterministic successful stage artifacts.

    Args:
        artifact_writer: JSON writer for public stage artifacts.
        config: Runtime-owned verified stage configuration.
        result: Verified stage result payload.
    """

    writer = artifact_writer or JsonArtifactWriter()
    config.stage_dir.mkdir(parents=True, exist_ok=True)
    writer.write(stage_prompt_context_path_get(config.stage_dir), config.prompt_context)
    writer.write(stage_result_path_get(config.stage_dir), result)
    writer.write(stage_verification_path_get(config.stage_dir), StageVerificationResult(status="success"))


class VerifiedCodexStageRunner:
    """Run one Codex action stage with semantic verification and retry feedback."""

    def __init__(
        self,
        *,
        artifact_writer: JsonArtifactWriter | None = None,
        codex_stage_run_callable: CodexStageRun | None = None,
        prompt_renderer: PromptRenderer | None = None,
    ) -> None:
        """Store reusable stage runtime dependencies.

        Args:
            artifact_writer: JSON writer for public stage artifacts.
            codex_stage_run_callable: Raw Codex stage execution boundary.
            prompt_renderer: Prompt renderer for project and runtime templates.
        """

        self._artifact_writer = artifact_writer or JsonArtifactWriter()
        self._codex_stage_run = codex_stage_run_callable or CodexStageRunner().run
        self._prompt_renderer = prompt_renderer or PromptRenderer()

    def run(
        self,
        *,
        config: VerifiedCodexStageConfig,
        mechanical_validate: MechanicalValidate[_ResultModelT],
        model_class: type[_ResultModelT],
    ) -> _ResultModelT:
        """Run one action stage until semantic and mechanical verification passes.

        Args:
            config: Runtime-owned verified stage configuration.
            mechanical_validate: Deterministic post-action validator.
            model_class: Pydantic result model for the action stage.

        Returns:
            Verified action-stage result.

        Raises:
            RuntimeError: If verification does not pass within the retry limit.
        """

        feedback_list: list[str] = []
        config.stage_dir.mkdir(parents=True, exist_ok=True)
        self._artifact_writer.write(stage_prompt_context_path_get(config.stage_dir), config.prompt_context)
        for attempt_index in range(1, MAX_STAGE_ATTEMPT_COUNT + 1):
            result = self._action_result_get(
                attempt_index=attempt_index,
                config=config,
                feedback_list=feedback_list,
                model_class=model_class,
            )
            result_path = stage_result_path_get(config.stage_dir)
            ArtifactMaterializer(config.result_dir).stage_artifact_materialize(
                config.stage_dir,
                config.artifact_materialization_policy,
            )
            self._artifact_writer.write(result_path, result)
            try:
                mechanical_validate(result)
            except RuntimeError as exc:
                verification = StageVerificationResult(
                    feedback_list=[str(exc)],
                    status="failed",
                )
            else:
                verification = self._verification_get(
                    config=config,
                )
            self._artifact_writer.write(stage_verification_path_get(config.stage_dir), verification)
            if verification.status == "success":
                return result
            feedback_list = verification.feedback_list

        feedback = "; ".join(feedback_list)
        if feedback:
            raise RuntimeError(
                f"Stage {config.stage_key} did not pass verification after {MAX_STAGE_ATTEMPT_COUNT} attempts: "
                f"{feedback}"
            )
        raise RuntimeError(
            f"Stage {config.stage_key} did not pass verification after {MAX_STAGE_ATTEMPT_COUNT} attempts."
        )

    def _action_result_get(
        self,
        *,
        attempt_index: int,
        config: VerifiedCodexStageConfig,
        feedback_list: list[str],
        model_class: type[_ResultModelT],
    ) -> _ResultModelT:
        """Run the action-stage Codex subprocess.

        Args:
            attempt_index: Current attempt index.
            config: Runtime-owned verified stage configuration.
            feedback_list: Verification feedback from previous attempts.
            model_class: Pydantic result model class.

        Returns:
            Validated action-stage result.
        """

        prompt_text = self._prompt_renderer.render(
            f"{config.stage_key}.md.j2",
            {
                "attempt_index": attempt_index,
                "feedback_list": feedback_list,
                "prompt_context_path": stage_prompt_context_path_get(config.stage_dir)
                .relative_to(config.result_dir)
                .as_posix(),
                "previous_stage_result_path": (
                    stage_result_path_get(config.stage_dir).relative_to(config.result_dir).as_posix()
                    if attempt_index > 1
                    else ""
                ),
                "stage_key": config.stage_key,
            },
        )
        return self._codex_stage_run(
            browser_runtime_mcp_url=config.browser_runtime_mcp_url,
            model_class=model_class,
            prompt_text=prompt_text,
            result_dir=config.result_dir,
            stage_dir=config.stage_dir,
            stage_name=config.stage_key,
        )

    def _verification_get(
        self,
        *,
        config: VerifiedCodexStageConfig,
    ) -> StageVerificationResult:
        """Run the verification-stage Codex subprocess.

        Args:
            config: Runtime-owned verified stage configuration.

        Returns:
            Verification-stage result.
        """

        verification_prompt = self._prompt_renderer.render(
            f"{config.stage_key}_verify.md.j2",
            {
                "prompt_context_path": stage_prompt_context_path_get(config.stage_dir)
                .relative_to(config.result_dir)
                .as_posix(),
                "stage_key": config.stage_key,
                "stage_result_path": stage_result_path_get(config.stage_dir).relative_to(config.result_dir).as_posix(),
            },
        )
        return cast(
            StageVerificationResult,
            self._codex_stage_run(
                browser_runtime_mcp_url=config.browser_runtime_mcp_url,
                model_class=StageVerificationResult,
                prompt_text=verification_prompt,
                result_dir=config.result_dir,
                stage_dir=config.stage_dir,
                stage_name=f"{config.stage_key}_verify",
            ),
        )
