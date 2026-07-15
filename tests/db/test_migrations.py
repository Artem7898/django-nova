"""Tests for zero-downtime migrations and chunked operations."""

from __future__ import annotations

import pytest
from django.db import models

from nova.db.splitter import chunked_migration
from nova.db.zero_downtime import AddFieldConcurrently, CreateIndexConcurrently


class TestZeroDowntimeMigrations:
    """Test suite for zero-downtime migration operations."""

    def test_add_field_concurrently_requires_null_or_default(self) -> None:
        """AddFieldConcurrently must raise if field has no null=True and no default."""
        bad_field = models.CharField(max_length=200)
        bad_field.null = False
        bad_field.default = models.NOT_PROVIDED

        # Инициализация должна упасть сразу (Fail-Fast принцип)
        with pytest.raises(ValueError, match="zero-downtime AddField requires"):
            AddFieldConcurrently(model_name="fakemodel", name="bad_field", field=bad_field)

    def test_add_field_concurrently_allows_null(self) -> None:
        """AddFieldConcurrently must succeed if null=True."""
        good_field = models.CharField(max_length=200)
        good_field.null = True
        good_field.default = models.NOT_PROVIDED

        op = AddFieldConcurrently(model_name="fakemodel", name="good_field", field=good_field)
        assert op.name == "good_field"

    def test_create_index_concurrently_generates_sql(self) -> None:
        """CreateIndexConcurrently must generate correct SQL."""
        op = CreateIndexConcurrently(
            table="tests_fakemodel", index_name="idx_fake_name", columns=["name"]
        )
        assert "CONCURRENTLY" in op.sql
        assert "idx_fake_name" in op.sql
        assert "tests_fakemodel" in op.sql

    def test_create_index_concurrently_reverse_sql(self) -> None:
        """Reverse operation must safely drop index."""
        op = CreateIndexConcurrently(
            table="tests_fakemodel", index_name="idx_fake_name", columns=["name"]
        )
        assert "DROP INDEX IF EXISTS idx_fake_name" in op.reverse_sql


class TestChunkedMigration:
    """Test suite for chunked data migration wrapper."""

    def test_chunked_migration_returns_runpython(self) -> None:
        """chunked_migration must return a RunPython instance."""

        def dummy_migration(apps, schema_editor, pks):
            pass

        result = chunked_migration(dummy_migration, batch_size=100)
        from django.db.migrations import RunPython

        assert isinstance(result, RunPython)
