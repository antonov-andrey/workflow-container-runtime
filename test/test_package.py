"""Package smoke tests."""

import workflow_container_runtime


def test_package_version_exist() -> None:
    """Verify the runtime package imports."""

    assert workflow_container_runtime.__version__ == "0.1.0"
