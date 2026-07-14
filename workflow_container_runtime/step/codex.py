"""Strict configuration, policy, and durable state for Codex-backed steps."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from workflow_container_runtime.artifact.materializer import ArtifactMaterializationPolicy
from workflow_container_runtime.mcp_playwright_profile import mcp_playwright_profile_name_validate
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
    mcp_playwright_profile: str | None = Field(
        description="Run-local logical Playwright target profile, or null for isolated execution.",
        json_schema_extra={"default": None},
        title="Playwright profile",
    )
    mcp_playwright_profile_source: str | None = Field(
        description="Exact completed run-local physical profile copied into the target before every action call.",
        json_schema_extra={"default": None},
        title="Playwright profile source",
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

    @field_validator("mcp_playwright_profile", "mcp_playwright_profile_source")
    @classmethod
    def profile_name_validate(cls, value: str | None) -> str | None:
        """Require a non-empty profile identifier without path or query syntax."""

        return None if value is None else mcp_playwright_profile_name_validate(value)

    def mcp_playwright_profile_physical_list_get(self) -> list[str | None]:
        """Return the exact physical target profile used by a non-concurrent step.

        Returns:
            Single physical target profile, or the isolated marker.
        """

        return [self.mcp_playwright_profile]

    @model_validator(mode="after")
    def profile_relationship_validate(self) -> "WorkflowStepCodexConfigBase":
        """Require a target and prevent source collision with its physical profile set."""

        if self.mcp_playwright_profile_source is not None and self.mcp_playwright_profile is None:
            raise ValueError("Playwright profile source requires a target profile")
        physical_profile_list = self.mcp_playwright_profile_physical_list_get()
        if (
            self.mcp_playwright_profile_source is not None
            and self.mcp_playwright_profile_source in physical_profile_list
        ):
            raise ValueError("Playwright profile source collides with physical target")
        return self


class WorkflowStepCodexConcurrentConfigBase(WorkflowStepCodexConfigBase):
    """Add bounded concurrent scheduling to one explicit Codex step config."""

    concurrency: int = Field(
        description="Maximum concurrent independent invocations of this step inside one workflow run.",
        ge=1,
        json_schema_extra={"default": 1},
        title="Concurrency",
    )

    def mcp_playwright_profile_physical_list_get(self) -> list[str | None]:
        """Return every deterministic physical target assigned to fixed worker lanes.

        Returns:
            Physical target profiles in one-based lane order, or isolated markers.
        """

        logical_profile = self.mcp_playwright_profile
        if logical_profile is None:
            return [None] * self.concurrency
        if self.concurrency == 1:
            return [logical_profile]
        return [
            mcp_playwright_profile_name_validate(f"{logical_profile}-{lane_number}")
            for lane_number in range(1, self.concurrency + 1)
        ]


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
