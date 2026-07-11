"""Validated SQLite storage for mutable current-state records."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
import sys
from types import NoneType, UnionType
from typing import Iterator, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, model_validator

from workflow_container_runtime.model import model_snapshot_get
from workflow_container_runtime.step.file import (
    INPUT_FILENAME,
    STATE_DATABASE_FILENAME,
    input_path_get,
    state_database_path_get,
)

_IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
_SQLITE_INTEGER_TYPE = "INTEGER"
_SQLITE_REAL_TYPE = "REAL"
_SQLITE_TEXT_TYPE = "TEXT"


@dataclass(frozen=True)
class _SqliteStateColumn:
    """Describe one validated SQLite column derived from a model field."""

    name: str
    sqlite_type: str
    nullable: bool
    structured: bool
    scalar_type: type[object] | None


class SqliteStateTable[RecordT: BaseModel](BaseModel):
    """Bind one strict record model to its SQLite table and natural primary key."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True, extra="forbid", frozen=True, strict=True, validate_default=True
    )

    name: str
    primary_key_field_name_tuple: tuple[str, ...]
    record_model: type[RecordT]

    @model_validator(mode="after")
    def _contract_validate(self) -> SqliteStateTable[RecordT]:
        """Reject descriptors that cannot produce one safe exact schema."""

        if _IDENTIFIER_PATTERN.fullmatch(self.name) is None or self.name.startswith("sqlite_"):
            raise ValueError(f"SQLite table name is unsafe: {self.name}")
        if not self.primary_key_field_name_tuple:
            raise ValueError("SQLite table primary key must contain at least one field")
        if len(set(self.primary_key_field_name_tuple)) != len(self.primary_key_field_name_tuple):
            raise ValueError("SQLite table primary key fields must be unique")
        _record_model_contract_validate(self.record_model)
        for field_name in self.record_model.model_fields:
            if _IDENTIFIER_PATTERN.fullmatch(field_name) is None:
                raise ValueError(f"SQLite column name is unsafe: {field_name}")
            _literal_scalar_type_get(self.record_model.model_fields[field_name].annotation)
        for field_name in self.primary_key_field_name_tuple:
            field = self.record_model.model_fields.get(field_name)
            if field is None:
                raise ValueError(f"SQLite table primary key field is unknown: {field_name}")
            if _field_nullable(field.annotation):
                raise ValueError(f"SQLite table primary key field must not be optional: {field_name}")
            if _field_structured(field.annotation):
                raise ValueError(f"SQLite table primary key field must be scalar: {field_name}")
        return self


def _column_list_get(table: SqliteStateTable[BaseModel]) -> list[_SqliteStateColumn]:
    """Derive one ordered SQLite column contract from a record model.

    Args:
        table: Validated table descriptor.

    Returns:
        Ordered field-to-column contract.
    """

    return [
        _SqliteStateColumn(
            name=field_name,
            nullable=_field_nullable(field.annotation),
            scalar_type=_scalar_type_get(field.annotation),
            sqlite_type=_sqlite_type_get(field.annotation),
            structured=_field_structured(field.annotation),
        )
        for field_name, field in table.record_model.model_fields.items()
    ]


def _column_name_sql_get(
    table: SqliteStateTable[BaseModel],
    field_name_tuple: tuple[str, ...] | None = None,
) -> str:
    """Return comma-separated quoted column names for one declared field sequence.

    Args:
        table: Validated table descriptor.
        field_name_tuple: Optional declared field sequence.

    Returns:
        SQL column-name list.
    """

    return ", ".join(_identifier_quote(name) for name in (field_name_tuple or tuple(table.record_model.model_fields)))


def _primary_key_value_tuple_validate(
    table: SqliteStateTable[BaseModel],
    primary_key_value_tuple: tuple[object, ...],
    *,
    allow_prefix: bool,
) -> tuple[object, ...]:
    """Validate ordered complete or leading primary-key values.

    Args:
        table: Validated table descriptor.
        primary_key_value_tuple: Candidate key values in descriptor order.
        allow_prefix: Whether a non-empty leading prefix is valid.

    Returns:
        Validated values in the original key order.
    """

    primary_key_count = len(table.primary_key_field_name_tuple)
    if not primary_key_value_tuple:
        raise ValueError("SQLite primary key values must not be empty")
    if len(primary_key_value_tuple) > primary_key_count or (
        not allow_prefix and len(primary_key_value_tuple) != primary_key_count
    ):
        expected = f"at most {primary_key_count}" if allow_prefix else str(primary_key_count)
        raise ValueError(f"SQLite primary key expected {expected} values")
    validated_value_list: list[object] = []
    for field_name, value in zip(
        table.primary_key_field_name_tuple[: len(primary_key_value_tuple)],
        primary_key_value_tuple,
        strict=True,
    ):
        try:
            validated_value_list.append(
                TypeAdapter(table.record_model.model_fields[field_name].rebuild_annotation()).validate_python(
                    value,
                    strict=True,
                )
            )
        except ValidationError as exc:
            raise ValueError(f"SQLite primary key value is invalid: {field_name}") from exc
    return tuple(validated_value_list)


def _record_from_row_get[RecordT: BaseModel](table: SqliteStateTable[RecordT], row: tuple[object, ...]) -> RecordT:
    """Decode one SQLite row and rebuild the exact strict record model.

    Args:
        table: Validated table descriptor.
        row: SQLite values in declared field order.

    Returns:
        Independently validated exact record.
    """

    payload_by_field_name = {
        column.name: _value_model_get(value, column) for column, value in zip(_column_list_get(table), row, strict=True)
    }
    try:
        return table.record_model.model_validate_json(
            json.dumps(payload_by_field_name, separators=(",", ":"), sort_keys=True)
        )
    except ValidationError as exc:
        raise RuntimeError(f"SQLite state row does not satisfy {table.record_model.__name__}") from exc


def _table_schema_ensure(connection: sqlite3.Connection, table: SqliteStateTable[BaseModel]) -> None:
    """Create a missing table or validate its exact existing schema.

    Args:
        connection: Configured SQLite connection.
        table: Validated table descriptor.
    """

    table_kind = connection.execute("SELECT type FROM sqlite_master WHERE name = ?", (table.name,)).fetchone()
    if table_kind is None:
        column_list = _column_list_get(table)
        column_definition_sql = ", ".join(
            f"{_identifier_quote(column.name)} {column.sqlite_type}" + ("" if column.nullable else " NOT NULL")
            for column in column_list
        )
        connection.execute(
            f"CREATE TABLE {_identifier_quote(table.name)} ("
            f"{column_definition_sql}, PRIMARY KEY ({_column_name_sql_get(table, table.primary_key_field_name_tuple)})"
            ")"
        )
    elif table_kind[0] != "table":
        raise RuntimeError(f"SQLite table schema incompatible: {table.name} is not a table")
    _table_schema_validate(connection, table)


def _table_schema_validate(connection: sqlite3.Connection, table: SqliteStateTable[BaseModel]) -> None:
    """Require an existing table to match its descriptor column-for-column.

    Args:
        connection: Configured SQLite connection.
        table: Validated table descriptor.

    Raises:
        RuntimeError: If the registered table is missing or incompatible.
    """

    table_kind = connection.execute("SELECT type FROM sqlite_master WHERE name = ?", (table.name,)).fetchone()
    if table_kind is None:
        raise RuntimeError(f"SQLite state table is not initialized: {table.name}")
    if table_kind[0] != "table":
        raise RuntimeError(f"SQLite table schema incompatible: {table.name} is not a table")
    expected_column_info_list = [
        (
            index,
            column.name,
            column.sqlite_type,
            0 if column.nullable else 1,
            None,
            (
                table.primary_key_field_name_tuple.index(column.name) + 1
                if column.name in table.primary_key_field_name_tuple
                else 0
            ),
        )
        for index, column in enumerate(_column_list_get(table))
    ]
    actual_column_info_list = connection.execute(f"PRAGMA table_info({_identifier_quote(table.name)})").fetchall()
    if actual_column_info_list != expected_column_info_list:
        raise RuntimeError(f"SQLite table schema incompatible: {table.name}")


def _value_database_get(record: BaseModel, column: _SqliteStateColumn) -> object:
    """Encode one model field in its single declared SQLite column.

    Args:
        record: Exact validated record snapshot.
        column: Declared storage contract for one field.

    Returns:
        SQLite-native scalar or canonical JSON text.
    """

    value = record.model_dump(mode="json")[column.name]
    if value is None:
        return None
    if column.structured:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    if column.sqlite_type == _SQLITE_INTEGER_TYPE and isinstance(value, bool):
        return int(value)
    return value


def _value_model_get(value: object, column: _SqliteStateColumn) -> object:
    """Decode one SQLite column into a JSON-compatible model field value.

    Args:
        value: Raw SQLite value.
        column: Declared storage contract for one field.

    Returns:
        JSON-compatible field value for strict model validation.
    """

    if value is None:
        return None
    if column.structured:
        if not isinstance(value, str):
            raise RuntimeError(f"SQLite structured column is not text: {column.name}")
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"SQLite structured column is invalid JSON: {column.name}") from exc
    if column.sqlite_type == _SQLITE_INTEGER_TYPE and column.scalar_type is bool:
        if value not in (0, 1):
            raise RuntimeError(f"SQLite boolean column is invalid: {column.name}")
        return bool(value)
    return value


def _where_sql_get(field_name_tuple: tuple[str, ...]) -> str:
    """Return an exact primary-key predicate for declared field names.

    Args:
        field_name_tuple: Complete or leading primary-key field names.

    Returns:
        Parameterized SQL predicate.
    """

    return " AND ".join(f"{_identifier_quote(field_name)} = ?" for field_name in field_name_tuple)


class SqliteStateStore:
    """Store exact current records in one short-lived SQLite connection per operation."""

    def delete(
        self,
        path: Path,
        table: SqliteStateTable[BaseModel],
        primary_key_value_tuple: tuple[object, ...],
    ) -> None:
        """Delete one current record identified by its complete primary key.

        Args:
            path: Exact initialized state database path.
            table: Validated table descriptor.
            primary_key_value_tuple: Complete primary-key values in descriptor order.
        """

        primary_key_value_tuple = _primary_key_value_tuple_validate(table, primary_key_value_tuple, allow_prefix=False)
        with self._connection_get(path) as connection:
            _table_schema_validate(connection, table)
            where_sql = _where_sql_get(table.primary_key_field_name_tuple)
            with connection:
                connection.execute(
                    f"DELETE FROM {_identifier_quote(table.name)} WHERE {where_sql}",
                    primary_key_value_tuple,
                )

    def get[RecordT: BaseModel](
        self,
        path: Path,
        table: SqliteStateTable[RecordT],
        primary_key_value_tuple: tuple[object, ...],
    ) -> RecordT | None:
        """Return one current record for its complete natural key.

        Args:
            path: Exact initialized state database path.
            table: Validated table descriptor.
            primary_key_value_tuple: Complete primary-key values in descriptor order.

        Returns:
            Validated current record, or `None` when no row matches.
        """

        primary_key_value_tuple = _primary_key_value_tuple_validate(table, primary_key_value_tuple, allow_prefix=False)
        with self._connection_get(path) as connection:
            _table_schema_validate(connection, table)
            row = connection.execute(
                f"SELECT {_column_name_sql_get(table)} FROM {_identifier_quote(table.name)} "
                f"WHERE {_where_sql_get(table.primary_key_field_name_tuple)}",
                primary_key_value_tuple,
            ).fetchone()
        return None if row is None else _record_from_row_get(table, row)

    def initialize(self, path: Path, table_list: list[SqliteStateTable[BaseModel]]) -> None:
        """Create or validate every registered table in one state database.

        Args:
            path: Exact state database path to initialize.
            table_list: Complete static table registry.
        """

        if len({table.name for table in table_list}) != len(table_list):
            raise ValueError("SQLite state table names must be unique")
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection_get(path) as connection:
            with connection:
                for table in table_list:
                    _table_schema_ensure(connection, table)

    def list[RecordT: BaseModel](self, path: Path, table: SqliteStateTable[RecordT]) -> list[RecordT]:
        """Return all current rows in complete primary-key order.

        Args:
            path: Exact initialized state database path.
            table: Validated table descriptor.

        Returns:
            Validated current rows in deterministic key order.
        """

        with self._connection_get(path) as connection:
            _table_schema_validate(connection, table)
            row_list = connection.execute(
                f"SELECT {_column_name_sql_get(table)} FROM {_identifier_quote(table.name)} "
                f"ORDER BY {_column_name_sql_get(table, table.primary_key_field_name_tuple)}"
            ).fetchall()
        return [_record_from_row_get(table, row) for row in row_list]

    def list_by_primary_key_prefix[RecordT: BaseModel](
        self,
        path: Path,
        table: SqliteStateTable[RecordT],
        primary_key_value_prefix_tuple: tuple[object, ...],
    ) -> list[RecordT]:
        """Return rows matching one non-empty leading primary-key prefix.

        Args:
            path: Exact initialized state database path.
            table: Validated table descriptor.
            primary_key_value_prefix_tuple: Leading primary-key values in descriptor order.

        Returns:
            Validated matching rows in complete primary-key order.
        """

        primary_key_value_prefix_tuple = _primary_key_value_tuple_validate(
            table,
            primary_key_value_prefix_tuple,
            allow_prefix=True,
        )
        primary_key_field_name_tuple = table.primary_key_field_name_tuple[: len(primary_key_value_prefix_tuple)]
        with self._connection_get(path) as connection:
            _table_schema_validate(connection, table)
            row_list = connection.execute(
                f"SELECT {_column_name_sql_get(table)} FROM {_identifier_quote(table.name)} "
                f"WHERE {_where_sql_get(primary_key_field_name_tuple)} "
                f"ORDER BY {_column_name_sql_get(table, table.primary_key_field_name_tuple)}",
                primary_key_value_prefix_tuple,
            ).fetchall()
        return [_record_from_row_get(table, row) for row in row_list]

    def upsert[RecordT: BaseModel](
        self,
        path: Path,
        table: SqliteStateTable[RecordT],
        record: RecordT,
    ) -> RecordT:
        """Insert or atomically replace one current record through its natural key.

        Args:
            path: Exact initialized state database path.
            table: Validated table descriptor.
            record: Exact strict record model to persist.

        Returns:
            Independently validated persisted record snapshot.
        """

        if type(record) is not table.record_model:
            raise TypeError(f"SQLite state record must use exact model: {table.record_model.__name__}")
        record = model_snapshot_get(record)
        column_list = _column_list_get(table)
        column_name_sql = _column_name_sql_get(table)
        placeholder_sql = ", ".join("?" for _ in column_list)
        primary_key_sql = _column_name_sql_get(table, table.primary_key_field_name_tuple)
        non_primary_key_column_list = [
            column for column in column_list if column.name not in table.primary_key_field_name_tuple
        ]
        if non_primary_key_column_list:
            conflict_sql = f"ON CONFLICT ({primary_key_sql}) DO UPDATE SET " + ", ".join(
                f"{_identifier_quote(column.name)} = excluded.{_identifier_quote(column.name)}"
                for column in non_primary_key_column_list
            )
        else:
            conflict_sql = f"ON CONFLICT ({primary_key_sql}) DO NOTHING"
        value_tuple = tuple(_value_database_get(record, column) for column in column_list)
        primary_key_value_tuple = tuple(
            getattr(record, field_name) for field_name in table.primary_key_field_name_tuple
        )
        with self._connection_get(path) as connection:
            with connection:
                _table_schema_validate(connection, table)
                existing_row = connection.execute(
                    f"SELECT {_column_name_sql_get(table)} FROM {_identifier_quote(table.name)} "
                    f"WHERE {_where_sql_get(table.primary_key_field_name_tuple)}",
                    primary_key_value_tuple,
                ).fetchone()
                if existing_row is not None:
                    existing_record = _record_from_row_get(table, existing_row)
                    if existing_record == record:
                        return existing_record
                connection.execute(
                    f"INSERT INTO {_identifier_quote(table.name)} ({column_name_sql}) VALUES ({placeholder_sql}) {conflict_sql}",
                    value_tuple,
                )
        return record

    @contextmanager
    def _connection_get(self, path: Path) -> Iterator[sqlite3.Connection]:
        """Open one configured short-lived SQLite connection.

        Args:
            path: Exact database file path.

        Yields:
            Configured SQLite connection.
        """

        connection = sqlite3.connect(path)
        try:
            journal_mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
            if journal_mode is None or journal_mode[0].lower() != "delete":
                raise RuntimeError("SQLite state database must use journal_mode=DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()


class SqliteStateReader:
    """Read validated current-state rows through SQLite URI read-only connections."""

    def list[RecordT: BaseModel](self, path: Path, table: SqliteStateTable[RecordT]) -> list[RecordT]:
        """Return all validated rows in complete primary-key order without database mutation.

        Args:
            path: Existing state database path.
            table: Validated table descriptor.

        Returns:
            Validated current rows in deterministic key order.
        """

        with self._connection_get(path) as connection:
            _table_schema_validate(connection, table)
            row_list = connection.execute(
                f"SELECT {_column_name_sql_get(table)} FROM {_identifier_quote(table.name)} "
                f"ORDER BY {_column_name_sql_get(table, table.primary_key_field_name_tuple)}"
            ).fetchall()
        return [_record_from_row_get(table, row) for row in row_list]

    @contextmanager
    def _connection_get(self, path: Path) -> Iterator[sqlite3.Connection]:
        """Open one SQLite URI connection that cannot modify the declared database.

        Args:
            path: Existing state database path.

        Yields:
            SQLite read-only connection.
        """

        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            yield connection
        finally:
            connection.close()


class SqliteStateCommand:
    """Run the fixed stdin/stdout state protocol for one container table registry."""

    def run(
        self,
        argument_list: list[str],
        input_model: type[BaseModel],
        table_by_name_map: dict[str, SqliteStateTable[BaseModel]],
    ) -> int:
        """Validate the current input and execute one fixed state operation.

        Args:
            argument_list: `<input-path> <operation> [table-name]` command arguments.
            input_model: Exact model that owns the current `input.json` contract.
            table_by_name_map: Static registry of allowed tables by exact table name.

        Returns:
            Zero after writing one compact JSON response.
        """

        if len(argument_list) < 2:
            raise ValueError("SQLite state command requires input path and operation")
        input_path = Path(argument_list[0]).resolve()
        self._input_validate(input_path, input_model)
        operation = argument_list[1]
        if operation == "initialize":
            if len(argument_list) != 2:
                raise ValueError("SQLite initialize does not accept a table or path option")
            SqliteStateStore().initialize(state_database_path_get(input_path.parent), list(table_by_name_map.values()))
            self._json_write(None)
            return 0
        if operation not in {"upsert", "get", "list", "list-prefix", "delete"}:
            raise ValueError(f"SQLite state operation is unknown: {operation}")
        if len(argument_list) != 3:
            raise ValueError("SQLite state operation requires exactly one registered table name")
        table = table_by_name_map.get(argument_list[2])
        if table is None:
            raise ValueError(f"SQLite state table is unknown: {argument_list[2]}")
        store = SqliteStateStore()
        path = state_database_path_get(input_path.parent)
        if operation == "upsert":
            self._json_write(store.upsert(path, table, self._record_get(table)))
            return 0
        if operation == "get":
            self._json_write(store.get(path, table, self._primary_key_value_tuple_get(table, prefix=False)))
            return 0
        if operation == "list":
            self._json_write(store.list(path, table))
            return 0
        if operation == "list-prefix":
            self._json_write(
                store.list_by_primary_key_prefix(path, table, self._primary_key_value_tuple_get(table, prefix=True))
            )
            return 0
        store.delete(path, table, self._primary_key_value_tuple_get(table, prefix=False))
        self._json_write(None)
        return 0

    def _input_validate(self, input_path: Path, input_model: type[BaseModel]) -> None:
        """Require one current exact public input artifact.

        Args:
            input_path: Candidate public input artifact path.
            input_model: Exact model that owns the input contract.
        """

        if input_path != input_path_get(input_path.parent):
            raise ValueError(f"SQLite state command input must resolve to {INPUT_FILENAME}: {input_path}")
        try:
            input_model.model_validate_json(input_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise RuntimeError(f"SQLite state command input is invalid: {input_path}") from exc

    def _json_object_get(self) -> dict[str, object]:
        """Read exactly one JSON object from standard input.

        Returns:
            Parsed JSON object.
        """

        try:
            value = json.loads(sys.stdin.read())
        except json.JSONDecodeError as exc:
            raise ValueError("SQLite state command stdin must contain JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("SQLite state command stdin must contain one JSON object")
        return value

    def _json_write(self, value: BaseModel | list[BaseModel] | None) -> None:
        """Write one compact JSON response to standard output.

        Args:
            value: Optional model or model list response.
        """

        if isinstance(value, BaseModel):
            payload: object = value.model_dump(mode="json")
        elif isinstance(value, list):
            payload = [record.model_dump(mode="json") for record in value]
        else:
            payload = None
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")

    def _primary_key_value_tuple_get(
        self,
        table: SqliteStateTable[BaseModel],
        *,
        prefix: bool,
    ) -> tuple[object, ...]:
        """Validate an exact full or leading primary-key object from stdin.

        Args:
            table: Validated table descriptor.
            prefix: Whether a non-empty leading key prefix is expected.

        Returns:
            Ordered complete or leading primary-key values.
        """

        value_by_field_name = self._json_object_get()
        key_field_name_tuple = table.primary_key_field_name_tuple
        if prefix:
            if not value_by_field_name:
                raise ValueError("SQLite state primary-key prefix must not be empty")
            expected_field_name_tuple = key_field_name_tuple[: len(value_by_field_name)]
        else:
            expected_field_name_tuple = key_field_name_tuple
        if set(value_by_field_name) != set(expected_field_name_tuple):
            raise ValueError("SQLite state primary-key object fields are invalid")
        return _primary_key_value_tuple_validate(
            table,
            tuple(value_by_field_name[field_name] for field_name in expected_field_name_tuple),
            allow_prefix=prefix,
        )

    def _record_get[RecordT: BaseModel](self, table: SqliteStateTable[RecordT]) -> RecordT:
        """Validate one complete record object from stdin.

        Args:
            table: Validated table descriptor.

        Returns:
            Exact strict row model.
        """

        try:
            return table.record_model.model_validate(self._json_object_get())
        except ValidationError as exc:
            raise ValueError(f"SQLite state row is invalid for {table.name}") from exc


def _field_nullable(annotation: object) -> bool:
    """Return whether one Pydantic field annotation permits `None`.

    Args:
        annotation: Field annotation supplied by Pydantic.

    Returns:
        Whether `None` is one allowed value.
    """

    return annotation is NoneType or NoneType in get_args(annotation)


def _field_structured(annotation: object) -> bool:
    """Return whether one field uses JSON rather than a native SQLite scalar.

    Args:
        annotation: Field annotation supplied by Pydantic.

    Returns:
        Whether the field needs canonical JSON storage.
    """

    field_type = _field_type_get(annotation)
    if _literal_scalar_type_get(annotation) is not None:
        return False
    return get_origin(field_type) is not None or (isinstance(field_type, type) and issubclass(field_type, BaseModel))


def _field_type_get(annotation: object) -> object:
    """Remove an optional wrapper while retaining the one concrete field type.

    Args:
        annotation: Field annotation supplied by Pydantic.

    Returns:
        Non-null concrete field type.
    """

    if get_origin(annotation) not in {Union, UnionType}:
        return annotation
    argument_tuple = tuple(argument for argument in get_args(annotation) if argument is not NoneType)
    return argument_tuple[0] if len(argument_tuple) == 1 else annotation


def _literal_scalar_type_get(annotation: object) -> type[object] | None:
    """Return the native scalar type for one homogeneous `Literal` annotation.

    Args:
        annotation: Field annotation supplied by Pydantic.

    Returns:
        Homogeneous literal scalar type, or `None` for non-literal annotations.

    Raises:
        ValueError: If the literal values do not share one supported SQLite scalar type.
    """

    field_type = _field_type_get(annotation)
    if get_origin(field_type) is not Literal:
        return None
    literal_type_set = {type(value) for value in get_args(field_type)}
    if len(literal_type_set) != 1 or not literal_type_set <= {bool, float, int, str}:
        raise ValueError("SQLite literal fields must use one homogeneous supported scalar type")
    return next(iter(literal_type_set))


def _scalar_type_get(annotation: object) -> type[object] | None:
    """Return one native SQLite scalar type when a field has one.

    Args:
        annotation: Field annotation supplied by Pydantic.

    Returns:
        Native scalar type, or `None` when the field is structured or text-like.
    """

    literal_scalar_type = _literal_scalar_type_get(annotation)
    if literal_scalar_type is not None:
        return literal_scalar_type
    field_type = _field_type_get(annotation)
    return field_type if field_type in {bool, float, int, str} else None


def _identifier_quote(identifier: str) -> str:
    """Quote one descriptor-validated SQLite identifier.

    Args:
        identifier: Descriptor-validated lower-snake-case name.

    Returns:
        SQL identifier literal.
    """

    return f'"{identifier}"'


def _record_model_contract_validate(record_model: type[BaseModel]) -> None:
    """Require a strict closed row model for durable current-state records.

    Args:
        record_model: Candidate Pydantic row model.
    """

    if record_model.model_config.get("strict") is not True:
        raise ValueError(f"SQLite state record model must use strict=True: {record_model.__name__}")
    if record_model.model_config.get("extra") != "forbid":
        raise ValueError(f"SQLite state record model must use extra='forbid': {record_model.__name__}")
    if record_model.model_config.get("validate_assignment") is not True:
        raise ValueError(f"SQLite state record model must use validate_assignment=True: {record_model.__name__}")
    if record_model.model_config.get("validate_default") is not True:
        raise ValueError(f"SQLite state record model must use validate_default=True: {record_model.__name__}")


def _sqlite_type_get(annotation: object) -> str:
    """Map one field contract to its exact SQLite storage affinity.

    Args:
        annotation: Field annotation supplied by Pydantic.

    Returns:
        Exact SQLite column type declaration.
    """

    scalar_type = _scalar_type_get(annotation)
    if scalar_type in {bool, int}:
        return _SQLITE_INTEGER_TYPE
    if scalar_type is float:
        return _SQLITE_REAL_TYPE
    return _SQLITE_TEXT_TYPE
