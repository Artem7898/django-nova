"""Query: cycle safety contracts (self-ref / two-node cycle)."""

from __future__ import annotations

from nova.query.planner import build_query_plan
from tests.models import Left, Node
from tests.query.contracts import CycleSafetyContract


def test_self_reference_no_recursion() -> None:
    contract = CycleSafetyContract()
    plan = contract.check_builds_without_recursion(lambda: build_query_plan(Node))
    contract.check_bounded(plan)


def test_two_node_cycle_no_recursion() -> None:
    contract = CycleSafetyContract()
    plan = contract.check_builds_without_recursion(lambda: build_query_plan(Left))
    contract.check_bounded(plan)
