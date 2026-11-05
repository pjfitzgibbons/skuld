# Failure modes

A command that writes outside its own process, to git, Jira, GitHub or a file another window
reads, gets a failure-mode table in its PR body before its code is written. The table lists each
step that writes or reads outside state against five ways it can go wrong:

| Column | The question |
|---|---|
| **Fails** | The step fails part way. Does the next run finish the job, from whatever it left behind? |
| **Twice** | The command runs again after success. Does it change nothing? |
| **At once** | Two runs overlap, from two invocations or two tmux sessions. Does one join the other, or fail before it writes? |
| **Stale** | What it read has changed since: an issue, a status, a folder deleted by hand. Does it check again before writing? |
| **Guards** | Each limit or existence check it adds. Which recovery does it block? |

A cell names the line or test that answers it, or says "open" for Peter to decide. Every *Fails*
cell gets a row in the command's fault-injection test, which fails that step once and asserts the
next run ends where a clean one does (`STEPS` in `test/start.test.js`).

A review fix re-reads the whole table. Prefer a refusal that names its fix to automatic recovery:
recovery code is new surface, and some findings in the original tool came from it.

Only a silent failure needs a fix or an *Open* row. That means one that leaves no trace and loses
work, files, commits or the tracker's state. A refusal or an error the user sees is already
handled.

Before each push, `/code-review high` runs on the branch's whole diff, review fixes included, and
the PR body says it ran.

## Jira silently blocks a transition

Jira can fail a transition two ways, and this tool's read-back guard exists because of the second,
rarer one:

- **Outright refusal (the common case).** The transition id posted doesn't correspond to an
  allowed transition from the issue's current status, so `POST /rest/api/3/issue/CIAM-123/
  transitions` returns a 4xx with a body naming the problem. This is already visible to the
  caller; `lib/jira.py` avoids it by listing the issue's actual available transitions and picking
  the one whose `to.name` matches the configured target immediately before posting, so it should
  only happen if the issue's status changed between the lookup and the POST (an "at once" case,
  below).
- **Accepted but didn't move (the rare case).** The POST returns success (Jira accepts the
  transition id as structurally valid for the issue) but a workflow condition or validator
  attached to that transition — a permission scheme, a required-field validator, a custom
  condition — prevents the actual status change from taking effect. The transition call can
  succeed at the API layer while the issue's status is unchanged.

A success response doesn't mean the status changed. Every write that posts a transition goes
through `jira.py`'s transition call, which reads the issue back afterward. If the status isn't the
one it posted for, the write is refused, naming the issue, the attempted status and the actual
status. Any comment posted in the same call is not rolled back — the refusal says so, the same way
the original Redmine guard did for a saved journal note.

## A smart-commit token silently transitions or comments on the issue

[Jira smart commits](https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/)
let a `#<word>` token in a commit message, PR title or PR body (`#close`, `#comment`, `#time`, ...)
act on the Jira issue named next to it — read and acted on by Jira's GitHub integration itself,
not by anything this tool calls. A commit message, PR title or body that accidentally carried one
would transition or comment on the issue the moment it reached GitHub, from outside Peter's own
hand — the same failure "Done never transitions the issue" (above) is built to prevent, but via a
path neither `done.py` nor its `PreToolUse` hook watches (the token never appears in a Bash command
the hook sees if it's typed straight into `commit.txt` or `pr-body.md` by a text edit instead).

Four independent checks, each catching a different point the text could reach GitHub from:

- `pr.sh` refuses to commit if `.tmp/KEY/commit.txt` carries a `#<word>` right after whitespace or
  line-start on a line that also names a Jira key.
- `pr.sh` also scans the whole commit range it's about to push (`git log --format=%B HEAD --not
  --remotes=origin`), not only the message it just wrote to `commit.txt` — so a smart commit
  already on the branch some other way (`git commit -am`, an amend, a squash, a cherry-pick) is
  caught too, and the scan always runs rather than being skipped when `origin/<base>` happens not
  to be a local ref (N7).
- `ghbody.sh` refuses `gh pr create`/`gh pr edit` the same way, checking the PR title (`pr-create`,
  from the branch's first commit subject) and `pr-body.md`'s content.
- Claude's own `no_terminal_jira_transition.py` `PreToolUse` hook blocks two shapes of the same Bash
  call: the inline text itself (`git commit -m`/`--message`, or `gh pr create|edit --title|--body`,
  including combined short flags like `-am`), and the *content* of whatever file a `-F`/`--file`/
  `--body-file` flag points at — in any of its forms (`-F path`, `-Fpath`, `-F=path`, `--file=path`,
  `--body-file=path`), through a `git`/`gh` global option before the subcommand (`git -C <dir>
  commit`, `git -c k=v commit`), and through a heredoc when the flag reads from stdin (`git commit
  -F- <<EOF ... EOF` — the hook reads the heredoc body out of the command text itself, since there
  is no file on disk to check) (N11). The same hook also blocks `gh api ... pulls/<n>` calls
  carrying `-f`/`-F title=...`/`body=...` fields with the token-next-to-a-key pattern, including the
  attached `--body-file=x`/`--title=` forms on `gh pr` (N8) — `gh api` can write a PR's title/body
  directly, bypassing `gh pr create`/`edit` entirely.

The rule is deliberately broad — any `#<word>`, not a fixed list of smart-commit command words —
since Jira's own list of recognized commands can grow; a false positive (an ordinary `#3`-style
reference with no Jira key on the same line passes; a Jira key with no `#<word>` passes) is the
accepted cost of never letting one through silently.

### Open

- **A smart-commit token written directly into `commit.txt`/`pr-body.md` by hand, then pushed
  through some path other than `pr`/`ghbody`/a Claude Bash call** (e.g. Peter running `git commit`
  himself from a different terminal) is not caught by any of the four checks above, since none of
  them runs. This is the same class of gap as GH Plan's original attribution check, which only
  ever covered this tool's own commit path.
- **A PR's title or body edited directly in the GitHub UI**, after `pr`/`ghbody` already pushed it,
  is not caught by anything in this repo — none of the four checks run again at that point, and
  nothing here watches GitHub for a post-creation edit (N8).
- **A squash-merge commit message edited in the merge dialog at merge time** (GitHub lets the
  merger rewrite the squash commit's message, which defaults to the PR body/title but need not stay
  that way) is likewise uncovered — the message that actually reaches `origin/<base>`'s history can
  differ from everything `pr`/`ghbody`/the hook ever checked (N8). **Remedy:** check the squash
  message in the merge dialog for a smart-commit token before confirming the merge.

## Start (ported from gh-plan PR #110)

Within a session, a second invocation on an issue joins the Start under way. Across sessions, a
lock file in the repo's git dir, holding the invoking process's pid, refuses the second Start
before it changes anything. A lock left by a Start that stopped is never taken over, because two
processes taking it at once could both win. The refusal names the file for Peter to delete.

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| Read the issue | Nothing written; rerun | — | — | Any `statusCategory` but `new` or `indeterminate` is refused | — |
| Read the blockers | A blocker Jira won't show (permission scheme) is listed as unknown | — | — | — | Issue links of type other than "Blocks" are ignored; only the inward "is blocked by" direction counts |
| Lock | — | Released after every Start | The second invocation is refused | A dead holder's lock is refused by name, never taken over | Blocks recovery after a crash until the file is deleted |
| Fetch origin | Stops before any branch | No-op | git locks the repo | `--prune` drops a deleted `develop`; the base is `develop`, else origin's default, else `main` | — |
| Setup marker | Written before the worktree, removed if `git worktree add` fails and leaves no worktree, and after the handoff, so a failure in between reruns setup | Absent once set up, so Open changes nothing | — | A worktree made by hand or before the marker counts as set up | — |
| Branch and worktree | Rerun reuses the branch left behind, on its own base | Found by key, reused | Behind the lock | A listed worktree deleted by hand is refused, naming `git worktree prune`; the issue's branch in the main checkout is refused; a worktree mid-rebase is found by its folder | Only `feat/`, `fix/`, `chore/` or no prefix names an issue's branch |
| Pushed branch | — | — | — | An issue branch on `origin` is checked out, tracking it, before a new one is made | — |
| Transition to in-progress | Rerun sends it | Skipped when `statusCategory` is already `indeterminate` | Behind the lock | The transition is looked up by target status name at call time, never a hardcoded id. If the issue's status moved since the lookup, the POST 4xxs and Start refuses before the handoff. If the POST succeeds but the read-back shows the old status, Start refuses naming both statuses | — |
| "Started" comment | Rerun posts it again (idempotent-ish: a duplicate comment, not a correctness issue) | Not deduplicated — an open item, see *Open* below | Behind the lock | — | — |
| Handoff to Claude | The last setup step: a failed write leaves the marker, so the next Start retries it | tmux's `new-session -A` makes this a non-issue — re-running attaches instead of re-creating (see *Open*, "safe cases") | `tmux new-session -A` is attach-safe by design: two near-simultaneous invocations both converge on one session, neither errors | — | — |

### Open

Found during porting and left unfixed, or newly introduced by the terminal front end: each is
unlikely in use, and its worst case is one an agent or the human can read and undo.

| Case | Worst case | Recovery |
|---|---|---|
| Two invocations press Start on a new issue at once; the second read the issue before the first took the lock | A second "Started" comment | Delete the extra comment in Jira |
| A tmux session for the issue exists but the slots file's line for it points at a workspace the user closed by hand | Open could reuse a workspace with no windows left on it | Workspace reuse is gated on `windowsIn(workspace)`, not the slot lookup alone (`term.openIssueWorkspace`); a stale slot fails that check and a fresh free workspace is picked and relabelled instead |
| The slots file names a workspace for an issue whose windows the user closed, and no command runs again before the user looks at SketchyBar | The bar still shows a label for an empty workspace | Every workspace-touching command (`start`, `open`, `done`, `list`, the fzf picker) sweeps the slots file first (`term.sweepSlots`), dropping any line whose workspace has no windows |
| SketchyBar isn't installed, or its binary is missing | `sketchybar --trigger` would otherwise raise and abort the command | `term.triggerSketchybar()` swallows the `RuntimeError` and continues silently — a missing bar is not this tool's problem to report |
| Two `plan start`/`plan open` invocations race to create the same tmux session | None — `tmux new-session -A -s CIAM-123` is documented (confirmed via `man tmux`) to behave like `attach-session` when the session name already exists, so this case is **safe by construction**, not merely unlikely | None needed |
| Ghostty's new-window flow (`open -na Ghostty.app --args -e <cmd>`) has no synchronous way to learn the new window's AeroSpace window-id | A window matched by title text alone, racing another window opening at the same instant, could in principle be ambiguous | **Guarded**: the implementation diffs the before/after sets from `aerospace list-windows --monitor all --app-bundle-id com.mitchellh.ghostty --json`, never matches by title alone. A third window opening in the same race window (two *other* new Ghostty windows appearing between the before- and after-snapshot) is not guarded against and is left **open** |
| A subject with no ASCII letters or digits | The branch is named `feat/CIAM-123-` | Rename the branch |
| An issue started in repo A, then `plan open CIAM-123` run from a different repo B | **Resolved** when A is listed under `checkouts.<PROJECT>` (user decision 2026-11-05): `mainCheckout()` searches the cwd repo (B) *and* every listed repo, including A, for an existing worktree/branch before falling back to cwd for new work — found in A, it's used, not B. Still open if A is **not** listed anywhere in config: nothing but cwd (B) is searched, so a second worktree is made there, duplicating the first. **Recovery:** remove the duplicate worktree/branch in B by hand, or add A to `checkouts.<PROJECT>` beforehand |
| An issue's worktree or branch exists in more than one of cwd/the listed repos at once (e.g. started by hand in two places, or two listed repos both happen to have a same-named branch) | `mainCheckout()` refuses outright, naming every repo it was found in, rather than silently picking one | None needed — it's a refusal, not a silent failure; the human picks and removes the extra one. **OPEN:** whether one Jira issue may legitimately span two repos in the same round is not answered here; today it's always a refusal |
| Each Jira request reads the token from `~/.netrc` again, and blockers are fetched with issue links | A Start with several blockers is slower | None needed |
| `aerospace` or Brave is missing, or any `openUrl()` step fails (focus/diff/move) | The browser never opens or ends up on the wrong workspace | `openUrl()` always prints the URL first and wraps every Aerospace/`open` call best-effort; a failure there never blocks or fails Start itself |
| `open -a "Brave Browser" <url>` against an already-open Brave window doesn't put the URL in a new tab the way `open -na ... --new-window` is confirmed to | A second tab never opens, or opens in the wrong window | **UNVERIFIED** — never run live; not guarded today, left **open** |

## Done (ported from gh-plan PR #114)

Done finishes an issue once its PR has merged. It checks everything before it changes anything: the
PR is merged, the worktree holds nothing the PR lacks, `origin` holds the merge, and the base can
fast-forward to it. Each later step runs only when it has something left to do, so a Done that
stopped part way is finished by the next one, and a Done after success changes nothing. A Done that
fails part way says what it changed before the failure. Start and Done take one lock per issue, so
Open and Done on the same issue exclude each other.

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| Read the issue | Nothing written; rerun | `statusCategory` `done` is accepted, though Next up shows no Done on a Done-category issue | — | Any `statusCategory` but `indeterminate` or `done` is refused | — |
| Find the PR | Nothing written; rerun | — | — | Found via `gh pr view <branch>` / `gh pr list --head <branch>` fresh each time, never from a cached Jira field — an open PR, or one closed unmerged, is refused with nothing changed | Jira's native development panel is not read or trusted for this; the branch is the only source of truth for which PR belongs to this issue |
| Lock | — | Released after every Done | A second invocation joins the Done under way; a Start or Open of the same issue is refused | A dead holder's lock is refused by name, never taken over | Blocks recovery after a crash until the file is deleted |
| Fetch origin | Stops before any change | No-op | git locks the repo | `origin` must be the PR's repo, must still have the base, and `origin/<base>` must hold the merge commit. A local base must be behind it, unless another worktree has it checked out. Otherwise Done stops before any change | A PR head that isn't local is fetched from `refs/pull/<n>/head` before any change; if that fetch fails, Done stops, rather than reading the head as missing |
| Bring the base up | The next Done fast-forwards it again | A no-op | git locks the repo | The main checkout switches to the base only from the issue's branch, and changes on the base refuse Done before the worktree goes. A base checked out in another worktree is left for Peter to pull there | Fast-forward only, checked before any change |
| Remove the worktree | The next Done removes it, or drops git's entry for a folder already gone | Skipped once gone | Behind the lock | Checked before any change: changes, commits the PR's merged head lacks, or an ignored file outside the disposable list refuse it | Only the disposable list's entries go with the worktree: `.tmp/` and what a tool makes again |
| Delete the branch | The next Done deletes it | Skipped once gone | Behind the lock | Every branch named for the issue, deleted only when its tip holds nothing the PR's merged head lacks | — |
| **Attempt to transition the issue to a terminal status** | **N/A — no code path in this tool can do this** | **N/A** | **N/A** | **N/A** | Structural: `lib/done.py` contains no call to `jira.transition()` — grep `done.py` for `transition` to confirm. Defensive: a `PreToolUse` hook on the human's own Claude Code setup independently blocks any Bash command recognized as transitioning a Jira ticket to a terminal/closed state, so even a hand-run CLI command inside a Claude session is stopped a second time |
| Comment the issue | The next Done sends what's missing (idempotency not yet guarded — see *Open*) | Posts a second comment — open, below | Behind the lock | — | Posts an ADF comment linking the PR and merge commit; never a transition |
| Print "Close CIAM-123 in Jira" | — | Printed every time; harmless | — | — | Done never calls `openUrl()` (§7.3/§7.6.1) — it only prints the browse URL; a human closes the issue by hand in the Jira UI, or runs the transition themselves — this tool offers no shortcut for it |
| Free the issue's workspace slot | The next Done removes it, or finds it already gone | Skipped once gone | Behind the lock | `term.removeSlot(key)` drops the slot's line only if one exists, writing (and triggering SketchyBar) only on an actual change | — |

### Open

Found during porting and left unfixed: each fails silently, and its worst case is one an agent or
the human can read and undo.

| Case | Worst case | Recovery |
|---|---|---|
| A second Done run after the comment was posted, before the worktree/branch cleanup finished | A duplicate "Done" comment on the issue | Delete the extra comment in Jira |
| GitHub keeps the PR's branch after the merge | `origin/<branch>` survives, and a later Start of a reopened issue checks out the merged branch | `git push origin --delete <branch>` |
| Ship runs `pr` in the worktree while Done removes it; Ship takes neither the lock nor an in-session guard | `pr` fails part way, or pushes a branch Done just deleted | Don't run Done until Ship's window has finished; delete a re-pushed branch on GitHub |
| `CIAM-123` appears in an unrelated commit message or PR title in the same repo | GitHub's Jira integration may link it to that issue's development panel spuriously | None; this is a property of the GitHub↔Jira integration, outside this tool |
