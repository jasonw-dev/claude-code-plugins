---
name: tmux-session
description: Start a Claude Code session in a specific directory on this machine, or stop one that is running. Use when the user asks to open, launch, or spin up a session for a repo or project ("open a session in <project>", "start Claude Code on <repo>", "I need a session for X"), or to close one ("kill the <name> session", "shut down X"). Built for driving a machine from claude.ai Remote Control, where the current session's working directory is fixed and cannot be changed.
---

# tmux-session

A Claude Code session binds its working directory when it launches, and that
directory decides which `CLAUDE.md`, project memory, and settings get loaded.
There is no way to change it afterwards. When someone is driving this machine
remotely they cannot walk over and open a terminal in the right place, so the
only option is to launch a fresh session in the directory they actually want.

This skill does that from an existing session. The new session runs in a
detached tmux session, so it needs no GUI, no unlocked screen, and nobody
present. It registers with Remote Control and appears on claude.ai within a few
seconds.

## Prerequisite

There must already be one session running on this machine with Remote Control
enabled — the one you are reading this in. Someone had to start it by hand:

```
claude --remote-control=<name>
```

That first session cannot be automated; a person has to open it once. Its own
working directory does not matter, because its job is to launch other sessions
rather than to edit code. Every request below is handled from it.

## Opening a session

```
${CLAUDE_PLUGIN_ROOT}/scripts/session.py open <absolute-path> [--label <label>] [--trust]
```

The script takes an **absolute path only**. Turning what the user said into a
path is your job, not the script's:

- Resolve the project name they gave you against the directories on this
  machine. Repository layout conventions usually live in `CLAUDE.md`.
- **Never guess.** If nothing matches, or several things match, say so and list
  the candidates. Opening a session on the wrong project is worse than opening
  none, because the user will not notice for a while.

Give `--label` when the user's request implies what the session is for ("open
one for the login bug" → `--label login`). It only matters when several sessions
share one directory; the label is what tells them apart on claude.ai. Derive it
from what they already said rather than asking.

The session is named `<directory>` or `<directory>-<label>`, with `-2`, `-3`
appended if that name is taken. Report the final name back — that is what the
user will look for on claude.ai and what `kill` expects.

## Workspace trust

Claude Code asks for confirmation the first time it runs in a directory, and
that prompt blocks startup. Nobody is there to answer it, so the script would
otherwise hang.

Before launching, check whether the directory is already trusted by reading
`hasTrustDialogAccepted` for it under `projects` in `~/.claude.json`:

- **Already trusted** — just open it. Do not bother the user.
- **Not trusted, or no entry** — ask first: *"`<path>` has not been trusted yet.
  Want me to accept it?"* Pass `--trust` only if they say yes.

Do not pass `--trust` by default and do not offer to make it permanent. The
prompt exists to stop an agent from running in a directory where it should not,
and that is worth keeping — the point of this tool is that the user cannot reach
the machine, not that they should stop being asked. Moving the question to
wherever they are keeps the safeguard intact.

Without `--trust`, hitting the prompt is a clean failure: the script removes the
half-started session and reports why. Nothing is left behind for the user to
find later and wonder about.

## Stopping a session

```
${CLAUDE_PLUGIN_ROOT}/scripts/session.py kill <name>
```

Use the name as it appears on claude.ai. Stopping a session ends its Claude Code
process and removes it from the list there.

There is deliberately no bulk kill. Sessions hold conversation context that
cannot be recovered, so each one is ended on purpose.

## Listing

There is no list command, by design — claude.ai already shows every live
session, and closed ones disappear from it. If the user asks what is running,
point them at that list. If `kill` cannot find the name they gave, it prints the
names it does know about, which covers the case where they misremembered one.

## Lifetime

Sessions run until they are stopped. Nothing expires them, and nothing cleans up
in the background: an idle session is indistinguishable from one whose owner
stepped away from a conversation they intend to continue, and silently
discarding that context would be a loss the user never sees. Each session costs
a few hundred MB of memory, so it is worth mentioning if a lot of them pile up —
but the decision to close one is always theirs.
