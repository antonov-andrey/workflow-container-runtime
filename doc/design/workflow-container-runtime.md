# Workflow Container Runtime

## Scope
This project owns generic executable runtime mechanics for workflow-container projects. Concrete workflow containers consume it as a pinned Python dependency.

The runtime owns `Codex` subprocess execution, structured JSON output schema handling, generic prompt resource loading, generic prompt partials, browser-tool event validation, and small generic artifact helpers needed by those mechanisms.

The runtime does not own `DBOS` workflow orchestration, domain schemas, domain validators, source-type behavior, domain extraction logic, browser/VPN process launch, OpenVPN, Playwright MCP server startup, or developer CLI tooling.

## Dependency Boundary
Concrete workflow-container projects import this package at runtime. This package must not import concrete workflow-container projects, `workflow-container-developer`, or domain workflow code.

`workflow-container-developer` owns authoring guidance and audits. `browser-vpn-runtime` owns the browser/VPN stack and exposes a configured Playwright MCP URL to workflow containers. This runtime package may pass that URL to Codex config, but it must not start or configure browser/VPN processes itself.

## Prompt Resource Boundary
Generic prompt partials live in this package under `workflow_container_runtime/prompt/template/`. Concrete workflow-container prompts may include them through the `runtime/` template prefix.

Concrete workflow-container projects own their full domain prompt templates and domain prompt partials. They must not keep local copies of runtime-owned generic partials.
