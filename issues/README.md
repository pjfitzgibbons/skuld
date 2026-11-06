# jira-plan issues

Work order: each file's `rank:` frontmatter field, lowest first; unranked issues sort last, by
issue number. The reason for an issue's rank (or for staying unranked) lives in that issue's own
`## Rank` section. `rank:` only orders JP issues among themselves -- it never touches Jira's own
order.

A JP issue can also be slotted in among Jira issues with `after: <JIRA-KEY>` (e.g.
`after: CIAM-163`): in `plan list`/the picker's merged view (`lib/cli.py`'s `mergeRows()`), it
then sorts right after that Jira issue, instead of at the end with the rest of the JP issues.
Jira's own issues are never reordered or re-sorted -- they keep whatever order the JQL's `ORDER
BY Rank` returned. A JP issue falls back to the end (in its normal `rank:`/number order) when it
has no `after:`, when its anchor isn't a Jira key currently in the picker's list (closed,
unassigned, or filtered out by the JQL), or when its anchor is a JP key -- only a Jira key can
anchor a placement.

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
rank:               # optional int; orders JP issues among themselves. Lower first; unranked issues
                    # sort last, by issue number. Go up in steps of 100 (100, 200, 300, ...) so a
                    # new issue can take a value in the gap between two existing ones (e.g. 250,
                    # between 200 and 300) without renumbering the rest.
after:              # optional Jira key, e.g. CIAM-163. Sorts this JP issue right after that Jira
                    # issue in the picker's merged list, instead of at the end. Falls back to the
                    # end (in rank/number order) if absent, if the key isn't a Jira issue currently
                    # in the picker's list, or if it's a JP key.
---
```

then a `# Title`, what's wrong or wanted, and where it lives (`file:line`).
When an issue is done, set `status`/`closed` and add a line saying what fixed
it (commit or file). List the open ones:

```
grep -l '^status: open' issues/JP-*.md
```

Issues for one GUIDE section:

```
grep -l '^guide:.*§4\.4' issues/JP-*.md
```

**User decision 2026-11-05:** `plan` treats `JP-<n>` as issues too
(`lib/fileissues.py`), so this file format is also what
`start`/`ship`/`done`/`view`/the picker read and write. Start sets `status:
in-progress` itself; `done`/`wontfix` is always set by a human, never by
`plan`. Every dated note Start or Done would otherwise post as a Jira comment
is appended instead as a `- YYYY-MM-DD: <text>` line under a `## Log` heading
at the end of the file (added once, if not already there):

```
## Log
- 2026-11-02: Started on `feat/JP-2-a-task-already`, in /repo.worktrees/JP-2.
```
