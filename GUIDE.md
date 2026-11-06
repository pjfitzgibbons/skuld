<!-- markdownlint-disable MD013 -->
# Jira Plan: an engineering guide to `plan`, Ship and `pr`

This guide covers the developer workflow around Jira Plan: a terminal CLI (`plan`) that starts,
ships and finishes each issue; the shell scripts behind it (`pr` and its helpers); the bare-word
commands linked into `~/.local/bin`; and the Claude Code skill and hooks that let Claude do the
writing while a person does every write to git, GitHub and the tracker.

The tracker is Jira Cloud, `cryoport.atlassian.net`, project `CIAM`. This is a full port of
[GH Plan](https://github.com/), which did the same thing against a local Redmine with a VS Code
extension; everything that changed is Jira's API and status model and the switch from VS Code to a
terminal front end over Ghostty, Aerospace, tmux, fzf, nvim and lazygit. Everything else — the
button rule, the `.tmp/<KEY>/` contract, the attribution hook, the git/`gh` mechanics — carried
over unchanged.

## Contents

1. [The loop in one picture](#1-the-loop-in-one-picture)
2. [The pieces and where they live](#2-the-pieces-and-where-they-live)
3. [Setting it up](#3-setting-it-up)
4. [The terminal frontend](#4-the-terminal-frontend)
5. [The review round: `.tmp/<KEY>/`](#5-the-review-round-tmpkey)
6. [The shell side: `pr` and its helpers](#6-the-shell-side-pr-and-its-helpers)
7. [Claude's side: skills, CLAUDE.md, hooks](#7-claudes-side-skills-claudemd-hooks)
8. [Patterns worth copying](#8-patterns-worth-copying)
9. [Testing](#9-testing)
10. [Known gaps and rough edges](#10-known-gaps-and-rough-edges)
11. [Package index](#11-package-index)

---

## 1. The loop in one picture

```
   plan                          worktree <repo>.worktrees/CIAM-123        Jira CIAM-123
   ────                          ──────────────────────────────────        ─────────────
   (1) START ──────────────────▶ branch feat/CIAM-123-slug, off the base ▶ In Progress,
                                 Ghostty window + tmux session,            "Started" comment
                                 Claude Code pane and a shell pane
                                 Claude works · writes .tmp/CIAM-123/ files
   (2) SHIP ────────────────────▶ pr CIAM-123: commit, push, open/update PR ──▶ dev panel (native,
                                 review rounds: replies/, issue.md ──────▶    no field write)
                                                                           Done boxes ticked
   (3) squash merge on GitHub; the PR subject includes (CIAM-123)
   (4) DONE ───────────────────▶ worktree and branch removed, base up ───▶ comment posted;
                                                                            human closes CIAM-123
```

The whole design rests on one rule, unchanged from GH Plan: **Claude writes files, the human
presses the buttons.** Claude never commits, pushes, calls a `gh` write or writes to Jira. It
writes the commit message, the PR body, review replies and the ticked checklist into
`.tmp/CIAM-123/`. The human runs **Ship**, and `pr` sends them. So every outward write happens on a
button press, and the person who pressed it is the one who sends it.

The default branch carries the code, and everything reaches it by PR. Issue status lives in Jira,
and the only transition this tool ever makes is Start's move to "in progress" — **Done never
transitions the issue.** A human closes it by hand after reading Done's printed browse URL.

## 2. The pieces and where they live

| Piece | Lives in | Reference copy | What it does |
| --- | --- | --- | --- |
| `plan` CLI | this repo, `lib/`, `bin/plan` | — | fzf picker and the `list`/`start`/`ship`/`go`/`done`/`view` subcommands |
| `pr` | the jira-issues skill, `scripts/pr.sh` | `skills/jira-issues/scripts/pr.sh` | Commit, push, open or update the PR, post replies, apply ticks |
| `gh pc`, `gh pb` | gh aliases over `scripts/ghbody.sh` | `bin/gh-aliases.yml`, `…/ghbody.sh` | Create or edit the PR with the body from `.tmp/CIAM-123/pr-body.md` |
| `pr_threads.py` | the jira-issues skill | `…/scripts/pr_threads.py` | List review threads; draft, post and resolve replies |
| `checklist.py` | the jira-issues skill | `…/scripts/checklist.py` | Apply only checkbox changes to an issue's description, via ADF |
| `new_issue` | the jira-issues skill, linked into `~/.local/bin` | `…/scripts/new_issue.py` | Create an issue from a Markdown file with front matter |
| `outline.py` | the jira-issues skill | `…/scripts/outline.py` | Check an issue body against the house outline |
| jira-issues skill | `~/.claude/skills/jira-issues/` | `skills/jira-issues/` | Tells Claude how issues, commits, PRs and reviews are done here |
| Attribution hook | the repo, `.claude/hooks/` | `claude/hooks/` | Blocks a commit that names an AI as author or lacks `Assisted-by:` |
| Project settings | the repo, `.claude/settings.json` | `claude/settings.json` | Attribution trailer, allow/deny lists, the hook |
| Repo CLAUDE.md | the repo root | `claude/CLAUDE.md` | Boundaries, commands and gotchas Claude reads each session |

### Bare-word commands: `~/.local/bin`

`~/.local/bin` is on `PATH`. The scripts stay in the skill folder, and symlinks there let you type
them as bare words in any shell:

```sh
ln -s ~/.claude/skills/jira-issues/scripts/pr.sh        ~/.local/bin/pr
ln -s ~/.claude/skills/jira-issues/scripts/new_issue.py ~/.local/bin/new_issue
```

Two consequences, both carried over from GH Plan:

- **`pr` collides with `/usr/bin/pr`**, the Unix paginator. An interactive shell with
  `~/.local/bin` first in `PATH` finds the right one, but a login shell may not. The `plan` CLI
  runs `pr` **by path**, through the `pr` config key (default `~/.local/bin/pr`), never by name.
- **`pr.sh` finds its siblings through `realpath "$0"`**, so it works through the symlink.

## 3. Setting it up

macOS only as written. It needs `open` for Ghostty windows, `aerospace` for workspaces, `tmux`,
`fzf`, `nvim` and `lazygit`.

1. **Tools:** `git`, `gh` (authenticated: `gh auth login`), Python ≥ 3.10 on `PATH` as `python3`
   (stdlib only), plus Ghostty, Aerospace, tmux, fzf, nvim and lazygit (all assumed already
   installed, per this environment).
2. **API token in `~/.netrc`**, never in a file this repo tracks or an env var:

   ```
   machine cryoport.atlassian.net
     login peter.fitzgibbons@cryoport.com
     password <API token>
   ```

3. **The skill:** put `skills/jira-issues/` at `~/.claude/skills/jira-issues/` so every repo shares
   one copy.
4. **The links:** see [§2](#bare-word-commands-localbin).
5. **gh aliases:** `gh alias import bin/gh-aliases.yml`, or by hand:

   ```sh
   gh alias set --shell pc '~/.claude/skills/jira-issues/scripts/ghbody.sh pr-create "$@"'
   gh alias set --shell pb '~/.claude/skills/jira-issues/scripts/ghbody.sh pr-edit "$@"'
   ```

6. **The `plan` CLI**, from this repo's root:

   ```sh
   python3 -m unittest discover -s lib -t .
   ln -s "$(pwd)/bin/plan" ~/.local/bin/plan
   ```

7. **Config** at `~/.config/jira-plan/config.json` (every key documented in
   [`config/README.md`](config/README.md) if present, else inline in `lib/config.py`): chiefly
   `jql`, `checkouts` (maps a Jira project key to a repo path, or a **list** of them, since several
   repos can serve one project — a bare string is still accepted as a one-item list; it's only a
   *fallback*, consulted when an issue's work isn't already found in some other listed repo and
   cwd isn't inside a git repo at all — see [§4.4 step 2](#44-start-and-open) and
   [`docs/architecture.md` §7.0](docs/architecture.md#repo-resolution)), `typeMap` (Jira
   issue type → branch prefix; merged shallowly over the default map, so overriding one issue
   type doesn't drop the others out of branch recognition — `issueBranch()` always accepts the
   built-in default's prefixes too, N9), and the Ghostty/Aerospace/tmux workspace-naming scheme.
8. **`.tmp/` must be gitignored** in every repo you use this with. `pr` stages with `git add -A`,
   and the review files must never be committed.

## 4. The terminal frontend

### 4.1 Shape

There is no webview and no extension host. `plan` is a single CLI that either runs one
subcommand and exits, or (with no arguments) shows an fzf picker:

```
 bin/plan (no args)                              lib/cli.py
 ┌─ fzf picker over JQL results ──┐  invoke   ┌─ list/start/ship/done/view ────────────────┐
 │ preview pane: issue detail     │ ────────▶ │ dispatches to the matching lib/ module     │
 │ enter / ctrl-s /               │           │                                            │
 │ ctrl-d / ctrl-r                │           └──────┬───────────┬───────────┬─────────────┘
 └─────────────────────────────────┘                │           │           │
                                              lib/jira.py    lib/start.py  lib/ship.py / done.py
                                                (REST v3)    (git, gh)     (plain functions)
                                                                  lib/term.py
                                                            (tmux, aerospace, ghostty,
                                                             sketchybar, open/Brave)
```

| Module | Role | Spawns a process? |
| --- | --- | --- |
| `bin/plan`, `lib/cli.py` | Entry point, the fzf picker, subcommand dispatch | yes (`fzf`) |
| `lib/jira.py` | Every Jira call: REST v3 search, issue read, transitions, issue links, comments. The token comes from `~/.netrc` per call | no (network only) |
| `lib/start.py` | Start / Open: names, worktrees, the lock, git helpers (`run`, `git`) | yes (git, `gh`) |
| `lib/term.py` | Ghostty/Aerospace/tmux orchestration for Start/Open/Ship, the slots file, SketchyBar's trigger, and `openUrl()`'s Brave window | yes (`open`, `aerospace`, `tmux`, `sketchybar`) |
| `lib/ship.py` | What Ship checks, round-file naming | no |
| `lib/done.py` | Finish after merge: every check first, then the changes, then the comment | yes (git, `gh`) |

**Only `lib/jira.py` opens a network connection to Jira**, and that invariant is the one carried
over from GH Plan's "only `redmine.js` and `start.js`" rule, narrowed to Jira calls specifically
— the terminal front end legitimately spawns many kinds of process (`ghostty`, `aerospace`,
`tmux`, `fzf`, `nvim`, `lazygit`), which the original VS Code extension never needed to. See
[`docs/architecture.md` §6.2](docs/architecture.md#process-boundary) for the full reasoning.

### 4.2 `plan` with no args: the fzf picker

Runs the config's JQL against `/rest/api/3/search/jql`, feeds the results to `fzf` with a preview
window showing the issue's detail (description, status, blockers). Key bindings:

| Key | Does |
| --- | --- |
| `enter` | Start (new issue) or Open (in-progress issue) |
| `ctrl-s` | Ship |
| `ctrl-d` | Done |
| `ctrl-r` | Reload the list |

### 4.3 Scriptable subcommands

```
plan list                  # print the JQL results
plan start CIAM-123        # or: plan open CIAM-123 (same command, same code path)
plan ship [CIAM-123]       # key inferred from the current worktree's branch when omitted
plan done [CIAM-123]
plan view CIAM-123         # print issue detail, read-only
```

### 4.4 Start and Open

`lib/start.py` → `start()`. The command is **Start** on a `new`-category issue and **Open** on an
`indeterminate`-category (In Progress) one. Both run the same code, and every step runs only when
it has something to do:

1. Re-read the issue and refuse a `statusCategory` other than `new` or `indeterminate`, since the
   picker's list may be stale.
2. **Find the repo** (`lib/start.py`'s `mainCheckout()`, DECIDED user decision 2026-11-05): search
   for an existing worktree or branch for the issue across the cwd's own repo (its main checkout,
   resolved via `git rev-parse --path-format=absolute --git-common-dir` so a cwd inside a linked
   worktree still finds it) and every repo listed under `checkouts.<PROJECT>` — found in one of
   them, that's the repo, found in more than one is a refusal naming them. Nothing found yet and
   cwd is a repo: that's the repo, for the new worktree about to be made — `checkouts` isn't even
   consulted. Nothing found and cwd isn't a repo: fall back to `checkouts.<PROJECT>` — one entry is
   used outright, several prompt via `fzf`, none is a refusal ("Run plan from the repo the work
   belongs in…"). Full detail: [`docs/architecture.md` §7.0](docs/architecture.md#repo-resolution).
3. **Take the lock:** `<git-common-dir>/plan-busy-CIAM-123`, created with `wx` (exclusive) and
   holding `pid action`. A second invocation gets a refusal that names the file.
4. With no worktree yet: check blockers (issue links of type "Blocks", inward "is blocked by",
   filtered to `statusCategory != done`), `git fetch --prune`, pick the base (`develop`, else
   `origin/HEAD`, else `main`), and reuse the issue's branch if one exists locally or on origin.
   Otherwise make `<type>/CIAM-123-<slug>` (feat/fix/chore from the config's type map, keyed on the
   issue's Jira type; the slug from the summary's first five words). `git worktree add` into
   `<repo>.worktrees/CIAM-123`, beside the checkout.
5. A **setting-up marker**, `<git-common-dir>/plan-setting-up-CIAM-123`, is written before the
   worktree is made and removed only when setup has fully finished.
6. **Transition to "in progress".** `lib/jira.py` lists the issue's available transitions, picks
   the one whose `to.name` matches the configured target (default `"In Progress"`), posts it, then
   **reads the issue back** and refuses if `statusCategory` isn't `indeterminate` — see
   [§8](#8-patterns-worth-copying) and
   [`docs/failure-modes.md`](docs/failure-modes.md#jira-silently-blocks-a-transition). Posts a
   "Started" ADF comment.
7. **Pick or reuse a workspace, then hand off to a Ghostty window.** Aerospace workspaces are
   numbered/lettered and keep the fixed hotkeys `~/.aerospace.toml` already binds them to — `plan`
   never renames or creates one. `term.openIssueWorkspace()` first looks up the issue's key in the
   slots file (`slotsFile`, default `~/.cache/plan/workspaces`, lines of `<workspace>=<KEY>`): if
   the slot names a workspace that still has windows, it's reused as-is; otherwise
   `term.freeWorkspace()` picks the first workspace that isn't named in `~/.aerospace.toml`'s
   `# Workspaces: 1 work, 2 editors, ...` comment line and currently has no windows
   (`aerospaceConfig`, default `~/.aerospace.toml`), and `term.writeSlot()` writes the new
   `<workspace>=<KEY>` line (replacing any prior line for that workspace), then triggers
   SketchyBar (`sketchybar --trigger plan_workspaces_change`, skipped silently if SketchyBar isn't
   installed) so `~/.config/sketchybar/plugins/workspaces.sh` can relabel the bar. Every
   workspace-touching command — `start`/`open`/`done`/`list`/the picker — first **sweeps** the
   slots file, dropping any line whose workspace has no windows, so a slot never outlives the
   window it named.

   `open -na /Applications/Ghostty.app --args -e "tmux new-session -A -s CIAM-123 -c <worktree>"`
   opens a new Ghostty window (macOS's `ghostty +new-window` is unsupported on this platform;
   `ghostty +help` says so directly). The new window's AeroSpace window-id is found by diffing
   `aerospace list-windows --monitor all --app-bundle-id com.mitchellh.ghostty --json` taken before
   and after the `open` call — never by matching the window's title text alone (see
   [`docs/failure-modes.md`](docs/failure-modes.md), *Open* under Start). It's then moved to the
   workspace resolved above with `aerospace move-node-to-workspace --window-id <id> <workspace>`.
   Inside the tmux session, a window runs `claude "Work on Jira CIAM-123"` (for a JP issue, a prompt naming its file instead) and a second window is a
   plain shell. If the tmux session already exists (`tmux has-session -t CIAM-123`), Open just
   focuses the resolved workspace instead of creating anything — `tmux new-session -A` makes
   create-or-attach safe by construction (confirmed via `man tmux`).
8. **Open the issue's browse URL.** Start/Open calls `term.openUrl()` (§4.6) with the workspace
   just resolved, so the browser ends up beside the Ghostty/tmux window it's for.

**A `JP-<n>` key is file-backed, not Jira (user decision 2026-11-05).** Steps 1-7 above run
unchanged — `JP`'s issues live in this repo's own `issues/` folder (`lib/fileissues.py`), and
Start/Open find it via `checkouts.JP`, which defaults to this repo. Step 6's transition writes
`status: in-progress` into the issue file's frontmatter and reads it back the same way; its
"Started" note is appended under the file's `## Log` heading instead of posted as a Jira comment.
Step 8 does not open a browser — there's no browse URL — but instead opens (or reuses) a tmux
**pane**, split side-by-side off the issue's `claude` window, running `nvim` on the issue file, and
still prints the file path; the split is tagged (a pane user option) so Open run again on the same
issue reuses it instead of splitting a second one. Open on a JP key is the same code path as Start
(`cmdStart` serves both), so this already covers "run Open again on an issue that's already
started." Full detail: [`docs/architecture.md` §7.7](docs/architecture.md#file-backed-issues).

### 4.5 Ship

Ship runs `ship.check(root, key)`, which refuses by name: a branch that isn't `…/CIAM-123-…`, a
missing `pr-body.md`, a dirty tree with no `commit.txt`, a reply file that isn't named
`replies/<digits>.md`, an empty reply, or two replies to one thread.

On success, it runs `pr CIAM-123` visibly in a tmux window or pane in the issue's session, so its
output is seen as it happens (replacing the VS Code Task/`ProcessExecution` mechanism). Ship used
to stop after the pre-flight check and open the round files for a separate review before a second
command, Go, ran `pr`; that separate confirmation step is gone — Ship now runs the check and `pr`
in one invocation, and `plan go` no longer exists. (The round files themselves are unchanged and
still reviewable by hand in `.tmp/CIAM-123/`; a dedicated review page, tracked separately, is
expected to take over that role later.)

- **Non-zero exit:** the pane shows why. Nothing else happens.
- **Zero exit:**
  1. Any round file that was **edited while `pr` ran**, which `pr` has since sent and deleted, is
     saved beside it as `<file>.unsent` (or `.unsent2`, and so on, never over a file) — the
     `keepEdits()` behavior ported unchanged from `ship.py`.
  2. `gh pr view` reads the PR; there is **no** write to a Jira field for PR linkage — Jira's own
     development panel already shows it, because the branch name and PR title both contain
     `CIAM-123` (decision #4).

Ship again runs `pr` again: the next round. With nothing left to send, `pr` sends nothing — Ship
stays safe to re-run ([§8](#8-patterns-worth-copying)).

### 4.6 Opening the browse URL (`openUrl()`)

`term.openUrl(url, workspace=None, ...)` always prints `url`, then best-effort opens it in a Brave
window (`browserApp`/`browserBundleId`, default "Brave Browser"/`com.brave.Browser` — user decision
2026-11-05) on the **same Aerospace workspace as the Ghostty/tmux window the command ran from**,
never a bare `open <url>` and never relying on a terminal's own link-click (a bare `open <url>` was
reported opening the wrong window entirely; that approach is rejected).

The target workspace, in order: an explicit `workspace` argument (Start passes the one it just
resolved in §4.4 step 7, since Start runs in the invoking terminal, not the new issue's own
workspace); else, if inside tmux and the session name is a key present in the slots file, that
slot's workspace; else the focused workspace (`aerospace list-workspaces --focused`).

Once resolved: if that workspace already has a Brave window (`aerospace list-windows --workspace
<ws> --app-bundle-id com.brave.Browser --json`), `openUrl()` focuses it (`aerospace focus
--window-id <id>`) and opens the URL as a new tab (`open -a "Brave Browser" <url>`) — **this
new-tab behavior was never run live and is unverified against a real Brave process.** Otherwise it
opens a new window exactly as `~/.local/bin/plan-open` does (`open -na "Brave Browser" --args
--new-window <url>`) and moves it to the workspace by a before/after window-id diff, the same
technique §4.4 step 7 uses for Ghostty. Either way, focus then returns to whatever window was
focused when `openUrl()` was called, so the terminal that ran the command is never left in the
background.

Done never calls `openUrl()` — it only prints the browse URL (§4.7). `plan view` also only prints
it, since a view is read-only and shouldn't carry a second side effect.

### 4.7 Done

Done runs once a PR for the issue's branch is found (`gh pr view <branch>` / `gh pr list --head
<branch>`, never a Jira field) and merged. `lib/done.py` → `finish()` follows the same strictest
rule as the original: **every check before the first change.**

Checks, in order: the PR is MERGED; the PR belongs to this checkout's `origin`; the worktree is
clean and holds no ignored files outside the disposable list, and its HEAD is the PR's merged head
or behind it; no unsent round files for any issue in the worktree's `.tmp/`; the worktree isn't
locked; the main checkout's base can fast-forward to `origin/<base>`.

Then the changes: fast-forward the main checkout's base, `git worktree remove`, delete every
branch named for the issue whose tip is in the PR, **post an ADF comment** linking the PR and merge
commit, and print:

```
Close CIAM-123 in Jira: https://cryoport.atlassian.net/browse/CIAM-123
```

**Done never transitions the issue.** There is no code path in `lib/done.py` that calls
`jira.transition()`; the human's own Claude Code `PreToolUse` hook independently blocks any Bash
command recognized as moving a Jira issue to a terminal status, as a second layer. A human closes
`CIAM-123` by hand in the Jira UI. Done accepts an issue whose `statusCategory` is `indeterminate`
or `done`.

If Done stops part way, it throws with `error.result`, so the error says what it already changed.
Done again finishes the rest.

### Opening a URL goes through `openUrl()`, never a bare `open` or a terminal link-click

See [§4.6](#46-opening-the-browse-url-openurl) for `openUrl()`'s full design (Brave, same-workspace
reuse-as-tab or new-window-and-move, user decision 2026-11-05). In-terminal browsing (a TUI
browser, or a terminal hyperlink escape sequence) remains out of scope for this port.

## 5. The review round: `.tmp/<KEY>/`

This folder is the contract between Claude and the buttons. It's gitignored and lives in the
worktree. Unchanged from GH Plan except the directory name, keyed by Jira key instead of a Redmine
number.

| File | Written by | Sent by | Then |
| --- | --- | --- | --- |
| `commit.txt` | Claude | `pr`: `git add -A && git commit -F` | deleted, so the next change needs a new message |
| `pr-body.md` | Claude, from `references/pr-body.md` | `gh pc` (new PR) or `gh pb` (update) | kept: every `pr` replaces the body |
| `replies/<comment id>.md` | `pr_threads.py --drafts` writes the context; Claude writes the reply | `pr_threads.py --post`: reply, resolve the thread | deleted per file once resolved |
| `issue.md` | Claude: the description as Jira holds it now, with boxes ticked | `checklist.py`: only if box state is the **one** difference | deleted; any other difference refuses and keeps it |
| `*.unsent` | Ship, from edits made while `pr` ran | nobody, by design | Done refuses until they're dealt with |

Rules the files follow:

- **The commit subject carries the key**, e.g. `Add X (CIAM-123)` — not `(fixes #<n>)`, since no
  commit or merge ever transitions the issue. `gh pc` takes the branch's first commit subject as
  the PR title; the squash merge keeps it, and Jira's GitHub integration matches `CIAM-123` in it.
- **The commit message ends `Assisted-by: Claude:<model>`. The PR body carries no attribution.**
  `ghbody.sh` refuses a body containing `Assisted-by` or `Co-authored-by`, which is why `gh pc` is
  used instead of `gh pr create --fill`.
- **No Jira smart commits.** `commit.txt`, the PR title and `pr-body.md` must never carry a
  [smart-commit](https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/)
  token (`#close`, `#comment`, `#time`, ...) next to the issue key — Jira's GitHub integration
  reads those straight off the commit/PR text, so a stray one would transition or comment on the
  issue from outside Peter's own hand, the same thing "Done never transitions the issue" (above)
  is built to prevent. `pr.sh` refuses to commit, and `ghbody.sh` refuses to create or edit the
  PR, if a line (or, for the PR title, the whole string) names a key and also carries a `#<word>`
  right after whitespace or line-start — deliberately broad rather than a fixed list of smart-
  commit words, since Jira's own list can grow. The same check sits in Claude's own
  `no_terminal_jira_transition.py` `PreToolUse` hook, for `git commit -m`/`gh pr create|edit
  --title|--body` typed directly as a Bash command rather than through these scripts.
- **A reply file opens with an HTML comment** holding the thread's path, line, comment and diff
  hunk, so the reviewer reads the reply against what it answers. `--post` strips the comment.

### Review: co-signing and dispositions

Accepting a review means co-signing the PR: taking responsibility, as a second coworker who read
the change and agrees with it, that it's correct, proper in every way the PR touches, and ready to
ship. It isn't an adversarial pass looking for reasons to reject — it's the second half of making
the change.

The reviewer records a disposition for every changed file: its path, its blob SHA
(`git rev-parse HEAD:<path>`, or `git hash-object <path>` for a file not yet committed), the passes
run against it, and a verdict — OK, meaning the reviewer vouches for that file, or a list of
findings.

A file marked OK, whose blob SHA hasn't changed since, is not reviewed again. A later round
reviews only the files whose blobs changed since their last disposition.

Recommending fix code is encouraged, and the reviewer owns what they recommend. When the author
applies a recommended fix verbatim, the finding is resolved immediately, with no further review of
that fix. `pr_threads.py` (`skills/jira-issues/scripts/pr_threads.py`, the `post()` function,
around lines 152–167) already supports this mechanically: it posts whatever reply text a thread's
file holds — e.g. "Fixed: applied your suggestion verbatim" — and resolves the thread in the same
pass, with no separate gate checking what the reply says. Writing a reply that says the fix was
applied verbatim, and running `--post`, is what carries out "resolved immediately" in this repo.

When an aspect of the change, or its breadth, can't be reviewed comprehensively just by reading —
every call site of a rename, every row of a generated file — the reviewer writes a Python check
script so that coverage is mechanical instead of eyeballed. These scripts go under `.tmp/<KEY>/`,
alongside `commit.txt` and `pr-body.md` in the same gitignored review-round folder, unless the
check is worth keeping for future rounds — then it's committed alongside the PR, under
`skills/jira-issues/scripts/` with the rest of the review tooling. Either way, the script's output
is part of the disposition, not just a claim that a check ran.

## 6. The shell side: `pr` and its helpers

### 6.1 `pr [done] [KEY]` (`pr.sh`)

`KEY` defaults to the key in the branch name (`fix/CIAM-88-go-requests` → `CIAM-88`), parsed with a
regex matching `[A-Z][A-Z0-9]+-\d+` instead of GH Plan's bare digits. Each step runs only when it
has something to do, so `pr` is safe to repeat:

| Step | When | Does |
| --- | --- | --- |
| guard | always | refuses the default branch, and a PR closed unmerged |
| merged? | PR is MERGED | refuses if HEAD isn't the merged head or the tree is dirty; sends any leftover replies and ticks; says "run `plan done`"; exits |
| commit | tree dirty | needs `.tmp/CIAM-123/commit.txt`; `git add -A`, `git commit -F`, deletes the file |
| push | always | `GIT_TERMINAL_PROMPT=0 git push -u origin HEAD` |
| Copilot | existing PR, and the push moved GitHub's head | `gh pr edit --add-reviewer @copilot` |
| PR | always | `gh pc` if there's no PR, else `gh pb` |
| round | `replies/` or `issue.md` exist | `pr_threads.py --post`, then `checklist.py`, **after** the push |
| hand-off | always | writes `branch.<b>.github-pr-owner-number`; prints the PR URL (no `vscode://` open — this is terminal-native) |

`pr done` does the merged-PR branch and refuses an unmerged PR.

### 6.2 `ghbody.sh` (`gh pc` / `gh pb`)

Unchanged from GH Plan except the subject regex now matches `[A-Z][A-Z0-9]+-\d+` for the key
instead of `#\d+`. `gh pc`: title from `git log --reverse origin/<base>..HEAD | head -1`, body from
`.tmp/CIAM-123/pr-body.md`, then asks Copilot to review. `gh pb`: `gh pr edit --body-file`. Both
refuse an attribution line. `GHBODY_DRY=1` prints the command instead of running it.

### 6.3 Python helpers (stdlib only)

- **`pr_threads.py <PR>`** lists unresolved threads; `--drafts DIR` writes each thread's context
  file; `--post DIR` checks **every** file before the first post, then replies over REST and
  resolves over GraphQL. Unchanged from GH Plan — this is purely GitHub-side.
- **`checklist.py CIAM-123 FILE`** diffs the file against the live Jira description (fetched as
  ADF, diffed by rendering to Markdown line-for-line with box state normalised — see
  `~/.claude/skills/jira-adf-surgical-edit/SKILL.md` for the GET→walk→PUT→verify-diff procedure
  this follows). Any other difference refuses, naming the line. Otherwise it PUTs the updated ADF
  (`taskItem` state), adds a comment listing each box, reads the issue back, and deletes the file.
- **`new_issue [--check] FILE`** creates a Jira issue from front matter (`project`, `type`,
  `summary`, `blocked_by`, `relates`) and a body. CIAM requires `customfield_11325` (Compliance
  Tags, default `"Not Required"`) and `duedate` at create time — see
  `~/.claude/skills/jira-ciam-create/SKILL.md` and its `create-ciam-issue.sh` for the exact payload,
  which this script delegates to rather than re-implementing. `--check` reads only. A second run
  finds the issue by summary and adds only the missing relations.
- **`outline.py FILE`** checks the issue body has Task / Guardrails / Lenses / Done, at least two
  Done boxes, and a Done line naming every ticked lens. Unchanged from GH Plan apart from reading
  the description from Jira's ADF instead of Redmine's Markdown-ish text.

## 7. Claude's side: skills, CLAUDE.md, hooks

### 7.1 The jira-issues skill

[`skills/jira-issues/SKILL.md`](skills/jira-issues/SKILL.md) is what Claude loads when it picks up
an issue, writes a commit or PR body, or answers a review. It's the human-facing spec of §5,
written for Claude, renamed from `gh-issues` and updated for Jira:

- read the issue before writing about it, never from memory;
- check the description with `outline.py` before coding;
- when done, write `commit.txt` and `pr-body.md` (and replies and ticks), then stop: **the human
  presses the buttons**;
- for a review: `pr_threads.py --drafts`, fix the code, write replies under the context.

### 7.2 The repo's CLAUDE.md

[`claude/CLAUDE.md`](claude/CLAUDE.md) is written as a contents page, not a manual. It keeps the
button rule: "Every commit, push, `gh` write and Jira write happens on Start, Ship or Done, or a
`pr` he runs" — updated from "Redmine write" — and adds the hard rule that Done never transitions
the issue.

### 7.3 Enforcement: settings and the hook

[`claude/settings.json`](claude/settings.json):

- `attribution.commit` sets the `Assisted-by:` trailer, `attribution.pr` is empty, and
  `sessionUrl` is false — unchanged from GH Plan, since attribution semantics don't change with
  the tracker;
- **deny** `git push`, `git rebase`, `git reset` outright, and reads of `.env`, keys and secrets —
  carried over, with the `.netrc` deny kept scoped to this repo's own working tree (`./**/.netrc`),
  since this repo tracks no `.netrc` of its own; `~/.netrc` itself is read only by `lib/jira.py` at
  runtime, outside any Claude Code tool call, so it's never exposed to a Bash command this hook
  would see regardless;
- **allow** the read-only git and test commands;
- a **PreToolUse hook** on Bash runs `no_ai_attribution.py` — unchanged, ported verbatim, since it
  is entirely about git/`gh` command text, not the tracker.

## 8. Patterns worth copying

These recur through the code, and they're the main thing to take away even if you don't use the
tools. Every one of these is carried over unchanged in spirit from GH Plan; only the tracker
underneath (and, for #9, the UI layer) changed.

1. **Every step runs only when it has something to do.** Start, `pr`, Done, `checklist.py` and
   `new_issue` can all run twice, or after a crash, and end where a clean run would. See
   [`docs/failure-modes.md`](docs/failure-modes.md).
2. **Check everything, then change anything.** Done and `pr_threads.py --post` both refuse before
   their first write rather than stopping half way.
3. **Read back writes the server may silently drop.** A Jira transition can return success while a
   workflow condition blocks the actual status change. `jira.py`'s transition call reads the issue
   back after any status write and throws a message naming both statuses.
4. **Compare-and-swap on refs.** `git update-ref <ref> <new> <old>` refuses if a branch moved since
   it was checked.
5. **Exclusive lock files with a pid, never auto-stolen.** `open(file, "x")` (`O_CREAT|O_EXCL`)
   in the git common dir. A stale lock is reported with its path, not taken over.
6. **Atomic hand-off files:** write `x.tmp`, then `rename`.
7. **Secrets from `~/.netrc` per call.** Nothing in env vars, argv, files or logs. `gh` holds the
   GitHub auth, so no PAT is needed anywhere.
8. **A refusal that names its fix** instead of automatic recovery code.
9. **Keep process orchestration in dedicated modules.** `lib/jira.py` is the only Jira network
   client; `lib/term.py` is the only module that spawns Ghostty/Aerospace/tmux — the terminal
   equivalent of "keep the `vscode` import in one file," so the rest of `lib/` stays testable under
   plain `unittest` without a real terminal.

## 9. Testing

```sh
python3 -m unittest discover -s lib -t .                                     # lib/ unit tests
cd skills/jira-issues/scripts && python3 -m unittest test_scripts           # see test/ for current coverage
python3 claude/hooks/test_no_ai_attribution.py
```

Tests build real temporary git repos with a bare `origin`, and a fake Jira client (an injected
object or a local stub HTTP server) rather than a fake Redmine. `lib/test_start.py` and
`lib/test_done.py` carry over the original's fault injection: each step is made to fail once, and
the test asserts the next run converges. New tests added for this port cover: read-back refusal on
Start's transition, the structural impossibility of a terminal transition in Done, Jira-key
branch-name parsing, the JQL-based list, blocker detection via issue links, and the checklist
ADF diff guard.

## 10. Known gaps and rough edges

- **macOS only:** `open` for Ghostty windows, `aerospace` for Aerospace workspaces. (GH Plan's
  Keychain dependency is gone — auth is `~/.netrc` — but the rest of the macOS-only surface grew,
  not shrank, with the terminal front end.)
- **In-terminal browsing is out of scope.** `openUrl()` (§4.6) opens issue links in Brave, same
  workspace as the caller; a TUI browser or terminal hyperlink escape sequence is left to another
  piece of work.
- **`openUrl()`'s existing-window reuse is unverified.** The "open as a new tab in an
  already-running Brave window" branch (`open -a "Brave Browser" <url>`) was never run live; only
  the "new window" branch matches a confirmed-working reference (`~/.local/bin/plan-open`).
- **The live "In Progress" transition name is verified only indirectly.** A live CIAM issue
  (Task-type) was confirmed to have a status literally named "In Progress" in category In
  Progress, via a direct issue read. A GET of that issue's `/transitions` list — which would show
  the exact transition name from its current status — was blocked by a defensive local Claude
  Code hook that pattern-matches the word "transitions" in a URL regardless of HTTP method. This
  is a documentation gap, not a functional one: the ported code never hardcodes a transition name
  or id in the first place, and looks it up dynamically against whatever the issue's actual
  available transitions are at call time.
- **Older skills not included:** as in GH Plan, nothing targeting GitHub Issues or a removed
  skill is carried into this port.
- **Single-user assumptions:** one Jira site, one project (`CIAM`), one machine, paths under
  `~/programming`, and the netrc machine name fixed in config.

## 11. Package index

```
GUIDE.md                           this file
bin/
  plan                             the CLI entry point
  gh-aliases.yml                   `gh alias list` output: pc, pb (plus co, squash)
lib/
  jira.py                          every Jira REST v3 call; ~/.netrc auth; transition lookup + read-back guard
  start.py                         Start/Open: names, worktrees, the lock, git helpers
  ship.py                          Ship's checks, round-file naming
  done.py                          Finish after merge: checks, then changes, then the comment — never a transition
  term.py                          Ghostty/Aerospace/tmux/SketchyBar/Brave orchestration; numbered-
                                   workspace picking, the slots file, openUrl()
  cli.py                           subcommand dispatch, the fzf picker
  config.py                        reads ~/.config/jira-plan/config.json
  test_*.py                        unittest suite (test_cli, test_jira, test_start, test_ship, test_done, test_term)
skills/jira-issues/                SKILL.md, references/ (pr-body, issue templates),
                                   scripts/ (pr.sh, ghbody.sh, pr_threads.py, checklist.py,
                                   new_issue.py, outline.py, ghread.py, test_scripts.py)
claude/CLAUDE.md                   the repo's Claude instructions
claude/settings.json               attribution, permissions, the hook
claude/hooks/                      no_ai_attribution.py and its test
docs/architecture.md               the system's architecture (§6.2 process boundary, §7 operations, §10 invariants)
docs/failure-modes.md              the failure-mode table method, and Start's and Done's tables
lib/test_*.py                      unittest suite against a fake Jira client and fake gh/git
```

Several of the paths above (`lib/`, `skills/jira-issues/scripts/`, `test/`) were being written
concurrently by sibling agents while this guide was drafted; the layout reflects the decisions list
this port worked from, not a read of their finished output. Confirm against the actual tree before
relying on an exact path.
