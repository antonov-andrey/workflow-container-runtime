"""Standard public and private file paths for one owner instance."""

from pathlib import Path

INPUT_FILENAME = "input.json"
RESULT_FILENAME = "result.json"
STATE_FILENAME = "state.json"
STATE_DATABASE_FILENAME = "state.sqlite3"
VERIFICATION_FILENAME = "verification.json"


def input_path_get(instance_dir: Path) -> Path:
    """Return the standard public input path.

    Args:
        instance_dir: Workflow or step instance directory.

    Returns:
        Public input path.
    """

    return instance_dir / INPUT_FILENAME


def result_path_get(instance_dir: Path) -> Path:
    """Return the standard public result path.

    Args:
        instance_dir: Workflow or step instance directory.

    Returns:
        Public result path.
    """

    return instance_dir / RESULT_FILENAME


def state_path_get(instance_dir: Path) -> Path:
    """Return the optional private state path.

    Args:
        instance_dir: Workflow or step instance directory.

    Returns:
        Private state path.
    """

    return instance_dir / STATE_FILENAME


def state_database_path_get(instance_dir: Path) -> Path:
    """Return the standard mutable-state database path.

    Args:
        instance_dir: Workflow or step instance directory.

    Returns:
        Private current-state database path.
    """

    return instance_dir / STATE_DATABASE_FILENAME


def verification_path_get(instance_dir: Path) -> Path:
    """Return the standard public verification path.

    Args:
        instance_dir: Workflow or step instance directory.

    Returns:
        Public verification path.
    """

    return instance_dir / VERIFICATION_FILENAME
