# JP-<n> issues: Jira Plan's own backlog (issues/README.md), stored as files in this repo instead
# of Jira. FileIssues implements the same surface lib/jira.py's Jira class does -- list(jql),
# issue(key), blockers(issue), statusTransitionId(key, name), transition(key, name),
# textComment(text), addComment(key, doc) -- returning dicts shaped the way callers (lib/cli.py,
# lib/start.py, lib/done.py) already read Jira's, so none of them need a JP-specific branch.
# isFileBacked(key) is the dispatch: lib/cli.py's jiraFrom(cfg, key) calls it to decide whether a
# key's commands go to Jira or to a FileIssues instance -- user decision 2026-11-05.
#
# Three decisions this implements (2026-11-05):
#  1. Every plan command (list/picker, view, start/open, ship, done) works on a JP key.
#  2. Writes edit the file: Start's transition sets frontmatter `status: in-progress`, then reads
#     it back (transition()), the same refuse-if-it-didn't-take guard Jira's own transition()
#     uses. Every addComment call appends a dated `- YYYY-MM-DD: <text>` line under a `## Log`
#     heading at the end of the file (created if absent). Done never changes status -- same hard
#     rule as Jira (docs/architecture.md §7.3); the human sets `status: done` by hand.
#  3. `plan list`/the picker merge Jira's JQL results with every JP file whose status is `open` or
#     `in-progress` (lib/cli.py's issueRows()).
import re
from datetime import date
from pathlib import Path

from .jira import projectOf, textComment as _adfTextComment

# The one project this module serves. projectOf("JP-3") == PROJECT routes a key here instead of
# to Jira.
PROJECT = "JP"

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

FRONTMATTER_RE = re.compile(r"^---\n(.*?\n)---\n?(.*)$", re.DOTALL)
LOG_HEADING_RE = re.compile(r"(?m)^## Log\s*$")
LOG_LINE_RE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2}):\s*(.*)$")


def isFileBacked(key):
    return projectOf(key) == PROJECT


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
    m = re.match(rf"^({re.escape(PROJECT)}-\d+)(?:-.*)?\.md$", name)
    return m.group(1) if m else None


class FileIssues:
    # settings: { dir }. dir defaults to this repo's issues/ folder (lib/config.py's issuesDir).
    def __init__(self, settings=None):
        settings = settings or {}
        d = settings.get("dir")
        if not d:
            d = Path(__file__).resolve().parent.parent / "issues"
        self.dir = Path(d).resolve()
        self.settings = {"dir": str(self.dir)}

    def _path(self, key):
        matches = sorted(p for p in self.dir.glob(f"{key}-*.md"))
        if not matches:
            exact = self.dir / f"{key}.md"
            if exact.exists():
                return exact
            raise RuntimeError(f"no issue file for {key} in {self.dir}")
        if len(matches) > 1:
            names = ", ".join(p.name for p in matches)
            raise RuntimeError(f"more than one issue file matches {key} in {self.dir}: {names}")
        return matches[0]

    # Every JP file whose status is open or in-progress, in the shape lib/jira.py's Jira.list()
    # returns, so lib/cli.py's issueRows() can concatenate the two lists untouched (decision #3).
    # jql is accepted and ignored: a file has no JQL to run.
    #
    # Sort order (2026-11-06, issues/README.md's `rank:` field -- the work order; each issue's
    # file carries its own reason in a `## Rank` section): ranked issues first, by `rank:` ascending;
    # unranked issues after them, by issue NUMBER ascending (numeric, not the lexical glob/filename
    # order, so JP-10 doesn't sort before JP-2). Glob order is irrelevant here -- every candidate
    # is parsed first, then the whole batch is sorted by (hasRank, rank, number) below.
    def list(self, jql=None):
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
            rankValue = front.get("rank")
            rank = int(rankValue) if rankValue else None
            candidates.append((rank, number, key, title, status, front))
        candidates.sort(key=lambda c: (c[0] is None, c[0] if c[0] is not None else 0, c[1]))

        rows = []
        for rank, number, key, title, status, front in candidates:
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
                    # a Jira key this JP issue should sort right after in issueRows()'s merge
                    # (lib/cli.py). None when absent -- lib/cli.py's mergeRows() decides the
                    # fallback-to-end cases (missing, a JP key, or not in the current Jira list).
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
            "key": key,
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

    # Files carry no issue links, so nothing ever blocks a JP issue.
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
        value = self.statusTransitionId(key, targetStatusName)
        path = self._path(key)
        path.write_text(_setField(path.read_text(encoding="utf8"), "status", value), encoding="utf8")
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
        path = self._path(key)
        path.write_text(_appendLog(path.read_text(encoding="utf8"), _flatten(doc)), encoding="utf8")

    # What plan view/Start print instead of a Jira browse URL: the file itself.
    def browseUrl(self, key):
        return str(self._path(key))

    # What Done prints instead of "Close KEY in Jira: <url>" -- there is no Jira transition to ask
    # for; the human sets status: done in the file by hand (decision #2).
    def closeNote(self, key):
        return f"{key} is file-backed: set status: done in {self._path(key)} when it's settled."
