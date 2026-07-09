"""Workflow step stage runtime tests."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from workflow_container_runtime.artifact import ArtifactMaterializationPolicy
from workflow_container_runtime.codex import CodexStageRunner
from workflow_container_runtime.prompt import PromptRenderer
from workflow_container_runtime.stage import (
    BrowserActionResult,
    StageVerificationResult,
    WorkflowStepBase,
    WorkflowStepCodexBase,
    stage_input_path_get,
    stage_result_path_get,
    stage_state_path_get,
    stage_verification_path_get,
)


class ExampleInput(BaseModel):
    """Typed public stage input for test workflow steps."""

    model_config = ConfigDict(extra="forbid", strict=True)

    brand_name: str


class ExampleActionOutput(BaseModel):
    """Codex action output for test workflow steps."""

    model_config = ConfigDict(extra="forbid", strict=True)

    action_value: str


class ExampleResult(BaseModel):
    """Typed public stage result for test workflow steps."""

    model_config = ConfigDict(extra="forbid", strict=True)

    value: str


class FakeCodexStageRunner(CodexStageRunner):
    """Return queued models and capture prompts for assertions."""

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
        """Return the next queued model.

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


class ExampleWorkflowStep(WorkflowStepBase[ExampleInput, ExampleResult]):
    """Deterministic workflow step for public file lifecycle tests."""

    def input_build(self) -> ExampleInput:
        """Build typed public stage input.

        Returns:
            Public stage input.
        """

        return ExampleInput(brand_name="Defacto")

    def result_build(self, stage_input: ExampleInput) -> ExampleResult:
        """Build public stage result.

        Args:
            stage_input: Typed public stage input.

        Returns:
            Public stage result.
        """

        return ExampleResult(value=stage_input.brand_name)


class ExampleCodexStep(WorkflowStepCodexBase[ExampleInput, ExampleActionOutput, ExampleResult]):
    """Codex-backed workflow step for runtime lifecycle tests."""

    stage_key = "sample_action"

    def action_output_model_get(self) -> type[ExampleActionOutput]:
        """Return Codex action output model.

        Returns:
            Typed Codex action output model.
        """

        return ExampleActionOutput

    def input_build(self) -> ExampleInput:
        """Build typed public stage input.

        Returns:
            Public stage input.
        """

        return ExampleInput(brand_name="Defacto")

    def result_build(self, stage_input: ExampleInput, action_output: ExampleActionOutput) -> ExampleResult:
        """Build public result from input and action output.

        Args:
            stage_input: Typed public stage input.
            action_output: Typed Codex action output.

        Returns:
            Public stage result.
        """

        _ = stage_input
        return ExampleResult(value=action_output.action_value)


def _template_dir_prepare(tmp_path: Path) -> Path:
    """Create action and verification templates for workflow step tests.

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
                "input_path={{ input_path }}",
                "previous_stage_result_path={{ previous_stage_result_path }}",
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "sample_action_verify.md.j2").write_text(
        "\n".join(
            [
                "stage_result_path={{ stage_result_path }}",
                "stage={{ stage_key }}",
                "input_path={{ input_path }}",
            ]
        ),
        encoding="utf-8",
    )
    return template_dir


def test_workflow_step_base_writes_standard_stage_files(tmp_path: Path) -> None:
    """Write deterministic input, result, and verification artifacts."""

    stage = ExampleWorkflowStep(result_dir=tmp_path)

    result = stage.run()

    assert result == ExampleResult(value="Defacto")
    assert json.loads((tmp_path / "stage/input.json").read_text(encoding="utf-8")) == {"brand_name": "Defacto"}
    assert json.loads((tmp_path / "stage/result.json").read_text(encoding="utf-8")) == {"value": "Defacto"}
    assert json.loads((tmp_path / "stage/verification.json").read_text(encoding="utf-8")) == {
        "feedback_list": [],
        "status": "success",
    }


def test_workflow_step_base_serializes_validated_result_state(tmp_path: Path) -> None:
    """Serialize the validated result state after one mutating validator runs."""

    class MutatingValidationStep(ExampleWorkflowStep):
        """Workflow step with one mutating validator."""

        def result_validate(self, result: ExampleResult) -> None:
            """Mutate the result before final serialization.

            Args:
                result: Current public stage result.
            """

            result.value = result.value.upper()

    stage = MutatingValidationStep(result_dir=tmp_path)

    result = stage.run()

    assert result == ExampleResult(value="DEFACTO")
    assert json.loads((tmp_path / "stage/result.json").read_text(encoding="utf-8")) == {"value": "DEFACTO"}


def test_workflow_step_codex_base_writes_input_result_and_verification(tmp_path: Path) -> None:
    """Write standard artifacts and route prompts by stage file path."""

    fake_codex_runner = FakeCodexStageRunner(
        [
            ExampleActionOutput(action_value="ok"),
            StageVerificationResult(status="success"),
        ]
    )
    stage = ExampleCodexStep(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
        result_dir=tmp_path,
    )

    result = stage.run()

    assert result == ExampleResult(value="ok")
    assert json.loads((tmp_path / "stage/input.json").read_text(encoding="utf-8")) == {"brand_name": "Defacto"}
    assert json.loads((tmp_path / "stage/result.json").read_text(encoding="utf-8")) == {"value": "ok"}
    assert json.loads((tmp_path / "stage/verification.json").read_text(encoding="utf-8")) == {
        "feedback_list": [],
        "status": "success",
    }
    assert "input_path=stage/input.json" in fake_codex_runner.prompt_text_list[0]
    assert "stage_result_path=stage/result.json" in fake_codex_runner.prompt_text_list[1]


def test_workflow_step_codex_base_serializes_validated_result_state(tmp_path: Path) -> None:
    """Serialize the validated result state after one mutating validator runs."""

    fake_codex_runner = FakeCodexStageRunner(
        [
            ExampleActionOutput(action_value="ok"),
            StageVerificationResult(status="success"),
        ]
    )

    class MutatingValidationStep(ExampleCodexStep):
        """Codex-backed workflow step with one mutating validator."""

        def result_validate(self, result: ExampleResult) -> None:
            """Mutate the result before final serialization.

            Args:
                result: Current public stage result.
            """

            result.value = result.value.upper()

    stage = MutatingValidationStep(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
        result_dir=tmp_path,
    )

    result = stage.run()

    assert result == ExampleResult(value="OK")
    assert json.loads((tmp_path / "stage/result.json").read_text(encoding="utf-8")) == {"value": "OK"}


def test_workflow_step_codex_base_can_disable_default_browser_artifact_policy(tmp_path: Path) -> None:
    """Disable runtime browser artifact materialization through policy config."""

    browser_artifact_path = tmp_path / ".playwright-mcp/current/stage/evidence.txt"
    browser_artifact_path.parent.mkdir(parents=True)
    browser_artifact_path.write_text("browser evidence", encoding="utf-8")
    fake_codex_runner = FakeCodexStageRunner(
        [
            ExampleActionOutput(action_value="ok"),
            StageVerificationResult(status="success"),
        ]
    )
    stage = ExampleCodexStep(
        artifact_materialization_policy=ArtifactMaterializationPolicy(artifact_root_list=[]),
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
        result_dir=tmp_path,
    )

    stage.run()

    assert not (tmp_path / "stage/evidence.txt").exists()


def test_workflow_step_codex_base_retries_with_feedback(tmp_path: Path) -> None:
    """Retry action stage with verifier feedback and previous result routing."""

    fake_codex_runner = FakeCodexStageRunner(
        [
            ExampleActionOutput(action_value="first"),
            StageVerificationResult(feedback_list=["fix result"], status="failed"),
            ExampleActionOutput(action_value="second"),
            StageVerificationResult(status="success"),
        ]
    )
    stage = ExampleCodexStep(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
        result_dir=tmp_path,
    )

    result = stage.run()

    assert result == ExampleResult(value="second")
    assert fake_codex_runner.stage_name_list == [
        "sample_action",
        "sample_action_verify",
        "sample_action",
        "sample_action_verify",
    ]
    assert "feedback=['fix result']" in fake_codex_runner.prompt_text_list[2]
    assert "previous_stage_result_path=" in fake_codex_runner.prompt_text_list[0]
    assert "previous_stage_result_path=stage/result.json" not in fake_codex_runner.prompt_text_list[0]
    assert "previous_stage_result_path=stage/result.json" in fake_codex_runner.prompt_text_list[2]


def test_workflow_step_codex_base_feeds_mechanical_errors_to_action(tmp_path: Path) -> None:
    """Convert mechanical validator failures into retry feedback."""

    fake_codex_runner = FakeCodexStageRunner(
        [
            ExampleActionOutput(action_value="first"),
            ExampleActionOutput(action_value="second"),
            StageVerificationResult(status="success"),
        ]
    )

    class MechanicalFailureStep(ExampleCodexStep):
        """Workflow step that fails deterministic validation once."""

        def __init__(self, **kwargs: object) -> None:
            """Store validation call state.

            Args:
                **kwargs: Base workflow step constructor arguments.
            """

            super().__init__(**kwargs)
            self.validation_call_count = 0

        def result_validate(self, result: ExampleResult) -> None:
            """Raise one deterministic error for the first result only.

            Args:
                result: Current public stage result.

            Raises:
                RuntimeError: If the first result must be retried.
            """

            self.validation_call_count += 1
            if self.validation_call_count == 1:
                raise RuntimeError(f"bad result: {result.value}")

    stage = MechanicalFailureStep(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
        result_dir=tmp_path,
    )

    result = stage.run()

    assert result == ExampleResult(value="second")
    assert "feedback=['bad result: first']" in fake_codex_runner.prompt_text_list[1]
    assert fake_codex_runner.stage_name_list == [
        "sample_action",
        "sample_action",
        "sample_action_verify",
    ]
    assert "previous_stage_result_path=stage/result.json" in fake_codex_runner.prompt_text_list[1]
    assert stage.validation_call_count == 2


def test_workflow_step_codex_base_reraises_non_runtime_mechanical_error(tmp_path: Path) -> None:
    """Reraise non-`RuntimeError` validation failures without retry feedback."""

    fake_codex_runner = FakeCodexStageRunner([ExampleActionOutput(action_value="first")])

    class NonRetryableFailureStep(ExampleCodexStep):
        """Workflow step with one non-retryable validator failure."""

        def result_validate(self, result: ExampleResult) -> None:
            """Raise one non-retryable validator failure.

            Args:
                result: Current public stage result.

            Raises:
                ValueError: Always, to prove non-retryable failures propagate.
            """

            raise ValueError(f"bad result: {result.value}")

    stage = NonRetryableFailureStep(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
        result_dir=tmp_path,
    )

    with pytest.raises(ValueError, match="bad result: first"):
        stage.run()

    assert fake_codex_runner.stage_name_list == ["sample_action"]
    assert not (tmp_path / "stage/result.json").exists()
    assert not (tmp_path / "stage/verification.json").exists()


def test_workflow_step_codex_base_uses_default_browser_artifact_policy(tmp_path: Path) -> None:
    """Copy browser artifacts through the default runtime policy."""

    browser_artifact_path = tmp_path / ".playwright-mcp/current/stage/evidence.txt"
    browser_artifact_path.parent.mkdir(parents=True)
    browser_artifact_path.write_text("browser evidence", encoding="utf-8")
    fake_codex_runner = FakeCodexStageRunner(
        [
            ExampleActionOutput(action_value="ok"),
            StageVerificationResult(status="success"),
        ]
    )
    stage = ExampleCodexStep(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
        result_dir=tmp_path,
    )

    stage.run()

    assert (tmp_path / "stage/evidence.txt").read_text(encoding="utf-8") == "browser evidence"


def test_workflow_step_standard_stage_paths(tmp_path: Path) -> None:
    """Expose standard public and private stage paths."""

    stage_dir = tmp_path / "stage"

    assert stage_input_path_get(stage_dir) == stage_dir / "input.json"
    assert stage_result_path_get(stage_dir) == stage_dir / "result.json"
    assert stage_state_path_get(stage_dir) == stage_dir / "state.json"
    assert stage_verification_path_get(stage_dir) == stage_dir / "verification.json"


def test_workflow_step_rejects_stage_dir_outside_result_dir(tmp_path: Path) -> None:
    """Fail construction when the stage directory is outside the result root."""

    with pytest.raises(ValueError, match="stage_dir must be inside result_dir"):
        ExampleWorkflowStep(result_dir=tmp_path, stage_dir=tmp_path.parent / "external-stage")


def test_workflow_step_verified_result_can_reuse_browser_base_model(tmp_path: Path) -> None:
    """Allow browser action output models in Codex-backed stage steps."""

    fake_codex_runner = FakeCodexStageRunner(
        [
            BrowserActionResult(),
            StageVerificationResult(status="success"),
        ]
    )

    class BrowserResultStep(WorkflowStepCodexBase[ExampleInput, BrowserActionResult, ExampleResult]):
        """Workflow step that uses browser action output."""

        stage_key = "sample_action"

        def action_output_model_get(self) -> type[BrowserActionResult]:
            """Return browser action output model.

            Returns:
                Browser action output model.
            """

            return BrowserActionResult

        def input_build(self) -> ExampleInput:
            """Build typed public stage input.

            Returns:
                Public stage input.
            """

            return ExampleInput(brand_name="Defacto")

        def result_build(self, stage_input: ExampleInput, action_output: BrowserActionResult) -> ExampleResult:
            """Build public result from input and browser action output.

            Args:
                stage_input: Typed public stage input.
                action_output: Typed browser action output.

            Returns:
                Public stage result.
            """

            _ = action_output
            return ExampleResult(value=stage_input.brand_name)

    stage = BrowserResultStep(
        codex_stage_run_callable=fake_codex_runner.run,
        prompt_renderer=PromptRenderer(template_dir=_template_dir_prepare(tmp_path)),
        result_dir=tmp_path,
    )

    assert stage.run() == ExampleResult(value="Defacto")
