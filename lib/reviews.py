# Which issues have a review of Claude's waiting on a person: the status engine (lib/agents.py)
# turns those issues orange. A review is waiting when the last finished review was of the
# worktree's current HEAD, left findings unsettled (open or New), and no review is unfinished. The
# next push moves HEAD and clears it, so nothing here keeps state.
#
# It reads the issue's round folders, .tmp/<KEY>/rounds/<n>/, in each worktree: the highest round
# whose review.json has a head is the last review, and a round with a request.json and no
# review.json is unfinished (bin/pr-helpers/review.py writes them). HEAD comes from a
# local `git rev-parse`; no gh is called. The rule itself, is_orange(), and where the rounds are,
# rounds_folder(), are review.py's, loaded from there, so the script that writes a review and the
# engine that reads it can't differ.
import collections
import functools
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

from .start import git

REVIEW_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "pr-helpers", "review.py"
)


def loadReviewScript(path=REVIEW_SCRIPT):
    spec = importlib.util.spec_from_file_location("review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# review.py, loaded on first use, so a missing or broken review.py fails only the review features
# and not every plan command that imports this module. It is loaded again when the file changes, so
# the long-running watch process follows a pulled change to the rule without a restart.
def review():
    return loadedReviewScript(REVIEW_SCRIPT, os.stat(REVIEW_SCRIPT).st_mtime_ns)


@functools.lru_cache(maxsize=1)
def loadedReviewScript(path, version):
    return loadReviewScript(path)


# The last finished review's record, or None when there is none.
def readRecord(worktree, key):
    return readRounds(review(), worktree, key)[0]


# (the last finished review's record or None, whether a round is unfinished), from one listing of the
# issue's rounds folder. module is review.py, resolved by the caller.
def readRounds(module, worktree, key):
    record, unfinished = module.round_state(module.rounds_folder(worktree, key))
    return record, unfinished is not None


def headOf(worktree):
    try:
        return git(worktree, "rev-parse", "HEAD").strip()
    except RuntimeError:
        return None


# What the rule needs from an issue's worktree: (record, HEAD, round unfinished). HEAD is read only
# when it can matter (review.py's could_be_orange: a finished review with unsettled findings and no
# round unfinished), else it is None, so for every other issue the tick spawns no git. The rounds
# folder is listed once and review.py resolved once. A worktree whose rounds can't be read (a
# permission error, a malformed file) has no review waiting.
def readReviewState(worktree, key):
    try:
        module = review()
        record, unfinished = readRounds(module, worktree, key)
        return record, headOf(worktree) if module.could_be_orange(record, unfinished) else None, unfinished
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


# The answer of review.py request: path, the request.md written or reopened (None when there is
# none), and note, what happened. round and token name the request for the plan window's review
# link; written is True only for a request this call wrote, the one withdrawRequest may take back.
RoundRequest = collections.namedtuple("RoundRequest", "path note token round written")


# Writes the next review round's request.md in the worktree (review.py request) and returns its
# RoundRequest. restart restarts an
# unfinished round for an older head in its own folder; without it the script refuses and says so.
# ifDue (`pr`'s call) writes and reopens nothing when no review is due, checked under the script's lock.
# ticket, a store issue's ticket file or a Jira issue's page, is where the request finds the PR's lenses.
def requestRound(worktree, key, restart=False, ifDue=False, ticket=None):
    arguments = ["request", key] + (["--restart"] if restart else []) + (["--if-due"] if ifDue else [])
    arguments += ["--ticket", ticket] if ticket else []
    outcome = json.loads(runReviewScript(worktree, arguments))
    return RoundRequest(outcome["path"], outcome["note"], outcome["token"], outcome["round"], outcome["written"])


# Takes back the request the token names, whose review never started, so the next `pr` writes the round
# again, and returns what the script did. The script does it under its lock and only while request.json
# still carries the token and no reviewer has claimed the round.
def withdrawRequest(worktree, key, token):
    return runReviewScript(worktree, ["withdraw", key, "--token", token]).strip()


# How long review.py may run before `pr` gives up on it: request calls gh and claude, and a stalled
# one must not hold up the rest of pr.
REVIEW_SCRIPT_TIMEOUT = 60


# review.py's output for arguments, run in the worktree; its refusal, or a run past the timeout,
# raises. A run killed at the timeout leaves its lock to a dead pid, which the next run clears.
def runReviewScript(worktree, arguments):
    try:
        answer = subprocess.run([sys.executable, REVIEW_SCRIPT, *arguments], cwd=worktree, capture_output=True, text=True,
                                timeout=REVIEW_SCRIPT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"review.py {arguments[0]} took longer than {REVIEW_SCRIPT_TIMEOUT} s and was stopped")
    if answer.returncode != 0:
        raise RuntimeError(answer.stderr.strip() or f"review.py {arguments[0]} exited with code {answer.returncode}")
    return answer.stdout
