# The interim suite's pure helpers: they read data the suite already fetched and answer one
# question each, so lib/test_interim_e2e.py proves them with no Redmine, git or Keychain.
import os
import re
import json
import urllib.parse
from html.parser import HTMLParser

from . import fileissues

KEY_SPLIT_RE = re.compile(r"[,\s]+")
FINDINGS_FILE = "review.json"
DONE_LINE_RE = re.compile(r"^- \[([ xX])\] (.*)$")
SCP_REMOTE_RE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(?!//)(.+)$")


# ---- Redmine journals

def changesStatus(journal):
    return any(d.get("name") == "status_id" for d in journal.get("details", []))


def newestStatusJournal(journals):
    """The newest journal that changes the status, or None. Redmine lists journals oldest first."""
    changing = [j for j in journals if changesStatus(j)]
    return max(changing, key=lambda j: j["id"]) if changing else None


def newestJournal(journals):
    return max(journals, key=lambda j: j["id"]) if journals else None


def journalUserId(journal):
    return journal["user"]["id"] if journal else None


def journalCount(issue):
    return len(issue.get("journals", []))


def journalMentions(journal, text):
    """True when any detail of the journal holds `text` in one of its values."""
    return bool(journal) and any(text in str(v) for d in journal.get("details", []) for v in d.values())


# ---- git state

def remoteIdentity(url):
    """One string per repo, whichever form its remote URL takes: `host/owner/repo` for ssh, scp-style
    and https URLs, the real path for a local one."""
    host, path = remoteHostAndPath(url.strip())
    repoPath = path.strip("/").removesuffix(".git")
    if not host:
        return os.path.realpath(path.removesuffix(".git"))
    return f"{host}/{repoPath}".lower()


def remoteHostAndPath(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme and parsed.scheme != "file":
        return parsed.hostname or "", parsed.path
    if parsed.scheme == "file":
        return "", parsed.path
    scp = SCP_REMOTE_RE.match(url)
    return (scp.group(1), scp.group(2)) if scp else ("", url)


def branchNames(branchListOutput):
    """The names in `git branch --list --format=%(refname:short)` output, one per line. That format
    carries no `*` or `+` marker for the current branch or one checked out in a linked worktree."""
    return [line.strip() for line in branchListOutput.splitlines() if line.strip()]


def worktreesOf(porcelain, branches, folderName):
    """The paths of `git worktree list --porcelain` entries that belong to an issue: those on one of
    `branches`, and those in a folder named `folderName` (Start's `<repo>.worktrees/<KEY>`) even
    when Done left its HEAD detached and its branch deleted."""
    paths = []
    for entry in porcelain.strip().split("\n\n"):
        fields = dict(line.split(" ", 1) for line in entry.splitlines() if " " in line)
        branch = fields.get("branch", "").removeprefix("refs/heads/")
        path = fields["worktree"]
        if branch in branches or os.path.basename(path) == folderName:
            paths.append(path)
    return paths


def isDirectoryEntry(entry):
    return entry.endswith("/")


def isListed(path, manifest):
    """True when the manifest names `path`, or a directory entry above it."""
    return any(path == entry or (isDirectoryEntry(entry) and path.startswith(entry)) for entry in manifest)


def compareStaged(staged, manifest):
    """(listed but not staged, staged but not listed), both sorted."""
    missing = sorted(entry for entry in manifest if not isDirectoryEntry(entry) and entry not in staged)
    unexpected = sorted(path for path in staged if not isListed(path, manifest))
    return missing, unexpected


def porcelainStatusOf(porcelain, path):
    """The two-character status git shows for `path`, or None when it is absent."""
    for line in porcelain.splitlines():
        if line[3:] == path:
            return line[:2]
    return None


# ---- Redmine pages and users

class _DescriptionLinks(HTMLParser):
    """Collects the href of every <a> inside an element whose class list has "description"."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.depth:
            self.depth += tag not in VOID_TAGS
            if tag == "a" and attributes.get("href"):
                self.links.append(attributes["href"])
        elif "description" in (attributes.get("class") or "").split():
            self.depth = 1

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1


VOID_TAGS = {"br", "hr", "img", "input", "meta", "link"}


def descriptionLinks(html):
    parser = _DescriptionLinks()
    parser.feed(html)
    return parser.links


def linksToIssue(html, number):
    return f"/issues/{number}" in descriptionLinks(html)


def membershipProjectNames(user):
    return sorted(m["project"]["name"] for m in user.get("memberships", []))


def roundsAdded(before, after):
    return len(after.get("review_rounds", [])) - len(before.get("review_rounds", []))


def fetchChangesetsPath(projectId, key):
    return "/sys/fetch_changesets?" + urllib.parse.urlencode({"id": projectId, "key": key})


def endsWithPrCommand(pageText, key):
    """True when the report page's text ends with "run `pr <key>`"."""
    return pageText.rstrip().endswith(f"run `pr {key}`")


# ---- the ticket store

def parseTicket(text):
    """{"frontmatter", "title", "body", "done"} for a store ticket file, read by skuld's own
    parser (lib/fileissues.py). `body` is the text between the title and `## Log`; `done` is the
    `## Done` section's [{"text", "ticked"}], each item's wrapped lines joined by one space."""
    frontmatter, title, body, _log = fileissues._parse(text)
    return {"frontmatter": frontmatter, "title": title, "body": body.strip(), "done": doneItems(body)}


def ticketKey(fileName):
    """The key a store file name carries (`SK-0012-slug.md` is SK-0012), or None for any other file."""
    return fileissues._keyFromFilename(fileName)


def blockerKeys(frontmatter):
    return [k for k in KEY_SPLIT_RE.split(frontmatter.get("blocked_by", "")) if k]


def doneItems(body):
    section = re.search(r"(?m)^## Done\s*$(.*?)(?=^## |\Z)", body, re.DOTALL)
    items = []
    for line in (section.group(1).splitlines() if section else []):
        item = DONE_LINE_RE.match(line)
        if item:
            items.append({"text": item.group(2).strip(), "ticked": item.group(1) != " "})
        elif items and line.startswith("      "):
            items[-1]["text"] += " " + line.strip()
    return items


def customField(issue, name):
    for field in issue.get("custom_fields", []):
        if field.get("name") == name:
            return field.get("value")
    return None


def importMismatches(ticket, issue, checklist, blockerIds, statusMap, botUserId):
    """What differs between a ticket file and the issue the import made from it (§3.10). Empty
    means the issue keeps every field. `blockerIds` are the Redmine ids of the ticket's blockers."""
    fields = ticket["frontmatter"]
    found = []

    def expect(name, expected, actual):
        if expected != actual:
            found.append(f"{name}: file has {expected!r}, Redmine has {actual!r}")

    expect("subject", ticket["title"], issue.get("subject"))
    expect("description", ticket["body"], (issue.get("description") or "").strip())
    expect("status", statusMap.get(fields.get("status")), issue.get("status", {}).get("name"))
    expect("fixed_version", fields.get("milestone") or None, issue.get("fixed_version", {}).get("name"))
    expect("Ordinal", fields.get("ordinal") or None, customField(issue, "Ordinal") or None)
    expect("blocks", sorted(blockerIds), sorted(incomingBlockers(issue)))
    expect("checklist", ticket["done"], [{"text": i["text"], "ticked": i["ticked"]} for i in checklist])
    foreign = sorted({j["user"]["id"] for j in issue.get("journals", []) if j["user"]["id"] != botUserId})
    expect("journal users other than the bot", [], foreign)
    return found


def incomingBlockers(issue):
    return [
        r["issue_id"]
        for r in issue.get("relations", [])
        if r["relation_type"] == "blocks" and r["issue_to_id"] == issue["id"]
    ]


def splitRound(files):
    """(the round's files, its findings or None) from the files of a store round folder. The
    findings are the `findings` array of its review.json, when it has one."""
    named = {name: content for name, content in files.items() if name != FINDINGS_FILE}
    if FINDINGS_FILE not in files:
        return named, None
    return named, json.loads(files[FINDINGS_FILE]).get("findings", [])


def firstFailures(messages, limit=3):
    """One line for a loop's failures: the first few, then how many more."""
    shown = "; ".join(messages[:limit])
    return shown + (f"; and {len(messages) - limit} more" if len(messages) > limit else "")
