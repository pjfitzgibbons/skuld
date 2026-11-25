# SK-<nnnn> issues: Jira Plan's own backlog (issues/README.md), stored as files in this repo instead
# of Jira. FileIssues implements the same surface lib/jira.py's Jira class does -- list(jql),
# issue(key), blockers(issue), statusTransitionId(key, name), transition(key, name),
# textComment(text), addComment(key, doc) -- returning dicts shaped the way callers (lib/cli.py,
# lib/start.py, lib/done.py) already read Jira's, so none of them need a SK-specific branch. Done's
# last step is the port's close(key), after its checkClose(key): a ticket closes, a Jira issue
# returns the note to close it by hand.
# isFileBacked(key) is the dispatch: lib/cli.py's jiraFrom(cfg, key) calls it to decide whether a
# key's commands go to Jira or to a FileIssues instance -- user decision 2026-11-05.
#
# Three decisions this implements (2026-11-05):
#  1. Every plan command (list/picker, view, start/open, ship, done) works on a SK key.
#  2. Writes go through lib/issue_update.py, the one writer of an issue file (the `issue-update`
#     command): Start's transition runs `status=in-progress`, then reads it back (transition()),
#     the same refuse-if-it-didn't-take guard Jira's own transition() uses. Every addComment call
#     is an `issue-update --log` line under the `## Log` heading. Done's close(key) sets
#     `status: done` and `closed:` the same way, after the merge line; Done never changes a Jira
#     issue's status (docs/architecture.md §7.3).
#  3. `plan list`/the picker merge Jira's JQL results with every SK file whose status is `open` or
#     `in-progress` (lib/cli.py's issueRows()).
import re
from pathlib import Path

from .issuekeys import KEY_RE, projectOf
from .jira import textComment as _adfTextComment

# The one project this module serves. projectOf("SK-0003") == PROJECT routes a key here instead of
# to Jira.
PROJECT = "SK"

# A key's number is zero-padded to this many digits, SK-0003, so a file listing of the store sorts
# in number order. A number past 9999 simply grows a digit.
KEY_DIGITS = 4

# The store's folders (docs/architecture.md §7.7): tickets live in the first two, read together.
# `plan init-store` makes all of them.
TICKET_FOLDERS = ("active", "archive")
STORE_FOLDERS = ("milestones", *TICKET_FOLDERS, "reviews", "queries")

# issues/README.md's status enum -> the Jira-shaped (status name, statusCategory key) pair a
# caller reads off issue["fields"]["status"]. done/wontfix both read as Jira's "done" category --
# neither is a status Start or Done need to tell apart from the other.
STATUS_MAP = {
    "open": ("To Do", "new"),
    "in-progress": ("In Progress", "indeterminate"),
    "done": ("Done", "done"),
    "wontfix": ("Won't Fix", "done"),
}

# The only write-back transition FileIssues supports: Start's "to In Progress" move. Nothing else
# in this tool transitions an issue (Done never does, Jira or file).
TARGET_TO_STATUS_VALUE = {"In Progress": "in-progress"}

# issues/README.md's type enum -> a Jira issuetype name that start.py's DEFAULT_TYPE_MAP (and any
# configured typeMap) already knows how to turn into a branch prefix: Bug -> fix, Task -> feat,
# anything else -> chore. bug -> Bug (fix); task and question -> Task (feat).
TYPE_NAME_MAP = {"bug": "Bug", "task": "Task", "question": "Task"}

# The statuses that end a ticket (issues/README.md's enum).
CLOSED_STATUSES = ("done", "wontfix")

# A fenced code block (CommonMark 0.31.2 §4.5): up to three spaces, then three or more backticks or
# tildes. A closing fence is the same character, at least as long, followed by nothing but whitespace.
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
BOX_RE = re.compile(r"^- \[([ xX])\]\s*(.*)$")

# Shared with lib/issue_update.py, which edits the front matter and Log this module reads.
FRONTMATTER_RE = re.compile(r"^---\n(.*?\n)---\n?(.*)$", re.DOTALL)
LOG_LINE_RE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2}):\s*(.*)$")


def isFileBacked(key):
    return projectOf(key) == PROJECT


# The key as the store names it: a file-backed key with its number zero-padded (SK-0003 -> SK-0003),
# so a key typed without the padding still finds its file. Any other key comes back unchanged.
def canonicalKey(key):
    if not (isFileBacked(key) and KEY_RE.match(key)):
        return key
    number = int(key.split("-", 1)[1])
    return f"{PROJECT}-{number:0{KEY_DIGITS}d}"


def _paragraphAdf(text):
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}] if text else []}],
    }


# An ADF comment body (what textComment() returns) back to one line of plain text, for the ##
# Log line. A plain string is accepted as-is, so a caller that already has text doesn't need to
# wrap it in ADF first.
def _flatten(doc):
    if isinstance(doc, str):
        return doc
    parts = []
    for node in (doc or {}).get("content") or []:
        text = "".join(c.get("text", "") for c in (node.get("content") or []) if c.get("type") == "text")
        if text:
            parts.append(text)
    return " ".join(parts)


# The fence a line opens ("````", "~~~"), or None. A backtick fence's info string has no backtick.
def openingFence(line):
    found = FENCE_RE.match(line.rstrip("\r\n"))
    if not found or ("`" in found[1] and "`" in found[2]):
        return None
    return found[1]


def closesFence(line, fence):
    found = FENCE_RE.match(line.rstrip("\r\n"))
    return bool(found) and found[1][0] == fence[0] and len(found[1]) >= len(fence) and not found[2].strip()


# (offset, line) for each line of text outside a code fence, fence lines included in neither. A fence
# left open runs to the end of the text.
def linesOutsideFences(text):
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        if fence is None and (opened := openingFence(line)):
            fence = opened
        elif fence is not None and closesFence(line, fence):
            fence = None
        elif fence is None:
            yield offset, line
        offset += len(line)


# The start and end of the first `## <name>` line in text that is outside a code fence, or None. A
# heading inside a fenced block, or in inline code, is quoted text, not a section.
def headingSpan(text, name):
    for offset, line in linesOutsideFences(text):
        if re.match(rf"## {name}\s*$", line):
            return offset, offset + len(line)
    return None


# The lines under the `## <name>` heading, up to the next `## ` heading, leaving out code fences.
def sectionLines(text, name):
    span = headingSpan(text, name)
    if not span:
        return []
    lines = []
    for _offset, line in linesOutsideFences(text[span[1] :]):
        if line.startswith("## "):
            break
        lines.append(line.rstrip("\n"))
    return lines


# The `## Done` boxes still unticked, each as its text.
def untickedBoxes(text):
    boxes = (BOX_RE.match(line.strip()) for line in sectionLines(text, "Done"))
    return [m.group(2).strip() for m in boxes if m and m.group(1) == " "]


# frontmatter dict, title, description body (everything between the title and ## Log, or end of
# file), and the ## Log entries (each { date, text }), from one issue file's full text.
def _parse(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise RuntimeError("issue file has no --- frontmatter block")
    front = {}
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = re.split(r"\s+#", value.strip(), maxsplit=1)[0].strip()
        front[key.strip()] = value

    rest = m.group(2)
    log_span = headingSpan(rest, "Log")
    before, log_text = (rest[: log_span[0]], rest[log_span[1] :]) if log_span else (rest, "")

    title = ""
    body_lines = []
    found_title = False
    for line in before.strip("\n").split("\n"):
        if not found_title and line.startswith("# "):
            title = line[2:].strip()
            found_title = True
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip("\n")

    return front, title, body, _logEntries(log_text)


# The ## Log entries (each { date, text }) in log_text. A wrapped entry's indented continuation
# lines join its text, so a long line reads back whole; an indented list item under an entry is its
# own line, not part of the entry.
def _logEntries(log_text):
    entries = []
    for line in log_text.strip("\n").split("\n"):
        lm = LOG_LINE_RE.match(line.strip())
        if lm:
            entries.append({"date": lm.group(1), "text": lm.group(2)})
        elif entries and line.startswith("  ") and line.strip() and not line.strip().startswith(("- ", "* ")):
            entries[-1]["text"] += " " + line.strip()
    return entries


# Makes the store's folders that are missing and returns the ones it made. Only creates: the
# store's root must already exist (it is the store's git checkout), and nothing in it is moved,
# rewritten or deleted, so a second run makes nothing.
def initStore(root):
    root = Path(root)
    if not root.is_dir():
        raise RuntimeError(f"the issue store {root} doesn't exist; git init it first, then run plan init-store")
    for name in STORE_FOLDERS:
        if (root / name).exists() and not (root / name).is_dir():
            raise RuntimeError(f"{root / name} is a file; the store needs a folder there")
    made = [name for name in STORE_FOLDERS if not (root / name).is_dir()]
    for name in made:
        (root / name).mkdir()
    return made


# The front matter a Done writes to close a ticket: nothing when it is closed with its date, else the
# closed status it has (or done); issue_update's closed rule fills the date in.
def closingFields(front):
    status = front.get("status")
    if status in CLOSED_STATUSES:
        return {} if front.get("closed") else {"status": status}
    return {"status": "done"}


def _keyFromFilename(name):
    m = re.match(rf"^({re.escape(PROJECT)}-\d{{{KEY_DIGITS},}})(?:-.*)?\.md$", name)
    return m.group(1) if m else None


# A list() candidate's place in the work order: issues with an ordinal first, by ordinal, then the
# rest; the issue number breaks ties.
def workOrder(candidate):
    ordinal, number = candidate[0], candidate[1]
    return (ordinal is None, ordinal or 0, number)


class FileIssues:
    # settings: { dir }, the store's checkout (lib/config.py's issueStore). There is no default:
    # the store is its own repo, outside skuld.
    def __init__(self, settings=None):
        settings = settings or {}
        d = settings.get("dir")
        if not d:
            raise RuntimeError("FileIssues needs the issue store's folder (issueStore)")
        self.dir = Path(d).resolve()
        self.settings = {"dir": str(self.dir)}

    # The ticket file for key, in the store's folder, its active/ or its archive/.
    def _path(self, key):
        from .issue_update import ticketPath  # issue_update imports this module, so not at the top

        return ticketPath(self.dir, canonicalKey(key))

    # Every SK file whose status is open or in-progress, in the shape lib/jira.py's Jira.list()
    # returns, so lib/cli.py's issueRows() can concatenate the two lists untouched (decision #3).
    # jql is accepted and ignored: a file has no JQL to run.
    #
    # Sort order (issues/README.md's `ordinal:` field, the work order): issues with an ordinal first,
    # by `ordinal:` ascending; the rest after them, by issue NUMBER ascending (numeric, so the
    # order holds even past SK-9999, where the padding runs out). Glob order is irrelevant
    # here -- every candidate is parsed first, then the whole batch is sorted by workOrder().
    def list(self, jql=None):
        from .issue_update import ticketFiles  # see _path()

        candidates = []
        for path in ticketFiles(self.dir):
            key = _keyFromFilename(path.name)
            if not key:
                continue
            front, title, _body, _entries = _parse(path.read_text(encoding="utf8"))
            status = front.get("status", "open")
            if status not in ("open", "in-progress"):
                continue
            number = int(key.split("-", 1)[1])
            ordinalValue = front.get("ordinal")
            ordinal = int(ordinalValue) if ordinalValue else None
            candidates.append((ordinal, number, key, title, status, front))
        candidates.sort(key=workOrder)

        rows = []
        for _ordinal, number, key, title, status, front in candidates:
            statusName, categoryKey = STATUS_MAP.get(status, STATUS_MAP["open"])
            issuetypeName = TYPE_NAME_MAP.get(front.get("type", "task"), "Task")
            rows.append(
                {
                    "id": key,
                    "subject": title,
                    "status": statusName,
                    "statusCategory": categoryKey,
                    "issueType": issuetypeName,
                    "project": PROJECT,
                    "parent": None,
                    # Optional interleaving anchor (issues/README.md's `after:` field, 2026-11-06):
                    # a Jira key this SK issue should sort right after in issueRows()'s merge
                    # (lib/cli.py). None when absent -- lib/cli.py's mergeRows() decides the
                    # fallback-to-end cases (missing, a SK key, or not in the current Jira list).
                    "after": front.get("after") or None,
                }
            )
        return rows

    # One issue, in the shape callers already read off Jira's issue(): fields.summary, .status.name/
    # .statusCategory.key, .issuetype.name, .parent (always None -- files carry no hierarchy),
    # .issuelinks (always [] -- see blockers()), .description (ADF, for plan view), .comment.comments
    # (ADF bodies, from the ## Log entries, so Done's "already posted this note" check still works).
    def issue(self, key):
        path = self._path(key)
        front, title, body, entries = _parse(path.read_text(encoding="utf8"))
        status = front.get("status", "open")
        statusName, categoryKey = STATUS_MAP.get(status, STATUS_MAP["open"])
        issuetypeName = TYPE_NAME_MAP.get(front.get("type", "task"), "Task")
        comments = [{"author": {"displayName": "plan"}, "body": _paragraphAdf(e["text"])} for e in entries]
        return {
            "key": canonicalKey(key),
            "fields": {
                "summary": title,
                "status": {"name": statusName, "statusCategory": {"key": categoryKey}},
                "issuetype": {"name": issuetypeName},
                "parent": None,
                "issuelinks": [],
                "description": _paragraphAdf(body) if body else None,
                "comment": {"comments": comments},
            },
        }

    # Files carry no issue links, so nothing ever blocks a SK issue.
    def blockers(self, issue):
        return []

    def statusTransitionId(self, key, targetStatusName):
        if targetStatusName not in TARGET_TO_STATUS_VALUE:
            raise RuntimeError(
                f'{key} has no transition to "{targetStatusName}"; file-backed issues only support "In Progress"'
            )
        current = self.issue(key)["fields"]["status"]["name"]
        if current != "To Do":
            raise RuntimeError(f'{key} has no transition to "{targetStatusName}" from "{current}"')
        return TARGET_TO_STATUS_VALUE[targetStatusName]

    # Sets frontmatter status, then reads the file back and refuses if it didn't take -- the same
    # guard Jira.transition() applies after its POST (decision #2).
    def transition(self, key, targetStatusName):
        from .issue_update import updateTicket  # see _path()

        value = self.statusTransitionId(key, targetStatusName)
        updateTicket(self.dir, canonicalKey(key), {"status": value})
        issue = self.issue(key)
        if issue["fields"]["status"]["name"] != targetStatusName:
            raise RuntimeError(
                f'{key}\'s file still shows "{issue["fields"]["status"]["name"]}", not "{targetStatusName}"; '
                f"check its frontmatter, then try again"
            )
        return issue

    # Same ADF shape Jira.textComment() builds -- shared so a caller that builds a comment before
    # knowing which backend it's headed to (none currently do, but the shape is the same either
    # way) gets identical output.
    def textComment(self, text):
        return _adfTextComment(text)

    # Appends a dated log line (decision #2). Accepts either an ADF doc (what textComment()
    # returns) or a plain string.
    def addComment(self, key, doc):
        from .issue_update import updateTicket  # see _path()

        updateTicket(self.dir, canonicalKey(key), {}, log=_flatten(doc))

    # What plan view/Start print instead of a Jira browse URL: the file itself.
    def browseUrl(self, key):
        return str(self._path(key))

    # Refuses, before Done changes anything, when close() would: a Done box unticked, or a field the
    # checker rejects.
    def checkClose(self, key):
        from .issue_update import closeProblems  # see _path()

        problems = closeProblems(self.dir, self._path(key).read_text(encoding="utf8"))
        if problems:
            raise RuntimeError(f"{canonicalKey(key)} can't be closed; nothing changed: " + "; ".join(problems))

    # Done's close of a ticket: status done, closed today, INDEX.md regenerated (a failed reindex warns). A ticket already
    # closed with its date set is left as it is. Returns what Done prints.
    def close(self, key):
        from .issue_update import updateTicket  # see _path()

        key = canonicalKey(key)
        front = _parse(self._path(key).read_text(encoding="utf8"))[0]
        fields = closingFields(front)
        if not fields:
            return f"{key} is already closed."
        updateTicket(self.dir, key, fields)
        closed = _parse(self._path(key).read_text(encoding="utf8"))[0]
        return f"Closed {key}: status {closed['status']}, closed {closed['closed']}."
