"""``recurse`` — command-line entrypoint for the recursive-reasoning harness.

Examples::

    recurse "In a 4x4 Latin square ..."          # full run across the tier ladder
    recurse --dry-run "..."                        # print the call schedule, no API
    recurse --no-headroom --json "..."             # disable compression, emit JSON
    recurse --n 4 --steps 2 --threshold 0.85 "..." # tune the recursion
    recurse --max-calls 40 --max-seconds 300 "..." # hard budgets (stop, keep best)

Exit codes: 0 = confident halt; 2 = finished without a confident halt (best answer
printed anyway); 1 = run died mid-flight (partial trace printed to stderr);
130 = interrupted (partial trace printed).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from typing import Callable, Optional

from headroom_recursion.config import RecurseConfig
from headroom_recursion.ladder import RunError, plan_schedule, recurse


def _module_present(module: str) -> bool:
    importlib.invalidate_caches()  # a just-completed pip install must be visible
    return importlib.util.find_spec(module) is not None


def ensure_dependency(
    module: str,
    pip_spec: str,
    *,
    interactive: Optional[bool] = None,
    probe: Callable[[str], bool] = _module_present,
    asker: Callable[[str], str] = input,
    installer: Optional[Callable[[str], bool]] = None,
) -> bool:
    """Return True if ``module`` is importable, offering to install it when missing.

    In an interactive session the user is asked before anything is installed; in a
    non-interactive one (scripts, CI) nothing is installed and False is returned so
    the caller can fail with a copy-pasteable command instead of hanging on a prompt.
    ``probe``/``asker``/``installer`` are injectable for tests.
    """

    if probe(module):
        return True
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        return False

    try:
        reply = asker(f"'{module}' is not installed. Install {pip_spec} now? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    if reply.strip().lower() not in {"y", "yes"}:
        return False

    if installer is None:
        def installer(spec: str) -> bool:
            return subprocess.run([sys.executable, "-m", "pip", "install", spec]).returncode == 0

    if not installer(pip_spec):
        return False
    return probe(module)


def build_config(args) -> RecurseConfig:
    cfg = RecurseConfig()
    if getattr(args, "ladder", None):
        from headroom_recursion.config import Tier

        cfg.ladder = tuple(Tier(m.strip()) for m in args.ladder.split(",") if m.strip())
    if args.n is not None:
        cfg.n = args.n
    if args.steps is not None:
        cfg.T = args.steps
    if args.threshold is not None:
        cfg.halt_threshold = args.threshold
    if args.temperature is not None:
        cfg.temperature = args.temperature
    if args.judge_model is not None:
        cfg.judge_model = args.judge_model
    if args.judge_votes is not None:
        cfg.judge_votes = args.judge_votes
    if args.retrieval_k is not None:
        cfg.retrieval_k = args.retrieval_k
    if args.retrieval_max_chars is not None:
        cfg.retrieval_max_chars = args.retrieval_max_chars
    if args.max_calls is not None:
        cfg.max_total_calls = args.max_calls
    if args.max_seconds is not None:
        cfg.max_wall_seconds = args.max_seconds
    if getattr(args, "auto_oracle", False):
        cfg.oracle_auto = True
    if getattr(args, "oracle_model", None):
        cfg.oracle_model = args.oracle_model
    if getattr(args, "claim_audit", False):
        cfg.claim_audit = True
    if getattr(args, "research", False):
        if not getattr(args, "ladder", None):
            from headroom_recursion.config import RESEARCH_LADDER

            cfg.ladder = RESEARCH_LADDER
        if getattr(args, "lightrag", None) or getattr(args, "corpus", None):
            cfg.claim_audit = True
    cfg.use_headroom = not args.no_headroom
    return cfg


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="recurse",
        description="TRM-style recursive reasoning for Claude with Headroom compression.",
    )
    p.add_argument("problem", nargs="?", help="the task/problem to solve (or read from stdin)")
    p.add_argument("--n", type=int, help="latent updates per improvement step (default 6)")
    p.add_argument("--steps", type=int, help="improvement steps per tier (default 3)")
    p.add_argument("--threshold", type=float, help="halt_prob threshold to stop (default 0.9)")
    p.add_argument("--temperature", type=float, help="sampling temperature (default 0.7)")
    p.add_argument("--judge-model", dest="judge_model", help="pin the halt judge to this model (reduces self-preference)")
    p.add_argument("--judge-votes", dest="judge_votes", type=int, help="judge calls per step; the median wins (default 1)")
    p.add_argument("--max-calls", dest="max_calls", type=int, help="hard cap on total model calls; stops with the best answer, never escalates past it")
    p.add_argument("--max-seconds", dest="max_seconds", type=float, help="hard wall-clock cap for the run")
    p.add_argument("--no-headroom", action="store_true", help="disable Headroom compression")
    p.add_argument("--headroom-min-tokens", dest="headroom_min_tokens", type=int, default=0, help="skip compression for prompts under this size (est. tokens)")
    p.add_argument("--timeout", type=float, help="per-request timeout in seconds (SDK default otherwise)")
    p.add_argument("--max-retries", dest="max_retries", type=int, help="SDK retry count per request (SDK default otherwise)")
    p.add_argument("--lightrag", metavar="DIR", help="enable LightRAG retrieval from this working dir")
    p.add_argument("--lightrag-mode", default="mix", help="LightRAG query mode (default: mix)")
    p.add_argument("--index", action="append", metavar="FILE", help="index a text file into LightRAG before running (repeatable; runs on every invocation — LightRAG dedupes identical content)")
    p.add_argument("--retrieval-k", type=int, help="snippets to retrieve per step (default 4)")
    p.add_argument("--retrieval-max-chars", dest="retrieval_max_chars", type=int, help="cap on injected knowledge per step (default 8000 chars)")
    p.add_argument("--lean-gate", dest="lean_gate", action="store_true", help="rung-1 gate: any ```lean blocks in answers must compile; failures are mechanical rejections with compiler errors fed back")
    p.add_argument("--lean-statement", dest="lean_statement", metavar="FILE", help="rung-1 DECIDER: trusted skeleton pinning the theorem statement (one 'sorry' line + LEAN-ORACLE-TARGET marker); a kernel-checked, axiom-audited proof halts as validated")
    p.add_argument("--lean-project", dest="lean_project", metavar="DIR", help="Lake project for lean compiles (default: ./lean when present); enables Mathlib imports")
    p.add_argument("--lean-timeout", dest="lean_timeout", type=float, default=300.0, help="seconds per lean compile (default 300; first Mathlib import is slow)")
    p.add_argument("--auto-oracle", dest="auto_oracle", action="store_true", help="compile + calibrate a mechanical verifier for the problem before solving (oracle compiler)")
    p.add_argument("--oracle-model", dest="oracle_model", help="model that compiles the oracle (default: strongest ladder model)")
    p.add_argument("--claim-audit", dest="claim_audit", action="store_true", help="audit [KNOWN]/[NEW] claims against the retrieval corpus (needs --lightrag or --corpus)")
    p.add_argument("--ledger", metavar="PATH", help="run ledger: seed from prior verified results, record this run's outcome")
    p.add_argument("--corpus", metavar="FILE", help="curated corpus (one entry per line) for CorpusRetriever — rung-4 lookups without LightRAG")
    p.add_argument("--research", action="store_true", help="research mode: wrap the problem in the proven graded-rubric template, default the ladder to Sonnet+, auto-enable --claim-audit when a corpus/retriever is configured")
    p.add_argument("--client", choices=("claude", "openai"), default="claude", help="model backend; 'openai' also covers OpenAI-compatible servers (Ollama, vLLM, ...) via --base-url")
    p.add_argument("--ladder", help="comma-separated model ladder, cheapest first (default: the Claude tiers)")
    p.add_argument("--dry-run", action="store_true", help="print the call schedule and exit")
    p.add_argument("--json", action="store_true", help="emit the full trace as JSON")
    p.add_argument("--base-url", dest="base_url", help="API base_url (a headroom proxy, an OpenAI-compatible server, ...)")
    args = p.parse_args(argv)

    cfg = build_config(args)
    try:
        cfg.validate()
    except ValueError as exc:
        p.error(str(exc))

    if args.dry_run:
        print(plan_schedule(cfg))
        return 0

    problem = args.problem or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
    if not problem:
        p.error("no problem given (pass as an argument or via stdin)")
    if args.research:
        from headroom_recursion.prompts import research_prompt

        problem = research_prompt(problem)

    # Check the run's dependencies up front, offering to install missing ones
    # (interactive sessions only; scripts get a copy-pasteable error instead).
    if args.client == "openai":
        if not ensure_dependency("openai", "openai>=1"):
            p.error("--client openai requires the OpenAI SDK: python -m pip install 'openai>=1'")
    elif not ensure_dependency("anthropic", "anthropic>=0.40"):
        p.error("the Anthropic SDK is required: python -m pip install 'anthropic>=0.40'")
    if cfg.use_headroom and not ensure_dependency("headroom", "headroom-ai[all]"):
        print(
            "note: headroom-ai is not installed — running uncompressed "
            "(pass --no-headroom to silence this note)",
            file=sys.stderr,
        )
    if args.lightrag and not ensure_dependency("lightrag", "lightrag-hku"):
        p.error("--lightrag requires LightRAG: python -m pip install lightrag-hku")

    # Import the real client lazily so --dry-run and --help need no API key / SDK.
    client_kwargs = dict(
        base_url=args.base_url,
        timeout=args.timeout,
        max_retries=args.max_retries,
        headroom_min_tokens=args.headroom_min_tokens,
    )
    if args.client == "openai":
        from headroom_recursion.clients import OpenAIClient

        client = OpenAIClient(**client_kwargs)
    else:
        from headroom_recursion.claude import ClaudeClient

        client = ClaudeClient(**client_kwargs)

    # Optional curated-corpus retrieval (rung 4 without LightRAG).
    if args.corpus:
        from headroom_recursion.retrieval import CorpusRetriever

        try:
            cfg.retriever = CorpusRetriever.from_file(args.corpus)
        except OSError as exc:
            p.error(f"--corpus {args.corpus}: {exc}")

    # Optional LightRAG retrieval layer.
    if args.lightrag:
        from headroom_recursion.retrieval import LightRAGRetriever

        retriever = LightRAGRetriever(args.lightrag, client=client, mode=args.lightrag_mode)
        for path in args.index or []:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    retriever.index(fh.read())
            except OSError as exc:
                p.error(f"--index {path}: {exc}")
        cfg.retriever = retriever

    # Rung-1 Lean oracle: a decider (pinned statement) or a gate (compile-only).
    if getattr(args, "lean_statement", None) or getattr(args, "lean_gate", False):
        import os as _os

        from headroom_recursion import lean_oracle

        project = args.lean_project or ("lean" if _os.path.isdir("lean") else None)
        try:
            if args.lean_statement:
                oracle_obj = lean_oracle.make_decider_oracle(
                    args.lean_statement, project_dir=project, timeout_s=args.lean_timeout
                )
            else:
                oracle_obj = lean_oracle.make_gate_oracle(
                    project_dir=project, timeout_s=args.lean_timeout
                )
        except (OSError, ValueError) as exc:
            p.error(f"lean oracle: {exc}")
        cfg.validator = oracle_obj.validator
        cfg.feedback = oracle_obj.feedback
        cfg.oracle_sufficient = oracle_obj.sufficient
        cfg.oracle_note = oracle_obj.note
        cfg.oracle_rung = oracle_obj.rung

    # Run ledger: start from settled ground, never re-derive it.
    if args.ledger:
        from headroom_recursion import ledger as ledger_mod

        seed = ledger_mod.seed_for(args.ledger, problem)
        if seed:
            cfg.seed_scratchpad = seed
            print(f"note: seeded from ledger ({args.ledger})", file=sys.stderr)

    def emit(trace, *, to_stderr: bool = False) -> None:
        out = json.dumps(trace.to_dict(), indent=2) if args.json else trace.summary()
        print(out, file=sys.stderr if to_stderr else sys.stdout)

    try:
        trace = recurse(problem, client=client, config=cfg)
    except RunError as exc:
        # The run died, but everything completed before the error is in the trace.
        emit(exc.trace, to_stderr=True)
        print(f"recurse: run failed: {exc}", file=sys.stderr)
        return 1

    if args.ledger:
        ledger_mod.record(args.ledger, problem, trace)

    emit(trace)
    if trace.stop_reason == "interrupted":
        return 130
    return 0 if trace.halted else 2


if __name__ == "__main__":
    raise SystemExit(main())
