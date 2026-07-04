"""Package smoke tests."""

from pathlib import Path
import tomllib

import workflow_container_runtime


def test_package_version_exist() -> None:
    """Verify the runtime package imports."""

    assert workflow_container_runtime.__version__ == "0.1.0"


def test_setuptools_package_discovery_excludes_tests() -> None:
    """Package only runtime modules, not repository tests."""

    pyproject_payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    package_find_payload = pyproject_payload["tool"]["setuptools"]["packages"]["find"]

    assert package_find_payload["include"] == ["workflow_container_runtime*"]
