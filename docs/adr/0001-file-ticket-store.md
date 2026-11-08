# ADR 0001: Store issues as files in a git repo of their own

<a id="status"></a>

## 1. Status

Accepted.

<a id="context"></a>

## 2. Context

`plan` and `pr` work Jira issues for work, and need a tracker for everything else: skuld's own backlog and other personal projects. That work lived in gh-plan's Redmine, a server that only the home Mac can reach and that has to be run, backed up and upgraded. Its data isn't text an agent can grep. Skuld also kept a small file tracker of its own, one hard-coded project in `issues/` inside the code repo.

The tracker has to:

- be readable and editable as plain files, by a person or by an agent, with no server
- keep a full history of every change
- work the same on every machine that clones it
- sit behind the same port as Jira, so Start, Ship and Done have no tracker-specific branch ([architecture §6.1](../architecture.md#front-ends))

<a id="decision"></a>

## 3. Decision

Issues not in Jira are Markdown files with YAML front matter, in a ticket store: a folder that is a git repo of its own, at the path the machine config's `issueStore` names ([architecture §3](../architecture.md#storage)). `FileStore` is its `IssueStore` adapter.

- **Code and data are separate.** Skuld holds the code, the file spec, the issue template and `store.yml`, which names the store's projects and their repo remotes. The store holds data only: `active/`, `archive/`, `milestones/` and `reviews/<KEY>/`. No development repo holds tracker files.
- **One store, any machine.** Each machine clones the same store. Jira stays the work tracker, reached through its own adapter. A work machine's `issueStore` is a store whose remote may hold work data, never the home one.
- **`store.yml` lives in skuld,** versioned with the code that reads it.
- **A watcher commits the store.** `issue-watch` waits for edits to settle, runs the checker, and commits and pushes only a tree that passes. `plan` writes files and never commits.
- **The front matter is a restricted YAML subset,** read by a hand parser that the checker defines. The repo stays stdlib-only.
- **Numbers never repeat.** The next number is one above the highest in `active/` and `archive/` together. `plan new` pulls first, and the checker's duplicate-key rule catches two machines that took the same number offline; the second file is renumbered by hand.
- **One issue's work stays in one repo.** Existing work found in more than one of a project's repos is refused ([architecture §7.0](../architecture.md#repo-resolution)).
- **Done never changes an issue's status,** in either tracker. For a ticket it prints the file to set `status: done` in ([architecture §7.3](../architecture.md#finish)).
- **A round's review files live in the store,** under `reviews/<KEY>/`, so they outlive the worktree and are committed with the ticket they belong to.

<a id="alternatives"></a>

## 4. Alternatives considered

- **Stay on Redmine.** It works, but it is a server to run and back up, it is reachable only from the home Mac, and its data isn't plain text.
- **Files inside each development repo** (gh-plan ADR 0001 and 0008). Every Start becomes a commit to the default branch, and branches conflict on shared plan files.
- **An orphan branch per repo** (gh-plan ADR 0010). It avoids commits to the default branch, but adds a worktree and a symlink to every item worktree and still scatters the plan across repos.
- **A folder per status** (`open/`, `in-progress/`, `done/`). Every transition becomes a move, which makes a ticket's history harder to follow. Status is one field; only the archive is a folder.
- **`plan` commits its own writes.** The messages would be exact, but an edit by a person or an agent wouldn't be committed until the next `plan` write. The watcher commits every writer the same way.
- **`store.yml` in the store.** The store would describe itself and could be read without skuld. Nothing but skuld reads it, so the definition stays with the code.
- **PyYAML for front matter.** It handles YAML in full, which the flat schema doesn't need, and it would be the repo's first dependency.
- **`plan done` sets `status: done` after the merge.** The watcher would make the change visible, but it would break the rule that no code path closes an issue, which holds for Jira.

<a id="consequences"></a>

## 5. Consequences

- The store needs its own repo, remote, checker and watcher before it is safe to edit by hand: the checker refuses bad files, and the watcher keeps the history.
- Skuld's `issues/` stops being part of skuld's repo; skuld ignores it.
- Each Redmine project is imported into the store under a key prefix, and Redmine is retired.
- Every writer, whether `plan`, a person or an agent, must produce files the checker accepts. `plan new` and `plan set` exist so that nobody writes front matter by hand.
- Two machines can collide on a number while offline; that is caught, not prevented.
