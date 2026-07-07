"""Verified Codex stage runner tests."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from workflow_container_runtime.artifact import ArtifactMaterializationPolicy
from workflow_container_runtime.codex import CodexStageRunner
from workflow_container_runtime.prompt import PromptRenderer
from workflow_container_runtime.stage import (
    StageVerificationResult,
    VerifiedCodexStageConfig,
    VerifiedCodexStageRunner,
    stage_result_path_get,
    stage_verification_path_get,
)


class StageResult(BaseModel):
    """Simple action-stage result for verified runner tests."""

    model_config = ConfigDict(extra="forbid")

    message: str
    status: str


class StagePromptContext(BaseModel):
    """Simple prompt context for verified runner tests."""

    model_config = ConfigDict(extra="forbid", strict=True)

    brand_name: str


class NonStrictPromptContext(BaseModel):
    """Prompt context that intentionally misses the strict boundary config."""

    brand_name: str


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
        browser_runtime_mcp_url: str = "",
        model_class: type[BaseModel],
        prompt_text: str,
        result_dir: Path,
        stage_dir: Path,
        stage_name: str,
    ) -> BaseModel:
        """Return the next queued result.

        Args:
            browser_runtime_mcp_url: Unused browser runtime URL.
            model_class: Expected output model class.
            prompt_text: Rendered prompt text.
            result_dir: Root result directory.
            stage_dir: Stage artifact directory.
            stage_name: Stage name.

        Returns:
            Next queued model.
        """
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
    (template_dir / "sample_action.md.j2").write_text(
        "\n".join(
            [
                "attempt={{ attempt_index }}",
                "feedback={{ feedback_list }}",
                "context_path={{ prompt_context_path }}",
                "previous_result_path={{ previous_stage_result_path }}",
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "sample_action_verify.md.j2").write_text(
        "\n".join(
            [
                "result_path={{ stage_result_path }}",
                "stage={{ stage_key }}",
                "context_path={{ prompt_context_path }}",
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
        "prompt_context": StagePromptContext(brand_name="Defacto"),
        "result_dir": tmp_path,
        "stage_dir": tmp_path / "stage",
        "stage_key": "sample_action",
    }
    config_map.update(override_map)
    return VerifiedCodexStageConfig(**config_map)


def _mechanical_validate(result: StageResult) -> None:
    """Accept one structurally valid test stage result.

    Args:
        result: Current action-stage result.
    """

    _ = result


def test_verified_stage_config_rejects_unknown_fields_and_exposes_defaults(tmp_path: Path) -> None:
    """Validate verified stage config strictness and visible defaults."""

    config = _verified_stage_config_get(tmp_path)
    assert config.browser_runtime_mcp_url == ""
    assert config.artifact_materialization_policy.artifact_root_list == [Path(".playwright-mcp/current")]
    with pytest.raises(ValidationError):
        VerifiedCodexStageConfig(
            prompt_context=StagePromptContext(brand_name="Defacto"),
            result_dir=tmp_path,
            stage_dir=tmp_path / "stage",
            stage_key="sample_action",
            unexpected_field="bad",
        )


def test_verified_stage_config_requires_model_prompt_context(tmp_path: Path) -> None:
    """Require prompt context to be one typed model instead of raw prompt text."""

    with pytest.raises(ValidationError):
        VerifiedCodexStageConfig(
            prompt_context="domain context",
            result_dir=tmp_path,
            stage_dir=tmp_path / "stage",
            stage_key="sample_action",
        )
    with pytest.raises(ValidationError):
        VerifiedCodexStageConfig(
            prompt_context=NonStrictPromptContext(brand_name="Defacto"),
            result_dir=tmp_path,
            stage_dir=tmp_path / "stage",
            stage_key="sample_action",
        )


def test_verified_stage_runner_writes_prompt_context_artifact(tmp_path: Path) -> None:
    """Persist typed prompt context and pass only its path into prompts."""

    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "sample_action.md.j2").write_text(
        "context_path={{ prompt_context_path }}\ncontext={{ prompt_context | default('missing') }}",
        encoding="utf-8",
    )
    (template_dir / "sample_action_verify.md.j2").write_text(
        "context_path={{ prompt_context_path }}\nresult_path={{ stage_result_path }}",
        encoding="utf-8",
    )
    fake_codex_runner = FakeCodexStageRunner(
        [
            StageResult(message="first", status="success"),
            StageVerificationResult(status="success"),
        ]
    )

    VerifiedCodexStageRunner(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=template_dir),
    ).run(
        config=_verified_stage_config_get(
            tmp_path,
            prompt_context=StagePromptContext(brand_name="Defacto"),
        ),
        mechanical_validate=_mechanical_validate,
        model_class=StageResult,
    )

    assert json.loads((tmp_path / "stage/prompt_context.json").read_text(encoding="utf-8")) == {"brand_name": "Defacto"}
    assert "context_path=stage/prompt_context.json" in fake_codex_runner.prompt_text_list[0]
    assert "context=missing" in fake_codex_runner.prompt_text_list[0]
    assert "context_path=stage/prompt_context.json" in fake_codex_runner.prompt_text_list[1]


def test_verified_stage_result_write_helper_owns_standard_artifacts(tmp_path: Path) -> None:
    """Write deterministic verified stage artifacts through the runtime helper."""

    from workflow_container_runtime.stage import verified_stage_artifact_write

    result = StageResult(message="seed", status="success")
    config = _verified_stage_config_get(tmp_path)

    verified_stage_artifact_write(config=config, result=result)

    assert json.loads((tmp_path / "stage/prompt_context.json").read_text(encoding="utf-8")) == {"brand_name": "Defacto"}
    assert (tmp_path / "stage/result.json").is_file()
    assert (tmp_path / "stage/verification.json").is_file()
    assert '"message": "seed"' in (tmp_path / "stage/result.json").read_text(encoding="utf-8")
    assert '"status": "success"' in (tmp_path / "stage/verification.json").read_text(encoding="utf-8")


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
                artifact_root_list=[],
            ),
        ),
        mechanical_validate=_mechanical_validate,
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
        mechanical_validate=_mechanical_validate,
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
        config=_verified_stage_config_get(tmp_path),
        mechanical_validate=_mechanical_validate,
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
    assert "previous_result_path=" in fake_codex_runner.prompt_text_list[0]
    assert "previous_result_path=stage/result.json" not in fake_codex_runner.prompt_text_list[0]
    assert "previous_result_path=stage/result.json" in fake_codex_runner.prompt_text_list[2]


def test_verified_stage_runner_feeds_mechanical_errors_to_action(tmp_path: Path) -> None:
    """Convert mechanical validator failures into verifier feedback."""

    fake_codex_runner = FakeCodexStageRunner(
        [
            StageResult(message="first", status="success"),
            StageResult(message="second", status="success"),
            StageVerificationResult(status="success"),
        ]
    )
    runner = VerifiedCodexStageRunner(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
    )
    call_count = 0

    def mechanical_validate(result: StageResult) -> None:
        """Raise one mechanical error for the first action result only.

        Args:
            result: Current action-stage result.
        """

        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError(f"bad result: {result.message}")

    result = runner.run(
        config=_verified_stage_config_get(tmp_path),
        mechanical_validate=mechanical_validate,
        model_class=StageResult,
    )

    assert result.message == "second"
    assert "feedback=['bad result: first']" in fake_codex_runner.prompt_text_list[1]
    assert fake_codex_runner.stage_name_list == [
        "sample_action",
        "sample_action",
        "sample_action_verify",
    ]
    assert "previous_result_path=stage/result.json" in fake_codex_runner.prompt_text_list[1]
    assert call_count == 2


def test_verified_stage_runner_standard_stage_paths(tmp_path: Path) -> None:
    """Expose standard public stage paths through runtime owner."""

    stage_dir = tmp_path / "stage"

    assert stage_result_path_get(stage_dir) == stage_dir / "result.json"
    assert stage_verification_path_get(stage_dir) == stage_dir / "verification.json"
