"""JSON artifact writer for deterministic workflow output files."""

import json
from pathlib import Path

from pydantic import BaseModel


class JsonArtifactWriter:
    """Write JSON artifacts with deterministic formatting."""

    def write(self, path: Path, payload: BaseModel | dict[str, object]) -> None:
        """Write one JSON artifact.

        Args:
            path: Artifact path to write.
            payload: Pydantic model or JSON-compatible dictionary payload.
        """

        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, BaseModel):
            json_payload = payload.model_dump(mode="json")
        else:
            json_payload = payload
        path.write_text(json.dumps(json_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
