"""Verified Codex stage runner tests."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from workflow_container_runtime.artifact import ArtifactMaterializationPolicy
from workflow_container_runtime.codex import CodexStageRunner
from workflow_container_runtime.prompt import PromptRenderer
from workflow_container_runtime.stage import StageVerificationResult, VerifiedCodexStageConfig, VerifiedCodexStageRunner


class StageResult(BaseModel):
    """Simple action-stage result for verified runner tests."""

    model_config = ConfigDict(extra="forbid")

    message: str
    status: str


class FakeCodexStageRunner(CodexStageRunner):
    """Return queued models and keep prompt diagnostics for assertions."""

    def __init__(self, result_list: list[BaseModel]) -> None:
        """Store queued stage results.

        Args:
            result_list: Models returned by subsequent `run` calls.
        """

        self.prompt_text_list: list[str] = []
        self.stage_name_list: list[str] = []
        self._result_list = result_list

    def run(
        self,
        *,
        allow_user_config: bool = False,
        browser_runtime_mcp_url: str = "",
        model_class: type[BaseModel],
        prompt_text: str,
        result_dir: Path,
        stage_dir: Path,
        stage_name: str,
    ) -> BaseModel:
        """Return the next queued result.

        Args:
            allow_user_config: Unused browser access flag.
            browser_runtime_mcp_url: Unused browser runtime URL.
            model_class: Expected output model class.
            prompt_text: Rendered prompt text.
            result_dir: Root result directory.
            stage_dir: Stage artifact directory.
            stage_name: Stage name.

        Returns:
            Next queued model.
        """

        _ = allow_user_config
        _ = browser_runtime_mcp_url
        _ = model_class
        _ = result_dir
        _ = stage_dir
        self.prompt_text_list.append(prompt_text)
        self.stage_name_list.append(stage_name)
        return self._result_list.pop(0)


def _template_dir_prepare(tmp_path: Path) -> Path:
    """Create action and verification templates for verified runner tests.

    Args:
        tmp_path: Test temporary directory.

    Returns:
        Template directory.
    """

    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "action.md.j2").write_text(
        "\n".join(
            [
                "attempt={{ attempt_index }}",
                "draft={{ draft_result_json }}",
                "previous={{ previous_result_json }}",
                "feedback={{ feedback_list }}",
                "context={{ prompt_context }}",
                "shared={{ shared_instruction }}",
                "stage_instruction={{ stage_instruction_text }}",
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "verify.md.j2").write_text(
        "\n".join(
            [
                "draft={{ draft_verification_json }}",
                "result={{ stage_result_json }}",
                "result_path={{ stage_result_path }}",
                "state_path={{ stage_state_path }}",
                "stage={{ stage_key }}",
                "context={{ prompt_context }}",
            ]
        ),
        encoding="utf-8",
    )
    return template_dir


def _verified_stage_config_get(tmp_path: Path, **override_map: object) -> VerifiedCodexStageConfig:
    """Return a verified stage config for tests.

    Args:
        tmp_path: Test temporary directory.
        **override_map: Field overrides for the config model.

    Returns:
        Verified stage config.
    """

    config_map = {
        "action_template_name": "action.md.j2",
        "prompt_context": "domain context",
        "result_dir": tmp_path,
        "stage_dir": tmp_path / "stage",
        "stage_key": "sample_action",
        "verification_template_name": "verify.md.j2",
    }
    config_map.update(override_map)
    return VerifiedCodexStageConfig(**config_map)


def test_verified_stage_config_rejects_unknown_fields_and_exposes_defaults(tmp_path: Path) -> None:
    """Validate verified stage config strictness and visible defaults."""

    config = _verified_stage_config_get(tmp_path)

    assert config.allow_user_config is False
    assert config.browser_runtime_mcp_url == ""
    assert config.shared_instruction == ""
    assert config.stage_instruction_text == ""
    assert config.artifact_materialization_policy.browser_artifact_copy_enabled is True
    assert config.artifact_materialization_policy.browser_artifact_root == Path(".playwright-mcp/current")
    with pytest.raises(ValidationError):
        VerifiedCodexStageConfig(
            action_template_name="action.md.j2",
            prompt_context="domain context",
            result_dir=tmp_path,
            stage_dir=tmp_path / "stage",
            stage_key="sample_action",
            unexpected_field="bad",
            verification_template_name="verify.md.j2",
        )


def test_verified_stage_runner_can_disable_default_browser_artifact_policy(tmp_path: Path) -> None:
    """Disable runtime browser artifact materialization through policy config."""

    browser_artifact_path = tmp_path / ".playwright-mcp/current/stage/evidence.txt"
    browser_artifact_path.parent.mkdir(parents=True)
    browser_artifact_path.write_text("browser evidence", encoding="utf-8")
    fake_codex_runner = FakeCodexStageRunner(
        [
            StageResult(message="first", status="success"),
            StageVerificationResult(status="success"),
        ]
    )
    runner = VerifiedCodexStageRunner(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
    )

    runner.run(
        config=_verified_stage_config_get(
            tmp_path,
            artifact_materialization_policy=ArtifactMaterializationPolicy(
                browser_artifact_copy_enabled=False,
            ),
        ),
        draft_result=StageResult(message="draft", status="skipped"),
        model_class=StageResult,
    )

    assert not (tmp_path / "stage/evidence.txt").exists()


def test_verified_stage_runner_uses_default_browser_artifact_policy(tmp_path: Path) -> None:
    """Copy browser artifacts through the default runtime policy."""

    browser_artifact_path = tmp_path / ".playwright-mcp/current/stage/evidence.txt"
    browser_artifact_path.parent.mkdir(parents=True)
    browser_artifact_path.write_text("browser evidence", encoding="utf-8")
    fake_codex_runner = FakeCodexStageRunner(
        [
            StageResult(message="first", status="success"),
            StageVerificationResult(status="success"),
        ]
    )
    runner = VerifiedCodexStageRunner(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
    )

    runner.run(
        config=_verified_stage_config_get(tmp_path),
        draft_result=StageResult(message="draft", status="skipped"),
        model_class=StageResult,
    )

    assert (tmp_path / "stage/evidence.txt").read_text(encoding="utf-8") == "browser evidence"


def test_verified_stage_runner_retries_with_feedback(tmp_path: Path) -> None:
    """Retry action stage with verifier feedback and write standard artifacts."""

    fake_codex_runner = FakeCodexStageRunner(
        [
            StageResult(message="first", status="failed"),
            StageVerificationResult(feedback_list=["fix result"], status="failed"),
            StageResult(message="second", status="success"),
            StageVerificationResult(status="success"),
        ]
    )
    runner = VerifiedCodexStageRunner(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
    )

    result = runner.run(
        config=_verified_stage_config_get(
            tmp_path,
            shared_instruction="shared instruction",
            stage_instruction_text="- stage instruction",
        ),
        draft_result=StageResult(message="draft", status="skipped"),
        model_class=StageResult,
    )

    assert result.message == "second"
    assert (tmp_path / "stage" / "result.json").is_file()
    assert (tmp_path / "stage" / "verification.json").is_file()
    assert fake_codex_runner.stage_name_list == [
        "sample_action",
        "sample_action_verify",
        "sample_action",
        "sample_action_verify",
    ]
    assert "feedback=['fix result']" in fake_codex_runner.prompt_text_list[2]
    assert "draft={" in "\n".join(fake_codex_runner.prompt_text_list)


def test_verified_stage_runner_feeds_mechanical_errors_to_action(tmp_path: Path) -> None:
    """Convert mechanical validator failures into verifier feedback."""

    fake_codex_runner = FakeCodexStageRunner(
        [
            StageResult(message="first", status="success"),
            StageVerificationResult(status="success"),
            StageResult(message="second", status="success"),
            StageVerificationResult(status="success"),
        ]
    )
    runner = VerifiedCodexStageRunner(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
    )
    call_count = 0

    def mechanical_error_list_get(result: StageResult) -> list[str]:
        """Return one mechanical error for the first action result only.

        Args:
            result: Current action-stage result.

        Returns:
            Mechanical error list.
        """

        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [f"bad result: {result.message}"]
        return []

    result = runner.run(
        config=_verified_stage_config_get(tmp_path),
        draft_result=StageResult(message="draft", status="skipped"),
        mechanical_error_list_get=mechanical_error_list_get,
        model_class=StageResult,
    )

    assert result.message == "second"
    assert "feedback=['bad result: first']" in fake_codex_runner.prompt_text_list[2]
    assert call_count == 2


def test_verified_stage_runner_standard_stage_paths(tmp_path: Path) -> None:
    """Expose standard public stage paths through runtime owner."""

    runner = VerifiedCodexStageRunner()
    stage_dir = tmp_path / "stage"

    assert runner.stage_result_path(stage_dir) == stage_dir / "result.json"
    assert runner.stage_state_path(stage_dir) == stage_dir / "state.json"
    assert runner.stage_verification_path(stage_dir) == stage_dir / "verification.json"
