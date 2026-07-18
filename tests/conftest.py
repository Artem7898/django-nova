import pytest


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """
    Redefining the creation of a database for the library.
    Since we don't have a migrations folder in the tests/ folder, we use
    the --run-syncdb flag, which creates tables directly from the model classes.
    """
    with django_db_blocker.unblock():
        from django.core.management import call_command
        call_command('migrate', '--run-syncdb', verbosity=0, interactive=False)