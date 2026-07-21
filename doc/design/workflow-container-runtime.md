# Workflow Container Runtime

## Scope
This project owns generic executable runtime mechanics for workflow-container projects. Concrete workflow containers consume it as a pinned Python dependency.

The runtime owns the generic `WorkflowBase`, `WorkflowStepBase`, deterministic-step and Codex-step lifecycles, standard workflow and step file paths, recovery state machines, `Codex` subprocess execution, structured JSON output schema handling, generic prompt resources, browser-tool event validation, atomic JSON publication, validated SQLite current state, and source-neutral external artifact-tree materialization. It also owns logical Playwright profile validation, phase-specific MCP URL routing, fixed concurrent profile lanes, and an exclusive lease scoped by the run-local MCP router URL without query or fragment plus the physical profile. Query parameters do not create a second lease identity. Invocations with distinct profiles or distinct run-local endpoints remain concurrent.

The runtime does not own concrete `DBOS` workflow topology, domain input/result/state schemas, domain validators, domain handoff construction, source behavior, extraction logic, browser/VPN process launch, OpenVPN, Playwright MCP server startup, or authoring CLI tooling.

Shared workflow-container ecosystem authoring and code quality rules live in the `workflow-container-tools` plugin reference `references/workflow-container-authoring.md`; this document owns only runtime-specific boundaries.

## Dependency Boundary
Concrete workflow-container projects import this package at runtime and inherit its workflow and step base classes. This package imports runtime-neutral source and result contracts from `workflow-container-contract`; it must not import concrete workflow-container projects, `workflow-container-tools`, or domain workflow code.

`workflow-container-tools` owns authoring guidance and audits. `browser-vpn-runtime` owns browser/VPN processes, physical profile directories, copying, reset, and snapshots and exposes configured run-local Playwright MCP and candidate URLs to workflow containers. This runtime package builds logical profile routes and leases around those URLs, but it must not start or configure browser/VPN processes itself.

## Platform Adapter Boundary
`WorkflowPlatformRuntimeConfig` is the single environment adapter for `WorkflowSourceInterface` major 2. It loads the exact immutable `WorkflowRunContext` from `WORKFLOW_RUN_CONTEXT_PATH`, verifies its run identity against `WORKFLOW_RUN_ID`, and exposes the provenance model instead of duplicating its fields in package-local config.

`WorkflowDataPath` is the immutable pair of distinct absolute `/result` and `/workspace` roots carried by `WorkflowExecutionContext` and `WorkflowStepExecutionContext`. Concrete workflows derive their source-owned Data layouts from that pair; runtime-owned workflow artifacts remain below the separate `result_dir` rooted in `/runtime`.

`WorkflowControlRequestBuilder` resolves only source-declared `data.run` templates. It creates `WorkflowControlManifestRequest` values after checking the exact placeholder set, binds each safepoint to one declared `step_key` plus its dynamic `step_identity`, and never accepts a physical destination, Data owner, provider identity, or absolute storage path.

`WorkflowControlClient.safepoint_send(...)` is the synchronous accepted-checkpoint and optional Athena-projection barrier. A successful return proves acceptance of the requested manifests, the complete automatic `/runtime` checkpoint, and step completion. `final_send(...)` persists end-of-work intent through the `final` operation; concrete run states remain `done`, `failed`, or `cancelled`.

The package does not upload Data or implement platform storage. Accepted-state restore is represented by the `/runtime` tree materialized before command startup and by idempotent replay of the same control transition identities. `JsonLinesArtifactWriter` provides atomic publication of ordered validated model rows, but dataset schema ownership and row semantics remain with the concrete workflow source.

## Prompt Resource Boundary
Generic prompt partials live in this package under `workflow_container_runtime/prompt/template/`. Concrete workflow-container prompts may include them through the `runtime/` template prefix.

The `runtime/` prefix is a protected loader namespace. Project template trees cannot shadow it, and runtime system prompts are loaded through `runtime/system/...` names. Unprefixed template names belong to the concrete project.

Concrete workflow-container projects own their full domain prompt templates and domain prompt partials. They must not keep local copies of runtime-owned generic partials.

## Codex Execution Boundary
`CodexRunner` requires one immutable per-call `CodexRunnerConfig` with an explicit model and reasoning effort. The exact selected step config in the complete public workflow input owns both values. `WorkflowStepCodexBase` rebuilds the same call config for every action and verifier invocation while ignoring user-local Codex configuration, so behavior cannot depend on a hidden CLI default or constructor-owned run setting. The composition root injects only the reusable runner and source-owned runtime policy; it does not choose or retain run-owned model settings.

## Verification Boundary
Semantic verification returns a transient `VerificationDecision` with only `status` and `feedback_list`. The runtime binds that decision to the canonical validated result and one result publication revision, then publishes the required SHA-256 `result_digest` and `result_revision_index` in `VerificationResult`. Codex never supplies either identity field.

The digest covers only canonical result content. Workflow and deterministic results use revision `1`; Codex results use the current `attempt_index`. Recovery accepts a persisted verdict only when both fields match. This rejects a previous-attempt verdict even when the new attempt produced identical result bytes but changed artifacts or private state. In `ready`, that ambiguity is probed through current validation and semantic verification: success accepts the current revision, while failure runs the already-open current action attempt.

## Persistence Integrity Boundary
Before JSON publication, SQLite writes, or result digest calculation, the runtime rebuilds and validates an exact model snapshot so in-place mutation of nested values cannot bypass Pydantic assignment validation.

An absent `input.json` may be created only for an empty new workflow or step instance. Later lifecycle files, diagnostics, or artifacts without input are an identity error. Initial Codex `state.json` may be created only when the instance contains its validated input and no later lifecycle data; otherwise the missing state is inconsistent and must not reset the attempt index or retry budget.

External artifact materialization prevalidates the complete selected source tree before copying. It rejects a configured source root symlink, every symlink from that root through the current step path, descendant symlinks, path escapes, and root targets owned by the runtime (`input.json`, `result.json`, `state.json`, `state.sqlite3`, `verification.json`, and `diagnostics/`) without partially copying safe siblings. Accepted files replace targets atomically.

## Codex Browser Step Boundary
Browser-step system prompts and runtime prompt partials require Codex internal web search for search queries. Playwright MCP browser tools must not open public search-engine result pages. Browser tools are reserved for target source pages selected from internal search results, site navigation, saved evidence, or step input and declared step artifacts.

The runtime owns the generic `BrowsingError` and `BrowserActionResult` payloads but does not own concrete domain result schemas. Browser-backed action steps that open target URLs expose `browsing_error_list: list[BrowsingError]`, where every item contains one exact non-empty `url` and `error`. The concrete public step result preserves that list so network failures remain visible at the public boundary.

Connection-level browser navigation failure before a source response is a recoverable access condition, not source-content evidence. Runtime prompt resources require one delayed retry after `browser_close` discards the failed Chromium network context. A second failure receives one longer delayed retry after another context reset. Both retries reopen the same target inside the current action attempt; they do not restart the workflow step. Every observed failure remains a structured browsing error for the affected URL, and exhausted recovery cannot become source rejection or content absence.

## Incremental SQLite Boundary
Mutable keyed collections use the standard sibling database `state.sqlite3`. `state_database_path_get(...)` is the only owner of that path. A database remains private while only its current workflow or step owner reads it; when downstream code needs it, the current result exposes the database as a declared artifact without copying its rows.

`SqliteStateTable` binds one SQLite table name, one exact Pydantic row model, and an ordered non-empty primary-key field tuple. A naturally compound identity uses a composite primary key and never adds a concatenated surrogate key column. `SqliteStateStore` validates the current static schema, rebuilds exact model snapshots, uses `journal_mode=DELETE` and `synchronous=FULL`, performs one short transaction per connection, returns rows in primary-key order, supports exact full-key and non-empty leading-key-prefix reads, and provides idempotent current-row upsert without revision history. `SqliteStateReader` opens declared existing databases only with SQLite URI `mode=ro`, then validates schema and selects rows without connection-setting pragmas or writes. WAL is forbidden because a declared database artifact must remain one self-contained file.

`SqliteStateCommand` accepts the current public `input.json`, derives only its sibling database, and resolves one table from the concrete container's static registry. Codex may submit a validated row or primary-key object and invoke only the declared upsert, get, list, or delete operation. It cannot provide raw SQL, an arbitrary database path, an unregistered table, or transaction control.

Downstream read-only commands resolve a declared database artifact only from their own validated input and a concrete-container selector. They reuse the runtime store for schema validation and deterministic reads, cannot mutate the previous owner's database, and do not persist copied query rows as a second handoff model or artifact.

Once the owning result is successfully verified, its declared database is immutable. Only the owning action may update it before successful verification of the current attempt; all downstream access is read-only.

JSONL remains valid only for immutable event or log streams and immutable fixtures. It is not a workflow state, worklist, inventory, FSM, or mutable current-state format.
