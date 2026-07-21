"""Low-level Codex subprocess execution with typed structured output."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from workflow_container_runtime.artifact import JsonArtifactWriter
from workflow_container_runtime.capability import WorkflowRuntimeCapability
from workflow_container_runtime.codex.config import CodexRunnerConfig
from workflow_container_runtime.prompt import PromptRenderer
from workflow_container_runtime.retry import CodexExecutionRetryPolicy

CODEX_BROWSER_STEP_SYSTEM_PROMPT_TEMPLATE_NAME = "runtime/system/codex_browser_step.md.j2"
CODEX_EXEC_INACTIVITY_TIMEOUT_SECONDS = 900
CODEX_EXEC_POLL_SECONDS = 5
CODEX_STEP_SYSTEM_PROMPT_TEMPLATE_NAME = "runtime/system/codex_step.md.j2"
PLAYWRIGHT_MCP_APPROVED_TOOL_LIST = [
    "browser_click",
    "browser_close",
    "browser_evaluate",
    "browser_navigate",
    "browser_resize",
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_tabs",
]
BROWSER_JAVASCRIPT_FORBIDDEN_PATTERN_LIST = [
    re.compile(r"\brequire\s*\("),
    re.compile(r"\bimport\s*\("),
    re.compile(r"\b(?:fs|path|process)\s*\."),
    re.compile(r"\bBuffer\s*(?:[\.\(\[]|$)"),
    re.compile(r"[\"']node:"),
]
PLAYWRIGHT_MCP_CODE_TOOL_SET = {"browser_evaluate", "browser_run_code_unsafe"}
PLAYWRIGHT_MCP_FORBIDDEN_TOOL_SET = {"browser_run_code_unsafe"}
OutputT = TypeVar("OutputT", bound=BaseModel)


class CodexExecutionError(RuntimeError):
    """Raised when one low-level Codex execution cannot return valid output."""


class CodexRunner:
    """Run Codex through its CLI and validate one structured response."""

    def __init__(
        self,
        *,
        artifact_writer: JsonArtifactWriter,
        prompt_renderer: PromptRenderer,
        workflow_container_name: str,
    ) -> None:
        """Initialize reusable Codex execution dependencies.

        Args:
            artifact_writer: JSON artifact writer used for schema diagnostics.
            prompt_renderer: Runtime system-prompt renderer.
            workflow_container_name: Human-readable workflow container name for Codex system prompts.
        """

        self._artifact_writer = artifact_writer
        self._prompt_renderer = prompt_renderer
        self._workflow_container_name = workflow_container_name

    def run(
        self,
        *,
        config: CodexRunnerConfig,
        diagnostic_dir: Path,
        output_model: type[OutputT],
        prompt: str,
        retry_policy: CodexExecutionRetryPolicy,
        runtime_capability: WorkflowRuntimeCapability,
        working_directory: Path,
    ) -> OutputT:
        """Run one low-level Codex action with bounded transport retries.

        Args:
            config: Explicit model and reasoning selection for this call.
            diagnostic_dir: Deterministic base directory for this action's diagnostics.
            output_model: Pydantic model class for the structured response.
            prompt: Action prompt text.
            retry_policy: Low-level Codex retry limit.
            runtime_capability: Explicit capabilities granted to this action.
            working_directory: Root directory used as Codex working directory.

        Returns:
            Validated Codex response.

        Raises:
            CodexExecutionError: If no attempt returns valid structured output.
        """
        diagnostic_dir = diagnostic_dir.resolve()
        working_directory = working_directory.resolve()
        browser_runtime_mcp_url = "" if runtime_capability.browser is None else runtime_capability.browser.mcp_url
        execution_error: CodexExecutionError | None = None
        for attempt_index in range(1, retry_policy.attempt_limit + 1):
            try:
                return self._attempt_run(
                    browser_runtime_mcp_url=browser_runtime_mcp_url,
                    config=config,
                    diagnostic_dir=diagnostic_dir / f"attempt_{attempt_index:03d}",
                    output_model=output_model,
                    prompt=prompt,
                    working_directory=working_directory,
                )
            except CodexExecutionError as exc:
                execution_error = exc
        raise CodexExecutionError(
            f"Codex execution failed after {retry_policy.attempt_limit} attempts: {execution_error}"
        ) from execution_error

    def _attempt_run(
        self,
        *,
        browser_runtime_mcp_url: str,
        config: CodexRunnerConfig,
        diagnostic_dir: Path,
        output_model: type[OutputT],
        prompt: str,
        working_directory: Path,
    ) -> OutputT:
        """Run one Codex subprocess attempt and preserve its diagnostics.

        Args:
            browser_runtime_mcp_url: Configured browser runtime endpoint, when available.
            config: Explicit model and reasoning selection for this call.
            diagnostic_dir: Directory that owns this exact attempt's diagnostics.
            output_model: Pydantic model class for the structured response.
            prompt: Action prompt text.
            working_directory: Root directory used as Codex working directory.

        Returns:
            Validated structured response.

        Raises:
            CodexExecutionError: If Codex exits unsuccessfully or returns invalid output.
        """
        have_browser_runtime = browser_runtime_mcp_url != ""
        diagnostic_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = diagnostic_dir / "prompt.md"
        output_path = diagnostic_dir / "codex_output.json"
        schema_path = diagnostic_dir / "schema.json"
        stderr_path = diagnostic_dir / "stderr.txt"
        event_path = diagnostic_dir / "event.jsonl"
        self._final_diagnostic_remove(
            event_path=event_path,
            output_path=output_path,
            stderr_path=stderr_path,
        )
        event_path.touch()
        self._artifact_writer.schema_write(schema_path, output_model)
        system_prompt = self._system_prompt_get(have_browser_runtime=have_browser_runtime)
        prompt_path.write_text(f"{system_prompt}\n\n{prompt}\n", encoding="utf-8")
        command = self._command_list_get(
            browser_runtime_mcp_url=browser_runtime_mcp_url,
            config=config,
            output_path=output_path,
            working_directory=working_directory,
            schema_path=schema_path,
        )
        process = self._subprocess_run(
            command,
            browser_artifact_activity=have_browser_runtime,
            input=prompt_path.read_text(encoding="utf-8"),
            diagnostic_dir=diagnostic_dir,
            working_directory=working_directory,
        )
        stderr_path.write_text(process.stderr, encoding="utf-8")
        if have_browser_runtime:
            self._browser_tool_contract_validate(event_path=event_path)
        if process.returncode != 0:
            raise CodexExecutionError(f"Codex execution failed with exit code {process.returncode}.")
        return self._output_model_get(output_model=output_model, output_path=output_path)

    def _system_prompt_get(self, *, have_browser_runtime: bool) -> str:
        """Render the Codex system prompt for one step mode.

        Args:
            have_browser_runtime: Whether this step has a browser runtime MCP URL.

        Returns:
            Rendered system prompt text.
        """

        template_name = (
            CODEX_BROWSER_STEP_SYSTEM_PROMPT_TEMPLATE_NAME
            if have_browser_runtime
            else CODEX_STEP_SYSTEM_PROMPT_TEMPLATE_NAME
        )
        return self._prompt_renderer.render(
            template_name=template_name,
            variable_by_name_map={
                "workflow_container_name": self._workflow_container_name,
            },
        )

    def _browser_tool_argument_text_list_get(self, value: object) -> list[str]:
        """Return string leaves from one browser tool argument payload.

        Args:
            value: JSON-decoded tool argument value.

        Returns:
            String leaves inside the argument payload.
        """

        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            text_list: list[str] = []
            for item in value:
                text_list.extend(self._browser_tool_argument_text_list_get(item))
            return text_list
        if isinstance(value, dict):
            text_list = []
            for item in value.values():
                text_list.extend(self._browser_tool_argument_text_list_get(item))
            return text_list
        return []

    def _browser_artifact_path_get(self, *, diagnostic_dir: Path, working_directory: Path) -> Path | None:
        """Return the mirrored external artifact tree for the current step.

        Args:
            diagnostic_dir: Current Codex attempt diagnostic directory.
            working_directory: Root result directory mirrored by the external artifact root.

        Returns:
            Mirrored current-step path, or `None` outside the standard diagnostics layout.
        """

        diagnostics_dir = next(
            (path for path in (diagnostic_dir, *diagnostic_dir.parents) if path.name == "diagnostics"),
            None,
        )
        if diagnostics_dir is None:
            return None
        try:
            step_relative_path = diagnostics_dir.parent.relative_to(working_directory)
        except ValueError:
            return None
        return working_directory / ".playwright-mcp" / "current" / step_relative_path

    def _browser_tool_contract_validate(self, *, event_path: Path) -> None:
        """Validate browser tool usage emitted by one Codex browser step.

        Args:
            event_path: Codex JSONL event stream path.

        Raises:
            CodexExecutionError: If one browser tool call violates the page-JavaScript contract.
        """

        error_list: list[str] = []
        for line_index, line in enumerate(event_path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") != "mcp_tool_call" or item.get("server") != "playwright":
                continue
            tool_name = item.get("tool")
            if not isinstance(tool_name, str):
                continue
            if tool_name in PLAYWRIGHT_MCP_FORBIDDEN_TOOL_SET:
                error_list.append(
                    f"line {line_index}: forbidden Playwright MCP tool {tool_name}; use browser_evaluate with pure "
                    "page JavaScript and return serializable data instead."
                )
                continue
            if tool_name not in PLAYWRIGHT_MCP_CODE_TOOL_SET:
                continue
            for argument_text in self._browser_tool_argument_text_list_get(item.get("arguments")):
                if any(pattern.search(argument_text) for pattern in BROWSER_JAVASCRIPT_FORBIDDEN_PATTERN_LIST):
                    error_list.append(
                        f"line {line_index}: browser JavaScript for {tool_name} uses Node.js or dynamic import; "
                        "browser page code may read DOM data only and must return serializable data."
                    )
                    break
        if error_list:
            error_text = "; ".join(error_list)
            raise CodexExecutionError(f"Codex browser execution violated browser JavaScript contract: {error_text}")

    def _codex_completion_output_exist(self, *, diagnostic_dir: Path, output_path: Path | None) -> bool:
        """Return whether Codex wrote final output and reported turn completion.

        Args:
            diagnostic_dir: Current attempt diagnostic directory.
            output_path: `codex exec --output-last-message` path.

        Returns:
            Whether the attempt has enough final artifacts to stop a stuck process tree.
        """
        if output_path is None or not output_path.is_file() or output_path.stat().st_size == 0:
            return False
        return self._file_tail_contain(
            event_path=diagnostic_dir / "event.jsonl",
            needle='"type":"turn.completed"',
        )

    def _codex_output_path_get(self, command: list[str]) -> Path | None:
        """Return the `--output-last-message` path from one Codex command.

        Args:
            command: Codex command argv.

        Returns:
            Output path when the command declares one.
        """
        if "--output-last-message" not in command:
            return None
        index = command.index("--output-last-message") + 1
        if index >= len(command):
            return None
        return Path(command[index])

    def _command_list_get(
        self,
        *,
        browser_runtime_mcp_url: str,
        config: CodexRunnerConfig,
        output_path: Path,
        working_directory: Path,
        schema_path: Path,
    ) -> list[str]:
        """Return the Codex CLI command for one low-level execution.

        Args:
            browser_runtime_mcp_url: Browser/VPN runtime MCP URL.
            config: Explicit model and reasoning selection for this call.
            output_path: Final Codex message output path.
            working_directory: Root directory used as Codex working directory.
            schema_path: Structured output schema path.

        Returns:
            Codex CLI command argv.

        """
        command = [
            "codex",
            "exec",
            "--model",
            config.model,
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--ephemeral",
            "--ignore-user-config",
            "-c",
            'approval_policy="never"',
            "-c",
            f'model_reasoning_effort="{config.reasoning_effort}"',
            "--ignore-rules",
            "--skip-git-repo-check",
            "--cd",
            str(working_directory),
            "-",
        ]
        if browser_runtime_mcp_url:
            browser_config_args = self._playwright_mcp_config_arg_list_get(
                browser_runtime_mcp_url=browser_runtime_mcp_url,
            )
            for tool_name in PLAYWRIGHT_MCP_APPROVED_TOOL_LIST:
                browser_config_args.extend(
                    [
                        "-c",
                        f'mcp_servers.playwright.tools.{tool_name}.approval_mode="approve"',
                    ]
                )
            command[command.index("--ignore-rules") : command.index("--ignore-rules")] = browser_config_args
        return command

    def _file_tail_contain(self, *, event_path: Path, needle: str) -> bool:
        """Return whether one file tail contains a marker string.

        Args:
            event_path: File path to inspect.
            needle: Marker string.

        Returns:
            Whether the marker exists in the recent file tail.
        """
        try:
            with event_path.open("rb") as file:
                file.seek(0, os.SEEK_END)
                size = file.tell()
                file.seek(max(0, size - 20000))
                return needle.encode() in file.read()
        except OSError:
            return False

    def _event_stdout_append(
        self,
        *,
        event_path: Path,
        final: bool,
        persisted_stdout_offset: int,
        stdout_snapshot: str,
    ) -> int:
        """Append newly observed Codex stdout records and return the cumulative persisted offset.

        Args:
            event_path: Append-only Codex JSONL event stream path.
            final: Whether this is the final stdout snapshot.
            persisted_stdout_offset: Character offset persisted from earlier cumulative snapshots.
            stdout_snapshot: Current cumulative stdout snapshot.

        Returns:
            Character offset persisted from the cumulative stdout snapshot.
        """

        stdout_suffix = stdout_snapshot[persisted_stdout_offset:]
        stdout_text = stdout_suffix if final else stdout_suffix[: stdout_suffix.rfind("\n") + 1]
        if stdout_text:
            with event_path.open("a", encoding="utf-8") as event_file:
                event_file.write(stdout_text)
        return persisted_stdout_offset + len(stdout_text)

    def _output_model_get(
        self,
        *,
        output_model: type[OutputT],
        output_path: Path,
    ) -> OutputT:
        """Return the validated Codex output model.

        Args:
            output_model: Pydantic model class for the structured response.
            output_path: Final Codex message output path.

        Returns:
            Validated structured response.

        Raises:
            CodexExecutionError: If the output cannot be parsed as the expected model.
        """
        try:
            return output_model.model_validate_json(output_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CodexExecutionError(f"Codex execution returned invalid JSON: {exc}") from exc

    def _path_activity_marker_get(self, path: Path) -> int:
        """Return activity marker for one path tree.

        Args:
            path: Path to scan.

        Returns:
            Integer marker that changes when files under the path change.
        """
        try:
            path_stat = path.stat()
        except OSError:
            return 0
        activity_marker = path_stat.st_mtime_ns + path_stat.st_size
        for child_path in path.rglob("*"):
            try:
                child_stat = child_path.stat()
            except OSError:
                continue
            activity_marker += child_stat.st_mtime_ns + child_stat.st_size + 1
        return activity_marker

    def _path_activity_marker_list_get(self, path_list: list[Path]) -> int:
        """Return combined activity marker for watched path trees.

        Args:
            path_list: Path trees to scan.

        Returns:
            Combined activity marker.
        """

        return sum(self._path_activity_marker_get(path) for path in path_list)

    def _playwright_mcp_config_arg_list_get(self, *, browser_runtime_mcp_url: str) -> list[str]:
        """Return Codex config args for the run-level browser/VPN MCP server.

        Args:
            browser_runtime_mcp_url: Run-level browser/VPN runtime MCP URL.

        Returns:
            Codex `-c` argument list.
        """

        return [
            "-c",
            f"mcp_servers.playwright.url={browser_runtime_mcp_url!r}",
        ]

    def _process_group_kill(self, process: subprocess.Popen[str]) -> None:
        """Kill one subprocess process group.

        Args:
            process: Process whose group must be killed.
        """
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            process.kill()

    def _process_group_terminate(self, process: subprocess.Popen[str]) -> tuple[str, str]:
        """Terminate one subprocess process group and collect output.

        Args:
            process: Process whose group must be terminated.

        Returns:
            Captured stdout and stderr.
        """
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return process.communicate()
        except OSError:
            process.terminate()
        try:
            return process.communicate(timeout=CODEX_EXEC_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            self._process_group_kill(process)
            return process.communicate()

    def _subprocess_run(
        self,
        command: list[str],
        *,
        browser_artifact_activity: bool,
        diagnostic_dir: Path,
        input: str,
        working_directory: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Run `codex exec` with an artifact-activity inactivity timeout.

        Args:
            command: Codex command argv.
            browser_artifact_activity: Whether browser MCP artifacts count as subprocess activity.
            diagnostic_dir: Current attempt diagnostic directory watched for progress.
            input: Prompt text sent to Codex stdin.
            working_directory: Codex working directory.

        Returns:
            Completed process with captured stdout and stderr.
        """
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )
        communicate_input: str | None = input
        inactivity_seconds = 0
        output_path = self._codex_output_path_get(command)
        activity_path_list = [] if output_path is None else [output_path]
        if browser_artifact_activity:
            browser_artifact_path = self._browser_artifact_path_get(
                diagnostic_dir=diagnostic_dir,
                working_directory=working_directory,
            )
            if browser_artifact_path is not None:
                activity_path_list.append(browser_artifact_path)
        execution_activity_marker = self._path_activity_marker_list_get(activity_path_list)
        partial_stdout_text = ""
        persisted_stdout_offset = 0
        while True:
            try:
                stdout, stderr = process.communicate(
                    input=communicate_input,
                    timeout=CODEX_EXEC_POLL_SECONDS,
                )
                self._event_stdout_append(
                    event_path=diagnostic_dir / "event.jsonl",
                    final=True,
                    persisted_stdout_offset=persisted_stdout_offset,
                    stdout_snapshot=stdout,
                )
                if process.returncode is None:
                    return subprocess.CompletedProcess(args=command, returncode=1, stdout=stdout, stderr=stderr)
                return subprocess.CompletedProcess(
                    args=command, returncode=process.returncode, stdout=stdout, stderr=stderr
                )
            except subprocess.TimeoutExpired as exc:
                communicate_input = None
                partial_stdout = exc.stdout or ""
                if isinstance(partial_stdout, bytes):
                    partial_stdout = partial_stdout.decode(errors="replace")
                partial_stdout_changed = partial_stdout != partial_stdout_text
                if partial_stdout_changed:
                    partial_stdout_text = partial_stdout
                    persisted_stdout_offset = self._event_stdout_append(
                        event_path=diagnostic_dir / "event.jsonl",
                        final=False,
                        persisted_stdout_offset=persisted_stdout_offset,
                        stdout_snapshot=partial_stdout,
                    )
                if self._codex_completion_output_exist(
                    diagnostic_dir=diagnostic_dir,
                    output_path=output_path,
                ):
                    stdout, stderr = self._process_group_terminate(process)
                    self._event_stdout_append(
                        event_path=diagnostic_dir / "event.jsonl",
                        final=True,
                        persisted_stdout_offset=persisted_stdout_offset,
                        stdout_snapshot=stdout,
                    )
                    return subprocess.CompletedProcess(args=command, returncode=0, stdout=stdout, stderr=stderr)
                current_execution_activity_marker = self._path_activity_marker_list_get(activity_path_list)
                if partial_stdout_changed or current_execution_activity_marker != execution_activity_marker:
                    execution_activity_marker = current_execution_activity_marker
                    inactivity_seconds = 0
                    continue
                inactivity_seconds += CODEX_EXEC_POLL_SECONDS
                if inactivity_seconds < CODEX_EXEC_INACTIVITY_TIMEOUT_SECONDS:
                    continue
                self._process_group_kill(process)
                stdout, stderr = process.communicate()
                self._event_stdout_append(
                    event_path=diagnostic_dir / "event.jsonl",
                    final=True,
                    persisted_stdout_offset=persisted_stdout_offset,
                    stdout_snapshot=stdout,
                )
                timeout_stderr = (
                    f"{stderr}\nCodex exec timed out after {CODEX_EXEC_INACTIVITY_TIMEOUT_SECONDS} seconds "
                    "without execution artifact activity.\n"
                )
                return subprocess.CompletedProcess(args=command, returncode=124, stdout=stdout, stderr=timeout_stderr)

    def _final_diagnostic_remove(self, *, event_path: Path, output_path: Path, stderr_path: Path) -> None:
        """Remove stale final diagnostics before one Codex subprocess starts.

        Args:
            event_path: Codex JSON event stream path.
            output_path: Final Codex message output path.
            stderr_path: Captured stderr path.
        """
        for diagnostic_path in [output_path, stderr_path, event_path]:
            diagnostic_path.unlink(missing_ok=True)


__all__ = [
    "CodexExecutionError",
    "CodexRunner",
]
