"""Exercise the documented command in its own Django process."""

import os
import subprocess
import sys
from pathlib import Path


def test_dogfooding_demo() -> None:
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    # The command must select its own in-memory settings, even in PostgreSQL CI.
    env["DJANGO_SETTINGS_MODULE"] = "settings_that_must_not_be_imported"
    env["PYTHONPATH"] = os.pathsep.join((str(root / "src"), str(root)))
    result = subprocess.run(
        [sys.executable, "-m", "examples.dogfooding"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("OK ") == 6, result.stdout
    assert "Dogfooding demo passed." in result.stdout


def test_dogfooding_migrations_match_models() -> None:
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "examples.dogfooding.settings"
    env["PYTHONPATH"] = os.pathsep.join((str(root / "src"), str(root)))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "django",
            "makemigrations",
            "nova_dogfooding",
            "--check",
            "--dry-run",
        ],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
