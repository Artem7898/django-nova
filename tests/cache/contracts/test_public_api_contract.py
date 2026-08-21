"""
Public API architecture contracts.

Django Nova promise:
- Public API is stable.
- Lazy imports do not crash.
- Core modules expose documented symbols.
- Package is typed.
"""

from __future__ import annotations

from pathlib import Path


class TestPublicAPIContract:
    def test_root_exports(self) -> None:
        from nova import NovaConfig, NovaModel

        assert NovaModel is not None
        assert NovaConfig is not None

    def test_cache_exports(self) -> None:
        from nova.cache import QuerySetCache, connect_invalidation

        assert QuerySetCache is not None
        assert connect_invalidation is not None

    def test_cache_backend_exports(self) -> None:
        from nova.cache.backends import (
            CacheBackend,
            MemoryCacheBackend,
            NullCacheBackend,
        )

        assert CacheBackend is not None
        assert MemoryCacheBackend is not None
        assert NullCacheBackend is not None

    def test_core_observability_exports(self) -> None:
        from nova.core import bind, clear

        assert bind is not None
        assert clear is not None

    def test_core_exceptions_exports(self) -> None:
        from nova.core.exceptions import NovaCacheError, NovaValidationError

        assert NovaValidationError is not None
        assert NovaCacheError is not None

    def test_py_typed_exists(self) -> None:
        import nova

        package_dir = Path(nova.__file__).parent
        py_typed = package_dir / "py.typed"

        assert py_typed.exists(), "nova must ship py.typed for PEP 561"
