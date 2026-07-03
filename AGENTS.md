# Repository Guidelines

## Scope
- This repository owns reusable runtime code and runtime prompt resources for workflow-container projects.
- This repository must not contain domain-specific workflow logic, source-type logic, domain extraction logic, or concrete workflow-container project names.
- Concrete workflow-container projects depend on this repository at runtime through a pinned Python package dependency.
- Developer-only authoring tools belong to `workflow-container-developer`, not to this repository.
- Browser/VPN stack ownership belongs to `browser-vpn-runtime`; this repository may only receive a configured browser runtime `MCP` URL from callers.

## Python
- Python code uses Python 3.14.
- Every Python module, class, and function must have a docstring.
- Runtime configuration and runtime result objects must use strict Pydantic models when they carry stable field-like data.
- Tests must use `pytest`.

## Verification
- Run `python -m pytest -q` after Python behavior changes.
- Run `python -m compileall workflow_container_runtime` before handoff.
