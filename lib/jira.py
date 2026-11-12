# Jira Cloud's REST API v3, for the terminal front end and for Start. Stdlib only (urllib), so it
# runs under plain python3 too. Auth comes from ~/.netrc (the machine named by the site's host)
# each time it's needed; the token is never stored here, logged or printed.
#
# Ported from lib/jira.js. Faithful, behavior-preserving port: no change in logic.
import base64
import json
import re
from pathlib import Path
from urllib.parse import quote, urlparse
import urllib.error
import urllib.request

from .issue import DONE, IN_PROGRESS, NEW, Blocker, Comment, Issue, projectOf

# The machine config names the site and the projects; nothing here defaults to either.

# Jira's status category keys, in the port's words. Every Jira workflow sorts its statuses into
# these three, however many it has ("Blocked" and "QA Testing" are both indeterminate).
CATEGORIES = {"new": NEW, "indeterminate": IN_PROGRESS, "done": DONE}

# The status each port category a command moves an issue to names in Jira. Start's move is the only
# one: the status named "In Progress".
TARGET_STATUS = {IN_PROGRESS: "In Progress"}


# A Jira status's category, in the port's words. A category Jira adds later reads as NEW.
def categoryOf(status):
    return CATEGORIES.get(status["statusCategory"]["key"], NEW)


# A project's set in Next up when its config entry sets no `jql`: the open issues assigned to me,
# in Jira's rank order.
def nextUpJql(projects):
    return (
        f"project in ({', '.join(projects)}) AND assignee = currentUser() "
        "AND statusCategory != Done ORDER BY Rank"
    )


# A minimal one-paragraph-per-line ADF doc: a plain-text comment as Jira takes it. Markdown with
# structure (lists, task items) goes through ~/.claude/skills/jira-markdown-adf/md_to_adf.py instead.
def textComment(text):
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": ([{"type": "text", "text": line}] if line else [])}
            for line in text.split("\n")
        ],
    }


# A text node's text, as `[text](href)` when it carries a link mark, so the destination isn't lost.
# The text alone when it already is the href.
def linkedText(node):
    text = node.get("text") or ""
    href = linkHref(node)
    return f"[{text}]({href})" if href and href != text else text


def linkHref(node):
    for mark in node.get("marks") or []:
        if mark.get("type") == "link":
            return (mark.get("attrs") or {}).get("href")
    return None


# A minimal ADF document -> plain text renderer, for an Issue's description and comments. Only the
# node types md_to_adf.py and Jira's own editor actually produce: paragraphs, text (with a bold/
# code mark folded in as-is, a link mark as Markdown), hard breaks, bullet/ordered lists, headings
# (as Markdown `#` headings), task items (as `- [x]` / `- [ ]` lines) and link cards. Anything else
# just recurses into its content, so an unrecognized node still shows its text rather than
# vanishing silently.
def adfText(node, indent=""):
    if not node:
        return ""
    node_type = node.get("type")
    if node_type == "text":
        return linkedText(node)
    if node_type == "hardBreak":
        return "\n"
    if node_type == "inlineCard":
        return (node.get("attrs") or {}).get("url") or ""
    children = "".join(adfText(child, indent) for child in node.get("content") or [])
    if node_type == "paragraph":
        return f"{indent}{children}\n"
    if node_type == "heading":
        level = (node.get("attrs") or {}).get("level") or 1
        return f"{indent}{'#' * level} {children.strip()}\n"
    if node_type == "taskItem":
        done = (node.get("attrs") or {}).get("state") == "DONE"
        return f"{indent}- [{'x' if done else ' '}] {children.strip()}\n"
    if node_type == "listItem":
        return f"{indent}- {children.strip()}\n"
    return children


def adfToText(doc):
    if not doc:
        return ""
    return "".join(adfText(node) for node in doc.get("content") or []).strip()


def _default_netrc_file():
    return str(Path.home() / ".netrc")


# ~/.netrc's `machine X login Y password Z` tokens, possibly spread across lines. No library: the
# format is simple enough that pulling in a dependency for it would be the bigger risk.
#
# L3: a real .netrc can also hold `#`-prefixed comment lines, an `account` token alongside
# login/password, and `macdef` macro blocks (a macro name followed by its body, ending at the
# next blank line) -- none of which a plain whitespace-split tokenizer understands; a macdef body
# in particular can contain arbitrary words that would otherwise be misread as machine/login/
# password tokens. Both are stripped line-by-line before tokenizing.
def readNetrc(machine, file=None):
    if file is None:
        file = _default_netrc_file()
    try:
        with open(file, "r", encoding="utf8") as f:
            text = f.read()
    except FileNotFoundError as error:
        raise RuntimeError(f"can't read {file} for the Jira token: no such file") from error
    except OSError as error:
        raise RuntimeError(f"can't read {file} for the Jira token: {error}") from error

    kept = []
    in_macdef = False
    for line in text.split("\n"):
        trimmed = line.strip()
        if in_macdef:
            if trimmed == "":
                in_macdef = False  # a blank line ends the macro body
            continue
        if re.match(r"^macdef\b", trimmed):
            in_macdef = True
            continue
        if trimmed.startswith("#"):
            continue  # a whole-line comment
        kept.append(line)

    tokens = [t for t in re.split(r"\s+", " ".join(kept)) if t]
    for i, token in enumerate(tokens):
        if token == "machine" and i + 1 < len(tokens) and tokens[i + 1] == machine:
            entry = {"machine": machine}
            j = i + 2
            while j < len(tokens) and tokens[j] != "machine":
                if tokens[j] == "login":
                    entry["login"] = tokens[j + 1] if j + 1 < len(tokens) else None
                elif tokens[j] == "password":
                    entry["password"] = tokens[j + 1] if j + 1 < len(tokens) else None
                elif tokens[j] == "account":
                    entry["account"] = tokens[j + 1] if j + 1 < len(tokens) else None  # kept, though unused here
                else:
                    break
                j += 2
            if not entry.get("login") or not entry.get("password"):
                raise RuntimeError(f'{file} has a "machine {machine}" entry with no login/password')
            return entry
    raise RuntimeError(
        f'no "machine {machine}" entry in {file}; add one with your Jira email as login and an API token as password'
    )


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if not self._payload:
            return None
        return json.loads(self._payload.decode("utf8"))


class Jira:
    # settings: { baseUrl (required), jql: { <project>: <JQL> }, netrcFile }. A project jql
    # doesn't name runs nextUpJql().
    def __init__(self, settings):
        self.settings = {
            "baseUrl": re.sub(r"/+$", "", settings["baseUrl"]),
            "jql": settings.get("jql") or {},
            "netrcFile": settings.get("netrcFile"),
        }

    def authHeader(self):
        host = urlparse(self.settings["baseUrl"]).netloc
        entry = readNetrc(host, self.settings["netrcFile"])
        token = base64.b64encode(f'{entry["login"]}:{entry["password"]}'.encode("utf8")).decode("ascii")
        return f"Basic {token}"

    def get(self, path):
        return self.send("GET", path).json()

    def send(self, method, path, body=None):
        base_url = self.settings["baseUrl"]
        headers = {"Authorization": self.authHeader(), "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf8")
        req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                payload = resp.read()
        except urllib.error.HTTPError as error:
            with error:
                status = error.code
                payload = error.read()
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", error)
            raise RuntimeError(f"Jira can't be reached at {base_url}: {reason}") from error

        if status == 401:
            host = urlparse(base_url).netloc
            raise RuntimeError(
                f"Jira refused the credentials in ~/.netrc for {host} (401); check the login/password (API token) entry"
            )
        if not (200 <= status < 300):
            # A refused write names its reason in errorMessages/errors; a read only has its status.
            reasons = []
            try:
                r = json.loads(payload.decode("utf8")) if payload else {}
                reasons = list(r.get("errorMessages", [])) + list(r.get("errors", {}).values())
            except Exception:
                reasons = []
            suffix = f": {'; '.join(reasons)}" if reasons else ""
            raise RuntimeError(f"Jira {method} {path}: HTTP {status}{suffix}")
        return _Response(payload)

    # Next up's rows for one project: its query's issues, in its order.
    def list(self, project):
        return self.search(self.nextUpQuery(project))

    def nextUpQuery(self, project):
        return self.settings["jql"].get(project) or nextUpJql([project])

    # A JQL query's issues, in its order, with what a row shows. /search/jql is the live endpoint;
    # the older /search returns 410 Gone.
    def search(self, jql):
        fields = ["summary", "status", "issuetype", "parent"]
        issues = []
        next_page_token = None
        while True:
            body = {"jql": jql, "fields": fields, "maxResults": 100}
            if next_page_token:
                body["nextPageToken"] = next_page_token
            page = self.send("POST", "/rest/api/3/search/jql", body).json()
            issues.extend(page["issues"])
            next_page_token = page.get("nextPageToken")
            if not next_page_token:
                break
        # No "Pull requests" field: Jira has no equivalent custom field, and Done finds the PR from
        # the branch instead (gh pr view/list --head), never from the issue.
        result = []
        for issue in issues:
            fields_ = issue["fields"]
            parent = fields_.get("parent")
            result.append(
                {
                    "id": issue["key"],
                    "subject": fields_["summary"],
                    "status": fields_["status"]["name"],
                    "category": categoryOf(fields_["status"]),
                    "issueType": fields_["issuetype"]["name"],
                    "project": projectOf(issue["key"]),
                    "parent": parent["key"] if parent else None,
                }
            )
        return result

    # One issue in full, in one GET: what plan view shows, what Start reads to check blockers, and
    # what Done reads to check whether it already posted its merge note.
    def issue(self, key):
        fields = "summary,status,issuetype,parent,issuelinks,description,comment"
        raw = self.get(f"/rest/api/3/issue/{quote(key, safe='')}?fields={fields}")
        return self.issueFrom(raw)

    def issueFrom(self, raw):
        f = raw["fields"]
        parent = f.get("parent")
        return Issue(
            key=raw["key"],
            summary=f["summary"],
            status=f["status"]["name"],
            category=categoryOf(f["status"]),
            issueType=f["issuetype"]["name"],
            parent=parent["key"] if parent else None,
            description=adfToText(f.get("description")),
            comments=tuple(commentFrom(c) for c in commentsOf(f)),
            blockers=tuple(blockerFrom(link["inwardIssue"]) for link in openBlockingLinks(f)),
            page=self.browseUrl(raw["key"]),
        )

    # The transition id to category's target status (TARGET_STATUS). Never hardcode a transition
    # id: Jira assigns them per-project-per-workflow, and the same target status can sit behind a
    # different id, or a differently-worded transition, in another project.
    def statusTransitionId(self, key, category):
        targetStatusName = targetStatus(key, category)
        data = self.get(f"/rest/api/3/issue/{quote(key, safe='')}/transitions")
        transitions = data["transitions"]
        found = next((t for t in transitions if t["to"]["name"] == targetStatusName), None)
        if not found:
            names = ", ".join(f'"{t["name"]}" → {t["to"]["name"]}' for t in transitions) or "(none)"
            raise RuntimeError(f'{key} has no transition to "{targetStatusName}"; available: {names}')
        return found["id"]

    # Transition key to category's target status, then read it back and refuse if it didn't take. Jira, like
    # Redmine (docs/failure-modes.md), can accept a transition POST and still not move the issue: a
    # workflow condition or validator can block it, or a required field prompt (not sent here) can
    # leave it half-applied. A 201/204 from the POST is never trusted on its own.
    def transition(self, key, category):
        targetStatusName = targetStatus(key, category)
        tid = self.statusTransitionId(key, category)
        self.send("POST", f"/rest/api/3/issue/{quote(key, safe='')}/transitions", {"transition": {"id": tid}})
        issue = self.issue(key)
        if issue.status != targetStatusName:
            raise RuntimeError(
                f'Jira kept {key} {issue.status}, not {targetStatusName}. It can refuse a '
                f"transition when a workflow condition or validator blocks it, or a required field it needs isn't "
                f"set. Fix that, then try again"
            )
        return issue

    def browseUrl(self, key):
        return f'{self.settings["baseUrl"]}/browse/{key}'

    # What Done prints in place of a status change (decision #5/§7.3): there is no Jira write
    # left to make once the note is posted, only a human transition.
    def closeNote(self, key):
        return f"Close {key} in Jira: {self.browseUrl(key)}"

    # A plain-text comment, posted as ADF.
    def addComment(self, key, text):
        self.send("POST", f"/rest/api/3/issue/{quote(key, safe='')}/comment", {"body": textComment(text)})


def targetStatus(key, category):
    if category not in TARGET_STATUS:
        raise RuntimeError(f"{key} can't be moved to {category} by plan")
    return TARGET_STATUS[category]


def commentsOf(fields):
    return (fields.get("comment") or {}).get("comments") or []


def commentFrom(raw):
    author = (raw.get("author") or {}).get("displayName") or "?"
    return Comment(author=author, text=adfToText(raw.get("body")), date=(raw.get("created") or "")[:10] or None)


# The links naming an unresolved issue that blocks this one. Jira's issuelinks are directional: a
# link entry carries either outwardIssue (this issue <outward-phrase> that one) or inwardIssue (this
# issue <inward-phrase> that one). "Blocks"'s inward phrase is "is blocked by", so a link with
# inwardIssue present under that type names a blocker; a link with outwardIssue present names an
# issue this one blocks, which isn't a blocker on this issue at all. A blocker already Done doesn't
# count.
def openBlockingLinks(fields):
    return [link for link in fields.get("issuelinks") or [] if isOpenBlocker(link)]


def isOpenBlocker(link):
    blocker = link.get("inwardIssue")
    if link["type"]["name"] != "Blocks" or not blocker:
        return False
    return categoryOf(blocker["fields"]["status"]) != DONE


def blockerFrom(raw):
    f = raw["fields"]
    return Blocker(key=raw["key"], summary=f.get("summary") or "", status=f["status"]["name"])
