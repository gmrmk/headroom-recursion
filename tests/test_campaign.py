"""Campaign loop, heartbeat client, ledger epsilon, report — all stubbed."""

from __future__ import annotations

import json

import pytest

from headroom_recursion import campaign, ledger, prompts
from headroom_recursion.claude import CallResult
from headroom_recursion.config import RecurseConfig, Tier, Verdict
from headroom_recursion.heartbeat import CostCapExceeded, HeartbeatClient
from tests.conftest import StubClient


def tiny_cfg(**over):
    base = dict(ladder=(Tier("m0"),), n=1, T=1)
    base.update(over)
    return RecurseConfig(**base)


# ---------------------------------------------------------------------------
# HeartbeatClient
# ---------------------------------------------------------------------------


class CostedStub(StubClient):
    """StubClient whose calls each cost a fixed amount."""

    def complete(self, **kw):
        res = super().complete(**kw)
        res.cost_usd = 0.10
        return res


def test_heartbeat_counts_phases_and_cost(tmp_path):
    path = tmp_path / "hb.json"
    hb = HeartbeatClient(CostedStub(halt_prob=0.0), str(path))
    hb.meta["run"] = 7

    hb.complete(model="m", system=prompts.LATENT_SYSTEM, user="u")
    hb.complete(model="m", system=prompts.HALT_SYSTEM, user="u")

    assert hb.calls == 2 and hb.cost_usd == pytest.approx(0.20)
    beat = json.loads(path.read_text())
    assert beat["phase"] == "judge" and beat["status"] == "ok"
    assert beat["calls"] == 2 and beat["run"] == 7
    assert beat["cost_usd"] == pytest.approx(0.20)


def test_heartbeat_beats_before_call_so_stalls_are_visible(tmp_path):
    path = tmp_path / "hb.json"

    class Hanging:
        def complete(self, **kw):
            beat = json.loads(path.read_text())  # written BEFORE the inner call
            assert beat["status"] == "in-call"
            raise TimeoutError("stalled")

    hb = HeartbeatClient(Hanging(), str(path))
    with pytest.raises(TimeoutError):
        hb.complete(model="m", system=prompts.LATENT_SYSTEM, user="u")
    assert json.loads(path.read_text())["status"] == "error: TimeoutError"


def test_cost_cap_raises_before_spending(tmp_path):
    inner = CostedStub(halt_prob=0.0)
    hb = HeartbeatClient(inner, str(tmp_path / "hb.json"), max_cost_usd=0.15)

    hb.complete(model="m", system=prompts.LATENT_SYSTEM, user="u")  # $0.10 < cap
    hb.complete(model="m", system=prompts.LATENT_SYSTEM, user="u")  # $0.20 >= cap
    with pytest.raises(CostCapExceeded):
        hb.complete(model="m", system=prompts.LATENT_SYSTEM, user="u")
    assert len(inner.calls) == 2  # the third call never reached the backend


# ---------------------------------------------------------------------------
# Ledger epsilon (R7)
# ---------------------------------------------------------------------------


def _trace(score, *, answer="a", stop="exhausted"):
    from headroom_recursion.trace import RunTrace

    t = RunTrace(problem="p", final_answer=answer, stop_reason=stop)
    t.best_halt_prob = score
    return t


def test_judged_ledger_requires_real_margin(tmp_path):
    path = str(tmp_path / "ledger.json")
    assert ledger.record(path, "p", _trace(0.30))
    assert not ledger.record(path, "p", _trace(0.31))  # noise, not progress
    assert not ledger.record(path, "p", _trace(0.35))  # exactly epsilon: still noise
    assert ledger.record(path, "p", _trace(0.36))      # beats incumbent + epsilon


def test_verified_needs_no_margin_and_beats_judged(tmp_path):
    path = str(tmp_path / "ledger.json")
    assert ledger.record(path, "p", _trace(0.90))
    assert ledger.record(path, "p", _trace(0.10, stop="validated"))  # verified beats judged
    assert not ledger.record(path, "p", _trace(0.99))                # judged never demotes
    assert ledger.record(path, "p", _trace(0.11, stop="validated"))  # strictly better verified
    assert not ledger.record(path, "p", _trace(0.11, stop="validated"))  # tie: keep incumbent


# ---------------------------------------------------------------------------
# Campaign loop
# ---------------------------------------------------------------------------


def run_campaign(tmp_path, client, **over):
    kwargs = dict(
        client=client,
        base_config=tiny_cfg(),
        runs=5,
        ledger_path=str(tmp_path / "ledger.json"),
        trace_dir=str(tmp_path / "runs"),
        dry_stop=2,
        log=lambda m: None,
    )
    kwargs.update(over)
    return campaign.run_campaign("the problem", **kwargs)


class PerRunScores:
    """Judge score depends on how many answer-updates have happened (1/run here)."""

    def __init__(self, scores, cost=0.01):
        self.scores = list(scores)
        self.answers = 0
        self.cost = cost

    def complete(self, *, model, system, user, **kw):
        if system == prompts.HALT_SYSTEM:
            idx = min(self.answers - 1, len(self.scores) - 1)
            return CallResult(
                f'{{"halt_prob": {self.scores[idx]}, "reason": "scripted"}}', 10, 10,
                cost_usd=self.cost,
            )
        if system == prompts.ANSWER_SYSTEM:
            self.answers += 1
            return CallResult(f"answer-{self.answers}", 10, 10, cost_usd=self.cost)
        return CallResult(f"scratch-{self.answers}", 10, 10, cost_usd=self.cost)


def test_campaign_dry_stop_and_artifacts(tmp_path):
    # Run 0 records (0.30); runs 1-2 are noise (no ledger movement) -> dry stop.
    result = run_campaign(tmp_path, PerRunScores([0.30, 0.31, 0.32, 0.33, 0.34]))

    assert result.stopped == "dry"
    assert result.runs_completed == 3
    runs_dir = tmp_path / "runs"
    assert (runs_dir / "run-000.json").exists() and (runs_dir / "run-002.summary.txt").exists()
    summary = json.loads((runs_dir / "campaign-summary.json").read_text())
    assert summary["stopped"] == "dry"
    assert [r["ledger_improved"] for r in summary["runs"]] == [True, False, False]
    assert (runs_dir / "heartbeat.json").exists()


def test_campaign_validated_stops_immediately(tmp_path):
    cfg = tiny_cfg(validator=lambda a: Verdict(passed=True, note="oracle"), oracle_rung=1)
    result = run_campaign(tmp_path, PerRunScores([0.5]), base_config=cfg)

    assert result.stopped == "validated" and result.validated
    assert result.runs_completed == 1
    entry = ledger.load(str(tmp_path / "ledger.json")).popitem()[1]
    assert entry["verified"] is True


def test_campaign_survives_a_dying_run(tmp_path):
    class DiesSecondRun(PerRunScores):
        def complete(self, *, model, system, user, **kw):
            if self.answers >= 1 and system == prompts.LATENT_SYSTEM:
                self.answers += 100  # ensure we only die once
                raise RuntimeError("transport died")
            return super().complete(model=model, system=system, user=user, **kw)

    result = run_campaign(tmp_path, DiesSecondRun([0.30]), runs=3, dry_stop=3)
    # Run 1 died mid-flight; its trace still persisted and the campaign went on.
    assert result.runs_completed == 3
    assert (tmp_path / "runs" / "run-001.json").exists()


def test_campaign_cost_cap_stops_between_runs(tmp_path):
    # Each run: 1 latent + 1 answer + 1 judge = 3 calls * $0.40.
    result = run_campaign(
        tmp_path, PerRunScores([0.30, 0.50, 0.70, 0.90], cost=0.40), max_cost_usd=2.0
    )
    assert result.stopped == "cost-cap"
    assert result.total_cost_usd >= 2.0
    assert result.runs_completed == 2  # $1.20 after run 0, $2.40 after run 1 -> stop


def test_campaign_seeds_from_ledger_between_runs(tmp_path):
    seen_seeds = []

    class SeedSpy(PerRunScores):
        def complete(self, *, model, system, user, **kw):
            if system == prompts.LATENT_SYSTEM:
                seen_seeds.append("[LEDGER" in user)
            return super().complete(model=model, system=system, user=user, **kw)

    run_campaign(tmp_path, SeedSpy([0.30, 0.40, 0.41]), runs=3, dry_stop=5)
    assert seen_seeds[0] is False        # run 0: nothing to seed from
    assert all(seen_seeds[1:])           # later runs carry the ledger draft


def test_campaign_report_labels_judged_outcomes_honestly(tmp_path):
    run_campaign(tmp_path, PerRunScores([0.45, 0.46, 0.47]))
    report = campaign.campaign_report(str(tmp_path / "runs"), str(tmp_path / "ledger.json"))

    assert "judged opinion" in report
    assert "NEEDS HUMAN REVIEW" in report
    assert "Nothing here is a verified result" in report
    assert "| 0 |" in report  # trajectory table


def test_campaign_requires_sane_knobs(tmp_path):
    with pytest.raises(ValueError):
        run_campaign(tmp_path, PerRunScores([0.1]), runs=0)
    with pytest.raises(ValueError):
        run_campaign(tmp_path, PerRunScores([0.1]), dry_stop=0)


# ---------------------------------------------------------------------------
# Answer seeding: the incumbent is refined, never rebuilt
# ---------------------------------------------------------------------------


def test_seed_pair_provides_answer_and_provenance_note(tmp_path):
    path = str(tmp_path / "ledger.json")
    ledger.record(path, "p", _trace(0.30, answer="the incumbent draft"))
    note, answer = ledger.seed_pair(path, "p")
    assert answer == "the incumbent draft"
    assert "JUDGED, NOT VERIFIED" in note and "0.30" in note
    assert ledger.seed_pair(path, "unknown problem") == ("", "")

    ledger.record(path, "p", _trace(0.10, answer="proved", stop="validated"))
    note, answer = ledger.seed_pair(path, "p")
    assert answer == "proved" and "VERIFIED" in note


def test_seeded_answer_is_the_first_candidate_not_rebuilt():
    from headroom_recursion.ladder import recurse

    cfg = tiny_cfg(seed_answer="THE INCUMBENT", seed_scratchpad="[LEDGER] provenance")
    stub = PerRunScores([0.0])
    prompts_seen = []

    class Spy:
        def complete(self, *, model, system, user, **kw):
            prompts_seen.append((system, user))
            return stub.complete(model=model, system=system, user=user)

    recurse("p", client=Spy(), config=cfg)
    latent = [u for s, u in prompts_seen if s == prompts.LATENT_SYSTEM]
    assert "THE INCUMBENT" in latent[0]          # candidate answer, not "(none yet)"
    assert "(none yet)" not in latent[0]
    assert "[LEDGER] provenance" in latent[0]    # provenance rides the scratchpad


def test_campaign_seeds_answer_between_runs(tmp_path):
    seeded_answers = []

    class AnswerSeedSpy(PerRunScores):
        def complete(self, *, model, system, user, **kw):
            if system == prompts.LATENT_SYSTEM and "CURRENT CANDIDATE ANSWER:" in user:
                block = user.split("CURRENT CANDIDATE ANSWER:")[1].split("REASONING")[0]
                seeded_answers.append("answer-" in block)
            return super().complete(model=model, system=system, user=user, **kw)

    run_campaign(tmp_path, AnswerSeedSpy([0.30, 0.40, 0.41]), runs=3, dry_stop=5)
    assert seeded_answers[0] is False   # run 0 starts fresh
    assert seeded_answers[1] is True    # run 1+ carries the incumbent answer
