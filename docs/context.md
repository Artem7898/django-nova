# Context semantics

`nova.core.context` stores metadata in Python `ContextVar` bindings and mirrors
it to structlog when available. A scope replaces all Nova bindings and restores
its enclosing bindings on exit, including exceptions and cancellation.

```python
from nova.core import context

context.bind(correlation_id="request-42", user_id=7)

with context.new_context(operation="invoice"):
    assert context.get_all() == {"operation": "invoice"}
    context.bind(invoice_id=123)

assert context.get_all() == {"correlation_id": "request-42", "user_id": 7}
context.clear()
```

## Binding and restoration

| Operation | Contract |
| --- | --- |
| `bind(**fields)` | Merge into the current mapping; supplied keys overwrite existing values. |
| `unbind(*keys)` | Remove only named keys; missing or repeated keys are harmless. |
| `clear()` | Clear bindings in the current execution context. |
| `get(key, default=None)` | Return the stored value, including `None`, `0`, or `False`; use the default only for a missing key. |
| `get_all()` | Return a shallow copy of the current mapping. |
| `new_context(**fields)` | Temporarily replace the whole mapping; restore the enclosing state using the `ContextVar` token. |

Nested scopes restore one level at a time. Changes made inside a scope with
`bind()`, `unbind()`, or `clear()` do not change the enclosing binding dictionary.
An empty `new_context()` temporarily hides all enclosing bindings.

Values are references, not deep copies. A mutable list or dictionary stored as a
value can still be shared across scopes or tasks. Prefer immutable metadata such
as IDs and strings, or make a separate copy of a mutable value before binding it.
Changing the top-level dictionary returned by `get_all()` cannot change bindings.

## Async tasks and cancellation

Use a regular `with` inside an async function and keep awaited work inside it:

```python
import asyncio

from nova.core import context


async def handle(request_id, receive):
    with context.new_context(correlation_id=request_id):
        await receive()


async def serve(receive_a, receive_b):
    async with asyncio.TaskGroup() as group:
        group.create_task(handle("a", receive_a))
        group.create_task(handle("b", receive_b))
```

An `asyncio.create_task()` call normally captures the creator's current Python
context at task creation. Later `bind()`, `unbind()`, and `clear()` calls in a
parent or sibling task do not change that task's binding dictionary. An explicit
`context=` argument to task creation follows Python's rules for the supplied
context instead.

When cancellation reaches an awaited operation inside `new_context()`, the scope
restores the enclosing bindings and propagates `CancelledError`. If application
code intentionally handles cancellation, subsequent work in that same task sees
the restored bindings. Nova does not suppress or clear a task's cancellation.

`@new_context(...)` can decorate a synchronous function. It is not an async
decorator: for `async def`, use `with new_context(...)` inside the function so the
scope includes coroutine execution.

## Threads, queues, and processes

On supported Python versions, `asyncio.to_thread()` copies the caller's context
to the worker. Worker changes do not flow back. Plain thread-pool submission does
not automatically copy the caller's context. If needed, use a fresh
`contextvars.copy_context()` for each submission and run the callable in that
context; do not enter the same `Context` concurrently from multiple threads.

A queued Nova task is executed by an existing worker. Submission through
`@nova_task` or `engine.submit()` does not itself capture the submitter's Nova
bindings. Include required metadata in the payload and open a scope in the task
body. Network requests and other processes likewise need explicit propagation.

## Logging bridge

Successful Nova context operations mirror the current bindings to structlog's
context variables. With Nova logging configured, `merge_contextvars` includes
those fields in log events. Nested scope exit also restores the enclosing Nova
fields in the logging bridge.

The existing bridge replaces all structlog context variables. Fields bound only
through `structlog.contextvars.bind_contextvars()` are not separately saved or
restored by Nova. Use Nova bindings consistently for metadata managed by this
bridge. This does not refer to fields bound on a structlog logger instance.

Missing structlog or an ordinary logging exception does not break Nova binding
or restoration, or replace an exception raised by the scope body. After a
logging failure the mirror may be incomplete; the next successful Nova operation
resynchronizes it. Provider exceptions derived directly from `BaseException`
are not suppressed.

## Verification

Run the focused behavioral suite with a combined line-and-branch coverage gate:

```bash
uv run --locked pytest -q tests/core/test_context.py \
  --cov=nova.core.context --cov-branch \
  --cov-report=term-missing --cov-fail-under=85
```

The Context stage adds tests for binding operations, nested and empty scopes,
exceptions, concurrent siblings, inherited task bindings, cancellation and
recovery, thread propagation, and logging failures. These tests do not require
PostgreSQL, Redis, or Memcached.

See the [Core API](api/core.md), [tracing guide](tracing.md), and
[verification guide](testing.md).
