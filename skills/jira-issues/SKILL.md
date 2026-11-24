---
name: jira-issues
description: Work Jira issues and their PRs the way Peter's repos expect. Issues are Jira's (`<KEY>` at https://cryoport.atlassian.net, project CIAM, Jira Plan GUIDE.md); GitHub Issues are not used, and PRs are the one GitHub integration. Use when picking up, drafting or checking an issue, when writing a commit message or PR body, and when answering a PR review. Triggers on "work on CIAM-123", "pick up CIAM-123", "draft an issue", "create the ticket", "check the outline", "open the PR", "reply to the review".
---

# jira issues

Adapted from GitHub's `github-issues` skill ([github/awesome-copilot](https://github.com/github/awesome-copilot/tree/main/skills/github-issues), MIT), rewritten around Jira Cloud issues and the outlines in `~/.claude/agents/technical-writer.md`. Ported from the Redmine-based `gh-issues` skill for Jira Plan's terminal frontend.

An issue is a Jira issue, `<KEY>` (e.g. `CIAM-123`), its description in the issue outline (stored as ADF, Jira's native rich-text format). GitHub Issues are not used. The skill lives in `~/.claude/skills/jira-issues/`, outside every repo, so every repo uses the one skill. The scripts are stdlib Python and shell, plus `~/.claude/skills/jira-markdown-adf/md_to_adf.py` for Markdown→ADF conversion (called, never copied). Claude runs only the ones that read; every commit, push, `gh` write and Jira write happens on a step the human runs: Start, Ship or Done in `plan`, or `pr`.

## Rules

1. **Read the issue before you write about it.** `jira-get /rest/api/3/issue/<KEY>` (or `?expand=renderedFields` for a plain-text rendering of the ADF description), or `?expand=changelog` for its history. Never write an issue's description from memory.
2. **Claude stages the process; the human verifies and executes it.** Claude writes the files in `.tmp/<KEY>/` (the commit message, the PR body, review replies, the ticked description) for Peter to review; Ship sends them with `pr`. Never `git commit`, `git push`, `gh pr create` or a Jira write on Claude's own. **Never transition a Jira issue to a terminal status (Done/Closed/Cancelled/Resolved/...) from a script or command — that happens only in the Jira UI, by Peter's hand.** `.claude/hooks/no_terminal_jira_transition.py` enforces this.
3. **Branches are `<type>/<KEY>-<slug>`**, `feat/`, `fix/` or `chore/`, off the default branch. Start makes the branch and its worktree. Everything reaches the default branch by PR.
4. **The commit subject is the PR's title**, and ends `(<KEY>)` — e.g. `Add X (CIAM-123)`. `gh pc` titles the PR with the branch's first commit subject, the squash merge keeps it, and Jira's dev panel links the PR to the issue automatically because the branch name and PR title both contain the key — no Jira field write is needed or made. The PR body always opens with the same line, `<KEY>.`, naming the issue it's for.
5. **PRs get a body file, never `--fill`.** `gh pr create --fill` copies the commit message into the PR, including the `Assisted-by:` trailer, and PR descriptions carry no attribution. Start from [references/pr-body.md](references/pr-body.md).
6. **Look it up, don't guess.** If a gh flag isn't familiar, run `gh <cmd> --help` before saying it doesn't exist or flagging it unverified.
7. **Quote `[]` in zsh.** `-f 'labels[]=bug'`, never bare: zsh treats `[]` as a glob and fails with "no matches found".

## Aliases and commands

`gh pc` and `gh pb` are gh aliases over `bin/ghbody`. Each takes an optional Jira key and otherwise reads it from the branch name (`fix/CIAM-88-go-requests-copilot-s-review` → `CIAM-88`). `pr`, `ghbody`, `new_issue` and `jira-get` are in `~/.local/bin/`; `pr`, `ghbody` and `jira-get` link to skuld's `bin/`.

| Command | Does |
|---|---|
| `gh pc [KEY]` | `gh pr create`, titled with the branch's first commit subject, body `.tmp/<KEY>/pr-body.md`; asks Copilot to review it when `reviewer` is `copilot` |
| `gh pb [KEY]` | `gh pr edit` on this branch's PR with `.tmp/<KEY>/pr-body.md` |
| `pr [KEY]` | What Ship runs: commits with `.tmp/<KEY>/commit.txt`, pushes, opens or updates the PR, prints its URL, then asks the reviewer the machine config's `reviewer` names and prints which. `copilot` (the default): asks Copilot on a new PR and when the push moved the branch. `claude`: when a review is due by Claude's review record (HEAD isn't the head the last round reviewed, and no unfinished round was requested for it), starts Claude's review of the PR in the plan window's `Review <KEY>` terminal (`plan review <KEY>`). Then it sends the replies and ticks (`plan review <KEY> --restart` writes an unfinished round for an older HEAD again, once its reviewer's terminal is closed) |
| `pr done [KEY]` | After the merge: sends any replies and ticks left, then says to press Done in `plan`, which cleans up and tells you to close the issue in Jira. Bare `pr` does the same once the PR has merged |

`gh pc` and `gh pb` refuse a body containing `Assisted-by` or `Co-authored-by`. `GHBODY_DRY=1` prints the command instead.

Jira reads for picking an issue or checking its state go through the `jira` CLI's read-only verbs (`jira issue view`, `jira issue list`) or direct REST GETs via `jira-get` — never through `jira issue move`/`transition`, which is denied.

## Picking up an issue

Person presses **Start** on the issue's row in `plan`'s Next up list. It makes the branch and its worktree, transitions the issue to "In Progress" (by looking up the transition by its target status name, never a hardcoded id, and reading the issue back to confirm the move took), and opens the worktree in the issue's own VS Code window, with the issue in one tab and Claude Code in a terminal beside it. Then, before any code:

1. **Read the issue**, with `jira-get /rest/api/3/issue/<KEY>`.
2. **Check its description** against the issue outline, Task, Guardrails, Lenses and Done, plus whatever the repo's `docs/agents/technical-writer.md` adds. Save a plain-text/Markdown rendering of the description to a file and run, from inside the repo so it finds that file:
   ```
   python3 ~/.claude/skills/jira-issues/scripts/outline.py <file>
   ```
   Done must be at least two pass/fail checkboxes, and each `[x]` lens needs a Done condition that names it (`- [ ] L6: …`). If the description doesn't conform, show Peter the rewrite in chat; he makes it in Jira. An issue in the old outline (Summary, Scope, Acceptance, Tests, Lenses) is rewritten now: Summary and Scope become Task, out-of-scope lines become Guardrails, Lenses stays, and Acceptance and Tests become Done's conditions.
3. **Code once the description conforms.** When the code resists the change (a conflict, a failing invariant, a design that doesn't fit), find and state the root cause before writing code, and fix the cause; don't write code that goes around it. A fix that adds a layer, flag, special case or parallel path to get past a conflict names the root cause it addresses in the PR body.

## Shipping

When the work is done and tested, run `/ponytail:ponytail-review` on the uncommitted changes (it lists Must fix, Should fix and Nice to have, and lines that could go, and changes no code) and act on its Must fix and Should fix, or say in the PR body why not. Then write `.tmp/<KEY>/commit.txt` (ending `Assisted-by: Claude:<model>`, subject ending `(<KEY>)`) and `.tmp/<KEY>/pr-body.md`, plus any replies and ticks below. Person reviews them in the issue window and presses **Ship**, which checks them and runs `pr <KEY>` in one of its terminals. Jira's dev panel picks up the PR automatically from the branch name and PR title; nothing writes a Jira field for it. Ship again runs `pr` again, for the next round. After the merge, **Done** cleans up the worktree/branch, posts an ADF comment summarizing the work, and prints "Close `<KEY>` in Jira" with the browse URL — it never transitions the issue itself.

## Drafting a new issue

1. Copy `references/jira-issue.md` to `.tmp/<KEY>/<slug>.md`, `<KEY>` being the issue whose work found it. Fill in the front matter by the names Jira shows: `project` (defaults to `CIAM`), `type` and `summary` are required. A blank field is left out. Name issues by key, `CIAM-79` or `79` (resolved against `project`), with commas between them in `blocked_by` and `relates`. Then write the body in the outline.
2. Check it, from inside the repo so the repo's lenses count. This only reads: it runs the outline check, looks up every name in Jira and checks every value.
   ```
   new_issue --check .tmp/<KEY>/<slug>.md
   ```
3. Give Peter the same command without `--check`, as `! new_issue .tmp/<KEY>/<slug>.md`. It creates the issue (via CIAM's required-fields helper, `~/.claude/skills/jira-ciam-create/create-ciam-issue.sh`, when `project` is `CIAM`), adds its issue links and reads it back. A second run finds the issue by its summary and adds only the relations still missing.

## Answering a PR review

How the reviewer co-signs, records a disposition per file, and resolves a verbatim-applied fix
immediately is GUIDE.md §5, "Review: co-signing and dispositions." This section is the author's
side of that same round. With `reviewer: claude` in the machine config (`copilot` is the default; GUIDE.md §5), the review is Claude's own, started by `pr` when the review record says one is due
(GUIDE.md §5, "Claude's review"): it posts its findings on the PR, and keeps its record in the next round's folder, `.tmp/<KEY>/rounds/<n>/review.json`. Findings on lines of the PR's diff are review threads, which `pr_threads.py`
lists and `pr` answers like any other. A finding on a file's lines outside the diff is a file-level
comment, also a thread. A finding on a file outside the PR goes in the review's body: it is not a
thread, never appears in `pr_threads.py` or `--drafts`, and gets no reply in `replies/`; it is
answered in the next round's review record. The author
doesn't write that file, and doesn't ask Copilot or anyone else to review: `pr` asks the reviewer the config names.

When a Claude is handed a review request (`<KEY> review: read <path of request.md> and do the review it
describes.`), it follows request.md and nothing else: it never commits, pushes, replies
to a thread or resolves one, and ends with `review.py finish <KEY>`.

The PR's declared scope binds every review: the ticket's `## Task` and `## Guardrails`, and the author's notes, one file, `.tmp/<KEY>/notes.md`, beside the `rounds/` folder (never inside a round's folder). Create and edit it whenever you like; request.md names its path, and the reviewer reads it at the start of every review, one already requested included, so a line written after the request still reaches the review. A finding the Guardrails or the notes rule out comes back `"OutOfScope"`, its `scopeRule` quoting that line, and is settled; a reply that only explains or defers settles nothing. A round raises new findings in its diff and in files up to two steps out from it (callers, dependents, symbols, docs) whose behaviour its change materially changes or breaks; a break further out, or out of scope, is in the review's body as an advisory (`"kind": "break"`, no lens), with nothing to answer; so is each finding that became `OutOfScope` this round, with its `scopeRule`: its thread needs no answer.

Fix the findings first. When the code resists a fix, find and state its root cause before writing code, and fix the cause; a fix that adds a layer, flag, special case or parallel path to get past a conflict states the root cause it addresses in its reply. Then run `/ponytail:ponytail-review` on the uncommitted changes and act on its Must fix and Should fix, or say in the PR body why not. Then write each open thread's context into `.tmp/<KEY>/replies/`, one `<comment id>.md` each, and write the reply below it: "Fixed: …" saying what changed, or why not. The context, the path and line, the review comment and the last lines of the diff hunk it's on, is an HTML comment at the top, so Peter reads each reply against what it answers; `pr` strips it before posting. Run `--drafts` again after writing replies without it, and it adds the context above them.

```
helpers=$(dirname "$(realpath ~/.local/bin/pr)")/pr-helpers    # skuld's bin/pr-helpers, through the pr link
python3 $helpers/pr_threads.py 73          # open threads: comment id, path:line, first line
python3 $helpers/pr_threads.py 73 --drafts .tmp/<KEY>/replies
```

A file with no reply below its context stops `pr` before the first post; delete the file for a thread that gets no reply.

`pr`, after its push, posts each reply to its thread, resolves the thread and deletes the file, so the replies describe code that's on the PR (`pr_threads.py 73 --post .tmp/<KEY>/replies`). It checks every file before the first post, and a reply the thread already has is not posted again. The replies open for review beside `commit.txt` and `pr-body.md`.

## Ticking the issue's Done list

When a round meets Done conditions, write `.tmp/<KEY>/issue.md`: a plain-text/Markdown rendering of the issue's description as Jira has it now, with those boxes ticked. Change nothing else. `pr` applies it after its push (`checklist.py <KEY> .tmp/<KEY>/issue.md`) only when checkbox state is the one difference — it converts the file to ADF (via `md_to_adf.py`, never a hand-reimplemented converter), PUTs the description, verifies the PUT stuck by re-GETting it, and posts a comment listing each box, then deletes the file. Any other difference, such as an edit made in Jira since, stops `pr` naming the line, and the file is kept.

## Tests

In jira-plan, `uv run pytest` runs them with the rest. Alone:

```
cd ~/.claude/skills/jira-issues/scripts && python3 -m unittest test_scripts
```

The tests of `pr` and `ghbody` are `bin/test_pr.py`, and of the review script `bin/pr-helpers/test_review.py`:

```
cd <skuld>/bin && python3 -m unittest test_pr
```
