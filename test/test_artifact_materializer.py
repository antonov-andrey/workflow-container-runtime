"""Artifact materializer tests."""

from pathlib import Path

from workflow_container_runtime.artifact import ArtifactMaterializer


def test_artifact_materializer_preserves_external_reference_inside_allowed_root(tmp_path: Path) -> None:
    """Materialize allowed absolute references as result-dir-relative POSIX artifact paths."""

    result_dir = tmp_path / "result"
    external_root = result_dir / ".tool-output"
    external_file = external_root / "source.json"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("{}\n", encoding="utf-8")
    materializer = ArtifactMaterializer(result_dir=result_dir, allowed_root_list=[external_root])

    assert materializer.reference_list_materialize([str(external_file)]) == [".tool-output/source.json"]


def test_artifact_materializer_resolves_relative_paths_from_result_dir(tmp_path: Path) -> None:
    """Materialize result-dir-relative references without depending on process CWD."""

    result_dir = tmp_path / "result"
    external_file = result_dir / ".tool-output" / "source.json"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("{}\n", encoding="utf-8")
    materializer = ArtifactMaterializer(result_dir=result_dir, allowed_root_list=[Path(".tool-output")])

    assert materializer.reference_list_materialize([".tool-output/source.json"]) == [".tool-output/source.json"]
