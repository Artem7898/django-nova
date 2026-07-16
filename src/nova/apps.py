from __future__ import annotations

from django.apps import AppConfig
from django.apps import apps as django_apps


class NovaAppConfig(AppConfig):
    name = "nova"

    verbose_name = "Django Nova"

    def ready(self) -> None:
        from nova.cache.invalidation import connect_invalidation
        from nova.typing.models import NovaModel

        for model in django_apps.get_models():
            if (
                issubclass(model, NovaModel)
                and not model._meta.abstract
            ):
                connect_invalidation(model)