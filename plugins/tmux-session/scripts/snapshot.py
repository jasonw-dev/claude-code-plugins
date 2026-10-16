#!/usr/bin/env python3
"""Snapshot the Claude Code sessions running on this machine and bring them
back after a reboot, each resumed into its original conversation and
re-registered with Remote Control under its original name.

Usage:
    snapshot.py save                          record every live session
    snapshot.py show [--file F]               print a snapshot
    snapshot.py add NAME REF [--flags "..."]  add a dead session by claude.ai link,
                                              bridge id or session id
    snapshot.py drop NAME ...                 remove entries from the snapshot
    snapshot.py restore [NAME ...] [--file F] [--dry-run] [--trust]

Snapshots live in ~/.claude/session-snapshots/ (latest.json plus recent
history), which survives a reboot where /tmp does not.
"""

import argparse
import ctypes
import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
SESSIONS_DIR = CLAUDE_DIR / "sessions"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SNAP_DIR = CLAUDE_DIR / "session-snapshots"
LATEST = SNAP_DIR / "latest.json"
KEEP_SNAPSHOTS = 20

TMUX_PREFIX = "cc-"
PANE_WIDTH = 200
PANE_HEIGHT = 50
POLL_INTERVAL = 0.5
STARTUP_TIMEOUT = 60.0
READY_MARKER = "remote-control is active"
TRUST_MARKER = "trust this folder"
TRUST_CONFIRM_RE = re.compile(r"❯\s*(?:1\.\s*)?Yes, I trust this folder")
TRUST_OPTION = "Yes, I trust this folder"
LINK_RE = re.compile(r"https://claude\.ai/code/session_\w+")

# Flags that identify one particular conversation or its Remote Control name.
# They are rebuilt on restore, so they must not be carried over verbatim.
DROP_WITH_VALUE = {"--resume", "-r", "--session-id"}
DROP_BARE = {"--continue", "-c", "--fork-session", "--remote-control"}


def fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


# --- process inspection -----------------------------------------------------

def proc_argv(pid):
    """Exact argv of a process.

    `ps` joins argv with spaces and drops quoting, which mangles a Remote
    Control name containing spaces or shell metacharacters, so read the
    kernel's copy instead: /proc on Linux, sysctl on macOS.
    """
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return None
        return [a.decode(errors="replace") for a in raw.rstrip(b"\0").split(b"\0")]
    if sys.platform != "darwin":
        return None
    return darwin_argv(pid)


def darwin_argv(pid):
    """argv via sysctl KERN_PROCARGS2."""
    libc = ctypes.CDLL("libc.dylib", use_errno=True)
    CTL_KERN, KERN_ARGMAX, KERN_PROCARGS2 = 1, 8, 49
    argmax = ctypes.c_int()
    size = ctypes.c_size_t(ctypes.sizeof(argmax))
    mib = (ctypes.c_int * 2)(CTL_KERN, KERN_ARGMAX)
    if libc.sysctl(mib, 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0:
        return None
    buf = ctypes.create_string_buffer(argmax.value)
    size = ctypes.c_size_t(argmax.value)
    mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    raw = buf.raw[: size.value]
    argc = int.from_bytes(raw[:4], "little")
    rest = raw[4:]
    # exec path, then NUL padding, then argc NUL-terminated strings
    end = rest.index(b"\0")
    rest = rest[end:].lstrip(b"\0")
    return [a.decode(errors="replace") for a in rest.split(b"\0")[:argc]]


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def live_sessions():
    """Registry entries under ~/.claude/sessions whose process is still a claude."""
    out = []
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            info = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        pid = info.get("pid")
        if not isinstance(pid, int) or not pid_alive(pid):
            continue
        argv = proc_argv(pid)
        if not argv or not any("claude" in Path(a).name for a in argv[:2]):
            continue  # pid reused by something else
        info["argv"] = argv
        out.append(info)
    return out


def split_argv(argv):
    """Return (remote_control_name, carried_flags) from a claude argv."""
    rc_name = None
    flags = []
    args = argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        key = a.split("=", 1)[0]
        if key == "--remote-control":
            if "=" in a:
                rc_name = a.split("=", 1)[1]
        elif key in DROP_WITH_VALUE:
            if "=" not in a:
                i += 1
        elif a in DROP_BARE:
            pass
        else:
            flags.append(a)
        i += 1
    return rc_name, flags


def tmux_session_of(info):
    # registry stores "cc-name:@window.%pane"
    t = info.get("tmux")
    return t.split(":", 1)[0] if t else None


def entry_from_live(info):
    rc_name, flags = split_argv(info["argv"])
    name = rc_name or info.get("name")
    bridge = info.get("bridgeSessionId")
    return {
        "name": name,
        "sessionId": info["sessionId"],
        "cwd": info["cwd"],
        "flags": flags,
        "tmux": tmux_session_of(info),
        "link": f"https://claude.ai/code/{bridge}" if bridge else None,
        "source": "live",
    }


# --- transcripts ------------------------------------------------------------

def transcript_path(session_id):
    hits = glob.glob(str(PROJECTS_DIR / "*" / f"{session_id}.jsonl"))
    return Path(hits[0]) if hits else None


def transcript_cwd(path):
    with open(path, errors="replace") as fh:
        for line in fh:
            m = re.search(r'"cwd":"((?:[^"\\]|\\.)*)"', line)
            if m:
                return json.loads(f'"{m.group(1)}"')
    return None


def bridge_of(path):
    with open(path, errors="replace") as fh:
        for line in fh:
            if '"bridge-session"' in line:
                try:
                    return json.loads(line).get("bridgeSessionId")
                except ValueError:
                    pass
    return None


def resolve_ref(ref):
    """Map a claude.ai link / bridge id / session id to the transcript to resume.

    A claude.ai entry survives /clear: every transcript a process writes keeps
    the same bridge id, so the conversation to resume is the newest transcript
    carrying that id.
    """
    m = re.search(r"session_(\w+)", ref)
    if m:
        needle = f'"bridgeSessionId":"cse_{m.group(1)}"'
        # Top-level transcripts only: subagent transcripts and tool output
        # live deeper, are not resumable, and make a recursive grep crawl.
        files = [str(p) for p in PROJECTS_DIR.glob("*/*.jsonl")]
        r = run(["grep", "-lF", "-e", needle, "--", *files])
        hits = [Path(h) for h in r.stdout.splitlines()]
        if not hits:
            fail(f"No transcript carries Remote Control id session_{m.group(1)}.")
        newest = max(hits, key=lambda p: p.stat().st_mtime)
        return newest.stem, transcript_cwd(newest)
    path = transcript_path(ref)
    if not path:
        fail(f"No transcript for session id {ref}.")
    return ref, transcript_cwd(path)


# --- snapshot files ---------------------------------------------------------

def write_snapshot(snap):
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = SNAP_DIR / f"{stamp}.json"
    text = json.dumps(snap, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text)
    LATEST.write_text(text)
    old = sorted(p for p in SNAP_DIR.glob("2*.json"))[:-KEEP_SNAPSHOTS]
    for p in old:
        p.unlink()
    return path


def load_snapshot(file):
    path = Path(file).expanduser() if file else LATEST
    if not path.is_file():
        fail(f"No snapshot at {path}. Run 'save' first.")
    return json.loads(path.read_text())


def print_entries(entries):
    for e in entries:
        flags = " ".join(e["flags"]) or "-"
        where = e["tmux"] or "(not in tmux)"
        print(f"{e['name']}\n  id {e['sessionId']}  {where}\n  cwd {e['cwd']}\n"
              f"  flags {flags}\n  link {e.get('link') or '-'}")


def cmd_save(args):
    entries = [entry_from_live(i) for i in live_sessions()]
    # Sessions added by hand are not running, so a fresh scan cannot see
    # them; keep them until they are dropped or come back to life.
    live_names = {e["name"] for e in entries}
    if LATEST.is_file():
        entries += [e for e in load_snapshot(None)["sessions"]
                    if e.get("source") == "added" and e["name"] not in live_names]
    entries.sort(key=lambda e: (e["tmux"] is None, e["source"] != "live", e["name"] or ""))
    snap = {
        "host": socket.gethostname(),
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sessions": entries,
    }
    path = write_snapshot(snap)
    print_entries(entries)
    print(f"\n{len(entries)} sessions saved to {path}")


def cmd_show(args):
    snap = load_snapshot(args.file)
    print(f"{snap['host']}  {snap['createdAt']}\n")
    print_entries(snap["sessions"])


def cmd_add(args):
    snap = load_snapshot(None) if LATEST.is_file() else {
        "host": socket.gethostname(), "sessions": []}
    session_id, cwd = resolve_ref(args.ref)
    if not cwd:
        fail("Could not read the working directory from the transcript.")
    path = transcript_path(session_id)
    bridge = bridge_of(path) if path else None
    entry = {
        "name": args.name,
        "sessionId": session_id,
        "cwd": cwd,
        "flags": shlex.split(args.flags or ""),
        "tmux": TMUX_PREFIX + args.name,
        "link": f"https://claude.ai/code/{bridge.replace('cse_', 'session_')}" if bridge else None,
        "source": "added",
    }
    snap["sessions"] = [e for e in snap["sessions"] if e["name"] != args.name] + [entry]
    snap["createdAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    write_snapshot(snap)
    print_entries([entry])


def cmd_drop(args):
    snap = load_snapshot(None)
    before = len(snap["sessions"])
    snap["sessions"] = [e for e in snap["sessions"] if e["name"] not in args.names]
    if len(snap["sessions"]) == before:
        fail(f"None of {', '.join(args.names)} is in the snapshot.")
    write_snapshot(snap)
    print(f"Dropped {before - len(snap['sessions'])} entries.")


# --- restore ----------------------------------------------------------------

def is_trusted(cwd):
    try:
        projects = json.loads((HOME / ".claude.json").read_text()).get("projects", {})
    except (OSError, ValueError):
        return False
    p = Path(cwd)
    for candidate in [p, *p.parents]:
        if projects.get(str(candidate), {}).get("hasTrustDialogAccepted"):
            return True
    return False


def find_claude():
    claude = shutil.which("claude")
    if claude:
        return claude
    fallback = HOME / ".local" / "bin" / "claude"
    if fallback.is_file():
        return str(fallback)
    fail("claude executable not found.")


def capture(tmux, name):
    r = run([tmux, "capture-pane", "-p", "-t", f"={name}:"])
    return r.stdout if r.returncode == 0 else None


def cmd_restore(args):
    tmux = shutil.which("tmux") or fail("tmux not found (brew install tmux).")
    claude = find_claude()
    snap = load_snapshot(args.file)
    entries = snap["sessions"]

    if args.names:
        known = {e["name"] for e in entries}
        missing = [n for n in args.names if n not in known]
        if missing:
            fail(f"Not in snapshot: {', '.join(missing)}. Known: {', '.join(sorted(known))}")
        entries = [e for e in entries if e["name"] in args.names]
    else:
        # The launcher lives outside tmux and is started by hand; bringing a
        # second copy up under the same Remote Control name makes the two
        # kick each other off.
        entries = [e for e in entries if e["tmux"]]

    live = live_sessions()
    live_ids = {i["sessionId"] for i in live}
    live_names = {split_argv(i["argv"])[0] or i.get("name") for i in live}

    plan, skipped = [], []
    for e in entries:
        tmux_name = e["tmux"] or TMUX_PREFIX + e["name"]
        if e["sessionId"] in live_ids:
            skipped.append((e["name"], "already running"))
        elif e["name"] in live_names:
            skipped.append((e["name"], "another live session holds this Remote Control name"))
        elif not transcript_path(e["sessionId"]):
            skipped.append((e["name"], f"transcript {e['sessionId']} not found"))
        elif not Path(e["cwd"]).is_dir():
            skipped.append((e["name"], f"directory gone: {e['cwd']}"))
        elif run([tmux, "has-session", "-t", f"={tmux_name}"]).returncode == 0:
            skipped.append((e["name"], f"tmux session {tmux_name} already exists"))
        elif not is_trusted(e["cwd"]) and not args.trust:
            skipped.append((e["name"], f"{e['cwd']} not trusted (re-run with --trust after asking)"))
        else:
            plan.append((e, tmux_name))

    for name, why in skipped:
        print(f"skip  {name}: {why}")
    if args.dry_run:
        for e, tmux_name in plan:
            print(f"would {e['name']} ({tmux_name}) --resume {e['sessionId']} {' '.join(e['flags'])}")
        return
    if not plan:
        return

    for e, tmux_name in plan:
        cmd = [claude, "--resume", e["sessionId"], f"--remote-control={e['name']}", *e["flags"]]
        r = run([tmux, "new-session", "-d", "-s", tmux_name,
                 "-x", str(PANE_WIDTH), "-y", str(PANE_HEIGHT),
                 "-c", e["cwd"], shlex.join(cmd)])
        if r.returncode != 0:
            print(f"FAIL  {e['name']}: {r.stderr.strip()}")

    pending = {tmux_name: e for e, tmux_name in plan}
    results = {}
    answered, nudges = set(), {}
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while pending and time.monotonic() < deadline:
        for tmux_name, e in list(pending.items()):
            pane = capture(tmux, tmux_name)
            if pane is None:
                results[e["name"]] = "exited during startup"
                del pending[tmux_name]
            elif READY_MARKER in pane:
                links = LINK_RE.findall(pane)
                results[e["name"]] = "up  " + (links[-1] if links else "")
                del pending[tmux_name]
            elif TRUST_MARKER in pane and tmux_name not in answered:
                # Only press Enter with the cursor provably on "Yes". One
                # layout starts on "No, exit" and needs a nudge down first.
                if args.trust and TRUST_CONFIRM_RE.search(pane):
                    run([tmux, "send-keys", "-t", f"={tmux_name}:", "Enter"])
                    answered.add(tmux_name)
                elif args.trust and TRUST_OPTION in pane and nudges.get(tmux_name, 0) < 3:
                    run([tmux, "send-keys", "-t", f"={tmux_name}:", "Down"])
                    nudges[tmux_name] = nudges.get(tmux_name, 0) + 1
                else:
                    run([tmux, "kill-session", "-t", f"={tmux_name}"])
                    results[e["name"]] = "blocked on trust prompt, removed"
                    del pending[tmux_name]
        time.sleep(POLL_INTERVAL)
    for tmux_name, e in pending.items():
        tail = [l for l in (capture(tmux, tmux_name) or "").splitlines() if l.strip()][-4:]
        results[e["name"]] = "not confirmed within %ds, left running; pane tail:\n    %s" % (
            STARTUP_TIMEOUT, "\n    ".join(tail))

    for name, outcome in results.items():
        print(f"{'ok  ' if outcome.startswith('up') else 'warn'}  {name}: {outcome}")


def main():
    p = argparse.ArgumentParser(prog="snapshot.py")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("save").set_defaults(func=cmd_save)

    s = sub.add_parser("show")
    s.add_argument("--file")
    s.set_defaults(func=cmd_show)

    a = sub.add_parser("add")
    a.add_argument("name", help="Remote Control name to register under")
    a.add_argument("ref", help="claude.ai session link, session_xxx id, or local session id")
    a.add_argument("--flags", help='extra claude flags, e.g. "--model claude-fable-5-1"')
    a.set_defaults(func=cmd_add)

    d = sub.add_parser("drop")
    d.add_argument("names", nargs="+")
    d.set_defaults(func=cmd_drop)

    r = sub.add_parser("restore")
    r.add_argument("names", nargs="*")
    r.add_argument("--file")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--trust", action="store_true")
    r.set_defaults(func=cmd_restore)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
