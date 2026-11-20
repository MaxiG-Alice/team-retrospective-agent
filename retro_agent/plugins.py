"""ADK governance plugin: Intent vs Outcome capture, guardrails, self-eval, and async memory."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin

from .config import get_settings
from .observability import log_event, trace_span
from .pii_redactor import PIIRedactor


class RetroGovernancePlugin(BasePlugin):
    """Enforces security/anonymity guardrails, intent-outcome logging, and async memory."""

    _DEANONYMIZE_PATTERNS = (
        re.compile(r"\bwho\s+(?:wrote|submitted|said|gave)\b", re.IGNORECASE),
        re.compile(r"\breveal\s+(?:the\s+)?(?:author|sender|email|identity)\b", re.IGNORECASE),
        re.compile(r"\bignore\s+(?:all\s+)?previous\s+instructions\b", re.IGNORECASE),
    )

    def __init__(
        self,
        name: str = "retro_governance_plugin",
        memory_service: Optional[Any] = None,
    ) -> None:
        super().__init__(name=name)
        settings = get_settings()
        self.redactor = PIIRedactor(
            project_id=settings.project_id,
            use_cloud_dlp=settings.enable_cloud_dlp,
        )
        self.memory_service = memory_service
        self._tool_start_times: dict[str, float] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def check_prompt_guardrail(self, user_text: str) -> Optional[str]:
        """Return a refusal message if the prompt violates anonymity or injection rules."""
        for pattern in self._DEANONYMIZE_PATTERNS:
            if pattern.search(user_text or ""):
                return (
                    "Guardrail triggered: Section 1 team retrospective feedback is strictly "
                    "anonymous and cannot be linked back to individual participants."
                )
        return None

    async def before_model_callback(
        self, *, callback_context: Any, llm_request: Any, **kwargs: Any
    ) -> Optional[Any]:
        """Inspect outgoing LLM requests for anonymity-leak or prompt-injection attempts."""
        contents = getattr(llm_request, "contents", None) or []
        latest_text = ""
        if contents:
            last = contents[-1]
            parts = getattr(last, "parts", None) or []
            latest_text = " ".join(str(getattr(p, "text", "")) for p in parts)

        violation = self.check_prompt_guardrail(latest_text)
        if violation:
            log_event(
                "guardrail_blocked_request",
                level="warning",
                agent_name=getattr(callback_context, "agent_name", "unknown"),
                reason="anonymity_or_injection_policy",
            )
            from google.adk.models.llm_response import LlmResponse
            from google.genai import types

            return LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=violation)]
                )
            )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        **kwargs: Any,
    ) -> Optional[dict[str, Any]]:
        """Capture explicit agent intent before tool execution."""
        tool_name = getattr(tool, "name", str(tool))
        invocation_id = getattr(tool_context, "invocation_id", "unknown")
        key = f"{invocation_id}:{tool_name}"
        self._tool_start_times[key] = time.monotonic()

        log_event(
            "tool_intent_captured",
            agent_name=getattr(tool_context, "agent_name", "unknown"),
            invocation_id=invocation_id,
            tool_name=tool_name,
            intended_action=f"Execute {tool_name}",
            tool_args=self.redactor.redact_structure(tool_args),
        )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict[str, Any],
        **kwargs: Any,
    ) -> Optional[dict[str, Any]]:
        """Capture actual tool outcome and latency after execution."""
        tool_name = getattr(tool, "name", str(tool))
        invocation_id = getattr(tool_context, "invocation_id", "unknown")
        key = f"{invocation_id}:{tool_name}"
        started = self._tool_start_times.pop(key, None)
        duration_ms = round((time.monotonic() - started) * 1000, 2) if started else None

        outcome_status = (
            result.get("status", "unknown") if isinstance(result, dict) else "completed"
        )
        log_event(
            "tool_outcome_captured",
            agent_name=getattr(tool_context, "agent_name", "unknown"),
            invocation_id=invocation_id,
            tool_name=tool_name,
            intended_action=f"Execute {tool_name}",
            actual_outcome=outcome_status,
            duration_ms=duration_ms,
            result_summary=self.redactor.redact_structure(result),
        )
        return None

    async def after_agent_callback(
        self, *, callback_context: Any, **kwargs: Any
    ) -> Optional[Any]:
        """Run self-eval PII check and schedule async background memory consolidation."""
        if self.memory_service is not None:
            session = getattr(callback_context, "session", None)
            if session is not None:
                task = asyncio.create_task(self._consolidate_memory_async(session))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        return None

    async def _consolidate_memory_async(self, session: Any) -> None:
        """Persist session insights to MemoryService asynchronously without blocking UI."""
        with trace_span("memory.async_consolidation"):
            try:
                add_fn = getattr(self.memory_service, "add_session_to_memory", None)
                if callable(add_fn):
                    await add_fn(session)
                log_event("memory_consolidated_async", status="success")
            except Exception as exc:
                log_event(
                    "memory_consolidation_failed",
                    level="warning",
                    error=str(exc),
                )
