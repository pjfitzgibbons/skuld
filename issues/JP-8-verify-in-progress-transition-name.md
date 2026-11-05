---
status: open
type: task
guide: §10
opened: 2026-11-05
closed:
---

# Verify the live "In Progress" transition from a real CIAM issue's `/transitions`

GUIDE §10 ("The live 'In Progress' transition name is verified only indirectly"): a live CIAM Task
was confirmed to have a status named "In Progress", but a GET of its `/transitions` list was
blocked by a local Claude Code hook that matches the word "transitions" in a URL regardless of
method. So no one has seen the transition Start actually picks.

Start looks the transition up by target status name: `statusTransitionId(key, "In Progress")`
(`lib/jira.py:203`, called from `lib/start.py:375`), matching `t["to"]["name"]`. Nothing is
hardcoded, so this is a check, not a fix.

Done when a human-run GET of `/rest/api/3/issue/<KEY>/transitions` on a To Do CIAM issue (Task
and any other type Start handles) shows a transition whose `to.name` is exactly "In Progress",
and GUIDE §10's bullet is updated or removed.
