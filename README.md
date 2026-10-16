# claude-code-plugins

Personal [Claude Code](https://claude.com/claude-code) plugins.

## Plugins

### tmux-session

Start a Claude Code session in any directory on a machine you are driving
remotely, and stop it when you are done.

A session binds its working directory the moment it launches, and that directory
decides which `CLAUDE.md`, project memory, and settings get loaded. Nothing can
change it afterwards. That is fine when you are sitting at the machine — you
open a terminal wherever you want. It is a wall when you are driving the machine
from claude.ai on your phone: the session you are talking to is stuck wherever
it started, and you cannot pre-open one for every repository you might touch.

This plugin lets the session you already have launch new ones anywhere on the
machine. They run in detached tmux sessions, so they need no GUI, no unlocked
screen, and nobody present. Each one registers with Remote Control and shows up
on claude.ai a couple of seconds later. After a reboot it can bring every one of
them back, each into the conversation it was in.

## Install

```
/plugin marketplace add jasonw-dev/claude-code-plugins
/plugin install tmux-session@jasonw-dev-plugins
```

## Requirements

- `tmux` on the target machine
- `claude` on `PATH` (or at `~/.local/bin/claude`)
- macOS or Linux
- One session already running there with Remote Control enabled

That last one is the part you have to set up by hand, once, in front of the
machine:

```
claude --remote-control=launcher
```

Its own working directory does not matter — its job is to launch other sessions,
not to edit code. Everything else happens through it.

## Use

Ask the session you are connected to, in plain words:

> Open a session in the api project.
> Open one for the login bug in web-client.
> Shut down web-client-login.

It resolves what you said into a path, launches the session, and tells you what
it is called. You switch to it on claude.ai and carry on there.

The script underneath takes an absolute path and nothing else:

```
session.py open <absolute-path> [--label <label>] [--trust]
session.py kill <name>
```

Sessions are named after the directory, plus the label when there is one, plus
`-2`, `-3` when that name is taken — `api`, `web-client-login`,
`web-client-login-2`. That name is what appears on claude.ai and what `kill`
expects. Several sessions on one directory are fine and expected; the label is
what tells them apart.

## Workspace trust

Claude Code asks for confirmation the first time it runs in a directory, and
that prompt blocks startup. With nobody there to answer it, launching would
simply hang.

The plugin can answer it, but never on its own initiative. It asks you first:

> `/path/to/project` has not been trusted yet. Want me to accept it?

Only then does it pass `--trust`. There is no setting that makes this permanent
and no environment variable that turns it on globally, because that prompt is
what stops an agent from running somewhere it should not. The problem this
plugin solves is that you cannot reach the machine — not that you would rather
not be asked. Moving the question to wherever you are keeps the safeguard doing
its job.

Without `--trust`, hitting the prompt fails cleanly: the half-started session is
removed and the reason is reported. Nothing is left behind to puzzle over later.

## Surviving a reboot

A reboot ends every session's process, but not its conversation: the transcript
is still on disk and `claude --resume` can pick it up. What gets lost is which
sessions were running, where, under which Remote Control name, and with which
flags. `snapshot.py` records that and replays it.

```
snapshot.py save                       # before the reboot
snapshot.py restore --dry-run          # after it: preview
snapshot.py restore [NAME ...]         # bring them back
snapshot.py add NAME <claude.ai link>  # add a session that was already closed
snapshot.py show | drop NAME ...
```

Or just say "I'm about to reboot, save my sessions" and, afterwards, "restore my
sessions".

- Each session comes back in a detached tmux session, resumed into its original
  conversation and re-registered under its original Remote Control name, so the
  same claude.ai entry comes back online instead of a new one appearing.
- Launch flags such as `--model` and `--effort` are carried over; the session id
  is taken after any `/clear`, so you get the latest conversation, not the first.
- The launcher is skipped by default. You start it by hand after the reboot, and
  a second copy under the same name would fight it for the Remote Control link.
- Untrusted directories are skipped unless you pass `--trust`, under the same
  ask-first rule as `open`.
- Builds and background tasks that were running do not resume by themselves.
  Ask the conversation to run them again.

Snapshots are kept in `~/.claude/session-snapshots/` (`latest.json` plus the 20
most recent).

## Deliberate omissions

**No list command.** claude.ai already shows every live session, and closed ones
disappear from it. If `kill` cannot find the name you gave, it prints the names
it does know, which covers a misremembered one.

**No bulk kill.** Sessions hold conversation context that cannot be recovered.
Each one gets ended on purpose.

**Nothing expires.** An idle session is indistinguishable from one whose owner
stepped away from a conversation they mean to continue. Discarding that context
on a timer would be a loss you never see. Sessions cost a few hundred MB each,
so closing them matters — but when is your call.

## Licence

MIT
