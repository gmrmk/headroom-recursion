"""Oracle Compiler: synthesis parsing, the calibration gate, sandbox, integration.

The sandbox tests execute real subprocesses (local python, no network) — still
fully offline. The compiler's model call is faked with a scripted client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from headroom_recursion import oracle
from headroom_recursion.claude import CallResult
from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.ladder import recurse
from tests.conftest import StubClient

# A validator for answers that must contain the number 42 on the final line.
GOOD_SOURCE = """\
def validate(answer: str) -> bool:
    lines = [l.strip() for l in answer.strip().splitlines() if l.strip()]
    return bool(lines) and lines[-1] == "42"
"""

CASES = [
    {"answer": "reasoning...\n42", "should_pass": True, "note": "good"},
    {"answer": "42", "should_pass": True, "note": "bare good"},
    {"answer": "the answer is 41\n41", "should_pass": False, "note": "plausible but wrong"},
    {"answer": "forty-two", "should_pass": False, "note": "words not digits"},
    {"answer": "", "should_pass": False, "note": "empty"},
]


def envelope(source=GOOD_SOURCE, rung=3, cases=CASES, residuals=("whether 42 is justified",)):
    return json.dumps(
        {
            "rung": rung,
            "rationale": "final line must be 42",
            "validator_source": source,
            "residuals": list(residuals),
            "calibration_cases": cases,
        }
    )


@dataclass
class CompilerClient:
    """Returns a canned envelope for the compiler call; StubClient behavior otherwise."""

    reply: str = ""
    inner: StubClient = field(default_factory=StubClient)

    def complete(self, *, model, system, user, max_tokens=2048, temperature=0.7, use_headroom=True):
        if system == oracle.SYNTH_SYSTEM:
            return CallResult(self.reply, 10, 10)
        return self.inner.complete(
            model=model, system=system, user=user,
            max_tokens=max_tokens, temperature=temperature, use_headroom=use_headroom,
        )


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

def test_sandbox_runs_a_real_validator():
    ok, err = oracle.run_validator(GOOD_SOURCE, "thinking\n42")
    assert (ok, err) == (True, "")
    ok, err = oracle.run_validator(GOOD_SOURCE, "41")
    assert (ok, err) == (False, "")


def test_sandbox_crash_is_an_error_not_a_verdict():
    ok, err = oracle.run_validator("def validate(answer):\n    raise ValueError('boom')\n", "x")
    assert ok is None and "boom" in err


def test_sandbox_timeout_is_an_error():
    ok, err = oracle.run_validator(
        "def validate(answer):\n    while True: pass\n", "x", timeout_s=1.0
    )
    assert ok is None and "timed out" in err


# ---------------------------------------------------------------------------
# The calibration gate
# ---------------------------------------------------------------------------

def test_calibration_passes_a_discriminating_validator():
    cases = [oracle.CalibrationCase(c["answer"], c["should_pass"], c["note"]) for c in CASES]
    report = oracle.calibrate(GOOD_SOURCE, cases)
    assert report.passed is True
    assert len(report.checks) == len(CASES)


def test_accept_everything_validator_is_rejected():
    cases = [oracle.CalibrationCase(c["answer"], c["should_pass"], c["note"]) for c in CASES]
    report = oracle.calibrate("def validate(answer):\n    return True\n", cases)
    assert report.passed is False  # missed every planted-bad


def test_too_few_cases_fail_calibration():
    cases = [oracle.CalibrationCase("42", True), oracle.CalibrationCase("x", False)]
    report = oracle.calibrate(GOOD_SOURCE, cases)
    assert report.passed is False
    assert "insufficient" in report.error


# ---------------------------------------------------------------------------
# The compiler
# ---------------------------------------------------------------------------

def test_compile_installs_a_calibrated_validator():
    client = CompilerClient(reply=envelope())
    compiled = oracle.compile_oracle("emit 42", client=client, model="m-big")
    assert compiled.rung == 3
    assert compiled.validator is not None
    assert compiled.validator("thoughts\n42") is True
    assert compiled.validator("nope") is False
    assert "whether 42 is justified" in compiled.residuals
    assert "NOT verified" in compiled.note


def test_compile_demotes_on_failed_calibration():
    bad = envelope(source="def validate(answer):\n    return True\n")
    compiled = oracle.compile_oracle("emit 42", client=CompilerClient(reply=bad), model="m")
    assert compiled.validator is None
    assert compiled.rung == oracle.RUNG_JUDGE
    assert compiled.calibration is not None and compiled.calibration.passed is False


def test_compile_demotes_on_garbage_reply():
    compiled = oracle.compile_oracle("x", client=CompilerClient(reply="no json here"), model="m")
    assert compiled.validator is None and compiled.rung == oracle.RUNG_JUDGE


def test_compile_honours_rung_5_declaration():
    reply = json.dumps({"rung": 5, "validator_source": None, "residuals": ["everything"]})
    compiled = oracle.compile_oracle("judge-only problem", client=CompilerClient(reply=reply), model="m")
    assert compiled.validator is None and compiled.rung == oracle.RUNG_JUDGE
    assert "everything" in compiled.residuals


def test_compile_rejects_unsupported_rung_claims():
    compiled = oracle.compile_oracle("x", client=CompilerClient(reply=envelope(rung=1)), model="m")
    assert compiled.validator is None  # rung 1 backend doesn't exist yet — no authority


# ---------------------------------------------------------------------------
# Integration with the loop
# ---------------------------------------------------------------------------

def one_tier(**kw) -> RecurseConfig:
    return RecurseConfig(ladder=(Tier("m0"),), **kw)


def test_auto_oracle_halts_a_run_on_validated_answer():
    client = CompilerClient(reply=envelope(), inner=StubClient(answers=["42"]))
    cfg = one_tier(n=1, T=3, oracle_auto=True)
    trace = recurse("emit 42", client=client, config=cfg)

    assert trace.halted is True and trace.stop_reason == "validated"
    assert trace.oracle_rung == 3
    assert trace.oracle_calls == 1 and trace.total_calls >= 3
    assert trace.needs_human_review is False  # mechanically validated


def test_generator_never_sees_validator_source():
    client = CompilerClient(reply=envelope(), inner=StubClient(answers=["42"]))
    recurse("emit 42", client=client, config=one_tier(n=2, T=2, oracle_auto=True))

    for kind, prompt in client.inner.prompts_seen:
        if kind in ("latent", "answer"):
            assert "def validate" not in prompt  # pre-registration holds


def test_judge_is_told_about_residuals():
    # Answer never validates -> judge gets called with the oracle status addendum.
    client = CompilerClient(reply=envelope(), inner=StubClient(answers=["not it"]))
    recurse("emit 42", client=client, config=one_tier(n=1, T=1, oracle_auto=True))

    judge_prompts = [u for k, u in client.inner.prompts_seen if k == "judge"]
    assert judge_prompts and all("[ORACLE STATUS]" in u for u in judge_prompts)
    assert "whether 42 is justified" in judge_prompts[0]


def test_demoted_oracle_leaves_judge_in_charge():
    client = CompilerClient(reply="garbage", inner=StubClient(halt_prob=0.95))
    cfg = one_tier(n=1, T=1, oracle_auto=True)
    trace = recurse("x", client=client, config=cfg)

    assert trace.oracle_rung == oracle.RUNG_JUDGE
    assert trace.halted is True and trace.stop_reason == "halt"  # judge authority intact
    assert trace.needs_human_review is True  # judged halt -> flag for a human


def test_callers_config_is_not_mutated():
    client = CompilerClient(reply=envelope(), inner=StubClient(answers=["42"]))
    cfg = one_tier(n=1, T=1, oracle_auto=True)
    recurse("emit 42", client=client, config=cfg)
    assert cfg.validator is None and cfg.oracle_note == ""  # pre-registration on a copy


def test_high_judged_score_flags_human_review_without_halt():
    stub = StubClient(halt_prob=0.6)  # high but below threshold
    trace = recurse("x", client=stub, config=one_tier(n=1, T=1))
    assert trace.halted is False
    assert trace.needs_human_review is True
    assert "NEEDS HUMAN REVIEW" in trace.summary()