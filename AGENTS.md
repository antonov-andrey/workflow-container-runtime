# Repository Guidelines

## Scope
- This repository owns reusable runtime code and runtime prompt resources for workflow-container projects.
- Shared workflow-container ecosystem authoring and code quality rules live in the `workflow-container-tools` plugin reference `references/workflow-container-authoring.md`.
- This repository must not contain domain-specific workflow logic, source-type logic, domain extraction logic, or concrete workflow-container project names.
- Concrete workflow-container projects depend on this repository at runtime through a pinned Python package dependency.
- Authoring tools belong to `workflow-container-tools`, not to this repository.
- This repository owns logical Playwright profile validation, run-local profile leasing, phase-specific MCP URL routing, concurrent lane assignment, and writeback-candidate calls.
- Browser/VPN stack ownership belongs to `browser-vpn-runtime`; this repository must not own browser process launch, OpenVPN, physical profile directories, profile copying, stealth, locale, viewport, or package-selection behavior.

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
