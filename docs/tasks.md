# Background tasks

The default task backend executes async functions in the current process.
Start the shared engine during application startup and stop it during shutdown.
Both methods are coroutines and must be awaited.

```python
import asyncio

from nova import nova_task
from nova.tasks.engine import get_engine


async def main() -> None:
    engine = get_engine()
    completed = asyncio.Event()

    @nova_task(name="demo.greet")
    async def greet(person: str) -> None:
        print(f"Hello, {person}!")
        completed.set()

    await engine.start()
    try:
        task_id = await greet("Nova")
        print(f"Submitted: {task_id}")
        await asyncio.wait_for(completed.wait(), timeout=5)
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

Awaiting `greet()` submits the task and returns its ID. It does not wait for the
function's business result. The decorator uses `get_engine()`, so starting an
unrelated `NovaTaskEngine()` instance does not start the decorator's engine.
`engine.get_status(task_id)` exposes the current result/status when available.

The event in this example is process-local synchronization. It is not a durable
completion protocol. The default backend does not provide persistence across
process restarts; shutdown, retries, and failure handling must match your
application's needs. Installing the `tasks` extra does not create an external
worker. Blocking work should not run directly on the event loop.

The [dogfooding demo](quickstart.md) checks submission and completion separately.

::: nova.tasks.engine

::: nova.tasks.decorators
