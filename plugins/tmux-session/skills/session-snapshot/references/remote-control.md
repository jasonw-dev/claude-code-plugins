# Diagnosing Remote Control disconnects

Read this when checking, from another session through tmux, whether a session's
Remote Control link is down.

- **Reliable signal:** type `/remote-control` into that pane (do not press
  Enter) and read the completion hint. "Control this session from your phone or
  claude.ai/code" means it is disconnected and Enter would reconnect.
  "Disconnect Remote Control" means it is still connected and Enter would drop
  it.
- **Unreliable:** whether `/rc` shows in the status line. An extra `⧉ <name>`
  line can push `/rc` out of view, which looks like a disconnect but is not.
  Only an explicit `/rc failed` is conclusive.
- Local peers (`ListAgents`) talk over local IPC, which is separate from Remote
  Control's cloud channel. Seeing one says nothing about the other.
- After a disconnect the claude.ai view freezes at that moment (it can still
  show tasks that finished long ago). Do not read it as the current state.
- **The most common cause is two sessions registered under the same Remote
  Control name.** They kick each other off: `/rc failed`, or connecting and
  dropping again. Re-enabling one only kicks the other; the fix is to leave one
  session per name (close or rename the other). A session enabled from inside
  with `/remote-control` has no `--remote-control=` in its `ps` command line, so
  flags prove nothing; check each session's completion hint instead.

tmux notes: `capture-pane` can lag a few seconds behind keystrokes, so `C-u` or
`BSpace` that seem to have done nothing usually did. Send one visible character
to force a redraw before judging. Before clearing an input box, save the draft
and type it back afterwards with `send-keys -l` (without Enter).

## After switching accounts

Read this when the local Claude Code login changes to another account or
organization.

- Every session's Remote Control drops at once, with "signed-in claude.ai
  account or organization changed".
- Always reconnect with a name: `/remote-control <name>`. Without one the
  session gets an auto-generated name.
- Once a wrong name is registered, neither `--resume --remote-control=<name>`
  nor `/rename` changes what claude.ai shows: resume reattaches to the existing
  entry, and `/rename` is local only.
- The fix is to disconnect inside the session and reconnect with the right name.
  That creates a new entry under the correct name, and the misnamed one drops out
  of the sidebar.
- Entries created after an account switch do not backfill earlier history on the
  web; Claude itself still has the full context.
- claude.ai entries and artifacts stay with the old account and cannot be moved.
- Run `snapshot.py save` again after reconnecting so the snapshot holds the new
  links.

Reconnecting through tmux (check first that the pane is idle and its input box
is empty):

1. Send `/remote-control` and Enter. A dialog opens on "Continue".
2. Press Up twice to "Disconnect this session", then Enter. The pane shows
   "Remote Control disconnected."
3. Send `/remote-control <name>` and Enter, and wait for "/remote-control is
   active" and a new `session_…` link.
