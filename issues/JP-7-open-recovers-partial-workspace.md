---
status: open
type: task
guide: §4.4
opened: 2026-11-05
closed:
---

# Open finds a partially provisioned issue workspace and fills in what's missing

`plan open <KEY>` should check each piece of an issue's setup, keep what is already there, and
create only what is missing, so that re-running Open after any failure or hand-closed window
recovers the full setup. Open stays idempotent: run it twice on a complete setup and nothing
changes except focus.

The design already asks for part of this: Open reopens the worktree "and its tmux session/Aerospace
workspace via the slot lookup above, recreating either independently if the other already exists"
(`docs/architecture.md` §7.1, ~line 270). The code doesn't do that. `openIssueWorkspace()`
(`lib/term.py`) returns early whenever the tmux session exists (`if hasSession(key):`), so a
missing `claude`/`shell` window, or a window outside its
workspace is never repaired. JP-1 (done) fixed one case: a session with no Ghostty window
in the workspace now gets one.

## Pieces to check, in setup order

1. Worktree and branch: `start.start()` already reuses an existing worktree.
2. Slot: the slots-file entry for KEY and a workspace for it (reuse rule 3; else a free one).
3. tmux session `=<KEY>`.
4. tmux windows `shell` and `claude` in that session; a new `claude` window gets the
   `claude "Work on Jira <KEY>"` send-keys.
5. A Ghostty window attached to the session, in the slot's workspace: open one if none is
   attached; move it to the workspace if it's elsewhere.
6. The browse URL in Brave in that workspace (`openUrl()`).
7. Final focus: workspace, Ghostty window, `claude` tmux window (`focusIssueWorkspace()`).

## Open question

How to tell which Ghostty window is attached to the session. `tmux list-clients -t =<KEY>` shows
whether any client is attached, but not which Aerospace window id it belongs to. A simple rule
would be: "the slot's workspace has a Ghostty window and the session has a client" means present.

## Out of scope

Failures before the worktree exists (Jira lookup, blockers prompt, lock, the In Progress
transition, worktree/branch creation) are not recovered: the command stops and the human
intervenes. The same goes for any error raised during repair; Open reports it and stops.

## Done when

Unit tests cover each piece missing on its own (session without window, window without session,
missing `claude` window, Ghostty window in the wrong workspace, stale slot), and a live run of
`plan open` repairs a hand-broken setup.
