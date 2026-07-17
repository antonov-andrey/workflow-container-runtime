# workflow-container-runtime

Reusable optional implementation of `WorkflowSourceInterface` for workflow-container projects.

This project owns generic executable mechanics that concrete workflow containers use at runtime:

- base workflow and step lifecycles with deterministic recovery;
- Codex step execution and correction with explicit model and reasoning selection;
- atomic JSON artifacts and validated SQLite current state;
- schema-bound JSON output validation and result-content-and-revision-bound verification;
- external artifact-tree materialization;
- generic prompt resources;
- browser capability, error, and tool-contract checks.

It must not contain domain-specific workflow logic. Concrete workflow containers keep their own workflow subclasses, DBOS entrypoints, domain schemas, domain prompts, validators, handoff construction, and artifact semantics.

The package and the platform-owned base image built from it are implementation choices, not platform interface requirements. A `WorkflowSource` image may use this package, replace it with another implementation, or implement the interface without a shared runtime package. Images that use this package must pin its immutable released artifact; a platform build never adds a sibling repository as an additional Docker build context.

The platform-facing v1 boundary consists only of:

- the complete source-owned `command` from `workflow.yaml`;
- `WORKFLOW_RUN_ID`, `WORKFLOW_INPUT_PATH=/input/input.json`, `WORKFLOW_RUNTIME_PATH=/runtime`, `WORKFLOW_CONTROL_URL`, and `WORKFLOW_CAPABILITY_CONFIG_PATH=/input/capability.json`;
- immutable `/input`, private run-local `/runtime`, and declared user-mappable `/workspace` and `/result` roots;
- the versioned run-local HTTP control protocol for registration, safepoint publication, terminal reporting, and cancellation.

For a safepoint, the adapter sends the stable step identity, transition identity, and a canonical list of declared mount keys with safe mount-relative source paths. It does not send platform storage identity or an absolute storage path; the platform derives every destination scope from the immutable run snapshot and accepts the whole group atomically with step completion. Transition identity does not include request content, so a changed replay is rejected. An empty image list is valid; it changes no persistent mount unless the platform adds a policy-required capability candidate to the same step group.

The control adapter keeps the same idempotent operation pending and retries transport failures and HTTP `5xx` responses. Protocol rejections such as cancellation, stale fencing, invalid content, or identity conflicts remain concrete non-retryable `4xx` errors, so a temporary platform outage cannot become a persisted DBOS business failure while a replacement is still possible.

The platform may start the source-owned command in sequential replacement Jobs for the same durable run, but never concurrently. Before each replacement, the preceding execution is confirmed stopped and fenced out. If stop is not yet proven, the run stays working with replacement pending; reconciliation automatically continues immediately after proof without a user retry. The adapter receives the same `WORKFLOW_RUN_ID`, immutable input, and persistent `/runtime` state with a new current control proxy, resumes its DBOS state idempotently from the last accepted safepoint, and replays accepted or pending transitions without changing their identity. For terminal reporting, the adapter sends the open workflow result, stable transition identity, and canonical terminal publication list under the protocol's reserved terminal execution identity. After the control service durably records terminal intent and returns its receipt, the adapter exits without further business work and no replacement is allowed. The platform makes the result, terminal publications, and final run status visible together only after the exact current Job bound to that intent exits in the required state.

The platform does not append command-line arguments or assume DBOS, Codex, Python, SQLite, or any package-internal entrypoint. This package adapts those neutral inputs to its DBOS and Codex implementation. Package-specific environment variables, SQLite layout, recovery state, and process structure remain internal implementation details.

The platform-owned base image provides one tested implementation of the same adapter and common dependencies for first-party workflows. A source-owned Dockerfile may use it, use another base image, or build from scratch. Conformance is established only by the mandatory platform test suite injected into the exact built candidate image and, when declared, the separate publisher test command from `workflow.yaml`.

Mutable keyed collections use one current SQLite row per domain key. Concrete containers provide exact Pydantic row models and a static table registry; the runtime owns schema validation, ordinary and composite primary keys, short transactions, deterministic reads, idempotent upserts, and SQLite URI `mode=ro` downstream reads that cannot mutate declared artifacts. JSONL is reserved for immutable event or log streams and immutable fixtures.

## Development

```bash
uv venv --python 3.14
source .venv/bin/activate
uv pip install -e ".[test]"
python -m pytest -q
python -m compileall workflow_container_runtime
```
