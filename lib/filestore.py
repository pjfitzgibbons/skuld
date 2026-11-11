# SK-<nnnn> issues: skuld's own backlog (issues/README.md), kept as files in the issue store.
# FileStore is the IssueStore port's file adapter (lib/issuestore.py): issue(key) returns
# lib/issue.py's Issue, as every adapter does, so no caller needs a SK-specific branch.
# lib/issuestore.py's storeFor(cfg, key) picks this adapter for PROJECT's keys.
#
# Three decisions this implements (2026-11-05):
#  1. Every plan command (list/picker, view, start/open, ship, done) works on a SK key.
#  2. Writes edit the file: Start's transition sets frontmatter `status: in-progress`, then reads
#     it back (transition()), refusing if it didn't take. Every addComment call appends a dated
#     `- YYYY-MM-DD: <text>` line under a `## Log` heading at the end of the file (created if
#     absent). Done never changes status (docs/architecture.md §7.3); the human sets
#     `status: done` by hand.
#  3. Next up's SK set is every file whose status is `open` or `in-progress` (list()).
import re
from datetime import date
from pathlib import Path

from .issue import DONE, IN_PROGRESS, KEY_RE, NEW, Comment, Issue, IssueNotFound, projectOf

# The one project this module serves: storeFor() routes a key whose projectOf() is PROJECT here.
PROJECT = "SK"

# A key's number is zero-padded to this many digits, SK-0003, so a file listing of the store sorts
# in number order. A number past 9999 simply grows a digit.
KEY_DIGITS = 4

# issues/README.md's status enum -> its category. An Issue's status is the file's own value.
CATEGORIES = {"open": NEW, "in-progress": IN_PROGRESS, "done": DONE, "wontfix": DONE}

# The status value each category a command moves an issue to writes. Start's move is the only one;
# Done never changes status.
TARGET_STATUS = {IN_PROGRESS: "in-progress"}

# A ticket's type when its front matter names none.
DEFAULT_TYPE = "task"

FRONTMATTER_RE = re.compile(r"^---\n(.*?\n)---\n?(.*)$", re.DOTALL)
LOG_HEADING_RE = re.compile(r"(?m)^## Log\s*$")
LOG_LINE_RE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2}):\s*(.*)$")


# A ticket's status as its front matter sets it; open when it sets none or one the enum lacks.
def statusOf(front):
    status = front.get("status", "open")
    return status if status in CATEGORIES else "open"


def isFileBacked(key):
    return projectOf(key) == PROJECT


# The key as the store names it: a file-backed key with its number zero-padded (SK-0003 -> SK-0003),
# so a key typed without the padding still finds its file. Any other key comes back unchanged.
def canonicalKey(key):
    if not (isFileBacked(key) and KEY_RE.match(key)):
        return key
    number = int(key.split("-", 1)[1])
    return f"{PROJECT}-{number:0{KEY_DIGITS}d}"


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
    log_match = LOG_HEADING_RE.search(rest)
    before, log_text = (rest[: log_match.start()], rest[log_match.end() :]) if log_match else (rest, "")

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

    entries = []
    for line in log_text.strip("\n").split("\n"):
        lm = LOG_LINE_RE.match(line.strip())
        if lm:
            entries.append({"date": lm.group(1), "text": lm.group(2)})
    return front, title, body, entries


# Replaces frontmatter field's value in text (the "field: ..." line inside the --- block), leaving
# everything else -- comments, spacing, the rest of the file -- untouched. Raises if the field
# isn't already there: a transition never invents a field issues/README.md doesn't define.
def _setField(text, field, value):
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise RuntimeError("issue file has no --- frontmatter block")
    block = m.group(1)
    pattern = re.compile(rf"(?m)^{re.escape(field)}:.*$")
    if not pattern.search(block):
        raise RuntimeError(f'issue file has no "{field}" field in its frontmatter')
    new_block = pattern.sub(f"{field}: {value}", block, count=1)
    start, end = m.span(1)
    return text[:start] + new_block + text[end:]


# Appends a dated log line at the true end of the file, under a ## Log heading -- created, once,
# if the file doesn't have one yet.
def _appendLog(text, line_text):
    entry = f"- {date.today().isoformat()}: {line_text}"
    if LOG_HEADING_RE.search(text):
        return text.rstrip("\n") + "\n" + entry + "\n"
    return text.rstrip("\n") + "\n\n## Log\n" + entry + "\n"


def _keyFromFilename(name):
    m = re.match(rf"^({re.escape(PROJECT)}-\d{{{KEY_DIGITS},}})(?:-.*)?\.md$", name)
    return m.group(1) if m else None


# A list() candidate's place in the work order: issues with an ordinal first, by ordinal, then the
# rest; the issue number breaks ties.
def workOrder(candidate):
    ordinal, number = candidate[0], candidate[1]
    return (ordinal is None, ordinal or 0, number)


class FileStore:
    # settings: { dir, pages }. dir is the store's checkout (lib/config.py's issueStore); there is
    # no default, since the store is its own repo, outside skuld. pages is the local server's base
    # URL, for each Issue's page; without it an Issue has no page.
    def __init__(self, settings=None):
        settings = settings or {}
        d = settings.get("dir")
        if not d:
            raise RuntimeError("FileStore needs the issue store's folder (issueStore)")
        self.dir = Path(d).resolve()
        self.pages = settings.get("pages")
        self.settings = {"dir": str(self.dir), "pages": self.pages}

    def _path(self, key):
        key = canonicalKey(key)
        matches = sorted(p for p in self.dir.glob(f"{key}-*.md"))
        if not matches:
            exact = self.dir / f"{key}.md"
            if exact.exists():
                return exact
            raise IssueNotFound(f"no issue file for {key} in {self.dir}")
        if len(matches) > 1:
            names = ", ".join(p.name for p in matches)
            raise RuntimeError(f"more than one issue file matches {key} in {self.dir}: {names}")
        return matches[0]

    # Every SK file whose status is open or in-progress, as the rows every adapter's list() returns,
    # so lib/cli.py's nextUpRows() merges the sets untouched (decision #3). The store
    # serves one project, so any other project has no rows here.
    #
    # Sort order (issues/README.md's `ordinal:` field, the work order): issues with an ordinal first,
    # by `ordinal:` ascending; the rest after them, by issue NUMBER ascending (numeric, so the
    # order holds even past SK-9999, where the padding runs out). Glob order is irrelevant
    # here -- every candidate is parsed first, then the whole batch is sorted by workOrder().
    def list(self, project=PROJECT):
        if project != PROJECT:
            return []
        candidates = []
        for path in self.dir.glob(f"{PROJECT}-*.md"):
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
            rows.append(
                {
                    "id": key,
                    "subject": title,
                    "status": status,
                    "category": CATEGORIES[status],
                    "issueType": front.get("type") or DEFAULT_TYPE,
                    "project": PROJECT,
                    "parent": None,
                    # Optional interleaving anchor (issues/README.md's `after:` field): a key of an
                    # earlier set this SK issue sorts right after. None when absent; lib/cli.py's
                    # mergeSets() places a row whose anchor isn't in an earlier set at its set's end.
                    "after": front.get("after") or None,
                }
            )
        return rows

    # One issue, from one read of its file. Files carry no hierarchy and no issue links, so it has
    # no parent and no blockers; its ## Log entries are its comments, so Done's "already posted this
    # note" check works the same as on any tracker.
    def issue(self, key):
        path = self._path(key)
        front, title, body, entries = _parse(path.read_text(encoding="utf8"))
        status = statusOf(front)
        key = canonicalKey(key)
        return Issue(
            key=key,
            summary=title,
            status=status,
            category=CATEGORIES[status],
            issueType=front.get("type") or DEFAULT_TYPE,
            description=body,
            comments=tuple(Comment(author="plan", text=e["text"], date=e["date"]) for e in entries),
            page=self._page(key),
            file=str(path),
            fields=front,
        )

    # The ticket's page on the local server (lib/server.py's /issues/<KEY>).
    def _page(self, key):
        return f"{self.pages}issues/{key}" if self.pages else None

    # The status value a move to category writes; refuses a category Start doesn't move to, and an
    # issue that isn't open.
    def statusTransitionId(self, key, category):
        if category not in TARGET_STATUS:
            raise RuntimeError(f"{key} can't be moved to {category}: a ticket only moves to {IN_PROGRESS}")
        issue = self.issue(key)
        if not issue.isNew():
            raise RuntimeError(f'{key} can\'t be moved to {category} from "{issue.status}"')
        return TARGET_STATUS[category]

    # Sets frontmatter status, then reads the file back and refuses if it didn't take (decision #2).
    def transition(self, key, category):
        value = self.statusTransitionId(key, category)
        path = self._path(key)
        path.write_text(_setField(path.read_text(encoding="utf8"), "status", value), encoding="utf8")
        issue = self.issue(key)
        if issue.status != value:
            raise RuntimeError(
                f'{key}\'s file still shows "{issue.status}", not "{value}"; '
                f"check its frontmatter, then try again"
            )
        return issue

    # Appends a dated log line (decision #2).
    def addComment(self, key, text):
        path = self._path(key)
        path.write_text(_appendLog(path.read_text(encoding="utf8"), text), encoding="utf8")

    # What Done prints for the human's close: the human sets status: done in the file by hand
    # (decision #2).
    def closeNote(self, key):
        return f"{key} is file-backed: set status: done in {self._path(key)} when it's settled."
