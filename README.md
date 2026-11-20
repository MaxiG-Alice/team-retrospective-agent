# Team Year-End Retrospective Agent

A multi-agent **Google ADK** application that facilitates a team's year-end retrospective:

1. **Organizer Setup & Invitations:** Registers participant count, emails, and an end date, then dispatches invitations (HITL-gated).
2. **Two-Part Survey Intake:**
   - **Section 1 (Strictly Anonymous + PII-Redacted):**
     1. Highlight of the year relating to our core team (What went well?)
     2. Room for improvement in our core team (What didn't?)
     3. One thing to start doing
     4. 1–5 recommendation score for colleagues
   - **Section 2 (Named Customer Memories):**
     1. Personal customer highlight (with customer name)
     2. Up to 2 favorite photos of the year
3. **Strict Release Gate:** Results are compiled and sent **only after `end_date` has passed AND at least 75% of participants have completed the survey**.
4. **Synthesis & Distribution:** Runs sentiment analysis on Section 1, generates a PNG + HTML Year-in-Review collage from Section 2, and distributes the final package to all participants after human confirmation.

## Architecture & AgentOps Rubric Alignment

- **Tool & Interface Design:** All 8 tools in `retro_agent/tools.py` use specific action-oriented names, full Google-style docstrings, strict Pydantic v2 schemas (`retro_agent/schemas.py`), and `GuidedErrorResponse` recovery instructions.
- **Context & Memory:** Constitution system prompt (`retro_agent/agent.py`), ADK `EventsCompactionConfig` with `LlmEventSummarizer`, persistent SQLite/DB session & campaign state (`DatabaseSessionService` + `RetroStore`), and async background memory consolidation (`RetroGovernancePlugin.after_agent_callback`).
- **Orchestration & Logic:** Coordinator (`retro_coordinator`, `gemini-2.5-pro`) + Intake Specialist (`intake_and_invite_agent`, `gemini-2.5-flash`) + `SequentialAgent` synthesis pipeline (`sentiment_analyst_agent` on `gemini-2.5-pro`, `collage_creator_agent` & `distribution_agent` on `gemini-2.5-flash`), plus anonymity/injection guardrails and `ToolConfirmation` HITL hooks.
- **Observability & Tracing:** Structured JSON logging (`retro_agent/observability.py`), explicit **intent vs. outcome** capture (`before_tool_callback` / `after_tool_callback`), OpenTelemetry distributed tracing, and active PII redaction (`retro_agent/pii_redactor.py` using Google Cloud DLP + regex fallback).
- **Infrastructure & CI/CD:** Terraform IaC (`deployment/terraform/`), Secret Manager integration (`retro_agent/config.py`), GitHub Actions CI (`.github/workflows/ci.yml`), and golden evaluation suite (`evals/` + `tests/`).

## Quickstart (Local / Cloud Shell)

### 1. Scaffold or Run with Google `agents-cli` / `adk`

```bash
# Install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env

# Run ADK Web UI (use --allow_origins="regex:.*" in Cloud Shell Web Preview)
adk web . --allow_origins="regex:.*" --otel_to_cloud
```

> Tip: You can also bootstrap cloud deployment scaffolding using `uvx google-agents-cli setup` or `uvx agent-starter-pack create`.

### 2. Run Automated Tests & Golden Evaluation

```bash
# Unit & golden regression tests
pytest -v

# ADK CLI evaluation against golden dataset
adk eval retro_agent evals/retro_eval.evalset.json --config_file_path=evals/test_config.json
```

### 3. Provision GCP Infrastructure (Terraform)

```bash
cd deployment/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

Store your SMTP password in Google Cloud Secret Manager (`smtp-password`) instead of `.env` when enabling real email delivery (`EMAIL_DELIVERY_MODE=smtp` and `ENABLE_SECRET_MANAGER=true`).
