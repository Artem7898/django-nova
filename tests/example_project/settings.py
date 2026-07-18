SECRET_KEY = "secret-key-for-benchmarks"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "nova",
    "tests.apps.TestsConfig",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"