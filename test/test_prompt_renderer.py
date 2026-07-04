"""Runtime prompt renderer tests."""

from pathlib import Path

import pytest
from jinja2 import UndefinedError

from workflow_container_runtime.prompt import PromptRenderer


def test_prompt_renderer_loads_runtime_partial_by_prefix() -> None:
    """Render runtime-owned prompt resources through the runtime prefix."""

    prompt_text = PromptRenderer().render("runtime/partial/runtime_source_access.md.j2", {})

    assert "Use the configured browser" in prompt_text
    assert "browser_evaluate with pure browser JavaScript" in prompt_text


def test_browser_system_prompt_is_domain_neutral() -> None:
    """Keep browser-stage prompt text generic for every workflow container."""

    prompt_text = PromptRenderer().render(
        "runtime/system/codex_browser_stage.md.j2",
        {"workflow_container_name": "example-container"},
    )

    assert "chart artifacts" not in prompt_text
    assert "workflow output artifacts" in prompt_text


def test_local_artifact_reading_contract_is_single_partial() -> None:
    """Keep local-artifact reading rules in one runtime-owned partial."""
    partial_path_list = [
        Path("workflow_container_runtime/prompt/template/partial/artifact_reference_contract.md.j2"),
        Path("workflow_container_runtime/prompt/template/partial/stage_verification_contract.md.j2"),
    ]

    for partial_path in partial_path_list:
        partial_text = partial_path.read_text(encoding="utf-8")
        assert '{% include "partial/local_artifact_reading_contract.md.j2" %}' in partial_text
        assert "file://, localhost, or 127.0.0.1 URLs for local artifacts are forbidden" not in partial_text


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
    assert "Write browser evidence" in prompt_text


def test_prompt_renderer_uses_strict_undefined_variables(tmp_path: Path) -> None:
    """Fail prompt rendering when a project template variable is missing."""
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "stage.md.j2").write_text("Stage {{ missing_name }}", encoding="utf-8")

    with pytest.raises(UndefinedError):
        PromptRenderer(template_dir=template_dir).render("stage.md.j2", {})
