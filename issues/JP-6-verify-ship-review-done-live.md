---
status: open
type: task
guide: §4.5, §4.7, §5
opened: 2026-11-05
closed:
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
