"""Dependency prompting: offer to install what's missing, never hang a script."""

from __future__ import annotations

from headroom_recursion.cli import ensure_dependency


def test_present_module_needs_no_prompt():
    asked = []
    assert ensure_dependency(
        "json", "json", probe=lambda m: True, asker=lambda q: asked.append(q) or "n"
    )
    assert asked == []  # nothing to ask


def test_missing_module_installs_on_yes():
    installed = []

    def probe(m, _state={"n": 0}):
        _state["n"] += 1
        return _state["n"] > 1  # absent on first probe, present after "install"

    ok = ensure_dependency(
        "somepkg", "somepkg>=1", interactive=True,
        probe=probe, asker=lambda q: "y", installer=lambda spec: installed.append(spec) or True,
    )
    assert ok
    assert installed == ["somepkg>=1"]


def test_missing_module_declined_returns_false():
    installed = []
    ok = ensure_dependency(
        "somepkg", "somepkg>=1", interactive=True,
        probe=lambda m: False, asker=lambda q: "n", installer=lambda s: installed.append(s) or True,
    )
    assert not ok
    assert installed == []  # declined -> nothing installed


def test_non_interactive_never_asks_never_installs():
    asked, installed = [], []
    ok = ensure_dependency(
        "somepkg", "somepkg>=1", interactive=False,
        probe=lambda m: False,
        asker=lambda q: asked.append(q) or "y",
        installer=lambda s: installed.append(s) or True,
    )
    assert not ok
    assert asked == [] and installed == []  # scripts/CI must not hang on a prompt


def test_failed_install_returns_false():
    ok = ensure_dependency(
        "somepkg", "somepkg>=1", interactive=True,
        probe=lambda m: False, asker=lambda q: "yes", installer=lambda s: False,
    )
    assert not ok


def test_eof_during_prompt_is_a_decline():
    def eof_asker(q):
        raise EOFError

    ok = ensure_dependency(
        "somepkg", "somepkg>=1", interactive=True, probe=lambda m: False, asker=eof_asker
    )
    assert not ok
