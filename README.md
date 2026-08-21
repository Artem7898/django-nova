<div align="center">

<img src="assets/django-nova-logo.png" width="250" alt="Django Nova Logo">


# Django Nova

A typed, unified, async-first toolkit for Django 5+.

![Coverage](https://img.shields.io/badge/coverage-59%-yellow.svg)

&lt;!-- STATUS-INJECT-START --&gt;
&lt;!-- Эта секция обновляется автоматически scripts/generate_status.py --&gt;
&lt;!-- STATUS-INJECT-END --&gt;

---

## 📦 Installation

Requires **Python 3.12+** and **Django 5.0+** (tested with Django 5.0, 5.1, 5.2).

Using [`uv`](https://docs.astral.sh/uv/) (recommended):

```bash
# Core library
uv add django-nova

# With Django REST Framework support
uv add django-nova[drf]

# With Redis infrastructure & Distributed Locks
uv add django-nova[redis]
# or
uv add django-nova[cache]

# With OpenTelemetry tracing
uv add django-nova[tracing]

# Full enterprise stack (tracing + observability)
uv add django-nova[tracing,observability]

# With FastAPI integration
uv add django-nova[fastapi]

# With async task queue
uv add django-nova[tasks]

# With async database support
uv add django-nova[async]
```

### Add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "nova",
]
```

---

## 🎯 Philosophy

> ⚠️ **This is a Beta project.** See the [auto-generated status report](STATUS.md) for real module-by-module coverage and stability assessment.


## 📊 Honest Project Status


## 🚀 Quick Start


## 📊 Current Status

See [STATUS.md](./STATUS.md) for the honest, auto-generated breakdown.

## 📚 API Reference

Full API docs: [https://artem7898.github.io/django-nova](https://artem7898.github.io/django-nova)

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.

## 👤 Author

**Artem Alimpiev**

- ORCID: [0009-0007-6740-7242](https://orcid.org/0009-0007-6740-7242)
- DOI: [10.5281/zenodo.20057443](https://doi.org/10.5281/zenodo.20057443)
- DOI: [10.5281/zenodo.20659647](https://doi.org/10.5281/zenodo.20659647)
- PyPI: [Django Nova](https://pypi.org/project/django-nova/)

---

## 🛡️ Validation Boundary






