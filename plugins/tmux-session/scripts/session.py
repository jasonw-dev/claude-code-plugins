#!/usr/bin/env python3
"""Start and stop Claude Code sessions inside detached tmux sessions.

A Claude Code session binds its working directory at launch time, and that
directory decides which CLAUDE.md and project memory get loaded. When driving a
machine remotely there is no way to change it afterwards, so the only option is
to launch a fresh session in the directory you actually want. This script does
that without needing anyone in front of the machine: tmux provides the pty, and
Remote Control registers the new session so it shows up on claude.ai.

Usage:
    session.py open <absolute-path> [--label LABEL] [--trust]
    session.py kill <name>
"""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

TMUX_PREFIX = "cc-"

# Poll interval and ceiling for watching a freshly launched session come up.
# Measured on an M-series Mac: the trust prompt renders in ~0.75s and Remote
# Control reports ready ~2s after it is dismissed. The ceiling is slack for
# slower machines, not an expected wait.
POLL_INTERVAL = 0.25
STARTUP_TIMEOUT = 30.0

READY_MARKER = "remote-control is active"
TRUST_MARKER = "trust this folder"
# Only ever send Enter when the cursor is provably parked on the affirmative
# option. Anything else means the screen is not what we think it is. The
# prompt has shipped in two layouts: a numbered one where the cursor starts on
# "1. Yes, I trust this folder", and an unnumbered one where it starts on
# "No, exit" and has to be moved down onto the Yes option first.
TRUST_CONFIRM_RE = re.compile(r"❯\s*(?:1\.\s*)?Yes, I trust this folder")
TRUST_OPTION = "Yes, I trust this folder"
# How many cursor moves to attempt before declaring the screen unrecognized.
# Both known layouts need at most one.
MAX_TRUST_NUDGES = 3

PANE_WIDTH = 200
PANE_HEIGHT = 50


def fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


def find_tmux():
    tmux = shutil.which("tmux")
    if not tmux:
        fail(
            "tmux not found on this machine. Install it first "
            "(macOS: brew install tmux, Debian/Ubuntu: apt install tmux)."
        )
    return tmux


def find_claude():
    claude = shutil.which("claude")
    if claude:
        return claude
    # Claude Code's native installer drops the launcher here and some shells
    # never pick it up in a non-interactive PATH.
    fallback = Path.home() / ".local" / "bin" / "claude"
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    fail("claude executable not found in PATH or ~/.local/bin.")


def session_target(tmux_name):
    """Address a session by exact name.

    Without the '=' prefix tmux falls back to prefix matching, so a lookup for
    'cc-app' happily resolves to 'cc-app-login'. That turns a collision check
    into a false positive and, far worse, lets a kill land on a session the
    user never named.
    """
    return "=" + tmux_name


def pane_target(tmux_name):
    """Address a session's active pane by exact name.

    Pane targets are 'session:window.pane', so the exact-match prefix has to be
    paired with a trailing colon to mark where the session name ends. Plain
    '=name' is not a valid pane target and the command fails outright.
    """
    return "=" + tmux_name + ":"


def session_exists(tmux, tmux_name):
    return run([tmux, "has-session", "-t", session_target(tmux_name)]).returncode == 0


def list_managed_sessions(tmux):
    result = run([tmux, "list-sessions", "-F", "#{session_name}"])
    if result.returncode != 0:
        return []
    return [
        line[len(TMUX_PREFIX):]
        for line in result.stdout.splitlines()
        if line.startswith(TMUX_PREFIX)
    ]


def capture_pane(tmux, tmux_name):
    result = run([tmux, "capture-pane", "-p", "-t", pane_target(tmux_name)])
    if result.returncode != 0:
        return None
    return result.stdout


def pick_name(tmux, base):
    """Return the first free name in the base, base-2, base-3 ... series."""
    if not session_exists(tmux, TMUX_PREFIX + base):
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if not session_exists(tmux, TMUX_PREFIX + candidate):
            return candidate
    fail(f"Too many sessions already named '{base}'.")


def sanitize(part):
    """Reduce a path component or label to something safe as a session name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip("-")
    return cleaned or "session"


def cmd_open(args):
    tmux = find_tmux()
    claude = find_claude()

    directory = Path(args.path).expanduser()
    if not directory.is_absolute():
        fail(f"Path must be absolute, got '{args.path}'.")
    if not directory.is_dir():
        fail(f"Not a directory: {directory}")

    base = sanitize(directory.name)
    if args.label:
        base = f"{base}-{sanitize(args.label)}"

    name = pick_name(tmux, base)
    tmux_name = TMUX_PREFIX + name

    launch = f"{shlex.quote(claude)} --remote-control={shlex.quote(name)}"
    created = run([
        tmux, "new-session", "-d",
        "-s", tmux_name,
        "-x", str(PANE_WIDTH), "-y", str(PANE_HEIGHT),
        "-c", str(directory),
        launch,
    ])
    if created.returncode != 0:
        fail(f"Failed to create tmux session: {created.stderr.strip()}")

    outcome = watch_startup(tmux, tmux_name, allow_trust=args.trust)

    if outcome == "ready":
        print(f"Session '{name}' is up in {directory} (tmux: {tmux_name}).")
        return
    if outcome == "untrusted":
        run([tmux, "kill-session", "-t", session_target(tmux_name)])
        fail(
            f"{directory} has not been trusted yet, so the session cannot start "
            f"unattended. Re-run with --trust to accept it, or open Claude Code "
            f"in that directory once by hand. The half-started session was "
            f"removed."
        )
    if outcome == "died":
        fail(f"Session '{name}' exited before Remote Control came up.")
    if outcome == "wrong-screen":
        run([tmux, "kill-session", "-t", session_target(tmux_name)])
        fail(
            "A trust prompt appeared but the cursor was not on "
            "'Yes, I trust this folder', so nothing was sent. The session was "
            "removed. Check the directory by hand."
        )

    # Timed out. The session is probably fine and the startup banner simply
    # changed shape, so leave it alone rather than destroying working state.
    print(
        f"Session '{name}' was created in {directory} (tmux: {tmux_name}), but its "
        f"Remote Control status could not be confirmed within "
        f"{STARTUP_TIMEOUT:.0f}s. Check whether '{name}' appears on claude.ai."
    )


def watch_startup(tmux, tmux_name, allow_trust):
    """Poll the pane until the session is ready, blocked, or dead."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    trust_answered = False
    nudges = 0

    while time.monotonic() < deadline:
        pane = capture_pane(tmux, tmux_name)
        if pane is None:
            return "died"

        if READY_MARKER in pane:
            return "ready"

        if TRUST_MARKER in pane and not trust_answered:
            if not allow_trust:
                return "untrusted"
            if TRUST_CONFIRM_RE.search(pane):
                run([tmux, "send-keys", "-t", pane_target(tmux_name), "Enter"])
                trust_answered = True
            elif TRUST_OPTION in pane and nudges < MAX_TRUST_NUDGES:
                # Unnumbered layout: cursor starts on "No, exit". Step down
                # and let the next poll verify before confirming.
                run([tmux, "send-keys", "-t", pane_target(tmux_name), "Down"])
                nudges += 1
            else:
                return "wrong-screen"

        time.sleep(POLL_INTERVAL)

    return "timeout"


def cmd_kill(args):
    tmux = find_tmux()
    name = args.name
    if name.startswith(TMUX_PREFIX):
        name = name[len(TMUX_PREFIX):]
    tmux_name = TMUX_PREFIX + name

    if not session_exists(tmux, tmux_name):
        managed = list_managed_sessions(tmux)
        if managed:
            listing = ", ".join(sorted(managed))
            fail(f"No session named '{name}'. Currently open: {listing}")
        fail(f"No session named '{name}'. Nothing is open.")

    killed = run([tmux, "kill-session", "-t", session_target(tmux_name)])
    if killed.returncode != 0:
        fail(f"Failed to kill '{name}': {killed.stderr.strip()}")

    if session_exists(tmux, tmux_name):
        fail(f"'{name}' still exists after kill-session.")

    print(f"Session '{name}' stopped.")


def main():
    parser = argparse.ArgumentParser(
        prog="session.py",
        description="Open and close Claude Code sessions in detached tmux sessions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    opener = sub.add_parser("open", help="start a session in a directory")
    opener.add_argument("path", help="absolute path to the working directory")
    opener.add_argument(
        "--label",
        help="distinguishes several sessions on the same directory",
    )
    opener.add_argument(
        "--trust",
        action="store_true",
        help="accept the workspace trust prompt if it appears",
    )
    opener.set_defaults(func=cmd_open)

    killer = sub.add_parser("kill", help="stop a session by name")
    killer.add_argument("name", help="session name as shown on claude.ai")
    killer.set_defaults(func=cmd_kill)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
