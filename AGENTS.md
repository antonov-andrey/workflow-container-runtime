# Repository Guidelines

## Required Standards

- `project-standards:project-foundation` applies to all work in this repository.
- `project-standards:project-instruction-developer` applies to instruction artifacts.
- `project-standards:project-documentation-developer` applies to `DESIGN.md`.
- `project-standards:python-developer` and `project-standards:pytest-developer` apply to Python code and tests.
- `project-standards:docker-compose-developer` applies to container and base-image artifacts.
- `workflow-container-agent-tools:workflow-container-developer` applies to workflow-container runtime code, prompts, and integration.

If one required provider skill is unavailable, continue read-only discovery only and do not mutate this repository until the provider is restored.

Active task pairs live only under the ignored `.spec/` root.

## Scope
- This repository owns reusable runtime code and runtime prompt resources for workflow-container projects.
- Shared workflow-container ecosystem authoring and code quality rules belong to `workflow-container-agent-tools:workflow-container-developer`.
- This repository must not contain domain-specific workflow logic, source-type logic, domain extraction logic, or concrete workflow-container project names.
- Concrete workflow-container projects depend on this repository at runtime through a pinned Python package dependency.
- Authoring procedures belong to `workflow-container-agent-tools`, not to this repository.
- This repository owns logical Playwright profile validation, run-local profile leasing, phase-specific MCP URL routing, concurrent lane assignment, exact input-configured network-proxy lookup and route propagation, and writeback-candidate calls.
- Browser process ownership belongs to `browser-runtime`; this repository must not own browser process launch, physical profile directories, profile copying, stealth, locale, viewport, or package-selection behavior.
- VPN gateway, OpenVPN, SOCKS5, tunnel lifecycle, and leak prevention belong to `vpn-runtime`; this repository must not start or configure them.

## Python
- Python code uses Python 3.14.
- Python code must be formatted with Black using target version `py314` and line length `120`.
- Public API, stable runtime boundaries, and non-trivial modules must have docstrings that describe real behavior.
- Runtime configuration and runtime result objects must use strict Pydantic models when they carry stable field-like data.
- Tests must use `pytest`.
- Tests must not verify instruction artifacts by checking that specific prose, headings, phrases, examples, files, or placement rules exist or do not exist. Instruction artifacts are verified by semantic reread or semantic audit, not by pytest assertions over text or instruction artifact paths.

## Verification
- Run `python -m pytest -q` after Python behavior changes.
- Run `python -m compileall workflow_container_runtime` before handoff.
