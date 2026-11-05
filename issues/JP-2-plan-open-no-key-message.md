---
status: open
type: bug
guide: §4.3
opened: 2026-11-05
closed:
---

# `plan open` with no key prints `not "None"`

`plan open` (no argument) prints `Start needs a Jira key like CIAM-123, not "None"`. It should
print a usage line instead. The check is `startLib.checkId(key, "Start")` in `cmdStart`
(`lib/cli.py`), and the dispatch is `if cmd in ("start", "open")` (`lib/cli.py:294`).
