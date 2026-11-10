# Which issues have a Copilot review waiting on their open PR: the status engine (lib/agents.py)
# turns those Desktops orange. A review is waiting while Copilot has reviewed the PR's head commit;
# the next push moves the head and clears it, so nothing here keeps state.
#
# It asks `gh pr view` in each issue's worktree, which finds the PR by the worktree's branch. That
# costs a GitHub call per issue, so the watch loop asks once a minute, not every tick.
import json

from .start import expandHome, run

COPILOT = "copilot-pull-request-reviewer"
DEFAULT_GH_COMMAND = "gh"
REVIEW_CHECK_SECONDS = 60


def headCommit(pr):
    commits = pr.get("commits") or []
    return commits[-1].get("oid") if commits else None


def isCopilots(review):
    return (review.get("author") or {}).get("login") == COPILOT


def reviewedCommit(review):
    return (review.get("commit") or {}).get("oid")


# An open PR whose head commit Copilot has reviewed.
def copilotReviewWaiting(pr):
    head = headCommit(pr)
    if pr.get("state") != "OPEN" or not head:
        return False
    return any(isCopilots(review) and reviewedCommit(review) == head for review in pr.get("reviews") or [])


# The PR for the worktree's branch, or None when it has none or gh fails.
def readPr(worktree, ghCommand=None):
    try:
        answer = run(expandHome(ghCommand or DEFAULT_GH_COMMAND), ["pr", "view", "--json", "state,reviews,commits"], worktree)
        return json.loads(answer)
    except (RuntimeError, OSError, ValueError):
        return None


# The keys, of {key: worktree}, whose PR has a Copilot review waiting. readPr(worktree) is given,
# so the tests make PRs up.
def reviewedKeys(worktreesByKey, readPr=readPr):
    return frozenset(key for key, worktree in worktreesByKey.items() if copilotReviewWaiting(readPr(worktree) or {}))
