# workflow-container-runtime

Reusable runtime package for workflow-container projects.

This project owns generic executable mechanics that concrete workflow containers use at runtime:

- Codex stage execution;
- schema-bound JSON output validation;
- generic prompt resources;
- generic artifact and browser-tool contract checks.

It must not contain domain-specific workflow logic. Concrete workflow containers keep their own DBOS workflow, domain schemas, domain prompts, validators and artifact semantics.

## Development

```bash
python -m pytest -q
python -m compileall workflow_container_runtime
```
