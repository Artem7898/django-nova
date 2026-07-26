"""
Django System Checks for Nova Infrastructure.
Fails fast before accepting traffic if infrastructure is misconfigured.
"""

from __future__ import annotations

from django.core.checks import Error, Warning, register


@register("nova")
def check_nova_infrastructure(app_configs, **kwargs) -> list[Error | Warning]:
    errors: list[Error | Warning] = []

    from django.apps import apps
    from nova.conf import nova_settings
    from nova.typing.models import NovaModel

    # Determine which models to check
    if app_configs is None:
        models = apps.get_models()
    else:
        models = []
        for app_config in app_configs:
            models.extend(app_config.get_models())

    # -------------------------------------------------------------
    # CHECK W001: Cache enabled without Pydantic Schema
    # -------------------------------------------------------------
    for model in models:
        if issubclass(model, NovaModel) and not model._meta.abstract:
            config = getattr(model, '_nova_config', None)
            if config and config.cache_enabled and not config.pydantic_schema:
                errors.append(
                    Warning(
                        f"NovaModel '{model.__name__}' has cache_enabled=True but no pydantic_schema.",
                        hint="Provide a pydantic_schema in _nova_config to ensure deterministic cache keys.",
                        id="nova.W001",
                    )
                )

    # -------------------------------------------------------------
    # CHECK E001: Redis Backend configured but Redis is unreachable
    # -------------------------------------------------------------
    if nova_settings.cache_backend == "redis":
        try:
            from nova.redis.health import check_redis_health
            report = check_redis_health()

            if not report.is_healthy:
                errors.append(
                    Error(
                        f"Nova Redis cache backend is configured, but Redis is unreachable or lagging.",
                        hint=f"Error: {report.error}. Check NOVA_REDIS_URL in settings.",
                        id="nova.E001",
                    )
                )
        except Exception as e:
            errors.append(
                Error(
                    f"Failed to initialize Redis health check: {str(e)}",
                    hint="Ensure 'redis' package is installed if NOVA_CACHE_BACKEND='redis'.",
                    id="nova.E002",
                )
            )

    return errors