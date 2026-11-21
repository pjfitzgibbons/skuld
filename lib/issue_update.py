# The one way a store ticket's front matter and ## Log change: `issue_update <KEY> [field=value ...]
# [--log "text"]` (bin/issue_update), and Done's close of a ticket (lib/fileissues.py's close()).
# Every field is checked before any is written, so a bad one changes nothing. Every run regenerates
# the store's INDEX.md with the store's own scripts/index.py, the only writer of that file, even
# when only a log line was added. Nothing here commits or pushes: the store's watcher commits
# (docs/architecture.md §7.4, §7.7).
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from . import config as config_mod
from .fileissues import STATUS_MAP, FileIssues, appendLog, canonicalKey, isFileBacked, setFrontField

# The front-matter fields issues/README.md defines.
FIELDS = ("status", "type", "guide", "opened", "closed", "ordinal", "blocked_by", "after", "pr", "milestone")

# A ## Log line wraps here, continuation lines indented two spaces, as the store's tickets are.
LOG_WIDTH = 100

INDEX_SCRIPT = Path("scripts") / "index.py"

USAGE = 'usage: issue_update <KEY> [field=value ...] [--log "text"]'

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# argv as (key, { field: value }, log text or None). A bare word with no "=" is refused.
def parseArgs(argv):
    if not argv:
        raise RuntimeError(USAGE)
    key, rest = argv[0], list(argv[1:])
    fields = {}
    log = None
    while rest:
        arg = rest.pop(0)
        if arg == "--log":
            if not rest:
                raise RuntimeError(f"--log needs the text to add; {USAGE}")
            log = rest.pop(0)
        elif "=" in arg:
            name, _, value = arg.partition("=")
            fields[name.strip()] = value.strip()
        else:
            raise RuntimeError(f'"{arg}" is not field=value or --log; {USAGE}')
    return key, fields, log


def milestones(store):
    return sorted(p.stem for p in (Path(store) / "milestones").glob("*.md"))


# Why field=value is refused, or None when the store takes it.
def fieldProblem(store, field, value):
    if field not in FIELDS:
        return f'"{field}" is not a front-matter field; the fields are {", ".join(FIELDS)}'
    if field == "status" and value not in STATUS_MAP:
        return f'"{value}" is not a status; the statuses are {", ".join(STATUS_MAP)}'
    if field == "milestone" and value and value not in milestones(store):
        return f'"{value}" has no file in {Path(store) / "milestones"}; the milestones are {", ".join(milestones(store))}'
    if field == "ordinal" and value and not value.isdigit():
        return f'"{value}" is not a whole number'
    if field in ("opened", "closed") and value and not DATE_RE.match(value):
        return f'"{value}" is not a date like 2026-11-12'
    return None


# Refuses, naming each field, when any of fields is bad.
def checkFields(store, fields):
    problems = []
    for field, value in fields.items():
        problem = fieldProblem(store, field, value)
        if problem:
            problems.append(f"{field}: {problem}")
    if problems:
        raise RuntimeError("nothing changed; " + "; ".join(problems))


def checkStore(store):
    if not (Path(store) / INDEX_SCRIPT).is_file():
        raise RuntimeError(f"{store} has no {INDEX_SCRIPT}, so INDEX.md can't be regenerated")


# status=done with no closed= closes the ticket today.
def withClosedDate(fields):
    if fields.get("status") == "done" and "closed" not in fields:
        return {**fields, "closed": date.today().isoformat()}
    return fields


def withFields(text, fields):
    for field, value in fields.items():
        text = setFrontField(text, field, value)
    return text


def reindex(store):
    result = subprocess.run(
        [sys.executable, str(Path(store) / INDEX_SCRIPT)], cwd=store, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"the ticket changed, but {INDEX_SCRIPT} failed; run it, then check INDEX.md:\n{result.stderr}")


# Changes the ticket for key in store: its fields, then a log line, then INDEX.md. Everything is
# checked and the new text built before the ticket is written, once. Returns the ticket's path.
def updateTicket(store, key, fields, log=None):
    if not isFileBacked(key):
        raise RuntimeError(f"{key} is not a store ticket; issue_update writes SK tickets only")
    checkFields(store, fields)
    checkStore(store)
    path = FileIssues({"dir": store}).ticketPath(key)
    text = withFields(path.read_text(encoding="utf8"), withClosedDate(fields))
    if log:
        text = appendLog(text, log, LOG_WIDTH)
    path.write_text(text, encoding="utf8")
    reindex(store)
    return path


# bin/issue_update: prints the ticket's path.
def main(argv=None):
    key, fields, log = parseArgs(sys.argv[1:] if argv is None else argv)
    store = config_mod.issueStoreFrom(config_mod.load())
    print(updateTicket(store, canonicalKey(key), fields, log))
    return 0
