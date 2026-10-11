"""Offline tests for the demo's own logic: the matrix verdict and the token arithmetic."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))

from discovery_matrix import verdict_for  # noqa: E402
from token_compare import schema_costs  # noqa: E402


def test_verdict_clean_run_under_enforce():
    assert verdict_for("ENFORCE", 12, 0, 0) == "Search results stayed inside each persona's tools/list."


def test_verdict_reports_leaks():
    assert "surfaced 2 tool(s)" in verdict_for("ENFORCE", 12, 0, 2)


def test_verdict_is_inconclusive_when_every_search_failed():
    verdict = verdict_for("ENFORCE", 12, 12, 0)
    assert verdict.startswith("Inconclusive") and "stayed inside" not in verdict


def test_verdict_mentions_partial_failures():
    assert "4 of 12 search calls failed" in verdict_for("ENFORCE", 12, 4, 0)


def test_verdict_is_inconclusive_in_log_only():
    assert verdict_for("LOG_ONLY", 12, 0, 0).startswith("Inconclusive in LOG_ONLY")


def test_schema_costs_separates_fixed_overhead():
    baseline, fixed, schemas = 20, 300, [100, 150, 200]
    singles = [baseline + fixed + s for s in schemas]
    full = baseline + fixed + sum(schemas)
    assert schema_costs(baseline, singles, full) == (300, 150)


def test_schema_costs_needs_two_tools():
    with pytest.raises(ValueError):
        schema_costs(20, [400], 400)
