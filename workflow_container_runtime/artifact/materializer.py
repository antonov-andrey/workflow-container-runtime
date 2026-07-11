"""Generic external artifact tree materialization."""

import os
from pathlib import Path
import shutil
import tempfile

from pydantic import BaseModel, ConfigDict

_MATERIALIZATION_RESERVED_ROOT_NAME_SET = frozenset(
    {
        "diagnostics",
        "input.json",
        "result.json",
        "state.json",
        "state.sqlite3",
        "verification.json",
    }
)


class ArtifactMaterializationPolicy(BaseModel):
    """Configure source-neutral artifact tree roots."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    artifact_root_tuple: tuple[Path, ...]


class ArtifactMaterializer:
    """Materialize current-step trees from configured external roots."""

    def materialize(
        self,
        *,
        policy: ArtifactMaterializationPolicy,
        result_dir: Path,
        step_instance_dir: Path,
    ) -> None:
        """Copy each mirrored current-step tree into the canonical step.

        Args:
            policy: Source-neutral materialization policy.
            result_dir: Run result root.
            step_instance_dir: Current step instance directory.

        Raises:
            RuntimeError: If the step directory is outside the result root.
        """

        result_dir = result_dir.resolve()
        step_instance_dir = step_instance_dir.resolve()
        try:
            step_relative_path = step_instance_dir.relative_to(result_dir)
        except ValueError as exc:
            raise RuntimeError(f"Step artifact directory is outside result_dir: {step_instance_dir}") from exc
        source_target_path_pair_list: list[tuple[Path, Path]] = []
        for artifact_root in policy.artifact_root_tuple:
            source_root = artifact_root
            if not artifact_root.is_absolute():
                source_root = self._relative_source_root_get(result_dir=result_dir, artifact_root=artifact_root)
            source_target_path_pair_list.extend(
                self._step_tree_path_pair_list_get(
                    source_root=source_root,
                    step_instance_dir=step_instance_dir,
                    step_relative_path=step_relative_path,
                )
            )
        for _, target_path in source_target_path_pair_list:
            self._target_path_validate(step_instance_dir=step_instance_dir, target_path=target_path)
        for source_path, target_path in source_target_path_pair_list:
            self._file_copy(source_path=source_path, step_instance_dir=step_instance_dir, target_path=target_path)

    def _relative_source_root_get(self, *, result_dir: Path, artifact_root: Path) -> Path:
        """Reject symlinks in a relative source-root path before resolving it.

        Args:
            result_dir: Resolved current run result root.
            artifact_root: Relative configured external artifact root.

        Returns:
            Lexically joined unresolved source root.

        Raises:
            RuntimeError: If one configured root component is a symlink.
        """

        source_root = result_dir
        for path_part in artifact_root.parts:
            source_root = source_root / path_part
            if source_root.is_symlink():
                raise RuntimeError(f"Materialized artifact root must not contain a symlink: {source_root}")
        return source_root

    def _directory_chain_sync(self, *, directory_path: Path, step_instance_dir: Path) -> None:
        """Synchronize one target directory chain through the step root.

        Args:
            directory_path: Deepest directory whose entries changed.
            step_instance_dir: Canonical current-step directory.
        """

        current_path = directory_path
        while True:
            directory_descriptor = os.open(current_path, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            if current_path == step_instance_dir:
                return
            current_path = current_path.parent

    def _file_copy(self, *, source_path: Path, step_instance_dir: Path, target_path: Path) -> None:
        """Atomically publish one materialized file.

        Args:
            source_path: Validated source file.
            step_instance_dir: Canonical current-step directory.
            target_path: Validated target file.
        """

        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with source_path.open("rb") as source_file:
                with tempfile.NamedTemporaryFile(
                    dir=target_path.parent,
                    mode="wb",
                    prefix=f".{target_path.name}.",
                    delete=False,
                ) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                    shutil.copyfileobj(source_file, temporary_file)
                    temporary_file.flush()
                    shutil.copystat(source_path, temporary_path, follow_symlinks=False)
                    os.fsync(temporary_file.fileno())
            os.replace(temporary_path, target_path)
            temporary_path = None
            self._directory_chain_sync(
                directory_path=target_path.parent,
                step_instance_dir=step_instance_dir,
            )
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _step_tree_path_pair_list_get(
        self,
        *,
        source_root: Path,
        step_instance_dir: Path,
        step_relative_path: Path,
    ) -> list[tuple[Path, Path]]:
        """Validate and map one mirrored external tree without writing targets.

        Args:
            source_root: External tree root mirroring `result_dir`.
            step_instance_dir: Canonical current-step directory.
            step_relative_path: Current-step path relative to `result_dir`.

        Returns:
            Source and target path pairs in deterministic order.

        Raises:
            RuntimeError: If source data escape their root, contain symlinks, or target runtime-owned paths.
        """

        source_step_candidate_path = self._source_step_candidate_path_get(
            source_root=source_root,
            step_relative_path=step_relative_path,
        )
        source_root = source_root.resolve()
        source_step_path = source_step_candidate_path.resolve()
        try:
            source_step_path.relative_to(source_root)
        except ValueError as exc:
            raise RuntimeError(f"Materialized step artifact path is outside source root: {source_step_path}") from exc
        if not source_step_path.exists():
            return []
        if not source_step_path.is_dir():
            raise RuntimeError(f"Materialized step artifact path is not a directory: {source_step_path}")
        source_target_path_pair_list: list[tuple[Path, Path]] = []
        for source_path in sorted(source_step_path.rglob("*")):
            if source_path.is_symlink():
                raise RuntimeError(f"Materialized step artifact path must not be a symlink: {source_path}")
            if source_path.is_dir():
                continue
            if not source_path.is_file():
                raise RuntimeError(f"Materialized step artifact path must be a regular file: {source_path}")
            relative_path = source_path.relative_to(source_step_path)
            if relative_path.parts[0] in _MATERIALIZATION_RESERVED_ROOT_NAME_SET:
                raise RuntimeError(f"Materialized artifact target is runtime-reserved: {relative_path}")
            source_target_path_pair_list.append((source_path, step_instance_dir / relative_path))
        return source_target_path_pair_list

    def _source_step_candidate_path_get(self, *, source_root: Path, step_relative_path: Path) -> Path:
        """Reject source-root and step-chain symlinks before resolving either path.

        Args:
            source_root: Configured external tree root before resolution.
            step_relative_path: Current step path relative to the result root.

        Returns:
            Unresolved candidate source-step path.

        Raises:
            RuntimeError: If the configured root or one step-chain component is a symlink.
        """

        if source_root.is_symlink():
            raise RuntimeError(f"Materialized artifact root must not be a symlink: {source_root}")
        source_step_candidate_path = source_root
        for path_part in step_relative_path.parts:
            source_step_candidate_path = source_step_candidate_path / path_part
            if source_step_candidate_path.is_symlink():
                raise RuntimeError(
                    f"Materialized step artifact path must not be a symlink: {source_step_candidate_path}"
                )
        return source_step_candidate_path

    def _target_path_validate(self, *, step_instance_dir: Path, target_path: Path) -> None:
        """Reject destination symlinks and resolved path escapes.

        Args:
            step_instance_dir: Canonical current-step directory.
            target_path: Proposed destination artifact path.

        Raises:
            RuntimeError: If one destination component is a symlink or resolves outside the step.
        """

        relative_target_path = target_path.relative_to(step_instance_dir)
        current_path = step_instance_dir
        for path_part in relative_target_path.parts:
            current_path = current_path / path_part
            if current_path.is_symlink():
                raise RuntimeError(f"Materialized target path must not contain a symlink: {current_path}")
        try:
            target_path.parent.resolve().relative_to(step_instance_dir.resolve())
        except ValueError as exc:
            raise RuntimeError(f"Materialized target path is outside step directory: {target_path}") from exc
