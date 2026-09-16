"""Test database setup and async ORM connection cleanup."""

import asyncio

import pytest
from asgiref.sync import sync_to_async


async def _close_async_orm_connections() -> None:
    from django.db import connections

    # Resolve and close connections inside the thread-sensitive ORM worker.
    await sync_to_async(
        connections.close_all,
        thread_sensitive=True,
    )()


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Create unmigrated test tables and clean up the async ORM worker."""
    from django.core.management import call_command

    try:
        with django_db_blocker.unblock():
            call_command(
                "migrate",
                run_syncdb=True,
                verbosity=0,
                interactive=False,
            )

        yield
    finally:
        # This finalizer runs before the underlying django_db_setup finalizer.
        with django_db_blocker.unblock():
            asyncio.run(_close_async_orm_connections())
