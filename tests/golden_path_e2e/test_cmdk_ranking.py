"""Fixture-driven test for the cmd-K palette ranker (ADR-0017).

Loads tests/golden_path_e2e/cmdk_ranking_fixture.json and asserts that the
real Python-side ranker (mirror of the TS ranker, ported in WI-0606) returns
the expected top candidate for each case.

Marked xfail for Sprint 1 — the real ranker lands in WI-0606. This test:
1. Validates the fixture loads + schema is sane (sanity).
2. Asserts the fixture has >= 30 cases (ADR-0017 requirement).
3. xfail-asserts each case routes through a placeholder ranker (currently
   raises NotImplementedError); flips to plain assert in WI-0606.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_PATH = Path(__file__).parent / "cmdk_ranking_fixture.json"


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_loads() -> None:
    """Sanity: fixture is valid JSON, well-formed, well-keyed."""
    f = load_fixture()
    assert isinstance(f, dict)
    assert "candidates" in f
    assert "cases" in f
    assert "negative_cases" in f


def test_fixture_has_minimum_case_count() -> None:
    """ADR-0017: ≥30 ranking cases mandatory.

    Counts positive + negative cases together; the minimum spec is 30 ranking
    decisions documented in the fixture.
    """
    f = load_fixture()
    total = len(f["cases"]) + len(f["negative_cases"])
    assert total >= 30, f"Fixture has {total} cases; ADR-0017 requires >= 30"


def test_all_candidate_ids_unique() -> None:
    """Sanity: no duplicate candidate.id."""
    f = load_fixture()
    ids = [c["id"] for c in f["candidates"]]
    assert len(ids) == len(set(ids)), f"Duplicate candidate IDs: {ids}"


def test_expected_tops_reference_real_candidates() -> None:
    """Every case's expected_top (when not null) must reference a real candidate."""
    f = load_fixture()
    candidate_ids = {c["id"] for c in f["candidates"]}
    for case in f["cases"]:
        top = case.get("expected_top")
        if top is None or top == []:
            continue
        if "expected_top_in" in case:
            for cid in case["expected_top_in"]:
                assert cid in candidate_ids, f"case {case['id']}: top_in '{cid}' not a candidate"
        elif "expected_top_after" in case:
            assert case["expected_top_after"] in candidate_ids
        else:
            assert top in candidate_ids, f"case {case['id']}: expected_top '{top}' not a candidate"


# --- Ranker contract (WI-0606 ADR-0017 §4) ---

from osint_goblin_schemas.cmdk_rank import rank  # noqa: E402


@pytest.mark.parametrize("case_index", range(35))
def test_each_case_ranks_expected_top(case_index: int) -> None:
    """One parametrized assertion per fixture case."""
    f = load_fixture()
    cases = f["cases"]
    if case_index >= len(cases):
        pytest.skip(f"case {case_index} out of range ({len(cases)} cases)")
    case = cases[case_index]
    candidates = f["candidates"]
    context = {
        "subject": case.get("context_subject"),
        "warm": case.get("warm", False),
        "opsec_state": case.get("opsec_state", "green"),
    }

    ranked = rank(case["query"], candidates, context)

    # Expected forms:
    #  - expected_top: "id"       -- single deterministic winner
    #  - expected_top_in: [...]   -- any of these wins (ambiguity acceptable)
    #  - expected_top_after: "id" -- winner after applying opsec_penalty sorting
    #  - expected_top: null       -- no result / palette shows empty
    if "expected_top_in" in case:
        assert ranked and ranked[0]["id"] in case["expected_top_in"], (
            f"case {case['id']}: top={ranked[0] if ranked else None} "
            f"not in {case['expected_top_in']}"
        )
    elif "expected_top_after" in case:
        # When opsec red, item ranks last but is still in the list
        assert case["expected_top_after"] in {c["id"] for c in ranked}
    elif case.get("expected_top") is None:
        assert ranked == [] or all(
            r["score"] < 0.1 for r in ranked
        ), f"case {case['id']}: expected empty/low-score; got {ranked[:3]}"
    else:
        assert ranked, f"case {case['id']}: empty result; expected {case['expected_top']}"
        assert ranked[0]["id"] == case["expected_top"], (
            f"case {case['id']}: top={ranked[0]['id']} score={ranked[0]['score']:.2f}; "
            f"expected {case['expected_top']}"
        )


@pytest.mark.parametrize("neg_index", range(3))
def test_negative_cases_return_empty(neg_index: int) -> None:
    """Lucene-style operators (type:, tier:, from:) must NOT match."""
    f = load_fixture()
    if neg_index >= len(f["negative_cases"]):
        pytest.skip("neg_case out of range")
    case = f["negative_cases"][neg_index]
    ranked = rank(case["query"], f["candidates"], context=None)
    assert ranked == [], f"Negative case {case['id']} returned matches: {ranked}"
