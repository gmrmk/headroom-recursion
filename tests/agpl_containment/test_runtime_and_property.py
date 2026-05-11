"""R-8 phase6 (Camille + Yuki + Sora convergence): defense-in-depth runtime
+ property-test layers atop the AST + regex static lint.

Two surfaces tested here:

1. **Runtime sys.modules assertion** (osint_goblin_schemas.agpl_runtime_check):
   Walks sys.modules at app startup and raises AGPLContaminationError if any
   AGPL_FORBIDDEN top-level package name is loaded in the host process. Lives
   in osint_goblin_schemas so apps/api and apps/workers can both call it
   cheaply.

2. **Property test over the live module DAG** (Hypothesis):
   For every project module that imports, assert its transitive-import
   closure (as seen by sys.modules after a clean import) contains no
   AGPL_FORBIDDEN name. This catches the failure mode where a future
   transitive dep silently pulls in an AGPL package.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st
from osint_goblin_schemas.agpl_runtime_check import (
    AGPL_FORBIDDEN,
    AGPLContaminationError,
    assert_no_agpl_loaded,
    loaded_agpl_modules,
)


def test_clean_process_has_no_agpl_loaded() -> None:
    """The test process itself has never imported an AGPL module."""
    assert loaded_agpl_modules() == set()


def test_assert_no_agpl_loaded_is_clean() -> None:
    """Calling the startup assertion on a clean process is a no-op."""
    assert_no_agpl_loaded()  # does not raise


def test_assert_no_agpl_loaded_raises_when_present() -> None:
    """Inject an AGPL module name into sys.modules; assertion must fire."""
    fake_name = "ghunt"
    assert fake_name in AGPL_FORBIDDEN, "test fixture relies on ghunt being forbidden"
    original = sys.modules.get(fake_name)
    try:
        sys.modules[fake_name] = object()  # type: ignore[assignment]
        with pytest.raises(AGPLContaminationError) as exc:
            assert_no_agpl_loaded()
        assert "ghunt" in str(exc.value)
        assert "agpl_import_lint.py" in str(exc.value)  # remediation pointer
    finally:
        if original is None:
            sys.modules.pop(fake_name, None)
        else:
            sys.modules[fake_name] = original


def test_assert_no_agpl_loaded_names_all_leaked() -> None:
    """Multi-module leakage names all offenders, not just one."""
    leaked = ["ghunt", "bbot"]
    originals = {n: sys.modules.get(n) for n in leaked}
    try:
        for n in leaked:
            sys.modules[n] = object()  # type: ignore[assignment]
        with pytest.raises(AGPLContaminationError) as exc:
            assert_no_agpl_loaded()
        for n in leaked:
            assert n in str(exc.value)
    finally:
        for n, orig in originals.items():
            if orig is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = orig


def test_dotted_submodule_matches_top_level() -> None:
    """`ghunt.cli` in sys.modules counts as `ghunt` loaded."""
    fake = "ghunt.cli"
    original_parent = sys.modules.get("ghunt")
    original_child = sys.modules.get(fake)
    try:
        sys.modules[fake] = object()  # type: ignore[assignment]
        leaked = loaded_agpl_modules()
        assert "ghunt" in leaked
    finally:
        if original_child is None:
            sys.modules.pop(fake, None)
        else:
            sys.modules[fake] = original_child
        if original_parent is None:
            sys.modules.pop("ghunt", None)
        else:
            sys.modules["ghunt"] = original_parent


# ---------------------------------------------------------------------------
# Property test over the live project module DAG (Yuki Q5 phase6).
# ---------------------------------------------------------------------------

# The project modules we ship -- these MUST be import-clean.
PROJECT_MODULES: list[str] = [
    "osint_goblin_schemas",
    "osint_goblin_schemas.agpl_runtime_check",
    "osint_goblin_forensics",
    "osint_goblin_forensics.chain",
    "osint_goblin_forensics.signing",
    "osint_goblin_forensics.timestamping",
    "osint_goblin_forensics.verify",
    # apps/api + apps/workers main modules: we DELIBERATELY don't include
    # them here because their __init__ runs assert_no_agpl_loaded() which
    # would already raise -- this test asserts pre-import cleanliness.
]


@given(module_name=st.sampled_from(PROJECT_MODULES))
def test_project_module_imports_no_agpl(module_name: str) -> None:
    """For every project module, its transitive import closure (as seen via
    sys.modules after importlib.import_module) is disjoint from
    AGPL_FORBIDDEN.

    Hypothesis-driven because future contributors will add modules to
    PROJECT_MODULES and the property must hold across the whole set.
    """
    importlib.import_module(module_name)
    leaked = loaded_agpl_modules()
    assert leaked == set(), (
        f"importing {module_name!r} loaded forbidden AGPL modules: " f"{sorted(leaked)}"
    )


def test_property_test_module_list_is_not_empty() -> None:
    """Smoke check that the PROJECT_MODULES list survives refactoring.
    If the list is ever emptied accidentally, the @given test above
    silently provides no coverage."""
    assert len(PROJECT_MODULES) >= 5
