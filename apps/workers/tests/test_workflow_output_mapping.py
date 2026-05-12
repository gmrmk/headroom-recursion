"""Tests for workflow output-mapping (Margaret ship #2, 2026-05-11).

Covers:
  - WorkflowStep accepts and stores `inputs_from`
  - resolve_inputs_from for `step{N}.payload.{key}` syntax, missing
    references, malformed references
  - workflow_runner runs steps in-process and chains step N's output
    into step N+1's input via inputs_from
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from osint_goblin_workers.workflow_runner import workflow_runner
from osint_goblin_workers.workflows import (
    WORKFLOWS,
    Workflow,
    WorkflowStep,
    resolve_inputs_from,
)

# ---------------------------------------------------------------------------
# WorkflowStep accepts inputs_from
# ---------------------------------------------------------------------------


def test_workflow_step_inputs_from_defaults_to_empty_dict() -> None:
    s = WorkflowStep("x", {})
    assert s.inputs_from == {}


def test_workflow_step_inputs_from_stored() -> None:
    s = WorkflowStep(
        "x",
        {},
        inputs_from={"lat": "step0.payload.lat", "lon": "step0.payload.lon"},
    )
    assert s.inputs_from["lat"] == "step0.payload.lat"
    assert s.inputs_from["lon"] == "step0.payload.lon"


# ---------------------------------------------------------------------------
# resolve_inputs_from
# ---------------------------------------------------------------------------


def test_resolve_inputs_from_extracts_field_from_prior_step() -> None:
    """Happy path: step0 emitted {lat, lon}; step1 reads them via
    inputs_from."""
    step_results = [
        [
            {
                "event_type": "geocode-match",
                "payload": {"lat": 39.78, "lon": -89.65, "display_name": "X"},
            }
        ],
        [],  # step1 hasn't run yet; this is what step1 reads from
    ]
    overrides = resolve_inputs_from(
        {"lat": "step0.payload.lat", "lon": "step0.payload.lon"},
        step_results,
    )
    assert overrides == {"lat": 39.78, "lon": -89.65}


def test_resolve_inputs_from_scans_for_first_event_with_field() -> None:
    """If step N has multiple events, returns the first one carrying the
    requested key. Order of emission is preserved."""
    step_results = [
        [
            {"event_type": "tool-run-accepted", "payload": {}},
            {"event_type": "geocode-match", "payload": {"lat": 39.78, "lon": -89.65}},
            {"event_type": "tool-run-result", "payload": {"matches": 1}},
        ],
    ]
    overrides = resolve_inputs_from({"lat": "step0.payload.lat"}, step_results)
    assert overrides == {"lat": 39.78}


def test_resolve_inputs_from_skips_missing_step_index() -> None:
    """Reference to step3 with only 2 results -> silently omitted."""
    step_results = [[{"event_type": "tool-run-result", "payload": {}}]]
    overrides = resolve_inputs_from({"lat": "step3.payload.lat"}, step_results)
    assert overrides == {}


def test_resolve_inputs_from_skips_missing_field() -> None:
    """Reference to a field no event in step N carries -> omitted."""
    step_results = [[{"event_type": "tool-run-result", "payload": {"matches": 1}}]]
    overrides = resolve_inputs_from({"lat": "step0.payload.lat"}, step_results)
    assert overrides == {}


def test_resolve_inputs_from_rejects_malformed_syntax() -> None:
    """Garbage references (no `step{N}.payload.key` shape) -> omitted,
    not raised. Locks the syntax surface against accidental query-
    language growth."""
    step_results = [[{"event_type": "tool-run-result", "payload": {"lat": 1.0}}]]
    overrides = resolve_inputs_from(
        {
            "a": "step0.payload",  # missing field
            "b": "stepX.payload.lat",  # non-numeric step index
            "c": "step0.OTHER.lat",  # not 'payload'
            "d": "step0.payload.lat.sub",  # nested -- unsupported
        },
        step_results,
    )
    assert overrides == {}


def test_resolve_inputs_from_skips_none_and_empty_values() -> None:
    """If the prior step emitted lat=None or lat="" we should NOT
    override -- treat it as "not provided" and let the dependent step's
    template default apply."""
    step_results = [
        [
            {"event_type": "geocode-match", "payload": {"lat": None, "lon": ""}},
            {"event_type": "geocode-match", "payload": {"lat": 39.78, "lon": -89.65}},
        ]
    ]
    overrides = resolve_inputs_from(
        {"lat": "step0.payload.lat", "lon": "step0.payload.lon"},
        step_results,
    )
    # Should skip the first event (None / "") and grab the second.
    assert overrides == {"lat": 39.78, "lon": -89.65}


def test_resolve_inputs_from_ignores_non_string_references() -> None:
    """If inputs_from values aren't strings (caller bug), skip them."""
    step_results = [[{"event_type": "tool-run-result", "payload": {"x": 1}}]]
    overrides = resolve_inputs_from(
        {"a": None, "b": 42, "c": "step0.payload.x"},  # type: ignore[dict-item]
        step_results,
    )
    assert overrides == {"c": 1}


# ---------------------------------------------------------------------------
# workflow_runner end-to-end with chained steps
# ---------------------------------------------------------------------------


def test_workflow_runner_chains_step0_output_into_step1_input() -> None:
    """The marquee case: a two-step workflow where step1's `inputs_from`
    pulls a value emitted by step0's adapter. workflow_runner should
    invoke step1 with the chained payload."""
    captured: dict[str, Any] = {}

    def fake_geocode(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "event_type": "geocode-match",
                "payload": {"lat": 39.78, "lon": -89.65, "display_name": "X"},
            },
            {
                "event_type": "tool-run-result",
                "payload": {"matches": 1},
            },
        ]

    def fake_overpass(payload: dict[str, Any]) -> list[dict[str, Any]]:
        captured["overpass_payload"] = dict(payload)
        return [
            {
                "event_type": "tool-run-result",
                "payload": {"source": "overpass", "elements_total": 0},
            }
        ]

    # Build a synthetic 2-step workflow inline.
    test_wf = Workflow(
        id="test.chain",
        name="Chain Test",
        description="Step0 emits lat/lon; step1 reads them",
        steps=[
            WorkflowStep("__geocode__", {"q": "{address}"}, required_seed_keys=("address",)),
            WorkflowStep(
                "__overpass__",
                {"radius_m": 200},
                inputs_from={
                    "lat": "step0.payload.lat",
                    "lon": "step0.payload.lon",
                },
            ),
        ],
    )

    class _FakeEntry:
        def __init__(self, fn: Any) -> None:
            self.callable = fn

    fake_registry = {
        "__geocode__": _FakeEntry(fake_geocode),
        "__overpass__": _FakeEntry(fake_overpass),
    }

    published: list[dict[str, Any]] = []

    def fake_publish(inv_id: str, event: dict[str, Any]) -> int:
        published.append(event)
        return 1

    with (
        patch(
            "osint_goblin_workers.workflow_runner.get_workflow",
            return_value=test_wf,
        ),
        patch(
            "osint_goblin_workers.workflow_runner.get_registry",
            return_value=type("R", (), {"get": staticmethod(fake_registry.get)}),
        ),
        patch(
            "osint_goblin_workers.workflow_runner.publish_event",
            side_effect=fake_publish,
        ),
    ):
        workflow_runner.fn(
            {
                "investigation_id": "inv-1",
                "run_id": "run-1",
                "workflow_id": "test.chain",
                "seed": {"address": "1600 Pennsylvania Ave"},
            }
        )

    # The Overpass step should have been called with lat/lon from step0.
    assert "overpass_payload" in captured, "step1 adapter was never called"
    p = captured["overpass_payload"]
    assert p["lat"] == 39.78
    assert p["lon"] == -89.65
    assert p["radius_m"] == 200
    # `from_workflow` is set by the runner so events can be grouped.
    assert p["from_workflow"] == "test.chain"


def test_workflow_runner_skips_step_when_required_seed_missing() -> None:
    """Step with required_seed_keys not in seed -> tool-run-error emitted,
    step_results gets an empty list (indices stay aligned for inputs_from)."""

    def fake_step1(payload: dict[str, Any]) -> list[dict[str, Any]]:
        # Should NOT be called when seed lacks the key.
        raise AssertionError("step1 ran despite missing required seed key")

    test_wf = Workflow(
        id="test.skip",
        name="Skip Test",
        description="",
        steps=[
            WorkflowStep("__a__", {"x": "{x}"}, required_seed_keys=("x",)),
        ],
    )

    class _FakeEntry:
        def __init__(self, fn: Any) -> None:
            self.callable = fn

    published: list[dict[str, Any]] = []

    with (
        patch(
            "osint_goblin_workers.workflow_runner.get_workflow",
            return_value=test_wf,
        ),
        patch(
            "osint_goblin_workers.workflow_runner.get_registry",
            return_value=type(
                "R", (), {"get": staticmethod({"__a__": _FakeEntry(fake_step1)}.get)}
            ),
        ),
        patch(
            "osint_goblin_workers.workflow_runner.publish_event",
            side_effect=lambda inv, ev: published.append(ev) or 1,
        ),
    ):
        workflow_runner.fn(
            {
                "investigation_id": "inv-1",
                "run_id": "run-1",
                "workflow_id": "test.skip",
                "seed": {},  # missing `x`
            }
        )

    # Should emit a tool-run-error with the skip reason.
    skip_errors = [
        e
        for e in published
        if e.get("event_type") == "tool-run-error"
        and "skipped" in e.get("payload", {}).get("reason", "")
    ]
    assert len(skip_errors) == 1


# ---------------------------------------------------------------------------
# W9.pv production-workflow sanity: Overpass step has inputs_from set
# ---------------------------------------------------------------------------


def test_w9_pv_overpass_step_uses_inputs_from() -> None:
    """The W9.pv production workflow's address_nearby_features step
    should chain lat/lon from the prior nominatim_geocode step. Lock
    this in so a future refactor doesn't silently regress to the
    band-aid self-geocode path."""
    wf = WORKFLOWS["w9.pv"]
    overpass_steps = [s for s in wf.steps if s.adapter_id == "address_nearby_features"]
    assert len(overpass_steps) == 1
    overpass = overpass_steps[0]
    assert overpass.inputs_from == {
        "lat": "step0.payload.lat",
        "lon": "step0.payload.lon",
    }
