"""Generic artifact reference materialization."""

from pathlib import Path
import shutil

from pydantic import BaseModel, ConfigDict


class ArtifactMaterializationPolicy(BaseModel):
    """Configure default runtime-owned artifact materialization."""

    model_config = ConfigDict(extra="forbid", strict=True)

    browser_artifact_copy_enabled: bool = True
    browser_artifact_root: Path = Path(".playwright-mcp/current")


class ArtifactMaterializer:
    """Convert allowed filesystem references into run-relative artifact paths."""

    def __init__(self, result_dir: Path, allowed_root_list: list[Path]) -> None:
        """Store the result directory and allowed reference roots.

        Args:
            result_dir: Root result directory.
            allowed_root_list: Absolute or relative directories allowed to contain materialized references.
        """

        self._result_dir = result_dir.resolve()
        self._allowed_root_list = [self._absolute_path_get(allowed_root) for allowed_root in allowed_root_list]

    def reference_list_materialize(self, reference_list: list[str]) -> list[str]:
        """Return run-relative POSIX references for allowed filesystem paths.

        Args:
            reference_list: Filesystem paths to materialize.

        Returns:
            Result-dir-relative POSIX artifact references.

        Raises:
            RuntimeError: If one reference is outside all allowed roots or outside the result directory.
        """

        materialized_reference_list = []
        for reference in reference_list:
            reference_path = self._absolute_path_get(Path(reference))
            self._reference_path_validate(reference_path=reference_path, reference=reference)
            try:
                materialized_reference_list.append(reference_path.relative_to(self._result_dir).as_posix())
            except ValueError as exc:
                raise RuntimeError(f"Artifact reference is outside result_dir: {reference}") from exc
        return materialized_reference_list

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
        if policy.browser_artifact_copy_enabled:
            self._stage_tree_copy(
                source_root=self._absolute_path_get(policy.browser_artifact_root),
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

    def _reference_path_validate(self, *, reference_path: Path, reference: str) -> None:
        """Validate one filesystem reference against allowed roots.

        Args:
            reference_path: Resolved filesystem reference path.
            reference: Original reference text for diagnostics.

        Raises:
            RuntimeError: If the resolved path is outside all allowed roots.
        """

        for allowed_root in self._allowed_root_list:
            try:
                reference_path.relative_to(allowed_root)
            except ValueError:
                continue
            return
        raise RuntimeError(f"Artifact reference is outside allowed roots: {reference}")

    def _stage_tree_copy(self, *, source_root: Path, stage_path: Path, stage_relative_path: Path) -> None:
        """Copy one materialized artifact tree into one stage directory.

        Args:
            source_root: Root path that mirrors the result directory by stage-relative path.
            stage_path: Canonical stage artifact directory.
            stage_relative_path: Stage path relative to result directory.

        Raises:
            RuntimeError: If the source tree is outside allowed roots, not a directory, or contains symlinks.
        """

        source_stage_path = (source_root / stage_relative_path).resolve()
        self._reference_path_validate(reference_path=source_stage_path, reference=source_stage_path.as_posix())
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
