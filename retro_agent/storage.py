"""Persistent SQLite storage with strict separation of anonymous and named data."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_settings
from .pii_redactor import PIIRedactor


class RetroStore:
    """Manages persistent retrospective campaigns, participation, and responses."""

    def __init__(
        self,
        db_path: str | None = None,
        redactor: PIIRedactor | None = None,
    ) -> None:
        settings = get_settings()
        self.db_path = Path(db_path or settings.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor or PIIRedactor(
            project_id=settings.project_id,
            use_cloud_dlp=settings.enable_cloud_dlp,
        )
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    team_name TEXT NOT NULL,
                    organizer_email TEXT NOT NULL,
                    participant_count INTEGER NOT NULL,
                    end_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    invited_at TEXT,
                    anonymous_submitted INTEGER NOT NULL DEFAULT 0,
                    highlight_submitted INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (campaign_id, email),
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );

                -- Strictly no participant email or foreign key to participants.
                CREATE TABLE IF NOT EXISTS anonymous_responses (
                    response_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    team_highlight TEXT NOT NULL,
                    room_for_improvement TEXT NOT NULL,
                    one_thing_to_start TEXT NOT NULL,
                    recommend_score INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );

                CREATE TABLE IF NOT EXISTS customer_highlights (
                    highlight_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    participant_email TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    customer_highlight TEXT NOT NULL,
                    photo_paths_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                """
            )

    def create_campaign(
        self,
        team_name: str,
        organizer_email: str,
        participant_count: int,
        participant_emails: list[str],
        end_date: date,
    ) -> dict[str, Any]:
        """Create a new retrospective campaign and register participants."""
        campaign_id = f"retro-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO campaigns (
                    campaign_id, team_name, organizer_email, participant_count, end_date, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    campaign_id,
                    team_name.strip(),
                    organizer_email.strip().lower(),
                    participant_count,
                    end_date.isoformat(),
                    now,
                ),
            )
            for email in participant_emails:
                conn.execute(
                    "INSERT INTO participants (campaign_id, email) VALUES (?, ?)",
                    (campaign_id, email.strip().lower()),
                )
        return self.get_campaign(campaign_id)  # type: ignore[return-value]

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        """Fetch campaign metadata and participant completion stats."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            if row is None:
                return None
            participants = conn.execute(
                "SELECT email, invited_at, anonymous_submitted, highlight_submitted "
                "FROM participants WHERE campaign_id = ? ORDER BY id",
                (campaign_id,),
            ).fetchall()
            completed = sum(
                1
                for p in participants
                if p["anonymous_submitted"] == 1 and p["highlight_submitted"] == 1
            )
            anon_only = sum(1 for p in participants if p["anonymous_submitted"] == 1)
            return {
                "campaign_id": row["campaign_id"],
                "team_name": row["team_name"],
                "organizer_email": row["organizer_email"],
                "participant_count": row["participant_count"],
                "end_date": row["end_date"],
                "status": row["status"],
                "created_at": row["created_at"],
                "participant_emails": [p["email"] for p in participants],
                "completed_count": completed,
                "anonymous_count": anon_only,
            }

    def mark_participants_invited(self, campaign_id: str) -> int:
        """Record invitation timestamp for all participants in a campaign."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE participants SET invited_at = ? WHERE campaign_id = ?",
                (now, campaign_id),
            )
            return cur.rowcount

    def is_participant_eligible(self, campaign_id: str, email: str) -> bool:
        """Return True if the email belongs to the campaign roster."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM participants WHERE campaign_id = ? AND email = ?",
                (campaign_id, email.strip().lower()),
            ).fetchone()
            return row is not None

    def submit_anonymous_feedback(
        self,
        campaign_id: str,
        participant_email: str,
        team_highlight: str,
        room_for_improvement: str,
        one_thing_to_start: str,
        recommend_score: int,
    ) -> str:
        """Store PII-scrubbed anonymous Section 1 feedback without linking identity."""
        email_clean = participant_email.strip().lower()
        with self._connect() as conn:
            part = conn.execute(
                "SELECT anonymous_submitted FROM participants WHERE campaign_id = ? AND email = ?",
                (campaign_id, email_clean),
            ).fetchone()
            if part is None:
                raise LookupError(f"Participant '{email_clean}' is not in campaign '{campaign_id}'.")
            if part["anonymous_submitted"] == 1:
                raise ValueError(
                    f"Participant '{email_clean}' has already submitted Section 1 anonymous feedback."
                )

            response_id = f"anon-{uuid.uuid4().hex[:10]}"
            now = datetime.now(timezone.utc).isoformat()
            # Scrub PII before storing anonymous feedback.
            conn.execute(
                """
                INSERT INTO anonymous_responses (
                    response_id, campaign_id, team_highlight, room_for_improvement,
                    one_thing_to_start, recommend_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    response_id,
                    campaign_id,
                    self.redactor.redact_text(team_highlight.strip()),
                    self.redactor.redact_text(room_for_improvement.strip()),
                    self.redactor.redact_text(one_thing_to_start.strip()),
                    recommend_score,
                    now,
                ),
            )
            conn.execute(
                "UPDATE participants SET anonymous_submitted = 1 WHERE campaign_id = ? AND email = ?",
                (campaign_id, email_clean),
            )
            return response_id

    def submit_customer_highlight(
        self,
        campaign_id: str,
        participant_email: str,
        customer_name: str,
        customer_highlight: str,
        photo_paths: list[str],
    ) -> str:
        """Store Section 2 named customer highlight and favorite photo paths."""
        email_clean = participant_email.strip().lower()
        with self._connect() as conn:
            part = conn.execute(
                "SELECT highlight_submitted FROM participants WHERE campaign_id = ? AND email = ?",
                (campaign_id, email_clean),
            ).fetchone()
            if part is None:
                raise LookupError(f"Participant '{email_clean}' is not in campaign '{campaign_id}'.")
            if part["highlight_submitted"] == 1:
                raise ValueError(
                    f"Participant '{email_clean}' has already submitted Section 2 customer highlight."
                )

            highlight_id = f"hl-{uuid.uuid4().hex[:10]}"
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO customer_highlights (
                    highlight_id, campaign_id, participant_email, customer_name,
                    customer_highlight, photo_paths_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    highlight_id,
                    campaign_id,
                    email_clean,
                    customer_name.strip(),
                    customer_highlight.strip(),
                    json.dumps(photo_paths),
                    now,
                ),
            )
            conn.execute(
                "UPDATE participants SET highlight_submitted = 1 WHERE campaign_id = ? AND email = ?",
                (campaign_id, email_clean),
            )
            return highlight_id

    def get_anonymous_responses(self, campaign_id: str) -> list[dict[str, Any]]:
        """Return all anonymous Section 1 responses for aggregation."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT response_id, team_highlight, room_for_improvement, one_thing_to_start, recommend_score "
                "FROM anonymous_responses WHERE campaign_id = ? ORDER BY created_at",
                (campaign_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_customer_highlights(self, campaign_id: str) -> list[dict[str, Any]]:
        """Return all Section 2 customer highlights and photo paths."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT highlight_id, participant_email, customer_name, customer_highlight, photo_paths_json "
                "FROM customer_highlights WHERE campaign_id = ? ORDER BY created_at",
                (campaign_id,),
            ).fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["photo_paths"] = json.loads(item.pop("photo_paths_json"))
                results.append(item)
            return results

    def mark_campaign_sent(self, campaign_id: str) -> None:
        """Mark campaign as sent after final distribution."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE campaigns SET status = 'results_sent' WHERE campaign_id = ?",
                (campaign_id,),
            )
