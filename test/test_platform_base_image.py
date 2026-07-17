"""Source-definition checks for the optional platform base image."""

from pathlib import Path


def test_platform_base_image_contains_only_generic_pinned_runtime_components() -> None:
    """Build the reusable runtime and contract without one concrete workflow source."""

    dockerfile_text = (Path(__file__).resolve().parents[1] / "docker/platform-base/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "@openai/codex@0.144.1" in dockerfile_text
    assert "COPY --from=workflow_container_contract" in dockerfile_text
    assert "COPY workflow_container_runtime" in dockerfile_text
    assert "USER 1000:1000" in dockerfile_text
    assert "brand-size-chart" not in dockerfile_text
