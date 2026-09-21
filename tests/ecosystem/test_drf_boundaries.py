"""Optional dependency and configuration errors at the public DRF boundary."""

from __future__ import annotations

import runpy
import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from nova.ecosystem import drf


def test_import_without_drf_keeps_a_clear_optional_dependency_boundary(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "rest_framework", None)
        isolated = runpy.run_path(drf.__file__)
        assert isolated["DRF_AVAILABLE"] is False
        with pytest.raises(ImportError, match=r"django-nova\[drf\]"):
            isolated["to_drf_serializer"](object)


def test_missing_nova_config_is_reported():
    pytest.importorskip("rest_framework")
    model = type("MissingConfig", (), {})
    with pytest.raises(ValueError, match="MissingConfig requires _nova_config"):
        drf.to_drf_serializer(model)


class ExampleSchema(BaseModel):
    title: str


@pytest.mark.parametrize("schema", [None, {}, 42, ExampleSchema(title="instance")])
def test_schema_must_be_a_pydantic_model_class(schema):
    pytest.importorskip("rest_framework")
    model = type("BadSchema", (), {"_nova_config": SimpleNamespace(pydantic_schema=schema)})
    with pytest.raises(ValueError, match="BadSchema requires"):
        drf.to_drf_serializer(model)
