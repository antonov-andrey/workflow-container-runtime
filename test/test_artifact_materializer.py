"""Artifact materializer tests."""

from pathlib import Path

from workflow_container_runtime.artifact import ArtifactMaterializationPolicy, ArtifactMaterializer


def test_artifact_materializer_copies_stage_external_artifact_tree(tmp_path: Path) -> None:
    """Copy configured external artifacts into the matching stage directory."""

    result_dir = tmp_path / "result"
    source_path = result_dir / ".playwright-mcp/current/stage/evidence/source.json"
    stage_dir = result_dir / "stage"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("{}\n", encoding="utf-8")

    ArtifactMaterializer(result_dir=result_dir).stage_artifact_materialize(
        stage_dir=stage_dir,
        policy=ArtifactMaterializationPolicy(),
    )

    assert (stage_dir / "evidence/source.json").read_text(encoding="utf-8") == "{}\n"


def test_artifact_materializer_ignores_missing_stage_external_artifact_tree(tmp_path: Path) -> None:
    """Treat absent configured external artifact trees as an empty materialization."""

    result_dir = tmp_path / "result"
    stage_dir = result_dir / "stage"
    stage_dir.mkdir(parents=True)

    ArtifactMaterializer(result_dir=result_dir).stage_artifact_materialize(
        stage_dir=stage_dir,
        policy=ArtifactMaterializationPolicy(),
    )

    assert list(stage_dir.iterdir()) == []
