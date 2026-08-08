"""Query: unknown relation policy -> explicit NovaConfigurationError."""

from __future__ import annotations

from nova.query.planner import build_query_plan
from tests.models import Ghost
from tests.query.contracts import UnknownRelationContract


def test_unknown_relation_raises_configuration_error() -> None:
    UnknownRelationContract().check_raises_configuration_error(lambda: build_query_plan(Ghost))
