---
status: open
type: task
guide: §4.4
opened: 2026-11-05
closed:
---

# Verify Start/Open's Ghostty launch and final focus against the real apps

Two 2026-11-05 changes are covered by unit tests only:

- `openGhosttyWindow()` now passes the command as separate argv words after `-e`, with tmux's
  absolute path (`lib/term.py`, near "macOS Ghostty hands -e to `login`").
- `focusIssueWorkspace()` selects `=<KEY>:claude`, switches workspace, and focuses the
  workspace's Ghostty window (`lib/term.py`; called last in `cmdStart`, `lib/cli.py`).

Done when `plan start` and `plan open` on a real issue open an attached window and finish
focused on its claude tmux window.
