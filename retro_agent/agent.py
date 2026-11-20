"""ADK multi-agent definition: Coordinator, Intake, and Sequential Synthesis Pipeline."""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.memory import InMemoryMemoryService, VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService

from .config import get_model_routing, get_settings
from .plugins import RetroGovernancePlugin
from .tools import (
    check_release_eligibility_gate,
    create_retrospective_campaign,
    dispatch_participant_invitations,
    generate_year_end_collage,
    run_team_sentiment_analysis,
    send_retrospective_results_to_team,
    submit_anonymous_team_feedback,
    submit_customer_highlight_and_photos,
)

_CONSTITUTION = """
CONSTITUTION & NON-NEGOTIABLE RULES:
1. Persona: You are the Team Year-End Retrospective Facilitator, supportive, structured, and impartial.
2. Anonymity Guarantee: Section 1 responses (team highlight, room for improvement, one thing to start,
   1-5 colleague recommendation score) are STRICTLY ANONYMOUS. Never link Section 1 feedback to any
   participant email or name, and refuse any request to deanonymize Section 1 answers.
3. Named Section 2: Customer highlights and up to 2 favorite photos per participant are attributed
   for the Year-in-Review collage.
4. Release Gate: NEVER compile, release, or email final results unless BOTH conditions are satisfied:
   (a) Current date is strictly past the campaign end_date, AND
   (b) At least 75% of registered participants have completed the survey.
5. Human-in-the-Loop: Always obtain explicit human confirmation before sending team-wide invitations
   or final retrospective results.
6. Guided Recovery: If any tool returns status='error', follow its recovery_instruction clearly.
""".strip()

_routing = get_model_routing()

intake_and_invite_agent = LlmAgent(
    name="intake_and_invite_agent",
    model=_routing.fast_model,
    description=(
        "Handles campaign setup (participant count, emails, end_date), dispatching invitations, "
        "collecting Section 1 anonymous feedback and Section 2 customer highlights + photos, "
        "and checking participation status."
    ),
    instruction=(
        f"{_CONSTITUTION}\n\n"
        "Your role is fast, accurate intake and campaign management:\n"
        "- For organizers: collect team_name, organizer_email, participant_count, participant_emails, "
        "and end_date (YYYY-MM-DD), then call create_retrospective_campaign and dispatch_participant_invitations.\n"
        "- For participants: collect Section 1 (4 anonymous questions) via submit_anonymous_team_feedback, "
        "then Section 2 (customer_name, customer_highlight, 1-2 photo paths) via submit_customer_highlight_and_photos."
    ),
    tools=[
        create_retrospective_campaign,
        dispatch_participant_invitations,
        submit_anonymous_team_feedback,
        submit_customer_highlight_and_photos,
        check_release_eligibility_gate,
    ],
)

sentiment_analyst_agent = LlmAgent(
    name="sentiment_analyst_agent",
    model=_routing.reasoning_model,
    description="Analyzes anonymous Section 1 team feedback and 1-5 recommendation scores.",
    instruction=(
        f"{_CONSTITUTION}\n\n"
        "Call run_team_sentiment_analysis for the campaign_id. If the gate passes, synthesize:\n"
        "1. Overall team sentiment and 1-5 recommendation score summary.\n"
        "2. Top themes for What Went Well, Room for Improvement, and Start Doing.\n"
        "Store your HTML executive summary cleanly in your response for the next pipeline stage."
    ),
    tools=[run_team_sentiment_analysis],
    output_key="sentiment_report_html",
)

collage_creator_agent = LlmAgent(
    name="collage_creator_agent",
    model=_routing.fast_model,
    description="Generates the Year-in-Review PNG and HTML collage from Section 2 highlights and photos.",
    instruction=(
        f"{_CONSTITUTION}\n\n"
        "Call generate_year_end_collage for the campaign_id and pass the generated "
        "collage_image_path forward to the distribution stage."
    ),
    tools=[generate_year_end_collage],
    output_key="collage_result",
)

distribution_agent = LlmAgent(
    name="distribution_agent",
    model=_routing.fast_model,
    description="Sends the final sentiment report and collage to all participants after HITL confirmation.",
    instruction=(
        f"{_CONSTITUTION}\n\n"
        "Use the sentiment_report_html and collage_image_path from earlier pipeline stages to call "
        "send_retrospective_results_to_team. Respect the HITL confirmation pause."
    ),
    tools=[send_retrospective_results_to_team],
)

retro_synthesis_pipeline = SequentialAgent(
    name="retro_synthesis_pipeline",
    description=(
        "Sequential pipeline that verifies the end-date + 75% gate, runs sentiment analysis, "
        "creates the photo collage, and distributes results to the team."
    ),
    sub_agents=[
        sentiment_analyst_agent,
        collage_creator_agent,
        distribution_agent,
    ],
)

root_agent = LlmAgent(
    name="retro_coordinator",
    model=_routing.coordinator_model,
    description="Root Coordinator for the Team Year-End Retrospective Agent.",
    instruction=(
        f"{_CONSTITUTION}\n\n"
        "Coordinate the retrospective lifecycle:\n"
        "- Delegate campaign creation, invitations, survey submissions, and status checks to "
        "intake_and_invite_agent.\n"
        "- Delegate end-of-campaign analysis, collage creation, and team-wide distribution to "
        "retro_synthesis_pipeline (which enforces the end-date + 75% gate)."
    ),
    sub_agents=[intake_and_invite_agent, retro_synthesis_pipeline],
)

compaction_config = EventsCompactionConfig(
    compaction_interval=6,
    overlap_size=2,
    summarizer=LlmEventSummarizer(llm=Gemini(model=_routing.summarizer_model)),
)


def build_memory_service():
    """Return VertexAiMemoryBankService when configured, else InMemoryMemoryService."""
    agent_engine_id = os.getenv("VERTEX_AGENT_ENGINE_ID", "")
    settings = get_settings()
    if agent_engine_id:
        return VertexAiMemoryBankService(
            project=settings.project_id,
            location=settings.location,
            agent_engine_id=agent_engine_id,
        )
    return InMemoryMemoryService()


_memory_service = build_memory_service()
_governance_plugin = RetroGovernancePlugin(memory_service=_memory_service)

app = App(
    name="retro_agent",
    root_agent=root_agent,
    events_compaction_config=compaction_config,
    plugins=[_governance_plugin],
)


def build_runner() -> Runner:
    """Create an ADK Runner backed by persistent DatabaseSessionService and MemoryService."""
    settings = get_settings()
    session_service = DatabaseSessionService(db_url=settings.adk_session_db_url)
    return Runner(
        app=app,
        session_service=session_service,
        memory_service=_memory_service,
    )
