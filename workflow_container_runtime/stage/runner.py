"""Low-level Codex stage runner protocol."""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

MAX_STAGE_ATTEMPT_COUNT = 3


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
