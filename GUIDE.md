# Skuld: an engineering guide to `plan` and `pr`

This guide covers how to set skuld up, run each command, and what each command does step by step.
The [architecture](docs/architecture.md) says what the system is and the rules it keeps; this guide
links to it for the reasons and doesn't repeat them.

Skuld plans and ships work from the terminal. `plan` lists what's next, and starts, opens, ships and
finishes an issue, each in its own VS Code window on its own macOS Desktop. `pr` commits, pushes,
opens or updates the pull request and answers its review. Issues live in the ticket store, a git
repo of Markdown files, or in Jira Cloud (`cryoport.atlassian.net`).

## Contents

1. [Skuld in one picture](#1-skuld-in-one-picture)
2. [Setting up](#2-setting-up)
3. [Commands](#3-commands)
4. [Start and Open](#4-start-and-open)
5. [Ship and the review round](#5-ship-and-the-review-round)
6. [Done](#6-done)
7. [Pages and the browser](#7-pages-and-the-browser)
8. [The helpers](#8-the-helpers)
9. [Claude's side](#9-claudes-side)
10. [Patterns worth copying](#10-patterns-worth-copying)
11. [Testing](#11-testing)
12. [Limits](#12-limits)
13. [Package index](#13-package-index)

## 1. Skuld in one picture

```text
   Next up (plan order)       worktree <repo>.worktrees/<KEY>          the issue (store or Jira)
   ────────────────────       ───────────────────────────────          ─────────────────────────
   (1) START ───────────────▶ branch <type>/<KEY>-slug, off base ────▶ in progress, "Started" logged
                              issue window, Claude in its terminal      (a store ticket opens in a tab)
                              Claude works · writes <store>/reviews/<KEY>/
   (2) SHIP ────────────────▶ pr <KEY>: commit, push, open/update PR ─▶ Done boxes ticked
                              review rounds: replies/                    (Jira: dev panel, native)
   (3) squash merge on GitHub; the PR title and branch carry <KEY>
   (4) DONE ────────────────▶ worktree and branch removed, base up ───▶ merge noted;
                                                                         the human closes <KEY>
```

**Claude stages the process; the human verifies and executes it.** Claude never commits, pushes,
runs a `gh` write or writes to Jira. It writes the commit message, the PR body, review replies and
the ticked checklist into `<store>/reviews/<KEY>/`. The human reads them and runs **Ship**, and
`pr` sends them. Every outward write happens on a step the human runs.

A run touches up to three places: skuld holds the code, the ticket store holds tickets and review
files, and the configured repo holds the code being changed
([architecture §3](docs/architecture.md#storage)). The default branch carries the code, and
everything reaches it by PR. The only status change `plan` makes is Start's move to "in progress";
**Done never changes an issue's status**, and the human closes the issue.

## 2. Setting up

macOS only. In order:

1. **Tools.** `git`, `gh` (signed in with `gh auth login`), Python 3.10 or later on `PATH` as
   `python3` (`/usr/bin/python3` is 3.9 and can't run the scripts), VS Code with its `code` command
   on `PATH`, fzf, and Xcode's Command Line Tools for `swiftc` (step 12). SketchyBar is optional.

2. **The Jira token in `~/.netrc`**, never in a tracked file or an env var:

   ```text
   machine cryoport.atlassian.net
     login <your Atlassian email>
     password <API token>
   ```

3. **The ticket store.** Skuld's store is the private repo `pjfitzgibbons/issues`, cloned beside
   skuld: `git clone https://github.com/pjfitzgibbons/issues.git ~/programming/skuld-project/issues`,
   with `"issueStore": "~/programming/skuld-project/issues"` in `~/.config/skuld/config.json`. It
   can be cloned anywhere else; `issueStore` names whichever path.
   For a new store, `git init` a folder, give it a private remote, and run `plan init-store` to
   make the empty folders (`milestones/`, `active/`, `archive/`, `reviews/`) at `issueStore`.

4. **`store.yml`**, in skuld, lists the store's projects, each with its key prefix, its name and
   its repos by remote:

   ```yaml
   projects:
     - key: SK
       name: Skuld
       repos:
         - git@github.com:pjfitzgibbons/skuld.git
   archiveAfterDays: 14
   debounceSeconds: 5
   ```

5. **The machine config** at `~/.config/skuld/config.json` holds what differs per Mac:

   ```json
   {
     "issueStore": "~/issues",
     "checkouts": ["~/programming/skuld", "~/work/cryo-gitops-github"],
     "jira": {
       "CIAM": { "repos": ["git@github.com:cryoport/cryo-gitops-github.git"] }
     }
   }
   ```

   `checkouts` lists this Mac's main checkouts. Each is matched to its project by its `origin`
   remote, so no path is tied to a project by hand
   ([architecture §7.0](docs/architecture.md#repo-resolution)). The other keys have defaults:

   | Key | Default | What it sets |
   | --- | --- | --- |
   | `jql` | `project = CIAM AND assignee = currentUser() AND statusCategory != Done ORDER BY Rank` | The Jira half of Next up |
   | `typeMap` | Story and Task `feat`, Bug `fix` | Issue type to branch prefix, merged over the default |
   | `pr` | `~/.local/bin/pr` | The `pr` that Ship runs, by path |
   | `disposable` | `done.py`'s list | Ignored files Done may delete with a worktree |
   | `slotsFile` | `~/.cache/plan/workspaces` | The `<slot>=<KEY>` lines that place issues on Desktops |
   | `windowsFile` | `~/.cache/plan/windows` | The `<KEY>=<window id>` lines that say which window is each issue's |
   | `workspaceDir` | `~/.cache/plan/code-workspaces` | Where each issue window's throwaway `<KEY>.code-workspace` goes; Done deletes it |
   | `spacesHelper` | `macos/build/skuld-spaces` in skuld | The helper `bin/build-spaces` builds; `plan` builds the default one itself when it is missing or older than its source |
   | `reservedDesktops` | `[1]` | Desktops `plan` never takes for an issue |
   | `browserApp` | `Brave Browser` | The browser a Jira issue's page opens in, in a new window on the issue's Desktop |
   | `netrcFile`, `baseUrl` | `~/.netrc`, `https://cryoport.atlassian.net` | Where the Jira token and site are |

6. **The skill.** Link `skills/jira-issues/` to `~/.claude/skills/jira-issues/`, so every repo
   shares one copy.

7. **Bare-word commands.** `~/.local/bin` is on `PATH`; link skuld's commands into it:

   ```sh
   ln -s "$(pwd)/bin/plan" ~/.local/bin/plan
   ln -s "$(pwd)/bin/jira-get" ~/.local/bin/jira-get
   ln -s ~/.claude/skills/jira-issues/scripts/pr.sh ~/.local/bin/pr
   ln -s ~/.claude/skills/jira-issues/scripts/new_issue.py ~/.local/bin/new_issue
   ```

   `pr` collides with `/usr/bin/pr`, the Unix paginator. An interactive shell with `~/.local/bin`
   first finds the right one, but a login shell may not, so `plan` runs `pr` by path (the `pr`
   key). `pr.sh` finds its sibling scripts through `realpath "$0"`, so it works through the link.

8. **gh aliases.** `gh alias import bin/gh-aliases.yml` adds `gh pc` and `gh pb` (§8).

9. **Claude's settings in a configured repo.** Each configured repo gets skuld's `claude/` as its
   `.claude/` (settings and hooks) and `claude/CLAUDE.md` as its `CLAUDE.md` (§9).

10. **The plan-window extension.** Link skuld's `vscode/plan-window/` into VS Code's extensions,
    then restart VS Code:

    ```sh
    ln -s "$(pwd)/vscode/plan-window" ~/.vscode/extensions/skuld.plan-window-0.0.1
    ```

    When an issue window opens, it opens the ticket in a tab, starts Claude with Start's prompt in
    a terminal beside it, and opens a shell in the panel (§4). It is disabled in Restricted Mode,
    so trust the folder that holds each configured repo with its `<repo>.worktrees/`, and the
    ticket store's folder.

11. **The LaunchAgents.** `issue-watch` watches the store and commits and pushes it, and
    the local server serves issue pages (§7). Both start at login and restart if they stop.

12. **Native Desktops.** Each issue window gets its own macOS Desktop. SIP stays on and nothing is
    installed beyond the helper:
    1. Run no other window manager. Aerospace, for one, holds every window on one Desktop.
    2. In System Settings → Desktop & Dock → Mission Control, turn off "Automatically rearrange
       Spaces based on most recent use", and leave "Displays have separate Spaces" and "When
       switching to an application, switch to a Space with open windows" on.
    3. In System Settings → Keyboard → Keyboard Shortcuts → Mission Control, turn on "Switch to
       Desktop 1" through the highest Desktop an issue may use (^1 to ^N).
    4. In Mission Control, add Desktops up to that number. `plan` never creates one.
    5. Allow VS Code to control the computer through System Events when macOS asks, on the first
       Start.
    6. Build the helper with `bin/build-spaces`, or let the first Start build it. It needs
       `swiftc` alone and no permission grant.
    7. For SketchyBar, give each Desktop a `space` item with `associated_space=N`, and subscribe it
       to `plan_workspaces_change` so it relabels from the slots file. Run `sketchybar --reload`
       after adding a Desktop.

## 3. Commands

`plan` with no arguments shows Next up in an fzf picker, with the issue's detail in the preview
pane: fields, open blockers, what it blocks, and its body or description.

| Key | Does |
| --- | --- |
| `enter` | Start a new issue, or Open one in progress |
| `ctrl-s` | Ship |
| `ctrl-d` | Done |
| `ctrl-r` | Reload the list |

The same work runs as subcommands. A command that takes a key works from any folder; the key
decides the repo (§4).

| Command | Does |
| --- | --- |
| `plan list` | Print Next up in plan order, both trackers merged ([architecture §7.6](docs/architecture.md#ready-file)) |
| `plan view <KEY>` | Print one issue; reads only |
| `plan new <PREFIX> "<title>"` | Make a store ticket from the template, check it and open it in `$EDITOR` |
| `plan set <KEY> field=value…` | Change a ticket's front matter, all or nothing, checked first; `blocked_by+=SK-0004` and `blocked_by-=SK-0004` add and remove one blocker |
| `plan milestone new\|set\|list` | Manage milestones, through the checker |
| `plan start <KEY>`, `plan open <KEY>` | Start or Open (§4); one command, one code path |
| `plan ship [KEY]` | Ship (§5); the key defaults to the current worktree's branch |
| `plan done [KEY]` | Done (§6) |
| `plan browse <KEY \| pr#N \| url> [KEY]` | Open a page in the issue window's browser (§7) |
| `plan serve start\|stop\|restart\|status` | Control the local server's LaunchAgent (§7); `status` prints the server's URL and whether it answers |
| `plan archive` | Move closed tickets past `archiveAfterDays` to `archive/` |
| `plan init-store` | Make an empty store's folders |
| `pr [done] [--no-copilot] [KEY]` | Send a round (§5) |
| `issue-check [PATH…]` | Check ticket files, or the whole store; exits non-zero naming file, line and rule |
| `issue-watch` | The store's watcher; launchd runs it |
| `jira-get <path>` | One read-only Jira GET with `~/.netrc` auth, so an agent can read Jira without seeing the token |

## 4. Start and Open

`plan start` is **Start** on an issue that hasn't started (a store `status: open`, a Jira
`statusCategory` of `new`) and **Open** on one in progress. Both run the same code, and each step
runs only when it has something to do:

1. **Re-read the issue** and refuse any other status, since the picker's list may be stale.
2. **Find the repo** with `mainCheckout()`: the key's project gives the candidate checkouts, an
   existing worktree or branch wins, and new work goes in the current repo when it's a candidate
   ([architecture §7.0](docs/architecture.md#repo-resolution)).
3. **Take the lock**, `<git-common-dir>/plan-busy-<KEY>`, created exclusively and holding
   `pid action`. A second run is refused, naming the file. A stale lock is reported, never taken
   over.
4. **Check blockers.** Start lists the issue's open blockers and asks whether to go on; no stops
   with nothing changed. A store issue's blockers are its `blocked_by` keys still open; a Jira
   issue's are its inward "is blocked by" links not in the Done category
   ([architecture §4.2](docs/architecture.md#dependencies)).
5. **Make the branch and worktree.** `git fetch --prune`, then pick the base: `develop`, else
   `origin/HEAD`, else `main`. Reuse the issue's branch if it exists here or on `origin`;
   otherwise make `<type>/<KEY>-<slug>`, the prefix from the issue's type and the slug from the
   title's first five words. `git worktree add` puts it in `<repo>.worktrees/<KEY>`. A setting-up
   marker, `<git-common-dir>/plan-setting-up-<KEY>`, is written first and removed only when setup
   has finished.
6. **Move the issue to "in progress", and read it back.** Jira: `lib/jira.py` finds the
   transition whose target status matches the configured name (default `"In Progress"`), posts it,
   and refuses if the read-back `statusCategory` isn't `indeterminate`. Store: `FileStore` sets
   `status: in-progress` and reads the file back. Then "Started" is logged: an ADF comment in
   Jira, a `## Log` line in the ticket.
7. **Place the issue on a slot.** `SpacesPlacer` does this, and
   `term.writeSlot()` records `<slot>=<KEY>` through a temp file and a rename. SketchyBar is told
   to relabel its bar (`sketchybar --trigger plan_workspaces_change`, skipped when it isn't
   installed). Every command that touches slots first sweeps the slots file through the placer,
   dropping lines whose issue window is gone.
   `SpacesPlacer` reads the Desktops and VS Code's windows through `skuld-spaces`. It reuses the
   issue's slot if that Desktop holds no VS Code window. Otherwise it takes the first Desktop
   that exists, isn't in `reservedDesktops`, holds no VS Code window and is no other issue's
   slot. Start refuses, with nothing opened, when there is no such Desktop, when the Desktop
   doesn't exist, when its ^N shortcut is off (naming the setting), or when the helper reports a
   SkyLight call missing.
8. **Open the issue window.** Write `<KEY>.code-workspace` to `workspaceDir`, listing the
   worktree and the ticket store. Start sends the Desktop's ^N shortcut through System Events, waits until
   that Desktop shows, and runs `code` on the workspace file. A window id that wasn't there before
   is the new window, recorded in `windowsFile`; it is never found by its title. When no new
   window appears and VS Code has switched to another Desktop instead, the workspace was already
   open there, and Start takes the frontmost VS Code window as the issue's. A window on a Desktop
   other than the one switched to is left there, and its slot is rewritten to that Desktop, with
   a note. When that Desktop is another issue's slot, the note says to drag the window to a free
   Desktop and run `plan open <KEY>`. Claude starts in one integrated terminal with a prompt
   naming the issue, and a shell waits in another; both carry the issue's key in their
   environment. When the window is already open, Open reuses it, rewrites the slot when the
   window sits on another Desktop, and never moves it.
9. **Put the issue beside Claude.** A Jira issue's browse URL is printed, then opens in
   a new `browserApp` window on the issue's Desktop. Each
   step after the print is best-effort. A store ticket opens in an editor tab, on the ticket file in the window's store
   folder. The URL or file path is printed either way.
10. **Focus the issue.** Switch to the slot and focus the issue window with Claude's terminal
    in front, so the command ends with the human at the issue's Claude: `code <workspace file>`
    switches to the window's Desktop.

`plan list` and the picker show each issue's slot (`D3`), and both Desktops when
the window has moved off its slot (`D3, window on D4`).

Open on an issue whose window or slot is gone recreates what's missing
([failure-modes.md, *Open*](docs/failure-modes.md)).

## 5. Ship and the review round

### The round's files

A round lives in `<store>/reviews/<KEY>/`, beside the issue's other rounds, and the watcher commits
it like any other store change.

| File | Written by | Sent by | Then |
| --- | --- | --- | --- |
| `commit.txt` | Claude | `pr`: `git add -A`, `git commit -F` | deleted, so the next change needs a new message |
| `pr-body.md` | Claude, from `references/pr-body.md` | `gh pc` for a new PR, `gh pb` to update | kept; every `pr` replaces the body |
| `replies/<comment id>.md` | `pr_threads.py --drafts` writes the context, Claude the reply | `pr_threads.py --post`: reply, then resolve the thread | deleted once the thread resolves |
| `issue.md` (Jira only) | Claude: the description as Jira holds it, with Done boxes ticked | `checklist.py`, only when box state is the one difference | deleted; any other difference is refused and the file kept |
| `*.unsent` | Ship, from a file edited while `pr` ran | nobody | Done refuses until they're dealt with |

A store ticket's Done boxes are ticked in the ticket's own `## Done` section, so it has no
`issue.md`. The store's git history keeps every file `pr` sent and deleted.

Rules the files follow:

- **The commit subject carries the key**, e.g. `Add X (CIAM-123)`. `gh pc` takes the branch's
  first commit subject as the PR title, the squash merge keeps it, and Jira's GitHub integration
  matches the key in it.
- **The commit message ends `Assisted-by: Claude:<model>`; the PR body carries no attribution.**
  `ghbody.sh` refuses a body containing `Assisted-by` or `Co-authored-by`, which is why `gh pc` is
  used instead of `gh pr create --fill`.
- **No Jira smart commits.** A `#<word>` beside an issue key (`#close`, `#comment`, `#time`) in a
  commit message, PR title or PR body would let Jira's GitHub integration transition or comment on
  the issue. `pr.sh` and `ghbody.sh` refuse any line that names a key and carries a `#<word>` after
  whitespace or at the line's start, and the `no_terminal_jira_transition.py` hook refuses the same
  in a `git commit` or `gh pr` command typed directly.
- **A reply file opens with an HTML comment** holding the thread's path, line, comment and diff
  hunk, so the reply is read against what it answers. `--post` strips it.

### Ship

Ship runs `ship.check()`, which refuses by name: a branch that isn't the issue's, a missing
`pr-body.md`, a tree with changes and no `commit.txt`, a reply file not named
`replies/<digits>.md`, an empty reply, or two replies to one thread. On success it runs `pr <KEY>`
in an integrated terminal of the issue window, so its output shows as it runs.

- **Non-zero exit:** the terminal shows why, and nothing else happens.
- **Zero exit:** any round file edited while `pr` ran, which `pr` has since sent and deleted, is
  saved beside it as `<file>.unsent` (`.unsent2` and so on, never over a file).

`pr` writes no tracker field to link the PR; the key in the branch name and PR title does that.
Ship again is the next round, and with nothing left to send, `pr` sends nothing.

### `pr [done] [--no-copilot] [KEY]`

`KEY` defaults to the key in the branch name (`fix/CIAM-88-go-requests` is `CIAM-88`). Each step
runs only when it has something to do, so `pr` is safe to repeat:

| Step | When | Does |
| --- | --- | --- |
| guard | always | refuses the default branch, and a PR closed unmerged |
| merged? | the PR is merged | refuses if HEAD isn't the merged head or the tree has changes; sends any leftover replies and ticks; says to run `plan done`; exits |
| commit | the tree has changes | needs `commit.txt`; `git add -A`, `git commit -F`, deletes the file |
| push | always | `GIT_TERMINAL_PROMPT=0 git push -u origin HEAD` |
| Copilot | an existing PR whose head the push moved | `gh pr edit --add-reviewer @copilot` |
| PR | always | `gh pc` with no PR, else `gh pb` |
| round | `replies/` or `issue.md` exist | `pr_threads.py --post`, then `checklist.py`, after the push |
| hand-off | always | records the PR on the branch's git config and prints its URL |

`pr done` runs the merged-PR step and refuses an unmerged PR. `--no-copilot` skips the Copilot step,
and `gh pc` then doesn't ask Copilot to review a new PR either.

### Review: co-signing and dispositions

Accepting a review means co-signing the PR: taking responsibility, as a second coworker who read
the change and agrees with it, that it's correct, proper in every way the PR touches, and ready to
ship. It isn't an adversarial pass looking for reasons to reject; it's the second half of making
the change.

The reviewer records a disposition for every changed file: its path, its blob SHA
(`git rev-parse HEAD:<path>`, or `git hash-object <path>` for a file not yet committed), the passes
run against it, and a verdict: OK, meaning the reviewer vouches for that file, or a list of
findings.

A file marked OK, whose blob SHA hasn't changed since, is not reviewed again. A later round reviews
only the files whose blobs changed since their last disposition.

Recommending fix code is encouraged, and the reviewer owns what they recommend. When the author
applies a recommended fix verbatim, the finding is resolved immediately, with no further review of
that fix. `pr_threads.py --post` posts whatever reply a thread's file holds, such as "Fixed: applied
your suggestion verbatim", and resolves the thread in the same pass, so writing that reply and
running `--post` is what resolves it.

When an aspect of the change, or its breadth, can't be reviewed fully by reading (every call site
of a rename, every row of a generated file), the reviewer writes a Python check script so that
coverage is mechanical instead of eyeballed. The script goes in `<store>/reviews/<KEY>/` with the
round, unless it's worth keeping for later rounds; then it's committed with the PR, under
`skills/jira-issues/scripts/`. Either way, its output is part of the disposition.

## 6. Done

Done shows once a PR for the issue's branch is found (`gh pr view <branch>` or
`gh pr list --head <branch>`) and merged. `lib/done.py`'s `finish()` checks everything before it
changes anything.

The checks, in order: the PR is merged; it belongs to this checkout's `origin`; the worktree is
clean, holds no ignored files outside `disposable`, and its HEAD is the PR's merged head or behind
it; no round files are left unsent; the worktree isn't locked; the main checkout's base can
fast-forward to `origin/<base>`.

The changes: fast-forward the base, `git worktree remove`, delete the workspace file (a warning if it won't go), delete every
branch named for the issue whose tip is in the PR, note the PR and merge commit on the issue (an ADF
comment in Jira, a `## Log` line in a ticket), free the issue's workspace slot, and print the close
note:

```text
Close CIAM-123 in Jira: https://cryoport.atlassian.net/browse/CIAM-123
```

For a store ticket, the note names the file to set `status: done` in.

**Done never changes an issue's status.** `lib/done.py` has no call to a transition, and the
`no_terminal_jira_transition.py` hook refuses any Bash command that looks like it moves a Jira
issue to a terminal status. Done accepts an issue that is in progress or already closed.

A Done that stops part way says what it already changed, and Done again finishes the rest
([failure-modes.md, *Done*](docs/failure-modes.md)).

## 7. Pages and the browser

### The local server

One Flask server per machine, on `127.0.0.1` at a fixed port, started at login by a LaunchAgent.
`plan serve start|stop|restart|status` controls it; the command renders nothing itself. The
server's routes only read:

| Route | Serves |
| --- | --- |
| `/` | Every issue in progress, with links to its pages |
| `/issues/<KEY>` | A store ticket, rendered from its Markdown |
| `/issues/<KEY>/report`, `/issues/<KEY>/review` | A redirect to the latest round's page |
| `/issues/<KEY>/rounds/<n>/report` | Round `n`'s report, from the round data and `git diff` |
| `/issues/<KEY>/rounds/<n>/review` | Round `n`'s findings and dispositions |
| `/issues/<KEY>/files/<name>` | A file from `<store>/reviews/<KEY>/` |
| `/milestones` | Milestone progress |

Pages are Jinja templates rendered from the issue's data; Claude writes round data, never HTML.
A page's URL path keys name it, and the server renders it on each request, so a page exists as soon
as its data does and no command publishes it. A URL whose target doesn't exist redirects to the
nearest page that does: a missing round to the latest round, and an issue with no rounds to the
issue's page. The redirects are 302s, since the browser caches a 301 and the latest round changes.
An unknown key gets a 404 page that says what was asked for and what exists, and links `/`.
Every page's header links the issue (its Jira browse URL, or `/issues/<KEY>` for a store ticket)
and its pull request; before a PR exists, the header says so. Pages show commands to copy and have
no buttons that act. A page is always opened at its server URL, never as a `file://` path.

### `plan browse`

`plan browse` takes a key (the issue's page), `pr#N` (the pull request) or a URL. `[KEY]` names the
issue whose window the page belongs in when neither the target nor the terminal names one.
`plan browse` and the integrated browser are SK-0031's work and not built yet; until then Start
and Open open a Jira page in `browserApp` (§4, step 9).

`plan browse` will print the URL, then pick the issue window: an explicit key (Start passes its own),
else the key in the calling terminal's environment, else the issue window on the Desktop that is showing
([architecture §7.6.1](docs/architecture.md#open-url)). The page will open in that window's integrated
browser, and a link clicked in one of its terminals opens there too.

Every step is best-effort; the printed URL stands on its own. System `open` is never used for a
page, since it sends the page to the default browser, outside every issue window. The
`no_bare_open.py` hook refuses it in an agent's shell (§9).

### Browser automation and tests

Browser tests are scripts that run in Chrome or Chromium with the test suite. The
`chrome-devtools` MCP server (Chrome, `--isolated`) is for investigating an issue, not for
checking a change. Neither runs in an issue window, whose integrated browser is the human's own.

## 8. The helpers

All are stdlib Python or shell, in `skills/jira-issues/scripts/`.

- **`ghbody.sh`**, run as `gh pc` and `gh pb`. `gh pc` takes the title from the branch's first
  commit subject (`git log --reverse origin/<base>..HEAD`) and the body from `pr-body.md`, then asks
  Copilot to review. `gh pb` replaces the body (`gh pr edit --body-file`). Both refuse an
  attribution line and a smart-commit token. `GHBODY_DRY=1` prints the command instead of running
  it.
- **`pr_threads.py <PR>`** lists unresolved review threads. `--drafts DIR` writes each thread's
  context file; `--post DIR` checks every file before the first post, then replies over REST and
  resolves over GraphQL.
- **`checklist.py <KEY> FILE`** compares the file with the live Jira description, read as ADF and
  rendered to Markdown with box state normalized. Any other difference is refused, naming the line.
  Otherwise it writes the box changes back as ADF, comments listing each box, reads the issue back,
  and deletes the file.
- **`new_issue [--check] FILE`** creates a Jira issue from front matter (`project`, `type`,
  `summary`, `blocked_by`, `relates`) and a body. `CIAM` needs Compliance Tags
  (`customfield_11325`, default `"Not Required"`) and `duedate` at creation; the
  `jira-ciam-create` skill's script builds that payload. `--check` only reads. A second run finds
  the issue by summary and adds only the missing links. Store tickets are made with `plan new`.
- **`outline.py FILE`** checks an issue body for Task, Guardrails, Lenses and Done, at least two
  Done boxes, and a Done line naming every ticked lens. The checker runs it on every ticket.
- **`ghread.py`** reads GitHub for the other scripts.

## 9. Claude's side

| Piece | Installed at | Source in skuld | What it does |
| --- | --- | --- | --- |
| The jira-issues skill | `~/.claude/skills/jira-issues/` | `skills/jira-issues/` | How issues, commits, PRs and reviews are done; loaded when Claude picks up an issue, writes a commit or PR body, or answers a review |
| `CLAUDE.md` | the configured repo's root | `claude/CLAUDE.md` | Boundaries, commands and gotchas, read every session |
| Settings | `<repo>/.claude/settings.json` | `claude/settings.json` | The `Assisted-by:` trailer, allow and deny lists, the hooks |
| Attribution hook | `<repo>/.claude/hooks/` | `claude/hooks/no_ai_attribution.py` | Refuses a commit or PR that names an AI as author, and a commit without `Assisted-by:` |
| Terminal-transition hook | `<repo>/.claude/hooks/` | `claude/hooks/no_terminal_jira_transition.py` | Refuses a command that looks like it moves a Jira issue to a terminal status, or carries a smart-commit token |
| Page-open hook | `<repo>/.claude/hooks/` | `claude/hooks/no_bare_open.py` | Refuses system `open` on a URL or `.html` file, and names `plan browse` |

The skill tells Claude to read the issue before writing about it, never from memory; to check the
body with `outline.py` before coding; and when the work is done, to write the round's files and
stop, so the human verifies them and runs Ship. For a review, it runs `pr_threads.py --drafts`,
fixes the code, and writes each reply under its context.

The settings set `attribution.commit` to the `Assisted-by:` trailer, leave `attribution.pr` empty
and turn `sessionUrl` off. They deny `git push`, `git rebase` and `git reset`, and reads of `.env`,
keys, secrets and any `.netrc` in the working tree. They allow the read-only git commands and the
tests. All three hooks run before every Bash call; each exits 2 with its reason to refuse, and
each has a `--check '<command>'` mode for trying a command by hand.

## 10. Patterns worth copying

These recur through the code, and they're worth taking away even without the tools.

1. **Every step runs only when it has something to do.** Start, `pr`, Done, `checklist.py` and
   `new_issue` can run twice, or after a crash, and end where a clean run would
   ([failure-modes.md](docs/failure-modes.md)).
2. **Check everything, then change anything.** Done and `pr_threads.py --post` refuse before their
   first write rather than stopping half way.
3. **Read back writes the server may drop.** A Jira transition can return success while a workflow
   condition blocks the change, so every status write reads the issue back and refuses, naming
   both statuses.
4. **Compare-and-swap on refs.** `git update-ref <ref> <new> <old>` refuses if a branch moved since
   it was checked.
5. **Exclusive lock files with a pid, never taken over.** `open(file, "x")` in the git common dir;
   a stale lock is reported with its path.
6. **Atomic hand-off files.** Write `x.tmp`, then rename it.
7. **Secrets from `~/.netrc` per call.** Nothing in env vars, argv, files or logs; `gh` holds the
   GitHub credentials.
8. **A refusal that names its fix** instead of recovery code.
9. **Outside systems behind ports.** Each tracker, GitHub and git remote is reached through its
   port's adapter, and the `WindowPlacer` (`lib/placer.py`, `lib/spaces.py`)
   alone runs `code`, `skuld-spaces` and System Events, so the rest of `lib/`
   tests without a real terminal ([architecture §6](docs/architecture.md#components)).
10. **A gate that fails loudly.** The watcher commits nothing that fails the checker, and says so
    in a notification and a log.

## 11. Testing

```sh
python3 -m unittest discover -s lib -t .
python3 -m unittest discover -s skills/jira-issues/scripts -p 'test_*.py'
python3 claude/hooks/test_no_ai_attribution.py
python3 claude/hooks/test_no_terminal_jira_transition.py
python3 claude/hooks/test_no_bare_open.py
(cd vscode/plan-window && node --test)
```

Tests build real temporary git repos with a bare `origin`, a fixture store in a temp dir, and a
fake Jira client, an injected object or a local stub HTTP server. No test touches the network.
`lib/test_start.py` and `lib/test_done.py` inject faults: each step is made to fail once, and the
test checks that the next run converges. The watcher's debounce and gate test against a fake clock
and a temp repo. Each hook's test runs it both as a function and as a process, with JSON on stdin
and the exit code out.

## 12. Limits

- **macOS only:** native Desktops, launchd and the Keychain-free `~/.netrc` setup
  all assume it.
- **One user and one Jira site:** `cryoport.atlassian.net`, with the `netrc` machine name in
  config.
- **No in-terminal browsing:** pages open in the issue window's integrated browser, never in a TUI
  browser or through system `open`.
- **GitHub only for pull requests;** GitHub Issues is not used.

## 13. Package index

```text
GUIDE.md                   this file
store.yml                  the store's projects (key, name, repo remotes), archive delay, debounce
bin/
  plan                     plan's entry point
  jira-get                 one read-only Jira GET with ~/.netrc auth
  gh-aliases.yml           gh pc and gh pb
  build-spaces             builds macos/build/skuld-spaces with swiftc
macos/spaces.swift         skuld-spaces: read-only SkyLight calls listing Desktops and VS Code windows
lib/
  cli.py                   subcommand dispatch and the fzf picker
  config.py                reads the machine config and store.yml
  jira.py                  the Jira adapter: every Jira REST v3 call, transitions with read-back
  start.py                 Start and Open: repo resolution, names, worktrees, the lock
  ship.py                  Ship's checks and round-file naming
  done.py                  Done: checks, then changes, then the close note
  term.py                  the workspace file, the slots file, SketchyBar's trigger, Ship's tmux window
  placer.py                the WindowPlacer port, Placement and placerFrom()
  spaces.py                SpacesPlacer: native Desktops through skuld-spaces and System Events
  test_*.py                the unit tests
  (the FileStore adapter, the checker, issue-watch and the local server also live in lib/)
templates/issue.md         what plan new fills in
skills/jira-issues/        SKILL.md, references/ (pr-body.md, jira-issue.md),
                           scripts/ (pr.sh, ghbody.sh, pr_threads.py, checklist.py,
                           new_issue.py, outline.py, ghread.py, test_scripts.py)
claude/
  CLAUDE.md                a configured repo's Claude instructions
  settings.json            attribution, permissions, the hooks
  hooks/                   no_ai_attribution.py, no_terminal_jira_transition.py,
                           no_bare_open.py, and a test for each
vscode/plan-window/        the VS Code extension that opens an issue window's ticket and Claude
                           terminal: extension.js (the VS Code calls), plan.js (what they
                           need, from the plan.* settings), plan.test.js
docs/
  architecture.md          what the system is, and its rules (§10)
  failure-modes.md         each command's failure-mode table
  issue-format.md          the ticket file spec
  plan-file-issue-store.md the plan for the ticket store
```
