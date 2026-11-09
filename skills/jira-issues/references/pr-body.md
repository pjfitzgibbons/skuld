CIAM-<n>.

<One short paragraph: what the PR makes true, and the problem it fixes. Plain present tense.>

## <A change, named as the reader would look for it>

<What it does now, and any limit or choice that isn't obvious. Name code in backticks: `shipped()`, `pr <KEY>`, file paths.>

## Failure modes

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| <A step that writes to git, Jira, GitHub or a file another window reads> | | | | | |

## Tests

- <Each suite run, and its result: `npm test` in `plan/` (or `node --test`), `uv run pytest`.>
- <What has no test, and why.>
- Any mechanical coverage check a reviewer wrote for this PR, and its output, belongs with the
  reviewer's disposition (GUIDE.md §5, "Review: co-signing and dispositions"), not here.

<!--
Rules (delete this comment):
- The body opens with <KEY>. on its own line, naming the issue the PR is for. The title (from the
  commit subject) ends (<KEY>), e.g. `Add X (CIAM-123)`.
- Jira's dev panel links the PR to the issue automatically from the branch name and PR title
  containing the key; nothing here writes a Jira field.
- One section per change a reviewer would look for. Drop Failure modes when nothing writes
  (docs/failure-modes.md has the columns).
- No attribution lines.
- This body is the author's side. How the reviewer co-signs and records a per-file disposition is
  GUIDE.md §5, "Review: co-signing and dispositions" — not repeated here.
-->
