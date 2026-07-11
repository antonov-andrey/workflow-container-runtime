"""Validation of models used at runtime lifecycle boundaries."""

from typing import TypeVar, cast

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def model_snapshot_get(model: ModelT) -> ModelT:
    """Revalidate and return one exact canonical model snapshot.

    Pydantic assignment validation does not observe in-place mutation inside
    mutable fields. Rebuilding through the exact model class closes that gap
    before a value crosses a durable boundary.

    Args:
        model: Candidate model whose current field graph will be published.

    Returns:
        Independently validated snapshot of the exact model type.
    """

    return cast(ModelT, type(model).model_validate(model.model_dump(mode="python", warnings=False)))


def strict_model_contract_validate(model: BaseModel, *, model_role: str) -> None:
    """Require one closed strict model at a producer-owned boundary.

    Args:
        model: Candidate boundary object.
        model_role: Role used in validation errors.

    Raises:
        ValueError: If the complete strict model contract is not configured.
    """

    if model.model_config.get("strict") is not True:
        raise ValueError(f"{model_role} model must use strict=True")
    if model.model_config.get("extra") != "forbid":
        raise ValueError(f"{model_role} model must use extra='forbid'")
    if model.model_config.get("validate_assignment") is not True:
        raise ValueError(f"{model_role} model must use validate_assignment=True")
    if model.model_config.get("validate_default") is not True:
        raise ValueError(f"{model_role} model must use validate_default=True")
