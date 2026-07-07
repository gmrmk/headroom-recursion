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
    p.add_argument("--max-calls", dest="max_calls", type=int, help="hard cap on total Claude calls; stops with the best answer, never escalates past it")
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
    p.add_argument("--dry-run", action="store_true", help="print the call schedule and exit")
    p.add_argument("--json", action="store_true", help="emit the full trace as JSON")
    p.add_argument("--base-url", dest="base_url", help="Anthropic base_url (e.g. a headroom proxy)")
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

    # Check the run's dependencies up front, offering to install missing ones
    # (interactive sessions only; scripts get a copy-pasteable error instead).
    if not ensure_dependency("anthropic", "anthropic>=0.40"):
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
    from headroom_recursion.claude import ClaudeClient

    client = ClaudeClient(
        base_url=args.base_url,
        timeout=args.timeout,
        max_retries=args.max_retries,
        headroom_min_tokens=args.headroom_min_tokens,
    )

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

    emit(trace)
    if trace.stop_reason == "interrupted":
        return 130
    return 0 if trace.halted else 2


if __name__ == "__main__":
    raise SystemExit(main())
