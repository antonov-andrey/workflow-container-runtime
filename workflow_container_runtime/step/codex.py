"""Strict configuration, policy, and durable state for Codex-backed steps."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workflow_container_runtime.artifact.materializer import ArtifactMaterializationPolicy
from workflow_container_runtime.retry import CodexExecutionRetryPolicy

WorkflowCodexModel = Literal["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"]
WorkflowCodexReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]


class WorkflowStepCodexConfigBase(BaseModel):
    """Define one explicit user-owned Codex step configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    correction_attempt_limit: int = Field(
        description="Maximum correction attempts after the initial action attempt.",
        ge=0,
        json_schema_extra={"default": 3},
        title="Correction attempt limit",
    )
    instruction: str = Field(
        description="Additional instruction applied only to this step.",
        json_schema_extra={"default": "", "x-ui-control": "textarea"},
        title="Step instruction",
    )
    model: WorkflowCodexModel = Field(
        description="Codex model used by action and verifier.",
        json_schema_extra={"default": "gpt-5.6-terra"},
        title="Model",
    )
    reasoning_effort: WorkflowCodexReasoningEffort = Field(
        description="Reasoning effort used by action and verifier.",
        json_schema_extra={"default": "high"},
        title="Reasoning effort",
    )


class WorkflowStepCodexConcurrentConfigBase(WorkflowStepCodexConfigBase):
    """Add bounded concurrent scheduling to one explicit Codex step config."""

    concurrency: int = Field(
        description="Maximum concurrent independent invocations of this step inside one workflow run.",
        ge=1,
        json_schema_extra={"default": 1},
        title="Concurrency",
    )


class WorkflowStepCodexRuntimePolicy(BaseModel):
    """Store source-owned execution policy outside the public workflow input."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    artifact_materialization_policy: ArtifactMaterializationPolicy
    execution_retry_policy: CodexExecutionRetryPolicy


class WorkflowStepCodexState(BaseModel):
    """Private durable correction FSM state of one Codex step."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    attempt_index: int = Field(ge=1)
    state: Literal["ready", "result_published", "verification_failed", "completed"]
