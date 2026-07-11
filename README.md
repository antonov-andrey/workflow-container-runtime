# workflow-container-runtime

Reusable runtime package for workflow-container projects.

This project owns generic executable mechanics that concrete workflow containers use at runtime:

- base workflow and step lifecycles with deterministic recovery;
- Codex step execution and correction with explicit model and reasoning selection;
- atomic JSON artifacts and validated SQLite current state;
- schema-bound JSON output validation and result-content-and-revision-bound verification;
- external artifact-tree materialization;
- generic prompt resources;
- browser capability, error, and tool-contract checks.

It must not contain domain-specific workflow logic. Concrete workflow containers keep their own workflow subclasses, DBOS entrypoints, domain schemas, domain prompts, validators, handoff construction, and artifact semantics.

Mutable keyed collections use one current SQLite row per domain key. Concrete containers provide exact Pydantic row models and a static table registry; the runtime owns schema validation, ordinary and composite primary keys, short transactions, deterministic reads, idempotent upserts, and SQLite URI `mode=ro` downstream reads that cannot mutate declared artifacts. JSONL is reserved for immutable event or log streams and immutable fixtures.

## Development

```bash
uv venv --python 3.14
source .venv/bin/activate
uv pip install -e ".[test]"
python -m pytest -q
python -m compileall workflow_container_runtime
```
