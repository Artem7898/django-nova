# Tracing

Import tracing helpers from `nova.core.tracing`:

```python
from nova.core.tracing import nova_span, trace_task


@trace_task(operation="calculate")
async def calculate_total(prices: list[int]) -> int:
    return sum(prices)


async def fetch_and_measure(fetch):
    with nova_span("article.fetch", component="service"):
        return await fetch()
```

Keep awaited work inside the span. The tracing decorators handle ordinary and
async functions. `nova_span()` can yield `None` when a tracer is unavailable;
check the yielded value before calling span methods directly.

The `tracing` extra installs the OpenTelemetry API. The `observability` extra also
installs SDK/exporter dependencies. Configure a provider and exporter in your
application if you want to collect spans; installing an extra alone does not
send telemetry.

Nova guards its own telemetry setup, recording, and teardown against ordinary
exceptions while retaining business results, exceptions, and cancellation.
Application calls made directly on a yielded span are outside those guards.
Provider failures derived directly from `BaseException` are not suppressed.

Context bindings live in `nova.core.context`. They are local to the current
Python context; propagating data across processes or a network requires an
explicit transport. See [Context semantics](context.md) for nested restoration,
async task isolation, cancellation, and the logging bridge's boundaries.

See the [Core API](api/core.md) for the full signatures.
