"""ADK tool functions with Pydantic validation, HITL hooks, and guided error recovery."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import ValidationError

from .collage import generate_collage_assets
from .config import get_settings
from .email_service import send_or_preview_email
from .observability import log_event, trace_span
from .schemas import (
    CollageOutput,
    CreateCampaignInput,
    CreateCampaignOutput,
    DispatchInvitationsInput,
    DispatchInvitationsOutput,
    GuidedErrorResponse,
    ReleaseGateStatusOutput,
    SendResultsOutput,
    SentimentSummaryOutput,
    SubmitAnonymousFeedbackInput,
    SubmitAnonymousFeedbackOutput,
    SubmitCustomerHighlightInput,
    SubmitCustomerHighlightOutput,
)
from .storage import RetroStore

_store: RetroStore | None = None


def get_store() -> RetroStore:
    """Return the shared persistent RetroStore instance."""
    global _store
    if _store is None:
        _store = RetroStore()
    return _store


def set_store(store: RetroStore) -> None:
    """Override the RetroStore instance (used in tests)."""
    global _store
    _store = store


def _guided_error(
    error_code: str,
    message: str,
    recovery_instruction: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return GuidedErrorResponse(
        error_code=error_code,
        message=message,
        recovery_instruction=recovery_instruction,
        details=details or {},
    ).model_dump()


def _check_hitl_confirmation(
    tool_context: Any,
    hint: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    """Check or request ADK ToolConfirmation for high-stakes external actions."""
    if tool_context is None:
        return True, "No ToolContext provided (non-ADK direct invocation)."

    confirmation = getattr(tool_context, "tool_confirmation", None)
    if confirmation is None:
        request_fn = getattr(tool_context, "request_confirmation", None)
        if callable(request_fn):
            request_fn(hint=hint, payload=payload)
        return False, hint

    if getattr(confirmation, "confirmed", False):
        return True, "Confirmed by human operator."

    return False, "Action was declined by the human operator."


def create_retrospective_campaign(
    team_name: str,
    organizer_email: str,
    participant_count: int,
    participant_emails: list[str],
    end_date: str,
) -> dict[str, Any]:
    """Create a new team year-end retrospective campaign with participant roster and deadline.

    Args:
        team_name: Human-readable name of the core team (e.g., 'Cloud AI Core').
        organizer_email: Email address of the campaign organizer.
        participant_count: Expected number of participants; must match unique emails count.
        participant_emails: List of participant email addresses who will receive invitations.
        end_date: Survey deadline in ISO YYYY-MM-DD format; results release only after this date.

    Returns:
        Dictionary matching CreateCampaignOutput on success, or GuidedErrorResponse on error.
    """
    with trace_span("tool.create_retrospective_campaign", {"team_name": team_name}):
        try:
            payload = CreateCampaignInput(
                team_name=team_name,
                organizer_email=organizer_email,
                participant_count=participant_count,
                participant_emails=participant_emails,
                end_date=date.fromisoformat(end_date),
            )
        except (ValidationError, ValueError) as exc:
            return _guided_error(
                error_code="INVALID_CAMPAIGN_INPUT",
                message=f"Campaign input validation failed: {exc}",
                recovery_instruction=(
                    "Ask the user to provide a valid team_name, organizer_email, positive "
                    "participant_count, valid participant_emails, and end_date in YYYY-MM-DD format."
                ),
            )

        if len(payload.participant_emails) != payload.participant_count:
            return _guided_error(
                error_code="PARTICIPANT_COUNT_MISMATCH",
                message=(
                    f"participant_count is {payload.participant_count}, but "
                    f"{len(payload.participant_emails)} unique valid emails were provided."
                ),
                recovery_instruction=(
                    "Ask the organizer to either add the missing email addresses or update "
                    "participant_count to match the exact number of unique participant emails."
                ),
                details={
                    "expected_count": payload.participant_count,
                    "provided_unique_emails": len(payload.participant_emails),
                },
            )

        campaign = get_store().create_campaign(
            team_name=payload.team_name,
            organizer_email=payload.organizer_email,
            participant_count=payload.participant_count,
            participant_emails=payload.participant_emails,
            end_date=payload.end_date,
        )
        min_required = max(1, int(-(-payload.participant_count * 0.75 // 1)))
        log_event(
            "campaign_created",
            campaign_id=campaign["campaign_id"],
            team_name=payload.team_name,
            participant_count=payload.participant_count,
            end_date=payload.end_date.isoformat(),
        )
        return CreateCampaignOutput(
            campaign_id=campaign["campaign_id"],
            team_name=payload.team_name,
            participant_count=payload.participant_count,
            end_date=payload.end_date.isoformat(),
            min_required_responses=min_required,
            message=(
                f"Campaign '{campaign['campaign_id']}' created for {payload.team_name}. "
                f"At least {min_required}/{payload.participant_count} (75%) responses are required "
                f"and results can only be sent after {payload.end_date.isoformat()}."
            ),
        ).model_dump()


def dispatch_participant_invitations(
    campaign_id: str,
    custom_message: str = "",
    tool_context: Optional[Any] = None,
) -> dict[str, Any]:
    """Send retrospective survey invitations to all registered participants (HITL-gated).

    Args:
        campaign_id: Unique identifier of the retrospective campaign.
        custom_message: Optional personal note from the organizer to include in the invite.
        tool_context: ADK ToolContext used to enforce human-in-the-loop confirmation.

    Returns:
        Dictionary matching DispatchInvitationsOutput or GuidedErrorResponse.
    """
    with trace_span("tool.dispatch_participant_invitations", {"campaign_id": campaign_id}):
        try:
            payload = DispatchInvitationsInput(
                campaign_id=campaign_id, custom_message=custom_message
            )
        except ValidationError as exc:
            return _guided_error(
                error_code="INVALID_INVITATION_INPUT",
                message=str(exc),
                recovery_instruction="Provide a valid campaign_id and custom_message under 1000 chars.",
            )

        store = get_store()
        campaign = store.get_campaign(payload.campaign_id)
        if campaign is None:
            return _guided_error(
                error_code="CAMPAIGN_NOT_FOUND",
                message=f"No campaign found with ID '{payload.campaign_id}'.",
                recovery_instruction=(
                    "Verify the campaign_id with the user or create a new campaign using "
                    "create_retrospective_campaign."
                ),
            )

        confirmed, hint = _check_hitl_confirmation(
            tool_context=tool_context,
            hint=(
                f"Confirm sending retrospective survey invitations to "
                f"{campaign['participant_count']} participants for '{campaign['team_name']}' "
                f"(deadline: {campaign['end_date']})?"
            ),
            payload={
                "campaign_id": payload.campaign_id,
                "recipient_count": campaign["participant_count"],
                "end_date": campaign["end_date"],
            },
        )
        if not confirmed:
            return DispatchInvitationsOutput(
                status="confirmation_required",
                campaign_id=payload.campaign_id,
                invitations_sent=0,
                delivery_mode=get_settings().email_delivery_mode,
                message=f"Human confirmation required before emailing team: {hint}",
            ).model_dump()

        html_body = (
            f"<h2>You are invited to the {campaign['team_name']} Year-End Retrospective</h2>"
            f"<p>Please complete both sections by <strong>{campaign['end_date']}</strong>:</p>"
            f"<ul>"
            f"<li><strong>Section 1 (Anonymous):</strong> Team highlight, improvement area, "
            f"one thing to start doing, and 1-5 recommendation score.</li>"
            f"<li><strong>Section 2 (Named):</strong> Your personal customer highlight and 2 favorite photos.</li>"
            f"</ul>"
            f"<p>{payload.custom_message}</p>"
            f"<p>Campaign ID: <code>{payload.campaign_id}</code></p>"
        )
        mode, previews = send_or_preview_email(
            campaign_id=payload.campaign_id,
            recipients=campaign["participant_emails"],
            subject=f"[{campaign['team_name']}] Year-End Retrospective Invitation",
            html_body=html_body,
        )
        sent_count = store.mark_participants_invited(payload.campaign_id)
        return DispatchInvitationsOutput(
            status="success",
            campaign_id=payload.campaign_id,
            invitations_sent=sent_count,
            delivery_mode=mode,
            preview_paths=previews,
            message=f"Dispatched {sent_count} invitations via {mode}.",
        ).model_dump()


def submit_anonymous_team_feedback(
    campaign_id: str,
    participant_email: str,
    team_highlight: str,
    room_for_improvement: str,
    one_thing_to_start: str,
    recommend_score: int,
) -> dict[str, Any]:
    """Record Section 1 anonymous team feedback without linking participant identity.

    Args:
        campaign_id: Target retrospective campaign ID.
        participant_email: Used solely to verify eligibility and prevent duplicates; never stored with answers.
        team_highlight: Highlight of the year relating to the core team (What went well?).
        room_for_improvement: Where is room for improvement in the core team (What didn't?).
        one_thing_to_start: One thing the participant would like the team to start doing.
        recommend_score: Integer 1 to 5 rating on recommending the team to colleagues.

    Returns:
        Dictionary matching SubmitAnonymousFeedbackOutput or GuidedErrorResponse.
    """
    with trace_span("tool.submit_anonymous_team_feedback", {"campaign_id": campaign_id}):
        try:
            payload = SubmitAnonymousFeedbackInput(
                campaign_id=campaign_id,
                participant_email=participant_email,
                team_highlight=team_highlight,
                room_for_improvement=room_for_improvement,
                one_thing_to_start=one_thing_to_start,
                recommend_score=recommend_score,
            )
        except ValidationError as exc:
            return _guided_error(
                error_code="INVALID_ANONYMOUS_FEEDBACK",
                message=f"Section 1 validation failed: {exc}",
                recovery_instruction=(
                    "Ensure recommend_score is an integer between 1 and 5, and all three text "
                    "answers are provided with sufficient detail."
                ),
            )

        store = get_store()
        if store.get_campaign(payload.campaign_id) is None:
            return _guided_error(
                error_code="CAMPAIGN_NOT_FOUND",
                message=f"Campaign '{payload.campaign_id}' does not exist.",
                recovery_instruction="Ask the participant to double-check their campaign_id.",
            )

        try:
            response_id = store.submit_anonymous_feedback(
                campaign_id=payload.campaign_id,
                participant_email=payload.participant_email,
                team_highlight=payload.team_highlight,
                room_for_improvement=payload.room_for_improvement,
                one_thing_to_start=payload.one_thing_to_start,
                recommend_score=payload.recommend_score,
            )
        except LookupError as exc:
            return _guided_error(
                error_code="PARTICIPANT_NOT_ELIGIBLE",
                message=str(exc),
                recovery_instruction="Ask the participant to enter the exact email address invited by the organizer.",
            )
        except ValueError as exc:
            return _guided_error(
                error_code="DUPLICATE_SECTION1_SUBMISSION",
                message=str(exc),
                recovery_instruction="Inform the participant that Section 1 is already recorded and prompt for Section 2 if not yet completed.",
            )

        campaign = store.get_campaign(payload.campaign_id)
        assert campaign is not None
        rate = round(campaign["completed_count"] / campaign["participant_count"], 3)
        return SubmitAnonymousFeedbackOutput(
            campaign_id=payload.campaign_id,
            response_id=response_id,
            completed_count=campaign["completed_count"],
            participant_count=campaign["participant_count"],
            participation_rate=rate,
            message=(
                "Section 1 anonymous feedback recorded and scrubbed of PII. "
                "Please proceed to Section 2 (Customer Highlight + 2 favorite photos)."
            ),
        ).model_dump()


def submit_customer_highlight_and_photos(
    campaign_id: str,
    participant_email: str,
    customer_name: str,
    customer_highlight: str,
    photo_paths: list[str],
) -> dict[str, Any]:
    """Record Section 2 named customer highlight and 1 to 2 favorite photos of the year.

    Args:
        campaign_id: Target retrospective campaign ID.
        participant_email: Email of the participating team member.
        customer_name: Name of the customer associated with the highlight.
        customer_highlight: Narrative of the participant's personal customer highlight.
        photo_paths: List of 1 or 2 file paths/URLs for the participant's favorite photos.

    Returns:
        Dictionary matching SubmitCustomerHighlightOutput or GuidedErrorResponse.
    """
    with trace_span("tool.submit_customer_highlight_and_photos", {"campaign_id": campaign_id}):
        try:
            payload = SubmitCustomerHighlightInput(
                campaign_id=campaign_id,
                participant_email=participant_email,
                customer_name=customer_name,
                customer_highlight=customer_highlight,
                photo_paths=photo_paths,
            )
        except ValidationError as exc:
            return _guided_error(
                error_code="INVALID_HIGHLIGHT_INPUT",
                message=f"Section 2 validation failed: {exc}",
                recovery_instruction=(
                    "Ask the participant to provide a customer_name, customer_highlight, and "
                    "between 1 and 2 photo paths/URLs."
                ),
            )

        store = get_store()
        if store.get_campaign(payload.campaign_id) is None:
            return _guided_error(
                error_code="CAMPAIGN_NOT_FOUND",
                message=f"Campaign '{payload.campaign_id}' does not exist.",
                recovery_instruction="Ask the participant to verify the campaign_id.",
            )

        try:
            highlight_id = store.submit_customer_highlight(
                campaign_id=payload.campaign_id,
                participant_email=payload.participant_email,
                customer_name=payload.customer_name,
                customer_highlight=payload.customer_highlight,
                photo_paths=payload.photo_paths,
            )
        except LookupError as exc:
            return _guided_error(
                error_code="PARTICIPANT_NOT_ELIGIBLE",
                message=str(exc),
                recovery_instruction="Verify the participant's email is on the campaign roster.",
            )
        except ValueError as exc:
            return _guided_error(
                error_code="DUPLICATE_SECTION2_SUBMISSION",
                message=str(exc),
                recovery_instruction="Inform the participant that their Section 2 highlight is already saved.",
            )

        return SubmitCustomerHighlightOutput(
            campaign_id=payload.campaign_id,
            highlight_id=highlight_id,
            photo_count=len(payload.photo_paths),
            message=f"Section 2 customer highlight for '{payload.customer_name}' saved with {len(payload.photo_paths)} photo(s).",
        ).model_dump()


def check_release_eligibility_gate(
    campaign_id: str,
    as_of_date: str = "",
) -> dict[str, Any]:
    """Evaluate whether both release conditions (past end_date AND >=75% participation) are met.

    Args:
        campaign_id: Target retrospective campaign ID.
        as_of_date: Optional ISO date override (YYYY-MM-DD) for deterministic testing; defaults to today.

    Returns:
        Dictionary matching ReleaseGateStatusOutput or GuidedErrorResponse.
    """
    with trace_span("tool.check_release_eligibility_gate", {"campaign_id": campaign_id}):
        store = get_store()
        campaign = store.get_campaign(campaign_id)
        if campaign is None:
            return _guided_error(
                error_code="CAMPAIGN_NOT_FOUND",
                message=f"Campaign '{campaign_id}' was not found.",
                recovery_instruction="Confirm the campaign_id before checking release eligibility.",
            )

        try:
            current_dt = date.fromisoformat(as_of_date) if as_of_date else date.today()
        except ValueError:
            return _guided_error(
                error_code="INVALID_DATE_FORMAT",
                message=f"Invalid as_of_date '{as_of_date}'. Expected YYYY-MM-DD.",
                recovery_instruction="Provide as_of_date in YYYY-MM-DD format or omit it to use today.",
            )

        end_dt = date.fromisoformat(campaign["end_date"])
        is_past_end = current_dt > end_dt
        completed = campaign["completed_count"]
        total = campaign["participant_count"]
        rate = round(completed / total, 4) if total else 0.0
        min_rate = get_settings().min_participation_ratio
        meets_threshold = rate >= min_rate

        reasons: list[str] = []
        if not is_past_end:
            reasons.append(
                f"Current date ({current_dt.isoformat()}) is not past end_date ({end_dt.isoformat()})."
            )
        if not meets_threshold:
            reasons.append(
                f"Participation is {completed}/{total} ({rate:.1%}), below the 75% minimum."
            )

        ready = is_past_end and meets_threshold
        return ReleaseGateStatusOutput(
            status="ready" if ready else "blocked",
            campaign_id=campaign_id,
            is_past_end_date=is_past_end,
            end_date=end_dt.isoformat(),
            current_date=current_dt.isoformat(),
            completed_count=completed,
            participant_count=total,
            participation_rate=rate,
            required_rate=min_rate,
            meets_participation_threshold=meets_threshold,
            blocking_reasons=reasons,
            message=(
                "Release gate passed: past end_date and >=75% participation."
                if ready
                else "Release gate blocked: " + " ".join(reasons)
            ),
        ).model_dump()


def run_team_sentiment_analysis(
    campaign_id: str,
    as_of_date: str = "",
) -> dict[str, Any]:
    """Compute quantitative scores and theme inputs from anonymous Section 1 feedback.

    Args:
        campaign_id: Target retrospective campaign ID.
        as_of_date: Optional ISO date override (YYYY-MM-DD) for gate verification.

    Returns:
        Dictionary matching SentimentSummaryOutput or GuidedErrorResponse if gate is blocked.
    """
    with trace_span("tool.run_team_sentiment_analysis", {"campaign_id": campaign_id}):
        gate = check_release_eligibility_gate(campaign_id=campaign_id, as_of_date=as_of_date)
        if gate.get("status") == "error":
            return gate
        if gate.get("status") != "ready":
            return _guided_error(
                error_code="RELEASE_GATE_BLOCKED",
                message=gate["message"],
                recovery_instruction=(
                    "Do NOT compile or send results yet. Inform the user which condition is blocking "
                    "(end_date or 75% participation) and suggest sending a reminder to pending participants."
                ),
                details={
                    "is_past_end_date": gate["is_past_end_date"],
                    "participation_rate": gate["participation_rate"],
                },
            )

        responses = get_store().get_anonymous_responses(campaign_id)
        if not responses:
            return _guided_error(
                error_code="NO_ANONYMOUS_RESPONSES",
                message=f"No anonymous responses found for campaign '{campaign_id}'.",
                recovery_instruction="Wait for participants to submit Section 1 feedback.",
            )

        scores = [int(r["recommend_score"]) for r in responses]
        avg_score = round(sum(scores) / len(scores), 2)
        dist = {str(i): scores.count(i) for i in range(1, 6)}
        sentiment = (
            "positive"
            if avg_score >= 4.0
            else ("mixed" if avg_score >= 3.0 else "needs_attention")
        )

        return SentimentSummaryOutput(
            campaign_id=campaign_id,
            response_count=len(responses),
            average_recommend_score=avg_score,
            score_distribution=dist,
            positive_themes=[r["team_highlight"] for r in responses],
            improvement_themes=[r["room_for_improvement"] for r in responses],
            start_doing_ideas=[r["one_thing_to_start"] for r in responses],
            overall_sentiment=sentiment,
        ).model_dump()


def generate_year_end_collage(
    campaign_id: str,
    as_of_date: str = "",
) -> dict[str, Any]:
    """Create the year-in-review PNG and HTML collage from Section 2 highlights and photos.

    Args:
        campaign_id: Target retrospective campaign ID.
        as_of_date: Optional ISO date override (YYYY-MM-DD) for gate verification.

    Returns:
        Dictionary matching CollageOutput or GuidedErrorResponse if gate is blocked.
    """
    with trace_span("tool.generate_year_end_collage", {"campaign_id": campaign_id}):
        gate = check_release_eligibility_gate(campaign_id=campaign_id, as_of_date=as_of_date)
        if gate.get("status") == "error":
            return gate
        if gate.get("status") != "ready":
            return _guided_error(
                error_code="RELEASE_GATE_BLOCKED",
                message=gate["message"],
                recovery_instruction="Wait until after end_date and >=75% participation before generating the collage.",
            )

        store = get_store()
        campaign = store.get_campaign(campaign_id)
        assert campaign is not None
        highlights = store.get_customer_highlights(campaign_id)
        if not highlights:
            return _guided_error(
                error_code="NO_CUSTOMER_HIGHLIGHTS",
                message=f"No Section 2 customer highlights found for campaign '{campaign_id}'.",
                recovery_instruction="Remind participants to complete Section 2 before generating the collage.",
            )

        img_path, html_path, photo_count = generate_collage_assets(
            campaign_id=campaign_id,
            team_name=campaign["team_name"],
            highlights=highlights,
            output_dir=get_settings().artifacts_dir,
        )
        return CollageOutput(
            campaign_id=campaign_id,
            collage_image_path=img_path,
            collage_html_path=html_path,
            highlight_count=len(highlights),
            photo_count=photo_count,
            message=f"Generated year-in-review collage with {len(highlights)} highlights and {photo_count} photos.",
        ).model_dump()


def send_retrospective_results_to_team(
    campaign_id: str,
    executive_summary_html: str,
    collage_image_path: str,
    as_of_date: str = "",
    tool_context: Optional[Any] = None,
) -> dict[str, Any]:
    """Distribute the final retrospective sentiment report and collage to all participants (gate + HITL).

    Args:
        campaign_id: Target retrospective campaign ID.
        executive_summary_html: Synthesized HTML summary of anonymous team sentiment and themes.
        collage_image_path: File path to the generated year-in-review PNG collage.
        as_of_date: Optional ISO date override (YYYY-MM-DD) for gate verification.
        tool_context: ADK ToolContext used to enforce human-in-the-loop confirmation.

    Returns:
        Dictionary matching SendResultsOutput or GuidedErrorResponse.
    """
    with trace_span("tool.send_retrospective_results_to_team", {"campaign_id": campaign_id}):
        gate = check_release_eligibility_gate(campaign_id=campaign_id, as_of_date=as_of_date)
        if gate.get("status") == "error":
            return gate
        if gate.get("status") != "ready":
            return _guided_error(
                error_code="RELEASE_GATE_BLOCKED",
                message=gate["message"],
                recovery_instruction="Cannot distribute results until end_date has passed and >=75% of participants responded.",
            )

        store = get_store()
        campaign = store.get_campaign(campaign_id)
        assert campaign is not None

        confirmed, hint = _check_hitl_confirmation(
            tool_context=tool_context,
            hint=(
                f"Confirm sending final retrospective sentiment report and collage to all "
                f"{campaign['participant_count']} participants of '{campaign['team_name']}'?"
            ),
            payload={
                "campaign_id": campaign_id,
                "recipient_count": campaign["participant_count"],
                "collage_image_path": collage_image_path,
            },
        )
        if not confirmed:
            return SendResultsOutput(
                status="confirmation_required",
                campaign_id=campaign_id,
                recipients_count=0,
                delivery_mode=get_settings().email_delivery_mode,
                message=f"Human confirmation required before sending final results: {hint}",
            ).model_dump()

        mode, previews = send_or_preview_email(
            campaign_id=campaign_id,
            recipients=campaign["participant_emails"],
            subject=f"[{campaign['team_name']}] Year-End Retrospective Results & Collage",
            html_body=executive_summary_html,
            attachment_paths=[collage_image_path],
        )
        store.mark_campaign_sent(campaign_id)
        return SendResultsOutput(
            status="success",
            campaign_id=campaign_id,
            recipients_count=len(campaign["participant_emails"]),
            delivery_mode=mode,
            preview_paths=previews,
            message=f"Retrospective results and collage sent to {len(campaign['participant_emails'])} participants via {mode}.",
        ).model_dump()
