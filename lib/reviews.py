# Which issues have a review of Claude's waiting on a person: the status engine (lib/agents.py)
# turns those Desktops orange. A review is waiting when the last finished review was of the
# worktree's current HEAD, left findings unsettled (open or New), and no review is unfinished. The
# next push moves HEAD and clears it, so nothing here keeps state.
#
# It reads the issue's round folders, .tmp/<KEY>/rounds/<n>/, in each worktree: the highest round
# whose review.json has a head is the last review, and a round with a request.json and no
# review.json is unfinished (skills/jira-issues/scripts/review.py writes them). HEAD comes from a
# local `git rev-parse`; no gh is called. The rule itself, is_orange(), and where the rounds are,
# rounds_folder(), are review.py's, loaded from there, so the script that writes a review and the
# engine that reads it can't differ.
import importlib.util
import os
import subprocess
import sys

from .start import git

REVIEW_CHECK_SECONDS = 10
REVIEW_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "jira-issues", "scripts", "review.py"
)


def loadReviewScript(path=REVIEW_SCRIPT):
    spec = importlib.util.spec_from_file_location("review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review = loadReviewScript()


# The last finished review's record, or None when there is none.
def readRecord(worktree, key):
    return review.last_record(review.rounds_folder(worktree, key))[1]


def reviewUnfinished(worktree, key):
    return review.unfinished_round(review.rounds_folder(worktree, key)) is not None


def headOf(worktree):
    try:
        return git(worktree, "rev-parse", "HEAD").strip()
    except RuntimeError:
        return None


# What the rule needs from an issue's worktree: (record, HEAD, round unfinished).
def readReviewState(worktree, key):
    return readRecord(worktree, key), headOf(worktree), reviewUnfinished(worktree, key)


def reviewWaiting(state):
    record, head, unfinished = state
    return head is not None and review.is_orange(record, head, unfinished)


# The keys, of {key: worktree}, whose review is waiting. readState(worktree, key) is given, so the
# tests make states up.
def reviewedKeys(worktreesByKey, readState=readReviewState):
    return frozenset(key for key, worktree in worktreesByKey.items() if reviewWaiting(readState(worktree, key)))


# The line pre-filled in the new Claude tab: the key, and the absolute path of the round's request.
def reviewPrompt(key, requestPath):
    return f"{key} review: read {requestPath} and do the review it describes."


# Writes the next round's request.md in the worktree (review.py request) and returns the path it
# printed, or "" when the script found nothing to review. claudeState is "working" or "stopped"; a
# round still running is refused by the script.
def requestRound(worktree, key, claudeState):
    command = [sys.executable, REVIEW_SCRIPT, "request", key, "--claude-state", claudeState]
    answer = subprocess.run(command, cwd=worktree, capture_output=True, text=True)
    if answer.returncode != 0:
        raise RuntimeError(answer.stderr.strip() or f"review.py request exited with code {answer.returncode}")
    return answer.stdout.strip()
