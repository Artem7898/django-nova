"""Single-attempt metadata writes, independent of ordinary cache retries."""

from __future__ import annotations

import inspect
from contextlib import suppress
from typing import Any

from .generation import GenerationUnavailableError, GenerationWriter


class RedisGenerationWriter:
    """Borrow a pooled connection and send one SET, without command retry.

    Connection establishment may retry before the SET is sent. A failure
    during send/read discards that connection, including unread replies.
    No client or pool retry configuration is modified.
    """

    def __init__(self, client: Any) -> None:
        self._client: Any = client
        self._pool: Any = client.connection_pool
        # redis-py 5.0 requires command_name; 5.3+ deprecates supplying it.
        parameter = inspect.signature(self._pool.get_connection).parameters.get("command_name")
        self._requires_command = (
            parameter is not None and parameter.default is inspect.Parameter.empty
        )

    def write(self, key: str, token: bytes, *, only_if_absent: bool) -> bool:
        pool: Any = self._pool
        connection: Any = self._client.connection
        pinned = connection is not None
        if pinned:
            self._client.single_connection_lock.acquire()
        else:
            connection = (
                pool.get_connection("SET") if self._requires_command else pool.get_connection()
            )
        try:
            arguments = ("SET", key, token, "NX") if only_if_absent else ("SET", key, token)
            connection.send_command(*arguments)
            response: object = connection.read_response()
            if response in (b"OK", "OK"):
                return True
            if response is None and only_if_absent:
                return False
            raise GenerationUnavailableError("Redis returned an unexpected generation response")
        except BaseException:
            # Also discard the connection on cancellation or interruption.
            # Cleanup must not replace the original transport exception.
            with suppress(Exception):
                connection.disconnect()
            raise
        finally:
            if pinned:
                self._client.single_connection_lock.release()
            else:
                pool.release(connection)


class MemcachedGenerationWriter:
    """Use plain Client/PooledClient acknowledged SET or ADD exactly once."""

    def __init__(self, client: Any) -> None:
        self._client: Any = client

    def write(self, key: str, token: bytes, *, only_if_absent: bool) -> bool:
        method: Any = self._client.add if only_if_absent else self._client.set
        response: object = method(key, token, expire=0, noreply=False)
        if response is True:
            return True
        if response is False and only_if_absent:
            return False
        raise GenerationUnavailableError("Memcached did not acknowledge generation write")


def redis_generation_writer(client: Any) -> GenerationWriter:
    """Accept known standalone transports; custom clients need explicit DI."""
    from redis import Redis
    from redis.connection import (
        BlockingConnectionPool,
        Connection,
        ConnectionPool,
        SSLConnection,
        UnixDomainSocketConnection,
    )

    if (
        type(client) is not Redis
        or type(client.connection_pool) not in (ConnectionPool, BlockingConnectionPool)
        or client.connection_pool.connection_class
        not in (Connection, SSLConnection, UnixDomainSocketConnection)
        or getattr(client.connection_pool, "cache", None) is not None
    ):
        raise GenerationUnavailableError(
            "Custom Redis transports require an explicit single-attempt generation_writer"
        )
    return RedisGenerationWriter(client)


def memcached_generation_writer(client: Any) -> GenerationWriter:
    from pymemcache.client.base import Client, PooledClient

    if type(client) not in (Client, PooledClient):
        raise GenerationUnavailableError(
            "Custom Memcached transports require an explicit single-attempt generation_writer"
        )
    return MemcachedGenerationWriter(client)
