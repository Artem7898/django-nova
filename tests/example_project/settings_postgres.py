"""PostgreSQL settings for local ORM integration tests."""

from tests.example_project import settings as base

SECRET_KEY = base.SECRET_KEY
INSTALLED_APPS = base.INSTALLED_APPS
DEFAULT_AUTO_FIELD = base.DEFAULT_AUTO_FIELD

USE_TZ = True
TIME_ZONE = "UTC"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "nova_test",
        "USER": "nova",
        "PASSWORD": "nova_local_test_only",
        "HOST": "127.0.0.1",
        "PORT": "55432",
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "connect_timeout": 5,
        },
        "TEST": {
            "NAME": "test_nova_orm",
        },
    }
}
