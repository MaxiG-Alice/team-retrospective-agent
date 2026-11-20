"""Structured JSON logging (with PII scrubbing) and OpenTelemetry tracing."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import get_settings
from .pii_redactor import PIIRedactor

_redactor = PIIRedactor(
    project_id=get_settings().project_id,
    use_cloud_dlp=get_settings().enable_cloud_dlp,
)


def _setup_tracer():
    """Initialize OpenTelemetry tracer with OTLP exporter if configured."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        settings = get_settings()
        resource = Resource.create({"service.name": settings.service_name})
        provider = TracerProvider(resource=resource)

        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
            )

        trace.set_tracer_provider(provider)
        return trace.get_tracer(settings.service_name)
    except Exception:
        return None


_tracer = _setup_tracer()


class _NoOpSpan:
    """Fallback span when OpenTelemetry SDK is not installed in a minimal env."""

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def record_exception(self, exc: BaseException) -> None:
        return None


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Create an OpenTelemetry span with PII-redacted attributes."""
    safe_attrs = _redactor.redact_structure(attributes or {})
    if _tracer is None:
        yield _NoOpSpan()
        return

    with _tracer.start_as_current_span(name) as span:
        for k, v in safe_attrs.items():
            if isinstance(v, (str, int, float, bool)):
                span.set_attribute(k, v)
            else:
                span.set_attribute(k, json.dumps(v, default=str))
        yield span


class _JsonLogFormatter(logging.Formatter):
    """Formats stdlib log records as single-line JSON with PII redaction."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        extra = getattr(record, "structured_fields", None)
        if isinstance(extra, dict):
            payload.update(_redactor.redact_structure(extra))
        else:
            payload["event"] = _redactor.redact_text(str(payload["event"]))
        return json.dumps(payload, default=str)


def _configure_logger() -> logging.Logger:
    logger = logging.getLogger("retro_agent")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonLogFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


_logger = _configure_logger()


def log_event(event: str, level: str = "info", **fields: Any) -> None:
    """Emit a structured, PII-redacted JSON log entry.

    Args:
        event: Short machine-readable event name (e.g., 'tool_intent_captured').
        level: Log level ('info', 'warning', 'error').
        **fields: Rich structured metadata attached to the JSON log record.
    """
    safe_fields = _redactor.redact_structure(fields)
    try:
        import structlog  # noqa: F401
        # Keep deterministic JSON output via our configured JSON handler.
    except ImportError:
        pass

    log_fn = getattr(_logger, level.lower(), _logger.info)
    log_fn(event, extra={"structured_fields": safe_fields})
