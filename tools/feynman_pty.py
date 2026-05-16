"""Headless wrapper around the Feynman CLI for agent use (v1).

Feynman 0.2.52's `--prompt` mode hangs when stdin is not a TTY (Pi runtime
waits indefinitely on interactive-prompt libraries — see upstream issue at
osint-goblin/docs/feynman-headless-issue-draft.md). This wrapper spawns
Feynman inside a real Windows ConPTY via pywinpty, sends the prompt as if
typed at the REPL, waits for the response to settle, then exits.

USAGE — basic prompt (short responses; capture from terminal):
    python feynman_pty.py "your research prompt here"

USAGE — prompt from file (multi-line, complex prompts):
    python feynman_pty.py --prompt-file path\\to\\prompt.txt

USAGE — long research workflows (RECOMMENDED for /deepresearch, /lit, etc.):

    Because ConPTY redraws can scroll text out of the capture window, the
    reliable idiom for LONG outputs is to ask Feynman itself to save the
    result to a file, then read it from disk:

        prompt = "/deepresearch <topic>. Save the final brief to
                  C:/path/to/output.md when done."
        python feynman_pty.py --prompt-file prompt.txt \
            --session-dir C:/path/to/session \
            --idle-secs 60 --max-secs 1800

    Then read C:/path/to/output.md to get the durable artifact.

TUNING:
    --boot-secs  seconds of stdout quiet that signals boot complete (default 5)
    --idle-secs  seconds of stdout quiet that signals response complete
                 (default 30; use 60-120 for /deepresearch)
    --max-secs   hard cap on wallclock duration (default 900 = 15 min)

KNOWN v1 LIMITATIONS:
- Short responses (1-3 words) may get partially overwritten by ConPTY screen
  redraws before the reader captures them. Use the file-save idiom above for
  anything you need to durably retrieve.
- Strip-ANSI catches CSI + OSC + DCS + APC sequences plus the common leftover
  fragments ConPTY emits without their ESC prefix. Edge-case escapes may
  survive; agents should treat the captured text as advisory + cross-check
  against any file Feynman wrote to disk.
- Token cost flows on the user's Anthropic billing (whatever provider
  `feynman model set` points at), NOT this session's Claude Code billing.
"""

from __future__ import annotations

import argparse
import queue
import re
import sys
import threading
import time
from pathlib import Path

from winpty import PtyProcess

ANSI_RE = re.compile(
    r"\x1B(?:"
    r"[@-Z\\-_]"
    r"|\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07\x1B]*(?:\x07|\x1B\\)"  # OSC sequences (hyperlinks, cursor shape, etc.)
    r"|P[^\x1B]*\x1B\\"  # DCS sequences
    r"|_[^\x1B]*\x1B\\"  # APC sequences
    r")"
)
OSC_TAIL_RE = re.compile(r"]\d+;[^\x07\n]*\x07?")  # stray OSC fragments that lost their ESC prefix

# Patterns left over after ConPTY stripped the ESC byte from common terminal sequences.
# Examples seen with Feynman 0.2.52: "8;;" (OSC 8 hyperlink), "133;B" "133;C" (Final Term
# semantic prompts), "0;feynman - strid" (OSC 0 window-title).
LEFTOVER_TERMINAL_RE = re.compile(
    r"(?:\b0;[^\n]*"  # window-title remnant
    r"|\b8;;[^\n]*"  # hyperlink remnant
    r"|\b133;[A-Z](?:133;[A-Z])*"  # Final Term semantic prompts
    r")"
)
FEYNMAN_CMD = r"C:\Users\strid\AppData\Local\Programs\feynman\bin\feynman.cmd"


def strip_ansi(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = OSC_TAIL_RE.sub("", text)
    text = LEFTOVER_TERMINAL_RE.sub("", text)
    # Collapse runs of trailing whitespace and blank lines.
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _reader_thread(proc: PtyProcess, q: queue.Queue[str | None]) -> None:
    """Pump PTY output into a queue. Push None on EOF."""
    try:
        while True:
            chunk = proc.read(8192)
            if not chunk:
                q.put(None)
                return
            q.put(chunk)
    except EOFError:
        q.put(None)
    except Exception as e:
        q.put(f"\n[reader error: {e}]\n")
        q.put(None)


def read_until_idle(
    q: queue.Queue[str | None],
    idle_secs: float,
    max_secs: float,
) -> tuple[str, bool]:
    """Drain the queue until no new chunks for idle_secs, or until max_secs.

    Returns (accumulated_text, hit_eof).
    """
    buf: list[str] = []
    last_recv = time.time()
    start = time.time()
    while True:
        remaining = idle_secs - (time.time() - last_recv)
        if remaining <= 0:
            return "".join(buf), False
        if time.time() - start >= max_secs:
            return "".join(buf), False
        try:
            chunk = q.get(timeout=min(remaining, 0.5))
        except queue.Empty:
            continue
        if chunk is None:
            return "".join(buf), True
        buf.append(chunk)
        last_recv = time.time()


def run_feynman(
    prompt: str,
    *,
    boot_secs: float,
    idle_secs: float,
    max_secs: float,
    keep_boot: bool = False,
    session_dir: str | None = None,
) -> str:
    cmd = FEYNMAN_CMD
    args: list[str] = []
    if session_dir:
        args = ["--new-session", "--session-dir", session_dir]
    # pywinpty's spawn accepts a list with the command as first element.
    proc = PtyProcess.spawn([cmd, *args], dimensions=(40, 200))
    q: queue.Queue[str | None] = queue.Queue()
    t = threading.Thread(target=_reader_thread, args=(proc, q), daemon=True)
    t.start()

    full: list[str] = []
    try:
        # Phase 1: wait for boot to settle.
        boot, eof = read_until_idle(q, idle_secs=boot_secs, max_secs=60.0)
        if eof:
            return boot
        if keep_boot:
            full.append(boot)

        # Phase 2: send prompt + Enter (CR triggers submission in Feynman REPL).
        proc.write(prompt)
        proc.write("\r")

        # Phase 3: read response until idle.
        body, eof = read_until_idle(q, idle_secs=idle_secs, max_secs=max_secs)
        full.append(body)
        if eof:
            return "".join(full)

        # Phase 4: graceful exit.
        try:
            proc.sendcontrol("d")
        except Exception:
            pass
        time.sleep(0.5)
        try:
            proc.sendcontrol("c")
        except Exception:
            pass
        tail, _ = read_until_idle(q, idle_secs=2.0, max_secs=10.0)
        full.append(tail)
        return "".join(full)
    finally:
        try:
            if proc.isalive():
                proc.terminate(force=True)
        except Exception:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("prompt", nargs="?", help="Prompt text to send to Feynman.")
    g.add_argument("--prompt-file", type=Path, help="Path to a file containing the prompt.")
    ap.add_argument(
        "--boot-secs",
        type=float,
        default=5.0,
        help="Seconds of stdout quiet before we treat boot as done (default 5).",
    )
    ap.add_argument(
        "--idle-secs",
        type=float,
        default=30.0,
        help="Seconds of stdout quiet that signals response is done (default 30; bump to 60-120 for /deepresearch).",
    )
    ap.add_argument(
        "--max-secs",
        type=float,
        default=900.0,
        help="Hard cap in seconds (default 900 = 15 min).",
    )
    ap.add_argument(
        "--raw",
        action="store_true",
        help="Print raw transcript including ANSI (default: stripped).",
    )
    ap.add_argument(
        "--keep-boot",
        action="store_true",
        help="Include the boot banner in the output (default: drop).",
    )
    ap.add_argument(
        "--session-dir",
        type=str,
        default=None,
        help="If set, launch Feynman with --new-session --session-dir <path>. Recommended for long research workflows.",
    )
    args = ap.parse_args()

    prompt = args.prompt
    if args.prompt_file:
        prompt = args.prompt_file.read_text(encoding="utf-8")
    if not prompt or not prompt.strip():
        sys.stderr.write("error: empty prompt\n")
        sys.exit(2)

    transcript = run_feynman(
        prompt,
        boot_secs=args.boot_secs,
        idle_secs=args.idle_secs,
        max_secs=args.max_secs,
        keep_boot=args.keep_boot,
        session_dir=args.session_dir,
    )
    out = transcript if args.raw else strip_ansi(transcript)
    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
