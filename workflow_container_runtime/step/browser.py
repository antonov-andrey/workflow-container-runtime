"""Generic browser capability and action-result contracts."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BrowsingError(BaseModel):
    """One URL-level browser or network failure."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    error: str = Field(min_length=1)
    url: str = Field(min_length=1)

    @field_validator("error", "url")
    @classmethod
    def text_validate(cls, value: str) -> str:
        """Require exact non-empty browser failure text.

        Args:
            value: Browser failure field value.

        Returns:
            The unchanged validated value.

        Raises:
            ValueError: If the value is empty after trimming or contains outer whitespace.
        """

        if not value.strip() or value != value.strip():
            raise ValueError("browsing error fields must be non-empty and trimmed")
        return value


class BrowserActionResult(BaseModel):
    """Common action-owned browser failure payload."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, validate_default=True)

    browsing_error_list: list[BrowsingError]
