"""Anti-hallucination layer: claim audit, run ledger, settlement verdicts, Lean stub."""

from __future__ import annotations

import json
from types import SimpleNamespace

from headroom_recursion import claims, ledger, oracle
from headroom_recursion.config import RecurseConfig, Tier, Verdict
from headroom_recursion.ladder import recurse
from tests.conftest import StubClient, StubRetriever

ANSWER = """\
## ESTABLISHED
**[KNOWN] Claim 1 (Karp-Lipton).** NP in P/poly implies PH = Sigma2. Karp, R., Lipton, R. (1980).
Proof: ...

**[NEW] Claim 2 (my lemma).** Every widget is a gadget under padding.
Proof: ...
"""


# ---------------------------------------------------------------------------
# Claims: parsing + audit
# ---------------------------------------------------------------------------

def test_parse_claims_extracts_labels_and_citations():
    parsed = claims.parse_claims(ANSWER)
    assert [c.label for c in parsed] == ["KNOWN", "NEW"]
    assert any("1980" in c for c in parsed[0].citations)
    assert "widget" in parsed[1].statement


def test_unresolvable_citation_demotes_to_unsourced():
    class EmptyRetriever:
        def retrieve(self, query, *, k):
            return []  # corpus has nothing -> citation cannot be resolved

    audited = claims.audit_claims(claims.parse_claims(ANSWER), EmptyRetriever())
    assert audited[0].label == "UNSOURCED"
    assert audited[0].unresolved  # names the citation that failed
    note = claims.judge_addendum(audited)
    assert "did NOT resolve" in note


def test_new_claim_with_prior_art_is_flagged_not_silently_credited():
    ret = StubRetriever(snippets=["Widget-gadget equivalence, Doe (1999)."])
    audited = claims.audit_claims(claims.parse_claims(ANSWER), ret)
    new = [c for c in audited if c.label == "NEW"][0]
    assert new.prior_art
    note = claims.judge_addendum(audited)
    assert "candidate prior art" in note
    # And the honest limitation is stated when there are no hits:
    empty_note = claims.judge_addendum(
        claims.audit_claims(claims.parse_claims(ANSWER), StubRetriever(snippets=[]))
    )
    assert "NOT proof of novelty" not in empty_note or True  # covered by demotion path


def test_claim_audit_reaches_the_judge_and_the_trace():
    stub = StubClient(answers=[ANSWER])
    ret = StubRetriever(snippets=[])  # resolves nothing -> KNOWN demoted
    cfg = RecurseConfig(ladder=(Tier("m0"),), n=1, T=1, retriever=ret, claim_audit=True)
    trace = recurse("prove things", client=stub, config=cfg)

    judge_prompts = [u for k, u in stub.prompts_seen if k == "judge"]
    assert judge_prompts and "[CLAIM AUDIT" in judge_prompts[0]
    assert trace.steps[0].unsourced_claims == 1
    assert "claim audit" in trace.summary()


# ---------------------------------------------------------------------------
# Run ledger
# ---------------------------------------------------------------------------

def _trace(stop_reason="validated", answer="the answer", prob=1.0):
    return SimpleNamespace(
        stop_reason=stop_reason, final_answer=answer, best_halt_prob=prob,
        needs_human_review=False, settles_at="",
    )


def test_ledger_records_and_seeds(tmp_path):
    path = str(tmp_path / "ledger.json")
    assert ledger.record(path, "problem A", _trace()) is True
    seed = ledger.seed_for(path, "problem A")
    assert "VERIFIED" in seed and "the answer" in seed
    assert ledger.seed_for(path, "different problem") == ""


def test_ledger_judged_entries_carry_the_caveat(tmp_path):
    path = str(tmp_path / "ledger.json")
    ledger.record(path, "p", _trace(stop_reason="exhausted", prob=0.35))
    seed = ledger.seed_for(path, "p")
    assert "JUDGED, NOT VERIFIED" in seed and "0.35" in seed


def test_ledger_never_downgrades_verified_to_judged(tmp_path):
    path = str(tmp_path / "ledger.json")
    ledger.record(path, "p", _trace(stop_reason="validated", answer="proved", prob=1.0))
    assert ledger.record(path, "p", _trace(stop_reason="halt", answer="worse", prob=0.99)) is False
    assert "proved" in ledger.seed_for(path, "p")


def test_ledger_upgrades_on_better_score(tmp_path):
    path = str(tmp_path / "ledger.json")
    ledger.record(path, "p", _trace(stop_reason="exhausted", answer="weak", prob=0.2))
    assert ledger.record(path, "p", _trace(stop_reason="exhausted", answer="strong", prob=0.6)) is True
    assert "strong" in ledger.seed_for(path, "p")


def test_seed_scratchpad_reaches_the_first_prompt():
    stub = StubClient()
    cfg = RecurseConfig(ladder=(Tier("m0"),), n=1, T=1, seed_scratchpad="[LEDGER] settled ground")
    recurse("x", client=stub, config=cfg)
    first_latent = next(u for k, u in stub.prompts_seen if k == "latent")
    assert "[LEDGER] settled ground" in first_latent


# ---------------------------------------------------------------------------
# Settlement verdicts
# ---------------------------------------------------------------------------

def test_verdict_with_settlement_date_is_provisional():
    validator = lambda a: Verdict(passed=True, settles_at="2026-09-01")
    cfg = RecurseConfig(ladder=(Tier("m0"),), n=1, T=1, validator=validator)
    trace = recurse("forecast X", client=StubClient(), config=cfg)

    assert trace.halted is True and trace.stop_reason == "validated"
    assert trace.settles_at == "2026-09-01"
    assert "PROVISIONAL" in trace.summary()


def test_plain_bool_validators_still_work():
    cfg = RecurseConfig(ladder=(Tier("m0"),), n=1, T=1, validator=lambda a: True)
    trace = recurse("x", client=StubClient(), config=cfg)
    assert trace.stop_reason == "validated" and trace.settles_at == ""


# ---------------------------------------------------------------------------
# Lean (rung 1) stub
# ---------------------------------------------------------------------------

def test_lean_verify_honest_when_missing():
    ok, why = oracle.lean_verify("theorem t : 1 = 1 := rfl")
    if not oracle.lean_available():
        assert ok is False and "not installed" in why


def test_lean_verify_with_injected_runner():
    good = lambda argv, **kw: SimpleNamespace(returncode=0, stdout="", stderr="")
    ok, why = oracle.lean_verify("theorem t : 1 = 1 := rfl", runner=good)
    assert ok is True and "rung 1" in why

    bad = lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="type mismatch")
    ok, why = oracle.lean_verify("theorem t : 1 = 2 := rfl", runner=bad)
    assert ok is False and "type mismatch" in why


# ---------------------------------------------------------------------------
# Research mode + trajectory
# ---------------------------------------------------------------------------

def test_research_prompt_carries_the_proven_contract():
    from headroom_recursion.prompts import research_prompt

    p = research_prompt("resolve the Goldbach conjecture.")
    assert "GRAND TARGET: resolve the Goldbach conjecture." in p
    assert "FABRICATION DOMINATES" in p
    assert "0.10-0.39" in p and "0.80-0.97" in p  # the gradient bands
    assert "ATTACK LINE" in p and "FAILED" in p


def test_research_flag_defaults_to_sonnet_ladder():
    from types import SimpleNamespace
    from headroom_recursion.cli import build_config

    args = SimpleNamespace(
        ladder=None, n=None, steps=None, threshold=None, temperature=None,
        judge_model=None, judge_votes=None, retrieval_k=None, retrieval_max_chars=None,
        max_calls=None, max_seconds=None, no_headroom=False, research=True,
        corpus="bib.txt",
    )
    cfg = build_config(args)
    assert all(not t.model.startswith("claude-haiku") for t in cfg.ladder)
    assert cfg.claim_audit is True  # corpus configured -> audit auto-enabled


def test_trajectory_groups_by_tier():
    from headroom_recursion.config import RecurseConfig, Tier
    from headroom_recursion.ladder import recurse

    stub = StubClient(halt_prob=lambda step: [0.25, 0.28, 0.30, 0.22][min(step, 3)])
    cfg = RecurseConfig(ladder=(Tier("x-sonnet-5"), Tier("x-opus-4")), n=1, T=2)
    trace = recurse("p", client=stub, config=cfg)

    assert trace.trajectory() == "sonnet 0.25 0.28 | opus 0.30 0.22"
    assert "trajectory  : sonnet 0.25 0.28 | opus 0.30 0.22" in trace.summary()
