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

    def __init__(
        self,
        channel_name: str,
        *,
        client: Any | None = None,
    ) -> None:
        self.channel_name = f"nova:pubsub:{channel_name}"
        self._client = client

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        from nova.redis.client import get_async_redis_client

        return get_async_redis_client()

    async def publish(self, data: dict[str, Any]) -> None:
        """
        Publish an event to the channel.
        """
        client = await self._get_client()

        await client.publish(
            self.channel_name,
            json.dumps(data),
        )

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        """
        Async generator that yields messages as they arrive.
        """
        client = await self._get_client()
        pubsub = client.pubsub()

        await pubsub.subscribe(self.channel_name)

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield json.loads(message["data"])
        finally:
            await pubsub.unsubscribe(self.channel_name)
            await pubsub.aclose()