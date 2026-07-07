"""Generic browser-backed stage payload models."""

from pydantic import BaseModel, ConfigDict, field_validator


class BrowsingError(BaseModel):
    """Browser or network failure for one concrete URL."""

    model_config = ConfigDict(extra="forbid", strict=True)

    error: str
    url: str

    @field_validator("error", "url")
    @classmethod
    def text_validate(cls, value: str) -> str:
        """Validate one browsing-error text field.

        Args:
            value: Candidate text value.

        Returns:
            Trimmed non-empty text value.

        Raises:
            ValueError: If the text is empty after trimming.
        """

        text = value.strip()
        if not text:
            raise ValueError("browsing error fields must be non-empty strings")
        return text
