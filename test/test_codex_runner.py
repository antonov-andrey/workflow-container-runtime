"""Behavior tests for the low-level Codex execution boundary."""

import inspect
import json
import subprocess
import sys
import typing
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

import workflow_container_runtime.codex as codex
from workflow_container_runtime.codex import runner as codex_runner
from workflow_container_runtime.codex import CodexExecutionError, CodexRunner, CodexRunnerConfig


class ExecutionRetryPolicy(BaseModel):
    """Test-local stand-in for the lifecycle-owned retry policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    attempt_limit: int = Field(ge=1)


class BrowserRuntimeCapability(BaseModel):
    """Test-local browser capability supplied to the runner."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mcp_url: str


class OutputResult(BaseModel):
    """Structured output accepted from Codex."""

    model_config = ConfigDict(extra="forbid")

    message: str
    status: str
    warning_list: list[str] = Field(default_factory=list)


class RecordingPromptRenderer:
    """Record system-template routing without reading production prompt text."""

    def __init__(self) -> None:
        """Initialize an empty template-name log."""

        self.template_name_list: list[str] = []

    def render(self, *, template_name: str, variable_by_name_map: Mapping[str, str]) -> str:
        """Record one routed template and return a fixture system prompt."""

        _ = variable_by_name_map
        self.template_name_list.append(template_name)
        return "fixture system prompt"


class WorkflowRuntimeCapability(BaseModel):
    """Test-local composite runtime capability supplied to the runner."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    browser: BrowserRuntimeCapability | None


def _runner_get() -> CodexRunner:
    """Build one runner with explicit reusable dependencies."""

    return CodexRunner(
        artifact_writer=codex_runner.JsonArtifactWriter(),
        config=CodexRunnerConfig(model="gpt-5.6-terra", model_reasoning_effort="high"),
        prompt_renderer=codex_runner.PromptRenderer(),
        workflow_container_name="example-container",
    )


def _run_kwargs_get(tmp_path: Path, *, attempt_limit: int = 1) -> dict[str, object]:
    """Build the invariant public arguments of one runner call."""

    return {
        "diagnostic_dir": tmp_path / "workflow" / "step" / "diagnostics" / "action",
        "output_model": OutputResult,
        "prompt": "Return one output object.",
        "retry_policy": ExecutionRetryPolicy(attempt_limit=attempt_limit),
        "runtime_capability": WorkflowRuntimeCapability(browser=None),
        "working_directory": tmp_path,
    }


def test_codex_runner_routes_system_prompts_through_runtime_namespace() -> None:
    """Use protected runtime template names for both Codex step modes."""

    prompt_renderer = RecordingPromptRenderer()
    runner = CodexRunner(
        artifact_writer=codex_runner.JsonArtifactWriter(),
        config=CodexRunnerConfig(model="gpt-5.6-terra", model_reasoning_effort="high"),
        prompt_renderer=prompt_renderer,
        workflow_container_name="example-container",
    )

    assert runner._system_prompt_get(have_browser_runtime=False) == "fixture system prompt"
    assert runner._system_prompt_get(have_browser_runtime=True) == "fixture system prompt"
    assert prompt_renderer.template_name_list == [
        "runtime/system/codex_step.md.j2",
        "runtime/system/codex_browser_step.md.j2",
    ]


def test_codex_runner_exposes_only_the_canonical_low_level_api() -> None:
    """Expose the documented runner and error without legacy aliases."""

    run_signature = inspect.signature(CodexRunner.run)

    assert list(run_signature.parameters) == [
        "self",
        "diagnostic_dir",
        "output_model",
        "prompt",
        "retry_policy",
        "runtime_capability",
        "working_directory",
    ]
    assert "CodexRunner" in codex.__all__
    assert "CodexExecutionError" in codex.__all__
    assert not hasattr(codex, "CodexStageRunner")
    assert not hasattr(codex, "CodexStageError")


def test_codex_runner_uses_browser_capability_and_writes_first_attempt_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Route the configured browser MCP capability into one Codex execution."""
    captured_command: list[str] = []

    def fake_subprocess_run(
        runner: object,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        input: str,
        working_directory: Path,
        diagnostic_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Capture the browser command and write valid structured output.

        Args:
            runner: Runner instance.
            command: Codex command argv.
            browser_artifact_activity: Whether browser artifacts count as activity.
            input: Prompt text.
            working_directory: Codex working directory.
            diagnostic_dir: Attempt diagnostic directory.

        Returns:
            Successful process result.
        """

        _ = runner
        _ = input
        _ = working_directory
        assert browser_artifact_activity is True
        assert diagnostic_dir.name == "attempt_001"
        captured_command.extend(command)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(OutputResult(message="ok", status="success").model_dump_json(), encoding="utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(CodexRunner, "_subprocess_run", fake_subprocess_run)
    run_kwargs = _run_kwargs_get(tmp_path)
    run_kwargs["runtime_capability"] = WorkflowRuntimeCapability(
        browser=BrowserRuntimeCapability(mcp_url="http://127.0.0.1:8931/mcp")
    )

    result = _runner_get().run(**run_kwargs)

    command_text = "\n".join(captured_command)
    assert result == OutputResult(message="ok", status="success")
    assert "--model\ngpt-5.6-terra" in command_text
    assert 'model_reasoning_effort="high"' in command_text
    assert "mcp_servers.playwright.url='http://127.0.0.1:8931/mcp'" in command_text
    assert 'mcp_servers.playwright.tools.browser_close.approval_mode="approve"' in command_text
    schema_path = tmp_path / "workflow" / "step" / "diagnostics" / "action" / "attempt_001" / "schema.json"
    schema_payload = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema_payload["required"] == ["message", "status", "warning_list"]


def test_codex_runner_rejects_node_api_in_browser_page_javascript(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject browser tool calls that violate the pure-JavaScript contract."""

    def fake_subprocess_run(
        runner: object,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        input: str,
        working_directory: Path,
        diagnostic_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Write valid output and an invalid browser tool event.

        Args:
            runner: Runner instance.
            command: Codex command argv.
            browser_artifact_activity: Whether browser artifacts count as activity.
            input: Prompt text.
            working_directory: Codex working directory.
            diagnostic_dir: Attempt diagnostic directory.

        Returns:
            Process result whose browser event is invalid.
        """

        _ = runner
        _ = browser_artifact_activity
        _ = input
        _ = working_directory
        _ = diagnostic_dir
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(OutputResult(message="ok", status="success").model_dump_json(), encoding="utf-8")
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

    monkeypatch.setattr(CodexRunner, "_subprocess_run", fake_subprocess_run)
    run_kwargs = _run_kwargs_get(tmp_path)
    run_kwargs["runtime_capability"] = WorkflowRuntimeCapability(
        browser=BrowserRuntimeCapability(mcp_url="http://127.0.0.1:8931/mcp")
    )

    with pytest.raises(CodexExecutionError, match="Node.js"):
        _runner_get().run(**run_kwargs)


def test_codex_runner_retries_invalid_structured_output_in_distinct_attempt_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retry invalid output and retain diagnostics for every execution attempt."""
    diagnostic_dir_list: list[Path] = []

    def fake_subprocess_run(
        runner: object,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        input: str,
        working_directory: Path,
        diagnostic_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Return invalid output once and valid output on the retry.

        Args:
            runner: Runner instance.
            command: Codex command argv.
            browser_artifact_activity: Whether browser artifacts count as activity.
            input: Prompt text.
            working_directory: Codex working directory.
            diagnostic_dir: Attempt diagnostic directory.

        Returns:
            Successful process result.
        """

        _ = runner
        _ = browser_artifact_activity
        _ = input
        _ = working_directory
        diagnostic_dir_list.append(diagnostic_dir)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            (
                '{"message":"ok","status":"success","unexpected":true}'
                if len(diagnostic_dir_list) == 1
                else OutputResult(message="ok", status="success").model_dump_json()
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(CodexRunner, "_subprocess_run", fake_subprocess_run)

    result = _runner_get().run(**_run_kwargs_get(tmp_path, attempt_limit=2))

    assert result == OutputResult(message="ok", status="success")
    assert diagnostic_dir_list == [
        tmp_path / "workflow" / "step" / "diagnostics" / "action" / "attempt_001",
        tmp_path / "workflow" / "step" / "diagnostics" / "action" / "attempt_002",
    ]
    assert (diagnostic_dir_list[0] / "codex_output.json").is_file()
    assert (diagnostic_dir_list[1] / "codex_output.json").is_file()


def test_codex_runner_raises_after_exhausting_low_level_attempts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Surface the low-level execution error after the configured retry limit."""
    call_count = 0

    def fake_subprocess_run(
        runner: object,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        input: str,
        working_directory: Path,
        diagnostic_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Return one failed Codex process.

        Args:
            runner: Runner instance.
            command: Codex command argv.
            browser_artifact_activity: Whether browser artifacts count as activity.
            input: Prompt text.
            working_directory: Codex working directory.
            diagnostic_dir: Attempt diagnostic directory.

        Returns:
            Failed process result.
        """

        nonlocal call_count
        _ = runner
        _ = browser_artifact_activity
        _ = input
        _ = working_directory
        _ = diagnostic_dir
        call_count += 1
        return subprocess.CompletedProcess(args=command, returncode=1, stdout="", stderr="Codex failed")

    monkeypatch.setattr(CodexRunner, "_subprocess_run", fake_subprocess_run)

    with pytest.raises(CodexExecutionError, match="after 2 attempts"):
        _runner_get().run(**_run_kwargs_get(tmp_path, attempt_limit=2))

    assert call_count == 2


def test_codex_subprocess_terminates_after_partial_completed_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Persist partial events and terminate a process group after Codex completed output."""

    class StuckProcess:
        """Process double that reports one completed event but remains alive."""

        pid = 100
        returncode = None

        def communicate(self, input: str | None = None, timeout: int | None = None) -> tuple[str, str]:
            """Expose one partial completed event through `TimeoutExpired`.

            Args:
                input: Prompt text for the first poll.
                timeout: Poll timeout.

            Returns:
                This process never returns normally.

            Raises:
                TimeoutExpired: Always, with the completed event in partial stdout.
            """

            _ = input
            raise subprocess.TimeoutExpired(
                cmd=["codex"],
                timeout=timeout,
                output='{"type":"turn.completed"}\n',
                stderr="",
            )

    diagnostic_dir = tmp_path / "workflow" / "run" / "step" / "example" / "diagnostics" / "attempt_001"
    diagnostic_dir.mkdir(parents=True)
    output_path = diagnostic_dir / "codex_output.json"
    output_path.write_text('{"message":"ok","status":"success"}\n', encoding="utf-8")
    process = StuckProcess()
    monkeypatch.setattr(codex_runner.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(codex_runner, "CODEX_EXEC_INACTIVITY_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(
        CodexRunner,
        "_process_group_terminate",
        lambda self, current_process: ('{"type":"turn.completed"}\n', ""),
    )
    monkeypatch.setattr(CodexRunner, "_process_group_kill", lambda self, current_process: None)

    completed_process = _runner_get()._subprocess_run(
        ["codex", "--output-last-message", str(output_path)],
        browser_artifact_activity=False,
        diagnostic_dir=diagnostic_dir,
        input="prompt",
        working_directory=tmp_path,
    )

    assert completed_process.returncode == 0
    assert (diagnostic_dir / "event.jsonl").read_text(encoding="utf-8") == '{"type":"turn.completed"}\n'


def test_codex_runner_watches_browser_tree_for_step_owner(tmp_path: Path) -> None:
    """Map attempt diagnostics back to the mirrored external step tree."""

    diagnostic_dir = tmp_path / "workflow" / "run" / "step" / "example" / "diagnostics" / "attempt_001" / "action"

    browser_artifact_path = _runner_get()._browser_artifact_path_get(
        diagnostic_dir=diagnostic_dir,
        working_directory=tmp_path,
    )

    assert browser_artifact_path == (tmp_path / ".playwright-mcp" / "current" / "workflow" / "run" / "step" / "example")


def test_codex_subprocess_times_out_on_unchanged_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not treat monitor rewrites of identical partial output as process activity."""

    class RepeatingProcess:
        """Process double that emits the same partial output forever."""

        pid = 101
        returncode = None

        def __init__(self) -> None:
            """Initialize one poll counter."""

            self.poll_count = 0

        def communicate(self, input: str | None = None, timeout: int | None = None) -> tuple[str, str]:
            """Repeat one partial event until the timeout owner kills the process.

            Args:
                input: Prompt text for the first poll.
                timeout: Poll timeout.

            Returns:
                Captured output after process termination.

            Raises:
                TimeoutExpired: While the process remains alive.
                AssertionError: If the runtime fails to enforce its timeout.
            """

            _ = input
            if timeout is None:
                return ('{"type":"item.started"}\n', "")
            self.poll_count += 1
            if self.poll_count > 3:
                raise AssertionError("unchanged partial output postponed inactivity timeout")
            raise subprocess.TimeoutExpired(
                cmd=["codex"],
                timeout=timeout,
                output='{"type":"item.started"}\n',
                stderr="",
            )

    diagnostic_dir = tmp_path / "workflow" / "run" / "step" / "example" / "diagnostics" / "attempt_001"
    diagnostic_dir.mkdir(parents=True)
    process = RepeatingProcess()
    monkeypatch.setattr(codex_runner.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(codex_runner, "CODEX_EXEC_INACTIVITY_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(CodexRunner, "_process_group_kill", lambda self, current_process: None)

    completed_process = _runner_get()._subprocess_run(
        ["codex"],
        browser_artifact_activity=False,
        diagnostic_dir=diagnostic_dir,
        input="prompt",
        working_directory=tmp_path,
    )

    assert completed_process.returncode == 124
    assert process.poll_count == 3


def test_codex_runner_annotations_resolve_in_fresh_process() -> None:
    """Keep capability and retry-policy annotations importable at runtime."""

    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import typing; "
                "from workflow_container_runtime.capability import WorkflowRuntimeCapability; "
                "from workflow_container_runtime.codex import CodexRunner; "
                "typing.get_type_hints(CodexRunner.run)"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    assert typing.get_type_hints(CodexRunner.run)["runtime_capability"].__name__ == "WorkflowRuntimeCapability"
