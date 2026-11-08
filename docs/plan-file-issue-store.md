<!-- markdownlint-disable MD013 -->

# Plan: a file-backed issue store for `plan` and `pr`

## Contents

- [1. Context and scope](#context)
- [2. Principles](#principles)
- [3. Goals and non-goals](#goals)
- [4. Design](#design)
  - [4.1 Ports](#ports)
  - [4.2 The store's layout](#layout)
  - [4.3 Projects, repos and where `plan` runs](#projects)
  - [4.4 Milestones and ordinal](#milestones)
  - [4.5 The issue file](#issue-file)
  - [4.6 The checker](#checker)
  - [4.7 The template and `plan new`](#template)
  - [4.8 Archiving](#archiving)
  - [4.9 The git-backed store and the watcher](#git-store)
- [5. Phases](#phases)
- [6. Open questions](#open-questions)
- [7. Alternatives considered](#alternatives)
- [8. Cross-cutting concerns](#lenses)
- [9. Redmine survey: what carries over](#redmine-survey)

<a id="context"></a>

## 1. Context and scope

`plan` and `pr` exist twice. In gh-plan they run against Redmine, through a VS Code extension, the gh-issues scripts and
a `plan` package that [ADR 0014](../../gh-plan/docs/adr/0014-terminal-client.md) starts ([#185](http://127.0.0.1:3010/issues/185)). In this repo they run
against Jira Cloud as a terminal CLI (`lib/`), with `pr` still a shell script in `skills/jira-issues/scripts/pr.sh`.

This repo also already has a small file tracker: `lib/fileissues.py` stores `JP-<n>` issues as Markdown with front
matter in `issues/` ([architecture §7.7](architecture.md#file-backed-issues)). It answers in Jira's shapes (ADF
bodies, `fields.status.statusCategory`), so `start.py`, `ship.py` and `done.py` never see the difference.

This plan makes a file store the tracker `plan` works against first: Markdown issue files in a folder that lives outside
every development repo, later a git repo of its own, committed and pushed by a watcher. Jira and Redmine become adapters
behind the same port.

Each run touches up to three places:

- **Skuld** holds the code and the definitions: the library, the `plan` and `pr` commands, the helper scripts (checker,
  watcher, archiver), the format spec, the issue template, and the canonical `store.yml` that names the projects and
  their repos.
- **The ticket store** holds data only: ticket files and review files. It sits at a location the local config names,
  later in a git repo with a remote of its own, separate from skuld's, and the watcher commits and pushes every change
  to it.
- **The configured repo** is the development repo `plan` or `pr` runs in. Its branches, worktrees and PRs are what the
  commands change there; the tickets and reviews they write go to the store.

gh-plan kept issues as files once. [ADR 0001](../../gh-plan/docs/adr/0001-git-is-the-database.md) and
[ADR 0008](../../gh-plan/docs/adr/0008-file-native-items.md) kept items as files inside each repo, and
[ADR 0011](../../gh-plan/docs/adr/0011-redmine-is-the-tracker.md) moved to Redmine because of what _inside the repo_
cost: a direct commit to the default branch on every start, and `ready.yml` conflicts between branches. A store in its
own repo has neither cost. The third cost ADR 0011 named, owning what a tracker gives for free (web page, board, cycle
checks), stays, and the scope below keeps that list short.

<a id="principles"></a>

## 2. Principles

1. **`plan` and `pr` are Python, one package, in modules.** Every outside system is reached through a module interface:
   the issue store, the review store, the git remote and PR service (GitHub). Policy code names no tool.
2. **Files first.** The first store is a folder of Markdown files. Git, the remote, the watcher and archiving come after
   `plan` works against the folder.
3. **The store lives outside every development repo, skuld included.** Skuld carries the code and the definitions; the
   store is a configured location with its own git remote, and holds tickets and review files. A local clone may place the store inside one project as a
   submodule; that is local configuration, not something the tool assumes.
4. **Issues are edited in place.** Git history is the audit trail, so a file is never copied to record a change. Only a
   closed issue moves, from `active/` to `archive/`.
5. **The format is strict, and a checker enforces it.** Every writer, a person, `plan` or an agent, produces files the
   checker accepts. The watcher runs the checker before it commits and fails loudly when it doesn't pass.
6. **New issues start from a template.** `plan new` fills it in; nobody writes front matter from memory.

<a id="goals"></a>

## 3. Goals and non-goals

Goals:

- `plan list`, the picker, `view`, `start`, `ship` and `done` work against the file store as soon as the store exists.
- One issue format, specified in one place, checked by one script.
- The store can move from a plain folder to a git repo with a remote without changing a file.
- Jira (work) and Redmine (home, while it lasts) keep working as adapters.
- Review files live in the store beside the tickets, so they are committed and pushed the same way and no longer sit in
  each repo's ignored `.tmp/`.

Non-goals, for now:

- A web page per issue, a board or a Gantt. Files open in nvim; the picker is the list.
- Multi-user editing. One person, two Macs at most.
- Migrating Redmine's history. Open issues may be imported ([§5](#phases), phase 8); closed ones stay in Redmine's
  backup.

<a id="design"></a>

## 4. Design

<a id="ports"></a>

### 4.1 Ports

Four interfaces, each a module under `lib/` (later the `plan` package) with one class per adapter:

| Port           | Does                                                                                       | Adapters                                                |
| -------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------- |
| `IssueStore`   | list next up, read an issue, create, set fields, transition, append a log line, close note | `FileStore` (first), `Jira` (exists), `Redmine` (later) |
| `ReviewStore`  | record a round, its findings and outcomes; read them back                                  | files under `<store>/reviews/<KEY>/` (first), SQLite    |
| `PullRequests` | open or update a PR, read its state and threads, post replies                              | `gh`                                                    |
| `GitRemote`    | fetch, push, rebase onto the base, worktree add and remove                                 | `git`                                                   |

The port speaks a neutral model, an `Issue` dataclass (key, title, status, type, milestone, ordinal, after, parent,
blocked_by, body, log), not Jira's JSON. Today's `FileIssues` builds Jira-shaped dicts so the callers needn't change;
phase 3 inverts that: `Jira` maps its JSON into `Issue`, and the callers read `Issue`. Until then `FileStore` keeps
answering in the shapes callers read.

`lib/cli.py`'s `jiraFrom(cfg, key)` already picks a backend per key from its prefix. It becomes `storeFor(cfg, key)`: a
prefix skuld's `store.yml` lists goes to `FileStore`, any other to the machine's tracker adapter.

<a id="layout"></a>

### 4.2 The store's layout

```text
skuld/                         the code and the definitions
  lib/                         the library: plan, pr, FileStore, the checker
  bin/                         plan, pr, issue-check, issue-watch
  store.yml                    projects (key, name, repo remotes), archive delay, debounce
  docs/issue-format.md         the file spec
  templates/issue.md           what plan new fills in

<store>/                       data only: e.g. ~/issues, or a submodule's checkout; its own remote
  milestones/
    m5-terminal.md
  active/
    SK-12-file-store-port.md
  archive/
    SK-3-verify-ghostty-argv.md
  reviews/
    SK-12/                     one issue's review rounds: PR body, findings, replies
```

`<store>` comes from `~/.config/jira-plan/config.json`'s `issueStore` key (today's `issuesDir`, renamed). The review
files `pr` reads from `<repo>/.tmp/<KEY>/` today move to `<store>/reviews/<KEY>/`, so a round's PR body, findings and
replies are committed with the ticket they belong to. Their format is specified with the `ReviewStore` port in phase 4;
until then the checker only requires that `<KEY>` names an issue in the store or another configured tracker. A store holds
any number of projects, each with its own key prefix (`SK`, `GH`, `TT`, …, the `PREFIX-number` keys ADR 0014 already
settles on). Numbers are per prefix and never reused; the next is one above the highest found in `active/` and
`archive/` together.

<a id="projects"></a>

### 4.3 Projects, repos and where `plan` runs

Today `lib/fileissues.py` serves one hard-coded project, `PROJECT = "JP"`, and `lib/config.py` defaults `checkouts.JP`
and `issuesDir` to skuld's own root. Both go. `store.yml` names the projects and the repos each one works in, the
machine config lists where this Mac has those repos checked out, and `plan` and `pr` work out everything else from the
key they are given or the folder they run in.

```yaml
# skuld/store.yml: the canonical project list, shared by every machine that clones skuld
projects:
  - key: SK
    name: Skuld
    repos: # remotes, never paths
      - git@github.com:pjfitzgibbons/skuld.git
archiveAfterDays: 14
debounceSeconds: 5
```

```json
// ~/.config/jira-plan/config.json: this machine only
{
  "issueStore": "~/issues",
  "checkouts": ["~/programming/skuld", "~/work/cryo-gitops-github"],
  "jira": {
    "CIAM": { "repos": ["git@github.com:cryoport/cryo-gitops-github.git"] }
  }
}
```

A repo is identified by its `origin` remote, so one entry matches a clone at any path on any machine. Store projects
list their repos in skuld's `store.yml`, which travels with skuld. Jira projects list theirs in the machine config, since
only the work Mac has them. One project may have several repos (`CIAM` does); one repo belongs to one project, and the
checker refuses a remote listed twice.

`checkouts` is a plain list of this Mac's main checkouts. Each path's `origin` is matched against the remotes above, so
the project a checkout serves is never written twice, and a path whose remote no project lists is reported as an
error. A remote with no checkout on this Mac is simply not worked on here.

<a id="environment"></a>

On every run, `plan` and `pr` resolve their environment as `mainCheckout()` does
([architecture §7.0](architecture.md#repo-resolution)), with remotes naming the projects:

1. **A key decides.** When the command has a key (Open, Start, Ship, Done, view), its prefix gives the project, and
   the project's repos that appear in `checkouts` are the candidates. The current folder may belong to another
   project; `plan open CIAM-9` works from inside skuld.
2. **Existing work wins.** Each candidate is searched for `<repo>.worktrees/<KEY>` or the issue's branch. Found in
   one repo, that repo is used; found in more than one, the command refuses and names each.
3. **New work.** With nothing found, the current folder's repo is used when it is a candidate (a run inside
   `<repo>.worktrees/SK-12` means `<repo>`, through `git rev-parse --git-common-dir`). Otherwise one candidate is
   used outright, several open an `fzf` pick, and none is refused, naming the project's remotes and `checkouts`.
4. **No key.** `plan list` and the picker show the current repo's project first when run inside a configured repo,
   and every configured project anywhere else.

The resolved repo gives the project, its tracker (`FileStore` or `Jira`), the main checkout, the worktree root
`<repo>.worktrees/`, the base branch, and the store folders the run writes to (`active/` for tickets,
`reviews/<KEY>/` for review files).

A run then works in two places. In the configured repo it makes the git and GitHub changes: Start's branch and
worktree, Ship's commit, push and PR, Done's cleanup. In the store it makes the ticket and review changes: status, log
lines, review rounds. It never writes a ticket into the configured repo, and never touches a development repo from
the store. Even a run inside skuld edits skuld's code in skuld and `SK-` tickets in the store.

`FileStore` answers for every key whose prefix `store.yml` lists, and `storeFor()` sends any other key to the
machine's tracker adapter. A `plan new` for an unlisted prefix is refused, naming `store.yml`; adding a project is one
entry there, committed to skuld. `plan init-store` creates an empty store's folders at `issueStore`.

The `name` is what the picker and `plan list` show beside the key, which also fills the `projectMarkers` gap: that config key has no reader today.

**Skuld is the first project.** The store starts with one entry, `SK` (Skuld), and skuld's own backlog moves into it:
`JP-1` to `JP-9` become `SK-1` to `SK-9`, numbers kept, so a reference in a commit or an issue body maps by prefix
alone. Seven of the nine are open and none has a branch or worktree, so nothing in flight is renamed. Skuld's `issues/`
folder is then deleted, and from that point every skuld change is planned, started and shipped through the store it
builds.

<a id="milestones"></a>

### 4.4 Milestones and ordinal

Milestones organize the work, and `ordinal` orders it, as Redmine's versions and `Ordinal` field do in gh-plan.

A milestone is a file, `milestones/<id>.md`, with front matter and a Markdown body for its exit criterion. Milestones
belong to the store, not to a project, so one milestone can span projects, the way gh-plan's versions are shared across
the `plan` tree:

```markdown
---
id: m5-terminal
name: M5 Terminal
status: open # open | locked | closed
ordinal: 500 # orders milestones; steps of 100
due: # optional date
---

`plan` runs Start, Ship and Done from a terminal, with no VS Code on the path.
```

An issue joins one milestone with `milestone: m5-terminal`, or none by leaving it empty. `ordinal` is an integer on both
milestones and issues, set in steps of 100 so a new one fits in a gap without renumbering. It replaces today's `rank:`
field; the JP files' `rank:` values carry over as `ordinal:`.

Plan order, which the picker, `plan list` and `plan next` all use:

1. Issues in a milestone first, by the milestone's `ordinal`; issues with no milestone after them.
2. Within a milestone, open issues that block others first ([#114](http://127.0.0.1:3010/issues/114)), then by the issue's `ordinal`, then unordered issues
   by number.
3. A store issue with `after: <JIRA-KEY>` still sorts right after that Jira issue in the merged picker, as today.

`plan set SK-12 milestone=m5-terminal ordinal=250` moves an issue, and `plan milestone new`, `plan milestone set` and
`plan milestone list` manage milestones, all through the checker. A milestone report (the `plan-report` skill's
`milestones.py`) reads `milestones/` and the issue files directly.

<a id="blockers"></a>

**Blockers.** An issue records what blocks it in `blocked_by: [SK-4, SK-7]`, the store's form of Redmine's `blocks`
relation and Jira's issue links. Only the blocked side is written; "blocks" is derived by reading every file, so the two
directions can't disagree. The relation is tracked end to end:

- The checker refuses a `blocked_by` key that exists in neither `active/` nor `archive/`, and any cycle, as Redmine
  refuses one.
- A blocker counts as cleared when its status is `done` or `wontfix`; an archived blocker is closed, so archiving never
  breaks a relation.
- `FileStore.blockers(issue)` returns the open ones, so Start lists them and asks before it goes on, exactly as it
  does for a Jira issue.
- The picker and `plan view` show an issue's open blockers and what it blocks; plan order puts blocking issues first
  within a milestone (rule 2 above).
- A key from another tracker (`blocked_by: [CIAM-163]`) is allowed. The checker can't see it offline, so it skips that
  key, and Start asks that tracker's adapter whether it is still open.
- `plan set SK-12 blocked_by+=SK-4` and `blocked_by-=SK-4` add and remove one, checked first.

<a id="issue-file"></a>

### 4.5 The issue file

```markdown
---
key: SK-12
title: Read and write issues through an IssueStore port
status: open # open | in-progress | done | wontfix
type: task # task | bug | question
opened: 2026-11-07
closed: # set when status leaves open/in-progress
milestone: m5-terminal # optional; a milestones/ id
ordinal: 300 # optional; steps of 100
after: # optional; a Jira key to sort after in the merged picker
parent: # optional key
blocked_by: [] # keys, flow list; see Blockers
points: # optional: 1 2 3 5 8 13
---

### Task

…

### Guardrails

…

### Lenses

- [ ] L1 Security: …

### Done

- [ ] …

## Log

- 2026-11-07: Started on `feat/SK-12-issue-store-port`, in ~/programming/skuld.worktrees/SK-12.
```

Changes from today's `issues/README.md`: `key` and `title` move into the front matter, so the checker can compare the
key with the file name and the title isn't a heading convention; `guide:` drops (it was this repo's own); `rank` becomes
`ordinal`; `milestone`, `parent`, `blocked_by` and `points` arrive. No `repo` field: the project's repos come from `store.yml`, and Start uses the one `plan` is run in ([4.3](#environment)). The body is the house outline (Task,
Guardrails, Lenses, Done), the one `skills/jira-issues/scripts/outline.py` already checks. `## Log` stays: git says who
changed the file and when, the log says what `plan` did (started, shipped, merged), in the file where a reader looks.

The file name is `<KEY>-<slug>.md`, the slug fixed at creation. A title change doesn't rename the file, so `git log` on
it needs no `--follow`.

<a id="checker"></a>

### 4.6 The checker

`issue-check [PATH…]`, stdlib Python, exits non-zero on any error, naming file, line and rule. With no path it checks
the whole store. Rules:

- The front matter parses under the restricted subset (scalars, comments, flow lists), every key is known, every
  required key is present, and each value has its type and enum.
- `key` matches the file name; the prefix is one `store.yml` lists; no key appears twice in `active/` and `archive/`.
- `parent` and `blocked_by` name store keys that exist, or keys of another tracker, left unchecked; no `blocked_by`
  cycle ([Blockers](#blockers)).
- Every repo remote in `store.yml` appears under one project only.
- `closed` is set exactly when `status` is `done` or `wontfix`; an issue in `archive/` is closed.
- `milestone` names a file in `milestones/`; an open issue isn't in a closed milestone (Redmine refuses the same); a
  milestone's `id` matches its file name.
- The body passes `outline.py`; `## Log` is last and every line is `- YYYY-MM-DD: text`.
- Reality checks as they come (gh-plan [#21](http://127.0.0.1:3010/issues/21)): `points` above 5 on an issue in a milestone, for one.

`FileStore` runs the same checks on every file it writes, before the write, so `plan` can't produce a file the watcher
would refuse. The checker is the module; `issue-check` is its command line.

<a id="template"></a>

### 4.7 The template and `plan new`

`plan new <PREFIX> "<title>"` takes the next number, fills skuld's `templates/issue.md` (key, title, `opened`,
`status: open`, the empty outline with every lens listed), writes `active/<KEY>-<slug>.md`, runs the checker on it, and
opens it in `$EDITOR`. An agent drafting an issue runs the same command and edits the file it prints, so no agent writes
front matter by hand. `plan set <KEY> field=value…` changes front-matter fields, all or nothing, checked first.

<a id="archiving"></a>

### 4.8 Archiving

`plan archive` moves every closed issue whose `closed` date is older than `store.yml`'s `archiveAfterDays` from
`active/` to `archive/`, with `git mv` once the store is a repo. Its schedule (launchd daily, or on the watcher's
commit) is chosen when it's built. `FileStore` reads both folders, so a key resolves whichever one holds it.

<a id="git-store"></a>

### 4.9 The git-backed store and the watcher

Phase 6 makes `<store>` a git repo with a remote. Phase 7 adds the watcher:

- launchd runs `issue-watch` with `WatchPaths` on `active/`, `archive/`, `milestones/` and `reviews/` (or `fswatch` under a `KeepAlive` agent if
  `WatchPaths` misses nested changes).
- It waits for a quiet period (debounce, a few seconds, set in `store.yml`) so a mass edit or a reorder lands as one
  commit.
- It runs the checker. On failure it commits nothing, posts a macOS notification naming the file and rule, writes the
  same to a log, and tries again on the next change.
- On success it commits every change with a generated message (`SK-12: status in-progress`, or `12 issues: ordinal`) and
  pushes. A push that fails (offline, behind) is retried with `pull --rebase` first, and a conflict stops the watcher
  loudly until a person resolves it.

A submodule placement is the same repo checked out somewhere else; the watcher watches whatever path `issueStore` names.

<a id="phases"></a>

## 5. Phases

Each phase is usable on its own, and the files written in phase 1 stay valid through phase 7.

| #   | Phase                         | Delivers                                                                                                                                                                                                                                                                                                 | Reuses                              |
| --- | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| 1   | Format                        | in skuld: the file spec `docs/issue-format.md` (replacing `issues/README.md`), `templates/issue.md`, the `store.yml` and milestone schemas, the checker and `issue-check`                                                                                                                                | `outline.py`, today's `_parse`      |
| 2   | FileStore, dogfooded on Skuld | `FileIssues` generalized: projects from `store.yml` instead of `PROJECT = "JP"`, `active/` and `archive/`, a store outside this repo, `parent` and `blocked_by` for `blockers()`; the store created with one project, `SK` (Skuld), and `JP-1`–`JP-9` moved into it as `SK-1`–`SK-9` ([§4.3](#projects)) | `lib/fileissues.py` and its tests   |
| 3   | `plan` on the store           | `plan new`, `plan set`, `plan milestone`, list/picker/view/start/ship/done against `FileStore` in plan order ([§4.4](#milestones)); the neutral `Issue` model; `storeFor()`                                                                                                                              | `lib/cli.py`, `start.py`, `done.py` |
| 4   | Ports and `pr`                | `IssueStore`, `ReviewStore`, `PullRequests`, `GitRemote` as modules; `pr` ported from shell to Python behind them; review files moved from `.tmp/<KEY>/` to `<store>/reviews/<KEY>/` and their format specified                                                                                          | `pr.sh`, `pr_threads.py`, `ship.py` |
| 5   | Archive                       | `plan archive` and its schedule                                                                                                                                                                                                                                                                          | —                                   |
| 6   | Git store                     | the store as its own repo with a remote; `plan` refuses nothing new, it only reads and writes files                                                                                                                                                                                                      | —                                   |
| 7   | Watcher                       | `issue-watch`, launchd plist, debounce, checker gate, loud failure, push                                                                                                                                                                                                                                 | —                                   |
| 8   | Redmine import                | `plan import redmine <project>`: each open issue to a file under a prefix, its description already in the outline; each open version to a milestone file, Redmine's `Ordinal` to `ordinal`, and each `blocks` relation to the blocked issue's `blocked_by`                                               | gh-issues' `redmine.py`             |

Phases 1 to 3 are the short path: after them `plan` runs against a folder.

<a id="open-questions"></a>

## 6. Open questions

Each has a recommendation; none blocks phase 1 except the first two.

1. **One store or one per machine?** The work Mac's issues are Jira's; its personal ones could share the home store.
   _Recommend:_ one store, any machine; Jira stays the work tracker through its adapter.
2. **Front matter parser.** The repo is stdlib-only, and the schema is flat. _Recommend:_ keep a hand parser for a
   restricted YAML subset, which the checker defines, rather than adding PyYAML. Revisit if ADR 0014's `uv` packaging
   arrives here first.
3. **Number collisions across two Macs.** Two offline `plan new` calls can take the same number. _Recommend:_ `plan new`
   pulls first when the store is a repo; the checker's duplicate-key rule catches the rest, and the second file is
   renumbered by hand. Single-user rarity doesn't justify a counter service.
4. **One issue across two repos.** Existing work found in more than one of a project's repos is refused
   ([4.3](#environment)). _Recommend:_ keep refusing until a real case needs one issue's work split across repos.
5. **Does `plan` set `done`?** Today Done never transitions (architecture §7.3), and a person sets `status: done` by
   hand. With files, `plan done` could set it after the merge, since the watcher commits it visibly. _Recommend:_ keep
   the rule for Jira; for the file store, Done sets `status: done` and `closed:` only after it confirms the PR merged.
6. **Where `store.yml` lives.** In skuld it is a definition, versioned with the code that reads it, and the store
   holds data only. In the store, a store would describe itself and could be read without skuld. _Recommend:_ skuld,
   as [4.2](#layout) shows; revisit if a second tool ever reads the store.
7. **Work reviews and the store's remote.** Reviews of `CIAM` PRs land in the work Mac's store, while work issues stay
   in Jira. _Recommend:_ the work Mac's `issueStore` is a store whose remote work data may go to, never the home one.

The code's home is settled: skuld. gh-plan's [#185](http://127.0.0.1:3010/issues/185) starts a `plan` package with the same ports, so [#185](http://127.0.0.1:3010/issues/185) and [#186](http://127.0.0.1:3010/issues/186) are re-pointed to this work or closed ([§9](#redmine-survey)).

<a id="alternatives"></a>

## 7. Alternatives considered

- **Files inside each development repo** (gh-plan ADR 0001, 0008). Every start is a commit to the default branch, and
  branches conflict on shared plan files. A separate repo removes both.
- **An orphan branch per repo** (gh-plan ADR 0010). Removes the commit-to-main cost, adds a worktree and a symlink per
  item worktree, and still scatters the plan across repos.
- **Stay on Redmine.** It works, and it is a server to run, back up and upgrade ([#191](http://127.0.0.1:3010/issues/191), [#119](http://127.0.0.1:3010/issues/119), [#133](http://127.0.0.1:3010/issues/133)), reachable only from
  the home Mac, and its data isn't text an agent can grep.
- **Folders per status** (`open/`, `in-progress/`, `done/`). Every transition is a move, which makes history harder to
  follow; status is one field, and only the archive is a folder.
- **Commit from `plan` instead of a watcher.** Exact messages, but a person's or an agent's hand edit wouldn't be
  committed until the next `plan` write. The watcher commits every writer the same way.

<a id="lenses"></a>

## 8. Cross-cutting concerns

| Lens                           | Answer                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| L1 Security                    | The store holds no credentials. Issue text is untrusted input to agents that read it; the prompt `plan start` gives Claude names the file, it doesn't paste it. The watcher pushes with the user's own git credentials.                                                                                                                                                                                                                                   |
| L2 Privacy and data            | Issue text goes to whatever remote the store's repo has. Work (Jira) issues never enter it, but reviews of work PRs do (open question 7). A private remote is the default.                                                                                                                                                                                                                                                                                |
| L3 Reliability and failure     | Writes are whole-file, checked before writing. The watcher never commits a failing tree, retries a failed push after a rebase, and stops loudly on a conflict. A crash mid-debounce loses nothing: the next start commits what's on disk.                                                                                                                                                                                                                 |
| L4 Performance and limits      | `list` parses every active file; hundreds of files parse in well under a second. No index until that changes.                                                                                                                                                                                                                                                                                                                                             |
| L5 Observability               | The checker names file, line and rule. The watcher notifies on failure and logs every commit and push. `git log` in the store is the full history.                                                                                                                                                                                                                                                                                                        |
| L6 Compatibility and migration | The 9 `JP-` issues become `SK-` issues in the new format in phase 2 (a one-off script, checked), numbers kept; no branch or worktree carries a `JP` key. `issuesDir` becomes `issueStore`, with the old key read once and warned about; `checkouts` becomes a plain list of main checkouts, each matched to its project by its `origin` remote ([4.3](#environment)), and `CIAM`'s repos move to `jira.CIAM.repos`. Jira-shaped dicts stay until phase 3. |
| L7 Usability                   | `plan new` and `plan set` mean no hand-written front matter. Files open in nvim beside Claude, as JP issues do today.                                                                                                                                                                                                                                                                                                                                     |
| L8 Testability                 | The checker and `FileStore` test against a fixture store in a temp dir; the watcher's debounce and gate test with a fake clock and a temp repo. No network.                                                                                                                                                                                                                                                                                               |
| L9 Operations and cost         | One launchd agent, stdlib Python, git. The store needs no server.                                                                                                                                                                                                                                                                                                                                                                                         |
| L10 Docs impact                | `docs/architecture.md` §3 and §7.7, `GUIDE.md` §2 and §4, `issues/README.md` (replaced by the store's spec), the jira-issues skill's issue section.                                                                                                                                                                                                                                                                                                       |

<a id="redmine-survey"></a>

## 9. Redmine survey: what carries over

Redmine at `127.0.0.1:3010`, project `plan` and its subprojects: 102 open issues on 2026-11-07. Ulysses ([#96](http://127.0.0.1:3010/issues/96)–[#101](http://127.0.0.1:3010/issues/101)) and
tempotag ([#75](http://127.0.0.1:3010/issues/75), [#76](http://127.0.0.1:3010/issues/76)) are other products and stay where they are. Of the GH Plan issues, these bear on this plan.

**Reuse, re-scoped to this plan:**

| Issue | Subject                                                                  | Becomes                                                                                               |
| ----- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| #185  | Run next, status and worktrees from a plan command                       | Phase 3's read commands; its "policy names no tool" is principle 1                                    |
| #186  | Run Start, Open, Go, Ship and Done from the plan command                 | Phase 3 for the store, phase 4 for `pr` in Python; most of its scope already runs here                |
| #42   | cli.py: query commands with --json                                       | Fold into #185: `list`, `show`, `next`, `validate` over `FileStore`                                   |
| #44   | Write commands: new, set, ice, schedule                                  | Phase 3's `plan new` (template) and `plan set` (all or nothing, checked)                              |
| #63   | Pre-commit hook that runs plan validate                                  | Phase 7's checker gate in the watcher, and a pre-commit hook in the store repo                        |
| #64   | Dockerfile running plan validate in CI                                   | The store repo's CI runs `issue-check` (no Docker needed)                                             |
| #34   | End-to-end test over the fixture tree                                    | The fixture store for phases 1–3                                                                      |
| #51   | plan init: create a tracker, or register the current repo with one       | `plan init-store <path>`: `store.yml`, `active/`, `archive/`, and the `issueStore` config key         |
| #37   | plan.py: duration, lane heads, next/ready/why, schedule                  | `next` and `ready` in plan order ([§4.4](#milestones)); duration later                                |
| #36   | Design 0001: next/ready without lane heads; repo scope                   | Its repo-scope rule answers open question 4                                                           |
| #114  | Show Next up in plan order, by milestone and with blockers first         | Plan order over the store: milestone `ordinal`, blockers first, issue `ordinal` ([§4.4](#milestones)) |
| #111  | Keep each issue's Done ticks in Redmine and render them into the PR body | Simpler with files: the ticks live in the issue's `### Done`, and `pr` renders them into the PR body  |
| #21   | Reality checks in the Redmine plugin                                     | Checker rules ([§4.6](#checker))                                                                      |
| #173  | Reference Redmine issues as gh-plan#n                                    | Settled by `PREFIX-number` keys; close once the store's keys are in use                               |
| #61   | Post-merge hook reports closes                                           | Open question 5: `plan done` closes a file-store issue after a confirmed merge                        |

**Unchanged by the store, still wanted (port to this repo's `plan`):** [#167](http://127.0.0.1:3010/issues/167) worktrees view, [#141](http://127.0.0.1:3010/issues/141) `plan switch`, [#140](http://127.0.0.1:3010/issues/140) and [#179](http://127.0.0.1:3010/issues/179) session state, [#178](http://127.0.0.1:3010/issues/178) `pending`, [#177](http://127.0.0.1:3010/issues/177) setup on every Start and Open, [#103](http://127.0.0.1:3010/issues/103) worktree folder names, [#187](http://127.0.0.1:3010/issues/187) and [#190](http://127.0.0.1:3010/issues/190) the report server, [#145](http://127.0.0.1:3010/issues/145) docs for the terminal client [#138](http://127.0.0.1:3010/issues/138) kebab-case command names. The review work in M1 ([#107](http://127.0.0.1:3010/issues/107), [#108](http://127.0.0.1:3010/issues/108), [#127](http://127.0.0.1:3010/issues/127)–[#129](http://127.0.0.1:3010/issues/129), [#136](http://127.0.0.1:3010/issues/136), [#137](http://127.0.0.1:3010/issues/137), [#146](http://127.0.0.1:3010/issues/146)–[#166](http://127.0.0.1:3010/issues/166) [#169](http://127.0.0.1:3010/issues/169), [#174](http://127.0.0.1:3010/issues/174), [#176](http://127.0.0.1:3010/issues/176), [#180](http://127.0.0.1:3010/issues/180), [#181](http://127.0.0.1:3010/issues/181)) sits behind the `ReviewStore` port and doesn't depend on this plan.

**Later, reading the store:** [#24](http://127.0.0.1:3010/issues/24) estimate against tracked time, [#27](http://127.0.0.1:3010/issues/27) Timewarrior adapter, [#57](http://127.0.0.1:3010/issues/57) velocity, [#43](http://127.0.0.1:3010/issues/43)
planned-vs-actual, [#33](http://127.0.0.1:3010/issues/33) focus, [#39](http://127.0.0.1:3010/issues/39) terminal board, [#70](http://127.0.0.1:3010/issues/70) freeze (baselines as committed values, as ADR 0001 had them).

**Superseded or Redmine-only, to close or leave:** [#41](http://127.0.0.1:3010/issues/41) (the orphan branch, replaced by the store's own repo), [#55](http://127.0.0.1:3010/issues/55) drag
to reorder, [#23](http://127.0.0.1:3010/issues/23) and [#49](http://127.0.0.1:3010/issues/49) (plan-board extension), [#35](http://127.0.0.1:3010/issues/35) (VS Code tree views), [#92](http://127.0.0.1:3010/issues/92), [#135](http://127.0.0.1:3010/issues/135) and [#143](http://127.0.0.1:3010/issues/143) (VS Code frames, already frozen by ADR 0014), [#194](http://127.0.0.1:3010/issues/194), [#191](http://127.0.0.1:3010/issues/191), [#133](http://127.0.0.1:3010/issues/133), [#132](http://127.0.0.1:3010/issues/132), [#119](http://127.0.0.1:3010/issues/119), [#101](http://127.0.0.1:3010/issues/101) (Redmine operations), [#29](http://127.0.0.1:3010/issues/29) (gh-plan's requirements doc).
