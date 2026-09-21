"""Behavioral contracts for context scopes and Python execution boundaries."""

from __future__ import annotations

import asyncio
import contextvars
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
import structlog

from nova.core import context


@pytest.fixture(autouse=True)
def restore_context():
    """Do not leave bindings behind in another test or its logging bridge."""
    nova_before = context.get_all()
    log_before = structlog.contextvars.get_contextvars()
    context.clear()
    try:
        yield
    finally:
        context.clear()
        if nova_before:
            context.bind(**nova_before)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(**log_before)


def assert_bindings(expected):
    assert context.get_all() == expected
    assert structlog.contextvars.get_contextvars() == expected


def test_empty_context_and_missing_default():
    default = object()
    assert_bindings({})
    assert context.get("missing") is None
    assert context.get("missing", default) is default


def test_bind_merges_and_overwrites_without_dropping_other_keys():
    assert context.bind(correlation_id="request", user_id=1) is None
    context.bind(user_id=2, optional=None, zero=0, enabled=False)
    assert_bindings(
        {
            "correlation_id": "request",
            "user_id": 2,
            "optional": None,
            "zero": 0,
            "enabled": False,
        }
    )
    assert context.get("optional", "fallback") is None
    assert context.get("zero", "fallback") == 0
    assert context.get("enabled", "fallback") is False


@pytest.mark.parametrize("keys", [("user_id",), ("missing", "user_id", "user_id")])
def test_unbind_removes_only_named_keys(keys):
    context.bind(correlation_id="request", user_id=1)
    assert context.unbind(*keys) is None
    assert_bindings({"correlation_id": "request"})


def test_empty_operations_are_idempotent():
    context.unbind("absent")
    context.bind()
    context.unbind()
    assert_bindings({})
    context.bind(value=1)
    assert context.clear() is None
    context.clear()
    assert_bindings({})


def test_get_all_does_not_expose_the_binding_dictionary():
    context.bind(value=1)
    snapshot = context.get_all()
    snapshot["value"] = 2
    snapshot["added"] = True
    assert_bindings({"value": 1})
    context.bind(value=3)
    assert snapshot == {"value": 2, "added": True}


def test_values_are_shallow_and_keep_their_identity():
    payload = {"items": []}
    context.bind(payload=payload)
    assert context.get("payload") is payload
    assert context.get_all()["payload"] is payload
    with context.new_context(child=True):
        payload["items"].append("shared")
    assert context.get("payload") is payload
    assert context.get("payload") == {"items": ["shared"]}


def test_nested_scopes_replace_bindings_and_restore_each_enclosing_scope():
    context.bind(correlation_id="outer", user_id=1)
    with context.new_context(correlation_id="middle") as entered:
        assert entered is None
        assert_bindings({"correlation_id": "middle"})
        context.bind(step=2)
        with context.new_context(correlation_id="inner", temporary=True):
            assert_bindings({"correlation_id": "inner", "temporary": True})
            context.unbind("correlation_id")
            context.clear()
            context.bind(replacement=True)
        assert_bindings({"correlation_id": "middle", "step": 2})
    assert_bindings({"correlation_id": "outer", "user_id": 1})


@pytest.mark.parametrize("outer", [{}, {"request": "outer"}])
def test_empty_scope_restores_empty_or_nonempty_parent(outer):
    context.bind(**outer)
    with context.new_context():
        assert_bindings({})
        context.bind(temporary=True)
    assert_bindings(outer)


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt, SystemExit])
def test_exception_identity_and_enclosing_bindings_are_preserved(error_type):
    error = error_type("from body")
    context.bind(request="root")
    with pytest.raises(error_type) as caught, context.new_context(request="outer"):
        try:
            with context.new_context(request="inner"):
                context.clear()
                raise error
        finally:
            assert_bindings({"request": "outer"})
    assert caught.value is error
    assert_bindings({"request": "root"})


def test_decorated_sync_function_gets_a_fresh_scope_per_call():
    @context.new_context(operation="decorated")
    def operation(value):
        assert_bindings({"operation": "decorated"})
        context.bind(value=value)
        return context.get_all()

    context.bind(request="parent")
    assert operation(1) == {"operation": "decorated", "value": 1}
    assert operation(2) == {"operation": "decorated", "value": 2}
    assert_bindings({"request": "parent"})


@pytest.mark.asyncio
async def test_task_inherits_bindings_at_creation_without_parent_writeback():
    release = asyncio.Event()
    context.bind(request="at-creation", keep=True)

    async def child():
        await release.wait()
        assert_bindings({"request": "at-creation", "keep": True})
        context.bind(request="child")
        context.unbind("keep")
        return context.get_all(), structlog.contextvars.get_contextvars()

    task = asyncio.create_task(child())
    try:
        context.bind(request="parent-later")
        release.set()
        result, log_result = await asyncio.wait_for(task, timeout=5)
        assert result == log_result == {"request": "child"}
        assert_bindings({"request": "parent-later", "keep": True})
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_siblings_have_independent_nested_bindings():
    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    context.bind(request="parent")

    async def worker(index):
        assert_bindings({"request": "parent"})
        with context.new_context(worker=index):
            context.bind(step="ready")
            ready[index].set()
            await release.wait()
            assert_bindings({"worker": index, "step": "ready"})
            with context.new_context(inner=index):
                await asyncio.sleep(0)
                assert_bindings({"inner": index})
            assert_bindings({"worker": index, "step": "ready"})
            context.clear()
            assert_bindings({})
        assert_bindings({"request": "parent"})

    tasks = [asyncio.create_task(worker(index)) for index in range(2)]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready)), 5)
        assert_bindings({"request": "parent"})
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
        assert_bindings({"request": "parent"})
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True], ids=["exception", "cancellation"])
async def test_failed_task_restores_both_scopes_before_propagation(cancel):
    ready = asyncio.Event()
    release = asyncio.Event()
    error = ValueError("child failed")
    restored = []
    context.bind(request="parent")

    async def child():
        try:
            with context.new_context(request="child"):
                try:
                    with context.new_context(operation="awaiting"):
                        ready.set()
                        await release.wait()
                        raise error
                finally:
                    assert_bindings({"request": "child"})
                    restored.append("child")
        finally:
            assert_bindings({"request": "parent"})
            restored.append("parent")

    task = asyncio.create_task(child())
    try:
        await asyncio.wait_for(ready.wait(), 5)
        if cancel:
            task.cancel("requested cancellation")
        else:
            release.set()
        expected_error = asyncio.CancelledError if cancel else ValueError
        with pytest.raises(expected_error) as caught:
            await asyncio.wait_for(task, 5)
        if cancel:
            assert task.cancelled()
            assert caught.value.args == ("requested cancellation",)
        else:
            assert caught.value is error
        assert restored == ["child", "parent"]
        assert_bindings({"request": "parent"})
        with context.new_context(request="next"):
            assert_bindings({"request": "next"})
        assert_bindings({"request": "parent"})
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_caught_cancellation_allows_same_task_to_continue_in_parent_scope():
    ready = asyncio.Event()
    context.bind(request="parent")

    async def child():
        try:
            with context.new_context(request="cancelled"):
                ready.set()
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert_bindings({"request": "parent"})
            # This test intentionally handles cancellation and continues.
            current = asyncio.current_task()
            assert current is not None
            current.uncancel()
        with context.new_context(request="recovered"):
            assert_bindings({"request": "recovered"})
        assert_bindings({"request": "parent"})

    task = asyncio.create_task(child())
    try:
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        await asyncio.wait_for(task, 5)
        assert not task.cancelled()
        assert_bindings({"request": "parent"})
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_explicit_context_copy_does_not_write_back_to_parent():
    context.bind(request="parent")
    copied = contextvars.copy_context()
    copied.run(context.bind, request="copy")
    assert copied.run(context.get_all) == {"request": "copy"}
    assert_bindings({"request": "parent"})
    empty = contextvars.Context()
    assert empty.run(context.get_all) == {}


@pytest.mark.asyncio
async def test_to_thread_inherits_bindings_without_writing_back():
    context.bind(request="parent")

    def worker():
        assert_bindings({"request": "parent"})
        with context.new_context(request="thread"):
            assert_bindings({"request": "thread"})
        context.bind(request="thread-after")
        return context.get_all()

    assert await asyncio.to_thread(worker) == {"request": "thread-after"}
    assert_bindings({"request": "parent"})


def test_plain_thread_needs_explicit_context_propagation():
    context.bind(request="parent")
    copied = contextvars.copy_context()

    def worker():
        assert_bindings({})
        with context.new_context(request="thread"):
            assert_bindings({"request": "thread"})
        assert_bindings({})

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(worker).result(timeout=5)
        assert executor.submit(copied.run, context.get_all).result(timeout=5) == {
            "request": "parent"
        }
        executor.submit(worker).result(timeout=5)
    assert_bindings({"request": "parent"})


def exercise_nova_without_logging():
    context.bind(request="parent", removed=True)
    context.unbind("removed")
    with context.new_context(request="child"):
        assert context.get_all() == {"request": "child"}
    assert context.get_all() == {"request": "parent"}
    context.clear()
    assert context.get_all() == {}


def test_structlog_is_optional(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "structlog", None)
        exercise_nova_without_logging()


@pytest.mark.parametrize("method", ["clear_contextvars", "bind_contextvars"])
def test_logging_failure_does_not_break_nova_context(monkeypatch, method):
    def unavailable(*args, **kwargs):
        raise RuntimeError("logging unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(structlog.contextvars, method, unavailable)
        exercise_nova_without_logging()


@pytest.mark.parametrize("error_type", [ValueError, asyncio.CancelledError])
def test_logging_failure_during_cleanup_preserves_body_exception(monkeypatch, error_type):
    error = error_type("business failure")
    context.bind(request="parent")

    def unavailable(*args, **kwargs):
        raise RuntimeError("logging unavailable")

    with monkeypatch.context() as patch:
        with pytest.raises(error_type) as caught, context.new_context(request="child"):
            patch.setattr(structlog.contextvars, "clear_contextvars", unavailable)
            raise error
        assert caught.value is error
        assert context.get_all() == {"request": "parent"}


def test_bridge_continues_after_logging_recovers(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("logging unavailable")

    context.bind(request="parent")
    with monkeypatch.context() as patch:
        patch.setattr(structlog.contextvars, "bind_contextvars", unavailable)
        context.bind(request="during-outage")
        assert context.get("request") == "during-outage"
    context.bind(recovered=True)
    assert_bindings({"request": "during-outage", "recovered": True})
