"""Private in-memory database and file storage for the demo process."""

SECRET_KEY = "nova-dogfooding-demo-only"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "nova",
    "examples.dogfooding.apps.DogfoodingConfig",
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STORAGES = {"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}}
