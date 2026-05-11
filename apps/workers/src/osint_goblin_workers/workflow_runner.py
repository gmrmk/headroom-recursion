"""Dramatiq actor that runs an ADR-0017 workflow as a sequence of
tool_runner dispatches.

Emits workflow-lifecycle events via the same Redis pub/sub bridge
the per-adapter tool_runner emits through (R-6). The dossier SSE
stream renders them inline with the per-adapter events that the
workflow's steps produce.

Event types emitted:
  - tool-run-accepted (workflow-level acceptance; payload.workflow_id set)
  - tool-run-result (workflow-level completion summary)
  - tool-run-error (per-step skip or workflow-level error)

Per-step adapter events fire from tool_runner as normal -- the
investigator sees the workflow's progress as a stream of
image-match / person-match / geocode-match etc. events with
payload.from_workflow == workflow_id (set by this actor).
"""

from __future__ import annotations

import time
from typing import Annotated, Any
from uuid import uuid4

import dramatiq
from pydantic import BaseModel, ConfigDict, Field

from .publisher import publish_event
from .tool_runner import tool_runner
from .workflows import get_workflow


class WorkflowRunPayload(BaseModel):
    """Payload accepted by workflow_runner. Same shape as ToolRunPayload
    but with workflow_id instead of adapter_id, and seed instead of
    adapter_payload."""

    model_config = ConfigDict(frozen=True)

    investigation_id: str
    run_id: str
    workflow_id: Annotated[str, Field(min_length=1, max_length=64)]
    seed: dict[str, Any] = Field(default_factory=dict)


@dramatiq.actor(
    queue_name="workflow_runner",
    max_retries=2,
    time_limit=600_000,  # 10min wall ceiling per workflow
)
def workflow_runner(req: dict) -> None:
    """Single-entry workflow dispatcher.

    Looks up the workflow definition, builds each step's payload from
    the seed, dispatches each step via tool_runner.send(). Per-step
    failures (missing seed keys, unknown adapter) are emitted as
    tool-run-error events but do not abort the workflow -- subsequent
    steps still fire if their own seed keys are present.
    """
    payload = WorkflowRunPayload.model_validate(req)
    workflow = get_workflow(payload.workflow_id)
    if workflow is None:
        publish_event(
            payload.investigation_id,
            {
                "event_type": "tool-run-error",
                "investigation_id": payload.investigation_id,
                "run_id": payload.run_id,
                "payload": {
                    "reason": f"unknown workflow: {payload.workflow_id!r}",
                    "workflow_id": payload.workflow_id,
                },
            },
        )
        return

    started = time.time()
    publish_event(
        payload.investigation_id,
        {
            "event_type": "tool-run-accepted",
            "investigation_id": payload.investigation_id,
            "run_id": payload.run_id,
            "payload": {
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "step_count": len(workflow.steps),
                "from_workflow": workflow.id,
            },
        },
    )

    dispatched = 0
    skipped = 0
    for step_idx, step in enumerate(workflow.steps):
        sub_payload = step.build_payload(payload.seed)
        if sub_payload is None:
            skipped += 1
            publish_event(
                payload.investigation_id,
                {
                    "event_type": "tool-run-error",
                    "investigation_id": payload.investigation_id,
                    "run_id": payload.run_id,
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

        # Fresh run_id per step so each adapter's events carry their own
        # idempotency key; the workflow's run_id is the umbrella.
        step_run_id = str(uuid4())
        tool_runner.send(
            {
                "investigation_id": payload.investigation_id,
                "run_id": step_run_id,
                "adapter_id": step.adapter_id,
                "adapter_payload": {**sub_payload, "from_workflow": workflow.id},
            }
        )
        dispatched += 1

    publish_event(
        payload.investigation_id,
        {
            "event_type": "tool-run-result",
            "investigation_id": payload.investigation_id,
            "run_id": payload.run_id,
            "payload": {
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "dispatched": dispatched,
                "skipped": skipped,
                "duration_s": round(time.time() - started, 2),
                "from_workflow": workflow.id,
            },
        },
    )
