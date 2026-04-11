import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "nova",
            "tests.apps.TestsConfig",  # <-- Правильная регистрация приложения
        ],
        SECRET_KEY="test-secret-key-nova-2025",
        USE_TZ=True,
    )

    import django
    django.setup()