"""P0-3.5 contract tests for async ORM validation boundary."""

from __future__ import annotations

import pytest

from nova.async_orm.queryset import AsyncTypedQuerySet
from nova.core.exceptions import NovaValidationError
from tests.models import AsyncArticle
from tests.query.contracts import AsyncORMContract
from tests.query.fixtures import async_article_expectation


@pytest.fixture()
def contract() -> AsyncORMContract:
    return AsyncORMContract(async_article_expectation(AsyncArticle))


def test_async_manager_returns_async_queryset() -> None:
    qs = AsyncArticle.objects.all()
    assert isinstance(qs, AsyncTypedQuerySet)


def test_async_public_api_exists(contract: AsyncORMContract) -> None:
    contract.check_async_public_api()


def test_aauto_applies_plan(contract: AsyncORMContract) -> None:
    contract.check_aauto_applies_plan()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_alist_returns_list(contract: AsyncORMContract) -> None:
    await contract.check_alist_returns_list()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_asave_accepts_valid(contract: AsyncORMContract) -> None:
    await contract.check_asave_accepts_valid()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_asave_rejects_invalid_title() -> None:
    instance = AsyncArticle(title="", author_id=1)
    with pytest.raises(NovaValidationError):
        await instance.asave()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_aauto_is_async_iterable() -> None:
    qs = AsyncArticle.objects.all().aauto()
    assert hasattr(qs, "__aiter__")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_manager_acreate() -> None:
    from tests.models import Lab

    obj = await Lab.objects.acreate(name="Async Lab", budget=1000.0)
    assert obj.name == "Async Lab"
