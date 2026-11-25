# The only code that writes a store ticket file: `issue-create "<title>" [field=value ...]` and
# `issue-update <KEY> [field=value ...] [--log "text"]` (bin/issue-create, bin/issue-update), and
# FileIssues' transition() and addComment() (lib/fileissues.py), which call updateTicket(). `plan new`
# and `plan set` (lib/cli.py) call createTicket() and updateTicket() too.
# docs/architecture.md §7.7 states the rules this implements:
#  - the new text is written whole to a temporary file beside the ticket and renamed over it, so a
#    failed or interrupted write leaves the ticket as it was;
#  - every field is checked before anything is written, and a refusal names the field or key;
#  - a Jira key is refused; `ticket_id` is written first by issue-create, and never added by
#    issue-update to a file that has none;
#  - each command prints the path it wrote, then runs `store-index` on the store. It commits
#    nothing: the store's watcher does.
import fcntl
import os
import re
import shutil
import subprocess
import sys
import tempfile
from string import Template
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from . import config as config_mod
from .issuekeys import KEY_RE
from .fileissues import (
    _parse,
    FRONTMATTER_RE,
    KEY_DIGITS,
    LOG_HEADING_RE,
    PROJECT,
    STATUS_MAP,
    TICKET_FOLDERS,
    _keyFromFilename,
    canonicalKey,
    isFileBacked,
)

# issues/README.md's front matter, less `ticket_id`, which only issue-create writes.
# issues/README.md's `type` values.
TYPES = ("bug", "task", "question")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FIELDS = (
    "status",
    "type",
    "guide",
    "opened",
    "closed",
    "milestone",
    "ordinal",
    "blocked_by",
    "after",
    "parent",
    "host",
    "points",
    "pr",
)


# issue-create gives a new ticket the next multiple of this above the highest ordinal (README: steps
# of 100, so a ticket can take a value in a gap).
ORDINAL_STEP = 100

# What issue-create fills in: ticket_id, title, opened, status: open, the outline with every lens listed.
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "issue.md"

UPDATE_USAGE = 'usage: issue-update <KEY> [field=value ...] [blocked_by+=KEY ...] [--log "text"]'
CREATE_USAGE = 'usage: issue-create "<title>" [field=value ...]'

LOG_FLAG = "--log"


# --- finding a ticket -------------------------------------------------------------------------


# Files named like a ticket: a stray SK-notes.md has no key and is not one.
# A ticket at the store's top level is refused: it belongs in active/ (docs/architecture.md §7.7).
def ticketFiles(store):
    strays = sorted(p for p in Path(store).glob(f"{PROJECT}-*.md") if _keyFromFilename(p.name))
    if strays:
        raise RuntimeError(f"{strays[0]} is at the store's top level; move it to {Path(store) / 'active'}/")
    found = [p for folder in TICKET_FOLDERS for p in (Path(store) / folder).glob(f"{PROJECT}-*.md")]
    return [p for p in found if _keyFromFilename(p.name)]


def ticketPath(store, key):
    matches = sorted(p for p in ticketFiles(store) if _keyFromFilename(p.name) == key)
    if not matches:
        raise RuntimeError(f"no ticket file for {key} in {store}")
    if len(matches) > 1:
        raise RuntimeError(f"more than one ticket file matches {key}: {', '.join(p.name for p in matches)}")
    return matches[0]


def refuseNonStoreKey(key, command):
    if not isFileBacked(key):
        raise RuntimeError(f"key {key}: {command} writes {PROJECT} tickets only, not Jira issues")


# --- checking a field -------------------------------------------------------------------------


# Why field=value is refused, or None when the ticket takes it.
def fieldProblem(field, value):
    if field not in FIELDS:
        return f'field "{field}" is not a front-matter field; the fields are {", ".join(FIELDS)}'
    if re.search(r"[\r\n]", value):
        return f'field "{field}": the value is more than one line'
    # The reader (fileissues._parse) cuts a value at whitespace then "#" as a comment, so the value
    # would not read back as written.
    if re.search(r"\s#", value):
        return f'field "{field}": the value has whitespace before "#", which a reader takes as a comment'
    return valueProblem(field, value)


def isIsoDate(value):
    try:
        return bool(DATE_RE.match(value)) and bool(date.fromisoformat(value))
    except ValueError:
        return False


# The per-field rules; an empty value clears the field and is always taken.
def valueProblem(field, value):
    if not value:
        return None
    if field == "status" and value not in STATUS_MAP:
        return f'field "status": "{value}" is not a status; the statuses are {", ".join(STATUS_MAP)}'
    if field == "type" and value not in TYPES:
        return f'field "type": "{value}" is not a type; the types are {", ".join(TYPES)}'
    if field == "ordinal" and not value.isdigit():
        return f'field "ordinal": "{value}" is not a whole number'
    if field in ("opened", "closed") and not isIsoDate(value):
        return f'field "{field}": "{value}" is not a date like 2026-11-23'
    if field in KEY_LIST_FIELDS:
        return keyListProblem(field, value)
    return None


# `blocked_by` is a comma-separated scalar, SK-0002, SK-0003; `parent` is one key. Brackets, quotes,
# semicolons, lower case, an empty entry or a repeated key are refused.
KEY_LIST_FIELDS = ("parent", "blocked_by")


def keyListProblem(field, value):
    keys = [key.strip() for key in value.split(",")]
    bad = next((key for key in keys if not KEY_RE.match(key)), None)
    if bad is not None:
        return f'field "{field}": "{bad}" is not a ticket key; write keys like SK-0002, SK-0003'
    if len({canonicalKey(key) for key in keys}) != len(keys):
        return f'field "{field}": "{value}" lists a key twice'
    if field == "parent" and len(keys) > 1:
        return f'field "parent": "{value}" is more than one key'
    return None


# The value as written: padded keys joined by ", ", whatever spelling and spacing the caller used.
def writtenValue(field, value):
    if field in KEY_LIST_FIELDS and value:
        return ", ".join(canonicalKey(key.strip()) for key in value.split(","))
    return value


def refuseBadFields(fields):
    problems = [why for field, value in fields.items() if (why := fieldProblem(field, value))]
    if problems:
        raise RuntimeError("nothing written; " + "; ".join(problems))


def refuseBadLog(log):
    if log is not None and (not log.strip() or re.search(r"[\r\n]", log)):
        raise RuntimeError(f"{LOG_FLAG}: the text must be one non-empty line")


# --- editing the text -------------------------------------------------------------------------


def fieldLine(field, value):
    return f"{field}: {value}".rstrip()


# `field: value` in text's front matter, the rest of the file untouched. A field the file lacks is
# added at the end of the front matter.
def setField(text, field, value):
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise RuntimeError("the ticket has no --- front matter block")
    block = match.group(1)
    pattern = re.compile(rf"(?m)^{re.escape(field)}:.*$")
    if pattern.search(block):
        newBlock = pattern.sub(lambda _: fieldLine(field, value), block, count=1)
    else:
        newBlock = block + fieldLine(field, value) + "\n"
    start, end = match.span(1)
    return text[:start] + newBlock + text[end:]


# A dated line at the end of the file, under a ## Log heading added once if the file has none.
def appendLog(text, line):
    entry = f"- {date.today().isoformat()}: {line}"
    if LOG_HEADING_RE.search(text):
        return text.rstrip("\n") + "\n" + entry + "\n"
    return text.rstrip("\n") + "\n\n## Log\n" + entry + "\n"


# --- writing the file -------------------------------------------------------------------------


# The whole text goes to a hidden temporary file in the ticket's folder, then replaces the ticket in
# one rename. Any failure removes the temporary file and leaves the ticket as it was.
def atomicWrite(path, text):
    handle, tmpName = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmpName)
    try:
        with os.fdopen(handle, "w", encoding="utf8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# Writes a ticket that must not exist yet: a rename that fails when the name is taken.
def atomicCreate(path, text):
    handle, tmpName = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmpName)
    try:
        with os.fdopen(handle, "w", encoding="utf8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o644)
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# INDEX.md follows the write. The ticket is already written, so a store-index that is missing or
# fails is said on stderr, with the command to run, and does not fail the command.
def reindexStore(store):
    command = shutil.which("store-index")
    if not command:
        print(f"INDEX.md not regenerated: store-index is not on PATH; run: store-index {store}", file=sys.stderr)
        return
    try:
        result = subprocess.run([command, str(store)], capture_output=True, text=True)
    except OSError as error:
        print(f"INDEX.md not regenerated: store-index could not start ({error}); run: store-index {store}", file=sys.stderr)
        return
    if result.returncode:
        print(f"INDEX.md not regenerated: {result.stderr.strip()}; run: store-index {store}", file=sys.stderr)


# --- issue-update -----------------------------------------------------------------------------


# --- checks that read the store ---------------------------------------------------------------


def milestoneIds(store):
    return sorted(p.stem for p in (Path(store) / "milestones").glob("*.md"))


def keyList(value):
    return [key.strip() for key in value.split(",") if key.strip()]


# The ticket's blockers as written; a value that is not the comma form refuses, naming blocked_by.
def currentBlockers(value):
    problem = keyListProblem("blocked_by", value) if value else None
    if problem:
        raise RuntimeError(f"this ticket's {problem}")
    return keyList(value)


def storeKeys(store):
    return {_keyFromFilename(p.name) for p in ticketFiles(store)}


# A key another tracker owns can't be looked up offline, so only a store key must have a ticket.
def missingKeys(store, keys):
    return [key for key in keys if isFileBacked(key) and canonicalKey(key) not in storeKeys(store)]


# Why a value is refused by what the store holds, or None: a milestone names a file in milestones/,
# and parent and blocked_by name tickets that exist.
def storeProblem(store, field, value):
    if field == "milestone" and value and value not in milestoneIds(store):
        return f'field "milestone": "{value}" has no file in {Path(store) / "milestones"}'
    if field in ("parent", "blocked_by"):
        missing = missingKeys(store, keyList(value))
        if missing:
            return f'field "{field}": {", ".join(missing)} is not a ticket in {store}'
    return None


def refuseBadStoreValues(store, fields):
    problems = [why for field, value in fields.items() if (why := storeProblem(store, field, value))]
    if problems:
        raise RuntimeError("nothing written; " + "; ".join(problems))


# `blocked_by+=KEY` and `blocked_by-=KEY`: one blocker added or removed, as ("blocked_by", "+", KEY).
# Returns the new blocked_by value, or refuses naming the key or field.
def editedBlockers(store, ticketKey, current, field, op, value):
    if field != "blocked_by":
        raise RuntimeError(f'field "{field}": {op}= works on blocked_by only')
    if not KEY_RE.match(value):
        raise RuntimeError(f'field "blocked_by": "{value}" is not a ticket key')
    value = canonicalKey(value)
    blockers = [canonicalKey(key) for key in currentBlockers(current)]
    if op == "+":
        blockers = addedBlocker(store, ticketKey, blockers, value)
    else:
        if value not in blockers:
            raise RuntimeError(f'field "blocked_by": {value} is not one of this ticket\'s blockers')
        blockers = [key for key in blockers if key != value]
    return ", ".join(blockers)


def addedBlocker(store, ticketKey, blockers, value):
    if value == ticketKey:
        raise RuntimeError(f'field "blocked_by": {value} cannot block itself')
    if value in blockers:
        raise RuntimeError(f'field "blocked_by": {value} is already a blocker')
    storeProblemFound = storeProblem(store, "blocked_by", value)
    if storeProblemFound:
        raise RuntimeError(storeProblemFound)
    return blockers + [value]


# Sets fields, adds or removes blockers (edits) and appends a log line on key's ticket in store,
# checking everything first. Returns the ticket's path.
def updateTicket(store, key, fields, log=None, reindex=None, edits=()):
    refuseNonStoreKey(key, "issue-update")
    refuseBadFields(fields)
    refuseBadStoreValues(store, fields)
    refuseBadLog(log)
    ticketKey = canonicalKey(key)
    path = ticketPath(store, ticketKey)
    text = path.read_text(encoding="utf8")
    for field, value in fields.items():
        text = setField(text, field, writtenValue(field, value))
    for field, op, value in edits:
        current = _parse(text)[0].get("blocked_by", "")
        text = setField(text, field, editedBlockers(store, ticketKey, current, field, op, value))
    if log is not None:
        text = appendLog(text, log)
    atomicWrite(path, text)
    (reindex or reindexStore)(store)
    return path


# --- issue-create -----------------------------------------------------------------------------


def numberOf(path):
    return int(_keyFromFilename(path.name).split("-", 1)[1])


# The ordinal in the ticket's front matter; 0 when it has none or the file doesn't parse.
def ordinalOf(path):
    try:
        value = _parse(path.read_text(encoding="utf8"))[0].get("ordinal", "")
    except RuntimeError:
        return 0
    return int(value) if value.isdigit() else 0


def nextKey(store):
    highest = max((numberOf(p) for p in ticketFiles(store)), default=0)
    return f"{PROJECT}-{highest + 1:0{KEY_DIGITS}d}"


def nextOrdinal(store):
    highest = max((ordinalOf(p) for p in ticketFiles(store)), default=0)
    return (highest // ORDINAL_STEP + 1) * ORDINAL_STEP


# One issue-create at a time picks a key and writes its file: an advisory lock on the store's
# folder, which leaves no file behind for git to see.
@contextmanager
def storeLocked(store):
    fd = os.open(store, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def slugOf(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "-".join(words[:6]) or "ticket"


# The template's own fields; any other field the caller gave (after, parent, host, points) is added
# after the template is filled.
def templateValues(key, title, ordinal, given):
    today = date.today().isoformat()
    values = {
        "ticket_id": key,
        "status": "open",
        "type": "task",
        "guide": "",
        "opened": today,
        "closed": "",
        "milestone": "",
        "ordinal": str(ordinal),
        "blocked_by": "",
        "pr": "",
    }
    values.update({field: writtenValue(field, value) for field, value in given.items() if field in values})
    return {**values, "title": title.strip(), "filed": today}


def ticketText(key, title, ordinal, given):
    filled = Template(TEMPLATE.read_text(encoding="utf8")).substitute(templateValues(key, title, ordinal, given))
    text = re.sub(r"(?m)[ \t]+$", "", filled)
    for field, value in given.items():
        if field not in templateValues(key, title, ordinal, {}):
            text = setField(text, field, writtenValue(field, value))
    return text


# Writes a new ticket in store and returns its path: the next free key and ordinal, `ticket_id`
# first, the outline and a Log with `Filed.`, from templates/issue.md.
def createTicket(store, title, fields=None, reindex=None):
    fields = fields or {}
    if not title.strip() or re.search(r"[\r\n]", title):
        raise RuntimeError("title: the title must be one non-empty line")
    refuseBadFields(fields)
    refuseBadStoreValues(store, fields)
    with storeLocked(store):
        key = nextKey(store)
        folder = Path(store) / "active"
        folder.mkdir(exist_ok=True)  # a fresh clone has no empty active/; the store's root is not a ticket folder
        path = folder / f"{key}-{slugOf(title)}.md"
        text = ticketText(key, title, nextOrdinal(store), fields)
        atomicCreate(path, text)
    (reindex or reindexStore)(store)
    return path


# --- the command lines ------------------------------------------------------------------------


EDIT_RE = re.compile(r"^(\w+)([+-])=(.*)$")

# The house outline's checker, the same script the jira-issues skill runs.
OUTLINE_PY = Path(__file__).resolve().parent.parent / "skills" / "jira-issues" / "scripts" / "outline.py"


# (first word, { field: value }, log text or None, [(field, "+" or "-", value)]) from argv: the key
# or title, then field=value pairs, field+=value and field-=value edits, and --log. Any other word is
# refused.
def parseArgs(argv, usage):
    if not argv:
        raise RuntimeError(usage)
    first, rest, fields, log, edits = argv[0], list(argv[1:]), {}, None, []
    while rest:
        arg = rest.pop(0)
        edit = EDIT_RE.match(arg)
        if arg == LOG_FLAG:
            if not rest:
                raise RuntimeError(f"{LOG_FLAG} needs the text to add; {usage}")
            log = rest.pop(0)
        elif arg.startswith(LOG_FLAG + "="):
            log = arg[len(LOG_FLAG) + 1 :]
        elif edit:
            edits.append((edit.group(1), edit.group(2), edit.group(3).strip()))
        elif "=" in arg:
            name, _, value = arg.partition("=")
            fields[name.strip()] = value.strip()
        else:
            raise RuntimeError(f'"{arg}" is not field=value or {LOG_FLAG}; {usage}')
    return first, fields, log, edits


# What outline.py finds wrong with the ticket at path: one line per finding, none when it conforms.
def outlineProblems(path):
    path = Path(path).resolve()  # the checker runs in the ticket's folder, so a relative path would miss
    result = subprocess.run([sys.executable, str(OUTLINE_PY), str(path)], capture_output=True, text=True, cwd=path.parent)
    if result.returncode == 0:
        return []
    findings = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return findings or [f"the outline check failed: {lastLine(result.stderr)}"]


def lastLine(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


def storeFromConfig():
    return config_mod.issueStoreFrom(config_mod.load())


def updateMain(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["-h"], ["--help"]):
        print(UPDATE_USAGE)
        return 0
    key, fields, log, edits = parseArgs(argv, UPDATE_USAGE)
    if not (fields or edits) and log is None:
        raise RuntimeError(UPDATE_USAGE)
    print(updateTicket(storeFromConfig(), key, fields, log, edits=edits))
    return 0


def createMain(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["-h"], ["--help"]):
        print(CREATE_USAGE)
        return 0
    title, fields, log, edits = parseArgs(argv, CREATE_USAGE)
    if log is not None or edits:
        raise RuntimeError(CREATE_USAGE)
    print(createTicket(storeFromConfig(), title, fields))
    return 0
