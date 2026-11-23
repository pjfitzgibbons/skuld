# The one way a store ticket's front matter and ## Log change: `issue_update <KEY> [field=value ...]
# [--log "text"]` (bin/issue_update), and Done's close of a ticket (lib/fileissues.py's close()).
# Every field is checked before any is written, so a bad one changes nothing. Every run regenerates
# the store's INDEX.md with lib/store_index.py, the only writer of that file, even when only a log
# line was added. Nothing here commits or pushes: the store's watcher commits
# (docs/architecture.md §7.4, §7.7).
#
# ticketProblems() is the checker both paths share: the ticket issue_update would write, and the
# closed ticket Done would write, pass the same rules (docs/architecture.md §7.7).
import re
import sys
from datetime import date
from pathlib import Path

from . import config as config_mod
from . import store_index
from .fileissues import (
    CLOSED_STATUSES,
    STATUS_MAP,
    FileIssues,
    _keyFromFilename,
    _parse,
    appendLog,
    canonicalKey,
    closingFields,
    isFileBacked,
    setFrontField,
    untickedBoxes,
)

# The front-matter fields docs/architecture.md §7.7 lists, and issues/README.md's `guide` and `pr`.
FIELDS = (
    "status",
    "type",
    "guide",
    "opened",
    "closed",
    "ordinal",
    "blocked_by",
    "after",
    "pr",
    "milestone",
    "parent",
    "host",
    "points",
)

FIBONACCI = ("1", "2", "3", "5", "8", "13")

USAGE = 'usage: issue_update <KEY> [field=value ...] [--log "text"]'

HELP = f"""{USAGE}

Changes a store ticket's front matter and ## Log, checked first: one bad field changes nothing.
Regenerates INDEX.md; commits nothing; prints the ticket's path, then what it wrote.

fields:   {", ".join(FIELDS)}
statuses: {", ".join(STATUS_MAP)}
points:   {", ".join(FIBONACCI)}
status=done or wontfix without closed= sets closed to today; any other status clears it.
--log "text" adds "- <today>: text" to ## Log, wrapped to 100 columns."""

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

LOG_FLAG = "--log"


# argv as (key, { field: value }, log text or None). A bare word with no "=" is refused.
def parseArgs(argv):
    if not argv:
        raise RuntimeError(USAGE)
    key, rest = argv[0], list(argv[1:])
    fields = {}
    log = None
    while rest:
        arg = rest.pop(0)
        if arg == LOG_FLAG:
            log = logText(rest.pop(0) if rest else "")
        elif arg.startswith(LOG_FLAG + "="):
            log = logText(arg[len(LOG_FLAG) + 1 :])
        elif "=" in arg:
            name, _, value = arg.partition("=")
            fields[name.strip()] = value.strip()
        else:
            raise RuntimeError(f'"{arg}" is not field=value or {LOG_FLAG}; {USAGE}')
    return key, fields, log


def logText(text):
    if not text.strip():
        raise RuntimeError(f"{LOG_FLAG} needs the text to add; {USAGE}")
    return text


# Every key of a ticket in the store, its archive/ included.
def ticketKeys(store):
    names = [p.name for folder in ("", "archive", "active") for p in (Path(store) / folder).glob("SK-*.md")]
    return {_keyFromFilename(name) for name in names}


def milestones(store):
    return sorted(p.stem for p in (Path(store) / "milestones").glob("*.md"))


def milestoneStatus(store, milestone):
    return _parse((Path(store) / "milestones" / f"{milestone}.md").read_text())[0].get("status")


# An open ticket is never in a closed milestone (architecture §4.1).
def closedMilestoneProblem(store, front):
    milestone = front.get("milestone")
    if not milestone or milestone not in milestones(store) or front.get("status") in CLOSED_STATUSES:
        return None
    if milestoneStatus(store, milestone) == "closed":
        return f'milestone: "{milestone}" is closed, and an open ticket can\'t be in it'
    return None


def keyList(value):
    return [key.strip() for key in value.split(",") if key.strip()]


def isIsoDate(value):
    try:
        return bool(DATE_RE.match(value)) and bool(date.fromisoformat(value))
    except ValueError:
        return False


# Why field=value is refused, or None when the store takes it.
def fieldProblem(store, field, value):
    if field not in FIELDS:
        return f'"{field}" is not a front-matter field; the fields are {", ".join(FIELDS)}'
    if not value:
        return None
    if field == "status" and value not in STATUS_MAP:
        return f'"{value}" is not a status; the statuses are {", ".join(STATUS_MAP)}'
    if field == "points" and value not in FIBONACCI:
        return f'"{value}" is not a story-point number; the numbers are {", ".join(FIBONACCI)}'
    if field == "ordinal" and not value.isdigit():
        return f'"{value}" is not a whole number'
    if field in ("opened", "closed") and not isIsoDate(value):
        return f'"{value}" is not a date like 2026-11-12'
    if field == "milestone" and value not in milestones(store):
        return f'"{value}" has no file in {Path(store) / "milestones"}; the milestones are {", ".join(milestones(store))}'
    if field in ("parent", "blocked_by"):
        missing = [key for key in keyList(value) if key not in ticketKeys(store)]
        if missing:
            return f'{", ".join(missing)} is not a ticket in {store}'
    return None


# `closed` is set exactly when the status is closed.
def closedProblem(front):
    if "status" not in front:
        return None
    closing = front.get("status") in CLOSED_STATUSES
    if closing and not front.get("closed"):
        return "closed: a closed ticket needs the date it closed"
    if not closing and front.get("closed"):
        return f'closed: an open ticket ("{front.get("status")}") has no closing date'
    return None


# The problems with a ticket's front matter, each as "field: why": the checker both issue_update and
# Done's close run on the ticket they are about to write.
def ticketProblems(store, front):
    found = [(field, fieldProblem(store, field, value)) for field, value in front.items()]
    whole = [closedProblem(front), closedMilestoneProblem(store, front)]
    return [f"{field}: {why}" for field, why in found if why] + [why for why in whole if why]


# What stops Done closing the ticket in text, each as a sentence; none when it can close, or is
# already closed. All of them are found before Done changes anything.
def closeProblems(store, text):
    front = _parse(text)[0]
    fields = closingFields(front)
    if not fields:
        return []
    problems = [f'the Done box "{box}" is not ticked' for box in untickedBoxes(text)]
    problems += [f"the closed ticket fails the checker, {p}" for p in ticketProblems(store, {**front, **withClosed(fields, front)})]
    return problems


# `closed` follows the status: a closing status with no date gets today's, any other status clears it.
def withClosed(fields, front):
    status = fields.get("status")
    if status is None:
        return fields
    if status not in CLOSED_STATUSES:
        return {**fields, "closed": ""}
    if fields.get("closed") or front.get("closed"):
        return fields
    return {**fields, "closed": date.today().isoformat()}


def withFields(text, fields):
    for field, value in fields.items():
        text = setFrontField(text, field, value)
    return text


def reindex(store, changed):
    try:
        store_index.writeIndex(store)
    except Exception as error:
        raise RuntimeError(
            f"the ticket changed ({changed}), but INDEX.md could not be regenerated: "
            f"{type(error).__name__}: {error}; fix that, then run store-index {store}"
        ) from error


# Changes the ticket for key in store: its fields, then a log line, then INDEX.md. Everything is
# checked and the new text built before the ticket is written, once. Returns the ticket's path and
# the fields written, `closed` included when it followed the status.
def updateTicket(store, key, fields, log=None):
    if not isFileBacked(key):
        raise RuntimeError(f"{key} is not a store ticket; issue_update writes SK tickets only")
    path = FileIssues({"dir": store}).ticketPath(key)
    text = path.read_text(encoding="utf8")
    written = withClosed(fields, _parse(text)[0])
    problems = [f"{field}: is more than one line" for field, value in fields.items() if re.search(r"[\r\n]", value)]
    problems += ticketProblems(store, _parse(withFields(text, written))[0])
    if problems:
        raise RuntimeError("nothing changed; " + "; ".join(problems))
    text = withFields(text, written)
    if log:
        text = appendLog(text, log)
    path.write_text(text, encoding="utf8")
    reindex(store, ", ".join([*written, *(["a log line"] if log else [])]))
    return path, written


# bin/issue_update: prints the ticket's path, then each field written and whether a log line was.
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["-h"], ["--help"]):
        print(HELP)
        return 0
    key, fields, log = parseArgs(argv)
    store = config_mod.issueStoreFrom(config_mod.load())
    path, written = updateTicket(store, canonicalKey(key), fields, log)
    print(path)
    for field, value in written.items():
        print(f"{field}: {value}")
    if log:
        print("log: added")
    return 0
