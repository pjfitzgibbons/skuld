---
status: in-progress
type: task
guide: §4.4
opened: 2026-11-05
closed:
rank: 100
---

# Verify Start/Open's Ghostty launch and final focus against the real apps

Two 2026-11-05 changes are covered by unit tests only:

- `openGhosttyWindow()` now passes the command as separate argv words after `-e`, with tmux's
  absolute path (`lib/term.py`, near "macOS Ghostty hands -e to `login`").
- `focusIssueWorkspace()` selects `=<KEY>:claude`, switches workspace, and focuses the
  workspace's Ghostty window (`lib/term.py`; called last in `cmdStart`, `lib/cli.py`).

Done when `plan start` and `plan open` on a real issue open an attached window and finish
focused on its claude tmux window.

## Rank

Today's Ghostty launch, JP-1 reattach, and claude-window focus changes are unit-tested only. One
live `plan open CIAM-163` also exercises JP-4: Brave is already running, so `openUrl()` takes the
unverified `open -a` new-tab branch. Everything after this assumes Open works.

## Log
- 2026-11-05: Started on `feat/JP-3-verify-start-open-s-ghostty`, in /Users/pfitzgibbons/programming/jira-plan.worktrees/JP-3.
