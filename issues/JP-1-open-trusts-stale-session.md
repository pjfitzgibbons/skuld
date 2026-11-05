---
status: done
type: bug
guide: §4.4
opened: 2026-11-05
closed: 2026-11-05
---

# Open assumes a live tmux session means a visible Ghostty window

`openIssueWorkspace()` (`lib/term.py`, the `if hasSession(key):` branch) only switches to the
workspace when the issue's tmux session exists. If a Ghostty window never attached (the
CIAM-163 `login: ... No such file or directory` failure on 2026-11-05), or the window was closed
by hand, the session is still there and nothing opens. Workaround: `tmux kill-session -t =<KEY>`.

Wanted: when the session exists but the workspace has no Ghostty window
(`windowsIn(workspace, GHOSTTY_BUNDLE_ID)`), open one attached to the session.

Covered by JP-7, which recovers every missing piece of the setup, not only this one.

Fixed 2026-11-05: the `hasSession` branch of `openIssueWorkspace()` (`lib/term.py`) now attaches a
new Ghostty window via `attachGhosttyWindow()` when the workspace has none; test
`test_openIssueWorkspace_reattaches_ghostty_when_session_has_no_window`. Not yet run live (JP-3).
