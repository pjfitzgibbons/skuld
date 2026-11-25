# ADR 0006: The ticket store is skuld's tracker, read through a local server

## Contents

- [1. Status](#status)
- [2. Context](#context)
- [3. Decision](#decision)
  - [3.1 The tracker](#tracker)
  - [3.2 ADR 0001's store rules](#adr-0001)
  - [3.3 The store's layout](#layout)
  - [3.4 Writes and sync](#writes)
  - [3.5 The local server](#server)
  - [3.6 Links](#links)
  - [3.7 DuckDB for analysis](#duckdb)
- [4. Alternatives considered](#alternatives)
- [5. Consequences](#consequences)
  - [5.1 Build order](#build-order)
  - [5.2 The ticket sweep](#sweep)
  - [5.3 Non-goals](#non-goals)

<a id="status"></a>

## 1. Status

Accepted. Replaces [ADR 0005](0005-redmine-is-the-tracker.md) as skuld's tracker decision. ADR 0005
is tabled: it stays unchanged so it can be taken up again ([§4](#alternatives)). This ADR adopts the
store rules of [ADR 0001](0001-file-ticket-store.md) as amended here
([§3.2](#adr-0001)).

<a id="context"></a>

## 2. Context

[ADR 0005](0005-redmine-is-the-tracker.md) chose Redmine as skuld's tracker and kept the ticket
store as the interim. Its own alternatives section says skuld's three plugins "are what Redmine
needs for skuld's flow" (`docs/adr/0005-redmine-is-the-tracker.md:679-682`, §4), and that path
needs a Ruby core, an import, and a server to run, back up and upgrade
(`docs/adr/0005-redmine-is-the-tracker.md:86-113`, §2.2; §5.1).

What skuld's owner needs from a tracker is milestones, several repos per project, PR linking,
estimates, and progress over months, for small and medium tickets. The store already holds
tickets. What it lacks is a view: a dashboard of work in flight, milestone progress and review
rounds. This ADR decides that the store stays the tracker and gains that view.

<a id="decision"></a>

## 3. Decision

<a id="tracker"></a>

### 3.1 The tracker

The ticket store is skuld's tracker for good. It is a git repo (remote `pjfitzgibbons/issues`),
cloned on each Mac, of plain Markdown files with YAML front matter. There is no database.

Jira stays the work tracker on the work Mac, through skuld's existing Jira adapter. Work data never
enters the home store (`docs/adr/0001-file-ticket-store.md:29`, §3); the server on each Mac reads
only that Mac's own store ([§3.5](#server)).

<a id="adr-0001"></a>

### 3.2 ADR 0001's store rules

ADR 0005 is tabled and supersedes nothing, so ADR 0001's store rules stand. This ADR adopts
ADR 0001's decisions (`docs/adr/0001-file-ticket-store.md:26-36`, §3) as its own, amended by
[§3.3](#layout) to [§3.6](#links): the layout gains `points:`, `queries/` and `INDEX.md`; `store.yml`
names a key prefix and a list of repos; and every ticket has a web page ([§3.5](#server)). The
repo is no longer stdlib-only: the server depends on Flask, markdown-it-py, mdit-py-plugins and DuckDB
([§3.5](#server), [§3.7](#duckdb)). Every other rule of ADR 0001 holds as written: `store.yml` lives
in skuld; the front matter is a restricted YAML subset; numbers never repeat;
one issue's work stays in one repo; Done closes a ticket once its checks pass and never changes a Jira issue's status; a round's review files live in the
store.

ADR 0001's Status reads "Accepted, amended by ADR 0006" and names these amendments.

<a id="layout"></a>

### 3.3 The store's layout

- `SK-*.md`: a ticket. The front matter adds `points:`, a Fibonacci number (1 2 3 5 8 13)
  (`docs/plan-file-issue-store.md:297`), and `host:`, the name of the Mac holding the ticket's
  worktree, which `plan start` writes ([§3.6](#links)).
- `milestones/*.md`: a milestone, with `due`.
- `store.yml`: names each project as a key prefix and a list of repos (SK-0021, SK-0022).
- `reviews/<KEY>/round-N.jsonl`: one finding per line, one file per round, written once, plus the
  round's report files (SK-0014).
- `queries/*.sql`: saved DuckDB queries ([§3.7](#duckdb)).
- `INDEX.md`: a generated copy of the ticket list, for reading on GitHub. `issue-watch` regenerates
  it with skuld's `store-index` and stages it in the commit it makes ([§3.4](#writes)). A pre-commit
  hook runs `store-index --check` and refuses a commit whose `INDEX.md` is stale, so a commit
  made by hand keeps it current too.

<a id="writes"></a>

### 3.4 Writes and sync

The `plan` CLI, and `bin/issue-create` and `bin/issue-update`, are the only writers. `plan list --json` prints the tickets
for readers other than the server. A checker rejects dependency cycles (SK-0019).

Sync between Macs: `issue-watch` commits and pushes (SK-0026), with `INDEX.md` regenerated in the
same commit. After a `pull --rebase` it regenerates `INDEX.md` and commits it, and a rebase conflict
confined to `INDEX.md` is resolved by regenerating it. Pulling is plain git.

<a id="server"></a>

### 3.5 The local server

Each Mac runs the existing Flask server (`lib/server.py`, a LaunchAgent), bound to `127.0.0.1`,
with read-only routes and Jinja templates. Every page is rendered on request. The server reads only
its own Mac's store, the one the machine config's `issueStore` names. The work Mac's dashboard shows
the work store, and Jira work stays in Jira, as ADR 0001 decides
(`docs/adr/0001-file-ticket-store.md:29`, §3). The pages:

- `/`, the dashboard, in independently refreshable sections: work in flight (worktree, PR state,
  Claude state from the pulses file, latest round), and milestones (points progress, open tickets
  in work order), and trends (points burned per week, findings per round), from the saved
  `queries/*.sql` run through DuckDB ([§3.7](#duckdb)).
- The ticket page (SK-0034): every ticket has a read-only page at the ticket route, rendered from its
  file.
- The round report and review pages (SK-0033).

**Live updates** are server-sent events from an `/events` route. A watcher on the store clone and
the pulses file (`lib/config.py:72-73`, `:99`) drives it, and the page re-fetches only the section that
changed.

**PR state** comes from `gh pr view`, cached, and polled only for tickets in flight.

**Ticket bodies** render through markdown-it-py's `gfm-like` preset with HTML off, plus the
mdit-py-plugins `tasklists_plugin`. The preset enables tables, strikethrough and linkify
(<https://markdown-it-py.readthedocs.io/en/latest/using.html#the-parser>); linkify needs
`linkify-it-py`, installed as `markdown-it-py[linkify]`
(<https://markdown-it-py.readthedocs.io/en/latest/using.html#linkify>). The plugin renders `[ ]`
and `[x]` as disabled checkboxes (<https://mdit-py-plugins.readthedocs.io/en/latest/>, Task lists).
With HTML off, raw HTML in a body is escaped; running the parser confirmed this, and the docs do not
state it.

<a id="links"></a>

### 3.6 Links

Each dashboard row carries a link to its server page and `vscode://` links into the plan-window
extension. The link builders exist in `lib/term.py` (`browseLink` :421, `openLink` :429,
`reviewLink` :460); a `start` handler is to be added.

`plan start` writes `host: <mac name>` into the ticket's front matter, and the change syncs through
the store's git like any edit ([§3.4](#writes)). The dashboard and the extension read `host:` to say
a ticket's worktree is on the other Mac. The Mac's name comes from the machine config; the machine
config has no field for it yet; SK-0107 adds it.

<a id="duckdb"></a>

### 3.7 DuckDB for analysis

DuckDB is the analysis tool, from the command line and from the server. It runs over
`reviews/**/*.jsonl` and `plan list --json` output with the saved `queries/*.sql`. The server runs
the same `.sql` files for the dashboard's trends section ([§3.5](#server)) and takes a `duckdb`
dependency. DuckDB stores nothing; the files are the source of truth.

- **JSONL, not CSV, for findings.** Findings carry prose with commas, quotes and line breaks. One
  record per line keeps diffs line-local.
- **Size.** Git stores revisions as deltas in packs
  (<https://git-scm.com/docs/git-pack-objects/2.54.0>), and GitHub blocks files over 100 MiB
  (<https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github>).
  One file per round keeps each file far below that.
- **Reading.** DuckDB reads globs, with `filename` and `union_by_name`
  (<https://duckdb.org/docs/current/data/multiple_files/overview>,
  <https://duckdb.org/docs/current/data/multiple_files/combining_schemas>).

<a id="alternatives"></a>

## 4. Alternatives considered

- **Redmine (ADR 0005), tabled.** ADR 0005 says the three plugins and the core "are what Redmine
  needs for skuld's flow" (`docs/adr/0005-redmine-is-the-tracker.md:679-682`, §4). Without them,
  Redmine core covers the owner's needs. With them, the path requires the plugins, a Ruby core, an
  import and a server to run, back up and upgrade (0005 [§2.2](0005-redmine-is-the-tracker.md#leaving-redmine),
  [§5.1](0005-redmine-is-the-tracker.md#operations)). The store meets the owner's needs with small
  and medium tickets. ADR 0005 is kept unchanged so it can be taken up again.
- **Jira Cloud Free.** It is free for 10 users. The GitHub for Jira app links branches, commits and
  PRs by key with "unlimited organizations and repositories"
  (<https://marketplace.atlassian.com/apps/1219592/github-for-jira>). Automation is 150 steps a
  month on Free
  (<https://support.atlassian.com/cloud-automation/docs/how-is-my-usage-calculated/>). Releases,
  story points and the app's Free-plan support are not confirmed by Atlassian 🔍, and a Free user
  reported no release burndown
  (<https://community.atlassian.com/t5/Jira-questions/How-to-view-the-release-burn-chart-on-Free-plan-project/qaq-p/2331938>).
  It would mean migrating the store. Jira stays the work tracker only.
- **GitHub Issues and Projects.** Milestones are per repo. An iteration field with summed number
  fields stands in
  (<https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/customizing-the-table-layout#showing-the-sum-of-a-number-field>).
  The auto-add workflow is 1 per project on Free and watches one repo
  (<https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/adding-items-automatically#about-automatically-adding-items>).
  Burn-up on Free is unconfirmed 🔍. Skuld has no GitHub Issues adapter. Secondary rate limits (80
  content-generating requests a minute, 500 an hour:
  <https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api#about-secondary-rate-limits>)
  are not the reason: gh-plan's record shows them only as a design concern for bulk and sync writes
  (`~/programming/gh-plan/docs/adr/0003-disjoint-field-ownership.md:20`, `:49`;
  `0008-file-native-items.md:27`).
- **File-native CLI trackers.** Backlog.md deletes front matter it does not know on the next edit,
  so it cannot hold estimates (`~/programming/gh-plan/docs/alternatives-survey.md:184-196`; tested
  on v1.53.0). beads stores data in Dolt and needs a GitHub token. ticket has no estimates or
  milestones (`~/programming/gh-plan/docs/adr/0008-file-native-items.md:166-172`). git-bug has no
  milestones, priority, dependencies or fields (`alternatives-survey.md:150`).
- **A central server (the owner's Hetzner host).** Worktrees and Claude sessions exist only on the
  Mac running them, so a central dashboard needs each Mac to send its state, a write route,
  authentication and TLS. Deferred, not rejected: a later option.
- **A database for review findings (SQLite or a DuckDB file).** It is a second copy to keep in step
  with the files. DuckDB reading the files in place needs none ([§3.7](#duckdb)).

<a id="consequences"></a>

## 5. Consequences

<a id="build-order"></a>

### 5.1 Build order

1. `points:` and the cycle checker (SK-0019).
2. The dashboard on `/`, with the two kinds of link, the SSE live sections and the trends section
   (the `duckdb` dependency, with `queries/velocity.sql` and `queries/findings.sql` from steps 3
   and 4).
3. `plan list --json` and `queries/velocity.sql`.
4. Findings as JSONL in `reviews/<KEY>/` (SK-0014) and `queries/findings.sql`.
5. `issue-watch` (SK-0026).
6. `plan milestone` (SK-0024) and `due`.
7. Several repos per project (SK-0021, SK-0022).
8. The extension's `start` handler and the other-Mac message.

<a id="sweep"></a>

### 5.2 The ticket sweep

A ticket sweep follows this ADR. Tickets SK-0070 to SK-0093 and the store's open tickets are
reviewed against it in a separate pass. The docs that name Redmine as the tracker change in the same
sweep.

<a id="non-goals"></a>

### 5.3 Non-goals

A board and a Gantt chart stay non-goals (`docs/plan-file-issue-store.md:89-92`), and so does a
journal plugin.
