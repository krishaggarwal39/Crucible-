"""
OpenTelemetry wiring.

Why the lazy accessors matter
-----------------------------
Instruments capture whichever MeterProvider is installed at the moment they are
created. The previous version created `meter` and all four instruments at module
import time, while `set_meter_provider()` only ran inside `setup_telemetry()`.
Because import always happens first, every instrument bound permanently to the
default no-op provider — so even calling `setup_telemetry()` would not have made
metrics work.

Everything below therefore resolves the meter on first *use*, and
`setup_telemetry()` is called from the application's lifespan hook before any
request is served.
"""

import contextvars
import logging
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.semconv.resource import ResourceAttributes

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Global ContextVar for the current Evaluation Run ID
current_run_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_run_id", default=""
)

_configured = False


def setup_telemetry() -> bool:
    """
    Install the SDK tracer/meter providers.

    Returns True if providers were installed. Safe to call more than once.
    Disabled by default so a dev run is not flooded with console exporter output;
    enable with OTEL_ENABLED=true.
    """
    global _configured
    if _configured:
        return True
    if not settings.OTEL_ENABLED:
        logger.info("OpenTelemetry disabled (set OTEL_ENABLED=true to enable).")
        return False

    resource = Resource(
        attributes={
            ResourceAttributes.SERVICE_NAME: settings.OTEL_SERVICE_NAME,
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: settings.APP_ENV,
        }
    )

    span_exporter: Any = ConsoleSpanExporter()
    metric_exporter: Any = ConsoleMetricExporter()

    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT
            span_exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
            metric_exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics")
        except ImportError:
            logger.warning(
                "OTEL_EXPORTER_OTLP_ENDPOINT is set but the OTLP exporter package "
                "is not installed; falling back to console exporters."
            )

    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(trace_provider)

    reader = PeriodicExportingMetricReader(metric_exporter)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    _configured = True
    logger.info("OpenTelemetry initialised.")
    return True


def instrument_fastapi(app) -> None:
    """
    Attach FastAPI auto-instrumentation.

    opentelemetry-instrumentation-fastapi is a declared dependency but was never
    imported, so no HTTP spans were ever produced.
    """
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception as e:
        logger.warning(f"Could not instrument FastAPI: {e}")


# ── Lazy accessors ───────────────────────────────────────────────────────────

def get_meter():
    return metrics.get_meter("crucible.meter")


def get_tracer():
    return trace.get_tracer("crucible.tracer")


_instruments: dict[str, Any] = {}


def _instrument(key: str, factory):
    """Create-and-cache an instrument on first use, after providers are set."""
    if key not in _instruments:
        _instruments[key] = factory(get_meter())
    return _instruments[key]


def get_sse_active_connections():
    return _instrument(
        "sse_active_connections",
        lambda m: m.create_up_down_counter(
            name="crucible.sse.connections.active",
            description="Number of active SSE connections",
            unit="{connection}",
        ),
    )


def get_events_published_total():
    return _instrument(
        "events_published_total",
        lambda m: m.create_counter(
            name="crucible.events.published.total",
            description="Total number of events published",
            unit="{event}",
        ),
    )


def get_events_dropped_total():
    return _instrument(
        "events_dropped_total",
        lambda m: m.create_counter(
            name="crucible.events.dropped.total",
            description="Total number of events dropped due to errors or backpressure",
            unit="{event}",
        ),
    )


def get_events_publish_duration():
    return _instrument(
        "events_publish_duration",
        lambda m: m.create_histogram(
            name="crucible.events.publish.duration_seconds",
            description="Latency of publishing events to the broker",
            unit="s",
        ),
    )


def reset_for_testing() -> None:
    """Drop cached instruments so tests can re-bind against a fresh provider."""
    global _configured
    _instruments.clear()
    _configured = False
