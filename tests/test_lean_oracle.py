"""Rung-1 Lean oracle: splice soundness, escape screening, axiom-audit authority.

The adversarial cases mirror the risk register's R1 attack surfaces one-for-one:
a decider pass must be impossible to obtain by anything but a kernel-checked
proof of the pinned statement under the three standard axioms.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from headroom_recursion import lean_oracle
from headroom_recursion.config import RecurseConfig, Tier, Verdict
from headroom_recursion.ladder import recurse
from headroom_recursion.lean_oracle import (
    STANDARD_AXIOMS,
    audit_axioms,
    extract_lean_blocks,
    load_skeleton,
    make_decider_oracle,
    make_gate_oracle,
    splice,
)
from tests.conftest import StubClient

SKELETON = """\
-- LEAN-ORACLE-TARGET: main_goal
theorem main_goal : 1 + 1 = 2 :=
  sorry
"""


@pytest.fixture
def skeleton_file(tmp_path):
    path = tmp_path / "statement.lean"
    path.write_text(SKELETON)
    return str(path)


def lean_runner(*, ok=True, stdout="", stderr=""):
    """Fake `lean` process: records invocations, returns a scripted result."""

    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw))
        return SimpleNamespace(returncode=0 if ok else 1, stdout=stdout, stderr=stderr)

    runner.calls = calls
    return runner


def clean_pass_output(target="main_goal"):
    return f"'{target}' depends on axioms: [propext, Classical.choice, Quot.sound]"


# ---------------------------------------------------------------------------
# Block extraction and splicing
# ---------------------------------------------------------------------------


def test_extracts_only_lean_fences():
    answer = "prose\n```lean\nrfl\n```\nmore\n```python\nx=1\n```\n```lean\nsimp\n```"
    assert extract_lean_blocks(answer) == ["rfl", "simp"]


def test_splice_single_line_proof(skeleton_file):
    sk = load_skeleton(skeleton_file)
    assert "  (rfl)" in splice(sk, "rfl")
    assert "sorry" not in splice(sk, "rfl")


def test_splice_multiline_by_block_preserves_relative_indent(skeleton_file):
    sk = load_skeleton(skeleton_file)
    out = splice(sk, "by\n  have h : True := trivial\n  rfl")
    lines = out.splitlines()
    i = lines.index("  (by")
    assert lines[i + 1] == "    have h : True := trivial"
    assert lines[i + 2] == "    rfl"
    assert lines[i + 3] == "  )"


def test_skeleton_contract_enforced(tmp_path):
    two = tmp_path / "two.lean"
    two.write_text("-- LEAN-ORACLE-TARGET: t\ntheorem t : True :=\n  sorry\nexample : True :=\n  sorry\n")
    with pytest.raises(ValueError, match="exactly one"):
        load_skeleton(str(two))

    unmarked = tmp_path / "unmarked.lean"
    unmarked.write_text("theorem t : True :=\n  sorry\n")
    with pytest.raises(ValueError, match="LEAN-ORACLE-TARGET"):
        load_skeleton(str(unmarked))


# ---------------------------------------------------------------------------
# R1 adversarial corpus: every escape is rejected
# ---------------------------------------------------------------------------

ESCAPES = [
    ("sorry spelled out", "by sorry"),
    ("admit", "by admit"),
    ("sorryAx constant", "sorryAx (1 + 1 = 2) true"),
    ("paren rebalance + axiom", "trivial)\naxiom h : False\nexample : False := (h"),
    ("native_decide", "by native_decide"),
    ("implemented_by", "@[implemented_by cheat] def x := 0"),
    ("extern attribute", "@[extern \"c_lie\"] def x := 0"),
    ("unsafe", "unsafe rfl"),
    ("opaque smuggle", "opaque bad : False"),
    ("macro rewrite", "macro \"prove\" : term => `(sorry)"),
    ("elab rewrite", "elab \"prove\" : term => pure (mkConst ``trivial)"),
    ("notation rewrite", "notation \"qed\" => sorry"),
    ("initialize", "initialize hack : Unit ← pure ()"),
    ("import injection", "import Mathlib.Tactic\nrfl"),
    ("set_option skipKernelTC", "set_option debug.skipKernelTC true in rfl"),
    ("top-level theorem", "theorem other : True := trivial"),
]


@pytest.mark.parametrize("label,proof", ESCAPES, ids=[e[0] for e in ESCAPES])
def test_decider_screens_reject_escape(skeleton_file, label, proof):
    runner = lean_runner(ok=True, stdout=clean_pass_output())
    oracle = make_decider_oracle(skeleton_file, runner=runner)
    answer = f"```lean\n{proof}\n```"
    assert oracle.validator(answer) is False
    assert "forbidden" in oracle.feedback(answer) or "rejected" in oracle.feedback(answer)
    assert runner.calls == []  # screened before any compile


def test_comment_mentions_of_keywords_do_not_trip_screens(skeleton_file):
    runner = lean_runner(ok=True, stdout=clean_pass_output())
    oracle = make_decider_oracle(skeleton_file, runner=runner)
    answer = "```lean\n-- no axiom or sorry here, honest\n/- native_decide is screened -/\nrfl\n```"
    verdict = oracle.validator(answer)
    assert isinstance(verdict, Verdict) and verdict.passed


# ---------------------------------------------------------------------------
# The axiom audit is the authority, and it fails closed
# ---------------------------------------------------------------------------


def test_audit_accepts_standard_axioms_only():
    ok, why = audit_axioms(clean_pass_output(), "main_goal")
    assert ok and "standard" in why

    ok, why = audit_axioms("'main_goal' does not depend on any axioms", "main_goal")
    assert ok

    ok, why = audit_axioms("'main_goal' depends on axioms: [propext, sorryAx]", "main_goal")
    assert not ok and "sorryAx" in why

    ok, why = audit_axioms(
        "'main_goal' depends on axioms: [Lean.ofReduceBool]", "main_goal"
    )
    assert not ok  # native_decide's fingerprint

    ok, why = audit_axioms("'main_goal' depends on axioms: [my_axiom]", "main_goal")
    assert not ok


def test_audit_fails_closed_on_missing_or_foreign_output():
    assert audit_axioms("", "main_goal")[0] is False
    assert audit_axioms("garbage output", "main_goal")[0] is False
    # Output about a DIFFERENT declaration never validates the target.
    assert audit_axioms("'other' depends on axioms: [propext]", "main_goal")[0] is False


def test_decider_pass_requires_compile_and_audit(skeleton_file):
    runner = lean_runner(ok=True, stdout=clean_pass_output())
    oracle = make_decider_oracle(skeleton_file, runner=runner)
    verdict = oracle.validator("```lean\nrfl\n```")
    assert isinstance(verdict, Verdict) and verdict.passed and verdict.confidence == 1.0
    assert "main_goal" in verdict.note
    (cmd, kw) = runner.calls[0]
    assert cmd[0] == "lean"  # no project_dir -> core lean


def test_decider_rejects_clean_compile_with_rogue_axiom(skeleton_file):
    runner = lean_runner(ok=True, stdout="'main_goal' depends on axioms: [propext, cheat]")
    oracle = make_decider_oracle(skeleton_file, runner=runner)
    assert oracle.validator("```lean\nrfl\n```") is False
    assert "axiom audit" in oracle.feedback("```lean\nrfl\n```")


def test_decider_compile_failure_feeds_back_compiler_errors(skeleton_file):
    runner = lean_runner(ok=False, stderr="error: type mismatch at rfl")
    oracle = make_decider_oracle(skeleton_file, runner=runner)
    answer = "```lean\nrfl\n```"
    assert oracle.validator(answer) is False
    assert "type mismatch" in oracle.feedback(answer)


def test_decider_no_block_is_not_validated(skeleton_file):
    oracle = make_decider_oracle(skeleton_file, runner=lean_runner())
    assert oracle.validator("prose only, no formal offer") is False


def test_compile_memo_shared_between_validator_and_feedback(skeleton_file):
    runner = lean_runner(ok=False, stderr="error: nope")
    oracle = make_decider_oracle(skeleton_file, runner=runner)
    answer = "```lean\nrfl\n```"
    oracle.validator(answer)
    oracle.feedback(answer)
    oracle.validator(answer)
    assert len(runner.calls) == 1  # one compile per unique answer, ever


def test_decider_persists_audit_artifacts(skeleton_file, tmp_path):
    art = tmp_path / "artifacts"
    runner = lean_runner(ok=True, stdout=clean_pass_output())
    oracle = make_decider_oracle(skeleton_file, runner=runner, artifact_dir=str(art))
    oracle.validator("```lean\nrfl\n```")
    files = sorted(p.name for p in art.iterdir())
    assert any(f.endswith(".lean") for f in files) and any(f.endswith(".out") for f in files)


# ---------------------------------------------------------------------------
# Gate mode: rejections mechanical, passes deferential, absence harmless
# ---------------------------------------------------------------------------


def test_gate_no_block_is_pass_with_note():
    oracle = make_gate_oracle(runner=lean_runner())
    verdict = oracle.validator("no formal content")
    assert isinstance(verdict, Verdict) and verdict.passed
    assert "nothing mechanically checked" in verdict.note


def test_gate_toolchain_missing_is_pass_with_note_never_rejection():
    def runner(cmd, **kw):
        raise FileNotFoundError("lean not on PATH")

    oracle = make_gate_oracle(runner=runner)
    verdict = oracle.validator("```lean\ntheorem t : True := trivial\n```")
    assert isinstance(verdict, Verdict) and verdict.passed
    assert "could not run" in verdict.note


def test_gate_compile_failure_rejects_with_feedback():
    runner = lean_runner(ok=False, stderr="error: unknown identifier 'zorp'")
    oracle = make_gate_oracle(runner=runner)
    answer = "```lean\ntheorem t : True := zorp\n```"
    assert oracle.validator(answer) is False
    assert "zorp" in oracle.feedback(answer)


def test_gate_sorry_file_rejected_before_compile():
    runner = lean_runner(ok=True)
    oracle = make_gate_oracle(runner=runner)
    assert oracle.validator("```lean\ntheorem t : True := by sorry\n```") is False
    assert runner.calls == []


def test_gate_pass_note_disclaims_statement_correspondence():
    runner = lean_runner(ok=True, stdout="")
    oracle = make_gate_oracle(runner=runner)
    verdict = oracle.validator("```lean\ntheorem t : True := trivial\n```")
    assert isinstance(verdict, Verdict) and verdict.passed
    assert "NOT checked" in verdict.note


# ---------------------------------------------------------------------------
# trm gate-branch regressions (R9 + the discarded-Verdict note)
# ---------------------------------------------------------------------------


def _gate_cfg(validator, **over):
    base = dict(
        ladder=(Tier("m0"), Tier("m1")), n=1, T=2,
        validator=validator, oracle_sufficient=False, oracle_rung=1,
    )
    base.update(over)
    return RecurseConfig(**base)


def test_gate_validator_exception_is_not_a_mechanical_rejection():
    """R9: a broken toolchain must inform the judge, never zero the run."""

    def broken(answer):
        raise RuntimeError("lake exploded")

    stub = StubClient(halt_prob=0.95)
    trace = recurse("x", client=stub, config=_gate_cfg(broken))

    assert trace.halted and trace.stop_reason == "halt"  # judge ran and halted
    assert all(not s.gate_rejected for s in trace.steps)
    assert any("lake exploded" in s.validator_error for s in trace.steps)
    judge_prompts = [u for k, u in stub.prompts_seen if k == "judge"]
    assert any("ERRORED" in u for u in judge_prompts)  # judge told the gate was down


def test_gate_pass_note_reaches_the_judge():
    ok_note = Verdict(passed=True, note="no ```lean block offered; nothing mechanically checked")
    stub = StubClient(halt_prob=0.0)
    trace = recurse("x", client=stub, config=_gate_cfg(lambda a: ok_note))

    judge_prompts = [u for k, u in stub.prompts_seen if k == "judge"]
    assert any("nothing mechanically checked" in u for u in judge_prompts)
    assert all(not s.gate_rejected for s in trace.steps)


def test_gate_clean_false_still_rejects_mechanically():
    stub = StubClient(halt_prob=0.95)  # judge would halt, but must be skipped
    trace = recurse("x", client=stub, config=_gate_cfg(lambda a: False))

    assert not trace.halted
    assert all(s.gate_rejected for s in trace.steps)
    assert stub.count("judge") == 0


def test_hand_wired_oracle_rung_lands_in_trace():
    stub = StubClient(halt_prob=0.0)
    trace = recurse("x", client=stub, config=_gate_cfg(lambda a: False))
    assert trace.oracle_rung == 1 and trace.oracle_gate_only
    assert "rung 1" in trace.summary()
