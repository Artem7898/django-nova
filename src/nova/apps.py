from __future__ import annotations

from typing import Any

from django.apps import AppConfig
from django.apps import apps as django_apps


class NovaAppConfig(AppConfig):
    name = "nova"
    verbose_name = "Django Nova"

    def ready(self) -> None:
        # 1. Register System Checks (Fail-Fast mechanism)
        import nova.core.checks  # type: ignore # noqa: F401

        # 2. Connect Cache Invalidation Signals
        from nova.cache.invalidation import connect_invalidation
        from nova.typing.models import NovaModel

        for raw_model in django_apps.get_models():
            if issubclass(raw_model, NovaModel):
                meta: Any = raw_model._meta
                if not getattr(meta, "abstract", True):
                    connect_invalidation(raw_model)
