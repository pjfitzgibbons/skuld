# Which issues have a review of Claude's waiting on a person: the status engine (lib/agents.py)
# turns those issues orange. A review is waiting when the last finished review was of the
# worktree's current HEAD, left findings unsettled (open or New), and no review is unfinished. The
# next push moves HEAD and clears it, so nothing here keeps state.
#
# It reads the issue's round folders, .tmp/<KEY>/rounds/<n>/, in each worktree: the highest round
# whose review.json has a head is the last review, and a round with a request.json and no
# review.json is unfinished (skills/jira-issues/scripts/review.py writes them). HEAD comes from a
# local `git rev-parse`; no gh is called. The rule itself, is_orange(), and where the rounds are,
# rounds_folder(), are review.py's, loaded from there, so the script that writes a review and the
# engine that reads it can't differ.
import functools
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

from .start import git

REVIEW_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "jira-issues", "scripts", "review.py"
)


def loadReviewScript(path=REVIEW_SCRIPT):
    spec = importlib.util.spec_from_file_location("review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded on first use, so a missing or broken review.py fails only the review features and not
# every plan command that imports this module.
@functools.lru_cache(maxsize=None)
def review():
    return loadReviewScript()


# The last finished review's record, or None when there is none.
def readRecord(worktree, key):
    return review().last_record(review().rounds_folder(worktree, key))[1]


def reviewUnfinished(worktree, key):
    return review().unfinished_round(review().rounds_folder(worktree, key)) is not None


def headOf(worktree):
    try:
        return git(worktree, "rev-parse", "HEAD").strip()
    except RuntimeError:
        return None


# What the rule needs from an issue's worktree: (record, HEAD, round unfinished). A worktree whose
# rounds can't be read (a permission error, a malformed file) has no review waiting.
def readReviewState(worktree, key):
    try:
        return readRecord(worktree, key), headOf(worktree), reviewUnfinished(worktree, key)
    except Exception:
        return None, None, False


def reviewWaiting(state):
    record, head, unfinished = state
    return head is not None and review().is_orange(record, head, unfinished)


# The keys, of {key: worktree}, whose review is waiting. readState(worktree, key) is given, so the
# tests make states up.
def reviewedKeys(worktreesByKey, readState=readReviewState):
    return frozenset(key for key, worktree in worktreesByKey.items() if waitingOrNot(readState, worktree, key))


# One issue's answer; a record the rule can't read (a finding with no disposition, say) is no review
# waiting, and doesn't stop the other issues' colours.
def waitingOrNot(readState, worktree, key):
    try:
        return reviewWaiting(readState(worktree, key))
    except Exception:
        return False


# Writes the next review round's request.md in the worktree (review.py request) and returns (the
# path it wrote or reopened, or None, a note saying what happened, and the token of the request it
# wrote, None for one it reopened). restart restarts an
# unfinished round for an older head in its own folder; without it the script refuses and says so.
# ifDue (`pr`'s call) writes and reopens nothing when no review is due, checked under the script's lock.
def requestRound(worktree, key, restart=False, ifDue=False):
    command = [sys.executable, REVIEW_SCRIPT, "request", key] + (["--restart"] if restart else []) + (["--if-due"] if ifDue else [])
    answer = subprocess.run(command, cwd=worktree, capture_output=True, text=True)
    if answer.returncode != 0:
        raise RuntimeError(answer.stderr.strip() or f"review.py request exited with code {answer.returncode}")
    outcome = json.loads(answer.stdout)
    return outcome["path"], outcome["note"], outcome["token"]


# Takes back the request the token names, whose tab never opened, so the next `pr` writes the round
# again, and returns what the script did. The script does it under its lock and only while request.json
# still carries the token and no reviewer has claimed the round.
def withdrawRequest(worktree, key, token):
    command = [sys.executable, REVIEW_SCRIPT, "withdraw", key, "--token", token]
    answer = subprocess.run(command, cwd=worktree, capture_output=True, text=True)
    if answer.returncode != 0:
        raise RuntimeError(answer.stderr.strip() or f"review.py withdraw exited with code {answer.returncode}")
    return answer.stdout.strip()
