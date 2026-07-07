"""``recurse`` — command-line entrypoint for the recursive-reasoning harness.

Examples::

    recurse "In a 4x4 Latin square ..."          # full run across the tier ladder
    recurse --dry-run "..."                        # print the call schedule, no API
    recurse --no-headroom --json "..."             # disable compression, emit JSON
    recurse --n 4 --steps 2 --threshold 0.85 "..." # tune the recursion
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from headroom_recursion.config import RecurseConfig
from headroom_recursion.ladder import plan_schedule, recurse


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
    p.add_argument("--judge-model", dest="judge_model", help="pin the halt judge to this model")
    p.add_argument("--no-headroom", action="store_true", help="disable Headroom compression")
    p.add_argument("--dry-run", action="store_true", help="print the call schedule and exit")
    p.add_argument("--json", action="store_true", help="emit the full trace as JSON")
    p.add_argument("--base-url", dest="base_url", help="Anthropic base_url (e.g. a headroom proxy)")
    args = p.parse_args(argv)

    cfg = build_config(args)

    if args.dry_run:
        print(plan_schedule(cfg))
        return 0

    problem = args.problem or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
    if not problem:
        p.error("no problem given (pass as an argument or via stdin)")

    # Import the real client lazily so --dry-run and --help need no API key / SDK.
    from headroom_recursion.claude import ClaudeClient

    client = ClaudeClient(base_url=args.base_url)
    trace = recurse(problem, client=client, config=cfg)

    if args.json:
        print(json.dumps(trace.to_dict(), indent=2))
    else:
        print(trace.summary())
    return 0 if trace.halted else 2


if __name__ == "__main__":
    raise SystemExit(main())
