# A review round's data, as the local server's report and review pages read it (GUIDE §5, §7).
# Each round is a numbered folder, <worktree>/.tmp/<KEY>/rounds/<n>/, holding report.md (the
# round's report, Claude's prose) and review.json (its findings and per-file dispositions). Every
# function takes the folder it reads, so moving rounds to <store>/reviews/<KEY>/ (SK-0014) changes
# roundsFolder() alone. Stdlib only. Nothing here raises for a missing or malformed file: what's
# missing comes back as a note for the page to show.
import json
import os
import re
from dataclasses import dataclass, field

# Rounds count from 0: round 0 is the author's change, each later round answers a PR review.
ROUND_RE = re.compile(r"^(0|[1-9][0-9]*)$")
REPORT_FILE = "report.md"
REVIEW_FILE = "review.json"
PAGES = ("report", "review")


@dataclass
class Round:
    number: int
    report: str | None = None
    findings: list = field(default_factory=list)
    dispositions: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def roundsFolder(worktree, key):
    return os.path.join(worktree, ".tmp", key, "rounds")


# The server path of key's page: the latest round's when number is None, else round number's.
def pagePath(key, number, page):
    if number is None:
        return f"/issues/{key}/{page}"
    return f"/issues/{key}/rounds/{number}/{page}"


def isRoundNumber(text):
    return bool(ROUND_RE.match(str(text)))


# The rounds in folder, lowest first; none when the folder doesn't exist.
def roundNumbers(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(int(name) for name in names if isRoundNumber(name) and os.path.isdir(os.path.join(folder, name)))


def latestRound(folder):
    numbers = roundNumbers(folder)
    return numbers[-1] if numbers else None


# Round number's data from its folder, roundFolder = <rounds folder>/<number>.
def readRound(roundFolder, number):
    found = Round(number)
    found.report = readReport(roundFolder, found.notes)
    review = readReview(roundFolder, found.notes)
    found.findings = entries(review, "findings", found.notes)
    found.dispositions = entries(review, "dispositions", found.notes)
    return found


def readReport(roundFolder, notes):
    try:
        with open(os.path.join(roundFolder, REPORT_FILE), encoding="utf8") as f:
            return f.read()
    except FileNotFoundError:
        notes.append(f"{REPORT_FILE} is missing from this round.")
    except (OSError, UnicodeDecodeError) as error:
        notes.append(f"{REPORT_FILE} can't be read: {error}")
    return None


# review.json's object, or {} with a note when it is missing, unreadable or not a JSON object.
def readReview(roundFolder, notes):
    try:
        with open(os.path.join(roundFolder, REVIEW_FILE), encoding="utf8") as f:
            review = json.load(f)
    except FileNotFoundError:
        notes.append(f"{REVIEW_FILE} is missing from this round.")
        return {}
    except (OSError, ValueError) as error:
        notes.append(f"{REVIEW_FILE} can't be read: {error}")
        return {}
    if not isinstance(review, dict):
        notes.append(f"{REVIEW_FILE} isn't a JSON object.")
        return {}
    return review


# review[name]'s objects; anything else in the list is left out, with a note.
def entries(review, name, notes):
    listed = review.get(name, [])
    if not isinstance(listed, list):
        notes.append(f'{REVIEW_FILE}\'s "{name}" isn\'t a list.')
        return []
    kept = [entry for entry in listed if isinstance(entry, dict)]
    if len(kept) < len(listed):
        notes.append(f'{REVIEW_FILE}\'s "{name}" has {len(listed) - len(kept)} entries that aren\'t objects, left out.')
    return kept
