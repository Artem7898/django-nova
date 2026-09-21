# Installation and configuration

## Requirements

| Dependency | Declared range |
|---|---|
| Python | `>=3.12` |
| Django | `>=5.0,<6.0` |
| Pydantic | `>=2.8,<3.0` |

These ranges come from the package manifest and do not mean that every version
combination has been tested. SQLite supports a local demo and many tests;
PostgreSQL, Redis, and Memcached are used by their respective integration suites.

## Install in an application

```bash
uv add django-nova
```

Or, in an activated virtual environment:

```bash
python -m pip install django-nova
```

Choose optional dependencies for the integrations you use:

```bash
uv add 'django-nova[redis]'
uv add 'django-nova[drf]'
uv add 'django-nova[fastapi]'
uv add 'django-nova[graphql]'
uv add 'django-nova[observability]'
```

Installing an extra supplies dependencies; it does not configure a service,
start a worker, or confirm an adapter's runtime stability.

Extend the existing Django settings with Nova and your application:

```python
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "nova",
    "articles",
]
```

Retain the other applications your project requires. Keep `contenttypes` enabled;
Nova's Django field helpers use it. Define models in an installed app and import
them after Django has initialized settings and the app registry.

## Work on this repository

```bash
git clone https://github.com/Artem7898/django-nova.git
cd django-nova
uv sync --locked --all-extras --dev
uv run --locked python -m examples.dogfooding
```

For a checkout with local changes, run the commands in that checkout. A fresh
clone contains only changes already pushed to the repository. `--locked` detects
a manifest/lockfile mismatch rather than updating the lockfile implicitly.

See the [demo](quickstart.md) and [testing guide](testing.md) for expected output
and integration setup.
