import contextvars
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, BatchSpanProcessor

# Global ContextVar for the current Evaluation Run ID
current_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_run_id", default="")

def setup_telemetry():
    """Initializes OpenTelemetry Metrics and Tracing for the application."""
    resource = Resource(attributes={
        ResourceAttributes.SERVICE_NAME: "crucible"
    })
    
    # Trace setup
    trace_provider = TracerProvider(resource=resource)
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    trace_provider.add_span_processor(processor)
    trace.set_tracer_provider(trace_provider)
    
    # Metrics setup
    reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

# Convenience accessors
def get_meter():
    return metrics.get_meter("crucible.meter")

def get_tracer():
    return trace.get_tracer("crucible.tracer")

# Defined Metrics
meter = get_meter()

sse_active_connections = meter.create_up_down_counter(
    name="crucible.sse.connections.active",
    description="Number of active SSE connections",
    unit="{connection}"
)

events_published_total = meter.create_counter(
    name="crucible.events.published.total",
    description="Total number of events published",
    unit="{event}"
)

events_dropped_total = meter.create_counter(
    name="crucible.events.dropped.total",
    description="Total number of events dropped due to errors or backpressure",
    unit="{event}"
)

events_publish_duration = meter.create_histogram(
    name="crucible.events.publish.duration_seconds",
    description="Latency of publishing events to the broker",
    unit="s"
)
