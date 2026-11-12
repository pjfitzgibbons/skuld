# Review round {{ROUND}} of {{KEY}}'s PR #{{PR}}

Before anything else: this review is for the worktree `{{ROOT}}`. If your working directory isn't
`{{ROOT}}`, stop and say so; do nothing else.

You are reviewing PR #{{PR}} of {{KEY}} in this worktree again. The review is co-signing, not an
adversarial pass: you and the author vouch together that what you read is correct and ready to
ship. This round checks only what isn't settled, in the diff since the last reviewed head. Run exactly
this call:

`{{CALL}}`

{{SCOPE}}Then apply the rules below on top of its findings. The diff is `git diff {{RANGE}}`. HEAD is {{HEAD}}. Nothing settled is read again.

## Unsettled findings from the last record

{{UNSETTLED}}

## Files to read

{{FILES}}

## Files marked OK (blob SHA); an OK file whose blob is unchanged is not read again

{{OK_FILES}}

## Rules

1. Check each unsettled finding in context: its `file:lines`, its summary, the fix, and the code one
   and two steps out from the change (callers, symbols, docs). Mark it `"Fixed"` when it held, or
   `"New"` when it's back. A recommended fix the author applied verbatim is `"Fixed"` without
   further check. For a finding marked `"New"`, drop its `commentId`, so it gets a new comment.
2. Do not re-read a file marked OK whose blob is unchanged (`git rev-parse HEAD:<path>` against the
   blob above).
3. Raise a new finding (`"disposition": "New"`, no `commentId`) only for a silent loss or a crash in the
   changed code. Nothing else is a finding in a round.
4. A finding in the same area as an earlier one is grouped with it under a named cause, in its
   `summary`. When a cause repeats a third time, write a Python check script in
   `{{DIR}}/` instead of finding another instance by hand, and put its path and output
   in `checks`.
5. Do not commit, push, reply to a review thread or resolve one. Do not edit the code under
   review. You post findings only through the last step below.
6. Copy every finding the call reports into `{{RECORD}}`, however it was reported: the `code-review`
   skill can report through the ReportFindings tool instead of as text. Skip none.
7. Append a line to `{{LOG}}` as you finish each step, with `echo "..." >> {{LOG}}`.

## Last steps

1. Write `{{RECORD}}` as JSON in the shape of the last review, `{{LAST_RECORD}}` (read it): `head`
   `{{HEAD}}`, `base` `{{BASE}}`, `round` {{ROUND}}, `dispositions` with every file's entry (an
   unchanged OK file keeps its earlier one), `findings` with every earlier unsettled finding and
   every new one, each with its `disposition` (`"open"`, `"Fixed"` or `"New"`) and its `commentId`
   when it keeps its comment, and `checks`. Finding keys stay `file:lines:classification`, and each
   finding has a `summary`.
2. Run `python3 {{SCRIPTS}}/review.py finish {{KEY}}`. It posts the findings that have no comment
   as one PR review and writes the review. If it refuses the record, fix what it names and run it
   again.
