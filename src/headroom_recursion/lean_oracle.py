"""Rung-1 oracles: Lean 4 as the loop's mechanical verifier.

Two factories, two levels of authority:

* ``make_gate_oracle`` — the model ships complete Lean files in its answer; they
  must compile. Compile failure is a mechanical REJECTION (judge skipped,
  compiler errors fed back CEGIS-style); compile success is only a GATE PASS —
  nothing checks that what was proved is what the problem asked, so the judge
  keeps scoring correctness. No lean block / no toolchain / a crashed compile
  is a pass-with-note, never a rejection: absence of the checker must not
  zero the run.

* ``make_decider_oracle`` — the trusted, hand-authored skeleton file pins the
  theorem STATEMENT (so statement drift is impossible by construction) and the
  model contributes only the proof, spliced into the skeleton's single
  ``sorry`` placeholder. A pass DECIDES correctness (``sufficient=True``) and
  its authority rests on the kernel, not on string screens: after a clean
  compile, ``#print axioms <target>`` must report a subset of Lean's three
  standard axioms — anything else (``sorryAx``, smuggled axioms,
  ``native_decide``'s ``Lean.ofReduceBool``/``Lean.trustCompiler``) fails,
  and unparseable audit output FAILS CLOSED.

The pre-splice/pre-compile token screens are user experience, not authority:
they turn the obvious escapes into instant, explainable feedback instead of a
compile round-trip. Comments are stripped before screening so prose about
axioms doesn't trip them.

Both factories return an object whose ``validator`` and ``feedback`` plug into
the existing ``RecurseConfig`` seams and SHARE one compile memo per answer —
the loop runs both on the same answer within a step, and a Mathlib compile
costs 30-90s.

Trust base of a decider pass: the Lean kernel + the skeleton file. Nothing
model-written is trusted.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

from headroom_recursion.config import Verdict

STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

_BLOCK_RE = re.compile(r"```lean[^\S\n]*\n(.*?)```", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--.*?$", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/-.*?-/", re.DOTALL)
_TARGET_MARKER_RE = re.compile(r"^--\s*LEAN-ORACLE-TARGET:\s*([A-Za-z0-9_.'₀-₉]+)\s*$", re.MULTILINE)

# Screens for a DECIDER proof block: the model may contribute a proof term or
# tactic block, nothing else. Everything here either smuggles trust (axiom,
# native_decide, unsafe, extern), rewrites the language under us (macro, elab,
# notation, set_option), or tries to leave term position (import, top-level
# declarations). False positives are cheap: a screened block is one feedback
# line and one repaired step, not a lost run.
_DECIDER_SCREENS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pat, re.MULTILINE))
    for name, pat in [
        ("sorry", r"\bsorry\b"),
        ("admit", r"\badmit\b"),
        ("sorryAx", r"\bsorryAx\b"),
        ("axiom", r"\baxiom\b"),
        ("native_decide", r"\bnative_decide\b"),
        ("implemented_by", r"\bimplemented_by\b"),
        ("extern attribute", r"@\[\s*extern"),
        ("unsafe", r"\bunsafe\b"),
        ("opaque", r"\bopaque\b"),
        ("macro", r"\bmacro\b"),
        ("elab", r"\belab\b"),
        ("notation", r"\bnotation\b"),
        ("initialize", r"\binitialize\b"),
        ("import", r"\bimport\b"),
        ("set_option", r"\bset_option\b"),
        ("top-level declaration", r"^\s*(theorem|lemma|def|abbrev|instance|structure|inductive|class)\b"),
    ]
)

# Screens for a GATE file: the model writes a whole file, so imports, options,
# and declarations are its job. Only trust-smuggling constructs are screened —
# a gate REJECTION is final for the step, so this list must be low-false-positive,
# and a gate PASS defers to the judge anyway.
_GATE_SCREENS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pat, re.MULTILINE))
    for name, pat in [
        ("sorry", r"\bsorry\b"),
        ("admit", r"\badmit\b"),
        ("sorryAx", r"\bsorryAx\b"),
        ("axiom declaration", r"^\s*axiom\b"),
        ("native_decide", r"\bnative_decide\b"),
        ("implemented_by", r"\bimplemented_by\b"),
        ("extern attribute", r"@\[\s*extern"),
        ("skipKernelTC", r"debug\.skipKernelTC"),
    ]
)


def extract_lean_blocks(answer: str) -> list[str]:
    """All ```lean fenced blocks in an answer, outermost fences only."""

    return [m.group(1).strip() for m in _BLOCK_RE.finditer(answer or "") if m.group(1).strip()]


def strip_comments(code: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", code))


def screen(code: str, screens) -> str:
    """Return a reason string when ``code`` trips a screen, else ''."""

    bare = strip_comments(code)
    hits = [name for name, pat in screens if pat.search(bare)]
    return f"forbidden construct(s): {', '.join(hits)}" if hits else ""


# --------------------------------------------------------------------------------------
# Skeleton (decider mode)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Skeleton:
    """A trusted statement file: one ``sorry`` placeholder, one audit target."""

    text: str
    target: str          # declaration name for the #print axioms audit
    sorry_index: int     # line index of the placeholder


def load_skeleton(path: str) -> Skeleton:
    """Load and structurally validate a decider skeleton.

    Contract (each violation is a hard error at load, before any model runs):
    exactly one line whose entire content is ``sorry``, and a
    ``-- LEAN-ORACLE-TARGET: <name>`` marker naming the declaration to audit.
    Whether the skeleton *compiles* standalone is checked by the doctor / at
    campaign start — that requires the toolchain; this does not.
    """

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines()
    sorry_lines = [i for i, ln in enumerate(lines) if ln.strip() == "sorry"]
    if len(sorry_lines) != 1:
        raise ValueError(
            f"skeleton {path}: need exactly one line whose content is 'sorry' "
            f"(found {len(sorry_lines)})"
        )
    m = _TARGET_MARKER_RE.search(text)
    if not m:
        raise ValueError(
            f"skeleton {path}: missing '-- LEAN-ORACLE-TARGET: <decl-name>' marker "
            "naming the declaration for the axiom audit"
        )
    return Skeleton(text=text, target=m.group(1), sorry_index=sorry_lines[0])


def splice(skeleton: Skeleton, proof: str) -> str:
    """Replace the skeleton's ``sorry`` with the model's parenthesized proof.

    Parenthesizing makes ``by`` blocks legal in term position and keeps the
    proof's indentation relative; residual whitespace errors surface as
    compiler errors and are repaired by the feedback loop, by design.
    """

    lines = skeleton.text.splitlines()
    holder = lines[skeleton.sorry_index]
    indent = holder[: len(holder) - len(holder.lstrip())]
    proof_lines = proof.strip().splitlines()
    if len(proof_lines) == 1:
        lines[skeleton.sorry_index] = f"{indent}({proof_lines[0]})"
    else:
        import textwrap

        body = textwrap.dedent("\n".join(proof_lines[1:]))
        spliced = [f"{indent}({proof_lines[0]}"]
        spliced += [f"{indent}  {ln}" if ln.strip() else ln for ln in body.splitlines()]
        spliced.append(f"{indent})")
        lines[skeleton.sorry_index : skeleton.sorry_index + 1] = spliced
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------------------

def _compile(
    code: str,
    *,
    project_dir: Optional[str],
    timeout_s: float,
    runner: Optional[Callable],
) -> tuple[Optional[bool], str]:
    """Compile a Lean file; ``(ok, full output)``. ``(None, why)`` = couldn't check.

    Same command construction as ``oracle.lean_verify`` but returns the
    UNTRUNCATED output — the decider's axiom audit parses it.
    """

    if runner is None:
        import shutil

        if shutil.which("lean") is None or (project_dir and shutil.which("lake") is None):
            return None, "lean toolchain not installed"
        runner = subprocess.run

    with tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False) as fh:
        fh.write(code)
        path = fh.name
    if project_dir:
        cmd = ["lake", "env", "lean", path]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        cwd = project_dir
    else:
        cmd = ["lean", path]
        env = {"PATH": os.environ.get("PATH", "")}
        cwd = None
    try:
        out = runner(cmd, capture_output=True, text=True, timeout=timeout_s, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, f"lean timed out after {timeout_s}s"
    except Exception as exc:
        return None, f"lean runner failure: {type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    output = (out.stdout or "") + (out.stderr or "")
    return out.returncode == 0, output


_AXIOM_LIST_RE = re.compile(r"'([^']+)' depends on axioms: \[([^\]]*)\]")
_AXIOM_NONE_RE = re.compile(r"'([^']+)' does not depend on any axioms")


def audit_axioms(output: str, target: str) -> tuple[bool, str]:
    """Grade ``#print axioms <target>`` output against the standard-axiom whitelist.

    FAILS CLOSED: if the expected line for ``target`` isn't found, or any axiom
    outside {propext, Classical.choice, Quot.sound} appears, the answer is not
    validated — never "probably fine".
    """

    for m in _AXIOM_NONE_RE.finditer(output):
        if m.group(1) == target:
            return True, "depends on no axioms"
    for m in _AXIOM_LIST_RE.finditer(output):
        if m.group(1) != target:
            continue
        axioms = {a.strip() for a in m.group(2).split(",") if a.strip()}
        rogue = axioms - STANDARD_AXIOMS
        if rogue:
            return False, f"non-standard axioms in proof: {sorted(rogue)}"
        return True, f"axioms: {sorted(axioms)} (standard)"
    return False, f"axiom audit output for '{target}' not found (failing closed)"


# --------------------------------------------------------------------------------------
# The oracles
# --------------------------------------------------------------------------------------

@dataclass
class LeanOracle:
    """A (validator, feedback) pair over one compile memo, plus judge metadata."""

    validator: Callable[[str], "bool | Verdict"]
    feedback: Callable[[str], str]
    note: str
    sufficient: bool
    rung: int = 1


@dataclass
class _Memo:
    """Per-answer cache: validator and feedback both fire on the same answer in
    one step, and a Mathlib compile costs 30-90s. Never pay twice."""

    cap: int = 32
    entries: dict = field(default_factory=dict)
    order: list = field(default_factory=list)

    def get(self, answer: str):
        return self.entries.get(_key(answer))

    def put(self, answer: str, value) -> None:
        k = _key(answer)
        if k not in self.entries:
            self.order.append(k)
            if len(self.order) > self.cap:
                self.entries.pop(self.order.pop(0), None)
        self.entries[k] = value


def _key(answer: str) -> str:
    return hashlib.sha256((answer or "").encode()).hexdigest()


def make_gate_oracle(
    *,
    project_dir: Optional[str] = None,
    timeout_s: float = 300.0,
    runner: Optional[Callable] = None,
    artifact_dir: Optional[str] = None,
) -> LeanOracle:
    memo = _Memo()

    def _persist(block: str, output: str, ok) -> None:
        # Every compiled gate block is an artifact: independent checkers
        # (leanchecker/lean4export/nanoda) re-verify from these files alone.
        if not artifact_dir:
            return
        try:
            os.makedirs(artifact_dir, exist_ok=True)
            stem = os.path.join(artifact_dir, f"gate-{_key(block)[:12]}")
            with open(stem + ".lean", "w", encoding="utf-8") as fh:
                fh.write(block if block.endswith("\n") else block + "\n")
            with open(stem + ".out", "w", encoding="utf-8") as fh:
                fh.write(f"compile ok: {ok}\n\n{output}")
        except OSError:
            pass

    def check(answer: str) -> tuple["bool | Verdict", str]:
        blocks = extract_lean_blocks(answer)
        if not blocks:
            return (
                Verdict(passed=True, note="no ```lean block offered; nothing mechanically checked"),
                "",
            )
        for i, block in enumerate(blocks):
            why = screen(block, _GATE_SCREENS)
            if why:
                return False, f"lean block {i}: {why}"
        failures = []
        for i, block in enumerate(blocks):
            ok, output = _compile(block, project_dir=project_dir, timeout_s=timeout_s, runner=runner)
            if ok is None:
                return (
                    Verdict(passed=True, note=f"lean gate could not run ({output}); nothing mechanically checked"),
                    "",
                )
            _persist(block, output, ok)
            if not ok:
                failures.append(f"lean block {i} failed to compile:\n{output.strip()[:2000]}")
            elif "sorry" in output:
                failures.append(f"lean block {i}: compiled with sorry warnings (holes are not proofs)")
        if failures:
            return False, "\n\n".join(failures)
        return (
            Verdict(
                passed=True,
                note=f"{len(blocks)} lean block(s) compile clean — but statement-problem "
                "correspondence is NOT checked; correctness is yours to score",
            ),
            "",
        )

    def cached(answer: str):
        hit = memo.get(answer)
        if hit is None:
            hit = check(answer)
            memo.put(answer, hit)
        return hit

    return LeanOracle(
        validator=lambda a: cached(a)[0],
        feedback=lambda a: cached(a)[1],
        sufficient=False,
        note=(
            "a rung-1 Lean GATE compiles any ```lean blocks in the answer: rejections are "
            "mechanical (type errors); passes verify type-correctness ONLY — whether the "
            "right statement was proved is yours to score"
        ),
    )


def make_decider_oracle(
    skeleton_path: str,
    *,
    project_dir: Optional[str] = None,
    timeout_s: float = 300.0,
    runner: Optional[Callable] = None,
    artifact_dir: Optional[str] = None,
) -> LeanOracle:
    skeleton = load_skeleton(skeleton_path)
    memo = _Memo()

    def check(answer: str) -> tuple["bool | Verdict", str]:
        blocks = extract_lean_blocks(answer)
        if not blocks:
            return False, "no ```lean proof block found; emit exactly one block containing only the proof"
        proof = blocks[-1]  # the answer's final block is the proof offer
        why = screen(proof, _DECIDER_SCREENS)
        if why:
            return False, f"proof block rejected before compiling — {why}"
        code = splice(skeleton, proof) + f"\n#print axioms {skeleton.target}\n"
        ok, output = _compile(code, project_dir=project_dir, timeout_s=timeout_s, runner=runner)
        _persist(code, output, ok)
        if ok is None:
            return False, f"could not check ({output}); not validated"
        if not ok:
            return False, f"proof failed to compile:\n{output.strip()[:2000]}"
        audited, detail = audit_axioms(output, skeleton.target)
        if not audited:
            return False, f"compiled but failed the axiom audit: {detail}"
        return (
            Verdict(passed=True, note=f"kernel-checked proof of '{skeleton.target}' ({detail})"),
            "",
        )

    def _persist(code: str, output: str, ok) -> None:
        # Every decider attempt is auditable from artifacts alone.
        if not artifact_dir:
            return
        try:
            os.makedirs(artifact_dir, exist_ok=True)
            stem = os.path.join(artifact_dir, f"decider-{_key(code)[:12]}")
            with open(stem + ".lean", "w", encoding="utf-8") as fh:
                fh.write(code)
            with open(stem + ".out", "w", encoding="utf-8") as fh:
                fh.write(f"compile ok: {ok}\n\n{output}")
        except OSError:
            pass

    def cached(answer: str):
        hit = memo.get(answer)
        if hit is None:
            hit = check(answer)
            memo.put(answer, hit)
        return hit

    return LeanOracle(
        validator=lambda a: cached(a)[0],
        feedback=lambda a: cached(a)[1],
        sufficient=True,
        note=(
            f"a rung-1 Lean DECIDER: the pinned statement in {os.path.basename(skeleton_path)} "
            f"is trusted; a pass means the kernel checked a proof of '{skeleton.target}' using "
            "only standard axioms. Anything the skeleton does not state is yours to score"
        ),
    )
