"""Artifact materializer tests."""

from pathlib import Path

import pytest

from workflow_container_runtime.artifact import ArtifactMaterializationPolicy, ArtifactMaterializer


def test_artifact_materializer_copies_step_external_artifact_tree(tmp_path: Path) -> None:
    """Copy configured external artifacts into the matching step directory."""

    result_dir = tmp_path / "result"
    source_path = result_dir / ".playwright-mcp/current/workflow/run/step/example/evidence/source.json"
    step_instance_dir = result_dir / "workflow/run/step/example"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("{}\n", encoding="utf-8")

    ArtifactMaterializer().materialize(
        policy=ArtifactMaterializationPolicy(artifact_root_tuple=(Path(".playwright-mcp/current"),)),
        result_dir=result_dir,
        step_instance_dir=step_instance_dir,
    )

    assert (step_instance_dir / "evidence/source.json").read_text(encoding="utf-8") == "{}\n"


def test_artifact_materializer_ignores_missing_step_external_artifact_tree(tmp_path: Path) -> None:
    """Treat absent configured external artifact trees as an empty materialization."""

    result_dir = tmp_path / "result"
    step_instance_dir = result_dir / "workflow/run/step/example"
    step_instance_dir.mkdir(parents=True)

    ArtifactMaterializer().materialize(
        policy=ArtifactMaterializationPolicy(artifact_root_tuple=()),
        result_dir=result_dir,
        step_instance_dir=step_instance_dir,
    )

    assert list(step_instance_dir.iterdir()) == []


def test_artifact_materializer_rejects_destination_symlink_escape(tmp_path: Path) -> None:
    """Do not copy external artifacts through a symlink inside the step tree."""

    result_dir = tmp_path / "result"
    step_instance_dir = result_dir / "workflow/run/step/example"
    source_path = result_dir / ".playwright-mcp/current/workflow/run/step/example/evidence/source.json"
    outside_dir = tmp_path / "outside"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("{}\n", encoding="utf-8")
    step_instance_dir.mkdir(parents=True)
    outside_dir.mkdir()
    (step_instance_dir / "evidence").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        ArtifactMaterializer().materialize(
            policy=ArtifactMaterializationPolicy(artifact_root_tuple=(Path(".playwright-mcp/current"),)),
            result_dir=result_dir,
            step_instance_dir=step_instance_dir,
        )

    assert list(outside_dir.iterdir()) == []


def test_artifact_materializer_rejects_source_directory_symlink(tmp_path: Path) -> None:
    """Reject a mirrored current-step source that is itself a symlink."""

    result_dir = tmp_path / "result"
    step_instance_dir = result_dir / "workflow/run/step/example"
    source_root = result_dir / ".playwright-mcp/current"
    outside_dir = tmp_path / "outside"
    step_instance_dir.mkdir(parents=True)
    outside_dir.mkdir()
    source_step_path = source_root / "workflow/run/step/example"
    source_step_path.parent.mkdir(parents=True)
    source_step_path.symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        ArtifactMaterializer().materialize(
            policy=ArtifactMaterializationPolicy(artifact_root_tuple=(Path(".playwright-mcp/current"),)),
            result_dir=result_dir,
            step_instance_dir=step_instance_dir,
        )


@pytest.mark.parametrize("symlink_location", ("root", "intermediate"))
def test_artifact_materializer_rejects_source_chain_symlinks_without_partial_copy(
    tmp_path: Path, symlink_location: str
) -> None:
    """Reject a configured-root or step-chain symlink before any target copy.

    Args:
        tmp_path: Isolated result root.
        symlink_location: Source chain component replaced by a symlink.
    """

    result_dir = tmp_path / "result"
    step_instance_dir = result_dir / "workflow/run/step/example"
    source_root = result_dir / ".external/current"
    outside_dir = tmp_path / "outside"
    outside_step_dir = outside_dir / "workflow/run/step/example"
    outside_step_dir.mkdir(parents=True)
    (outside_step_dir / "safe.txt").write_text("safe\n", encoding="utf-8")
    if symlink_location == "root":
        source_root.parent.mkdir(parents=True)
        source_root.symlink_to(outside_dir, target_is_directory=True)
    else:
        source_root.mkdir(parents=True)
        (source_root / "workflow").symlink_to(outside_dir / "workflow", target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        ArtifactMaterializer().materialize(
            policy=ArtifactMaterializationPolicy(artifact_root_tuple=(Path(".external/current"),)),
            result_dir=result_dir,
            step_instance_dir=step_instance_dir,
        )

    assert not (step_instance_dir / "safe.txt").exists()


def test_artifact_materializer_rejects_relative_root_leading_symlink_without_partial_copy(tmp_path: Path) -> None:
    """Reject a symlink in a relative artifact root before resolving that root.

    Args:
        tmp_path: Isolated result root.
    """

    result_dir = tmp_path / "result"
    step_instance_dir = result_dir / "workflow/run/step/example"
    outside_step_dir = tmp_path / "outside" / "current" / "workflow/run/step/example"
    outside_step_dir.mkdir(parents=True)
    (outside_step_dir / "safe.txt").write_text("safe\n", encoding="utf-8")
    result_dir.mkdir()
    (result_dir / ".external").symlink_to(tmp_path / "outside", target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        ArtifactMaterializer().materialize(
            policy=ArtifactMaterializationPolicy(artifact_root_tuple=(Path(".external/current"),)),
            result_dir=result_dir,
            step_instance_dir=step_instance_dir,
        )

    assert not (step_instance_dir / "safe.txt").exists()


@pytest.mark.parametrize(
    "reserved_relative_path",
    (
        Path("input.json"),
        Path("result.json"),
        Path("state.json"),
        Path("state.sqlite3"),
        Path("verification.json"),
        Path("diagnostics/event.jsonl"),
    ),
    ids=("input", "result", "state", "state_database", "verification", "diagnostics"),
)
def test_artifact_materializer_rejects_reserved_root_without_partial_copy(
    tmp_path: Path,
    reserved_relative_path: Path,
) -> None:
    """Prevalidate a complete source tree before copying any safe sibling."""

    result_dir = tmp_path / "result"
    step_instance_dir = result_dir / "workflow/run/step/example"
    source_step_dir = result_dir / ".external/current/workflow/run/step/example"
    safe_source_path = source_step_dir / "artifact/safe.txt"
    reserved_source_path = source_step_dir / reserved_relative_path
    safe_source_path.parent.mkdir(parents=True, exist_ok=True)
    reserved_source_path.parent.mkdir(parents=True, exist_ok=True)
    safe_source_path.write_text("safe\n", encoding="utf-8")
    reserved_source_path.write_text("reserved\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        ArtifactMaterializer().materialize(
            policy=ArtifactMaterializationPolicy(artifact_root_tuple=(Path(".external/current"),)),
            result_dir=result_dir,
            step_instance_dir=step_instance_dir,
        )

    assert not (step_instance_dir / "artifact/safe.txt").exists()
    assert not (step_instance_dir / reserved_relative_path).exists()


def test_artifact_materializer_replaces_existing_target_without_temporary_residue(tmp_path: Path) -> None:
    """Replace an existing artifact and leave only the complete new file."""

    result_dir = tmp_path / "result"
    step_instance_dir = result_dir / "workflow/run/step/example"
    source_path = result_dir / ".external/current/workflow/run/step/example/artifact/source.txt"
    target_path = step_instance_dir / "artifact/source.txt"
    source_path.parent.mkdir(parents=True)
    target_path.parent.mkdir(parents=True)
    source_path.write_text("new payload\n", encoding="utf-8")
    target_path.write_text("old payload\n", encoding="utf-8")

    ArtifactMaterializer().materialize(
        policy=ArtifactMaterializationPolicy(artifact_root_tuple=(Path(".external/current"),)),
        result_dir=result_dir,
        step_instance_dir=step_instance_dir,
    )

    assert target_path.read_text(encoding="utf-8") == "new payload\n"
    assert list(target_path.parent.glob(f".{target_path.name}.*")) == []
