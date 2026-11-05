# jira-plan issues

Work order: [PLAN.md](PLAN.md).

One file per issue: `JP-<n>-short-slug.md` (JP-1, JP-2, ...), numbered in creation order and
never reused. Refer to an issue by its id alone, e.g. JP-3. Each file
opens with frontmatter:

```
---
status: open        # open | in-progress | done | wontfix
type: bug           # bug | task | question
guide: §4.4         # GUIDE.md section(s) it belongs to, comma-separated
opened: YYYY-MM-DD
closed:             # YYYY-MM-DD when status leaves open
---
```

then a `# Title`, what's wrong or wanted, and where it lives (`file:line`). When an issue is done,
set `status`/`closed` and add a line saying what fixed it (commit or file). List the open ones:

```
grep -l '^status: open' issues/JP-*.md
```

Issues for one GUIDE section:

```
grep -l '^guide:.*§4\.4' issues/JP-*.md
```

**User decision 2026-11-05:** `plan` treats `JP-<n>` as issues too (`lib/fileissues.py`), so this
file format is also what `start`/`ship`/`done`/`view`/the picker read and write. Start sets
`status: in-progress` itself; `done`/`wontfix` is always set by a human, never by `plan`. Every
dated note Start or Done would otherwise post as a Jira comment is appended instead as a
`- YYYY-MM-DD: <text>` line under a `## Log` heading at the end of the file (added once, if not
already there):

```
## Log
- 2026-11-02: Started on `feat/JP-2-a-task-already`, in /repo.worktrees/JP-2.
```
