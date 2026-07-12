"""Strict public workflow input and configuration base models."""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

RequestT = TypeVar("RequestT", bound=BaseModel)
WorkflowConfigT = TypeVar("WorkflowConfigT", bound="WorkflowConfigBase")


class WorkflowConfigBase(BaseModel):
    """Define the common explicit user instruction for one workflow run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    instruction: str = Field(
        description="Additional instruction applied to every Codex step in this workflow.",
        json_schema_extra={"default": "", "x-ui-control": "textarea"},
        title="Workflow instruction",
    )


class WorkflowInputBase(BaseModel, Generic[RequestT, WorkflowConfigT]):
    """Bind one exact workflow request to its complete explicit configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_assignment=True, validate_default=True)

    request: RequestT = Field(description="Domain work requested from the workflow.", title="Request")
    config: WorkflowConfigT = Field(description="Complete settings for this run.", title="Configuration")
