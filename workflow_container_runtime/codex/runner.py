"""Codex-backed semantic stage execution."""

import json
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from workflow_container_runtime.artifact import JsonArtifactWriter
from workflow_container_runtime.codex.schema import codex_output_schema_get

CODEX_BROWSER_STAGE_SYSTEM_PROMPT = (
    "You are a Codex browser workflow stage inside {workflow_container_name}. "
    "Use Codex internal web search for search queries. "
    "Do not use browser tools or Playwright MCP to open public search-engine result pages. "
    "Use the configured browser tools only for target source pages selected from internal search results, site "
    "navigation, saved evidence, or prompt context. "
    "All target source-page and source-data loading must go through the configured browser. "
    "All non-browser loading mechanisms are forbidden for target source data; curl, requests, wget, and direct HTTP "
    "are examples, not an exhaustive list. "
    "Do not open local result artifacts through browser tools; file://, localhost, or 127.0.0.1 URLs for local "
    "artifacts are forbidden. "
    "Read local artifact files through normal filesystem access. "
    "Browser tools may write only evidence artifacts under browser evidence write directories. "
    "Before clicking a page target, close or answer browser-visible cookie banners, drawers, and overlays that "
    "intercept pointer events, then retry the target action. "
    "When a selector or text locator matches multiple elements, use the browser snapshot to choose a scoped unique "
    "target instead of repeating the broad locator. "
    "Retry transient browser navigation failures such as ERR_NETWORK_CHANGED through the same configured browser "
    "before treating the source as unavailable. "
    "Do not use browser page context to write chart artifacts, result.json, verification.json, or audit JSON. "
    "Do not use jq with guessed JSON paths; schema validation is already enforced by the workflow. "
    "Do not use brittle glob scripts over heterogeneous JSON artifacts; validate each parsed JSON value shape before "
    "field access and skip unrelated JSON artifact shapes. "
    "Do not use browser_run_code_unsafe. "
    "When extracting data from one opened page, use browser_evaluate with pure browser JavaScript only. "
    "Browser JavaScript must read DOM, window, document, links, tables, page text, and browser-visible state, then "
    "return serializable data. "
    "Browser JavaScript must not use Node.js APIs or module systems such as require, dynamic import, node: modules, fs, "
    "path, process, or Buffer. "
    "Local artifact writing belongs to normal Codex filesystem access or workflow code outside page JavaScript. "
    "You may write files only under absolute artifact write directories explicitly named in the prompt. "
    "Do not write under referenced artifact directories unless the prompt explicitly names them as write directories. "
    "Do not emit progress text. Return only the final JSON object that matches the supplied output schema."
)
CODEX_EXEC_INACTIVITY_TIMEOUT_SECONDS = 900
CODEX_EXEC_POLL_SECONDS = 5
CODEX_STAGE_SYSTEM_PROMPT = (
    "You are a schema-bound workflow stage inside {workflow_container_name}. "
    "Return only a JSON object that matches the supplied output schema. "
    "Do not edit files. Read the referenced evidence files and preserve all source data. "
    "Do not use jq with guessed JSON paths; schema validation is already enforced by the workflow. "
    "Do not use brittle glob scripts over heterogeneous JSON artifacts; validate each parsed JSON value shape before "
    "field access and skip unrelated JSON artifact shapes."
)
PLAYWRIGHT_MCP_APPROVED_TOOL_LIST = [
    "browser_click",
    "browser_evaluate",
    "browser_navigate",
    "browser_resize",
    "browser_snapshot",
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
_ResultModelT = TypeVar("_ResultModelT", bound=BaseModel)


class CodexStageError(RuntimeError):
    """Raised when one Codex semantic stage fails."""


class CodexStageRunner:
    """Run one Codex stage through the Codex CLI and validate its JSON output."""

    def __init__(
        self, artifact_writer: JsonArtifactWriter | None = None, workflow_container_name: str = "workflow-container"
    ) -> None:
        """Initialize the Codex stage runner.

        Args:
            artifact_writer: JSON artifact writer used for schema diagnostics.
            workflow_container_name: Human-readable workflow container name for Codex system prompts.
        """

        self._artifact_writer = artifact_writer or JsonArtifactWriter()
        self._workflow_container_name = workflow_container_name

    def run(
        self,
        *,
        allow_user_config: bool = False,
        browser_runtime_mcp_url: str = "",
        model_class: type[_ResultModelT],
        prompt_text: str,
        result_dir: Path,
        stage_dir: Path,
        stage_name: str,
    ) -> _ResultModelT:
        """Run one Codex semantic stage and validate its JSON result.

        Args:
            allow_user_config: Whether to load the configured Codex profile and MCP tools.
            browser_runtime_mcp_url: Run-level browser/VPN runtime MCP URL for browser stages.
            model_class: Pydantic model class for the stage result.
            prompt_text: Stage prompt text.
            result_dir: Root result directory used as Codex working directory.
            stage_dir: Stage artifact directory.
            stage_name: Stage name used for diagnostic artifact names.

        Returns:
            Validated stage result.

        Raises:
            CodexStageError: If Codex exits with an error or returns invalid JSON.
        """
        result_dir = result_dir.resolve()
        stage_dir = stage_dir.resolve()
        stage_dir.mkdir(parents=True, exist_ok=True)
        diagnostic_dir = stage_dir / "diagnostics" / stage_name
        diagnostic_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = diagnostic_dir / "prompt.md"
        output_path = diagnostic_dir / "codex_output.json"
        schema_path = diagnostic_dir / "schema.json"
        stderr_path = diagnostic_dir / "stderr.txt"
        event_path = diagnostic_dir / "event.jsonl"
        self._terminal_diagnostic_remove(
            event_path=event_path,
            output_path=output_path,
            stderr_path=stderr_path,
        )
        self._artifact_writer.write(schema_path, codex_output_schema_get(model_class))
        system_prompt_template = CODEX_BROWSER_STAGE_SYSTEM_PROMPT if allow_user_config else CODEX_STAGE_SYSTEM_PROMPT
        system_prompt = system_prompt_template.format(workflow_container_name=self._workflow_container_name)
        prompt_path.write_text(f"{system_prompt}\n\n{prompt_text}\n", encoding="utf-8")
        command = self._command_list_get(
            allow_user_config=allow_user_config,
            browser_runtime_mcp_url=browser_runtime_mcp_url,
            output_path=output_path,
            result_dir=result_dir,
            schema_path=schema_path,
            stage_name=stage_name,
        )
        process = self._subprocess_run(
            command,
            browser_artifact_activity=allow_user_config,
            input=prompt_path.read_text(encoding="utf-8"),
            result_dir=result_dir,
            stage_dir=stage_dir,
        )
        event_path.write_text(process.stdout, encoding="utf-8")
        stderr_path.write_text(process.stderr, encoding="utf-8")
        if allow_user_config:
            self._browser_tool_contract_validate(event_path=event_path, stage_name=stage_name)
        if process.returncode != 0:
            raise CodexStageError(f"Codex stage {stage_name} failed with exit code {process.returncode}.")
        return self._output_model_get(model_class=model_class, output_path=output_path, stage_name=stage_name)

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

    def _browser_tool_contract_validate(self, *, event_path: Path, stage_name: str) -> None:
        """Validate browser tool usage emitted by one Codex browser stage.

        Args:
            event_path: Codex JSONL event stream path.
            stage_name: Stage name used for diagnostics.

        Raises:
            CodexStageError: If one browser tool call violates the page-JavaScript contract.
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
            raise CodexStageError(
                f"Codex browser stage {stage_name} violated browser JavaScript contract: {error_text}"
            )

    def _codex_completion_output_exist(self, *, output_path: Path | None, stage_dir: Path) -> bool:
        """Return whether Codex wrote final output and reported turn completion.

        Args:
            output_path: `codex exec --output-last-message` path.
            stage_dir: Stage artifact directory watched for diagnostics.

        Returns:
            Whether the stage has enough terminal artifacts to stop a stuck process tree.
        """
        if output_path is None or not output_path.is_file() or output_path.stat().st_size == 0:
            return False
        for event_path in stage_dir.glob("diagnostics/*/event.jsonl"):
            if self._file_tail_contain(event_path=event_path, needle='"type":"turn.completed"'):
                return True
        return False

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
        allow_user_config: bool,
        browser_runtime_mcp_url: str,
        output_path: Path,
        result_dir: Path,
        schema_path: Path,
        stage_name: str,
    ) -> list[str]:
        """Return the Codex CLI command for one stage.

        Args:
            allow_user_config: Whether browser MCP configuration is enabled.
            browser_runtime_mcp_url: Browser/VPN runtime MCP URL.
            output_path: Final Codex message output path.
            result_dir: Root result directory used as Codex working directory.
            schema_path: Structured output schema path.
            stage_name: Stage name used for diagnostics.

        Returns:
            Codex CLI command argv.

        Raises:
            CodexStageError: If a browser stage has no browser runtime URL.
        """
        command = [
            "codex",
            "exec",
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
            "--ignore-rules",
            "--skip-git-repo-check",
            "--cd",
            str(result_dir),
            "-",
        ]
        if allow_user_config:
            if not browser_runtime_mcp_url:
                raise CodexStageError(f"Codex browser stage {stage_name} has no browser/VPN runtime MCP URL.")
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

    def _output_model_get(
        self,
        *,
        model_class: type[_ResultModelT],
        output_path: Path,
        stage_name: str,
    ) -> _ResultModelT:
        """Return the validated Codex stage output model.

        Args:
            model_class: Pydantic model class for the stage result.
            output_path: Final Codex message output path.
            stage_name: Stage name used for diagnostics.

        Returns:
            Validated stage result.

        Raises:
            CodexStageError: If the stage output cannot be parsed as the expected model.
        """
        try:
            return model_class.model_validate_json(output_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CodexStageError(f"Codex stage {stage_name} returned invalid JSON: {exc}") from exc

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
        input: str,
        result_dir: Path,
        stage_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Run `codex exec` with an artifact-activity inactivity timeout.

        Args:
            command: Codex command argv.
            browser_artifact_activity: Whether browser MCP artifacts count as subprocess activity.
            input: Prompt text sent to Codex stdin.
            result_dir: Root result directory.
            stage_dir: Stage artifact directory watched for progress.

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
        activity_path_list = [stage_dir]
        if browser_artifact_activity:
            activity_path_list.append(result_dir / ".playwright-mcp" / "current" / stage_dir.relative_to(result_dir))
        stage_activity_marker = self._path_activity_marker_list_get(activity_path_list)
        while True:
            try:
                stdout, stderr = process.communicate(
                    input=communicate_input,
                    timeout=CODEX_EXEC_POLL_SECONDS,
                )
                if process.returncode is None:
                    return subprocess.CompletedProcess(args=command, returncode=1, stdout=stdout, stderr=stderr)
                return subprocess.CompletedProcess(
                    args=command, returncode=process.returncode, stdout=stdout, stderr=stderr
                )
            except subprocess.TimeoutExpired:
                communicate_input = None
                if self._codex_completion_output_exist(output_path=output_path, stage_dir=stage_dir):
                    stdout, stderr = self._process_group_terminate(process)
                    return subprocess.CompletedProcess(args=command, returncode=0, stdout=stdout, stderr=stderr)
                current_stage_activity_marker = self._path_activity_marker_list_get(activity_path_list)
                if current_stage_activity_marker != stage_activity_marker:
                    stage_activity_marker = current_stage_activity_marker
                    inactivity_seconds = 0
                    continue
                inactivity_seconds += CODEX_EXEC_POLL_SECONDS
                if inactivity_seconds < CODEX_EXEC_INACTIVITY_TIMEOUT_SECONDS:
                    continue
                self._process_group_kill(process)
                stdout, stderr = process.communicate()
                timeout_stderr = (
                    f"{stderr}\nCodex exec timed out after {CODEX_EXEC_INACTIVITY_TIMEOUT_SECONDS} seconds "
                    "without stage artifact activity.\n"
                )
                return subprocess.CompletedProcess(args=command, returncode=124, stdout=stdout, stderr=timeout_stderr)

    def _terminal_diagnostic_remove(self, *, event_path: Path, output_path: Path, stderr_path: Path) -> None:
        """Remove stale terminal diagnostics before one Codex subprocess starts.

        Args:
            event_path: Codex JSON event stream path.
            output_path: Final Codex message output path.
            stderr_path: Captured stderr path.
        """
        for terminal_path in [output_path, stderr_path, event_path]:
            terminal_path.unlink(missing_ok=True)


def codex_stage_run(
    *,
    allow_user_config: bool = False,
    browser_runtime_mcp_url: str = "",
    model_class: type[_ResultModelT],
    prompt_text: str,
    result_dir: Path,
    stage_dir: Path,
    stage_name: str,
    workflow_container_name: str = "workflow-container",
) -> _ResultModelT:
    """Run one Codex semantic stage and validate its JSON result.

    Args:
        allow_user_config: Whether to load the configured Codex profile and MCP tools.
        browser_runtime_mcp_url: Run-level browser/VPN runtime MCP URL for browser stages.
        model_class: Pydantic model class for the stage result.
        prompt_text: Stage prompt text.
        result_dir: Root result directory used as Codex working directory.
        stage_dir: Stage artifact directory.
        stage_name: Stage name used for diagnostic artifact names.
        workflow_container_name: Human-readable workflow container name for Codex system prompts.

    Returns:
        Validated stage result.

    Raises:
        CodexStageError: If Codex exits with an error or returns invalid JSON.
    """
    return CodexStageRunner(workflow_container_name=workflow_container_name).run(
        allow_user_config=allow_user_config,
        browser_runtime_mcp_url=browser_runtime_mcp_url,
        model_class=model_class,
        prompt_text=prompt_text,
        result_dir=result_dir,
        stage_dir=stage_dir,
        stage_name=stage_name,
    )


__all__ = [
    "CodexStageError",
    "CodexStageRunner",
    "codex_stage_run",
]
