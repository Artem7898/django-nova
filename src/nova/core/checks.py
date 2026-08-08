"""Django System Checks for Nova Infrastructure."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.core.checks import CheckMessage, Error, register
from django.core.checks import Warning as DjangoWarning


@register("nova")
def check_nova_infrastructure(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    from django.apps import apps

    from nova.conf import nova_settings
    from nova.typing.models import NovaModel

    models: list[Any] = []
    if app_configs is None:
        models = list(apps.get_models())
    else:
        for app_config in app_configs:
            models.extend(app_config.get_models())

    issues: list[CheckMessage] = []

    # CHECK W001
    for model in models:
        if issubclass(model, NovaModel) and not model._meta.abstract:
            config = getattr(model, "_nova_config", None)
            if config and config.cache_enabled and not config.pydantic_schema:
                issues.append(
                    DjangoWarning(
                        f"NovaModel '{model.__name__}' has cache_enabled=True but no pydantic_schema.",
                        hint="Provide a pydantic_schema in _nova_config to ensure deterministic cache keys.",
                        id="nova.W001",
                    )
                )

    # CHECK E001
    if nova_settings.cache_backend == "redis":
        try:
            from nova.redis.health import check_redis_health

            report = check_redis_health()

            if not report.is_healthy:
                issues.append(
                    Error(
                        "Nova Redis cache backend is configured, but Redis is unreachable.",
                        hint=f"Error: {report.error}. Check NOVA_REDIS_URL.",
                        id="nova.E001",
                    )
                )
        except Exception as e:
            issues.append(
                Error(
                    f"Failed to initialize Redis health check: {e!s}",
                    hint="Ensure 'redis' package is installed if NOVA_CACHE_BACKEND='redis'.",
                    id="nova.E002",
                )
            )

    return issues
