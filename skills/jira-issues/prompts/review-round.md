# Review round {{ROUND}} of {{KEY}}'s PR #{{PR}}

Before anything else: this review is for the worktree `{{ROOT}}`. If your working directory isn't
`{{ROOT}}`, stop and say so; do nothing else.

Then, as your first step, run `python3 {{REVIEW_SCRIPT}} claim {{KEY}}`. It records this Claude as the
round's reviewer, so the author can't restart the round under you. If it fails, stop and say why; do
nothing else.

You are reviewing PR #{{PR}} of {{KEY}} in this worktree again.

## Your stance

You are the reviewer, and now also an owner of this code. Your goal is a correct and accurate
change, feature, bug fix or chore, built with every best practice: Clean Code, Clean Architecture,
SOLID, security. So review it thoroughly, completely and kindly: be as kind to the author as you
would want a reviewer to be to you. Kind is never incomplete or shallow. The review and the author
work toward convergence: together you vouch that what you read is correct and ready to ship.

Owning the code is your stance toward its quality, not permission to change it: you never edit the
code, commit, push, or change anything on GitHub except through the last step below.

## Scope: this round's changes only

This is not a review of the whole PR again. Be thorough about the changes made since the last
reviewed head: check each against the finding it answers, whether it fixes that finding, and
whether it breaks something that held before. Nothing settled is read again.

{{SCOPE}}The diff is `git diff {{RANGE}}`. This review is of commit `{{HEAD}}`. If `git rev-parse HEAD`
differs from it, the working tree has moved on: read every file at that commit, with
`git show {{HEAD}}:<path>`, never from the working tree, and say so in the log.

## The PR's scope

The ticket's Task and Guardrails declare what this PR does and what it leaves alone; the author's
notes for this round add to them. Both bind this review, every leg of it, and override an earlier
finding's `fixShows`: what a scope line rules out is not asked of this PR, whatever `fixShows` says.

{{SCOPE_RULES}}

### The author's notes for this round

{{AUTHOR_NOTES}}

## Unsettled findings from the last record

Each carries the reasoning of the reviewer who raised it, who is not this conversation: why it
matters, and what a correct fix shows. A finding from a record older than that reasoning has its
summary only.

{{UNSETTLED}}

## Files to read

{{FILES}}

## Files marked OK (blob SHA); an OK file whose blob is unchanged is not read again

{{OK_FILES}}

## The PR's lenses

{{LENSES}}

## The fan-out

This round fans out to Sonnet subagents (`model: "sonnet"`), run in parallel, each scoped to this
round's diff and the files above:

- **One per selected lens.** Its brief: the lens, its note from the ticket and what the lens asks;
  the range, the commit and the rule to read files with `git show {{HEAD}}:<path>`; the files above;
  the unsettled findings; the PR's scope section above, as it is. Through that lens it checks
  whether each change fixes its finding, and what the change breaks.
- **One for the topic's overall concerns**, over the same diff and findings, with the same scope
  section.
- **The general-correctness leg** is this call, which you run yourself, exactly:

  `{{CALL}}`

Tell each subagent it reports findings only, in the record's shape (`key`, `summary`, `fix`, `why`,
`fixShows`), and
never edits a file, commits, pushes or touches GitHub. Then merge what every leg reported under the
rules below: one finding per defect, the duplicates folded into it, each checked against the code
and the PR's scope before you record it. The general-correctness call takes no brief, so you hold
its findings to the scope section when you merge. You own every finding you record.

When a concern recurs, found more than twice across the legs, and deserves to become a lens of its
own, record it as an advisory: `concern` (what recurs), `lens` (the lens it suggests, named, with
the question it would ask), and `seen` (the three or more places it was found, as finding keys or
`file:lines`).

## Rules

1. Judge each unsettled finding from three things: its reasoning above (`why`, `fixShows`), the
   author's reply in its review thread (`gh api repos/{owner}/{repo}/pulls/{{PR}}/comments`, the
   replies under its `commentId`), and the part of the diff that answers it, all under the PR's
   scope above. Check the fix in context, to judge whether it holds: its `file:lines` and the
   round's reach, the code one and two steps out from the change (callers, dependents, symbols,
   docs the changed code feeds; rule 3).
   Mark it `"Fixed"` when the fix shows what `fixShows` asks; `"OutOfScope"` when the finding's
   file or topic is out of scope by the ticket's Guardrails or the author's notes, with
   `scopeRule` quoting the line that rules it out; or `"New"` when it is neither, or the finding
   is back. A reply that only explains, defers or files a ticket doesn't
   fix it; a reply that points to a Guardrails or notes line ruling the finding out does, as
   `"OutOfScope"`, when the line says what the reply claims. A finding without `fixShows` is judged
   from its summary and fix. A recommended fix the author applied verbatim is `"Fixed"` without
   further check. For a finding marked `"New"`, drop its `commentId` and its `reviewId`, so it gets
   a new comment, and rewrite `why` and `fixShows` when the fix taught you what they missed.
2. Do not re-read a file marked OK whose blob is unchanged (`git rev-parse {{HEAD}}:<path>` against the
   blob above). A file listed as deleted, or no longer in the PR, gets a disposition whose blob is
   `"absent"`, and a verdict like any other file.
3. Raise a new finding (`"disposition": "New"`, no `commentId` or `reviewId`, with its `why` and
   `fixShows` written as the first review writes them) only for what this
   round's changes introduce: a regression (something that held at the last reviewed head and fails
   now), a silent loss or a crash. The round's reach is its diff, `git diff {{RANGE}}`, and the
   files one and two steps out from it: the callers, dependents, symbols and docs the changed code
   feeds. A new finding anchors on a line inside the diff, or in a file one or two steps out when
   this round's change materially changes that file's behaviour or breaks it: a fix in the diff
   can change what dependent code does. Two steps is the limit. The PR's scope still binds the
   reach: a break in a file the Guardrails or the author's notes rule out, or one further than two
   steps out, is not a finding but an advisory without a lens: `concern` (what breaks) and `seen`
   (its `file:lines`).
4. A finding in the same area as an earlier one is grouped with it under a named cause, in its
   `summary`. When a cause repeats a third time, write a Python check script in
   `{{DIR}}/` instead of finding another instance by hand, and put its path and output
   in `checks`.
5. Do not commit, push, reply to a review thread or resolve one. Do not edit the code under
   review. You post findings only through the last step below.
6. Copy every finding any leg reports into `{{RECORD}}`, however it was reported: the `code-review`
   skill can report through the ReportFindings tool instead of as text. Skip none.
7. Append a line to `{{LOG}}` as you finish each step, with `echo "..." >> {{LOG_ARG}}`.

## Last steps

1. Write `{{RECORD}}` as JSON in the shape of the last review, `{{LAST_RECORD}}` (read it): `head`
   `{{HEAD}}`, `base` `{{BASE}}`, `round` {{ROUND}}, `dispositions` with every file's entry (an
   unchanged OK file keeps its earlier one), `findings` with every earlier unsettled finding and
   every new one, each with its `disposition` (`"open"`, `"Fixed"`, `"OutOfScope"` with its
   `scopeRule`, or `"New"`) and its `commentId` when it keeps its comment, `checks`, and
   `advisories` (`[]` when no concern recurs and nothing breaks outside the diff or scope). Finding keys
   stay `file:lines:classification`, and each finding has a `summary`. Copy each earlier finding's
   `why` and `fixShows` as they are (or as rule 1 rewrote them); the next round's reviewer reads
   them, not this conversation.
2. Run `python3 {{REVIEW_SCRIPT}} finish {{KEY}}`. It posts the findings that have no comment
   as one PR review, with the advisories in its body, and writes the review. If it refuses the record
   for a mistake in it, fix that and run it again. If it refuses on a head mismatch (the round was
   restarted), or says another review run holds the lock,
   stop: edit nothing, never delete `.review.lock`, and tell the author.
