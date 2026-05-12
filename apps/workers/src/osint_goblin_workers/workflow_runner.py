"""Dramatiq actor that runs an ADR-0017 workflow as a sequence of
adapter dispatches.

As of Margaret ship #2 (2026-05-11), workflow_runner executes steps
**in-process synchronously** via the adapter registry instead of
fire-and-forget via tool_runner.send(). This unlocks workflow output-
mapping: step N+1 can read step N's emitted payload fields via the
declarative `inputs_from` field on WorkflowStep.

Events are published to the API via the same Redis pub/sub bridge
tool_runner uses. SSE clients see the events arrive in real-time, in
declaration order, just as before.

Event types emitted:
  - tool-run-accepted (workflow-level acceptance; payload.workflow_id set)
  - tool-run-result (workflow-level completion summary)
  - tool-run-error (per-step skip or workflow-level error)

Per-step adapter events fire from this actor as it calls each adapter
in-process -- the investigator sees the workflow's progress as a stream
of image-match / person-match / geocode-match etc. events with
`payload.from_workflow == workflow_id` set by this actor.

Architectural trade-off (recorded in tasks/todos.md 2026-05-11):
  + Workflow runs serialize on one worker thread for one Dramatiq
    invocation each (10min ceiling). All 11 current workflows complete
    in well under that bound.
  - No per-step Dramatiq retry. Adapter exceptions surface as
    tool-run-error events.
  + Output-mapping is trivial; events from prior steps are in scope.
"""

from __future__ import annotations

import time
import traceback
from typing import Annotated, Any
from uuid import uuid4

import dramatiq
from pydantic import BaseModel, ConfigDict, Field

from .adapters import get_registry
from .publisher import publish_event
from .workflows import get_workflow, resolve_inputs_from


class WorkflowRunPayload(BaseModel):
    """Payload accepted by workflow_runner. Same shape as ToolRunPayload
    but with workflow_id instead of adapter_id, and seed instead of
    adapter_payload."""

    model_config = ConfigDict(frozen=True)

    investigation_id: str
    run_id: str
    workflow_id: Annotated[str, Field(min_length=1, max_length=64)]
    seed: dict[str, Any] = Field(default_factory=dict)


def _publish(investigation_id: str, run_id: str, event: dict[str, Any]) -> None:
    """Stamp investigation_id + run_id then publish via the bridge."""
    publish_event(
        investigation_id,
        {**event, "investigation_id": investigation_id, "run_id": run_id},
    )


@dramatiq.actor(
    queue_name="workflow_runner",
    max_retries=2,
    time_limit=600_000,  # 10min wall ceiling per workflow
)
def workflow_runner(req: dict) -> None:
    """Single-entry workflow dispatcher.

    Looks up the workflow definition, runs each step in declaration order
    by calling the adapter callable in-process. Accumulates step events
    so that later steps can resolve `inputs_from` references against
    prior outputs.

    Per-step failures (missing seed keys, unknown adapter, adapter raises)
    are emitted as tool-run-error events but do NOT abort the workflow --
    subsequent steps still fire if their own preconditions are met.
    """
    payload = WorkflowRunPayload.model_validate(req)
    workflow = get_workflow(payload.workflow_id)
    if workflow is None:
        _publish(
            payload.investigation_id,
            payload.run_id,
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"unknown workflow: {payload.workflow_id!r}",
                    "workflow_id": payload.workflow_id,
                },
            },
        )
        return

    started = time.time()
    _publish(
        payload.investigation_id,
        payload.run_id,
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "step_count": len(workflow.steps),
                "from_workflow": workflow.id,
            },
        },
    )

    registry = get_registry()
    step_results: list[list[dict[str, Any]]] = []
    dispatched = 0
    skipped = 0
    errored = 0

    for step_idx, step in enumerate(workflow.steps):
        # Build the step's payload from the seed, then merge any
        # output-mapping overrides from prior steps' results.
        sub_payload = step.build_payload(payload.seed)
        if sub_payload is None:
            skipped += 1
            step_results.append([])  # keep indices aligned for inputs_from
            _publish(
                payload.investigation_id,
                payload.run_id,
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": (
                            f"workflow step {step_idx + 1}/{len(workflow.steps)} skipped: "
                            f"required seed key(s) absent for {step.adapter_id!r}"
                        ),
                        "workflow_id": workflow.id,
                        "step_index": step_idx,
                        "adapter_id": step.adapter_id,
                        "required_seed_keys": list(step.required_seed_keys),
                        "from_workflow": workflow.id,
                    },
                },
            )
            continue

        if step.inputs_from:
            overrides = resolve_inputs_from(step.inputs_from, step_results)
            sub_payload = {**sub_payload, **overrides}

        entry = registry.get(step.adapter_id)
        if entry is None:
            errored += 1
            step_results.append([])
            _publish(
                payload.investigation_id,
                payload.run_id,
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"unknown adapter: {step.adapter_id!r}",
                        "workflow_id": workflow.id,
                        "step_index": step_idx,
                        "adapter_id": step.adapter_id,
                        "from_workflow": workflow.id,
                    },
                },
            )
            continue

        # Run the adapter in-process. Each step gets a fresh run_id so
        # its events have their own idempotency key under the workflow
        # umbrella.
        step_run_id = str(uuid4())
        try:
            events = entry.callable({**sub_payload, "from_workflow": workflow.id})
        except Exception as exc:
            errored += 1
            step_results.append([])
            _publish(
                payload.investigation_id,
                step_run_id,
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": (
                            f"adapter {step.adapter_id!r} raised " f"{type(exc).__name__}: {exc}"
                        ),
                        "workflow_id": workflow.id,
                        "step_index": step_idx,
                        "adapter_id": step.adapter_id,
                        "from_workflow": workflow.id,
                        "traceback_tail": traceback.format_exc()[-800:],
                    },
                },
            )
            continue

        events = events or []
        step_results.append(events)
        for ev in events:
            # Annotate with workflow id so the SSE client + dossier can
            # group events by workflow run.
            evp = ev.get("payload", {}) if isinstance(ev, dict) else {}
            if isinstance(evp, dict):
                evp = {**evp, "from_workflow": workflow.id}
            _publish(
                payload.investigation_id,
                step_run_id,
                {**ev, "payload": evp},
            )
        dispatched += 1

    _publish(
        payload.investigation_id,
        payload.run_id,
        {
            "event_type": "tool-run-result",
            "payload": {
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "dispatched": dispatched,
                "skipped": skipped,
                "errored": errored,
                "duration_s": round(time.time() - started, 2),
                "from_workflow": workflow.id,
            },
        },
    )
