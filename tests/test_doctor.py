"""Doctor readiness checks — injected runners, no network, no real toolchains."""

from __future__ import annotations

import json
from types import SimpleNamespace

from headroom_recursion import doctor


def cli_envelope(result="CANARY", **over):
    body = {"type": "result", "subtype": "success", "is_error": False,
            "result": result, "stop_reason": "end_turn", "total_cost_usd": 0.01}
    body.update(over)
    return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")


def test_model_canary_exact_compliance_is_ok(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    checks = doctor.check_cli_transport(
        ("m1",), probe=True, runner=lambda argv, **kw: cli_envelope()
    )
    by_name = {c.name: c for c in checks}
    assert by_name["model: m1"].level == doctor.OK


def test_model_refusal_is_fail_and_noncompliance_is_warn(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")

    def refusing(argv, **kw):
        return cli_envelope("API error", is_error=True, subtype="error_during_execution")

    checks = doctor.check_cli_transport(("bad",), probe=True, runner=refusing)
    assert {c.name: c.level for c in checks}["model: bad"] == doctor.FAIL

    checks = doctor.check_cli_transport(
        ("chatty",), probe=True, runner=lambda argv, **kw: cli_envelope("Sure! CANARY it is.")
    )
    assert {c.name: c.level for c in checks}["model: chatty"] == doctor.WARN


def test_missing_cli_is_fail(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    checks = doctor.check_cli_transport(("m",), probe=True)
    assert checks[0].level == doctor.FAIL


def test_lean_levels(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert doctor.lean_level()[0] == "none"

    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name if name == "lean" else None)
    ok_run = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr="")
    assert doctor.lean_level(runner=ok_run)[0] == "core-lean"  # no lake -> no project mode

    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    proj = tmp_path / "lean"
    proj.mkdir()
    assert doctor.lean_level(project_dir=str(proj), runner=ok_run)[0] == "mathlib"

    def mathlib_broken(cmd, **kw):
        code = 1 if cmd[0] == "lake" else 0
        return SimpleNamespace(returncode=code, stdout="", stderr="unknown package 'Mathlib'")

    level, detail = doctor.lean_level(project_dir=str(proj), runner=mathlib_broken)
    assert level == "core-lean" and "Mathlib" in detail


def test_paths_and_stub_loop(tmp_path):
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("# comment\nCook (1971). NP-completeness.\n")
    checks = doctor.check_paths(corpus=str(corpus), writable=(str(tmp_path / "runs"),))
    assert all(c.level == doctor.OK for c in checks)
    assert "1 entries" in checks[0].detail

    checks = doctor.check_paths(corpus=str(tmp_path / "missing.txt"))
    assert checks[0].level == doctor.FAIL

    (check,) = doctor.check_stub_loop()
    assert check.level == doctor.OK


def test_run_doctor_exit_code_and_render(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    checks, code = doctor.run_doctor(
        models=("m1",),
        probe_models=True,
        lean_project=None,
        writable=(str(tmp_path / "runs"),),
        cli_runner=lambda argv, **kw: cli_envelope(),
        lean_runner=lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert code == 0
    text = doctor.render(checks)
    assert "[ OK ]" in text and "model: m1" in text

    checks, code = doctor.run_doctor(
        models=("m1",), probe_models=True, lean_project=None,
        writable=(str(tmp_path / "runs"),),
        cli_runner=lambda argv, **kw: cli_envelope("nope", is_error=True),
        lean_runner=lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert code == 1
