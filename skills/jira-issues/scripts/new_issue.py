#!/usr/bin/env python3
"""Create a Jira issue from a filled-in copy of references/jira-issue.md.

    new_issue.py .tmp/CIAM-79/retire-src.md            # create it, link it, read it back
    new_issue.py --check .tmp/CIAM-79/retire-src.md    # every check, reads only, nothing written

The front matter holds the fields, by the names Jira shows:

    project     the project's key, as CIAM (default) or another project's
    type        Task, Story, Bug, Sub-task, ... as the project has them
    summary     the title
    status      To Do by default (informational only: created issues start in the project's
                default create status; this script never transitions an issue)
    compliance  CIAM's required "Compliance Tags", comma-separated; default "Not Required"
    duedate     YYYY-MM-DD; CIAM requires one, default today + 14 days
    parent      an issue key, for a Sub-task or an Epic child; blocked_by and relates, issue
                keys separated by commas

Blank fields are left out. The body after the front matter is the description, and it must pass
outline.py's check, run from inside the repo so the repo's lenses count. Every name is looked up
in Jira and every value checked before anything is written.

A second run finds the issue by its summary in the project, creates nothing new, and adds any
link still missing, so a run that stopped part way is finished by running it again. The issue is
read back at the end, and any field Jira didn't keep is named.

Creation for project CIAM delegates to ~/.claude/skills/jira-ciam-create/create-ciam-issue.sh,
which supplies the project's required fields (Compliance Tags, due date) that a plain REST POST
without them would 400 on. Other projects are created with a plain REST POST, since they may not
share CIAM's required fields.

Auth is via ~/.netrc, machine cryoport.atlassian.net (login = email, password = API token). This
script never prints the token, and it never transitions an issue's status.
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import outline
from checklist import Jira, Refused

FIELDS = ["project", "type", "summary", "status", "compliance", "duedate", "parent", "blocked_by", "relates"]
CREATE_CIAM_ISSUE = Path.home() / ".claude/skills/jira-ciam-create/create-ciam-issue.sh"

BLOCKS_LINK_TYPE = "Blocks"     # verified live: id 10000, outward "blocks", inward "is blocked by"
RELATES_LINK_TYPE = "Relates"   # verified live: id 10003, outward "Relates To", inward "Is Related To"


def parse(text: str) -> tuple[dict[str, str], str]:
    """The front matter's fields, blanks dropped, and the body after it."""
    m = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text.replace("\r\n", "\n"), re.S)
    if not m:
        raise Refused("the file starts with a --- front matter block, as references/jira-issue.md does")
    fields = {}
    for n, line in enumerate(m[1].split("\n"), start=2):
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or key not in FIELDS:
            raise Refused(f"line {n}: {line!r} isn't one of {', '.join(FIELDS)}")
        if key in fields:
            raise Refused(f"line {n}: {key} is given twice")
        if value.strip():
            fields[key] = value.strip()
    if "project" not in fields:
        fields["project"] = "CIAM"
    for key in ("type", "summary"):
        if key not in fields:
            raise Refused(f"{key} is blank")
    return fields, m[2].strip() + "\n"


def keys(key: str, value: str) -> list[str]:
    out = []
    for part in value.split(","):
        part = part.strip().upper()
        if not re.match(r"^\d+$", part) and not re.match(r"^[A-Z][A-Z0-9]+-\d+$", part):
            raise Refused(f"{key}: {part!r} isn't a Jira key")
        out.append(part)
    return out


def resolve_key(project: str, value: str) -> str:
    return value if "-" in value else f"{project}-{value}"


def jql_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def lucene_escape(text: str) -> str:
    """Escape Lucene query-parser specials, so a literal phrase search doesn't trip on them."""
    specials = set('+-&|!(){}[]^"~*?:\\/')
    return "".join(("\\" + c) if c in specials else c for c in text)


def existing(jira, project: str, summary: str) -> str | None:
    """The key of the project's one issue with this summary; None if it has none.

    Jira Cloud's `summary = "..."` operator silently returns no results (verified live: CIAM-169);
    only `~` is documented for summary. Wrapping the escaped text in its own quotes inside the ~
    operand still does an exact-phrase match rather than an OR-of-words search. The found set is
    then filtered to an exact match below (==), since ~ is a text search, not equality, and may
    over-match (e.g. on substrings or stems).

    N6: /search/jql pages its results (nextPageToken); the phrase match can land anywhere in the
    result set, so a single un-paged GET could miss the real match on a later page and wrongly
    report "no existing issue", creating a duplicate on the second run. This follows
    nextPageToken until it's exhausted or an exact match turns up, whichever comes first.
    """
    phrase = f'"{lucene_escape(summary)}"'
    jql = f'project = "{jql_quote(project)}" AND summary ~ "{jql_quote(phrase)}"'
    token = None
    while True:
        path = f"/rest/api/3/search/jql?jql={urllib_quote(jql)}&fields=summary"
        if token:
            path += f"&nextPageToken={urllib_quote(token)}"
        result = jira.send("GET", path)
        found = [i["key"] for i in result.get("issues", []) if i["fields"]["summary"] == summary]
        if len(found) > 1:
            raise Refused(f"{', '.join(found)} all have this summary; give it a distinct one")
        if found:
            return found[0]
        token = result.get("nextPageToken")
        if not token:
            return None


def urllib_quote(text: str) -> str:
    import urllib.parse
    return urllib.parse.quote(text, safe="")


def validate(jira, fields: dict[str, str], relations: dict[str, list[str]]) -> None:
    """Looks up every name Jira needs and every linked key; Refused naming the first bad one."""
    project = jira.send("GET", f"/rest/api/3/project/{fields['project']}")
    types = [t["name"] for t in project.get("issueTypes", [])]
    if fields["type"] not in types:
        raise Refused(f"no issue type {fields['type']!r} in {fields['project']}; types: {', '.join(types)}")
    for other in [fields.get("parent")] + [k for ks in relations.values() for k in ks]:
        if other is not None:
            jira.send("GET", f"/rest/api/3/issue/{other}")  # a key that doesn't exist is a 404, Refused


def create_ciam(fields: dict[str, str], body: str) -> str:
    """Creates a CIAM issue via the required-fields-aware helper. Returns its key."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = f.name
    args = [str(CREATE_CIAM_ISSUE), "-s", fields["summary"], "-m", body_file, "-t", fields["type"]]
    if "compliance" in fields:
        args += ["-c", fields["compliance"]]
    if "duedate" in fields:
        args += ["-d", fields["duedate"]]
    if "parent" in fields:
        args += ["-P", resolve_key(fields["project"], fields["parent"])]
    result = subprocess.run(args, capture_output=True, text=True)
    Path(body_file).unlink(missing_ok=True)
    if result.returncode != 0:
        raise Refused(f"create-ciam-issue.sh failed: {result.stderr.strip()}")
    key = result.stdout.split()[0]
    if not re.match(r"^[A-Z][A-Z0-9]+-\d+$", key):
        raise Refused(f"create-ciam-issue.sh gave an unexpected result: {result.stdout.strip()}")
    return key


def create_generic(jira, fields: dict[str, str], body: str) -> str:
    """Creates a non-CIAM issue with a plain REST POST (no project-specific required fields)."""
    issue_fields = {
        "project": {"key": fields["project"]},
        "issuetype": {"name": fields["type"]},
        "summary": fields["summary"],
        "description": {"type": "doc", "version": 1,
                         "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]}
                                     for line in body.splitlines() if line]},
    }
    if "parent" in fields:
        issue_fields["parent"] = {"key": resolve_key(fields["project"], fields["parent"])}
    return jira.send("POST", "/rest/api/3/issue", {"fields": issue_fields})["key"]


def linked(links: list[dict], other: str, kind: str) -> bool:
    for link in links:
        type_name = link["type"]["name"]
        inward = link.get("inwardIssue", {}).get("key")
        outward = link.get("outwardIssue", {}).get("key")
        if other not in (inward, outward):
            continue
        if kind == "relates" and type_name == RELATES_LINK_TYPE:
            return True
        if kind == "blocked_by" and type_name == BLOCKS_LINK_TYPE and inward == other:
            return True
    return False


def add_link(jira, key: str, other: str, kind: str) -> None:
    """Jira's POST /rest/api/3/issueLink needs the full {inwardIssue, outwardIssue} pair, not just
    one side. For "Blocks", the outward phrase is "blocks" and the inward phrase "is blocked by":
    fetching the outward partner's own issue later shows this link as {inwardIssue: <the blocker>}
    (verified live, matching lib/jira.py's blockers()), so key (this issue) is blocked_by's
    outward partner and other (the blocker) is its inward partner.
    Doc: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-links/#api-rest-api-3-issuelink-post
    This direction can't be write-tested here (no live Jira writes allowed); see the report.
    """
    link_type = BLOCKS_LINK_TYPE if kind == "blocked_by" else RELATES_LINK_TYPE
    body = {"type": {"name": link_type}}
    if kind == "blocked_by":
        body["inwardIssue"] = {"key": other}
        body["outwardIssue"] = {"key": key}
    else:
        body["inwardIssue"] = {"key": key}
        body["outwardIssue"] = {"key": other}
    jira.send("POST", "/rest/api/3/issueLink", body)


def kept(issue: dict, fields: dict[str, str]) -> list[str]:
    """Each field new_issue asked for that the issue, read back, doesn't hold."""
    lost = []
    live_fields = issue["fields"]
    if live_fields["summary"] != fields["summary"]:
        lost.append("summary")
    if live_fields["issuetype"]["name"] != fields["type"]:
        lost.append("type")
    if "parent" in fields and (live_fields.get("parent") or {}).get("key") != resolve_key(fields["project"], fields["parent"]):
        lost.append("parent")
    if "duedate" in fields and live_fields.get("duedate") != fields["duedate"]:
        lost.append("duedate")
    return lost


def run(file: Path, jira, check_only: bool, lenses: list[str], extra: list[str]) -> str:
    fields, body = parse(file.read_text(encoding="utf-8"))
    findings = outline.check(body, lenses, extra)
    if findings:
        raise Refused("the body doesn't conform to the outline:\n" + "\n".join(f"  - {f}" for f in findings))

    relations = {k: [resolve_key(fields["project"], v) for v in keys(k, fields[k])]
                 for k in ("blocked_by", "relates") if k in fields}
    if "parent" in fields:
        fields["parent"] = resolve_key(fields["project"], fields["parent"])
    validate(jira, fields, relations)

    key = existing(jira, fields["project"], fields["summary"])
    if check_only:
        return f"{file} is ready: {key + ' exists, and a run adds only its missing links' if key else 'a run creates it'}"

    if key is None:
        key = create_ciam(fields, body) if fields["project"] == "CIAM" else create_generic(jira, fields, body)
        made = "created"
    else:
        made = "already there"

    have = jira.send("GET", f"/rest/api/3/issue/{key}?fields=issuelinks")["fields"].get("issuelinks", [])
    added = 0
    for kind, others in relations.items():
        for other in others:
            if not linked(have, other, kind):
                add_link(jira, key, other, kind)
                added += 1

    back = jira.send("GET", f"/rest/api/3/issue/{key}?fields=summary,issuetype,parent,duedate,issuelinks")
    missing = [f"{kind} {o}" for kind, others in relations.items() for o in others
               if not linked(back["fields"].get("issuelinks", []), o, kind)]
    lost = kept(back, fields) + missing
    if lost and made == "created":
        raise Refused(f"{key} is created, but Jira didn't keep: {', '.join(lost)}")
    if lost:
        raise Refused(f"{key} was already there, and differs from the file in: {', '.join(lost)}; edit it in Jira")
    url = f"https://cryoport.atlassian.net/browse/{key}"
    return f"{key} {made}, {added} link{'s' if added != 1 else ''} added: {url}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="check everything, reading only; write nothing")
    p.add_argument("file", type=Path, help="a filled-in copy of references/jira-issue.md")
    a = p.parse_args(argv)
    rw = outline.repo_writer()
    lenses, extra = outline.expected(
        outline.GLOBAL_WRITER.read_text(encoding="utf-8") if outline.GLOBAL_WRITER.is_file() else "",
        rw.read_text(encoding="utf-8") if rw else "",
    )
    try:
        print(f"new_issue: {run(a.file, Jira(), a.check, lenses, extra)}")
    except Refused as e:
        sys.exit(f"new_issue: {a.file}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
