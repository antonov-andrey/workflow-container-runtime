"""Package smoke tests."""

from pathlib import Path
import tomllib

import pytest

import workflow_container_runtime
from workflow_container_runtime.stage import BrowserActionResult, BrowsingError


def test_package_version_exist() -> None:
    """Verify the runtime package imports."""

    assert workflow_container_runtime.__version__ == "0.1.0"


def test_setuptools_package_discovery_excludes_tests() -> None:
    """Package only runtime modules, not repository tests."""

    pyproject_payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    package_find_payload = pyproject_payload["tool"]["setuptools"]["packages"]["find"]

    assert package_find_payload["include"] == ["workflow_container_runtime*"]


def test_stage_browsing_error_validates_text_fields() -> None:
    """Validate generic browser-backed stage error payload."""

    browsing_error = BrowsingError(error=" timeout ", url=" https://example.test ")

    assert browsing_error.error == "timeout"
    assert browsing_error.url == "https://example.test"

    with pytest.raises(ValueError, match="browsing error fields must be non-empty strings"):
        BrowsingError(error=" ", url="https://example.test")


def test_stage_browser_action_result_has_generic_error_list() -> None:
    """Validate generic browser-only action result payload."""

    result = BrowserActionResult(browsing_error_list=[BrowsingError(error="blocked", url="https://example.test")])

    assert result.model_dump(mode="json") == {
        "browsing_error_list": [{"error": "blocked", "url": "https://example.test"}]
    }
