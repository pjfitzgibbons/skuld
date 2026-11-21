# Failure modes

## Contents

- [1. Jira silently blocks a transition](#jira-silently-blocks-a-transition)
- [2. A smart-commit token silently transitions or comments on the issue](#smart-commit-token)
  - [2.1 Open](#smart-commit-open)
- [3. Start](#start)
  - [3.1 Open](#start-open)
- [4. Done](#done)
  - [4.1 Open](#done-open)
- [5. Session status](#session-status)

A command that writes outside its own process, to git, the tracker, GitHub or a file another
window reads, gets a failure-mode table in its PR body before its code is written. The table lists
each step that writes or reads outside state against five ways it can go wrong:

| Column | The question |
|---|---|
| **Fails** | The step fails part way. Does the next run finish the job, from whatever it left behind? |
| **Twice** | The command runs again after success. Does it change nothing? |
| **At once** | Two runs overlap, from two invocations or from the terminals of two issues in the plan window. Does one join the other, or fail before it writes? |
| **Stale** | What it read has changed since: an issue, a status, a folder deleted by hand. Does it check again before writing? |
| **Guards** | Each limit or existence check it adds. Which recovery does it block? |

A cell names the line or test that answers it, or says "open" for Peter to decide. Every *Fails*
cell gets a row in the command's fault-injection test, which fails that step once and asserts the
next run ends where a clean one does (`STEPS` in `test/start.test.js`).

A review fix re-reads the whole table. Prefer a refusal that names its fix to automatic recovery:
recovery code is new surface.

Only a silent failure needs a fix or an *Open* row. That means one that leaves no trace and loses
work, files, commits or the tracker's state. A refusal or an error the user sees is already
handled.

<a id="jira-silently-blocks-a-transition"></a>

## 1. Jira silently blocks a transition

This section and the next are the Jira adapter's, on the work computer. Jira can fail a transition
two ways, and the adapter's read-back guard exists because of the second, rarer one:

- **Outright refusal (the common case).** The transition id posted doesn't correspond to an
  allowed transition from the issue's current status, so `POST /rest/api/3/issue/CIAM-123/
  transitions` returns a 4xx with a body naming the problem. This is already visible to the
  caller; `lib/jira.py` avoids it by listing the issue's actual available transitions and picking
  the one whose `to.name` matches the configured target immediately before posting, so it happens
  only when the issue's status changed between the lookup and the POST (an "at once" case,
  below).
- **Accepted but didn't move (the rare case).** The POST returns success (Jira accepts the
  transition id as structurally valid for the issue) but a workflow condition or validator
  attached to that transition — a permission scheme, a required-field validator, a custom
  condition — prevents the actual status change from taking effect. The transition call can
  succeed at the API layer while the issue's status is unchanged.

A success response doesn't mean the status changed. Every write that posts a transition goes
through `jira.py`'s transition call, which reads the issue back afterward. If the status isn't the
one it posted for, the write is refused, naming the issue, the attempted status and the actual
status. Any comment posted in the same call is not rolled back, and the refusal says so.

<a id="smart-commit-token"></a>

## 2. A smart-commit token silently transitions or comments on the issue

[Jira smart commits](https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/)
let a `#<word>` token in a commit message, PR title or PR body (`#close`, `#comment`, `#time`, ...)
act on the Jira issue named next to it — read and acted on by Jira's GitHub integration itself,
not by anything this tool calls. A commit message, PR title or body that accidentally carried one
would transition or comment on the issue the moment it reached GitHub, from outside Peter's own
hand — the same failure "Done never changes the status" ([§4](#done)) is built to prevent, but
via a path neither `done.py` nor its `PreToolUse` hook watches (the token never appears in a Bash
command the hook sees if it's typed straight into `commit.txt` or `pr-body.md` by a text edit
instead).

Four independent checks, each catching a different point the text could reach GitHub from:

- `pr.sh` refuses to commit if `.tmp/KEY/commit.txt` carries a `#<word>` right after whitespace or
  line-start on a line that also names a Jira key.
- `pr.sh` also scans the whole commit range it's about to push (`git log --format=%B HEAD --not
  --remotes=origin`), not only the message it just wrote to `commit.txt` — so a smart commit
  already on the branch some other way (`git commit -am`, an amend, a squash, a cherry-pick) is
  caught too, and the scan always runs rather than being skipped when `origin/<base>` is not a
  local ref.
- `ghbody.sh` refuses `gh pr create`/`gh pr edit` the same way, checking the PR title (`pr-create`,
  from the branch's first commit subject) and `pr-body.md`'s content.
- Claude's own `no_terminal_jira_transition.py` `PreToolUse` hook blocks two shapes of the same Bash
  call: the inline text itself (`git commit -m`/`--message`, or `gh pr create|edit --title|--body`,
  including combined short flags like `-am`), and the *content* of whatever file a `-F`/`--file`/
  `--body-file` flag points at — in any of its forms (`-F path`, `-Fpath`, `-F=path`, `--file=path`,
  `--body-file=path`), through a `git`/`gh` global option before the subcommand (`git -C <dir>
  commit`, `git -c k=v commit`), and through a heredoc when the flag reads from stdin (`git commit
  -F- <<EOF ... EOF` — the hook reads the heredoc body out of the command text itself, since there
  is no file on disk to check). The same hook also blocks `gh api ... pulls/<n>` calls carrying
  `-f`/`-F title=...`/`body=...` fields with the token-next-to-a-key pattern, including the
  attached `--body-file=x`/`--title=` forms on `gh pr` — `gh api` can write a PR's title/body
  directly, bypassing `gh pr create`/`edit` entirely.

The rule is deliberately broad — any `#<word>`, not a fixed list of smart-commit command words —
since Jira's own list of recognized commands can grow; a false positive (an ordinary `#3`-style
reference with no Jira key on the same line passes; a Jira key with no `#<word>` passes) is the
accepted cost of never letting one through silently.

<a id="smart-commit-open"></a>

### 2.1 Open

- **A smart-commit token written directly into `commit.txt`/`pr-body.md` by hand, then pushed
  through some path other than `pr`/`ghbody`/a Claude Bash call** (e.g. Peter running `git commit`
  himself from a different terminal) is not caught by any of the four checks above, since none of
  them runs.
- **A PR's title or body edited directly in the GitHub UI**, after `pr`/`ghbody` already pushed it,
  is not caught by anything in this repo — none of the four checks run again at that point, and
  nothing here watches GitHub for a post-creation edit.
- **A squash-merge commit message edited in the merge dialog at merge time** (GitHub lets the
  merger rewrite the squash commit's message, which defaults to the PR body/title but need not stay
  that way) is likewise uncovered — the message that actually reaches `origin/<base>`'s history can
  differ from everything `pr`/`ghbody`/the hook ever checked. **Remedy:** check the squash message
  in the merge dialog for a smart-commit token before confirming the merge.

<a id="start"></a>

## 3. Start

Start and Open are triggered from the Mac, at the issue's `vscode://` link, which the issue page and
`plan` both open, so VS Code must be running for them. For Start the core is called first: it reads
the issue, moves it to "in progress", reads it back and journals "Started", and it can refuse. A
refusal leaves the Mac untouched. Once the core accepts, the git and worktree module does the work
on the Mac.

Within a session, a second invocation on an issue joins the Start under way. Across sessions, a
lock file in the repo's git dir, holding the invoking process's pid, refuses the second Start
before the module changes anything. A lock left by a Start that stopped is never taken over,
because two processes taking it at once could both win. The refusal names the file for a person to
delete.

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| Read the issue (core) | Nothing written; rerun | — | — | A closed issue is refused, and so is a parent issue with open children | — |
| Read the blockers (core) | Nothing written; rerun | — | — | Only open issues the issue depends on through a `blocks` relation are listed | — |
| Transition to in progress (core) | Nothing on the Mac has changed; rerun sends it | Skipped when the issue is already in progress | Open | The core reads the issue back, and a status that didn't change is refused, naming the attempted and actual status | — |
| "Started" journal entry (core) | Rerun journals it again | Not deduplicated: see [§3.1](#start-open) | Open | — | — |
| Lock | — | Released after every Start | The second invocation is refused | A dead holder's lock is refused by name, never taken over | Blocks recovery after a crash until the file is deleted |
| Fetch origin | Stops before any branch | No-op | git locks the repo | `--prune` drops a deleted branch; the base is `origin`'s default branch | — |
| Setup marker | Written before the worktree, removed if `git worktree add` fails and leaves no worktree, and after the handoff, so a failure in between reruns setup | Absent once set up, so Open changes nothing | — | A worktree made by hand or before the marker counts as set up | — |
| Branch and worktree | Rerun reuses the branch left behind, on its own base | Found by key, reused | Behind the lock | A listed worktree deleted by hand is refused, naming `git worktree prune`; the issue's branch in the main checkout is refused; a worktree mid-rebase is found by its folder | Only a prefix the tracker adapter gives, or no prefix, names an issue's branch |
| Pushed branch | — | — | — | An issue branch on `origin` is checked out, tracking it, before a new one is made | — |
| Issue workspace file and plan workspace file | Rerun rewrites them | The issue workspace file is rewritten; a worktree already in the plan workspace is not added again, and skuld's main checkout is moved to the front when it is listed further down | Behind the issue's lock. The plan workspace and the issues file are also changed under an exclusive lock on each read-modify-write, so Start, Open and Done never interleave their changes | — | Each is written through a uniquely named temp file and a rename |
| Plan window | A failed `code` stops Start, naming the step and the `code` command to run by hand (`term.step`); what the earlier steps opened stays open, and the marker stays, so the next Start retries | `code <plan workspace>` opens the plan window or raises the one already showing it, so Open never opens a second | Behind the lock | A window closed by hand is opened again by the next Start or Open, with every issue's folder | VS Code knows a window by its workspace file's path, never by its title |
| Issue page tab and terminals (`plan-window`), when the issue is opened | A bad or missing `plan.*` key in the issue workspace file shows a `plan window: …` notification and opens no terminal; a person starts Claude by hand | The issue page's existing tab is brought to the front, never a second one, and the `Claude <KEY>` and `Shell <KEY>` terminals are found by name and reused | — | A new `Claude <KEY>` terminal runs `claude --continue` when the worktree has an earlier Claude session, and `claude "$PLAN_PROMPT"` when it has none | Runs in the plan window. The issue page opens first, then the shell, then the Claude terminal is focused or created, so focus ends on Claude |
| Claude in its terminal | `claude` exiting or failing to start drops the `Claude <KEY>` terminal to a login shell in the worktree, where a person starts it again | A reused terminal is shown, not restarted | — | — | — |
| The `/open` link (`code --open-url`) | Best-effort: Start or Open still prints `<KEY> ready.`, adds `couldn't open <KEY> in the plan window: <error>; click it in the Issues view` on stderr, and a person clicks the issue in the Issues view | Opening an issue that is already open focuses its terminal | — | — | — |
| The issue page (`plan.ticketUrl`), when the issue is opened | Start has printed the URL; a page that doesn't open is opened from it, or with `plan browse <KEY>` | Each time the issue is opened, shows the page in the integrated-browser tab already on that URL, or in a new tab when none is | — | — | — |

<a id="start-open"></a>

### 3.1 Open

Each case is unlikely in use, and its worst case is one an agent or a person can read and undo.

| Case | Worst case | Recovery |
|---|---|---|
| Two invocations press Start on a new issue at once, and both reach the core before either takes the lock | A second "Started" journal entry | Delete the extra note in Redmine (**UNVERIFIED**: needs the permission to edit notes) |
| A `/open` or `/browse` link reaches a VS Code window that is not the plan window | The window shows "this link is for the plan window." and does nothing; the Issues view, the click command and the watchers exist only in the plan window | Send it again with the plan window open (`plan open <KEY>`, or click the issue in the Issues view) |
| A finished issue is reopened (Done removed its key and folder; its terminals are still open) | Start refuses a closed issue | One manual step: set the issue back to in progress in Redmine, then start it. Start makes the worktree again (a local branch for the issue, else `origin`'s, else a new one from the default branch), adds the key and the folder, and the Claude terminal runs `claude --continue` when the worktree's conversation files exist |
| A line in the issues file does not match `ISSUE_KEY_PATTERN` ([design §3.5](design.md#naming)), such as `ab-1` or a project prefix with a digit | The line is ignored: no Issues view item and no worktree folder | Correct the line, or start the issue again |
| The plan window was closed by hand | No Issues view and no tints show until it opens again | The next Start or Open reopens it with every issue's folder; opening an issue that has no terminal starts Claude in its worktree, with `claude --continue` when there is an earlier session |
| VS Code is not running when `plan` opens a Start, Open or Done link | `code --open-url` starts VS Code, or the link reaches no handler | Open the plan window, then open the link again |
| A window opened on an issue's own workspace file is still open beside the plan window | Two windows show the issue's worktree, and the terminals in the old window are outside the Issues view | Close the old window; opening the issue in the plan window starts `claude --continue` there |
| A subject with no ASCII letters or digits | The branch is named `bug/SK-123-` | Rename the branch |
| An issue started in repo A, then opened with B as the current folder | The module searches only the project's repos that `checkouts` lists. When A is listed, its existing worktree or branch wins over B. When A is not listed, the work in A isn't found: new work goes in B if B is a candidate, else in the one candidate, else a pick opens, and with no candidate the run is refused | Add A to `checkouts`, or remove the work in A by hand |
| An issue's worktree or branch exists in more than one candidate repo at once (started by hand in two places, or two repos with a same-named branch) | The module refuses, naming every repo it was found in, and picks none | None needed: it's a refusal. A person removes the extra one |
| Each call from the Mac reads the Redmine API key from the Keychain again | A Start that makes several calls is slower | None needed |
| `code` is missing, the plan window is closed, or any other `showUrl()` step fails | The page never opens | `showUrl()` prints the URL first, and every later step is best-effort, so a failure never blocks or fails Start. Click the printed URL |
| `showUrl()` focuses the plan window with `code <plan workspace>`, then sends `vscode://skuld.plan-window/browse?url=<url>&key=<KEY>`, which VS Code routes to the focused window; focus moves to another window between the two commands | The other window shows "this link is for the plan window." and opens no page | Run `plan browse` again with the plan window focused, or open the printed URL |
| `plan browse <url>` run with no key named and no `PLAN_KEY` in the terminal | The link carries no key | None needed: `showUrl()` focuses the plan window before it sends the link |
| `showUrl()` or Start's `/open` link aims at a plan window that isn't open yet: `code <plan workspace>` opens it, and the `/browse` or `/open` link arrives before its extensions have activated | The page or the issue doesn't open, or the page opens in another window | **UNVERIFIED**: never run live, and left **open**. Run `plan browse <KEY>`, or click the issue in the Issues view, once the window is up |
| No `~/.vscode/extensions/skuld.plan-window-*` entry resolves into skuld's main checkout: the link is missing, or points at another checkout such as a worktree | The links are handled by another checkout's extension, or by none. When it activates, the extension shows where the link points (or that no link is in the folder) and the numbered fix | Follow the steps, every path shell-quoted: `rm` a leftover link of another version, `mv -i` a real directory at any `skuld.plan-window-*` name into `~/.Trash/` (recoverable, and outside the extensions folder, so it stops matching), `mkdir -p` an extensions folder that does not exist, `ln -sfn <skuld's main checkout>/vscode/plan-window ~/.vscode/extensions/skuld.plan-window-0.0.1`, then "Restart VS Code (quit and reopen)." Any version suffix that resolves into skuld's main checkout passes. The extension only reads the link and never runs `ln`, `mv` or `mkdir` |
| plan-window isn't installed, so nothing handles the `/browse`, `/open`, Start or Done link | The page or the issue doesn't open | VS Code shows its own notice, and the URL is already printed |
| The plan window's folders aren't trusted | VS Code keeps `plan-window` disabled, so no issue page tab, no Claude terminal and no Issues view | Restricted Mode shows in the window. Trust, once, the folder that holds each configured repo with its `<repo>.worktrees/`, and skuld's main checkout |
| A link clicked in a terminal of the plan window names a host other than localhost or loopback | The page opens in the system browser, outside the plan window | None in the window: `workbench.browser.openLocalhostLinks` takes only localhost and loopback links. Open it with `plan browse <url>` |

<a id="done"></a>

## 4. Done

Done finishes an issue once its PR has merged. It is triggered from the Mac, at the issue's
`vscode://` link, as Start is. The core is called first, and it can refuse; a refusal leaves the
Mac untouched. It checks everything before it changes anything: the PR is merged, no round is
unsent, the worktree holds nothing the PR lacks, `origin` holds the merge, and the base can
fast-forward to it. Each later step runs only when it has something left to do, so a Done that
stopped part way is finished by the next one, and a Done after success changes nothing. A Done
that fails part way says what it changed before the failure. Start, Done and `pr` take one lock per
issue, so Open, Done and `pr` on the same issue exclude each other.

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| Read the issue (core) | Nothing written; rerun | A closed issue is accepted | — | An issue neither in progress nor closed is refused; a parent issue is ignored | — |
| Find the PR (core) | Nothing written; rerun | — | — | Found from the issue's branch through `PullRequests`, fresh each time, never from a stored tracker field. An open PR, or one closed unmerged, is refused with nothing changed | The branch is the only source of which PR belongs to the issue |
| Check for an unsent round (core) | Nothing written; rerun | — | — | A draft round in `review_findings` refuses Done, naming the round | — |
| Lock | — | Released after every Done | Within a session a second Done joins the one under way; across sessions it is refused. A Start or Open of the same issue is refused, and so is a Done while `pr` holds the lock. `pr` holds it only for its own run, and a second `pr` in that window is refused by name | A dead holder's lock is refused by name, never taken over | Blocks recovery after a crash until the file is deleted |
| Fetch origin | Stops before any change | No-op | git locks the repo | `origin` must be the PR's repo, must still have the base, and `origin/<base>` must hold the merge commit. A local base must be behind it, unless another worktree has it checked out. Otherwise Done stops before any change | A PR head that isn't local is fetched from `refs/pull/<n>/head` before any change; if that fetch fails, Done stops, rather than reading the head as missing |
| Bring the base up | The next Done fast-forwards it again | A no-op | git locks the repo | The main checkout switches to the base only from the issue's branch, and changes on the base refuse Done before the worktree goes. A base checked out in another worktree is left for a person to pull there | Fast-forward only, checked before any change |
| Remove the worktree | The next Done removes it, or drops git's entry for a folder already gone | Skipped once gone | Behind the lock | Checked before any change: changes, commits the PR's merged head lacks, or an ignored file or folder outside the disposable list refuse it, naming each folder once | Only the disposable list's entries go with the worktree: `.tmp/` and what a tool makes again |
| Delete the branch | The next Done deletes it | Skipped once gone | Behind the lock | Every branch named for the issue, deleted only when its tip holds nothing the PR's merged head lacks | — |
| **Change a Redmine issue's status** | **N/A: Done has no step that does it** | **N/A** | **N/A** | **N/A** | Structural: Done makes no status write. The one automated close is Redmine's own, from `fixes <KEY>` when it fetches the merge commit |
| Close a store ticket | The next Done closes it; a Done stopped after the merge line adds no second line | A ticket already `done` with `closed:` set is untouched | Behind the lock | Checked before any change: every box under the ticket's `## Done` is ticked, the closed ticket passes the checker, and the store has `scripts/index.py`; any that fails refuses Done, naming it. A ticket `done` without `closed:` gets only the date | `issue_update`'s code writes the front matter and runs `scripts/index.py`; nothing is committed, the watcher commits (§7.7); never a Jira issue |
| Journal the PR and its merge commit (core) | The next Done journals what's missing | Journals a second entry: see [§4.1](#done-open) | Open | — | A journal entry, never a status change |
| Print the close note | — | Printed every time; harmless | — | Done reads the issue back each time: "closed by <commit>" when Redmine has closed it, else a warning that Redmine has not closed it yet | Done never opens a page |
| Delete the issue workspace file | — | Skipped once gone | Behind the lock | A file that won't delete is a warning in Done's notes, never a failure; delete it by hand | Deletes `<KEY>.code-workspace` in `workspaceDir`, and an older one beside the worktree |
| Remove the issue from the issues file and the plan workspace file | The next Done removes them, or finds them already gone | Skipped once gone | Behind the issue's lock. Each file is also changed under an exclusive lock on its read-modify-write and written through a uniquely named temp file, so another issue's Start never loses Done's change | `term.removeIssue(key)` drops the issue's line only if one exists, writing only on an actual change; the plan workspace's folder goes the same way. The issue leaves the Issues view; its `Claude <KEY>` and `Shell <KEY>` terminals stay open until a person closes them, since Done is often run from one | — |

<a id="done-open"></a>

### 4.1 Open

Each case fails silently, and its worst case is one an agent or a person can read and undo.

| Case | Worst case | Recovery |
|---|---|---|
| A second Done run after the journal entry, before the worktree and branch cleanup finished | A duplicate journal entry of the merge on the issue | Delete the extra note in Redmine (**UNVERIFIED**: needs the permission to edit notes) |
| GitHub keeps the PR's branch after the merge | `origin/<branch>` survives, and a later Start of a reopened issue checks out the merged branch | `git push origin --delete <branch>` |
| On the work computer, `CIAM-123` appears in an unrelated commit message or PR title in the same repo | GitHub's Jira integration links it to that issue's development panel | None; this is a property of the GitHub and Jira integration, outside this tool |

<a id="session-status"></a>

## 5. Session status

Claude Code's hooks report each session's state to the core, and the core keeps it and gives each
issue its tint ([design §3.3.6](design.md#session-status)).

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| A hook's call to the core | The call is dropped: the hook prints nothing and exits 0, so the session goes on, and its next event reports its state again | A repeated call sets the same state | Each call carries one session's state, so two sessions' calls never touch the same record | A session whose folder names no issue in Redmine is placed on `-` | — |
| The Issues view reads the tints | No tint shows until the next read | — | — | Read from the core each time | — |
