"""
Contract tests for async pub/sub facade.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from nova.redis.pubsub import AsyncNovaPubSub


class FakePubSub:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def aclose(self) -> None:
        self.closed = True

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        for message in self.messages:
            yield message


class FakeRedisPubSubClient:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        self.published: list[tuple[str, str]] = []
        self.pubsub_obj: FakePubSub | None = None

    async def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1

    def pubsub(self) -> FakePubSub:
        self.pubsub_obj = FakePubSub(self.messages)
        return self.pubsub_obj


async def test_publish_serializes_json() -> None:
    client = FakeRedisPubSubClient([])
    pubsub = AsyncNovaPubSub("cache", client=client)

    await pubsub.publish({"model": "lab"})

    channel, payload = client.published[0]

    assert channel == "nova:pubsub:cache"
    assert json.loads(payload) == {"model": "lab"}


async def test_listen_yields_message_events_only() -> None:
    messages = [
        {"type": "subscribe", "data": 1},
        {
            "type": "message",
            "data": json.dumps({"model": "lab"}),
        },
    ]

    client = FakeRedisPubSubClient(messages)
    pubsub = AsyncNovaPubSub("cache", client=client)

    received: list[dict[str, Any]] = []

    async for event in pubsub.listen():
        received.append(event)

    assert received == [{"model": "lab"}]

    assert client.pubsub_obj is not None
    assert client.pubsub_obj.subscribed == ["nova:pubsub:cache"]
    assert client.pubsub_obj.unsubscribed == ["nova:pubsub:cache"]
    assert client.pubsub_obj.closed is True