---
status: open
type: task
guide: §4.5, §4.7, §5
opened: 2026-11-05
closed:
rank: 500
---

# Run Ship, a review round, and Done end to end on a real issue

Steps (2)–(4) of the lifecycle in `docs/architecture.md` §7 (the diagram under "## 7. Operations")
have not been run live since the Go/Ship merge (2026-11-04). Take one real CIAM issue, already
started (JP-3 covers Start/Open), through:

- **Ship** (§7.2): `plan ship <KEY>` runs the pre-flight check, then `pr <KEY>` in the issue's
  `ship` tmux window: commit, push, open or update the PR.
- **Review**: at least one review round: replies/, `issue.md` Done boxes ticked, `pr` re-run.
- **Merge**: squash merge on GitHub; the PR title and branch carry the key.
- **Done** (§7.3): worktree and branch removed, base updated, ADF comment posted, slot freed,
  no browser opened, and **no Jira transition**. The human closes the Jira issue.

Done when every step behaves as §7.2/§7.3 say, with anything that doesn't filed as its own JP issue.

## Rank

Ship, a review round, merge, and Done end to end. CIAM-163 is the natural candidate: it is
started and already has PR #105. Comes after JP-3/JP-4/JP-2/JP-7/JP-8 (the lower-ranked issues)
so a failure here is about Ship/Done, not Open or Start. Findings become new JP issues.
