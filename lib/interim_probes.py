# The interim suite's pure helpers: they read data the suite already fetched and answer one
# question each, so lib/test_interim_e2e.py proves them with no Redmine, git or Keychain.
import re
from html.parser import HTMLParser

KEY_SPLIT_RE = re.compile(r"[,\s]+")
FRONTMATTER_RE = re.compile(r"^---\n(.*?\n)---\n?(.*)$", re.DOTALL)
DONE_LINE_RE = re.compile(r"^- \[([ xX])\] (.*)$")


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


# ---- git state

def branchNames(branchListOutput):
    return [line.strip().lstrip("*").strip() for line in branchListOutput.splitlines() if line.strip()]


def worktreesOnBranches(porcelain, branches):
    """The paths of `git worktree list --porcelain` entries whose branch is one of `branches`."""
    paths = []
    for entry in porcelain.strip().split("\n\n"):
        fields = dict(line.split(" ", 1) for line in entry.splitlines() if " " in line)
        branch = fields.get("branch", "").removeprefix("refs/heads/")
        if branch in branches:
            paths.append(fields["worktree"])
    return paths


def compareStaged(staged, manifest):
    """(listed but not staged, staged but not listed), both sorted. A manifest directory entry
    (trailing "/") covers every staged path beneath it."""
    covered = lambda path: any(path == m or (m.endswith("/") and path.startswith(m)) for m in manifest)
    missing = sorted(m for m in manifest if not m.endswith("/") and m not in staged)
    unexpected = sorted(path for path in staged if not covered(path))
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
    return f"/sys/fetch_changesets?id={projectId}&key={key}"


# ---- the ticket store

def parseTicket(text):
    """{"frontmatter", "title", "body", "done"} for a store ticket file. `body` is the text after
    the title line; `done` is the `## Done` section's [{"text", "ticked"}], each item's wrapped
    lines joined by one space."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("a ticket file opens with a frontmatter block")
    head, rest = match.groups()
    title, _, body = rest.lstrip("\n").partition("\n")
    return {
        "frontmatter": frontmatterFields(head),
        "title": title.removeprefix("# ").strip(),
        "body": body.strip(),
        "done": doneItems(body),
    }


def frontmatterFields(head):
    fields = {}
    for line in head.splitlines():
        name, _, value = line.partition(":")
        fields[name.strip()] = value.strip()
    return fields


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


def firstFailures(messages, limit=3):
    """One line for a loop's failures: the first few, then how many more."""
    shown = "; ".join(messages[:limit])
    return shown + (f"; and {len(messages) - limit} more" if len(messages) > limit else "")
