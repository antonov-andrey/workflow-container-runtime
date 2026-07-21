"""Behavior tests for immutable JSON artifact publication."""

import os
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import workflow_container_runtime.artifact as artifact
from workflow_container_runtime.artifact.writer import (
    JsonArtifactWriter,
    JsonLinesArtifactWriter,
    shared_artifact_directory_prepare,
)


class DatasetRow(BaseModel):
    """Ordered dataset row used by JSON Lines writer tests."""

    model_config = ConfigDict(extra="forbid", strict=True)

    alpha: str
    beta: int


class ExampleModel(BaseModel):
    """Strict JSON artifact used by writer tests."""

    model_config = ConfigDict(extra="forbid", strict=True)

    value: str


class MutableCollectionModel(BaseModel):
    """Expose one nested collection whose in-place mutation bypasses assignment validation."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    value_list: list[str] = Field(min_length=1)


def test_artifact_public_api_does_not_expose_mutable_jsonl_state() -> None:
    """Keep JSONL limited to owner-specific immutable event streams."""

    assert not hasattr(artifact, "JsonlArtifactStore")
    assert not hasattr(artifact, "JsonlRecord")


def test_shared_artifact_directory_prepare_overrides_process_umask(tmp_path: Path) -> None:
    """Let an external artifact producer write only its declared directory."""

    artifact_directory = tmp_path / "external" / "evidence"
    previous_umask = os.umask(0o077)
    try:
        shared_artifact_directory_prepare(artifact_directory)
    finally:
        os.umask(previous_umask)

    assert artifact_directory.is_dir()
    assert artifact_directory.stat().st_mode & 0o777 == 0o777


def test_json_writer_replaces_model_atomically(tmp_path: Path) -> None:
    """Publish and replace canonical JSON atomically."""

    path = tmp_path / "result.json"
    writer = JsonArtifactWriter()

    writer.write(path, ExampleModel(value="first"))
    writer.write(path, ExampleModel(value="second"))

    assert ExampleModel.model_validate_json(path.read_text(encoding="utf-8")) == ExampleModel(value="second")
    assert list(tmp_path.glob(".result.json.*")) == []


def test_json_writer_publishes_schema_from_model(tmp_path: Path) -> None:
    """Generate schema from the exact model instead of caller-built dictionaries."""

    path = tmp_path / "example.schema.json"

    JsonArtifactWriter().schema_write(path, ExampleModel)

    schema_text = path.read_text(encoding="utf-8")
    assert '"value"' in schema_text
    assert '"additionalProperties": false' in schema_text


def test_json_writer_revalidates_in_place_mutated_model_before_publication(tmp_path: Path) -> None:
    """Reject a model whose nested collection became invalid after construction."""

    path = tmp_path / "result.json"
    value = MutableCollectionModel(value_list=["valid"])
    value.value_list.clear()

    with pytest.raises(ValidationError):
        JsonArtifactWriter().write(path, value)

    assert not path.exists()


def test_json_lines_writer_preserves_row_and_field_order(tmp_path: Path) -> None:
    """Publish compact UTF-8 rows in caller order with one trailing newline each."""

    path = tmp_path / "dataset" / "part-00000.jsonl"
    JsonLinesArtifactWriter().write(
        path,
        [DatasetRow(alpha="первый", beta=1), DatasetRow(alpha="second", beta=2)],
    )

    assert path.read_text(encoding="utf-8") == ('{"alpha":"первый","beta":1}\n' '{"alpha":"second","beta":2}\n')
    assert not list(path.parent.glob(f".{path.name}.*"))
