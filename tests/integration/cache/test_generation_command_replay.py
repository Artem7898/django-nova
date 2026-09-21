"""Lost acknowledgements over real servers must not cause metadata replay."""

import os
from contextlib import ExitStack
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from nova.cache.generation import GenerationUnavailableError, generation_key
from tests.cache import test_generation_command_replay as replay

pytestmark = pytest.mark.integration


@pytest.fixture(params=["redis", "memcached"])
def real_replay_case(request):
    kind = request.param
    variable = "NOVA_TEST_REDIS_URL" if kind == "redis" else "NOVA_TEST_MEMCACHED_SERVER"
    address = os.environ.get(variable)
    if not address:
        pytest.skip(f"Set {variable} to run the real {kind} replay contract")
    prefix = f"nova-replay-it:{uuid4().hex}"
    with ExitStack() as resources:
        clients = []
        for _ in range(2):
            if kind == "redis":
                from redis import Redis
                from redis.backoff import NoBackoff
                from redis.exceptions import TimeoutError as RedisTimeoutError
                from redis.retry import Retry

                client = Redis.from_url(
                    address,
                    decode_responses=False,
                    socket_connect_timeout=3,
                    socket_timeout=3,
                    retry=Retry(NoBackoff(), 2),
                    retry_on_error=[RedisTimeoutError],
                )
                resources.callback(client.close)
                assert client.ping()
            else:
                from pymemcache.client.base import Client

                endpoint = urlsplit(f"//{address}")
                assert endpoint.hostname and endpoint.port, "Expected host:port or [IPv6]:port"
                client = Client(
                    (endpoint.hostname, endpoint.port),
                    key_prefix=f"{prefix}:".encode("ascii"),
                    default_noreply=False,
                    connect_timeout=3,
                    timeout=3,
                )
                resources.callback(client.close)
                assert client.version()
            clients.append(client)
        case = replay.make_case(kind, *clients, prefix=prefix)
        try:
            yield case
        finally:
            for key in case.keys:
                case.peer.delete(key)
            for scope in (replay.SCOPE, replay.WILDCARD):
                case.peer.delete(generation_key(scope))


@pytest.mark.parametrize("mode", ["acknowledge", "lose-reply"])
def test_rotation_interleaving_on_server(real_replay_case, mode):
    replay.run_rotation_interleaving(real_replay_case, mode)


@pytest.mark.parametrize("mode", ["acknowledge", "lose-reply"])
def test_initialization_interleaving_on_server(real_replay_case, mode):
    replay.run_initialization_interleaving(real_replay_case, mode)


@pytest.mark.parametrize("real_replay_case", ["redis"], indirect=True)
@pytest.mark.parametrize("operation", ["initialize", "rotate"])
def test_redis_ack_loss_never_replays_metadata_but_data_retry_remains(
    real_replay_case, operation, monkeypatch
):
    from redis.exceptions import TimeoutError as RedisTimeoutError

    case = real_replay_case
    client = case.first_client
    connection_type = client.connection_pool.connection_class
    original_send = connection_type.send_command
    original_read = connection_type.read_response
    pending, commands, failed = {}, [], set()
    canary = f"{case.metadata}:ordinary-set"

    def send(connection, *args, **kwargs):
        if args[0] == "SET" and args[1] in {case.metadata, canary}:
            pending[id(connection)] = args[1]
            commands.append(args[1])
        return original_send(connection, *args, **kwargs)

    def read(connection, *args, **kwargs):
        response = original_read(connection, *args, **kwargs)
        key = pending.pop(id(connection), None)
        if key is not None and key not in failed:
            failed.add(key)
            raise RedisTimeoutError("Injected lost acknowledgement after server apply")
        return response

    with monkeypatch.context() as failure:
        failure.setattr(connection_type, "send_command", send)
        failure.setattr(connection_type, "read_response", read)
        method = (
            case.backend.get_generation
            if operation == "initialize"
            else case.backend.rotate_generation
        )
        with pytest.raises(GenerationUnavailableError):
            method(replay.SCOPE)
        assert commands.count(case.metadata) == 1
        # The SAME client's ordinary SET still retries; metadata writes never
        # mutated its retry policy. The canary is unrelated to query results.
        try:
            assert client.set(canary, b"data") is True
            assert commands.count(canary) == 2
        finally:
            client.delete(canary)
    applied = case.peer.get_generation(replay.SCOPE)
    assert case.backend.rotate_generation(replay.SCOPE) != applied


@pytest.mark.parametrize("real_replay_case", ["memcached"], indirect=True)
@pytest.mark.parametrize("operation", ["initialize", "rotate"])
def test_memcached_ack_loss_performs_one_store(real_replay_case, operation, monkeypatch):
    case = real_replay_case
    client = case.first_client
    original = client._store_cmd
    applications = []

    def lose_reply(name, values, expire, noreply, **kwargs):
        result = original(name, values, expire, noreply, **kwargs)
        if case.metadata in values:
            applications.append((name, noreply))
            raise OSError("Injected lost acknowledgement after server apply")
        return result

    with monkeypatch.context() as failure:
        failure.setattr(client, "_store_cmd", lose_reply)
        method = (
            case.backend.get_generation
            if operation == "initialize"
            else case.backend.rotate_generation
        )
        with pytest.raises(GenerationUnavailableError):
            method(replay.SCOPE)
        assert applications == [(b"add" if operation == "initialize" else b"set", False)]
    applied = case.peer.get_generation(replay.SCOPE)
    assert case.backend.rotate_generation(replay.SCOPE) != applied
