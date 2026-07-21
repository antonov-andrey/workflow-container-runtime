"""Standard image-visible workflow Data roots."""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class WorkflowDataPath(BaseModel):
    """Carry the two standard writable source-data roots for one run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    result_path: Path
    workspace_path: Path

    @field_validator("result_path", "workspace_path")
    @classmethod
    def _absolute_path_validate(cls, value: Path) -> Path:
        """Require one absolute image-visible data root.

        Args:
            value: Candidate source-data root.

        Returns:
            Validated absolute path.
        """

        if not value.is_absolute():
            raise ValueError("workflow data roots must be absolute")
        return value

    @model_validator(mode="after")
    def _distinct_path_validate(self) -> Self:
        """Keep result and workspace as distinct non-overlapping roots.

        Returns:
            Validated path pair.
        """

        result_path = self.result_path.resolve()
        workspace_path = self.workspace_path.resolve()
        if (
            result_path == workspace_path
            or result_path.is_relative_to(workspace_path)
            or workspace_path.is_relative_to(result_path)
        ):
            raise ValueError("workflow result and workspace roots must not overlap")
        return self
