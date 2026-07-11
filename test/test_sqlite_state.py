"""Behavior tests for the runtime-owned SQLite current-state API."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workflow_container_runtime.state import (
    STATE_DATABASE_FILENAME,
    SqliteStateCommand,
    SqliteStateReader,
    SqliteStateStore,
    SqliteStateTable,
    state_database_path_get,
)
import workflow_container_runtime.state.sqlite as state_sqlite


class StrictModel(BaseModel):
    """Provide the strict boundary configuration required by state records."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)


class CommandInput(StrictModel):
    """Represent one validated command input artifact."""

    run_key: str


class CompoundRecord(StrictModel):
    """Represent one row with a natural compound identity."""

    group_key: str
    item_number: int
    tag_list: list[str]
    title: str


class HeterogeneousLiteralRecord(StrictModel):
    """Expose an unsupported literal that mixes SQLite scalar types."""

    record_key: int
    state: Literal["active", 1]


class LiteralRecord(StrictModel):
    """Represent native scalar literal values beside one structured collection."""

    active: Literal[True, False]
    color: Literal["blue", "red"]
    record_key: int
    retry_count: Literal[0, 1, 2]
    tag_list: list[str]


class OptionalKeyRecord(StrictModel):
    """Expose an invalid nullable primary-key field."""

    record_key: str | None
    title: str


class ScalarRecord(StrictModel):
    """Represent one row with native scalars and one structured value."""

    active: bool
    metadata_by_name_map: dict[str, str]
    record_key: int
    title: str | None


class StructuredKeyRecord(StrictModel):
    """Expose an invalid structured primary-key field."""

    record_key_list: list[str]
    title: str


COMPOUND_TABLE = SqliteStateTable[CompoundRecord](
    name="compound_record",
    primary_key_field_name_tuple=("group_key", "item_number"),
    record_model=CompoundRecord,
)
SCALAR_TABLE = SqliteStateTable[ScalarRecord](
    name="scalar_record",
    primary_key_field_name_tuple=("record_key",),
    record_model=ScalarRecord,
)
LITERAL_TABLE = SqliteStateTable[LiteralRecord](
    name="literal_record",
    primary_key_field_name_tuple=("record_key",),
    record_model=LiteralRecord,
)


def test_state_database_path_get_uses_only_the_standard_sibling_filename(tmp_path: Path) -> None:
    """Derive the one runtime-owned database path from an instance directory."""

    assert STATE_DATABASE_FILENAME == "state.sqlite3"
    assert state_database_path_get(tmp_path / "instance") == tmp_path / "instance" / "state.sqlite3"


@pytest.mark.parametrize(
    ("name", "primary_key_field_name_tuple", "record_model"),
    (
        ("unsafe-name", ("record_key",), ScalarRecord),
        ("scalar_record", (), ScalarRecord),
        ("scalar_record", ("missing",), ScalarRecord),
        ("scalar_record", ("record_key", "record_key"), ScalarRecord),
        ("optional_key_record", ("record_key",), OptionalKeyRecord),
        ("structured_key_record", ("record_key_list",), StructuredKeyRecord),
    ),
    ids=("unsafe_name", "empty_key", "unknown_key", "duplicate_key", "optional_key", "structured_key"),
)
def test_sqlite_state_table_rejects_invalid_primary_key_contract(
    name: str,
    primary_key_field_name_tuple: tuple[str, ...],
    record_model: type[StrictModel],
) -> None:
    """Reject table descriptors that cannot produce one safe natural key."""

    with pytest.raises(ValueError):
        SqliteStateTable(
            name=name, primary_key_field_name_tuple=primary_key_field_name_tuple, record_model=record_model
        )


def test_sqlite_state_table_rejects_heterogeneous_literal_storage() -> None:
    """Reject literal fields that cannot use one native SQLite scalar type."""

    with pytest.raises(ValueError, match="heterogeneous"):
        SqliteStateTable(
            name="heterogeneous_literal_record",
            primary_key_field_name_tuple=("record_key",),
            record_model=HeterogeneousLiteralRecord,
        )


def test_sqlite_state_store_creates_exact_schema_and_round_trips_current_rows(tmp_path: Path) -> None:
    """Create exact tables and replace one current row through its natural key."""

    path = state_database_path_get(tmp_path)
    store = SqliteStateStore()
    first_record = ScalarRecord(
        active=True,
        metadata_by_name_map={"country": "TR", "source": "catalog"},
        record_key=4,
        title=None,
    )
    corrected_record = first_record.model_copy(update={"active": False, "title": "Corrected"})

    store.initialize(path, [SCALAR_TABLE, COMPOUND_TABLE])

    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA table_info("scalar_record")').fetchall() == [
            (0, "active", "INTEGER", 1, None, 0),
            (1, "metadata_by_name_map", "TEXT", 1, None, 0),
            (2, "record_key", "INTEGER", 1, None, 1),
            (3, "title", "TEXT", 0, None, 0),
        ]
        assert connection.execute('PRAGMA table_info("compound_record")').fetchall() == [
            (0, "group_key", "TEXT", 1, None, 1),
            (1, "item_number", "INTEGER", 1, None, 2),
            (2, "tag_list", "TEXT", 1, None, 0),
            (3, "title", "TEXT", 1, None, 0),
        ]

    assert store.upsert(path, SCALAR_TABLE, first_record) == first_record
    assert store.upsert(path, SCALAR_TABLE, first_record) == first_record
    assert store.upsert(path, SCALAR_TABLE, corrected_record) == corrected_record
    assert store.get(path, SCALAR_TABLE, (4,)) == corrected_record
    assert store.list(path, SCALAR_TABLE) == [corrected_record]
    assert not path.with_name(f"{path.name}-wal").exists()
    assert not path.with_name(f"{path.name}-shm").exists()


def test_sqlite_state_store_uses_native_columns_for_homogeneous_literals(tmp_path: Path) -> None:
    """Store scalar literals natively while keeping collections as canonical JSON."""

    path = state_database_path_get(tmp_path)
    record = LiteralRecord(active=True, color="red", record_key=7, retry_count=2, tag_list=["first", "second"])
    store = SqliteStateStore()
    store.initialize(path, [LITERAL_TABLE])

    store.upsert(path, LITERAL_TABLE, record)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            'SELECT "active", typeof("active"), "color", typeof("color"), "retry_count", '
            'typeof("retry_count"), "tag_list", typeof("tag_list") FROM "literal_record"'
        ).fetchone() == (1, "integer", "red", "text", 2, "integer", '["first","second"]', "text")
    assert store.get(path, LITERAL_TABLE, (7,)) == record


def test_sqlite_state_store_orders_compound_keys_and_supports_prefix_and_delete(tmp_path: Path) -> None:
    """Read compound rows in complete key order through exact and prefix keys."""

    path = state_database_path_get(tmp_path)
    store = SqliteStateStore()
    record_list = [
        CompoundRecord(group_key="b", item_number=1, tag_list=["b"], title="B1"),
        CompoundRecord(group_key="a", item_number=2, tag_list=["a", "two"], title="A2"),
        CompoundRecord(group_key="a", item_number=1, tag_list=["a", "one"], title="A1"),
    ]
    store.initialize(path, [COMPOUND_TABLE])
    for record in record_list:
        store.upsert(path, COMPOUND_TABLE, record)

    assert store.list(path, COMPOUND_TABLE) == [record_list[2], record_list[1], record_list[0]]
    assert store.list_by_primary_key_prefix(path, COMPOUND_TABLE, ("a",)) == [record_list[2], record_list[1]]
    assert store.get(path, COMPOUND_TABLE, ("a", 2)) == record_list[1]

    store.delete(path, COMPOUND_TABLE, ("a", 1))

    assert store.get(path, COMPOUND_TABLE, ("a", 1)) is None
    assert store.list_by_primary_key_prefix(path, COMPOUND_TABLE, ("a",)) == [record_list[1]]


@pytest.mark.parametrize(
    ("primary_key_value_tuple", "message"),
    ((("4",), "record_key"), ((4, "extra"), "expected 1")),
    ids=("wrong_scalar_type", "wrong_key_length"),
)
def test_sqlite_state_store_rejects_invalid_primary_key_values(
    tmp_path: Path,
    primary_key_value_tuple: tuple[object, ...],
    message: str,
) -> None:
    """Reject malformed keys before a query can reach SQLite."""

    path = state_database_path_get(tmp_path)
    store = SqliteStateStore()
    store.initialize(path, [SCALAR_TABLE])

    with pytest.raises((TypeError, ValidationError, ValueError), match=message):
        store.get(path, SCALAR_TABLE, primary_key_value_tuple)


def test_sqlite_state_store_rejects_incompatible_existing_schema(tmp_path: Path) -> None:
    """Refuse recovery when an existing table differs from its declared descriptor."""

    path = state_database_path_get(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE "scalar_record" ("record_key" TEXT PRIMARY KEY)')

    with pytest.raises(RuntimeError, match="schema"):
        SqliteStateStore().initialize(path, [SCALAR_TABLE])


def test_sqlite_state_store_reopens_and_validates_an_existing_current_database(tmp_path: Path) -> None:
    """Recover current rows by reopening the same validated database file."""

    path = state_database_path_get(tmp_path)
    record = ScalarRecord(active=True, metadata_by_name_map={"source": "recovery"}, record_key=9, title="Stored")
    first_store = SqliteStateStore()
    first_store.initialize(path, [SCALAR_TABLE])
    first_store.upsert(path, SCALAR_TABLE, record)

    reopened_store = SqliteStateStore()

    assert reopened_store.list(path, SCALAR_TABLE) == [record]


def test_sqlite_state_reader_uses_uri_read_only_access_without_mutating_database(tmp_path: Path) -> None:
    """Read one declared table through SQLite URI read-only access only.

    Args:
        tmp_path: Isolated state database root.
    """

    path = state_database_path_get(tmp_path)
    record = ScalarRecord(active=True, metadata_by_name_map={"source": "read-only"}, record_key=9, title="Stored")
    store = SqliteStateStore()
    store.initialize(path, [SCALAR_TABLE])
    store.upsert(path, SCALAR_TABLE, record)
    database_state = (path.read_bytes(), path.stat().st_mtime_ns)
    with sqlite3.connect(path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
    assert journal_mode is not None

    assert SqliteStateReader().list(path, SCALAR_TABLE) == [record]
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(
                'INSERT INTO "scalar_record" ("active", "metadata_by_name_map", "record_key", "title") '
                "VALUES (1, '{}', 10, 'forbidden')"
            )
    with sqlite3.connect(path) as connection:
        current_journal_mode = connection.execute("PRAGMA journal_mode").fetchone()

    assert (path.read_bytes(), path.stat().st_mtime_ns) == database_state
    assert current_journal_mode == journal_mode


def test_sqlite_state_store_upsert_uses_one_connection_for_one_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep one current-row comparison and update inside one SQLite connection."""

    path = state_database_path_get(tmp_path)
    store = SqliteStateStore()
    record = ScalarRecord(active=True, metadata_by_name_map={"source": "connection"}, record_key=2, title="Stored")
    store.initialize(path, [SCALAR_TABLE])
    sqlite_connect = sqlite3.connect
    connection_path_list: list[Path] = []

    def connection_get(database: str | Path, *args: object, **kwargs: object) -> sqlite3.Connection:
        """Record state-store connection attempts while retaining real SQLite behavior."""

        connection_path_list.append(Path(database))
        return sqlite_connect(database, *args, **kwargs)

    monkeypatch.setattr(state_sqlite.sqlite3, "connect", connection_get)

    store.upsert(path, SCALAR_TABLE, record)

    assert connection_path_list == [path]


def test_sqlite_state_command_uses_only_input_sibling_database_and_compact_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Drive the registry-only command through current input and stdin objects."""

    input_path = tmp_path / "step" / "input.json"
    input_path.parent.mkdir(parents=True)
    input_path.write_text(CommandInput(run_key="run").model_dump_json(), encoding="utf-8")
    command = SqliteStateCommand()
    table_by_name_map = {SCALAR_TABLE.name: SCALAR_TABLE}

    assert command.run([str(input_path), "initialize"], CommandInput, table_by_name_map) == 0
    assert capsys.readouterr().out == "null\n"

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO('{"active":true,"metadata_by_name_map":{"source":"command"},"record_key":7,"title":"Stored"}'),
    )
    assert command.run([str(input_path), "upsert", "scalar_record"], CommandInput, table_by_name_map) == 0
    assert capsys.readouterr().out == (
        '{"active":true,"metadata_by_name_map":{"source":"command"},"record_key":7,"title":"Stored"}\n'
    )

    monkeypatch.setattr(sys, "stdin", io.StringIO('{"record_key":7}'))
    assert command.run([str(input_path), "get", "scalar_record"], CommandInput, table_by_name_map) == 0
    assert capsys.readouterr().out == (
        '{"active":true,"metadata_by_name_map":{"source":"command"},"record_key":7,"title":"Stored"}\n'
    )
    assert state_database_path_get(input_path.parent).exists()


def test_sqlite_state_command_rejects_valid_non_current_input_without_creating_database(tmp_path: Path) -> None:
    """Require the resolved command input artifact to be exactly `input.json`."""

    input_path = tmp_path / "uncontrolled.json"
    input_path.write_text(CommandInput(run_key="run").model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="input.json"):
        SqliteStateCommand().run([str(input_path), "initialize"], CommandInput, {SCALAR_TABLE.name: SCALAR_TABLE})

    assert not state_database_path_get(input_path.parent).exists()


@pytest.mark.parametrize(
    ("argument_list", "stdin_text"),
    (
        (("--database", "outside.sqlite3", "initialize"), ""),
        (("input.json", "unknown", "scalar_record"), ""),
        (("input.json", "list", "unknown_table"), ""),
        (("input.json", "get", "scalar_record"), '{"record_key":1,"title":"extra"}'),
        (("input.json", "delete", "scalar_record"), "{}"),
        (("input.json", "list-prefix", "scalar_record"), "{}"),
        (("input.json", "list-prefix", "scalar_record"), '{"title":"not-leading"}'),
    ),
    ids=(
        "arbitrary_path",
        "unknown_operation",
        "unknown_table",
        "extra_key",
        "missing_key",
        "empty_prefix",
        "nonleading_prefix",
    ),
)
def test_sqlite_state_command_rejects_unregistered_or_malformed_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argument_list: tuple[str, ...],
    stdin_text: str,
) -> None:
    """Reject requests that escape the fixed command and key contract."""

    input_path = tmp_path / "input.json"
    input_path.write_text(CommandInput(run_key="run").model_dump_json(), encoding="utf-8")
    resolved_argument_list = [str(input_path) if value == "input.json" else value for value in argument_list]
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))

    with pytest.raises((RuntimeError, ValueError, ValidationError)):
        SqliteStateCommand().run(resolved_argument_list, CommandInput, {SCALAR_TABLE.name: SCALAR_TABLE})
