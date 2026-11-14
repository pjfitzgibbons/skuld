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

from .issuekeys import projectOf

# The machine config names the site and the projects; nothing here defaults to either.

# Next up's Jira half when the config sets no `jql`: the open issues assigned to me in the
# configured projects, in Jira's rank order.
def nextUpJql(projects):
    return (
        f"project in ({', '.join(projects)}) AND assignee = currentUser() "
        "AND statusCategory != Done ORDER BY Rank"
    )


# A minimal one-paragraph-per-line ADF doc, shared by Jira.textComment() and
# lib/fileissues.py's FileIssues.textComment() so both backends build comments the same way.
def textComment(text):
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": ([{"type": "text", "text": line}] if line else [])}
            for line in text.split("\n")
        ],
    }


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
    # settings: { baseUrl (required), jql, netrcFile }
    def __init__(self, settings):
        self.settings = {
            "baseUrl": re.sub(r"/+$", "", settings["baseUrl"]),
            "jql": settings.get("jql"),
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

    # The configured JQL's issues, in rank order, with what a row shows. /search/jql is the live
    # endpoint; the older /search returns 410 Gone.
    def list(self, jql=None):
        if jql is None:
            jql = self.settings["jql"]
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
                    "statusCategory": fields_["status"]["statusCategory"]["key"],
                    "issueType": fields_["issuetype"]["name"],
                    "project": projectOf(issue["key"]),
                    "parent": parent["key"] if parent else None,
                }
            )
        return result

    # One issue in full: the first click on its row, what Start reads to check blockers, and what
    # Done reads to check whether it already posted its merge note (comment included, so a rerun
    # doesn't post the same note twice).
    def issue(self, key):
        fields = "summary,status,issuetype,parent,issuelinks,description,comment"
        return self.get(f"/rest/api/3/issue/{quote(key, safe='')}?fields={fields}")

    # The transition id whose target status is named targetStatusName. Never hardcode a transition
    # id: Jira assigns them per-project-per-workflow, and the same target status can sit behind a
    # different id, or a differently-worded transition, in another project.
    def statusTransitionId(self, key, targetStatusName):
        data = self.get(f"/rest/api/3/issue/{quote(key, safe='')}/transitions")
        transitions = data["transitions"]
        found = next((t for t in transitions if t["to"]["name"] == targetStatusName), None)
        if not found:
            names = ", ".join(f'"{t["name"]}" → {t["to"]["name"]}' for t in transitions) or "(none)"
            raise RuntimeError(f'{key} has no transition to "{targetStatusName}"; available: {names}')
        return found["id"]

    # Transition key to targetStatusName, then read it back and refuse if it didn't take. Jira, like
    # Redmine (docs/failure-modes.md), can accept a transition POST and still not move the issue: a
    # workflow condition or validator can block it, or a required field prompt (not sent here) can
    # leave it half-applied. A 201/204 from the POST is never trusted on its own.
    def transition(self, key, targetStatusName):
        tid = self.statusTransitionId(key, targetStatusName)
        self.send("POST", f"/rest/api/3/issue/{quote(key, safe='')}/transitions", {"transition": {"id": tid}})
        issue = self.issue(key)
        if issue["fields"]["status"]["name"] != targetStatusName:
            raise RuntimeError(
                f'Jira kept {key} {issue["fields"]["status"]["name"]}, not {targetStatusName}. It can refuse a '
                f"transition when a workflow condition or validator blocks it, or a required field it needs isn't "
                f"set. Fix that, then try again"
            )
        return issue

    # A minimal one-paragraph ADF comment body. Markdown with structure (lists, task items) goes
    # through ~/.claude/skills/jira-markdown-adf/md_to_adf.py instead; this is for plain status notes.
    def textComment(self, text):
        return textComment(text)

    def browseUrl(self, key):
        return f'{self.settings["baseUrl"]}/browse/{key}'

    # What Done prints in place of a status change (decision #5/§7.3): there is no Jira write
    # left to make once the note is posted, only a human transition.
    def closeNote(self, key):
        return f"Close {key} in Jira: {self.browseUrl(key)}"

    def addComment(self, key, adfDoc):
        self.send("POST", f"/rest/api/3/issue/{quote(key, safe='')}/comment", {"body": adfDoc})

    # The unresolved issues blocking this one. Jira's issuelinks are directional: a link entry carries
    # either outwardIssue (this issue <outward-phrase> that one) or inwardIssue (this issue
    # <inward-phrase> that one). "Blocks"'s inward phrase is "is blocked by", so a link with
    # inwardIssue present under that type names a blocker; a link with outwardIssue present names an
    # issue this one blocks, which isn't a blocker on this issue at all. A blocker already Done
    # doesn't count.
    def blockers(self, issue):
        links = issue["fields"].get("issuelinks") or []
        result = []
        for link in links:
            if link["type"]["name"] == "Blocks" and link.get("inwardIssue"):
                blocker = link["inwardIssue"]
                if blocker["fields"]["status"]["statusCategory"]["key"] != "done":
                    result.append(blocker)
        return result
