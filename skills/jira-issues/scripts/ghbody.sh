#!/bin/sh
# Apply a drafted body from .tmp/ to the current branch's PR. Used by gh aliases:
#
#   gh pc [KEY]   pr create, title = the branch's first commit subject, body .tmp/KEY/pr-body.md;
#                 then requests Copilot's review
#   gh pb [KEY]   pr edit (this branch's PR) --body-file .tmp/KEY/pr-body.md
#
# KEY is a Jira issue key. It defaults to the one in the branch name: fix/CIAM-88-go-requests ->
# CIAM-88.
# GHBODY_DRY=1 prints the gh command instead of running it. GHBODY_NO_COPILOT=1 (pr --no-copilot)
# leaves Copilot off a new PR.
set -eu

kind=${1:?usage: ghbody.sh pr-create|pr-edit [KEY]}
root=$(git rev-parse --show-toplevel)
# L2: the prefix segment is optional, matching issueBranch() (lib/start.py) and lib/issuekeys.py's ISSUE_KEY_PATTERN,
# so a bare KEY-slug branch (no prefix) still resolves its key.
key=${2:-$(git branch --show-current | sed -nE 's,^([^/]+/)?([A-Z]+-[0-9]+)-.*,\2,p')}
[ -n "$key" ] || { echo "ghbody: no Jira key given and none in the branch name" >&2; exit 2; }
printf '%s' "$key" | grep -qE '^[A-Z]+-[0-9]+$' ||
  { echo "ghbody: '$key' is not a Jira key; the body is .tmp/KEY/pr-body.md" >&2; exit 2; }

case $kind in
  pr-create | pr-edit) file="$root/.tmp/$key/pr-body.md" ;;
  *) echo "ghbody: unknown kind $kind" >&2; exit 2 ;;
esac
[ -f "$file" ] || { echo "ghbody: $file not found" >&2; exit 2; }

# M5: Jira smart commits (#close, #comment, #time, ...) are parsed out of PR titles and bodies by
# Jira's GitHub integration, not by gh itself, so a stray one here would silently transition or
# comment on the ticket from the GitHub side instead of Peter's own hand. Strict rule, deliberately
# broad rather than a fixed list of command words (Jira's own list can grow): refuse any "#<word>"
# right after whitespace or line-start, on a line (or, for the title, the whole string) that also
# names a Jira key.
# https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/
is_smart_commit_line() {
  printf '%s\n' "$1" | grep -qE '[A-Z]+-[0-9]+' &&
    printf '%s\n' "$1" | grep -qE '(^|[[:space:]])#[A-Za-z]'
}

refuse_smart_commits_file() {
  while IFS= read -r line; do
    if is_smart_commit_line "$line"; then
      echo "ghbody: $2 looks like a Jira smart commit (a #<word> beside a ticket key); remove the" >&2
      echo "    #<word>. See" >&2
      echo "    https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/" >&2
      echo "    offending line: $line" >&2
      exit 2
    fi
  done < "$1"
}

run() { if [ "${GHBODY_DRY:-}" ]; then printf '%s\n' "$*"; else "$@"; fi; }

case $kind in
  pr-create | pr-edit)
    # PR descriptions carry no attribution.
    if grep -qi 'assisted-by\|co-authored-by' "$file"; then
      echo "ghbody: $file contains an attribution line; remove it" >&2; exit 1
    fi
    refuse_smart_commits_file "$file" "the PR body $file"
    if [ "$kind" = pr-create ]; then
      base=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
      title=$(git log --reverse --format=%s "origin/$base..HEAD" | head -n1)
      [ -n "$title" ] || { echo "ghbody: no commits ahead of origin/$base" >&2; exit 2; }
      if is_smart_commit_line "$title"; then
        echo "ghbody: the PR title '$title' looks like a Jira smart commit (a #<word> beside a" >&2
        echo "    ticket key); remove the #<word>. See" >&2
        echo "    https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/" >&2
        exit 2
      fi
      run gh pr create --title "$title" --body-file "$file"
      # gh pc asks Copilot to review each new PR; pr.sh asks again each time it updates one,
      # and gh documents @copilot for pr edit, not pr create. A failed request doesn't fail gh pc.
      if [ "${GHBODY_NO_COPILOT:-}" != 1 ]; then
        run gh pr edit --add-reviewer @copilot ||
          echo "ghbody: Copilot's review was not requested; request it under Reviewers on the PR" >&2
      fi
    else
      run gh pr edit --body-file "$file"
    fi ;;
esac
