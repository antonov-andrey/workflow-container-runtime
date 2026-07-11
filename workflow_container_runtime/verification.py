"""Transient verification decisions and result-bound persisted verdicts."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from workflow_container_runtime.model import model_snapshot_get


class VerificationDecision(BaseModel):
    """Carry one transient semantic or mechanical verification decision."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    feedback_list: list[str]
    status: Literal["success", "failed"]

    @model_validator(mode="after")
    def feedback_validate(self) -> Self:
        """Keep success and failure feedback unambiguous.

        Returns:
            Validated verification decision.

        Raises:
            ValueError: If feedback does not match the verdict.
        """

        if self.status == "success" and self.feedback_list:
            raise ValueError("success verification must not contain feedback")
        if self.status == "failed" and not self.feedback_list:
            raise ValueError("failed verification must contain feedback")
        return self


class VerificationResult(VerificationDecision):
    """Persist one decision bound to the exact canonical public result."""

    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_revision_index: int = Field(ge=1)

    @classmethod
    def from_decision(
        cls,
        *,
        decision: VerificationDecision,
        result: BaseModel,
        result_revision_index: int,
    ) -> Self:
        """Bind one transient decision to a canonical result revision.

        Args:
            decision: Transient verification decision.
            result: Exact validated public result.
            result_revision_index: Current result publication revision.

        Returns:
            Persistable verdict bound to the result content and revision.
        """

        return cls(
            feedback_list=list(decision.feedback_list),
            result_digest=cls._result_digest_get(result),
            result_revision_index=result_revision_index,
            status=decision.status,
        )

    def has_result_digest(self, result: BaseModel) -> bool:
        """Return whether this verdict names the canonical result content.

        Args:
            result: Exact parsed public result.

        Returns:
            Whether the persisted digest matches the result content.
        """

        return self.result_digest == self._result_digest_get(result)

    def is_bound_to(self, result: BaseModel, *, result_revision_index: int) -> bool:
        """Return whether this verdict belongs to one exact result revision.

        Args:
            result: Exact parsed public result.
            result_revision_index: Current result publication revision.

        Returns:
            Whether both the digest and publication revision match.
        """

        return self.result_revision_index == result_revision_index and self.has_result_digest(result)

    @classmethod
    def _result_digest_get(cls, result: BaseModel) -> str:
        """Return SHA-256 for one canonical JSON model payload.

        Args:
            result: Exact validated public result.

        Returns:
            Lowercase hexadecimal SHA-256 digest.
        """

        snapshot = model_snapshot_get(result)
        payload = json.dumps(snapshot.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
