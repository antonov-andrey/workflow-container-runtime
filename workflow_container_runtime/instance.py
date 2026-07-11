"""Shared validation for workflow and step instance identities."""

from pathlib import Path


def instance_key_validate(instance_key: str) -> None:
    """Validate one filesystem-segment instance identity.

    Args:
        instance_key: Candidate workflow or step instance key.

    Raises:
        ValueError: If the key is empty, special, or contains path separators.
    """

    if instance_key in {"", ".", ".."} or "/" in instance_key or "\\" in instance_key:
        raise ValueError("instance key must be one safe filesystem segment")


def instance_path_validate(*, instance_dir: Path, result_dir: Path, role: str) -> None:
    """Validate one owner directory against its result root.

    Args:
        instance_dir: Workflow or step instance directory.
        result_dir: Run result root.
        role: Directory role used in the validation error.

    Raises:
        ValueError: If paths are not absolute or the instance is outside the root.
    """

    if not result_dir.is_absolute() or not instance_dir.is_absolute():
        raise ValueError(f"result_dir and {role} must be absolute")
    try:
        instance_dir.resolve().relative_to(result_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"{role} must be inside result_dir") from exc
