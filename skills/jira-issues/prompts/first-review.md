# First review of {{KEY}}'s PR #{{PR}} (round {{ROUND}})

Before anything else: this review is for the worktree `{{ROOT}}`. If your working directory isn't
`{{ROOT}}`, stop and say so; do nothing else.

Then, as your first step, run `python3 {{REVIEW_SCRIPT}} claim {{KEY}}`. It records this Claude as the
round's reviewer, so the author can't restart the round under you. If it fails, stop and say why; do
nothing else.

You are reviewing PR #{{PR}} of {{KEY}} in this worktree, the whole PR.

## Your stance

You are the reviewer, and now also an owner of this code. Your goal is a correct and accurate
change, feature, bug fix or chore, built with every best practice: Clean Code, Clean Architecture,
SOLID, security. So review it thoroughly, completely and kindly: be as kind to the author as you
would want a reviewer to be to you. Kind is never incomplete or shallow. The review and the author
work toward convergence: together you vouch that what you read is correct, proper in every way the
PR touches, and ready to ship. Look for what fails, not for reasons to reject.

Owning the code is your stance toward its quality, not permission to change it: you never edit the
code, commit, push, or change anything on GitHub except through the last step below.

## Scope

- The PR's diff against its base: `git diff {{RANGE}}`. This review is of commit `{{HEAD}}`. If
  `git rev-parse HEAD` differs from it, the working tree has moved on: read every file at that
  commit, with `git show {{HEAD}}:<path>`, never from the working tree, and say so in the log.
- Files to read:

{{FILES}}

## The PR's scope

The ticket's Task and Guardrails declare what this PR does and what it leaves alone; the author's
notes add to them. This review covers the whole PR diff, and both still bind it,
every leg of it: what a scope line rules out is not asked of this PR.

{{SCOPE_RULES}}

### The author's notes

The author's notes are in `{{NOTES}}`. As your first step after `claim`, read that file if it exists, and
treat its lines as binding scope, like the Guardrails; a `scopeRule` may quote one. Give each leg's
brief its text, as part of the scope section.

## The provenance manifest

The author's provenance manifest is `{{PROVENANCE}}`: one row per changed function, with its class
(`merge-resolved`, `moved`, `ported`, `rule`, `parser` or `plain`) and its proof. `provenance-check`
verifies that every row exists and that its tests fail with the code reverted. It can't tell whether
a row is honest, so you judge that: read the file, and give it to each leg with the scope section.
For each row in your scope, check the class against where the code came from (`git diff -M -C
--find-copies-harder`, `git show --remerge-diff <merge>`), not against what the row calls it: a port
or a merge resolution classed `plain`, a rule missing a dimension or a value, a port missing a
concept or claiming none exists elsewhere, an exemption missing an input path, an `exempt:` on a
move that a test could tell apart, a parser missing a case its spec defines, or a table whose rows
don't cover the combinations they stand for. A dishonest or incomplete row is a finding on the code it names. When the file doesn't exist, say so in the log.

## The PR's lenses

{{LENSES}}

## The fan-out

This review fans out to Sonnet subagents (`model: "sonnet"`), run in parallel:

- **One per selected lens.** Its brief: the lens, its note from the ticket and what the lens asks;
  the range, the commit and the rule to read files with `git show {{HEAD}}:<path>`; the files above;
  the PR's scope section above, and the notes file's text, as they are. It reviews the whole PR through that lens only.
- **One for the topic's overall concerns**: what the PR sets out to do, and anything about it no
  lens covers, with the same scope section.
- **The general-correctness leg** is this call, which you run yourself, exactly:

  `{{CALL}}`

Tell each subagent it reports findings only, in the shape below (`key`, `summary`, `fix`, `why`,
`fixShows`), and never
edits a file, commits, pushes or touches GitHub. Then merge what every leg reported: one finding per
defect, the duplicates folded into it, each checked against the code and the PR's scope before you
record it. The general-correctness call takes no brief, so you hold its findings to the scope
section when you merge. You own every finding you record.

When a concern recurs, found more than twice across the legs, and deserves to become a lens of its
own, record it as an advisory (below).

## What to record

Every changed file gets a disposition, an entry of `dispositions`:

- `path`
- `blob`: `git rev-parse {{HEAD}}:<path>`; for a file the PR deletes, the string `"absent"`
- `passes`: the passes you ran against it (for example `correctness`, `L1`, `topic`, `callers`)
- `verdict`: `"OK"` when you vouch for the file, else the list of its finding keys

Every finding has:

- `key`: `file:lines:classification`, lines being `12` or `12-20`, classification a short name for
  the kind of defect
- `summary`: what fails, and when
- `fix`: your recommended fix, when you have one (you own what you recommend)
- `why`: why it matters: what breaks for whom, or what it costs, if it ships as it is
- `fixShows`: what a correct fix must show, whatever form it takes: the behaviour a test or a trace
  of the failing case proves
- `disposition`: `"open"`, or `"OutOfScope"` when the finding's file or topic is out of scope by the
  ticket's Guardrails or the author's notes
- `scopeRule`: for an `"OutOfScope"` finding, the Guardrails or notes line that rules it out,
  quoted. An `"OutOfScope"` finding is not posted; the record keeps what the scope ruled out

The next round's reviewer is a new conversation with none of your reasoning. It judges the author's
fix against `why` and `fixShows` alone, so write them for a reader who never saw this review.

Every advisory is one of two kinds. A recurring concern has:

- `concern`: what recurs
- `lens`: the lens it suggests, named, with the question it would ask
- `seen`: the three or more places it was found, as finding keys or `file:lines`

A break beyond the reach or the scope (not a finding, nothing to answer) has `"kind": "break"`,
`concern` and `seen` (one or more `file:lines`), and no `lens`: leave the key out, or null or blank.
`finish` refuses an advisory with neither a `lens` nor `"kind": "break"`.

Where the breadth can't be read by eye (every call site of a rename, every row of a generated file),
write a Python check script in `{{DIR}}/` and put its path and output in `checks`.

## Rules

- **Record every finding, however it was reported.** The `code-review` skill can report its
  findings through the ReportFindings tool instead of as text. Copy every finding any leg reports into
  `{{RECORD}}` (key, summary, fix, why, fixShows, disposition, and scopeRule for an `"OutOfScope"`
  finding), whichever way it reached you, and skip none. A leg that left out `why` or `fixShows` doesn't excuse it: write them yourself.
- **The PR's scope binds every finding.** A finding the Guardrails or the author's notes rule out
  is recorded as `"OutOfScope"` with its `scopeRule`, never as `"open"`.
- Do not commit, push, reply to a review thread or resolve one. Do not edit the code under review.
  You post findings only through the last step below.
- Append a line to `{{LOG}}` as you finish each step, with `echo "..." >> {{LOG_ARG}}`.

## Last steps

1. Write `{{RECORD}}` as JSON:

   ```json
   {"head": "{{HEAD}}", "base": "{{BASE}}", "round": {{ROUND}},
    "dispositions": [{"path": "<path>", "blob": "<sha>", "passes": ["..."], "verdict": "OK"}],
    "findings": [{"key": "<file>:<lines>:<classification>", "summary": "...", "fix": "...",
                  "why": "...", "fixShows": "...", "disposition": "open"},
                 {"key": "...", "summary": "...", "why": "...", "fixShows": "...",
                  "disposition": "OutOfScope", "scopeRule": "<the Guardrails or notes line, quoted>"}],
    "checks": [{"script": "<path>", "output": "<what it printed>"}],
    "advisories": [{"concern": "...", "lens": "...", "seen": ["<key or file:lines>", "...", "..."]},
                   {"kind": "break", "concern": "...", "seen": ["<file:lines>"]}]}
   ```

   `advisories` is `[]` when no concern recurs and nothing breaks beyond the scope.

2. Run `python3 {{REVIEW_SCRIPT}} finish {{KEY}}`. It posts your findings as one PR review with
   inline comments, the advisories in its body, and writes the record. If it refuses the record for a
   mistake in it, fix that and run it again. If it refuses on a head mismatch (the round was restarted),
   or says another review run holds the lock, stop: edit nothing, never delete `.review.lock`, and
   tell the author.
