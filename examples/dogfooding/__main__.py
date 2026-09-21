"""Run with: uv run python -m examples.dogfooding."""

import asyncio
import os

import django
from django.core.management import call_command
from django.db import connections


def main() -> None:
    # This CLI is a separate process and always uses its disposable settings.
    os.environ["DJANGO_SETTINGS_MODULE"] = "examples.dogfooding.settings"
    django.setup()
    from .scenarios import models_demo, tasks_demo

    try:
        call_command("check", verbosity=0)
        call_command("migrate", verbosity=0, interactive=False)
        models_demo()
        asyncio.run(tasks_demo())
        print("Dogfooding demo passed.")
    finally:
        connections.close_all()


if __name__ == "__main__":
    main()
