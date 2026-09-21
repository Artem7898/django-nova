"""A transport failure must never resend a generation mutation."""

from threading import Lock
from types import SimpleNamespace

import pytest

from nova.cache.generation import GenerationUnavailableError
from nova.cache.generation_transport import RedisGenerationWriter


class ConnectionDouble:
    def __init__(self, response=b"OK", error=None, phase="read"):
        self.response = response
        self.error = error
        self.phase = phase
        self.commands = []
        self.disconnected = False

    def send_command(self, *args):
        self.commands.append(args)
        if self.error and self.phase == "send":
            raise self.error

    def read_response(self):
        if self.error and self.phase == "read":
            raise self.error
        return self.response

    def disconnect(self):
        self.disconnected = True


class PoolDouble:
    def __init__(self, connection):
        self.connection = connection
        self.released = []

    def get_connection(self):
        return self.connection

    def release(self, connection):
        self.released.append(connection)


class LegacyPoolDouble(PoolDouble):
    def get_connection(self, command_name):
        assert command_name == "SET"
        return self.connection


@pytest.mark.parametrize("pool_type", [PoolDouble, LegacyPoolDouble])
@pytest.mark.parametrize("nx", [False, True])
@pytest.mark.parametrize("response", [b"OK", "OK"])
def test_acknowledged_write_is_sent_once(pool_type, nx, response):
    connection = ConnectionDouble(response)
    pool = pool_type(connection)
    writer = RedisGenerationWriter(SimpleNamespace(connection_pool=pool, connection=None))
    assert writer.write("scope", b"token", only_if_absent=nx) is True
    expected = ("SET", "scope", b"token", "NX") if nx else ("SET", "scope", b"token")
    assert connection.commands == [expected]
    assert pool.released == [connection]
    assert not connection.disconnected


@pytest.mark.parametrize("phase", ["send", "read"])
@pytest.mark.parametrize("error", [OSError("lost response"), KeyboardInterrupt()])
def test_failed_attempt_disconnects_and_releases_without_replay(phase, error):
    connection = ConnectionDouble(error=error, phase=phase)
    pool = PoolDouble(connection)
    writer = RedisGenerationWriter(SimpleNamespace(connection_pool=pool, connection=None))
    with pytest.raises(type(error)) as raised:
        writer.write("scope", b"token", only_if_absent=False)
    assert raised.value is error
    assert len(connection.commands) == 1
    assert connection.disconnected
    assert pool.released == [connection]


def test_nx_conflict_is_an_acknowledged_non_write():
    connection = ConnectionDouble(response=None)
    pool = PoolDouble(connection)
    writer = RedisGenerationWriter(SimpleNamespace(connection_pool=pool, connection=None))
    assert writer.write("scope", b"token", only_if_absent=True) is False
    assert len(connection.commands) == 1
    assert not connection.disconnected


@pytest.mark.parametrize("response", [None, False, b"unexpected"])
def test_unexpected_unconditional_reply_is_not_success(response):
    connection = ConnectionDouble(response=response)
    pool = PoolDouble(connection)
    writer = RedisGenerationWriter(SimpleNamespace(connection_pool=pool, connection=None))
    with pytest.raises(GenerationUnavailableError):
        writer.write("scope", b"token", only_if_absent=False)
    assert connection.disconnected
    assert pool.released == [connection]


@pytest.mark.parametrize("fail", [False, True])
def test_pinned_connection_lock_is_released(fail):
    error = OSError("lost response") if fail else None
    connection = ConnectionDouble(error=error)
    pool = PoolDouble(connection)
    lock = Lock()
    client = SimpleNamespace(
        connection_pool=pool, connection=connection, single_connection_lock=lock
    )
    writer = RedisGenerationWriter(client)
    if fail:
        with pytest.raises(OSError):
            writer.write("scope", b"token", only_if_absent=False)
    else:
        assert writer.write("scope", b"token", only_if_absent=False)
    assert lock.acquire(blocking=False)
    lock.release()
    assert pool.released == []
    assert len(connection.commands) == 1
