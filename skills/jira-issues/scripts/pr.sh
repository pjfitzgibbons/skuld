#!/bin/sh
# pr [done] [--no-copilot] [KEY]: commit, push and open or update the PR for the current branch, in one step that is
# safe to repeat. Each step runs only when it has something to do:
#
#   commit  when the tree has changes: stages everything (.tmp/ is ignored) and commits with the
#           message in .tmp/KEY/commit.txt, then deletes that file, so the next change needs a new one
#   push    git push -u origin HEAD; a no-op when the remote has the branch's commits
#   PR      gh pc when the branch has no PR, else gh pb, which replaces the body with
#           .tmp/KEY/pr-body.md
#   Copilot when the push moved the PR's branch: asks Copilot to review it again, so each round of
#           review fixes is reviewed in turn (gh pc asks for a new PR). --no-copilot skips both
#           requests
#   replies when .tmp/KEY/replies/ holds <comment id>.md files: posts each to its review thread,
#           resolves the thread and deletes the file (pr_threads.py --post)
#   ticks   when .tmp/KEY/issue.md exists: applies its checkbox changes to the issue's description in
#           Jira, refusing any other difference, then deletes it (checklist.py)
#
# Once the PR has merged, pr sends only the replies and ticks, refusing a branch with work the PR
# lacks, then says to run plan done KEY, which cleans up: it checks what removing the worktree and
# pulling would delete or overwrite, which pr can't see. pr done does the same, and refuses a PR
# that has not merged.
#
# KEY defaults to the Jira key in the branch name, as for gh pc (ghbody.sh). It refuses the default
# branch and a branch whose PR closed unmerged.
set -eu

# The review replies and checklist ticks of .tmp/KEY/, after the push, so the replies describe code
# the PR has. Each script checks everything before its first write and deletes each file once it
# is sent, so a stopped pr resumes where it stopped. They need Python 3.10; /usr/bin/python3 is 3.9.
scripts=$(dirname "$(realpath "$0")")
send_round() {
  [ -d ".tmp/$n/replies" ] || [ -f ".tmp/$n/issue.md" ] || return 0
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' ||
    { echo "pr: .tmp/$n/ has replies or ticks to send, and python3 is older than 3.10" >&2; exit 2; }
  if [ -d ".tmp/$n/replies" ]; then
    number=$(gh pr view "$branch" --json number --jq .number)
    python3 "$scripts/pr_threads.py" "$number" --post ".tmp/$n/replies"
  fi
  [ ! -f ".tmp/$n/issue.md" ] || python3 "$scripts/checklist.py" "$n" ".tmp/$n/issue.md"
}

# M5: Jira smart commits (#close, #comment, #time, ...) are parsed out of commit messages, PR
# titles and PR bodies by Jira's GitHub integration -- not by git or gh themselves -- so nothing
# here would ever see one fire, and an accidental one would silently transition or comment on the
# ticket from outside Peter's hand (decision #5 in done.py says Done never transitions the issue;
# a stray #close in a commit message would defeat that from the Jira side instead). Strict rule,
# deliberately broad rather than a fixed list of command words (Jira's own list can grow): refuse
# any "#<word>" right after whitespace or line-start, on a line that also names a Jira key.
# https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/
is_smart_commit_line() {
  printf '%s\n' "$1" | grep -qE '[A-Z][A-Z0-9]+-[0-9]+' &&
    printf '%s\n' "$1" | grep -qE '(^|[[:space:]])#[A-Za-z]'
}

refuse_smart_commits_file() {
  while IFS= read -r line; do
    if is_smart_commit_line "$line"; then
      echo "pr: $2 looks like a Jira smart commit (a #<word> beside a ticket key); smart commits" >&2
      echo "    run from the GitHub side and would transition/comment on the ticket outside this" >&2
      echo "    review -- remove the #<word>. See" >&2
      echo "    https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/" >&2
      echo "    offending line: $line" >&2
      exit 2
    fi
  done < "$1"
}

# N2: refuse_smart_commits_file only ever saw the ONE message pr.sh itself was about to commit
# (.tmp/KEY/commit.txt) -- a commit already on the branch (made with `git commit -am "..."`, a
# squash, an amend, a cherry-pick, or anything else that never passed through commit.txt) would
# reach origin with pr's push and this check would never have seen it. This is the real guarantee
# (the no_terminal_jira_transition.py hook is belt-and-braces for what Claude types inline; this is
# what actually runs before anything reaches GitHub): scan EVERY commit message that push is about
# to send, not just the one pr.sh just wrote.
refuse_smart_commits_range() {
  # N7: --not --remotes=origin excludes everything reachable from ANY origin ref, not just
  # origin/$base -- so it still works when origin/$base itself is missing locally (a stale or
  # never-fetched remote-tracking ref, or one deleted with `git update-ref -d`), and when the
  # branch is fresh with no upstream at all (HEAD --not --remotes=origin is then just HEAD's own
  # history). There is no case where this can't run, so it always runs -- see the call site below.
  git log --format=%B HEAD --not --remotes=origin | while IFS= read -r line; do
    if is_smart_commit_line "$line"; then
      echo "pr: a commit already on $branch looks like a Jira smart commit (a #<word> beside a" >&2
      echo "    ticket key); smart commits run from the GitHub side and would transition/comment" >&2
      echo "    on the ticket outside this review -- remove the #<word> (reword/amend/rebase) and" >&2
      echo "    run pr again. See" >&2
      echo "    https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/" >&2
      echo "    offending line: $line" >&2
      exit 2
    fi
  done
}

finish=0
copilot=1
while [ $# -gt 0 ]; do
  case $1 in
    done) finish=1 ;;
    --no-copilot) copilot=0 ;;
    *) break ;;
  esac
  shift
done
# gh pc reads it, so a new PR isn't sent to Copilot either.
[ "$copilot" = 1 ] || export GHBODY_NO_COPILOT=1

root=$(git rev-parse --show-toplevel)
cd "$root"
branch=$(git branch --show-current)
echo "pr: checking $branch"
base=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
[ "$branch" != "$base" ] || { echo "pr: on $base; switch to the issue's branch" >&2; exit 2; }
# L2: the prefix segment (feat/, fix/, chore/, ...) is optional, matching issueBranch() (lib/
# start.py) and lib/issue.py's KEY_RE, so a bare KEY-slug branch (no prefix) still resolves its key.
n=${1:-$(printf '%s' "$branch" | sed -nE 's,^([^/]+/)?([A-Z][A-Z0-9]+-[0-9]+)-.*,\2,p')}
[ -n "$n" ] || { echo "pr: no Jira key in branch $branch; give it: pr KEY" >&2; exit 2; }
# N10: n (whether from $1 or sed'd off the branch) is used to build .tmp/$n paths below, well
# before ghbody.sh/checklist.py would ever validate it -- an unanchored or path-shaped value
# (../x, a/../../b, an absolute path) would read or write outside .tmp/ via those paths. Anchor-
# validated against the same key shape as lib/issue.py's KEY_RE/L2 before it's used anywhere.
printf '%s' "$n" | grep -qE '^[A-Z][A-Z0-9]+-[0-9]+$' ||
  { echo "pr: \"$n\" isn't a Jira key (expected KEY-123)" >&2; exit 2; }

state=$(gh pr view "$branch" --json state --jq .state 2>/dev/null || true)
[ "$finish" = 0 ] || [ "$state" = MERGED ] || { echo "pr: the PR for $branch has not merged" >&2; exit 2; }
case $state in
  CLOSED) echo "pr: the PR for $branch closed unmerged; start a new branch" >&2; exit 2 ;;
  MERGED)
    # Work the merged PR lacks would be left unpushed, and replies would describe it: stop, so Ship
    # shows it rather than reporting success.
    head=$(gh pr view "$branch" --json headRefOid --jq .headRefOid)
    if [ "$(git rev-parse HEAD)" != "$head" ] || [ -n "$(git status --porcelain)" ]; then
      echo "pr: the PR for $branch has merged, and $branch has work it lacks; start a new branch for it" >&2
      git status --short >&2
      exit 2
    fi
    send_round
    echo "pr: the PR for $branch has merged; run plan done $n to clean up"
    exit 0 ;;
esac

if [ -n "$(git status --porcelain)" ]; then
  message=".tmp/$n/commit.txt"
  [ -f "$message" ] || { echo "pr: the tree has changes and $message is missing" >&2; git status --short >&2; exit 2; }
  refuse_smart_commits_file "$message" "the commit message $message"
  git add -A
  git commit -q -F "$message"
  rm "$message"
  git log --oneline --stat -1
else
  echo "pr: nothing to commit"
fi

# N2/N7: the real guarantee. Scans every commit message push is about to send -- the one just made
# above AND any already on the branch, however it got there (git commit -am, an amend, a squash, a
# cherry-pick) -- not only the single message pr.sh itself wrote to commit.txt. Always runs: N7
# found that skipping it when origin/$base wasn't a local ref (e.g. after `git update-ref -d
# refs/remotes/origin/main`, or on a repo that's never fetched) let a smart commit reach a real
# push instead of merely delaying the check -- the push does not need the ref to exist, so there
# was never a reason to skip.
refuse_smart_commits_range

# The PR's head on GitHub before the push, so Copilot is asked again only when the push adds
# commits. GitHub's, not the local origin/<branch>, which another clone's push leaves behind. A
# read that fails leaves it empty, so Copilot is asked rather than the push stopped.
head_before=""
[ -z "$state" ] || head_before=$(gh pr view "$branch" --json headRefOid --jq .headRefOid || true)

# No -q: a slow push shows its progress. A credential prompt fails at once rather than waiting.
echo "pr: pushing $branch"
GIT_TERMINAL_PROMPT=0 git push -u origin HEAD

if [ -n "$state" ]; then
  # Straight after the push, so a later step that fails can't leave pushed commits unreviewed. A pr
  # stopped between the push and this request still can: the next one finds nothing new to ask about.
  if [ "$copilot" = 0 ]; then
    echo "pr: --no-copilot; Copilot isn't asked"
  elif [ "$head_before" != "$(git rev-parse HEAD)" ]; then
    echo "pr: asking Copilot to review the new commits"
    gh pr edit "$branch" --add-reviewer @copilot >/dev/null ||
      echo "pr: Copilot wasn't asked to review the PR; ask under Reviewers" >&2
  else
    echo "pr: no new commits; Copilot isn't asked again"
  fi
  echo "pr: updating the PR body"
  gh pb "$n"
else
  echo "pr: opening the PR"
  gh pc "$n"
fi

send_round

# The PR's URL. There's no VS Code Pull Requests extension here to open it in; this is the
# showUrl() seam's shell-side equivalent — printing it is all this script does. Opening it in a
# terminal browser is out of scope (see GUIDE.md).
url=$(gh pr view "$branch" --json url --jq .url)
echo "$url"
