# tasks/todos.md — Workflow output-mapping (Margaret ship #2)

**Status:** plan-pending-verification
**Created:** 2026-05-11 (replaces the AI-detector plan from earlier in the session)
**Trigger:** Margaret ship #2 + band-aid count #1 already in (Overpass self-geocode); next band-aid triggers a stop-and-fix

---

## Goal

Let workflow step N+1 read step N's output via a declarative reference. Concretely: W9.pv's `address_nearby_features` step should read `lat`/`lon` from the prior `nominatim_geocode` step's emitted events, instead of the seed.

This clears band-aid #1 (Overpass self-geocoding from address) and prevents band-aid #2 (any future "I need person results to feed into the next adapter" pattern).

---

## Architecture branch point (the one decision worth verifying)

The current `workflow_runner` is a Dramatiq actor that dispatches each step via `tool_runner.send()` — fire-and-forget. It returns before any step actually executes. There's no way for it to see step N's output to feed into step N+1.

Two real ways forward:

### Option A — In-process serialized steps inside workflow_runner

`workflow_runner` calls each step's adapter callable **directly** (same registry, same code that `tool_runner` calls — but synchronous, no separate Dramatiq actor invocation per step). Events get published to the store via the same `publish_event` mechanism so SSE clients see them in real-time.

- **Pros:** simple data flow, trivial inputs_from resolution, one Dramatiq invocation per workflow (cleaner mental model)
- **Cons:** workflow steps share one 10-min ceiling; no per-step Dramatiq retry; long workflow occupies a worker thread

### Option B — Async with await-via-store

`workflow_runner` keeps `tool_runner.send()` but polls the store for each step's `tool-run-result` before dispatching the next.

- **Pros:** preserves per-step Dramatiq retry; existing tool_runner unchanged
- **Cons:** workflow_runner needs a polling loop or subscribe; per-step actor lifecycle adds latency; more complex

### Margaret's call (and recommendation): **Option A**

- Workflows are sequential by definition — per-step parallelism wasn't a real benefit
- Per-step retry rarely matters for read-only OSINT adapters
- All 11 workflows complete in under 2 min total; the 10-min ceiling has plenty of room
- The code becomes simpler, not more complex
- Dramatiq is still in the loop at the workflow-level boundary; just not per-step

If you'd rather Option B, say so before I start; it's a meaningfully different implementation.

---

## Plan (assuming Option A)

### Step 1: WorkflowStep gets `inputs_from`

Add an optional `inputs_from: dict[str, str] | None = None` field to `WorkflowStep`. Maps payload keys → reference strings of the form `step{N}.payload.{key}`.

Example:
```python
WorkflowStep(
    "address_nearby_features",
    {"radius_m": 200},
    inputs_from={
        "lat": "step0.payload.lat",
        "lon": "step0.payload.lon",
    },
    required_seed_keys=(),  # everything comes from prior step
    description="...",
)
```

### Step 2: Reference resolver

`_resolve_inputs_from(inputs_from, step_results) -> dict[str, Any]`:
- Parse `step{N}.payload.{key}` syntax
- For each: scan step_results[N] for the first event whose payload contains `key`, return that value
- If not found → leave the key absent from the override dict (let `required_seed_keys` enforce)
- Returns a dict that's merged into the step's payload AFTER `build_payload(seed)`

### Step 3: Refactor workflow_runner to run in-process

```python
step_results: list[list[dict]] = []
for step_idx, step in enumerate(workflow.steps):
    payload = step.build_payload(payload.seed) or {}
    if step.inputs_from:
        overrides = _resolve_inputs_from(step.inputs_from, step_results)
        payload = {**payload, **overrides}
    # required_seed_keys check after overrides
    entry = get_registry().get(step.adapter_id)
    events = entry.callable(payload)
    for e in events:
        e["payload"]["from_workflow"] = workflow.id
        publish_event(payload.investigation_id, {...wrap...})
    step_results.append(events)
```

### Step 4: W9.pv update

Change the `address_nearby_features` step to use `inputs_from={"lat": "step0.payload.lat", "lon": "step0.payload.lon"}` instead of relying on the band-aid self-geocode.

Keep the self-geocode fallback in the adapter for direct-dispatch users (cmd-K → run individual adapter against an address). Band-aid count goes back to 0 for workflow paths.

### Step 5: Tests

- Unit: `_resolve_inputs_from` for `step{N}.payload.{key}` syntax, missing references, malformed references
- Unit: `WorkflowStep` accepts and stores `inputs_from`
- Integration: workflow_runner runs a fixture workflow with two steps where step 1 reads step 0's output

### Step 6: Update tasks/lessons.md

Add: "2026-05-11 — Band-aid count #1 cleared by shipping workflow output-mapping (Margaret ship #2). Threshold reset to 0."

---

## Risk audit

| Risk | Mitigation |
|---|---|
| In-process steps share one 10min ceiling | Current workflows complete in <2min; reassess when we add a slow workflow |
| Removing tool_runner.send() loses per-step retry | OSINT adapters are read-only; retry value is low; surface in dossier as tool-run-error |
| Existing tests for workflow_runner depend on Dramatiq send semantics | Audit + update; expect ~2-3 test edits |
| `inputs_from` syntax could grow into a query language | Lock the syntax to `step{N}.payload.{key}` only; refuse anything else |

---

## Definition of done

- WorkflowStep accepts `inputs_from`
- workflow_runner resolves it via in-process serialized step execution
- W9.pv uses `inputs_from` instead of self-geocode for the Overpass step
- All Python tests pass (existing + new)
- Live W9.pv smoke via `smoke-workflow.py w9.pv --synthetic` still clean
- Lessons.md band-aid count reset

---

## Out of scope

- Output paths richer than `step{N}.payload.{key}` (no JSONPath, no event-type filters)
- Cross-workflow references
- Conditional steps ("only run step 5 if step 4 verdict is X")
- Replacing tool_runner for direct (non-workflow) adapter dispatches
