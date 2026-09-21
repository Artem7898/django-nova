# QuerySet caching

This page describes the development checkout. The distributed generation,
relation-dependency, and ownership guarantees added after v0.6.2 must be present
in the installed source before relying on them. Check the changelog for your
release.

## Enable and connect

Set `cache_enabled=True` in the model's `NovaConfig`. Nova's app configuration
connects invalidation for enabled models at Django startup. The default
`QuerySetCache` and `get_default_cache()` share the default process-local state.

For a custom backend, create a cache at application startup and connect
invalidation to that same cache in every reader and writer process:

```python
from nova.cache.invalidation import connect_invalidation
from nova.cache.queryset_cache import QuerySetCache

# Article is an installed NovaModel with cache_enabled=True.
# backend is a configured synchronous cache backend.
cache = QuerySetCache(backend=backend, ttl=60)
connect_invalidation(Article, cache=cache)

rows = cache.get_or_set(Article.objects.order_by("pk"))
```

The returned object is a list. Use a fresh queryset for a new read. Ordinary
Django queryset evaluation without the cache API keeps Django's behavior.

## Invalidation and concurrent writes

Signal-driven invalidation runs after a successful commit on the signal's
database alias. Rollback discards it. In autocommit mode it runs immediately.
Cache reads inside `atomic()` or with autocommit disabled use the database.

Backends implementing the shared generation contract store a token for each
model/database scope. Result keys include the generations captured before SQL.
A late fill remains under its original generations even if a commit occurs while
the query is running. Writers must subscribe even if they have never read the
cached query. Process-local backends only coordinate their shared local state.

Ordinary joins and supported string prefetch paths track related models and M2M
through tables. Save/delete signals and supported M2M manager changes invalidate
those scopes. Query plans whose dependencies cannot be determined use uncached
execution; custom `Prefetch`, raw SQL, subqueries, and routing can cross that
boundary. Review `nova.cache.dependencies` when introducing a new query shape.

Bulk writes and raw SQL may bypass the relevant signals. Arrange explicit
invalidation after commit for every affected model/database scope, including a
through table when changing its rows directly. Cache TTL is not a replacement
for that notification.

## Failures and object ownership

Generation metadata must be available before shared results can be trusted.
Metadata loss and failed operations are handled conservatively, but cache
invalidation cannot guarantee immediate notification to remote processes during
a network partition. The implementation logs failures and retries locally;
choose an operational policy appropriate for the data's consistency needs.

Mutable lists, models, JSON, and loaded relations returned to one caller must not
change stored values or another caller's results. Two independent capabilities
control whether a defensive copy can be omitted:

| Capability | Guarantee |
|---|---|
| `returns_detached_values` | Every successful read returns an independent mutable graph. |
| `stores_detached_values` | A write captures an independent value synchronously before returning and does not mutate its input. |

A read guarantee does not grant a write guarantee. Implementations and custom
serializers must explicitly preserve each contract. Backends without the relevant
guarantee retain defensive copying. Failed copying, serialization, or publication
on the fill path returns the ORM result according to the cache's failure contract.

## Measure the workload

Measure actual hit rate after invalidations, miss cost, write cost, and tail
latency. A cache hit can be faster while the mixed workload is slower. Compare
ordinary timing runs separately from diagnostics with additional instrumentation;
GC overlap is wall-clock overlap, not a measured counterfactual speedup.

See [testing](testing.md) and the [cache API](api/cache.md).
