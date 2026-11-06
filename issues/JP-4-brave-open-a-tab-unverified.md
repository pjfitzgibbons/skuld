---
status: done
type: task
guide: §4.6, §10
opened: 2026-11-05
closed: 2026-11-05
rank:
---

# Verify `open -a "Brave Browser" <url>` opens a tab in the focused window

`docs/architecture.md` §7.6.1 (`#open-url`) marks this UNVERIFIED: that a plain `open -a` opens
a new tab in the Brave window `openUrl()` just focused, rather than in some other Brave window.
Needs a live run against a real Brave process.

## Rank

Shares JP-3's rank (100): one live `plan open CIAM-163` exercises both. Brave is already running,
so `openUrl()` takes the unverified `open -a` new-tab branch this issue is about.

## Log

- 2026-11-05: Verified live. `plan open CIAM-163`, with Brave running, opened the ticket as the
  active tab of workspace 3's Brave window. architecture.md §7.6.1 updated.
