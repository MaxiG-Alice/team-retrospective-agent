"""Configuration, strategic model routing, and Secret Manager integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(frozen=True)
class ModelRoutingConfig:
    """Routes agent roles to the most appropriate Gemini model tier."""

    coordinator_model: str = os.getenv("RETRO_PRO_MODEL", "gemini-3.1-pro-preview")
    reasoning_model: str = os.getenv("RETRO_PRO_MODEL", "gemini-3.1-pro-preview")
    fast_model: str = os.getenv("RETRO_FLASH_MODEL", "gemini-2.5-flash")
    summarizer_model: str = os.getenv("RETRO_FLASH_MODEL", "gemini-2.5-flash")


@dataclass(frozen=True)
class AppSettings:
    """Application runtime settings loaded from environment variables."""

    project_id: str = os.getenv("GOOGLE_CLOUD_PROJECT", "mglaesle-sandbox")
    location: str = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    db_path: str = os.getenv("RETRO_DB_PATH", "./data/retro_campaigns.db")
    artifacts_dir: str = os.getenv("RETRO_ARTIFACTS_DIR", "./data/artifacts")
    adk_session_db_url: str = os.getenv(
        "ADK_SESSION_DB_URL", "sqlite+aiosqlite:///./data/adk_sessions.db"
    )
    service_name: str = os.getenv("OTEL_SERVICE_NAME", "team-retrospective-agent")
    enable_cloud_dlp: bool = os.getenv("ENABLE_CLOUD_DLP", "false").lower() == "true"
    enable_secret_manager: bool = (
        os.getenv("ENABLE_SECRET_MANAGER", "false").lower() == "true"
    )
    email_delivery_mode: str = os.getenv("EMAIL_DELIVERY_MODE", "local_preview")
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    min_participation_ratio: float = 0.75


class SecretManagerProvider:
    """Retrieves secrets from Google Cloud Secret Manager with env fallback."""

    def __init__(self, project_id: str, enabled: bool = False) -> None:
        self.project_id = project_id
        self.enabled = enabled
        self._client = None

    def get_secret(self, secret_id: str, env_fallback: Optional[str] = None) -> str:
        """Fetch a secret value from Secret Manager or fall back to environment.

        Args:
            secret_id: Secret Manager secret identifier (e.g., 'smtp-password').
            env_fallback: Optional environment variable name to check when Secret
                Manager is disabled or unavailable in local dev.

        Returns:
            The secret string, or empty string if not configured.
        """
        if self.enabled:
            try:
                if self._client is None:
                    from google.cloud import secretmanager

                    self._client = secretmanager.SecretManagerServiceClient()
                name = (
                    f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
                )
                response = self._client.access_secret_version(request={"name": name})
                return response.payload.data.decode("utf-8")
            except Exception:
                # Fall back to env var in non-prod environments without crashing.
                pass

        env_key = env_fallback or secret_id.upper().replace("-", "_")
        return os.getenv(env_key, "")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return cached application settings."""
    return AppSettings()


@lru_cache(maxsize=1)
def get_model_routing() -> ModelRoutingConfig:
    """Return cached model routing configuration."""
    return ModelRoutingConfig()


@lru_cache(maxsize=1)
def get_secret_provider() -> SecretManagerProvider:
    """Return cached Secret Manager provider."""
    settings = get_settings()
    return SecretManagerProvider(
        project_id=settings.project_id,
        enabled=settings.enable_secret_manager,
    )
