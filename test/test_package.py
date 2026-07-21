"""Package smoke tests."""

from pathlib import Path
import importlib.metadata
import tomllib

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import workflow_container_runtime
from workflow_container_runtime.model import strict_model_contract_validate
from workflow_container_runtime.step import BrowserActionResult, BrowsingError
from workflow_container_runtime.step import (
    WorkflowStepCodexConcurrentConfigBase,
    WorkflowStepCodexConfigBase,
    WorkflowStepCodexRuntimePolicy,
)
from workflow_container_runtime.verification import VerificationDecision, VerificationResult
from workflow_container_runtime.workflow import WorkflowConfigBase, WorkflowInputBase


def test_setuptools_package_discovery_excludes_tests() -> None:
    """Package only runtime modules, not repository tests."""

    pyproject_payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    package_find_payload = pyproject_payload["tool"]["setuptools"]["packages"]["find"]

    assert package_find_payload["include"] == ["workflow_container_runtime*"]


def test_package_root_does_not_duplicate_distribution_version() -> None:
    """Keep distribution versioning in installed package metadata only."""

    assert importlib.metadata.version("workflow-container-runtime") == "0.6.3"
    assert not hasattr(workflow_container_runtime, "__version__")
    assert "__version__" not in workflow_container_runtime.__all__


def test_browsing_error_validates_exact_text_fields() -> None:
    """Reject empty or padded browser error fields."""

    browsing_error = BrowsingError(error="timeout", url="https://example.test")

    assert browsing_error.error == "timeout"
    assert browsing_error.url == "https://example.test"

    with pytest.raises(ValueError, match="browsing error fields must be non-empty and trimmed"):
        BrowsingError(error=" ", url="https://example.test")


def test_browser_action_result_has_generic_error_list() -> None:
    """Validate generic browser-only action result payload."""

    result = BrowserActionResult(browsing_error_list=[BrowsingError(error="blocked", url="https://example.test")])

    assert result.model_dump(mode="json") == {
        "browsing_error_list": [{"error": "blocked", "url": "https://example.test"}]
    }


def test_runtime_result_models_require_explicit_list_fields() -> None:
    """Reject omitted verdict feedback and browser failure lists at the runtime boundary."""

    with pytest.raises(ValidationError):
        BrowserActionResult()
    with pytest.raises(ValidationError):
        VerificationDecision(status="success")
    with pytest.raises(ValidationError):
        VerificationResult(status="success", feedback_list=[])


def test_workflow_input_and_codex_step_config_require_every_public_field() -> None:
    """Keep schema defaults as annotations while runtime config remains explicit."""

    class ExampleRequest(BaseModel):
        """Provide the public workflow request contract."""

        model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

        value: str

    class ExampleWorkflowConfig(WorkflowConfigBase):
        """Provide one exact workflow-level config contract."""

        model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

        step_map: dict[str, WorkflowStepCodexConfigBase]

    class ExampleWorkflowInput(WorkflowInputBase[ExampleRequest, ExampleWorkflowConfig]):
        """Bind the request and config into the public workflow input."""

        model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    with pytest.raises(ValidationError):
        WorkflowStepCodexConfigBase()
    with pytest.raises(ValidationError):
        ExampleWorkflowInput()
    with pytest.raises(ValidationError):
        WorkflowStepCodexConfigBase(
            correction_attempt_limit=0,
            instruction="",
            mcp_playwright_profile=None,
            mcp_playwright_profile_source=None,
            model="gpt-5.6-terra",
            reasoning_effort="ultra",
        )

    schema = WorkflowStepCodexConfigBase.model_json_schema()
    assert schema["properties"]["model"]["default"] == "gpt-5.6-terra"
    assert schema["properties"]["reasoning_effort"]["default"] == "high"
    assert set(schema["required"]) == {
        "correction_attempt_limit",
        "instruction",
        "mcp_playwright_profile",
        "mcp_playwright_profile_source",
        "model",
        "reasoning_effort",
    }
    assert WorkflowStepCodexRuntimePolicy.model_fields.keys() == {
        "artifact_materialization_policy",
        "execution_retry_policy",
    }
    assert issubclass(WorkflowStepCodexConcurrentConfigBase, WorkflowStepCodexConfigBase)


def test_verification_result_binds_decision_to_exact_canonical_result() -> None:
    """Bind one transient decision to the canonical validated result payload."""

    class ExampleResult(BaseModel):
        """Provide one strict result with deterministic canonical JSON."""

        model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

        output: str

    result = ExampleResult(output="TEXT")
    decision = VerificationDecision(status="success", feedback_list=[])

    verification = VerificationResult.from_decision(
        decision=decision,
        result=result,
        result_revision_index=2,
    )

    assert decision.model_dump(mode="json") == {"feedback_list": [], "status": "success"}
    assert "result_digest" not in VerificationDecision.model_json_schema()["properties"]
    assert verification.model_dump(mode="json") == {
        "feedback_list": [],
        "result_digest": "04f5f40e3bbc9efa2a9ab83622d87501a48dd3d3a164a3e6a1d539de6ba60d49",
        "result_revision_index": 2,
        "status": "success",
    }
    assert verification.is_bound_to(result, result_revision_index=2)
    assert not verification.is_bound_to(result, result_revision_index=1)
    assert not verification.is_bound_to(ExampleResult(output="DIFFERENT"), result_revision_index=2)

    with pytest.raises(ValidationError):
        VerificationResult(status="success", feedback_list=[], result_digest="", result_revision_index=1)


def test_verification_result_revalidates_in_place_mutated_result_before_digest() -> None:
    """Do not bind a verdict to an invalid result snapshot."""

    class ExampleResult(BaseModel):
        """Expose one nested collection protected by a field constraint."""

        model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

        value_list: list[str] = Field(min_length=1)

    result = ExampleResult(value_list=["valid"])
    result.value_list.clear()

    with pytest.raises(ValidationError):
        VerificationResult.from_decision(
            decision=VerificationDecision(status="success", feedback_list=[]),
            result=result,
            result_revision_index=1,
        )


def test_strict_model_gate_requires_complete_runtime_configuration() -> None:
    """Reject strict models that omit assignment or default validation."""

    class IncompleteModel(BaseModel):
        """Boundary model missing two required validation settings."""

        model_config = ConfigDict(extra="forbid", strict=True)

        value: str

    with pytest.raises(ValueError, match="validate_assignment=True"):
        strict_model_contract_validate(IncompleteModel(value="value"), model_role="test")
