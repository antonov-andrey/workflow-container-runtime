"""Configuration and durable state for Codex-backed steps."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workflow_container_runtime.artifact.materializer import ArtifactMaterializationPolicy
from workflow_container_runtime.retry import CodexExecutionRetryPolicy


class WorkflowStepCodexConfig(BaseModel):
    """Explicit correction and materialization policy for one Codex step."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    artifact_materialization_policy: ArtifactMaterializationPolicy
    attempt_limit: int = Field(ge=1)
    execution_retry_policy: CodexExecutionRetryPolicy


class WorkflowStepCodexState(BaseModel):
    """Private durable correction FSM state of one Codex step."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    attempt_index: int = Field(ge=1)
    state: Literal["ready", "result_published", "verification_failed", "completed"]
