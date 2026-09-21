"""Spawn-safe workers: Django is initialized only after test DB settings arrive."""

import os
import traceback
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import urlsplit


@contextmanager
def remote_backend(spec):
    """Open an independent connection in this process and close it on exit."""
    if spec["backend"] == "redis":
        from redis import Redis

        from nova.cache.backends.redis import RedisCacheBackend

        client = Redis.from_url(
            spec["address"], decode_responses=False, socket_connect_timeout=3, socket_timeout=3
        )
        try:
            assert client.ping()
            yield RedisCacheBackend(client=client, key_prefix=spec["namespace"])
        finally:
            client.close()
    else:
        from pymemcache.client.base import Client

        from nova.cache.backends.memcached import MemcachedCacheBackend

        endpoint = urlsplit(f"//{spec['address']}")
        assert endpoint.hostname and endpoint.port, "Expected host:port or [IPv6]:port"
        client = Client(
            (endpoint.hostname, endpoint.port),
            key_prefix=f"{spec['namespace']}:".encode("ascii"),
            default_noreply=False,
            connect_timeout=3,
            timeout=3,
        )
        try:
            assert client.version()
            yield MemcachedCacheBackend(client=client)
        finally:
            client.close()


def worker_main(channel, spec, role):
    """Exchange only primitive results; never inherit parent DB/cache clients."""
    connections = None
    try:
        import django
        from django.conf import settings

        os.environ["DJANGO_SETTINGS_MODULE"] = spec["settings_module"]
        # pytest-django changes the database NAME at runtime. The child must
        # use that exact test database, not the original application database.
        settings.DATABASES = deepcopy(spec["databases"])
        django.setup()

        from django.db import connection, connections, transaction
        from django.test.utils import CaptureQueriesContext

        from nova.cache.invalidation import connect_invalidation
        from nova.cache.queryset_cache import QuerySetCache
        from tests.models import CachedItem

        def query():
            return CachedItem.objects.filter(pk=spec["pk"])

        def values(rows):
            return None if rows is None else [row.value for row in rows]

        with remote_backend(spec) as backend:
            cache = QuerySetCache(backend=backend, ttl=300)
            connect_invalidation(CachedItem, cache=cache)
            key = cache._generate_key(query())[0]

            if role == "reader":

                def pause():
                    channel.send({"kind": "ready-to-store", "pid": os.getpid(), "key": key})
                    if not channel.poll(30):
                        raise TimeoutError("Reader did not receive the resume command")
                    assert channel.recv() == "resume"

                phase = spec.get("pause_fill")
                if phase == "before-set":
                    original_set = backend.set

                    def delayed_set(*args, **kwargs):
                        # This pause occurs AFTER the final generation check.
                        pause()
                        return original_set(*args, **kwargs)

                    with patch.object(backend, "set", delayed_set):
                        initial = values(cache.get_or_set(query()))
                elif phase == "after-sql":
                    qs = query()
                    original_iter = type(qs).__iter__

                    def delayed_iter(self):
                        rows = list(original_iter(self))
                        pause()
                        return iter(rows)

                    with patch.object(type(qs), "__iter__", delayed_iter):
                        initial = values(cache.get_or_set(qs))
                else:
                    initial = values(cache.get_or_set(query()))
                channel.send({"kind": "filled", "pid": os.getpid(), "key": key, "values": initial})
                if not channel.poll(30):
                    raise TimeoutError("Reader did not receive the post-write read command")
                assert channel.recv() == "read"
                with CaptureQueriesContext(connection) as captured:
                    cached = values(cache.get_or_set(query()))
                actual = list(query().values_list("value", flat=True))
                channel.send(
                    {
                        "kind": "read",
                        "values": cached,
                        "database": actual,
                        "sql_count": len(captured),
                    }
                )
            else:
                # Confirm shared storage without registering this key in the
                # writer's QuerySetCache index, unless explicitly requested.
                assert key == spec["key"], "Workers generated different keys for the same query"
                if spec.get("pause_fill"):
                    assert backend.get(key) is None, (
                        "Reader must still be paused before publication"
                    )
                else:
                    assert values(backend.get(key)) == [1], (
                        "Writer cannot see the reader's cache entry"
                    )
                if spec["prime_writer"]:
                    assert values(cache.get(query())) == [1]

                committed = []
                with transaction.atomic():
                    row = query().get()
                    if spec["operation"] == "save":
                        row.value = 2
                        row.save(update_fields=["value"])
                    else:
                        row.delete()
                    transaction.on_commit(lambda: committed.append(True))
                    if spec["rollback"]:
                        transaction.set_rollback(True)

                channel.send(
                    {
                        "kind": "written",
                        "pid": os.getpid(),
                        "committed": bool(committed),
                        "database": list(query().values_list("value", flat=True)),
                    }
                )
    except Exception:
        channel.send({"kind": "error", "traceback": traceback.format_exc()})
        raise
    finally:
        if connections is not None:
            connections.close_all()
        channel.close()
