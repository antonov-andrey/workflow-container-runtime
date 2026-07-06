"""Reusable verified Codex stage lifecycle."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field

from workflow_container_runtime.artifact import ArtifactMaterializationPolicy, ArtifactMaterializer, JsonArtifactWriter
from workflow_container_runtime.codex import CodexStageRunner
from workflow_container_runtime.prompt import PromptRenderer

MAX_STAGE_ATTEMPT_COUNT = 3
CodexStageRun = Callable[..., BaseModel]
_ResultModelT = TypeVar("_ResultModelT", bound=BaseModel)
MechanicalErrorListGet = Callable[[_ResultModelT], list[str]]


class StageVerificationResult(BaseModel):
    """Verification result for one completed workflow-container stage."""

    model_config = ConfigDict(extra="forbid", strict=True)

    error_list: list[str] = Field(default_factory=list)
    feedback_list: list[str] = Field(default_factory=list)
    status: Literal["success", "failed"]


class VerifiedCodexStageConfig(BaseModel):
    """Configuration for one verified Codex stage run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    action_template_name: str
    allow_user_config: bool = False
    artifact_materialization_policy: ArtifactMaterializationPolicy = Field(
        default_factory=ArtifactMaterializationPolicy
    )
    browser_runtime_mcp_url: str = ""
    prompt_context: str
    result_dir: Path
    shared_instruction: str = ""
    stage_dir: Path
    stage_instruction_text: str = ""
    stage_key: str
    verification_template_name: str


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
        draft_result: _ResultModelT,
        mechanical_error_list_get: MechanicalErrorListGet[_ResultModelT] | None = None,
        model_class: type[_ResultModelT],
    ) -> _ResultModelT:
        """Run one action stage until semantic and mechanical verification passes.

        Args:
            config: Runtime-owned verified stage configuration.
            draft_result: Deterministic draft result used as initial stage input.
            mechanical_error_list_get: Optional deterministic post-verification validator.
            model_class: Pydantic result model for the action stage.

        Returns:
            Verified action-stage result.

        Raises:
            RuntimeError: If verification does not pass within the retry limit.
        """

        feedback_list: list[str] = []
        draft_result_json = draft_result.model_dump_json(indent=2)
        previous_result_json = ""
        config.stage_dir.mkdir(parents=True, exist_ok=True)
        for attempt_index in range(1, MAX_STAGE_ATTEMPT_COUNT + 1):
            result = self._action_result_get(
                attempt_index=attempt_index,
                config=config,
                draft_result_json=draft_result_json,
                feedback_list=feedback_list,
                model_class=model_class,
                previous_result_json=previous_result_json,
            )
            result_path = self.stage_result_path(config.stage_dir)
            artifact_materializer = self._artifact_materializer_get(config)
            if artifact_materializer is not None:
                artifact_materializer.stage_artifact_materialize(
                    config.stage_dir,
                    config.artifact_materialization_policy,
                )
            self._artifact_writer.write(result_path, result)
            previous_result_json = result.model_dump_json(indent=2)
            verification = self._verification_get(
                config=config,
                result=result,
            )
            if verification.status == "success" and mechanical_error_list_get is not None:
                mechanical_error_list = mechanical_error_list_get(result)
                if mechanical_error_list:
                    verification = StageVerificationResult(
                        error_list=mechanical_error_list,
                        feedback_list=mechanical_error_list,
                        status="failed",
                    )
            self._artifact_writer.write(self.stage_verification_path(config.stage_dir), verification)
            if verification.status == "success":
                return result
            feedback_list = verification.feedback_list or verification.error_list

        feedback = "; ".join(feedback_list)
        if feedback:
            raise RuntimeError(
                f"Stage {config.stage_key} did not pass verification after {MAX_STAGE_ATTEMPT_COUNT} attempts: "
                f"{feedback}"
            )
        raise RuntimeError(
            f"Stage {config.stage_key} did not pass verification after {MAX_STAGE_ATTEMPT_COUNT} attempts."
        )

    def stage_result_path(self, stage_dir: Path) -> Path:
        """Return the standard public action-stage result path.

        Args:
            stage_dir: Stage artifact directory.

        Returns:
            Standard action-stage result path.
        """

        return stage_dir / "result.json"

    def stage_state_path(self, stage_dir: Path) -> Path:
        """Return the standard optional stage state path.

        Args:
            stage_dir: Stage artifact directory.

        Returns:
            Standard stage state path.
        """

        return stage_dir / "state.json"

    def stage_verification_path(self, stage_dir: Path) -> Path:
        """Return the standard public verification-stage result path.

        Args:
            stage_dir: Stage artifact directory.

        Returns:
            Standard verification-stage result path.
        """

        return stage_dir / "verification.json"

    def _action_result_get(
        self,
        *,
        attempt_index: int,
        config: VerifiedCodexStageConfig,
        draft_result_json: str,
        feedback_list: list[str],
        model_class: type[_ResultModelT],
        previous_result_json: str,
    ) -> _ResultModelT:
        """Run the action-stage Codex subprocess.

        Args:
            attempt_index: Current attempt index.
            config: Runtime-owned verified stage configuration.
            draft_result_json: JSON draft result for prompt context.
            feedback_list: Verification feedback from previous attempts.
            model_class: Pydantic result model class.
            previous_result_json: Previous attempt result JSON.

        Returns:
            Validated action-stage result.
        """

        prompt_text = self._prompt_renderer.render(
            config.action_template_name,
            {
                "attempt_index": attempt_index,
                "draft_result_json": draft_result_json,
                "feedback_list": feedback_list,
                "previous_result_json": previous_result_json,
                "prompt_context": config.prompt_context,
                "shared_instruction": config.shared_instruction,
                "stage_instruction_text": config.stage_instruction_text,
                "stage_key": config.stage_key,
            },
        )
        return self._codex_stage_run(
            allow_user_config=config.allow_user_config,
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
        result: BaseModel,
    ) -> StageVerificationResult:
        """Run the verification-stage Codex subprocess.

        Args:
            config: Runtime-owned verified stage configuration.
            result: Action-stage result.

        Returns:
            Verification-stage result.
        """

        draft_verification = StageVerificationResult(status="success")
        verification_prompt = self._prompt_renderer.render(
            config.verification_template_name,
            {
                "draft_verification_json": draft_verification.model_dump_json(indent=2),
                "prompt_context": config.prompt_context,
                "stage_key": config.stage_key,
                "stage_result_json": result.model_dump_json(indent=2),
                "stage_result_path": self.stage_result_path(config.stage_dir).relative_to(config.result_dir).as_posix(),
                "stage_state_path": self.stage_state_path(config.stage_dir).relative_to(config.result_dir).as_posix(),
            },
        )
        return cast(
            StageVerificationResult,
            self._codex_stage_run(
                allow_user_config=config.allow_user_config,
                browser_runtime_mcp_url=config.browser_runtime_mcp_url,
                model_class=StageVerificationResult,
                prompt_text=verification_prompt,
                result_dir=config.result_dir,
                stage_dir=config.stage_dir,
                stage_name=f"{config.stage_key}_verify",
            ),
        )

    def _artifact_materializer_get(self, config: VerifiedCodexStageConfig) -> ArtifactMaterializer | None:
        """Return the materializer for the current stage policy.

        Args:
            config: Runtime-owned verified stage configuration.

        Returns:
            Runtime-owned artifact materializer or `None` when materialization is disabled.
        """

        if not config.artifact_materialization_policy.browser_artifact_copy_enabled:
            return None
        return ArtifactMaterializer(
            config.result_dir,
            allowed_root_list=[config.result_dir / config.artifact_materialization_policy.browser_artifact_root],
        )
