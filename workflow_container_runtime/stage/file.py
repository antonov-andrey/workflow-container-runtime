"""Standard workflow-container stage file paths."""

from pathlib import Path

STAGE_INPUT_FILENAME = "input.json"
STAGE_RESULT_FILENAME = "result.json"
STAGE_STATE_FILENAME = "state.json"
STAGE_VERIFICATION_FILENAME = "verification.json"


def stage_input_path_get(stage_dir: Path) -> Path:
    """Return the standard public stage input path.

    Args:
        stage_dir: Stage artifact directory.

    Returns:
        Standard public stage input path.
    """

    return stage_dir / STAGE_INPUT_FILENAME


def stage_result_path_get(stage_dir: Path) -> Path:
    """Return the standard public stage result path.

    Args:
        stage_dir: Stage artifact directory.

    Returns:
        Standard public stage result path.
    """

    return stage_dir / STAGE_RESULT_FILENAME


def stage_state_path_get(stage_dir: Path) -> Path:
    """Return the standard private stage state path.

    Args:
        stage_dir: Stage artifact directory.

    Returns:
        Standard private stage state path.
    """

    return stage_dir / STAGE_STATE_FILENAME


def stage_verification_path_get(stage_dir: Path) -> Path:
    """Return the standard public stage verification path.

    Args:
        stage_dir: Stage artifact directory.

    Returns:
        Standard public stage verification path.
    """

    return stage_dir / STAGE_VERIFICATION_FILENAME
