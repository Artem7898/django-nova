"""TCP addresses and acknowledged writes for the production client."""

from unittest.mock import Mock, patch

import pytest

from nova.cache.backends.memcached import MemcachedCacheBackend


@pytest.mark.parametrize(
    ("server", "address"),
    [
        ("localhost:11211", ("localhost", 11211)),
        ("127.0.0.1:51211", ("127.0.0.1", 51211)),
        ("cache.internal:12345", ("cache.internal", 12345)),
        ("[::1]:11211", ("::1", 11211)),
    ],
)
def test_constructor_passes_tcp_address_and_requires_replies(server, address):
    with (
        patch("nova.cache.backends.memcached._memcached_available", True),
        patch("nova.cache.backends.memcached.PyMemcacheClient") as factory,
    ):
        backend = MemcachedCacheBackend(server)
    factory.assert_called_once_with(address, default_noreply=False)
    assert backend._client is factory.return_value


@pytest.mark.parametrize(
    "server",
    [
        "",
        "localhost",
        ":11211",
        "localhost:",
        "localhost:abc",
        "localhost:0",
        "localhost:65536",
        "localhost:-1",
        "localhost: 11211",
        "bad host:11211",
        "::1:11211",
        "[::1:11211",
    ],
)
def test_malformed_address_fails_before_client_creation(server):
    with (
        patch("nova.cache.backends.memcached._memcached_available", True),
        patch("nova.cache.backends.memcached.PyMemcacheClient") as factory,
        pytest.raises(ValueError),
    ):
        MemcachedCacheBackend(server)
    factory.assert_not_called()


def test_injected_client_does_not_parse_unused_server():
    client = Mock()
    assert MemcachedCacheBackend("unused", client=client)._client is client
