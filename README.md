# workflow-container-runtime

Reusable optional implementation of `WorkflowSourceInterface` for workflow-container projects.

This project owns generic executable mechanics that concrete workflow containers use at runtime:

- base workflow and step lifecycles with deterministic recovery;
- Codex step execution and correction with explicit model and reasoning selection;
- atomic JSON and JSON Lines artifacts and validated SQLite current state;
- schema-bound JSON output validation and result-content-and-revision-bound verification;
- external artifact-tree materialization;
- generic prompt resources;
- browser capability, error, and tool-contract checks.

It must not contain domain-specific workflow logic. Concrete workflow containers keep their own workflow subclasses, DBOS entrypoints, domain schemas, domain prompts, validators, handoff construction, and artifact semantics.

The package and the platform-owned base image built from it are implementation choices, not platform interface requirements. A `WorkflowSource` image may use this package, replace it with another implementation, or implement the interface without a shared runtime package. Images that use this package must pin its immutable released artifact; a platform build never adds a sibling repository as an additional Docker build context.

The platform-facing major-2 boundary consists only of:

- the complete source-owned `command` from `workflow.yaml`;
- `WORKFLOW_RUN_ID`, `WORKFLOW_INPUT_PATH=/input/input.json`, `WORKFLOW_RUN_CONTEXT_PATH=/input/run-context.json`, `WORKFLOW_RUNTIME_PATH=/runtime`, `WORKFLOW_CONTROL_URL`, and `WORKFLOW_CAPABILITY_CONFIG_PATH=/input/capability.json`;
- immutable `/input`, complete accepted checkpoint `/runtime`, writable `/workspace` and `/result` roots, and attempt-local `/tmp` excluded from accepted Data;
- source-owned Data, secret, dataset, and step declarations imported from the exact `workflow.yaml`;
- the versioned run-local HTTP control protocol for registration, safepoints, final reporting, and cancellation.

`WorkflowPlatformRuntimeConfig` loads and validates the immutable `WorkflowRunContext`, checks that its 17-digit run id matches `WORKFLOW_RUN_ID`, and exposes the exact provenance values to the concrete workflow. `WorkflowDataPath` carries the distinct absolute `/result` and `/workspace` roots through every workflow and step context. `WorkflowControlRequestBuilder` creates manifest, safepoint, and final requests only from the exact source definition; it requires declared manifest keys, exact safe parameter sets, and a declared `step_key`. The source never supplies a Data owner, provider key, destination, or absolute storage path.

For a safepoint, the adapter sends the source-declared `step_key`, stable dynamic step identity, transition identity, and canonical `manifest_request_list`. The synchronous `204` response is the checkpoint barrier: all requested trees, complete `/runtime`, step state, dataset validation, and any source-required Athena projection wait are accepted before the call returns. Transition identity does not include request content, so a changed replay is rejected. An empty manifest list is valid and still checkpoints `/runtime` and completes the step transition.

The control adapter keeps the same idempotent operation pending and retries transport failures and HTTP `5xx` responses. Protocol rejections such as cancellation, stale fencing, invalid content, or identity conflicts remain concrete non-retryable `4xx` errors, so a temporary platform outage cannot become a persisted DBOS business failure while a replacement is still possible.

The platform may start the source-owned command in sequential replacement Jobs for the same durable run, but never concurrently. A replacement row and suspended Job may already exist while the preceding execution is stopping; activation waits for stop proof. The adapter receives the same immutable context and latest accepted `/runtime` with a new current control proxy, resumes DBOS state idempotently, and replays accepted or pending transitions without changing their identities. For final reporting, the adapter sends the open workflow result, stable transition identity, and canonical manifest list. After the control service durably records end-of-work intent and returns its receipt, the adapter exits without further domain work. The platform accepts the result, manifests, checkpoint, and concrete run state together only after the compatible exact Job exit.

The platform does not append command-line arguments or assume DBOS, Codex, Python, SQLite, or any package-internal entrypoint. This package adapts those neutral inputs to its DBOS and Codex implementation. Package-specific environment variables, SQLite layout, recovery state, and process structure remain internal implementation details.

The platform-owned base image provides one tested implementation of the same adapter and common dependencies for first-party workflows. A source-owned Dockerfile may use it, use another base image, or build from scratch. Conformance is established only by the mandatory platform test suite injected into the exact built candidate image and, when declared, the separate publisher test command from `workflow.yaml`.

Mutable keyed collections use one current SQLite row per domain key. Concrete containers provide exact Pydantic row models and a static table registry; the runtime owns schema validation, ordinary and composite primary keys, short transactions, deterministic reads, idempotent upserts, and SQLite URI `mode=ro` downstream reads that cannot mutate declared artifacts. `JsonLinesArtifactWriter` atomically publishes ordered validated rows for queryable datasets and other immutable streams; JSON Lines is not mutable workflow state.

## Development

```bash
uv venv --python 3.14
source .venv/bin/activate
uv pip install -e ".[test]"
python -m pytest -q
python -m compileall workflow_container_runtime
```
