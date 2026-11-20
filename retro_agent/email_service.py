"""Email delivery service with local preview (.eml) and Secret Manager SMTP support."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from .config import get_secret_provider, get_settings
from .observability import log_event


def send_or_preview_email(
    campaign_id: str,
    recipients: list[str],
    subject: str,
    html_body: str,
    attachment_paths: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Send an email via SMTP or write local .eml previews depending on configuration.

    Args:
        campaign_id: Retrospective campaign identifier.
        recipients: List of target email addresses.
        subject: Email subject line.
        html_body: Rendered HTML email body.
        attachment_paths: Optional file paths (e.g., collage PNG) to attach.

    Returns:
        Tuple of (delivery_mode, list_of_saved_preview_paths).
    """
    settings = get_settings()
    mode = settings.email_delivery_mode.lower()
    preview_dir = Path(settings.artifacts_dir) / campaign_id / "emails"
    preview_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []

    for idx, recipient in enumerate(recipients):
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_username or "retro-agent@example.com"
        msg["To"] = recipient
        msg.set_content("Please view this retrospective email in an HTML-capable client.")
        msg.add_alternative(html_body, subtype="html")

        for path_str in attachment_paths or []:
            p = Path(path_str)
            if p.exists() and p.is_file():
                msg.add_attachment(
                    p.read_bytes(),
                    maintype="application",
                    subtype="octet-stream",
                    filename=p.name,
                )

        if mode == "smtp":
            password = get_secret_provider().get_secret(
                "smtp-password", env_fallback="SMTP_PASSWORD"
            )
            if not password or not settings.smtp_username:
                raise RuntimeError(
                    "SMTP mode is enabled but SMTP_USERNAME or Secret Manager 'smtp-password' is missing."
                )
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
                server.starttls()
                server.login(settings.smtp_username, password)
                server.send_message(msg)
        else:
            safe_name = recipient.replace("@", "_at_").replace(".", "_")
            eml_path = preview_dir / f"{idx:02d}_{safe_name}.eml"
            eml_path.write_bytes(msg.as_bytes())
            saved_paths.append(str(eml_path))

    log_event(
        "emails_dispatched",
        campaign_id=campaign_id,
        delivery_mode=mode,
        recipient_count=len(recipients),
    )
    return mode, saved_paths
