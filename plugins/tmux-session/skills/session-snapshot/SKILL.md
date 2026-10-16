---
name: session-snapshot
description: Snapshot the Claude Code sessions running on this machine (Remote Control name, session id, working directory, launch flags) and bring them back after a reboot — each resumed into its original conversation in tmux and re-registered with Remote Control under the same name, so the same claude.ai entry comes back online. Also brings back a session that was already closed, given its claude.ai link. Use when the user says they are about to reboot, asks to save or snapshot sessions, says the machine has rebooted, asks to restore or recover sessions, wants a closed session back, or asks whether sessions survive a reboot.
---

# session-snapshot

A reboot ends every Claude Code process, but not the conversations: each one is
a transcript on disk that `claude --resume <id>` can pick up again. What is lost
is the knowledge of which sessions were running, where, under which Remote
Control name, and with which flags. This skill records that before the reboot
and replays it afterwards.

```
S=${CLAUDE_PLUGIN_ROOT}/scripts/snapshot.py
$S save                      # record every live session
$S show                      # print the snapshot
$S restore --dry-run         # preview: what would come back, what would be skipped, and why
$S restore [NAME ...]        # bring them back (no names = every tmux session in the snapshot)
$S add NAME <claude.ai link> # add a session that is already closed
$S drop NAME ...             # remove entries from the snapshot
```

Snapshots are kept in `~/.claude/session-snapshots/`: `latest.json` plus the 20
most recent ones. That directory survives a reboot; `/tmp` does not.

## Before a reboot: save

Run `save` and report the list back. The data comes from Claude Code's own
session registry (`~/.claude/sessions/<pid>.json`) plus each process's exact
argv, so the session id after a `/clear` and launch flags such as `--model` or
`--effort` are captured too. Entries added by hand with `add` are kept across
later saves until they are dropped or that session comes back to life.

Remind the user that **builds and background tasks that were mid-run do not
resume on their own** — once a conversation is back, it has to be told to run
them again.

## After a reboot: restore

The session the user talks to (the launcher, see the tmux-session skill) has to
be started by hand first; nothing here can do that remotely.

1. Run `restore --dry-run` and show the user what would come back and what would
   be skipped.
2. On their go-ahead run `restore`, or only the names they picked.
3. Report each result with its claude.ai link. Resuming reuses the original
   claude.ai entry: the existing item in the sidebar comes back online rather
   than a new one appearing.

`restore` skips, and says why: a session that is already running, a name held by
another live session, a missing transcript, a missing directory, an existing
tmux session of the same name, and an untrusted directory.

- **The launcher is not restored by default.** It runs outside tmux and is
  started by hand. Do not bring up a second copy with `restore <launcher-name>`:
  two sessions registered under one Remote Control name keep kicking each other
  off. To tell whether a restored session is disconnected, and why, see
  `references/remote-control.md`.
- **Untrusted directories** — ask first: *"`<path>` has not been trusted yet.
  Want me to accept it?"* Pass `--trust` only if they say yes. Trust on a parent
  directory counts.
- The moment a session reconnects, messages that were still waiting on the
  claude.ai side are delivered, so it may start working on its own.

## Bringing back a closed session

The quickest route: ask the user to open that session from the claude.ai sidebar
and copy its URL (`https://claude.ai/code/session_…`), then:

```
$S add <name as shown in the sidebar> <url>
```

The script finds the newest transcript carrying that Remote Control id and uses
it as the conversation to resume — a process keeps the same id however many
times it is `/clear`ed. The original launch flags cannot be recovered, so none
are passed; if the user remembers them, add `--flags "--model … --effort …"`.

With only a name and no link: look in the launcher's transcript
(`~/.claude/projects/<launcher-dir>/*.jsonl`) for `Session '<name>' is up` and
note the time, or take the tmux `created` time. Then, in the transcripts for
that session's directory, find the one whose earliest `bridge-session` record
matches. Once you have the `session_…` id, `add` it the same way.
