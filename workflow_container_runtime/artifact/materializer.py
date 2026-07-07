"""Generic artifact tree materialization."""

from pathlib import Path
import shutil

from pydantic import BaseModel, ConfigDict, Field


class ArtifactMaterializationPolicy(BaseModel):
    """Configure runtime-owned artifact tree materialization roots."""

    model_config = ConfigDict(extra="forbid", strict=True)

    artifact_root_list: list[Path] = Field(default_factory=lambda: [Path(".playwright-mcp/current")])


class ArtifactMaterializer:
    """Materialize configured external stage artifact trees."""

    def __init__(self, result_dir: Path) -> None:
        """Store the result directory.

        Args:
            result_dir: Root result directory.
        """

        self._result_dir = result_dir.resolve()

    def stage_artifact_materialize(self, stage_dir: Path, policy: ArtifactMaterializationPolicy) -> None:
        """Materialize stage artifacts according to one policy.

        Args:
            stage_dir: Canonical stage artifact directory.
            policy: Runtime-owned materialization policy.

        Raises:
            RuntimeError: If the stage directory is outside the result directory.
        """

        stage_path = self._absolute_path_get(stage_dir)
        try:
            stage_relative_path = stage_path.relative_to(self._result_dir)
        except ValueError as exc:
            raise RuntimeError(f"Stage artifact directory is outside result_dir: {stage_dir}") from exc
        for artifact_root in policy.artifact_root_list:
            self._stage_tree_copy(
                source_root=self._absolute_path_get(artifact_root),
                stage_path=stage_path,
                stage_relative_path=stage_relative_path,
            )

    def _absolute_path_get(self, path: Path) -> Path:
        """Return an absolute path using result_dir as relative-path base.

        Args:
            path: Absolute or result-dir-relative path.

        Returns:
            Resolved absolute path.
        """

        if path.is_absolute():
            return path.resolve()
        return (self._result_dir / path).resolve()

    def _stage_tree_copy(self, *, source_root: Path, stage_path: Path, stage_relative_path: Path) -> None:
        """Copy one materialized artifact tree into one stage directory.

        Args:
            source_root: Root path that mirrors the result directory by stage-relative path.
            stage_path: Canonical stage artifact directory.
            stage_relative_path: Stage path relative to result directory.

        Raises:
            RuntimeError: If the source tree is outside its source root, not a directory, or contains symlinks.
        """

        source_root = source_root.resolve()
        source_stage_path = (source_root / stage_relative_path).resolve()
        try:
            source_stage_path.relative_to(source_root)
        except ValueError as exc:
            raise RuntimeError(f"Materialized stage artifact path is outside source root: {source_stage_path}") from exc
        if not source_stage_path.exists():
            return
        if not source_stage_path.is_dir():
            raise RuntimeError(f"Materialized stage artifact path is not a directory: {source_stage_path}")
        for source_path in sorted(source_stage_path.rglob("*")):
            if source_path.is_dir():
                continue
            if source_path.is_symlink():
                raise RuntimeError(f"Materialized stage artifact path must not be a symlink: {source_path}")
            target_path = stage_path / source_path.relative_to(source_stage_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
