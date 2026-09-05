"""Tests for Nova async Redis Pub/Sub."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova.redis.pubsub import AsyncNovaPubSub


class FakePubSub:
    """Minimal async Redis Pub/Sub test double."""

    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages
        self.subscribe = AsyncMock()
        self.unsubscribe = AsyncMock()
        self.aclose = AsyncMock()

    def listen(self) -> FakePubSub:
        """Return the async iterator used by redis-py's Pub/Sub API."""
        return self

    def __aiter__(self) -> FakePubSub:
        self._iterator = iter(self.messages)
        return self

    async def __anext__(self) -> dict:
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration from None


def make_client(pubsub: FakePubSub | None = None) -> MagicMock:
    """Create a fake Redis client."""
    client = MagicMock()

    client.publish = AsyncMock()

    if pubsub is None:
        pubsub = FakePubSub([])

    client.pubsub.return_value = pubsub

    return client


@pytest.mark.asyncio
async def test_publish_uses_prefixed_channel() -> None:
    """Published messages use the Nova channel namespace."""
    client = make_client()

    pubsub = AsyncNovaPubSub("events", client=client)

    payload = {"event": "created", "id": 123}

    result = await pubsub.publish(payload)

    assert result is None

    client.publish.assert_awaited_once_with(
        "nova:pubsub:events",
        json.dumps(payload),
    )


@pytest.mark.asyncio
async def test_publish_serializes_payload_as_json() -> None:
    """Payloads are serialized to JSON before publishing."""
    client = make_client()

    pubsub = AsyncNovaPubSub("events", client=client)

    payload = {
        "event": "user.created",
        "user": {
            "id": 42,
            "name": "Artem",
        },
        "tags": ["python", "django"],
    }

    await pubsub.publish(payload)

    client.publish.assert_awaited_once_with(
        "nova:pubsub:events",
        json.dumps(payload),
    )


@pytest.mark.asyncio
async def test_publish_supports_nested_payloads() -> None:
    """Complex nested dictionaries can be published."""
    client = make_client()

    pubsub = AsyncNovaPubSub("orders", client=client)

    payload = {
        "order": {
            "id": 1001,
            "items": [
                {"sku": "A-1", "quantity": 2},
                {"sku": "B-2", "quantity": 5},
            ],
        },
        "metadata": {
            "source": "api",
            "version": 1,
        },
    }

    await pubsub.publish(payload)

    client.publish.assert_awaited_once_with(
        "nova:pubsub:orders",
        json.dumps(payload),
    )


@pytest.mark.asyncio
async def test_subscribe_uses_prefixed_channel() -> None:
    """Subscription uses the same Nova channel namespace."""
    fake_pubsub = FakePubSub([])

    client = make_client(fake_pubsub)

    pubsub = AsyncNovaPubSub("events", client=client)

    messages = [message async for message in pubsub.listen()]

    assert messages == []

    client.pubsub.assert_called_once_with()

    fake_pubsub.subscribe.assert_awaited_once_with(
        "nova:pubsub:events",
    )


@pytest.mark.asyncio
async def test_listen_yields_message_payload() -> None:
    """JSON message payloads are decoded and yielded."""
    fake_pubsub = FakePubSub(
        [
            {
                "type": "message",
                "data": '{"event": "created", "id": 123}',
            }
        ]
    )

    client = make_client(fake_pubsub)

    pubsub = AsyncNovaPubSub("events", client=client)

    messages = [message async for message in pubsub.listen()]

    assert messages == [
        {
            "event": "created",
            "id": 123,
        }
    ]


@pytest.mark.asyncio
async def test_listen_yields_multiple_messages() -> None:
    """Multiple Pub/Sub messages are decoded independently."""
    fake_pubsub = FakePubSub(
        [
            {
                "type": "message",
                "data": '{"event": "created", "id": 1}',
            },
            {
                "type": "message",
                "data": '{"event": "updated", "id": 1}',
            },
            {
                "type": "message",
                "data": '{"event": "deleted", "id": 1}',
            },
        ]
    )

    client = make_client(fake_pubsub)

    pubsub = AsyncNovaPubSub("events", client=client)

    messages = [message async for message in pubsub.listen()]

    assert messages == [
        {"event": "created", "id": 1},
        {"event": "updated", "id": 1},
        {"event": "deleted", "id": 1},
    ]


@pytest.mark.asyncio
async def test_listen_ignores_non_message_events() -> None:
    """Subscribe and other events are not yielded as messages."""
    fake_pubsub = FakePubSub(
        [
            {
                "type": "subscribe",
                "data": 1,
            },
            {
                "type": "message",
                "data": '{"event": "created"}',
            },
            {
                "type": "pong",
                "data": "pong",
            },
        ]
    )

    client = make_client(fake_pubsub)

    pubsub = AsyncNovaPubSub("events", client=client)

    messages = [message async for message in pubsub.listen()]

    assert messages == [{"event": "created"}]


@pytest.mark.asyncio
async def test_listen_supports_bytes_json_payload() -> None:
    """Redis byte payloads are accepted by json.loads."""
    fake_pubsub = FakePubSub(
        [
            {
                "type": "message",
                "data": b'{"event": "created", "id": 42}',
            }
        ]
    )

    client = make_client(fake_pubsub)

    pubsub = AsyncNovaPubSub("events", client=client)

    messages = [message async for message in pubsub.listen()]

    assert messages == [
        {
            "event": "created",
            "id": 42,
        }
    ]


@pytest.mark.asyncio
async def test_listen_unsubscribes_and_closes_on_normal_completion() -> None:
    """Normal listener completion cleans up the Pub/Sub object."""
    fake_pubsub = FakePubSub([])

    client = make_client(fake_pubsub)

    pubsub = AsyncNovaPubSub("events", client=client)

    messages = [message async for message in pubsub.listen()]

    assert messages == []

    fake_pubsub.subscribe.assert_awaited_once_with(
        "nova:pubsub:events",
    )

    fake_pubsub.unsubscribe.assert_awaited_once_with(
        "nova:pubsub:events",
    )

    fake_pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_listen_cleanup_when_generator_is_closed() -> None:
    """Closing the async generator triggers Pub/Sub cleanup."""
    fake_pubsub = FakePubSub(
        [
            {
                "type": "message",
                "data": '{"event": "created"}',
            }
        ]
    )

    client = make_client(fake_pubsub)

    pubsub = AsyncNovaPubSub("events", client=client)

    generator = pubsub.listen()

    first_message = await anext(generator)

    assert first_message == {"event": "created"}

    await generator.aclose()

    fake_pubsub.unsubscribe.assert_awaited_once_with(
        "nova:pubsub:events",
    )

    fake_pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_listen_cleanup_on_exception() -> None:
    """Pub/Sub resources are cleaned up when iteration fails."""

    class FailingPubSub(FakePubSub):
        def listen(self) -> FailingPubSub:
            return self

        def __aiter__(self) -> FailingPubSub:
            self._iteration_started = False
            return self

        async def __anext__(self) -> dict:
            if not self._iteration_started:
                self._iteration_started = True

                return {
                    "type": "message",
                    "data": '{"event": "created"}',
                }

            raise RuntimeError("Redis connection lost")

    fake_pubsub = FailingPubSub([])

    client = make_client(fake_pubsub)

    pubsub = AsyncNovaPubSub("events", client=client)

    generator = pubsub.listen()

    assert await anext(generator) == {"event": "created"}

    with pytest.raises(RuntimeError, match="Redis connection lost"):
        await anext(generator)

    await generator.aclose()

    fake_pubsub.unsubscribe.assert_awaited_once_with(
        "nova:pubsub:events",
    )

    fake_pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_lazy_client_resolution() -> None:
    """Redis client is resolved lazily when no client is injected."""
    client = make_client()

    with patch(
        "nova.redis.client.get_async_redis_client",
        return_value=client,
    ) as factory:
        pubsub = AsyncNovaPubSub("events")

        result = await pubsub.publish({"event": "created"})

    assert result is None

    factory.assert_called_once_with()

    client.publish.assert_awaited_once_with(
        "nova:pubsub:events",
        json.dumps({"event": "created"}),
    )


@pytest.mark.asyncio
async def test_injected_client_bypasses_global_factory() -> None:
    """An explicitly injected Redis client is used directly."""
    client = make_client()

    with patch(
        "nova.redis.client.get_async_redis_client",
    ) as factory:
        pubsub = AsyncNovaPubSub("events", client=client)

        result = await pubsub.publish({"event": "created"})

    assert result is None

    factory.assert_not_called()

    client.publish.assert_awaited_once_with(
        "nova:pubsub:events",
        json.dumps({"event": "created"}),
    )


@pytest.mark.asyncio
async def test_lazy_client_is_used_for_listen() -> None:
    """listen() resolves the Redis client lazily."""
    fake_pubsub = FakePubSub(
        [
            {
                "type": "message",
                "data": '{"event": "created"}',
            }
        ]
    )

    client = make_client(fake_pubsub)

    with patch(
        "nova.redis.client.get_async_redis_client",
        return_value=client,
    ) as factory:
        pubsub = AsyncNovaPubSub("events")

        messages = [message async for message in pubsub.listen()]

    assert messages == [{"event": "created"}]

    factory.assert_called_once_with()

    client.pubsub.assert_called_once_with()


@pytest.mark.asyncio
async def test_injected_client_is_used_for_listen() -> None:
    """listen() uses the injected Redis client."""
    fake_pubsub = FakePubSub(
        [
            {
                "type": "message",
                "data": '{"event": "created"}',
            }
        ]
    )

    client = make_client(fake_pubsub)

    with patch(
        "nova.redis.client.get_async_redis_client",
    ) as factory:
        pubsub = AsyncNovaPubSub("events", client=client)

        messages = [message async for message in pubsub.listen()]

    assert messages == [{"event": "created"}]

    factory.assert_not_called()

    client.pubsub.assert_called_once_with()


@pytest.mark.asyncio
async def test_publish_does_not_modify_original_payload() -> None:
    """Publishing does not mutate the caller's payload."""
    client = make_client()

    pubsub = AsyncNovaPubSub("events", client=client)

    payload = {
        "event": "created",
        "metadata": {
            "source": "test",
        },
    }

    original_payload = {
        "event": "created",
        "metadata": {
            "source": "test",
        },
    }

    await pubsub.publish(payload)

    assert payload == original_payload


@pytest.mark.asyncio
async def test_listen_preserves_json_data_types() -> None:
    """Decoded JSON values retain their native Python types."""
    fake_pubsub = FakePubSub(
        [
            {
                "type": "message",
                "data": json.dumps(
                    {
                        "string": "value",
                        "integer": 42,
                        "float": 3.14,
                        "boolean": True,
                        "null": None,
                        "list": [1, 2, 3],
                    }
                ),
            }
        ]
    )

    client = make_client(fake_pubsub)

    pubsub = AsyncNovaPubSub("events", client=client)

    messages = [message async for message in pubsub.listen()]

    assert messages == [
        {
            "string": "value",
            "integer": 42,
            "float": 3.14,
            "boolean": True,
            "null": None,
            "list": [1, 2, 3],
        }
    ]


@pytest.mark.asyncio
async def test_channel_names_are_isolated() -> None:
    """Different channels use different namespaced Redis channels."""
    events_client = make_client()
    orders_client = make_client()

    events = AsyncNovaPubSub(
        "events",
        client=events_client,
    )

    orders = AsyncNovaPubSub(
        "orders",
        client=orders_client,
    )

    await events.publish({"event": "created"})
    await orders.publish({"event": "created"})

    events_client.publish.assert_awaited_once_with(
        "nova:pubsub:events",
        json.dumps({"event": "created"}),
    )

    orders_client.publish.assert_awaited_once_with(
        "nova:pubsub:orders",
        json.dumps({"event": "created"}),
    )
