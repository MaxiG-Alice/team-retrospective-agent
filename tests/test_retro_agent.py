"""Unit and integration tests for the Team Year-End Retrospective Agent."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from retro_agent.pii_redactor import PIIRedactor
from retro_agent.storage import RetroStore
from retro_agent.tools import (
    check_release_eligibility_gate,
    create_retrospective_campaign,
    dispatch_participant_invitations,
    generate_year_end_collage,
    run_team_sentiment_analysis,
    send_retrospective_results_to_team,
    set_store,
    submit_anonymous_team_feedback,
    submit_customer_highlight_and_photos,
)


class RetroAgentWorkflowTests(unittest.TestCase):
    """Tests campaign setup, anonymity, PII scrubbing, 75%+date gate, HITL, and collage."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_retro.db"
        self.artifacts_dir = Path(self.tmp_dir.name) / "artifacts"
        os.environ["RETRO_ARTIFACTS_DIR"] = str(self.artifacts_dir)
        # Clear cached settings so RETRO_ARTIFACTS_DIR takes effect per test.
        from retro_agent.config import get_settings

        get_settings.cache_clear()
        set_store(RetroStore(db_path=str(self.db_path)))

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_pii_redactor_scrubs_sensitive_tokens(self) -> None:
        redactor = PIIRedactor()
        raw = "Contact alice@example.com or +1-555-234-5678, key AIzaSyDummyKey1234567890123456"
        scrubbed = redactor.redact_text(raw)
        self.assertNotIn("alice@example.com", scrubbed)
        self.assertNotIn("555-234-5678", scrubbed)
        self.assertNotIn("AIzaSyDummyKey", scrubbed)
        self.assertIn("[REDACTED_EMAIL]", scrubbed)
        self.assertIn("[REDACTED_PHONE]", scrubbed)
        self.assertIn("[REDACTED_SECRET]", scrubbed)

    def test_guided_error_on_participant_count_mismatch(self) -> None:
        res = create_retrospective_campaign(
            team_name="Core AI",
            organizer_email="lead@example.com",
            participant_count=4,
            participant_emails=["a@example.com", "b@example.com"],
            end_date="2026-12-15",
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["error_code"], "PARTICIPANT_COUNT_MISMATCH")
        self.assertIn("recovery_instruction", res)

    def test_end_to_end_gate_hitl_anonymity_and_collage(self) -> None:
        created = create_retrospective_campaign(
            team_name="Core AI",
            organizer_email="lead@example.com",
            participant_count=4,
            participant_emails=[
                "p1@example.com",
                "p2@example.com",
                "p3@example.com",
                "p4@example.com",
            ],
            end_date="2026-12-15",
        )
        self.assertEqual(created["status"], "success")
        cid = created["campaign_id"]

        # HITL pause when ToolContext has no confirmation yet
        mock_ctx = MagicMock()
        mock_ctx.tool_confirmation = None
        invite_paused = dispatch_participant_invitations(cid, tool_context=mock_ctx)
        self.assertEqual(invite_paused["status"], "confirmation_required")
        mock_ctx.request_confirmation.assert_called_once()

        # HITL approved
        mock_ctx.tool_confirmation = MagicMock(confirmed=True)
        invite_sent = dispatch_participant_invitations(cid, tool_context=mock_ctx)
        self.assertEqual(invite_sent["status"], "success")
        self.assertEqual(invite_sent["invitations_sent"], 4)

        # 3 of 4 participants (75%) complete both Section 1 and Section 2
        for idx in range(1, 4):
            email = f"p{idx}@example.com"
            s1 = submit_anonymous_team_feedback(
                campaign_id=cid,
                participant_email=email,
                team_highlight=f"Great collaboration with {email} on launch!",
                room_for_improvement="Fewer overlapping meetings.",
                one_thing_to_start="Monthly architecture demos.",
                recommend_score=5 if idx < 3 else 4,
            )
            self.assertEqual(s1["status"], "success")
            self.assertTrue(s1["anonymized"])

            s2 = submit_customer_highlight_and_photos(
                campaign_id=cid,
                participant_email=email,
                customer_name=f"Customer {idx}",
                customer_highlight=f"Shipped AI assistant with Customer {idx}!",
                photo_paths=[f"/tmp/photo_{idx}_a.jpg", f"/tmp/photo_{idx}_b.jpg"],
            )
            self.assertEqual(s2["status"], "success")

        # Gate MUST block on or before end_date even with 75% participation
        blocked_before_date = check_release_eligibility_gate(cid, as_of_date="2026-12-15")
        self.assertEqual(blocked_before_date["status"], "blocked")
        self.assertFalse(blocked_before_date["is_past_end_date"])

        # Gate passes after end_date with 75% participation
        ready = check_release_eligibility_gate(cid, as_of_date="2026-12-16")
        self.assertEqual(ready["status"], "ready")

        # Sentiment analysis succeeds and contains no participant email (scrubbed)
        sentiment = run_team_sentiment_analysis(cid, as_of_date="2026-12-16")
        self.assertEqual(sentiment["status"], "success")
        self.assertEqual(sentiment["response_count"], 3)
        self.assertAlmostEqual(sentiment["average_recommend_score"], 4.67, places=2)
        for theme in sentiment["positive_themes"]:
            self.assertNotIn("@example.com", theme)

        # Collage generation creates PNG + HTML
        collage = generate_year_end_collage(cid, as_of_date="2026-12-16")
        self.assertEqual(collage["status"], "success")
        self.assertEqual(collage["photo_count"], 6)
        self.assertTrue(Path(collage["collage_image_path"]).exists())
        self.assertTrue(Path(collage["collage_html_path"]).exists())

        # Final results dispatch with HITL confirmation
        sent = send_retrospective_results_to_team(
            campaign_id=cid,
            executive_summary_html="<p>Great year!</p>",
            collage_image_path=collage["collage_image_path"],
            as_of_date="2026-12-16",
            tool_context=mock_ctx,
        )
        self.assertEqual(sent["status"], "success")
        self.assertEqual(sent["recipients_count"], 4)


if __name__ == "__main__":
    unittest.main()
