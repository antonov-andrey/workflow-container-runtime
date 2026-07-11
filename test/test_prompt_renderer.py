"""Behavior tests for prompt loader ownership and strict rendering."""

from pathlib import Path

import pytest
from jinja2 import FileSystemLoader, TemplateNotFound, UndefinedError

import workflow_container_runtime.prompt.renderer as renderer_module
from workflow_container_runtime.prompt import PromptRenderer


def _runtime_loader_replace(
    monkeypatch: pytest.MonkeyPatch,
    runtime_template_dir: Path,
) -> None:
    """Replace package resource loading with one test-owned runtime tree."""

    monkeypatch.setattr(
        renderer_module,
        "PackageLoader",
        lambda package_name, package_path: FileSystemLoader(runtime_template_dir),
    )


def test_prompt_renderer_protects_runtime_namespace_from_project_shadowing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resolve the runtime prefix only from the runtime-owned loader."""

    runtime_template_dir = tmp_path / "runtime-template"
    project_template_dir = tmp_path / "project-template"
    (runtime_template_dir / "partial").mkdir(parents=True)
    (project_template_dir / "runtime/partial").mkdir(parents=True)
    (runtime_template_dir / "partial/shared.md.j2").write_text("runtime {{ value }}", encoding="utf-8")
    (project_template_dir / "runtime/partial/shared.md.j2").write_text("project shadow", encoding="utf-8")
    _runtime_loader_replace(monkeypatch, runtime_template_dir)

    prompt_text = PromptRenderer(template_dir=project_template_dir).render(
        template_name="runtime/partial/shared.md.j2",
        variable_by_name_map={"value": "owned"},
    )

    assert prompt_text == "runtime owned"


def test_prompt_renderer_rejects_project_fallback_for_missing_runtime_resource(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fail when a reserved runtime path is absent from the runtime package.

    Args:
        monkeypatch: Pytest replacement helper.
        tmp_path: Temporary template roots.
    """

    runtime_template_dir = tmp_path / "runtime-template"
    project_template_dir = tmp_path / "project-template"
    runtime_template_dir.mkdir()
    (project_template_dir / "runtime/partial").mkdir(parents=True)
    (project_template_dir / "runtime/partial/missing.md.j2").write_text("project shadow", encoding="utf-8")
    _runtime_loader_replace(monkeypatch, runtime_template_dir)

    with pytest.raises(TemplateNotFound):
        PromptRenderer(template_dir=project_template_dir).render(
            template_name="runtime/partial/missing.md.j2",
            variable_by_name_map={},
        )


def test_prompt_renderer_project_template_includes_runtime_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allow a project template to include one protected runtime partial."""

    runtime_template_dir = tmp_path / "runtime-template"
    project_template_dir = tmp_path / "project-template"
    (runtime_template_dir / "partial").mkdir(parents=True)
    project_template_dir.mkdir()
    (runtime_template_dir / "partial/shared.md.j2").write_text("runtime {{ value }}", encoding="utf-8")
    (project_template_dir / "step.md.j2").write_text(
        'project {% include "runtime/partial/shared.md.j2" %}',
        encoding="utf-8",
    )
    _runtime_loader_replace(monkeypatch, runtime_template_dir)

    prompt_text = PromptRenderer(template_dir=project_template_dir).render(
        template_name="step.md.j2",
        variable_by_name_map={"value": "partial"},
    )

    assert prompt_text == "project runtime partial"


def test_prompt_renderer_uses_project_loader_for_unprefixed_template(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep unprefixed template ownership in the concrete project."""

    runtime_template_dir = tmp_path / "runtime-template"
    project_template_dir = tmp_path / "project-template"
    runtime_template_dir.mkdir()
    project_template_dir.mkdir()
    (runtime_template_dir / "step.md.j2").write_text("runtime fallback", encoding="utf-8")
    (project_template_dir / "step.md.j2").write_text("project owned", encoding="utf-8")
    _runtime_loader_replace(monkeypatch, runtime_template_dir)

    prompt_text = PromptRenderer(template_dir=project_template_dir).render(
        template_name="step.md.j2",
        variable_by_name_map={},
    )

    assert prompt_text == "project owned"


def test_prompt_renderer_does_not_expose_unprefixed_runtime_template(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Require the protected prefix for every runtime-owned template."""

    runtime_template_dir = tmp_path / "runtime-template"
    project_template_dir = tmp_path / "project-template"
    (runtime_template_dir / "system").mkdir(parents=True)
    project_template_dir.mkdir()
    (runtime_template_dir / "system/step.md.j2").write_text("runtime system", encoding="utf-8")
    _runtime_loader_replace(monkeypatch, runtime_template_dir)

    with pytest.raises(TemplateNotFound):
        PromptRenderer(template_dir=project_template_dir).render(
            template_name="system/step.md.j2",
            variable_by_name_map={},
        )


def test_prompt_renderer_uses_strict_undefined_variables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fail rendering when a project template variable is missing."""

    runtime_template_dir = tmp_path / "runtime-template"
    project_template_dir = tmp_path / "project-template"
    runtime_template_dir.mkdir()
    project_template_dir.mkdir()
    (project_template_dir / "step.md.j2").write_text("Step {{ missing_name }}", encoding="utf-8")
    _runtime_loader_replace(monkeypatch, runtime_template_dir)

    with pytest.raises(UndefinedError):
        PromptRenderer(template_dir=project_template_dir).render(
            template_name="step.md.j2",
            variable_by_name_map={},
        )
