"""Strict Jinja2 renderer for workflow-container prompt templates."""

from collections.abc import Mapping
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader, PrefixLoader, StrictUndefined


class PromptRenderer:
    """Render project and runtime prompt templates with strict undefined handling."""

    def __init__(self, template_dir: Path | None = None) -> None:
        """Create a strict prompt renderer.

        Args:
            template_dir: Optional project template directory.
        """
        loader_list = [
            PrefixLoader(
                {
                    "runtime": PackageLoader(
                        "workflow_container_runtime",
                        "prompt/template",
                    )
                }
            ),
            PackageLoader(
                "workflow_container_runtime",
                "prompt/template",
            ),
        ]
        if template_dir is not None:
            loader_list.insert(0, FileSystemLoader(template_dir))
        self._environment = Environment(
            autoescape=False,
            loader=ChoiceLoader(loader_list),
            undefined=StrictUndefined,
        )

    def render(self, template_name: str, context: Mapping[str, object]) -> str:
        """Render one prompt template with strict undefined-variable handling.

        Args:
            template_name: Template file name relative to the prompt template directory.
            context: Template context values.

        Returns:
            Rendered prompt text.
        """

        return self._environment.get_template(template_name).render(**context)
