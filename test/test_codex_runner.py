"""Codex stage runner tests."""

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from workflow_container_runtime.codex import runner as codex_runner
from workflow_container_runtime.codex.runner import CodexStageError, CodexStageRunner, codex_stage_run


class StageResult(BaseModel):
    """Simple stage result for runner tests."""

    model_config = ConfigDict(extra="forbid")

    message: str
    status: str


def test_browser_stage_uses_configured_mcp_url_without_direct_launcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify browser stages use caller-provided MCP URL and no direct launcher."""
    captured_command: list[str] = []

    def fake_subprocess_run(
        runner: object,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        input: str,
        result_dir: Path,
        stage_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Capture command and write schema-valid Codex output.

        Args:
            runner: Runner instance.
            command: Codex command argv.
            browser_artifact_activity: Whether browser artifacts count as activity.
            input: Prompt text.
            result_dir: Result root.
            stage_dir: Stage artifact directory.

        Returns:
            Successful process result.
        """
        _ = runner
        _ = input
        _ = result_dir
        _ = stage_dir
        assert browser_artifact_activity is True
        captured_command.extend(command)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(StageResult(message="ok", status="success").model_dump_json(), encoding="utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(CodexStageRunner, "_subprocess_run", fake_subprocess_run)

    result = codex_stage_run(
        allow_user_config=True,
        browser_runtime_mcp_url="http://127.0.0.1:8931/mcp",
        model_class=StageResult,
        prompt_text="Run browser task.",
        result_dir=tmp_path,
        stage_dir=tmp_path / "stage",
        stage_name="source_discover",
        workflow_container_name="example-container",
    )

    command_text = "\n".join(captured_command)
    assert result.status == "success"
    assert "mcp_servers.playwright.url='http://127.0.0.1:8931/mcp'" in command_text
    assert "@playwright/mcp" not in command_text
    assert "npx" not in command_text


def test_browser_stage_rejects_node_api_inside_browser_evaluate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject Node.js APIs in browser page JavaScript."""

    def fake_subprocess_run(
        runner: object,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        input: str,
        result_dir: Path,
        stage_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Write valid output and an invalid browser tool event.

        Args:
            runner: Runner instance.
            command: Codex command argv.
            browser_artifact_activity: Whether browser artifacts count as activity.
            input: Prompt text.
            result_dir: Result root.
            stage_dir: Stage artifact directory.

        Returns:
            Successful process result whose event stream violates the browser contract.
        """
        _ = runner
        _ = browser_artifact_activity
        _ = input
        _ = result_dir
        _ = stage_dir
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(StageResult(message="ok", status="success").model_dump_json(), encoding="utf-8")
        event_payload = {
            "item": {
                "arguments": {"function": "() => import('node:fs')"},
                "server": "playwright",
                "tool": "browser_evaluate",
                "type": "mcp_tool_call",
            },
            "type": "item.started",
        }
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=json.dumps(event_payload), stderr="")

    monkeypatch.setattr(CodexStageRunner, "_subprocess_run", fake_subprocess_run)

    with pytest.raises(CodexStageError, match="Node.js"):
        codex_stage_run(
            allow_user_config=True,
            browser_runtime_mcp_url="http://127.0.0.1:8931/mcp",
            model_class=StageResult,
            prompt_text="Run browser task.",
            result_dir=tmp_path,
            stage_dir=tmp_path / "stage",
            stage_name="source_discover",
        )


def test_system_prompt_uses_runtime_project_name_parameter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify runtime system prompts do not hardcode one concrete workflow project."""
    captured_prompt_list: list[str] = []

    def fake_subprocess_run(
        runner: object,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        input: str,
        result_dir: Path,
        stage_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Capture prompt and write schema-valid output.

        Args:
            runner: Runner instance.
            command: Codex command argv.
            browser_artifact_activity: Whether browser artifacts count as activity.
            input: Prompt text.
            result_dir: Result root.
            stage_dir: Stage artifact directory.

        Returns:
            Successful process result.
        """
        _ = runner
        _ = browser_artifact_activity
        _ = result_dir
        _ = stage_dir
        captured_prompt_list.append(input)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(StageResult(message="ok", status="success").model_dump_json(), encoding="utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(CodexStageRunner, "_subprocess_run", fake_subprocess_run)

    codex_stage_run(
        model_class=StageResult,
        prompt_text="Run schema task.",
        result_dir=tmp_path,
        stage_dir=tmp_path / "stage",
        stage_name="schema_stage",
        workflow_container_name="custom-workflow",
    )

    assert "inside custom-workflow" in captured_prompt_list[0]
    assert "concrete-domain-container" not in captured_prompt_list[0]


def test_browser_stage_system_prompt_forbids_browser_search_engine_pages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Require internal Codex search and reserve browser tools for target source pages."""
    captured_prompt_list: list[str] = []

    def fake_subprocess_run(
        runner: object,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        input: str,
        result_dir: Path,
        stage_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Capture browser prompt and write schema-valid output.

        Args:
            runner: Runner instance.
            command: Codex command argv.
            browser_artifact_activity: Whether browser artifacts count as activity.
            input: Prompt text.
            result_dir: Result root.
            stage_dir: Stage artifact directory.

        Returns:
            Successful process result.
        """
        _ = runner
        _ = browser_artifact_activity
        _ = result_dir
        _ = stage_dir
        captured_prompt_list.append(input)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(StageResult(message="ok", status="success").model_dump_json(), encoding="utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(CodexStageRunner, "_subprocess_run", fake_subprocess_run)

    codex_stage_run(
        allow_user_config=True,
        browser_runtime_mcp_url="http://127.0.0.1:8931/mcp",
        model_class=StageResult,
        prompt_text="Run browser task.",
        result_dir=tmp_path,
        stage_dir=tmp_path / "stage",
        stage_name="source_discover",
    )

    assert "Use Codex internal web search for search queries." in captured_prompt_list[0]
    assert "Do not use browser tools or Playwright MCP to open public search-engine result pages." in (
        captured_prompt_list[0]
    )


def test_strict_schema_rejects_extra_output_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Validate Codex output through the supplied Pydantic model."""

    def fake_subprocess_run(
        runner: object,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        input: str,
        result_dir: Path,
        stage_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Write model-invalid Codex output.

        Args:
            runner: Runner instance.
            command: Codex command argv.
            browser_artifact_activity: Whether browser artifacts count as activity.
            input: Prompt text.
            result_dir: Result root.
            stage_dir: Stage artifact directory.

        Returns:
            Successful process result with invalid output.
        """
        _ = runner
        _ = browser_artifact_activity
        _ = input
        _ = result_dir
        _ = stage_dir
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"status":"success","unexpected":true}', encoding="utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(CodexStageRunner, "_subprocess_run", fake_subprocess_run)

    with pytest.raises(CodexStageError, match="returned invalid JSON"):
        codex_stage_run(
            model_class=StageResult,
            prompt_text="Run schema task.",
            result_dir=tmp_path,
            stage_dir=tmp_path / "stage",
            stage_name="schema_stage",
        )


def test_codex_runner_module_has_no_concrete_workflow_container_name() -> None:
    """Keep runtime runner free from concrete workflow-container names."""

    runner_text = Path(codex_runner.__file__).read_text(encoding="utf-8")
    assert "concrete-domain-container" not in runner_text
