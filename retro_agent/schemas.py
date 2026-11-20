"""Strict Pydantic v2 input/output schemas and guided error models."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class GuidedErrorResponse(BaseModel):
    """Structured error returned to the LLM with actionable recovery instructions."""

    status: Literal["error"] = "error"
    error_code: str = Field(..., description="Machine-readable error identifier.")
    message: str = Field(..., description="Human-readable explanation of what failed.")
    recovery_instruction: str = Field(
        ...,
        description="Explicit step-by-step guidance telling the LLM how to recover or ask the user.",
    )
    details: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="Additional structured context for debugging or user display.",
    )


class CreateCampaignInput(BaseModel):
    """Input schema for creating a new team retrospective campaign."""

    team_name: str = Field(..., min_length=2, max_length=120, description="Name of the core team.")
    organizer_email: str = Field(..., description="Email address of the retrospective organizer.")
    participant_count: int = Field(
        ..., ge=1, le=500, description="Expected number of participants."
    )
    participant_emails: list[str] = Field(
        ..., min_length=1, description="List of participant email addresses."
    )
    end_date: date = Field(
        ..., description="Survey deadline in YYYY-MM-DD format. Results release only after this date."
    )

    @field_validator("organizer_email")
    @classmethod
    def validate_organizer_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not _EMAIL_PATTERN.match(cleaned):
            raise ValueError(f"Invalid organizer email format: {value}")
        return cleaned

    @field_validator("participant_emails")
    @classmethod
    def validate_participant_emails(cls, emails: list[str]) -> list[str]:
        cleaned: list[str] = []
        for email in emails:
            item = email.strip().lower()
            if not _EMAIL_PATTERN.match(item):
                raise ValueError(f"Invalid participant email format: {email}")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned


class CreateCampaignOutput(BaseModel):
    """Output schema returned after creating a retrospective campaign."""

    status: Literal["success"] = "success"
    campaign_id: str
    team_name: str
    participant_count: int
    end_date: str
    min_required_responses: int
    message: str


class DispatchInvitationsInput(BaseModel):
    """Input schema for dispatching survey invitation emails."""

    campaign_id: str = Field(..., min_length=4, description="Target retrospective campaign ID.")
    custom_message: str = Field(
        default="",
        max_length=1000,
        description="Optional personal note from the organizer included in the invitation.",
    )


class DispatchInvitationsOutput(BaseModel):
    """Output schema for invitation dispatch results."""

    status: Literal["success", "confirmation_required"]
    campaign_id: str
    invitations_sent: int
    delivery_mode: str
    preview_paths: list[str] = Field(default_factory=list)
    message: str


class SubmitAnonymousFeedbackInput(BaseModel):
    """Input schema for Section 1 (strictly anonymous core team feedback)."""

    campaign_id: str = Field(..., min_length=4, description="Target retrospective campaign ID.")
    participant_email: str = Field(
        ...,
        description="Used ONLY to verify eligibility and mark participation; NEVER stored with answers.",
    )
    team_highlight: str = Field(
        ..., min_length=5, max_length=2000, description="Highlight of the year (What went well?)."
    )
    room_for_improvement: str = Field(
        ..., min_length=5, max_length=2000, description="Room for improvement (What didn't?)."
    )
    one_thing_to_start: str = Field(
        ..., min_length=3, max_length=1000, description="One thing the team should start doing."
    )
    recommend_score: int = Field(
        ..., ge=1, le=5, description="1 to 5 score on recommending the team to colleagues."
    )


class SubmitAnonymousFeedbackOutput(BaseModel):
    """Output schema confirming anonymous feedback submission."""

    status: Literal["success"] = "success"
    campaign_id: str
    response_id: str
    anonymized: bool = True
    completed_count: int
    participant_count: int
    participation_rate: float
    message: str


class SubmitCustomerHighlightInput(BaseModel):
    """Input schema for Section 2 (named customer highlight + up to 2 favorite photos)."""

    campaign_id: str = Field(..., min_length=4, description="Target retrospective campaign ID.")
    participant_email: str = Field(..., description="Participant email address.")
    customer_name: str = Field(
        ..., min_length=2, max_length=120, description="Customer name for the highlight."
    )
    customer_highlight: str = Field(
        ..., min_length=5, max_length=2000, description="Personal customer highlight of the year."
    )
    photo_paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=2,
        description="Paths or URLs to the participant's 1 to 2 favorite pictures of the year.",
    )


class SubmitCustomerHighlightOutput(BaseModel):
    """Output schema confirming Section 2 submission."""

    status: Literal["success"] = "success"
    campaign_id: str
    highlight_id: str
    photo_count: int
    message: str


class ReleaseGateStatusOutput(BaseModel):
    """Output schema describing whether the end-date and 75% gate have passed."""

    status: Literal["ready", "blocked"]
    campaign_id: str
    is_past_end_date: bool
    end_date: str
    current_date: str
    completed_count: int
    participant_count: int
    participation_rate: float
    required_rate: float = 0.75
    meets_participation_threshold: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    message: str


class SentimentSummaryOutput(BaseModel):
    """Output schema for the aggregated anonymous team sentiment analysis."""

    status: Literal["success"] = "success"
    campaign_id: str
    response_count: int
    average_recommend_score: float
    score_distribution: dict[str, int]
    positive_themes: list[str]
    improvement_themes: list[str]
    start_doing_ideas: list[str]
    overall_sentiment: Literal["positive", "mixed", "needs_attention"]


class CollageOutput(BaseModel):
    """Output schema for the generated year-in-review collage."""

    status: Literal["success"] = "success"
    campaign_id: str
    collage_image_path: str
    collage_html_path: str
    highlight_count: int
    photo_count: int
    message: str


class SendResultsOutput(BaseModel):
    """Output schema for final retrospective results distribution."""

    status: Literal["success", "confirmation_required"]
    campaign_id: str
    recipients_count: int
    delivery_mode: str
    preview_paths: list[str] = Field(default_factory=list)
    message: str
