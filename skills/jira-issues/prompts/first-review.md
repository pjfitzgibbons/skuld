# First review of {{KEY}}'s PR #{{PR}} (round {{ROUND}})

Before anything else: this review is for the worktree `{{ROOT}}`. If your working directory isn't
`{{ROOT}}`, stop and say so; do nothing else.

You are reviewing PR #{{PR}} of {{KEY}} in this worktree. Run exactly this call:

`{{CALL}}`

The review is co-signing, not an adversarial pass: you and the author vouch together that what
you read is correct, proper in every way the PR touches, and ready to ship. Look for what fails,
not for reasons to reject.

## Scope

- The PR's diff against its base: `git diff {{RANGE}}`. HEAD is {{HEAD}}.
- Files to read:

{{FILES}}

## What to record

Every changed file gets a disposition, an entry of `dispositions`:

- `path`
- `blob`: `git rev-parse HEAD:<path>`
- `passes`: the passes you ran against it (for example `correctness`, `callers`, `docs`)
- `verdict`: `"OK"` when you vouch for the file, else the list of its finding keys

Every finding has:

- `key`: `file:lines:classification`, lines being `12` or `12-20`, classification a short name for
  the kind of defect
- `summary`: what fails, and when
- `fix`: your recommended fix, when you have one (you own what you recommend)
- `disposition`: `"open"`

Where the breadth can't be read by eye (every call site of a rename, every row of a generated file),
write a Python check script in `{{DIR}}/` and put its path and output in `checks`.

## Rules

- **Record every finding, however it was reported.** The `code-review` skill can report its
  findings through the ReportFindings tool instead of as text. Copy every finding it reports into
  `{{RECORD}}` (key, summary, fix, disposition), whichever way it reached you, and skip none.
- Do not commit, push, reply to a review thread or resolve one. Do not edit the code under review.
  You post findings only through the last step below.
- Append a line to `{{LOG}}` as you finish each step, with `echo "..." >> {{LOG}}`.

## Last steps

1. Write `{{RECORD}}` as JSON:

   ```json
   {"head": "{{HEAD}}", "base": "{{BASE}}", "round": {{ROUND}},
    "dispositions": [{"path": "<path>", "blob": "<sha>", "passes": ["..."], "verdict": "OK"}],
    "findings": [{"key": "<file>:<lines>:<classification>", "summary": "...", "fix": "...", "disposition": "open"}],
    "checks": [{"script": "<path>", "output": "<what it printed>"}]}
   ```

2. Run `python3 {{SCRIPTS}}/review.py finish {{KEY}}`. It posts your findings as one PR review with
   inline comments and writes the record. If it refuses the record, fix what it names and run it
   again.
