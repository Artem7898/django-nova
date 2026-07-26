"""
Async Pub/Sub facade for real-time inter-process communication.
Useful for invalidating caches across multiple Gunicorn/Uvicorn workers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


class AsyncNovaPubSub:
    """
    Wrapper around redis.asyncio.PubSub for typed event streaming.
    """

    def __init__(self, channel_name: str) -> None:
        self.channel_name = f"nova:pubsub:{channel_name}"

    async def publish(self, data: dict[str, Any]) -> None:
        """Publish an event to the channel."""
        from nova.redis.client import get_async_redis_client
        client = get_async_redis_client()
        await client.publish(self.channel_name, json.dumps(data))

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        """
        Async generator that yields messages as they arrive.
        Usage:
            async for event in pubsub.listen():
                handle(event)
        """
        from nova.redis.client import get_async_redis_client
        client = get_async_redis_client()
        pubsub = client.pubsub()

        await pubsub.subscribe(self.channel_name)
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield json.loads(message["data"])
        finally:
            await pubsub.unsubscribe(self.channel_name)
            await pubsub.aclose()