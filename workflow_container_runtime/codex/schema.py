"""JSON schema helpers for Codex structured output."""

from pydantic import BaseModel


def codex_output_schema_get(model_class: type[BaseModel]) -> dict[str, object]:
    """Return a strict JSON schema accepted by `codex exec --output-schema`.

    Args:
        model_class: Pydantic model class.

    Returns:
        Strict JSON schema.
    """
    schema = model_class.model_json_schema()
    _schema_strict_normalize(schema)
    return schema


def _schema_strict_normalize(schema: object) -> None:
    """Normalize one JSON schema tree in place for strict structured output.

    Args:
        schema: JSON schema node.
    """
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            schema["required"] = sorted(properties)
            schema["additionalProperties"] = False
        for value in schema.values():
            _schema_strict_normalize(value)
        return
    if isinstance(schema, list):
        for value in schema:
            _schema_strict_normalize(value)


__all__ = [
    "codex_output_schema_get",
]
