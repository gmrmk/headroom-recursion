"""The Oracle Compiler — synthesize the strongest verifier a problem admits.

Instead of hand-writing a validator per problem, step zero of a run can compile
one: a model is asked to CLASSIFY the problem's verifiability and SYNTHESIZE a
mechanical checker, which is then CALIBRATED against planted cases in a sandbox
and INSTALLED as ``RecurseConfig.validator`` only if it discriminates correctly.

The verification ladder (trust required, ascending):

    rung 1  formal proof     (Lean/SMT — future backend)
    rung 2  execution        (run/test/simulate the candidate answer)
    rung 3  constraints      (parse the answer, check structure mechanically)
    rung 4  ground truth     (retrieval/lookup — routed via the Retriever seam)
    rung 5  judged opinion   (no validator; the judge keeps full authority)

Non-negotiable rules, each one earned by a live-run failure mode:
* **Calibration is the gate.** A validator that misses ANY planted case — or that
  arrives with too few cases to test — is demoted to rung 5. An uncalibrated
  validator is opinion with a Python accent.
* **Pre-registration.** The oracle is compiled before any solution attempt and
  frozen; the generator never sees the validator source, only pass/fail.
* **Residuals are first-class.** Whatever the validator cannot check is listed,
  shown to the judge, printed in the trace, and never reported as verified.
* The sandbox (``subprocess`` + isolated interpreter + stripped env + timeout)
  is best-effort *accident* isolation, not a security boundary against a
  malicious model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from typing import Callable, Optional

RUNG_PROOF = 1
RUNG_EXECUTION = 2
RUNG_CONSTRAINTS = 3
RUNG_LOOKUP = 4
RUNG_JUDGE = 5

MIN_GOOD_CASES = 2
MIN_BAD_CASES = 3


@dataclass
class CalibrationCase:
    answer: str
    should_pass: bool
    note: str = ""


@dataclass
class CalibrationReport:
    """What happened when the candidate validator met the planted cases."""

    passed: bool
    checks: list[str] = field(default_factory=list)  # human-readable, one per case
    error: str = ""


@dataclass
class CompiledOracle:
    """The product: a calibrated validator, or an honest rung-5 nothing."""

    validator: Optional[Callable[[str], bool]]
    rung: int
    residuals: list[str]
    calibration: Optional[CalibrationReport]
    source: str = ""  # recorded for audit; NEVER shown to the generator
    note: str = ""    # one line for the judge/trace ("oracle checks X; you score Y")
    # Halt authority: True = a pass DECIDES correctness (validated halt); False =
    # the validator is a GATE — fails are final, passes defer to the judge. The
    # compiler must claim sufficiency explicitly; absence is insufficiency.
    sufficient: bool = False


# --------------------------------------------------------------------------------------
# Synthesis prompt
# --------------------------------------------------------------------------------------

SYNTH_SYSTEM = (
    "You are an oracle compiler. Given a problem, you design the strongest MECHANICAL "
    "verifier it admits — a pure Python function that checks a candidate answer — and "
    "you are ruthlessly honest about what it cannot check. You never inflate the rung. "
    "Respond with ONLY a JSON object, no prose around it."
)

SYNTH_USER = """\
PROBLEM:
{problem}

Design a verifier for candidate answers to this problem. Reply with ONLY this JSON:

{{
  "rung": 2 | 3 | 5,
  "rationale": "<one sentence>",
  "sufficient": true | false,
  "validator_source": "<python source or null>",
  "residuals": ["<claim the validator CANNOT check>", ...],
  "calibration_cases": [
    {{"answer": "<candidate answer text>", "should_pass": true|false, "note": "<why>"}},
    ...
  ]
}}

"sufficient" means: does a validator PASS fully establish the answer is CORRECT —
not merely well-formed? true only when the checked object IS the answer (e.g. an
equation whose arithmetic is verified). If the validator checks structure/format
while correctness lives in unchecked prose, say false: the validator will then act
as a GATE (rejections are final; passes defer to the judge) instead of a decider.
Be conservative — when in doubt, false.

Rules for validator_source:
- Define exactly `def validate(answer: str) -> bool`. Pure function of its input.
- Python standard library only. No network, no file writes, no subprocess, no imports
  beyond the stdlib. It must terminate in well under 10 seconds.
- rung 2 = the validator EXECUTES something derived from the answer (evaluates the
  proposed expression, simulates, tests). rung 3 = it checks structure/constraints
  mechanically. If nothing meaningful is mechanically checkable, use rung 5 with
  "validator_source": null and put everything in residuals.
- Parse defensively: answers arrive as free text; extract the checkable part
  (a final line, a code block, a grid) the way the problem's answer format implies.

Rules for calibration_cases (MANDATORY when validator_source is not null):
- At least {min_good} cases with should_pass=true and {min_bad} with should_pass=false.
- At least one false case must be PLAUSIBLE-BUT-WRONG (close to correct, subtly off),
  not just garbage.

Rules for residuals: list every part of a correct answer your validator does not
verify (novelty, optimality, semantics beyond structure, ...). An empty list is only
acceptable if the validator fully decides correctness."""


# --------------------------------------------------------------------------------------
# Sandbox
# --------------------------------------------------------------------------------------

_HARNESS = textwrap.dedent(
    """

    if __name__ == "__main__":
        import sys as _sys
        _ans = _sys.stdin.read()
        try:
            _ok = bool(validate(_ans))
        except Exception as _e:  # a crashing validator must not look like a verdict
            print("validator raised: %r" % (_e,), file=_sys.stderr)
            _sys.exit(3)
        _sys.exit(0 if _ok else 1)
    """
)


def run_validator(
    source: str,
    answer: str,
    *,
    timeout_s: float = 10.0,
    runner: Optional[Callable] = None,
) -> tuple[Optional[bool], str]:
    """Run validator ``source`` against ``answer`` in a subprocess sandbox.

    Returns ``(verdict, error)``: verdict True/False on a clean run, or
    ``(None, why)`` when the validator crashed, timed out, or misbehaved.
    """

    runner = runner or subprocess.run
    program = source + _HARNESS
    with tempfile.NamedTemporaryFile("w", suffix="_oracle.py", delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        # -I: isolated mode (no site, no env-var injection into sys.path).
        # Stripped env: no proxy variables, no credentials — accident isolation.
        out = runner(
            [sys.executable, "-I", path],
            input=answer,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except subprocess.TimeoutExpired:
        return None, f"validator timed out after {timeout_s}s"
    except Exception as exc:
        return None, f"sandbox failure: {type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if out.returncode == 0:
        return True, ""
    if out.returncode == 1:
        return False, ""
    return None, (out.stderr or f"validator exited {out.returncode}").strip()[:200]


def calibrate(
    source: str,
    cases: list[CalibrationCase],
    *,
    timeout_s: float = 10.0,
    runner: Optional[Callable] = None,
) -> CalibrationReport:
    """The gate: the validator must discriminate every planted case, or it has no authority."""

    goods = sum(1 for c in cases if c.should_pass)
    bads = sum(1 for c in cases if not c.should_pass)
    if goods < MIN_GOOD_CASES or bads < MIN_BAD_CASES:
        return CalibrationReport(
            passed=False,
            error=f"insufficient calibration cases (need >={MIN_GOOD_CASES} good/"
            f">={MIN_BAD_CASES} bad, got {goods}/{bads})",
        )

    checks: list[str] = []
    passed = True
    for i, case in enumerate(cases):
        verdict, err = run_validator(source, case.answer, timeout_s=timeout_s, runner=runner)
        ok = verdict is not None and verdict == case.should_pass
        passed = passed and ok
        expected = "pass" if case.should_pass else "fail"
        got = "error: " + err if verdict is None else ("pass" if verdict else "fail")
        checks.append(f"case {i} ({case.note or 'unnamed'}): expected {expected}, got {got}")
    return CalibrationReport(passed=passed, checks=checks)


# --------------------------------------------------------------------------------------
# The compiler
# --------------------------------------------------------------------------------------

def compile_oracle(
    problem: str,
    *,
    client,
    model: str,
    max_tokens: int = 4096,
    timeout_s: float = 10.0,
    use_headroom: bool = False,
    runner: Optional[Callable] = None,
) -> CompiledOracle:
    """CLASSIFY + SYNTHESIZE (one model call), CALIBRATE (sandbox), INSTALL (or demote).

    Never raises on a bad compilation — every failure path returns an honest
    rung-5 oracle (validator=None) so the judge keeps full authority.
    """

    res = client.complete(
        model=model,
        system=SYNTH_SYSTEM,
        user=SYNTH_USER.format(problem=problem, min_good=MIN_GOOD_CASES, min_bad=MIN_BAD_CASES),
        max_tokens=max_tokens,
        temperature=0.0,
        use_headroom=use_headroom,
    )

    envelope = _extract_json(res.text)
    if not isinstance(envelope, dict):
        return _demoted("compiler returned no parseable JSON envelope")

    residuals = [str(r) for r in envelope.get("residuals") or []]
    source = envelope.get("validator_source")
    rung = envelope.get("rung")

    if not source or not isinstance(source, str):
        return CompiledOracle(
            validator=None,
            rung=RUNG_JUDGE,
            residuals=residuals or ["entire answer (compiler judged nothing mechanically checkable)"],
            calibration=None,
            note="no mechanical oracle; judge has full authority",
        )
    if rung not in (RUNG_EXECUTION, RUNG_CONSTRAINTS):
        return _demoted(f"compiler claimed unsupported rung {rung!r}", residuals)
    if "def validate" not in source:
        return _demoted("validator_source does not define validate()", residuals)

    cases = [
        CalibrationCase(
            answer=str(c.get("answer", "")),
            should_pass=bool(c.get("should_pass")),
            note=str(c.get("note", "")),
        )
        for c in (envelope.get("calibration_cases") or [])
        if isinstance(c, dict)
    ]
    report = calibrate(source, cases, timeout_s=timeout_s, runner=runner)
    if not report.passed:
        # The single most important line in this module: a validator that cannot
        # discriminate planted cases gets NO authority, ever.
        demoted = _demoted("calibration failed", residuals)
        demoted.calibration = report
        return demoted

    def validator(answer: str) -> bool:
        verdict, _err = run_validator(source, answer, timeout_s=timeout_s, runner=runner)
        return verdict is True  # crash/timeout at runtime = "don't halt", never "pass"

    sufficient = envelope.get("sufficient") is True  # unclaimed = insufficient
    checkable = envelope.get("rationale", "").strip() or "mechanical answer check"
    authority = "DECIDES correctness" if sufficient else "GATE only (format/constraints; correctness is yours to score)"
    note = (
        f"a calibrated rung-{rung} oracle ({authority}) verifies: {checkable} | "
        f"NOT verified (score these yourself): {'; '.join(residuals) or 'nothing — oracle is total'}"
    )
    return CompiledOracle(
        validator=validator,
        rung=int(rung),
        residuals=residuals,
        calibration=report,
        source=source,
        note=note,
        sufficient=sufficient,
    )


def _demoted(reason: str, residuals: Optional[list[str]] = None) -> CompiledOracle:
    return CompiledOracle(
        validator=None,
        rung=RUNG_JUDGE,
        residuals=(residuals or []) + [f"oracle demoted: {reason}"],
        calibration=None,
        note=f"oracle demoted to judge-only: {reason}",
    )


def _extract_json(text: str):
    """First balanced JSON object in ``text`` (models love to wrap JSON in prose)."""

    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


# --------------------------------------------------------------------------------------
# Rung 1: formal proof backend (Lean 4)
# --------------------------------------------------------------------------------------

def lean_available() -> bool:
    import shutil

    return shutil.which("lean") is not None


def lean_verify(
    code: str,
    *,
    timeout_s: float = 120.0,
    project_dir: Optional[str] = None,
    runner: Optional[Callable] = None,
) -> tuple[bool, str]:
    """Check a self-contained Lean 4 file: compile == verified at rung 1.

    The only verdict in this codebase that requires zero trust in any model.
    Returns ``(ok, detail)``; ``ok`` is True only on a clean compile. When Lean
    is not installed (and no test runner is injected) the answer is an honest
    ``(False, "lean not installed")`` — absence of the checker never upgrades a
    claim. Mathematical claims should only ever score above the judged ceiling
    when they carry a formalization that passes here.

    ``project_dir``: a Lake project (e.g. one depending on Mathlib). When set,
    the file is checked with ``lake env lean`` from that directory so its
    imports (``import Mathlib``) resolve. Without it, only core Lean 4 is
    available. Import-heavy files load slowly — raise ``timeout_s`` accordingly.
    """

    runner = runner or (subprocess.run if lean_available() else None)
    if runner is None:
        return False, "lean not installed"

    # Two rung-1 false positives, rejected before compiling: `sorry` (Lean treats
    # proof holes as a WARNING — exit 0) and custom `axiom`s (an arbitrary axiom
    # "proves" anything, silently). A proof carrying either is not a proof.
    import re as _re

    if _re.search(r"\b(sorry|admit)\b", code):
        return False, "rejected: proof contains sorry/admit (holes are not proofs)"
    if _re.search(r"^\s*axiom\b", code, _re.MULTILINE):
        return False, "rejected: proof declares a custom axiom"

    with tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False) as fh:
        fh.write(code)
        path = fh.name
    if project_dir:
        cmd = ["lake", "env", "lean", path]
        # lake/elan need HOME to locate the toolchain and the olean cache.
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        cwd = project_dir
    else:
        cmd = ["lean", path]
        env = {"PATH": os.environ.get("PATH", "")}
        cwd = None
    try:
        out = runner(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return False, f"lean timed out after {timeout_s}s"
    except Exception as exc:
        return False, f"lean runner failure: {type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if out.returncode == 0:
        chatter = (out.stdout or "") + (out.stderr or "")
        if "sorry" in chatter:
            return False, "rejected: compiled with sorry warnings (holes are not proofs)"
        return True, "compiled clean (rung 1)"
    return False, (out.stderr or out.stdout or f"lean exited {out.returncode}").strip()[:500]


# --------------------------------------------------------------------------------------
# Rung-2 answer-extraction presets (competition math, closed-form answers)
# --------------------------------------------------------------------------------------

def extract_final_integer(text: str) -> Optional[int]:
    """Pull the answer integer from a solution, most-explicit signal first.

    Order: \\boxed{...}, then "answer is/= N" / "FINAL: N", then the last integer
    in the text. Returns None if nothing integer-like is present. Tolerates commas
    and surrounding non-digits; competition answers are integers (often mod 1000).
    """

    import re as _re

    def _int(s: str) -> Optional[int]:
        s = s.replace(",", "").strip()
        m = _re.search(r"-?\d+", s)
        return int(m.group()) if m else None

    boxed = _re.findall(r"\\boxed\{([^}]*)\}", text)
    if boxed:
        v = _int(boxed[-1])
        if v is not None:
            return v
    labelled = _re.findall(r"(?:final answer|answer)\s*(?:is|:|=)?\s*(-?[\d,]+)", text, _re.IGNORECASE)
    if labelled:
        v = _int(labelled[-1])
        if v is not None:
            return v
    ints = _re.findall(r"-?\d[\d,]*", text)
    return _int(ints[-1]) if ints else None


def integer_answer_validator(expected: int, *, modulus: Optional[int] = None) -> Callable[[str], bool]:
    """A SUFFICIENT rung-2 validator: the extracted answer equals ``expected``.

    ``modulus`` (e.g. 1000 for AIMO/AIME) compares residues. Use for benchmarking
    against known answers, or as an oracle when the answer is independently
    checkable. Extraction failure = not validated (never a false pass).
    """

    def validate(answer: str) -> bool:
        got = extract_final_integer(answer)
        if got is None:
            return False
        if modulus:
            return got % modulus == expected % modulus
        return got == expected

    return validate
