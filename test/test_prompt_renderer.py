"""Runtime prompt renderer tests."""

from pathlib import Path

import pytest
from jinja2 import UndefinedError

from workflow_container_runtime.prompt import PromptRenderer


def test_prompt_renderer_loads_runtime_partial_by_prefix() -> None:
    """Render runtime-owned prompt resources through the runtime prefix."""

    prompt_text = PromptRenderer().render("runtime/partial/runtime_source_access.md.j2", {})

    assert prompt_text.strip()


def test_prompt_renderer_prefers_project_template_then_runtime_partial(tmp_path: Path) -> None:
    """Render project templates that include runtime-owned partials."""
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "stage.md.j2").write_text(
        'Stage {{ stage_name }}\n{% include "runtime/partial/artifact_reference_contract.md.j2" %}',
        encoding="utf-8",
    )

    prompt_text = PromptRenderer(template_dir=template_dir).render("stage.md.j2", {"stage_name": "source_discover"})

    assert "Stage source_discover" in prompt_text
    assert len(prompt_text.splitlines()) > 1


def test_prompt_renderer_uses_strict_undefined_variables(tmp_path: Path) -> None:
    """Fail prompt rendering when a project template variable is missing."""
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "stage.md.j2").write_text("Stage {{ missing_name }}", encoding="utf-8")

    with pytest.raises(UndefinedError):
        PromptRenderer(template_dir=template_dir).render("stage.md.j2", {})
