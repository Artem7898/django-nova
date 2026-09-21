"""Atomic initialization and acknowledgement at the metadata transport boundary."""

import pytest

from nova.cache.backends.memcached import MemcachedCacheBackend
from nova.cache.backends.redis import RedisCacheBackend
from nova.cache.generation import GenerationUnavailableError, generation_key, new_generation


class MetadataClient:
    def __init__(self):
        self.values = {}
        self.before_create = None
        self.reject_set = False
        self.disappear = False
        self.error = None
        self.calls = []

    def get(self, key):
        if self.error:
            raise self.error
        value = None if self.disappear else self.values.get(key)
        if value is None and self.before_create:
            callback, self.before_create = self.before_create, None
            callback()
        return value

    def set(self, key, value, *, nx=False, expire=0, noreply=False):
        self.calls.append(("set", nx, expire, noreply))
        if self.reject_set or (nx and key in self.values):
            return False
        self.values[key] = value
        return True

    def add(self, key, value, *, expire=0, noreply=True):
        self.calls.append(("add", True, expire, noreply))
        if key in self.values:
            return False
        self.values[key] = value
        return True


class MetadataWriter:
    """Explicit single-attempt writer for the deterministic store double."""

    def __init__(self, client, kind):
        self.client = client
        self.kind = kind

    def write(self, key, token, *, only_if_absent):
        if self.kind == "redis":
            return self.client.set(key, token, nx=only_if_absent)
        method = self.client.add if only_if_absent else self.client.set
        return method(key, token, expire=0, noreply=False)


@pytest.fixture(params=["redis", "memcached"])
def metadata(request):
    client = MetadataClient()
    writer = MetadataWriter(client, request.param)
    if request.param == "redis":
        backend = RedisCacheBackend(client=client, key_prefix="", generation_writer=writer)
    else:
        backend = MemcachedCacheBackend(client=client, generation_writer=writer)
    return backend, client


def test_initialization_reuses_the_acknowledged_token(metadata):
    backend, client = metadata
    token = backend.get_generation("model:default")
    assert len(token) == 32
    assert backend.get_generation("model:default") == token
    assert len(client.calls) == 1
    _, atomic, expire, noreply = client.calls[0]
    assert atomic is True
    assert expire == 0
    assert noreply is False


def test_initialization_does_not_overwrite_a_concurrent_rotation(metadata):
    backend, client = metadata
    rotated = []
    client.before_create = lambda: rotated.append(backend.rotate_generation("scope"))
    assert backend.get_generation("scope") == rotated[0]


def test_unacknowledged_rotation_is_an_error(metadata):
    backend, client = metadata
    old = backend.get_generation("scope")
    client.reject_set = True
    with pytest.raises(GenerationUnavailableError):
        backend.rotate_generation("scope")
    assert backend.get_generation("scope") == old


def test_repeated_eviction_is_bounded(metadata):
    backend, client = metadata
    client.disappear = True
    with pytest.raises(GenerationUnavailableError):
        backend.get_generation("scope")
    assert len(client.calls) == 3


@pytest.mark.parametrize("raw", [b"broken", b"\xff", 17, "0" * 33, "z" * 32])
def test_corruption_is_reported_without_blindly_overwriting(metadata, raw):
    backend, client = metadata
    key = generation_key("scope")
    client.values[key] = raw
    with pytest.raises(GenerationUnavailableError):
        backend.get_generation("scope")
    assert client.values[key] == raw
    assert client.calls == []


def test_transport_error_is_wrapped(metadata):
    backend, client = metadata
    client.error = OSError("unreachable")
    with pytest.raises(GenerationUnavailableError) as raised:
        backend.get_generation("scope")
    assert raised.value.__cause__ is client.error


def test_eviction_installs_a_fresh_token(metadata):
    backend, client = metadata
    key = generation_key("scope")
    old = new_generation()
    client.values[key] = old
    assert backend.get_generation("scope") == old
    del client.values[key]
    assert backend.get_generation("scope") != old
