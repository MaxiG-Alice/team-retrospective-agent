"""Active PII scrubbing for logging, memory, and anonymous survey storage."""

from __future__ import annotations

import re
from typing import Any


class PIIRedactor:
    """Redacts sensitive PII using Google Cloud DLP with deterministic regex fallback."""

    _EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _PHONE_RE = re.compile(
        r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)\d{3,4}[\s.-]?\d{4}(?!\w)"
    )
    _SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    _API_KEY_RE = re.compile(r"\b(?:AIza[0-9A-Za-z\-_]{20,}|sk-[0-9A-Za-z]{20,}|ghp_[0-9A-Za-z]{20,})\b")
    _IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

    def __init__(self, project_id: str = "", use_cloud_dlp: bool = False) -> None:
        self.project_id = project_id
        self.use_cloud_dlp = use_cloud_dlp
        self._dlp_client = None

    def redact_text(self, text: str) -> str:
        """Scrub PII from a text string before logging or persisting.

        Args:
            text: Raw input string that may contain emails, phone numbers, or keys.

        Returns:
            Redacted string with sensitive tokens replaced by typed placeholders.
        """
        if not text:
            return text

        scrubbed = text
        if self.use_cloud_dlp and self.project_id:
            scrubbed = self._redact_with_dlp(scrubbed)

        scrubbed = self._API_KEY_RE.sub("[REDACTED_SECRET]", scrubbed)
        scrubbed = self._EMAIL_RE.sub("[REDACTED_EMAIL]", scrubbed)
        scrubbed = self._SSN_RE.sub("[REDACTED_SSN]", scrubbed)
        scrubbed = self._PHONE_RE.sub("[REDACTED_PHONE]", scrubbed)
        scrubbed = self._IP_RE.sub("[REDACTED_IP]", scrubbed)
        return scrubbed

    def redact_structure(self, data: Any) -> Any:
        """Recursively redact PII across nested dicts, lists, and strings."""
        if isinstance(data, str):
            return self.redact_text(data)
        if isinstance(data, list):
            return [self.redact_structure(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self.redact_structure(item) for item in data)
        if isinstance(data, dict):
            return {
                str(k): self.redact_structure(v)
                for k, v in data.items()
            }
        return data

    def _redact_with_dlp(self, text: str) -> str:
        """Call Google Cloud DLP deidentify_content when enabled in GCP."""
        try:
            if self._dlp_client is None:
                from google.cloud import dlp_v2

                self._dlp_client = dlp_v2.DlpServiceClient()

            parent = f"projects/{self.project_id}/locations/global"
            inspect_config = {
                "info_types": [
                    {"name": "EMAIL_ADDRESS"},
                    {"name": "PHONE_NUMBER"},
                    {"name": "PERSON_NAME"},
                    {"name": "US_SOCIAL_SECURITY_NUMBER"},
                ]
            }
            deidentify_config = {
                "info_type_transformations": {
                    "transformations": [
                        {
                            "primitive_transformation": {
                                "replace_with_info_type_config": {}
                            }
                        }
                    ]
                }
            }
            response = self._dlp_client.deidentify_content(
                request={
                    "parent": parent,
                    "deidentify_config": deidentify_config,
                    "inspect_config": inspect_config,
                    "item": {"value": text},
                }
            )
            return response.item.value
        except Exception:
            # Regex scrubbing still executes as defense-in-depth.
            return text
