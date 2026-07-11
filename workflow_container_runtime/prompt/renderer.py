"""Strict Jinja2 renderer with exclusive runtime and project namespaces."""

from collections.abc import Callable, Mapping
from pathlib import Path

from jinja2 import BaseLoader, Environment, FileSystemLoader, PackageLoader, StrictUndefined, TemplateNotFound

RUNTIME_TEMPLATE_PREFIX = "runtime/"


class PromptTemplateLoader(BaseLoader):
    """Dispatch each template path to its single owning namespace."""

    def __init__(self, *, project_loader: BaseLoader | None, runtime_loader: BaseLoader) -> None:
        """Store exclusive project and runtime template loaders.

        Args:
            project_loader: Optional loader for unprefixed project templates.
            runtime_loader: Loader for runtime package templates.
        """

        self._project_loader = project_loader
        self._runtime_loader = runtime_loader

    def get_source(
        self,
        environment: Environment,
        template: str,
    ) -> tuple[str, str | None, Callable[[], bool] | None]:
        """Load one template only from the owner selected by its path.

        Args:
            environment: Jinja environment requesting the template.
            template: Template path supplied to Jinja.

        Returns:
            Source text, source filename, and optional freshness callback.

        Raises:
            TemplateNotFound: If the selected owner does not contain the template.
        """

        if template.startswith(RUNTIME_TEMPLATE_PREFIX):
            runtime_template = template.removeprefix(RUNTIME_TEMPLATE_PREFIX)
            return self._runtime_loader.get_source(environment, runtime_template)
        if self._project_loader is None:
            raise TemplateNotFound(template)
        return self._project_loader.get_source(environment, template)


class PromptRenderer:
    """Render project and runtime prompt templates with strict undefined handling."""

    def __init__(self, template_dir: Path | None = None) -> None:
        """Create a strict prompt renderer.

        Args:
            template_dir: Optional project template directory.
        """
        project_loader = None if template_dir is None else FileSystemLoader(template_dir)
        self._environment = Environment(
            autoescape=False,
            loader=PromptTemplateLoader(
                project_loader=project_loader,
                runtime_loader=PackageLoader(
                    "workflow_container_runtime",
                    "prompt/template",
                ),
            ),
            undefined=StrictUndefined,
        )

    def render(self, *, template_name: str, variable_by_name_map: Mapping[str, str]) -> str:
        """Render one prompt template with strict undefined-variable handling.

        Args:
            template_name: Template file name relative to the prompt template directory.
            variable_by_name_map: Template variables keyed by template name.

        Returns:
            Rendered prompt text.
        """

        return self._environment.get_template(template_name).render(**variable_by_name_map)
