# ADR 0005: Redmine is skuld's tracker, with the ticket store as the interim

## Contents

- [1. Status](#status)
- [2. Context](#context)
  - [2.1 gh-plan ADRs 0011 and 0014](#gh-plan-0011)
  - [2.2 Why ADR 0001 left Redmine](#leaving-redmine)
  - [2.3 The prior-art survey](#survey)
  - [2.4 The trials that ended](#trials)
- [3. Decision](#decision)
  - [3.1 The tracker](#adopted)
  - [3.2 The core and its adapters](#adapter)
  - [3.3 The API keys, the GitHub token and the bind address](#credentials)
  - [3.4 Keys](#keys)
  - [3.5 Closing an issue and ticking its Done boxes](#closing)
  - [3.6 Review rounds](#review-rounds)
  - [3.7 The runtime and the ship step](#runtime)
  - [3.8 The interim](#interim)
  - [3.9 The end of the interim](#interim-end)
  - [3.10 The tickets' path into Redmine](#migration)
  - [3.11 ADR 0001 and the store's interim rules](#adr-0001)
  - [3.12 The Redmine setup moves into skuld](#redmine-folder)
  - [3.13 Where skuld departs from gh-plan ADRs 0011 and 0014](#departures)
  - [3.14 Open](#open)
- [4. Alternatives considered](#alternatives)
- [5. Consequences](#consequences)
  - [5.1 Data, failure and operations](#operations)
  - [5.2 Follow-up tickets](#follow-ups)

<a id="status"></a>

## 1. Status

Tabled. [ADR 0006](0006-the-ticket-store-is-skulds-tracker.md) replaces it as skuld's tracker
decision for now, and this ADR is kept unchanged so it can be taken up again. While it is tabled, it
supersedes nothing: [ADR 0001](0001-file-ticket-store.md)'s store rules stand under ADR 0006
([§3.2](0006-the-ticket-store-is-skulds-tracker.md#adr-0001)). If taken up again, it supersedes ADR 0001
in full. That includes the consequence "Each Redmine project is imported into the store under a key
prefix, and Redmine is retired" ([ADR 0001 §5](0001-file-ticket-store.md#consequences)) and
the alternative "Stay on Redmine" ([ADR 0001 §4](0001-file-ticket-store.md#alternatives)). The
store's rules for the interim are this ADR's own ([§3.11](#adr-0001)).

<a id="context"></a>

## 2. Context

Skuld replaces gh-plan. gh-plan's tracker is its own Redmine, decided in gh-plan's ADR 0011, and
gh-plan's ADR 0014 makes its tracker a port and its keys `PREFIX-number`. This ADR takes those
decisions on as skuld's own, each cited at its source in gh-plan, except where they conflict with
what skuld has already decided ([§3.13](#departures)).

<a id="gh-plan-0011"></a>

### 2.1 gh-plan ADRs 0011 and 0014

- **ADR 0011** (`~/programming/gh-plan/docs/adr/0011-redmine-is-the-tracker.md`) makes Redmine 7
  gh-plan's tracker: "Redmine 7 is the tracker. It runs outside the repos it plans, and nothing
  tracker-related is committed in them" (`0011:49-50`, §3 Decision). Its argument against a
  tracker kept as files is in its §2 Context (`:24-44`) and §4 Alternatives (`:76-91`). Peter
  treats it as Accepted; its file reads "Proposed" (`0011:16`).
- **ADR 0014** (`~/programming/gh-plan/docs/adr/0014-terminal-client.md`) makes `plan` the runtime,
  the tracker a port with Redmine and Jira as equal adapters, and every key `PREFIX-number`
  (`0014:63-109`, §3 Decision). Its file reads "Proposed" (`0014:14`).

In this ADR, `0011:` and `0014:` are line numbers in those two files, and `survey.md:` is a line number in
`docs/research/prior-art-survey.md`.

<a id="leaving-redmine"></a>

### 2.2 Why ADR 0001 left Redmine

ADR 0001 gave three reasons: gh-plan's Redmine is "a server that only the home Mac can reach and
that has to be run, backed up and upgraded. Its data isn't text an agent can grep"
(`docs/adr/0001-file-ticket-store.md:13`, §2 Context). Its alternative "Stay on Redmine" repeats
them: "a server to run and back up, it is reachable only from the home Mac, and its data isn't
plain text" (`:42`, §4).

This decision accepts the first two. Redmine stays a server on the home Mac, bound to
`127.0.0.1` ([§3.3](#credentials)), and someone has to run, back it up and upgrade it
([§5.1](#operations)). The instance's setup already does much of that work:

- **Backup.** `redmine/backup.py` backs the database up nightly and checks its integrity
  (`~/programming/gh-plan/redmine/README.md:51-63`, _Backup and export_).
- **Upgrades.** The upgrade has written steps and a rollback (`README.md:343-388`, _Upgrading_).
- **Reach.** The work Mac does not need Redmine. Its issues are Jira's
  (`docs/adr/0001-file-ticket-store.md:29`, §3), and its Jira-centric arrangement is decided
  outside this ADR (SK-0089).

The third reason has three answers, though the text is no longer a file in git:

- **The REST API.** An agent reads issues through it (`README.md:111`, the read-only
  `redmine-get`).
- **The nightly export.** Each night every issue is written as JSON, journal included
  (`README.md:60`).
- **The `review_findings` plugin.** It keeps review findings in one table that any `SELECT` can
  answer (`README.md:221-227`, _Querying the findings_).

<a id="survey"></a>

### 2.3 The prior-art survey

The survey (`docs/research/prior-art-survey.md`, in skuld) ranks projects to build on by
their bones. Its URLs are in the round-2 reports it names (`survey.md:3-11`). Redmine's row is
"strong | strong | adequate | adequate | adequate" for extension, data model, storage and ops, API
and contribution (`survey.md:112`, §4). Redmine ranks second:

> For: the most mature in-process plugin API, typed relations, full change history, any number of
> repos per project, no paid tier and no CLA. … Against: there is no PR model in core, API keys
> are per user with no scopes, and core patches go through redmine.org, not GitHub PRs.
> (`survey.md:131-136`)

Multica ranks first, and its license is why it is not the choice: "Accept source-available terms
or not. If yes, Multica is the strongest base. If no, Redmine is"
(`survey.md:174-175`, §6 item 1; the terms are at `survey.md:158-170`, §5).

<a id="trials"></a>

### 2.4 The trials that ended

- **YouTrack Cloud with IntelliJ** (SK-0066, won't fix). The trial found five IntelliJ gaps
  (`issues/SK-0066-youtrack-tracker-design.md:134-144`). The decision: "skuld goes back to VS
  Code, `plan` and the Issues view, with SK in the ticket store on disk"
  (`SK-0066-youtrack-tracker-design.md:163-165`).
- **Emdash** (no ticket). Peter's verdict is that it is polished, but its agent integration is
  incomplete, it has no AI PR review, and its diff view has no key navigation
  (`issues/SK-0070-adr-redmine-is-skuld-tracker.md:64-65`). That is Peter's assessment from use.
  It has not been checked against Emdash's documentation 🔍. The survey grades Emdash's data model
  "weak (not a tracker)" (`survey.md:119`), and notes that its tracker adapters are read-only and
  compiled into the app (`survey.md:179-180`).

<a id="decision"></a>

## 3. Decision

Skuld's issue tracker is Redmine 7, the Redmine instance at `127.0.0.1:3010`
(`~/programming/gh-plan/redmine/compose.yml:16`). Its setup moves into skuld
([§3.12](#redmine-folder)).

On the home Mac skuld's core is Redmine-centric. The flow core and its interface are part of
skuld's own Redmine plugin, in Ruby ([§3.7](#runtime)). The tracker, the review store and
the GitHub PR integration are the core's adapters ([§3.2](#adapter)). The ticket store stays
skuld's tracker until that core works inside Redmine ([§3.8](#interim), [§3.9](#interim-end)).

Skuld's architecture depends on these Redmine plugins:

- **Skuld's own plugin** holds the core, its interface and the checklist for Done boxes
  ([§3.5](#closing)).
- **`review_findings`** is `ReviewStore`'s adapter, a ledger of each issue's review rounds and
  findings (`~/programming/gh-plan/redmine/plugins/review_findings/README.md:3-6`;
  [§3.6](#review-rounds)).
- **A key plugin** reads `SK-<n>` in text and in commits ([§3.4](#keys)).

The work computer is Jira-centric. Its arrangement is decided outside this ADR (SK-0089).

<a id="adopted"></a>

### 3.1 The tracker

- **One Redmine, outside the repos it plans.** Nothing tracker-related is committed in the repos it
  plans (`0011:49-50`). Architecture §10 rule 1 already says this of skuld's trackers.
- **A Redmine project is where a key's prefix lives.** It carries the prefix and the GitHub
  repositories its work happens in, and it may have several. A parent project only groups
  projects: it carries no prefix and no repositories. Redmine has no prefix field, so skuld adds
  one, a project custom field, beside its key plugin ([§3.4](#keys)). Redmine allows "any number of
  repos per project" (`survey.md:131-136`, quoted in [§2.3](#survey)), and skuld's own suite
  already registers a fixture repo as a second repository of the skuld project
  ([§3.9](#interim-end)), so one project per repo would split one prefix across projects for no
  gain. Milestones are versions of the parent
  project, shared with its tree (`0011:52-54`).
- **Dependencies are the `blocks` relation.** Redmine refuses a relation that would close a cycle
  (`0011:55-57`).
- **Custom fields fill what Redmine lacks:** `Points`, `Lane` and the baseline dates
  (`0011:58-61`). The instance also has `Old ID` and `Ordinal` (`redmine/README.md:154-157`).
- **Each repo is registered as a Redmine repository of its project,** with `refs` and `fixes` as
  commit keywords (`0011:62-63`). The keywords take skuld's keys ([§3.4](#keys)).
- **Redmine runs in Docker on the Mac, on SQLite in a named volume backed up nightly**
  (`0011:65`).
- **What Redmine does not do is a plugin, in Ruby on Redmine's plugin API.** Scripts outside
  Redmine use its REST API (`0011:66-67`).
- **An issue above 5 points is split before it is scheduled.** A reality check lists any that are
  not, and nothing refuses the save (`0011:72-74`).
- **Tracking data lives in Redmine's database, not in git.** The journal is the record of change,
  and the nightly export is how someone with no account reads it (`0011:103-109`).
- **Hand-editable files are lost.** The web UI and the REST API replace them (`0011:110`).
- **Redmine is one more system to run, back up and upgrade, and it is down while the Mac is off**
  (`0011:111-112`).

<a id="adapter"></a>

### 3.2 The core and its adapters

On the home Mac the tracker, the review store and the GitHub PR integration are adapters of the
core's ports ([architecture §6.1](../architecture.md#front-ends)). Each is as thin and specific as
the interface it meets needs: Redmine's plugin API for the first two, GitHub's PRs for the third.

| Port                                      | Adapter on the home Mac                    |
| ----------------------------------------- | ------------------------------------------ |
| `IssueStore` (`docs/architecture.md:169`) | `Redmine`                                  |
| `ReviewStore` (`:170`)                    | `review_findings` ([§3.6](#review-rounds)) |
| `PullRequests` (`:171`)                   | GitHub                                     |

`IssueStore` stays, with its operations: list Next up, read an issue, create, set fields,
transition, append a log line, close note. `FileStore` is deprecated in favour of `Redmine`: it
serves the store's tickets until the interim ends, and nothing new is built on it.

`storeFor(cfg, key)` keeps picking the adapter by the key's prefix
(`docs/architecture.md:174-175`). On the home Mac an `SK` key goes to `Redmine`. The work
computer's tracker, its adapters and its routing are decided outside this ADR (SK-0089).

The adapters are part of the Ruby core in skuld's Redmine plugin ([§3.7](#runtime)). No
`Redmine` adapter is written in Python. Policy names no tool: adapters meet the ports, and the
core wires them together (`0014:75-76`), as architecture §6.2 already requires
(`docs/architecture.md:193`).

An issue's type, and the branch prefix it gives, belong to the tracker's adapter, and every
adapter maps one to the other by the same mechanism. Each tracker names its types its own way, so
a map held anywhere else would branch on the tracker. Redmine's map is one to one: a tracker's
name, lowercased and made safe for a ref, is its prefix, so Bug gives `bug/`. Jira's types vary
per project and have no fixed branch convention, so its adapter keeps its `typeMap`
(`lib/start.py:16-18`, `lib/config.py:57-59`).

<a id="credentials"></a>

### 3.3 The API keys, the GitHub token and the bind address

**Skuld's automation acts as a skuld bot user.** It is a Redmine user of its own whose role reaches
only skuld's projects, so Redmine's journal tells skuld's automated changes apart from a person's.
Redmine's API key belongs to one user and has no scopes, so a call made with it can do whatever
that user can (survey `:135`; "using your API key which is a handy way to avoid putting a
password in a script", <https://www.redmine.org/projects/redmine/wiki/Rest_api>). The role is what
limits it.

**Calls from the Mac use Peter's own API key.** The VS Code extension, `plan` and `pr` call the
core with Peter's own Redmine API key. Each of those calls is something Peter did on the Mac, so
the journal names him, as it does for what he does in the core's interface inside Redmine.

**The keys live in the macOS Keychain** (`0011:69-70`), a credential on the machine kept out of
every repo (`0011:115-116`). Anything of skuld's that calls Redmine from outside Redmine reads its
key from there on each call. It is never cached, never put in an env var and never logged: the
same per-call rule architecture §3 sets for the Jira token, which lives in `~/.netrc`
(`docs/architecture.md:96-98`). The instance's backup job reads its key the same way, from the
Keychain item `redmine-gh-plan` (`~/programming/gh-plan/redmine/backup.py:52`, `:154-159`).

**Two more secrets are in the Keychain, and the interim suite reads them** ([§3.9](#interim-end)).
Peter's signed-in Redmine session cookie is the item `skuld-redmine-session`: the suite sends it as
`_redmine_session` to read an issue page as Peter (check 7). The fixture repository's WS key is the
item named by `repositoryKeyService` in `lib/interim_config.py`, `skuld-redmine-repository-key`:
check 8 sends it as the `key` of its `fetch_changesets` call. Both follow the rule above: read from
the Keychain on each use, never cached, never put in an env var, never logged. The session cookie
acts as Peter's whole account, with no role to limit it as the bot user's role limits the bot's key.

**A secret skuld presents to another service comes from the host's secret store, never from
Redmine's database.** The Keychain on the Mac and the host's secret manager when hosted are that
store. The GitHub token is one: the `PullRequests` adapter runs in skuld's Redmine plugin
([§3.2](#adapter)), and the plugin reads the token from the host's secret store, so a copy of the
database holds no GitHub token. Moving the token out of the plugin's settings is SK-0091
([§5.2](#follow-ups)).

**Redmine's own API keys are in its database, in plain text.** Redmine 7.0.2 stores each key as the
`value` of a row in the `tokens` table, and looks a key up with `find_by(:action => action, :value =>
key)` (`app/models/token.rb:124`,
<https://github.com/redmine/redmine/blob/7.0.2/app/models/token.rb#L124>), so every copy of the
database holds every user's key. Until Redmine stores API keys hashed, every Redmine backup is a
secret: the host keeps it encrypted, with access limited to its owner, by whatever means that host
provides. Redmine API keys are rotated on a schedule (SK-0092) and right after any suspected leak
(OWASP, _Secrets Management Cheat Sheet_, §2.9 encrypt backups and §2.7.2 rotation,
<https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html>). Redmine's
patch "Fine-grained Personal Access Tokens" stores only the SHA-256 digest of a token and converts
legacy plain-text keys to hashed tokens without invalidating them
(<https://www.redmine.org/issues/44430>; New, a candidate for the next major release). When Redmine
ships hashed tokens, skuld moves to them (SK-0093).

**The core acts as a person, or as the bot user.** What a person does in skuld's interface inside
Redmine, the core does as that person's signed-in Redmine user. A call from the Mac acts as the
user whose key it carries. Either way, the journal names the person.
Skuld's own automation, such as the import ([§3.10](#migration)), acts as the skuld bot user. A
`fixes <KEY>` close is Redmine's own ([§3.5](#closing)), and Redmine journals it as the commit's
author.

**The instance is bound to `127.0.0.1`.** `compose.yml` publishes the port as
`"127.0.0.1:${REDMINE_PORT:-3010}:3000"` (`compose.yml:16`): loopback only, port 3010 unless
`REDMINE_PORT` sets another. The README says the same: "bound to localhost only"
(`redmine/README.md:30`).

**Skuld is designed for hosting.** The link from the Mac to the core, from the VS Code extension
and from the CLIs, is a network boundary with authentication, wherever Redmine runs. Redmine can
move to a hosted service. With the link built as a network boundary from the start, that move
changes the binding and where the instance runs, and leaves skuld's architecture as it is. The
loopback binding is how the instance runs on the home Mac, and nothing in skuld depends on it. A
hosted move also sends the database, the attachments and the credentials to the host, so it needs a
decision of its own ([§3.14](#open)).

<a id="keys"></a>

### 3.4 Keys

Every issue key is `PREFIX-number` (`0014:87-93`):

- **The number is Redmine's issue number.** It is unique across Redmine's projects, so a key's
  number finds the issue (`0014:89-90`).
- **Each project has a prefix,** in the project custom field skuld adds ([§3.1](#adopted)).
  Skuld's is `SK`. A key whose prefix doesn't match its issue's project is refused (`0014:90`).
- **A Redmine plugin reads keys.** It reads `SK-123` in text and in commits, and renders it as a
  link to the issue (`0014:90-91`, `:167-168`). It changes how Redmine behaves, so it is checked
  again on each upgrade (`0014:168`). It is not yet written (SK-0081).
- **Commit keywords take the key:** `refs SK-123`, `fixes SK-123` (`0014:167`).
- **Branches are `<type>/<KEY>-<slug>`,** and worktree folders and `.tmp/` folders are named for the
  key (`0014:91-92`), as skuld's already are.

A store ticket's key changes when it is imported: it gets a new Redmine number, and its old key
stays as a reference ([§3.10](#migration)).

<a id="closing"></a>

### 3.5 Closing an issue and ticking its Done boxes

A `fixes <KEY>` keyword in the merge commit closes the issue when Redmine fetches the commit
(`0011:62-64`). That is the one place automation moves an issue (`0014:80-82`). Done and the rest of
skuld never set a Redmine issue's status, so architecture §10 rule 2 holds for Redmine issues:
Start's one move sets an issue in progress, and nothing else in skuld changes its status
(`docs/architecture.md:665-667`).

**A Redmine issue's Done boxes are ticked in Redmine,** and each tick is journaled, so the journal
records who verified what. Skuld builds the checklist in its own Redmine plugin: a checklist model
with a CRUD API, where each tick and untick is a journal entry naming the user (SK-0085).
RedmineUP Checklists' design is a reference for it, and nothing of RedmineUP's is installed. Two
checklist plugins were weighed and turned down because their ticks miss the journal:

- UX Toolkit's checklists "live in a table of their own with no journal entry"
  (`~/programming/gh-plan/docs/adr/0012-issue-ux-plugin.md:83-84`, §4).
- RedmineUP Checklists 4.1.0: "◐ journal is paid", and it is ruled out for "their own data model"
  (`~/programming/gh-plan/docs/design/0004-issue-ux.md:92`).

<a id="review-rounds"></a>

### 3.6 Review rounds

Review findings have a port of their own, separate from the tracker's (`0014:83-84`). In skuld that
port is `ReviewStore` (`docs/architecture.md:170`). After the interim its adapter is Redmine's
`review_findings` plugin: a round, its findings and its files (the commit message, the PR body and
the replies to review threads) are recorded in Redmine, beside the issue they belong to. Ship
uploads a round as a draft, and `pr` marks it sent ([§3.7](#runtime)). The rounds in the store's
`reviews/<KEY>/` are moved there ([§3.10](#migration)).

<a id="runtime"></a>

### 3.7 The runtime and the ship step

- **Skuld's core is the runtime.** The flow runs from the core, with its checks and refusals, and a
  client shows the results (`0014:63-65`). Skuld's client is VS Code
  ([§3.13](#departures)), and the bridge between VS Code and the core is written in TypeScript
  (SK-0086). The bridge is part of the `plan-window` extension, and TypeScript settles the choice
  between TypeScript and JavaScript for it.
- **No Mac daemon: the Mac calls the core.** The core never calls the Mac. A core hosted away from
  the Mac has no route into it, and none is needed: every trigger already comes from VS Code, from
  a `vscode://` link into the extension, or from a CLI. So git and worktree operations, and the
  local file writes (the issues file and the workspace files), are a module of the VS Code
  extension.
- **`plan` and `pr` are thin CLIs.** Each calls the core and holds no flow logic of its own, so a
  terminal drives the same flow, with the same checks and refusals, as VS Code and the core's
  interface. The one thing `pr` does beyond calling the core is the round's commit and push.
- **Pages are the plugin's.** Issue pages and review reports are served by skuld's Redmine plugin,
  rendered from the issue's data. There is no local web server: it would be a second process on the
  Mac to keep running, and the data it would render is already in Redmine.
- **Claude Code is a helper agent.** It writes code and reviews in VS Code's terminals, started by
  the extension, since the core never calls the Mac. Every command that only reads works with no
  Claude session running (`0014:66-68`).
- **Start, Open and Done on Redmine's pages are `vscode://` links.** The core's interface shows
  them as links into the `plan-window` extension. The extension's handlers are thin wrappers over
  its git and worktree module, which does the work on the Mac and calls the core. Every
  trigger for work on the Mac comes from the Mac, so the core never needs a route into it.
- **`plan` opens the same links.** `plan`'s Start, Open and Done open the `vscode://` link that the
  issue page's button uses, so there is one path into the git and worktree module. The cost is that
  VS Code must be running for them.
- **The core comes first.** For Start and Done the core is called first, and it can refuse. The
  module does its work on the Mac only after the core accepts, so a refusal leaves the Mac
  untouched.
- **Ship uploads the round, and a person runs `pr`.** Ship is triggered on the Mac and uploads the
  issue's round to the core, which records it in `review_findings` as a draft. The core can't read
  the worktree, since it never calls the Mac, so the Mac sends the round. The plugin renders the
  round's report page like its other pages, the plan window shows it, and it ends with "run
  `pr <KEY>`". Ship stages the round's changes, and it moves no HEAD and pushes nothing. Ship
  decides what belongs to the round, and `pr` is the thin final step that trusts it. There is no
  Go step and no manual staging step: the round's commit message, PR body and replies are
  produced automatically at the end of the author's implementation step and at the end of each
  review-resolve step, so the round is ready when Ship uploads it (SK-0087).
- **`pr` owns the Mac's commit and push.** A person runs `pr <KEY>` on the Mac, as the report
  page says. It commits only what Ship staged, with the round's commit message, and pushes, then
  calls the core, which opens or updates the PR, posts the replies through its adapters and marks
  the round sent. Commit and push stay in `pr` (`0014:103-104`), and they are the only git work on
  the Mac outside the extension's git and worktree module. The round is already in Redmine
  ([§3.6](#review-rounds)).
- **Session status is the core's.** The status engine is about the status of Claude's sessions,
  not review rounds. Claude Code's hooks report each session's status with one call to skuld's
  Redmine plugin, and the core keeps it. The Issues view and `plan agents` read it from the core,
  so the pulses file, `plan pulse` and a local engine go. A hook's call goes from the Mac to the
  core, so the core still never calls the Mac.
- **An issue's tint is the core's.** An issue is orange while one of its sessions waits on a
  person, and green while it has a draft round waiting for `pr`. One rule covers the round of the
  first authorship, its commit message and PR body, and every review-resolve round. The core
  knows the draft from `review_findings`, so no local report file is needed to tell it.
- **The core and its interface are part of skuld's Redmine plugin, in Ruby.** Redmine's plugin API
  is Ruby ([§3.1](#adopted)). Until the interim ends, the repo stays stdlib-only
  for skuld's Python, one of the store's interim rules ([§3.11](#adr-0001)).

<a id="interim"></a>

### 3.8 The interim

The interim lasts until skuld's Ruby core works inside Redmine: until every check in
[§3.9](#interim-end) answers yes. Meanwhile:

1. **New tickets go in the ticket store.** A new ticket for a project `store.yml` lists is filed in
   the store, as today. No issue for those projects is created in Redmine, with one exception: the
   end-to-end task, the one issue the store does not hold. It lives in Redmine's skuld project, so
   the suite of [§3.9](#interim-end) runs on skuld's own tracker.
2. **Who writes the store is unchanged.** Every writer, whether `plan`, a person or an agent,
   produces files the checker accepts, and the watcher commits them ([§3.11](#adr-0001)).
3. **Skuld writes no store ticket to Redmine** except through the import
   ([§3.10](#migration)).
4. **A ticket filed during the interim is an ordinary store ticket.** The import takes it with the
   rest, so no ticket is filed twice.
5. **Status is set where the ticket lives.** A store ticket's status stays in its file. Start sets
   it in progress, and a person closes it (`docs/architecture.md:485`, §7.3).
6. **Skuld's Python flow keeps working the store and Jira.** No Python `Redmine` adapter is built
   ([§3.2](#adapter)).

<a id="interim-end"></a>

### 3.9 The end of the interim

The interim ends when every check below passes. The ticket store is then retired: no ticket is
filed in it, and the store's interim rules ([§3.11](#adr-0001)) end.

The checks are an end-to-end suite (SK-0070), run on the home Mac against the instance. It fails
until the Ruby core works. It works on these:

- **The end-to-end task,** issue `SK-<n>` in Redmine's skuld project, with the subject "End-to-end
  task" and no `Old ID` ([§3.8](#interim) rule 1). It is created once and reused on every run, so
  the project holds one such issue rather than one per run, and its number stays fixed in the
  suite's config. Each run first resets it: the bot user sets its status back to the open status,
  deletes its checklist items, and removes any branch and worktree left for it.
- **A fixture GitHub repo,** registered as a second Redmine repository of the skuld project. Start,
  Ship, Done and the `fixes` commit run there, so no run pushes to or merges into skuld's own
  repo.
- **Peter's Redmine user,** signed in to Redmine, with his API key in the Keychain for the calls
  from the Mac ([§3.3](#credentials)), so the journal can tell a person's action from the bot
  user's.
- **REST reads use the bot user's key** from the Keychain ([§3.3](#credentials)). The endpoints and
  their `include` and `cf_<id>` parameters are Redmine's
  (<https://www.redmine.org/projects/redmine/wiki/Rest_Issues>,
  <https://www.redmine.org/projects/redmine/wiki/Rest_Users>).
- **The triggers.** Start and Done are the `vscode://` links on the end-to-end task's Redmine
  page, handled by the `plan-window` extension. Ship is triggered on the Mac and uploads the round,
  and `pr <KEY>` is run on the Mac as the report page says ([§3.7](#runtime)). SK-0086 builds the
  links and their handlers. Until they exist, the checks that need them fail.

Each check gives its action and its probe, and passes when every probe reads as expected.

1. **Start, from the issue's `vscode://` link** (SK-0086). The suite opens the Start link on
   `SK-<n>`'s Redmine page.
   - `GET /issues/<n>.json?include=journals`: `status` is the in-progress status, and the newest
     journal that changes the status has Peter's user as its `user`.
   - In the fixture repo, `git branch --list '*/SK-<n>-*'` lists one branch, and
     `git worktree list` shows its worktree.
2. **A mismatched prefix is refused** (SK-0086). The suite opens a Start link for `XX-<n>`: the
   end-to-end task's number with `XX`, a prefix that belongs to no project, so it doesn't match the
   issue's project (`0014:90`).
   - The refusal is shown.
   - `GET /issues/<n>.json?include=journals`: the status and the number of journals are as they were
     before.
   - In the fixture repo, `git branch --list '*/XX-<n>-*'` lists nothing, and `git worktree list`
     shows no worktree for `XX-<n>`.
3. **Ship uploads the round as a draft** (SK-0087, SK-0086). The worktree has a changed file, and
   the round as the end of the author's implementation step leaves it: its manifest, which lists
   the changed file, and its commit message and PR body in `.tmp/SK-<n>/`. The suite keeps a
   copy of the commit message, the PR body and HEAD, and reads the rounds on issue `<n>` through
   `review_findings` (`~/programming/gh-plan/redmine/README.md:221-227`). Peter's user ships
   `SK-<n>`.
   - `git rev-parse HEAD` is the HEAD the suite kept.
   - `git diff --cached --name-only` lists the changed file and nothing else: the index holds the
     files the manifest lists.
   - `review_findings` holds one round more than before, a draft, and its commit message and PR
     body are the suite's copies, each byte for byte.
   - The round's report page ends with "run `pr SK-<n>`".
4. **`pr` sends the round and marks it sent** (SK-0087, SK-0084). After check 3, the suite runs
   `pr SK-<n>` on the Mac, as the report page says.
   - `git rev-parse HEAD` has moved, and `git log -1 --format=%B` is the round's commit message.
   - `git diff-tree --no-commit-id --name-only -r HEAD` lists the files check 3 found staged, and
     no other.
   - `git ls-remote origin <the issue's branch>` names the new HEAD.
   - `gh pr list --head <the issue's branch> --state open --json body` lists one PR, and its body
     is the round's PR body.
   - The suite reads the rounds on issue `<n>` back through `review_findings`. The round check 3
     added is marked sent, and there is no other new round.
5. **Done never changes the status** (SK-0086). The fixture PR is merged on GitHub, and the suite
   opens the Done link on `SK-<n>`'s Redmine page.
   - `git worktree list` no longer shows the worktree.
   - `GET /issues/<n>.json`: `status` is what it was before Done.
6. **A ticked Done box is journaled** (SK-0085). The suite creates a checklist item on issue `<n>`
   through the checklist's CRUD API, and Peter's user ticks it.
   - `GET /issues/<n>.json?include=journals`: the newest journal records the tick, with Peter's
     user as its `user`.
   - The checklist API reads the item back as ticked.
7. **A key renders as a link** (SK-0081). The end-to-end task's description contains its own key,
   `SK-<n>`.
   - The issue page `/issues/<n>`, signed in, contains a link to `/issues/<n>` in the description.
8. **`fixes` closes the issue** (SK-0081). The suite pushes a commit with the message
   `fixes SK-<n>` to the fixture repo's default branch, then calls
   `GET /sys/fetch_changesets?id=<skuld's project>&key=<repository WS key>`
   (<https://www.redmine.org/projects/redmine/wiki/HowTo_setup_automatic_refresh_of_repositories_in_Redmine_on_commit>).
   - `GET /issues/<n>.json`: `status` is a closed status.
9. **Every store ticket is imported once** (SK-0079). For each ticket file in the store, open and
   closed:
   - `GET /issues.json?project_id=<skuld's project>&status_id=*&cf_<Old ID>=<old key>`:
     `total_count` is 1. `Old ID` is marked "used as a filter", which `cf_<id>` needs (Rest_Issues).
10. **The import keeps every field** (SK-0079). For each issue check 9 found,
    `GET /issues/<id>.json?include=journals,relations` and the checklist API, compared with its
    ticket file by the table in [§3.10](#migration):
    - `subject` is the title, `description` the body, `status` the mapped status, `fixed_version`
      the milestone, `Ordinal` the ordinal, and the `blocks` relations the `blocked_by` keys.
    - Each checklist item has a `## Done` line's text and tick.
    - The import's journals have the bot user as their `user`.
11. **Every round is imported** (SK-0079). For each round in the store's `reviews/<KEY>/`:
    - Read back as in check 4, it is on the imported issue, with its findings and files byte for
      byte.
12. **The bot user reaches skuld's projects only** (SK-0080).
    - `GET /users/current.json?include=memberships` with the bot user's key: the memberships'
      projects are skuld's projects, and no others.

Checks 9 to 11 start from the store's ticket files and look issues up by `Old ID`. The end-to-end
task has no ticket file and no `Old ID`, so they never reach it, and its rounds are not in
`reviews/<KEY>/`.

<a id="migration"></a>

### 3.10 The tickets' path into Redmine

The store's tickets are imported into Redmine once, keeping each body. This is how 0011 brought in
gh-plan's items, "with the old ID in a field" (`0011:120-121`), and the import uses the same fields:

| Store               | Redmine                                                                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| The key (`SK-0012`) | A new Redmine number, so a new key `SK-<n>`; the old key goes in the `Old ID` custom field, which is searchable (`redmine/README.md:155`) |
| `milestone`         | A version in the parent project, shared with its tree (`0011:52-54`)                                                                      |
| `ordinal`           | The `Ordinal` custom field, a whole number (`redmine/README.md:156`)                                                                      |
| `blocked_by`        | A `blocks` relation from each blocker (`0011:55-57`)                                                                                      |
| `status`, `type`    | Redmine's status and tracker; the mapping is SK-0079's                                                                                    |
| `## Done` boxes     | The issue's checklist, ticked as in the file ([§3.5](#closing))                                                                           |
| `## Log` lines      | Journal notes                                                                                                                             |
| `reviews/<KEY>/`    | Rounds and findings in `review_findings` ([§3.6](#review-rounds))                                                                         |

<a id="adr-0001"></a>

### 3.11 ADR 0001 and the store's interim rules

This ADR supersedes ADR 0001 in full. ADR 0001 retired Redmine (its consequence at
`docs/adr/0001-file-ticket-store.md:57` and its alternative at `:42`), and this ADR keeps it as
the tracker. The store stays skuld's tracker until the interim ends ([§3.8](#interim)), and these
are the rules it runs under meanwhile. They are this ADR's own rules. ADR 0001's decisions
(`docs/adr/0001-file-ticket-store.md:26-36`) are where they came from:

- the store's layout
- `FileStore`, deprecated in favour of `Redmine` ([§3.2](#adapter))
- `store.yml` in skuld
- the watcher
- the restricted YAML front matter, and the stdlib-only repo
- numbers that never repeat
- one issue's work in one repo
- Done that never changes a status
- review files in `reviews/<KEY>/`

When the interim ends, the store is retired and these rules end with it. Milestones are Redmine
versions ([§3.1](#adopted)), and review rounds are in `review_findings`
([§3.6](#review-rounds)).

<a id="redmine-folder"></a>

### 3.12 The Redmine setup moves into skuld

The instance's setup moves into skuld: `compose.yml`, the `Dockerfile`, the plugins, `backup.py`,
the README and the rest of the folder now at `~/programming/gh-plan/redmine/` (SK-0082). Skuld is
then the Redmine setup.

<a id="departures"></a>

### 3.13 Where skuld departs from gh-plan ADRs 0011 and 0014

- **The client.** 0014 builds for the terminal, "Ghostty with tmux on both Macs", and takes the
  extension off the code path (`0014:105-109`). Skuld keeps VS Code: "Each issue gets one VS Code
  window" ([ADR 0002 §3](0002-vscode-front-end.md#decision), `0002-vscode-front-end.md:35-36`),
  and "There is one VS Code window for all issues, the plan window"
  ([ADR 0004 §3](0004-one-plan-window.md#decision), `0004-one-plan-window.md:38-40`).
- **The port.** 0014's `Tracker` port covers "next up, read an issue, transition, link a PR and add
  a note" (`0014:77-78`). Skuld keeps `IssueStore` ([§3.2](#adapter)).
- **What picks the adapter.** In 0014 "the machine's chezmoi `machine` value picks the adapter"
  (`0014:80`). Skuld keeps `storeFor` and the key's prefix ([§3.2](#adapter)).
- **A helper's state.** 0014 maps `claude agents --json` to one `helper_state` and lets nothing past
  the adapter see the raw fields (`0014:94-96`). Skuld keeps Claude Code's hooks for a session's
  state (`docs/architecture.md:365-366`), and they report it to the core, which keeps each
  session's status ([§3.7](#runtime)).
- **The core's language and packaging.** 0014 makes the core "one Python package, `plan/`, run with
  `uv`" on Python 3.14 (`0014:69-74`), and serves reports from one Flask server (`0014:97-99`,
  `:151-152`). Skuld's core and interface are part of the Redmine plugin, in Ruby, instead, and
  the plugin serves the pages ([§3.7](#runtime)).
- **The writer.** 0011 makes gh-plan's VS Code extension the first writer to Redmine
  (`0011:69-71`). In skuld the writer is the core's `Redmine` adapter ([§3.2](#adapter)).
- **The setup's home.** 0011 makes gh-plan the Redmine setup: plugin, scripts and export
  (`0011:66-68`). In skuld it moves into skuld ([§3.12](#redmine-folder)).
- **The flow.** 0011 §6 (`0011:127-135`) describes gh-plan's buttons, and skuld does not adopt
  it. Its Done closes the issue (`0011:134-135`); skuld's Done never does ([§3.5](#closing)).
  Skuld's flow is [architecture §7](../architecture.md#operations) and
  [ADR 0004](0004-one-plan-window.md).
- **The Done boxes.** 0011 §7 keeps the record in the PR body and never ticks an issue's Done list
  (`0011:140-144`). Skuld ticks them in Redmine ([§3.5](#closing)).
- **Keys.** 0011 numbers an item `#<n>` (`0011:100`). Skuld takes 0014's `PREFIX-number` instead
  ([§3.4](#keys)), which 0014 says replaces 0011 on this point (`0014:18`). 0014 also reads a bare
  number as a reference for older branches and folders (`0014:92-93`); skuld has none.
- **Where findings are kept.** 0014 keeps findings out of Redmine: "This machine … uses a local
  SQLite database. … Redmine stores no findings" (`0014:85-86`). Once its flow works, the findings
  in Redmine migrate out, and Redmine's findings tables and plugin code retire (`0014:162-164`).
  Skuld keeps findings in Redmine, through `review_findings` ([§3.6](#review-rounds)). It does keep
  findings behind a port of their own, as 0014 does.

<a id="open"></a>

### 3.14 Open

No question is open on the boundary between the core and the Mac ([§3.7](#runtime)). Where
Redmine runs is not an architectural question: the link from the Mac to the core is a
network boundary with authentication wherever it runs ([§3.3](#credentials)). One question about
data is open:

- **Hosting Redmine.** OPEN, owner Peter. After the interim ticket data lives on the home Mac
  ([§5.1](#operations)). A hosted move sends the database, the attachments and the credentials to
  the host, and needs its own decision.

<a id="alternatives"></a>

## 4. Alternatives considered

0011 §4 weighs trackers outside the repo (`0011:76-91`), and 0014 §4 weighs the runtime, the
findings store and the key form (`0014:114-146`). These are the alternatives specific to skuld.

- **Keep the file store for good (ADR 0001 as it stands).** The store is a tracker of skuld's own,
  outside the repo. 0011 rejects writing one because "Redmine already does most of it"
  (`0011:91`). Redmine has a board, the review findings ledger and a journal that the store would
  have to build.
- **YouTrack Cloud (SK-0066).** The IntelliJ flow it was tried with lists at most 20 issues, and
  refuses worktrees with several repos in one window
  (`SK-0066-youtrack-tracker-design.md:136-140`). Skuld went back to VS Code and `plan`
  (`:163-165`). YouTrack is also closed source (`0011:83-84`, marked 🔍 there).
- **Emdash.** In Peter's verdict its agent integration is incomplete, it has no AI PR review, and
  its diff view has no key navigation (`SK-0070-adr-redmine-is-skuld-tracker.md:64-65`; 🔍
  against Emdash's docs). It is not a tracker, and its tracker adapters are read-only
  (`survey.md:119`, `:179-180`).
- **Multica.** Its license restricts hosting for third parties and works as a CLA
  (`survey.md:162-167`, §5;
  <https://github.com/multica-ai/multica/blob/main/LICENSE>).
- **OpenProject.** Baselines are in the paid Enterprise tier, and no board groups by a custom field
  (`0011:85-86`). It requires a CLA, and its MCP server is reported to be Enterprise, which is
  unverified (`survey.md:142-143`; 🔍).
- **Redmine without the plugins.** Without `review_findings`, review rounds have no ledger in
  Redmine. Without the key plugin, Redmine reads `#<n>` and not `SK-<n>`. Without skuld's own
  plugin, there is no core in Redmine and no journaled Done box. Those plugins are what Redmine
  needs for skuld's flow.
- **A Mac daemon.** A separate process on the Mac would run the git and worktree operations and
  the local file writes. It is a second local process to install and keep running, and it carries
  no responsibility the VS Code extension can't: every trigger already comes from VS Code, a
  `vscode://` link into the extension, or a CLI ([§3.7](#runtime)).
- **A status engine in the extension.** It would read `claude agents --json`, the waiting markers
  and the review rounds in the worktree, and write each issue's colour to a pulses file. With
  rounds and their report pages in the plugin, the worktree holds no report file to tell a fresh
  report by, and the rule for a waiting review is Python that an engine in TypeScript can't load.
  Session status in the core needs neither: the hooks report it, and each reader asks the core
  ([§3.7](#runtime)).
- **A local web server for pages.** 0014 serves reports from one Flask server (`0014:97-99`). On
  the Mac it is one more process to keep running, and the data it renders is in Redmine, whose
  plugin serves the pages instead ([§3.7](#runtime)).
- **One Redmine project per repo.** It splits one key prefix across several projects whenever a
  project's work spans repos, which Redmine does not need: a project holds any number of
  repositories ([§3.1](#adopted)).
- **Peter's own user for skuld's automation.** It needs no second account, and the journal then
  can't tell the import's writes from Peter's. Calls from the Mac do use Peter's own key, since
  each is something he did ([§3.3](#credentials)).

<a id="consequences"></a>

## 5. Consequences

<a id="operations"></a>

### 5.1 Data, failure and operations

- **Where data lives.** After the interim, tickets and review findings live in one Redmine database
  on the home Mac, and stay there: `redmine.db` in the volume `gh-plan-redmine_db`, with
  attachments in `gh-plan-redmine_files` (`redmine/README.md:41-46`).
- **Where data goes.** Ticket data leaves the database in the nightly backup and export to a USB
  drive, which "holds every issue body, unencrypted" (`README.md:53-63`). The backup copies the whole
  database, so it holds Redmine's API keys, which Redmine stores in plain text
  ([§3.3](#credentials)). It holds no GitHub token, since that comes from the host's secret store.
  The backup is kept as a secret ([§3.3](#credentials)). PR bodies on GitHub carry what they carry
  today. The store's remote stops receiving tickets when the interim ends.
- **Hosting.** A hosted move sends the database, the attachments and the credentials to the host.
  It is outside this decision's data boundary and needs its own decision ([§3.14](#open)).
- **An outage.** Redmine is down when Docker is stopped or the Mac is off (`0011:111-112`). After
  the interim the core runs inside Redmine ([§3.7](#runtime)), so an outage stops the flow itself:
  no Next up, Start, Ship or Done from the plugin. Branches, worktrees, commits, pushes and PRs
  still work by hand with git and GitHub. A merged `fixes` commit closes its issue when Redmine
  next fetches the repo. During the interim the Python flow works the store and Jira, and Redmine
  being down does not touch it.
- **A restore.** A restore puts the database back as it was at the backup. A restore during a
  failed upgrade "loses every change made since step 3's backup" (`README.md:379-380`). Status
  changes, journal notes and review rounds written since the backup are gone. Branches, worktrees,
  PRs and merges are not, so the database then disagrees with git and GitHub. Each step reconciles
  from the side that is still right:
  - **Start.** The issue is back at its status from before Start, and its branch and worktree
    exist. Start run again sends the transition, journals "Started" again and reuses the branch
    left behind ([failure-modes §3](../failure-modes.md#start), the Branch and worktree row).
  - **Ship.** A round that `pr` marked sent is a draft again or gone, though its commit and PR are
    pushed. Ship uploads the round again as a draft, and `pr` updates the PR and marks the round
    sent ([§3.7](#runtime)). Until then a draft round refuses Done
    ([failure-modes §4](../failure-modes.md#done)).
  - **Done.** The journal entry for the PR and its merge commit is gone, and so may be the `fixes`
    close. Done run again journals what is missing ([failure-modes §4](../failure-modes.md#done)),
    and Redmine closes the issue again when it next fetches the `fixes` commit 🔍.
- **The store meanwhile.** Until the interim ends, every store ticket's history is in the store's
  git history. Redmine holds none of it, apart from the end-to-end task, which each run resets
  ([§3.9](#interim-end)). A restore during the interim leaves every store ticket intact. This
  decision does not delete the store's repo.
- **Operations.** Peter runs, backs up and upgrades the server: start and stop
  (`README.md:21-28`), the nightly backup LaunchAgent (`:78-90`), and the upgrade with its rollback
  (`:343-388`). Until the setup moves into skuld ([§3.12](#redmine-folder)) these run from where it
  is now; after it, from skuld's main checkout. This is the cost ADR 0001 left Redmine over, and it
  is accepted.

<a id="follow-ups"></a>

### 5.2 Follow-up tickets

Each place this decision changes has a ticket in the store. The architecture tickets group sections
that change for one reason.

Until `docs/architecture.next.md` replaces it, `docs/architecture.md` is the architecture in force.
It describes skuld with the ticket store and Jira as its trackers. `docs/architecture.next.md`
describes skuld under this ADR, and the swap is SK-0071's. `docs/design.md` holds the design under
`docs/architecture.next.md`.

| Ticket  | What changes                                                                                                                                                                                                                                                                                                                                                                                       |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SK-0070 | This ADR; `docs/architecture.next.md`, `docs/design.md` and `docs/failure-modes.md` for Redmine and the core, and the interim-end suite. `docs/architecture.md` gains two sentences in §3 Storage: an `issues/` folder in skuld is not part of skuld's repo, and each Mac clones the same ticket store (`docs/architecture.md:76-77`, `:93-94`)                                                    |
| SK-0071 | Review `docs/architecture.next.md` §1 Purpose, §2 Definitions, §8 Failure modes, §9 Views and §10 rule 1 against this ADR; once SK-0072 to SK-0075 have reviewed their sections, swap it in as `docs/architecture.md`; GUIDE names the trackers as the architecture does                                                                                                                           |
| SK-0072 | Review `docs/architecture.next.md` §3.1 Credentials, §6.2 Process boundary and §10 rule 9 against [§3.3](#credentials): Peter's key, the bot user's key and the secret-store rule for the GitHub token                                                                                                                                                                                             |
| SK-0073 | Review `docs/architecture.next.md` §6.1 Ports against [§3.2](#adapter): the `Redmine` adapter in the `IssueStore` row, `FileStore` deprecated, and `storeFor` sending `SK` to `Redmine`                                                                                                                                                                                                            |
| SK-0074 | Review `docs/architecture.next.md` §5 Identity, §7.3 Ship, §7.4 Finish and §10 rule 2 against [§3.4](#keys) and [§3.5](#closing): `PREFIX-number` keys, `fixes <KEY>` as the one automated close, and Done boxes ticked in Redmine                                                                                                                                                                 |
| SK-0075 | Review `docs/architecture.next.md` §3 Storage, §6.1's `ReviewStore` row, §7.6 Review rounds and §10 rules 4 to 6 against [§3.6](#review-rounds) and [§3.11](#adr-0001): milestones as versions, rounds and their files in `review_findings`, and no section that names the ticket store as a tracker                                                                                               |
| SK-0076 | `docs/plan-file-issue-store.md`: its context, "Stay on Redmine" alternative and Redmine survey (§1, §7, §9)                                                                                                                                                                                                                                                                                        |
| SK-0077 | `docs/failure-modes.md`: Redmine down, a restore (the three reconciliations of [§5.1](#operations)) and a `fixes <KEY>` commit closing an issue. Its Start, Done and Session status sections follow the core already                                                                                                                                                                               |
| SK-0079 | The import of the store's tickets, Done boxes and rounds into Redmine, checks 9 to 11 of [§3.9](#interim-end)                                                                                                                                                                                                                                                                                      |
| SK-0080 | Redmine's skuld project with its prefix, versions and repos, the end-to-end task and a skuld bot user, check 12 of [§3.9](#interim-end)                                                                                                                                                                                                                                                            |
| SK-0081 | The Redmine key plugin, checks 7 and 8 of [§3.9](#interim-end)                                                                                                                                                                                                                                                                                                                                     |
| SK-0082 | The Redmine setup moves into skuld ([§3.12](#redmine-folder))                                                                                                                                                                                                                                                                                                                                      |
| SK-0084 | The core records rounds and their files in `review_findings`, check 4 of [§3.9](#interim-end)                                                                                                                                                                                                                                                                                                      |
| SK-0085 | Skuld's checklist for Done boxes, in its own plugin ([§3.5](#closing)), check 6 of [§3.9](#interim-end)                                                                                                                                                                                                                                                                                            |
| SK-0086 | The ADR for the plugin's design of the core and the interface (Rails, Stimulus/Turbo; the language, Ruby, is decided in [§3.7](#runtime)): the `vscode://` links for Start, Open and Done, which `plan` opens too, and their handlers in the extension, checks 1, 2 and 5 of [§3.9](#interim-end)                                                                                                  |
| SK-0087 | Ship uploading the round as a draft, with a report page that tells a person to run `pr <KEY>`, Ship staging the round's changes, `pr` committing only what is staged, pushing on the Mac and marking the round sent, and the round produced at the end of the author's implementation step and of each review-resolve step: architecture §7.2 and the code, checks 3 and 4 of [§3.9](#interim-end) |
| SK-0089 | The work computer's Jira-centric arrangement, decided outside this ADR ([§3.2](#adapter))                                                                                                                                                                                                                                                                                                          |
| SK-0091 | The Redmine plugin reads the GitHub token from the host's secret store, and the token leaves the plugin's settings ([§3.3](#credentials))                                                                                                                                                                                                                                                          |
| SK-0092 | Redmine API keys are rotated on a schedule and after a suspected leak ([§3.3](#credentials))                                                                                                                                                                                                                                                                                                       |
| SK-0093 | Skuld moves to Redmine's hashed tokens when Redmine ships them ([§3.3](#credentials))                                                                                                                                                                                                                                                                                                              |
