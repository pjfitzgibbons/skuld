#!/usr/bin/env python3
"""Claude's review of a PR, kept in the issue's latest round folder, .tmp/<KEY>/rounds/<n>/, of its worktree.

    review.py request KEY [--restart | --if-due]             # write request.md for the next review
    review.py withdraw KEY --token T                        # take back the request T names when its review never started
    review.py claim KEY                                     # record the reviewer's Claude in the unfinished round
    review.py finish KEY                                    # post review.next.json, then review.json
    review.py due KEY                                       # exit 0 when HEAD needs a review; 1, and why, when not

`pr` runs `plan review KEY`, which runs `request` and starts a fresh Claude on the line that starts
the review, in the plan window's "Review KEY" terminal in the worktree. The Claude reads request.md, runs `claim`, reviews, appends progress to the round's log, writes
review.next.json and runs `finish` as its last step.

Rounds are numbered from 0, one folder each, the highest the latest. Round 0 is the author's change,
reviewed with `/code-review medium`; each later round answers a review of the PR. A review after a
push opens the next round: n is the latest round plus one, and at least 1. The author then answers
it in the same folder (report.md, replies/, commit.txt), which this script never writes. The
folder's location is rounds_folder(); the rounds move to the store with the rest of an issue's
files, and that function is the one place the path is written.

A review's files, in rounds/<n>/:

    review.json        the finished review (absent until `finish`)
    review.next.json   the review in progress, written by the reviewer, read by `finish`
    request.md         the prompt of the unfinished review; `finish` deletes it
    request.json       {"round", "head", "pr", "range", "call", "token", "reviewer"} of that request,
                       "reviewer" ({"pid", "sessionId"}) added by `claim`; `finish` deletes it
    posted.json        the ids `finish` has posted so far; `finish` deletes it when it is done
    review.log         what the review did, one line per step

review.json, and review.next.json in the same shape (the reviewer leaves out file, line, range and
call, which `finish` adds):

    {"head": "<sha reviewed>", "base": "main", "round": 2, "range": "<rev range>", "call": "<the call>",
     "findings": [{"file": "<path>", "line": "12-20", "key": "<file>:<lines>:<classification>",
                   "summary": "what fails, and when", "fix": "recommended fix (optional)",
                   "disposition": "open" | "Fixed" | "New", "commentId": 123, "reviewId": 45}],
     "dispositions": [{"path": "<path>", "blob": "<blob sha at head>", "passes": ["callers", "docs"],
                       "verdict": "OK" | ["<finding key>", ...]}],
     "checks": [{"script": "<path>", "output": "what it printed"}]}

findings and dispositions are the lists the local server's review page reads; the rest is extra. A
review's findings include every earlier unsettled finding with its new disposition, so the latest
review is complete on its own.

The last review is the highest round whose review.json has a head, which only a finished review
after a push writes: round 0's review.json, from `/code-review medium`, has none, so the first
review after a push is a first review. A round is unfinished while its folder has request.json and
no review.json.

A finding key is file:lines:classification, lines being "12" or "12-20". A finding is settled when
Fixed; open and New are unsettled. `finish` posts each unsettled finding that has neither a commentId
nor a reviewId, by where its lines are in the PR's diff, and records the id:

    every line in the diff   an inline comment of one PR review          -> commentId
    its file in the diff     a file-level review comment (subject_type)  -> commentId
    its file not in the PR   a line of that review's body                -> reviewId

request.json holds the head the review was requested for; `finish` checks the record against that
head, not the current HEAD, and posts with it as the review's commit, so a push during the review
doesn't lose its work. `request` with an unfinished review whose head is HEAD reopens it as it is: request.md and
request.json are not rewritten. With one for an older head it refuses and names `--restart`, which
restarts the review in its own folder at HEAD, unless the reviewer that `claim`ed the round is still
a session in `claude agents --json`, whatever its state: then it refuses, and so does it when that
list can't be read. A round nobody claimed, or whose reviewer's pid is gone, restarts. With none, and nothing due, it says why and writes
nothing. `finish` is safe to rerun: posted.json in the round's folder records each posted id as soon
as its POST succeeds, and a rerun skips what it names. Before each POST it writes an "intent" (the
keys about to be sent) to posted.json; a rerun that finds an intent with no outcome, because the POST
failed or GitHub took it and the answer or the write was lost, looks for the review (this round, this
head, carrying every key) or the file-level comment on the PR by its finding markers, records what is
there and posts only what is missing. It survives `--restart`, so a restarted
round never posts a finding twice, and posts its new findings in a further review, so it can always
finish. `--if-due` (`pr`'s call) writes and reopens nothing when no review is due, which it checks
inside the lock, so two overlapping runs start one review. A rebase with nothing left to review
completes an unfinished restarted round, so none stays unfinished.

Every range, the call, the blob lookups and the diff the findings are placed against use the commit
the review was requested for, never the moving HEAD, so a push between the request and the review
doesn't change what is reviewed. A path the PR deletes, or that a rebase dropped from the PR, gets a
disposition whose blob is "absent". `request` and `finish` hold an exclusive lock on the issue's
rounds folder (.review.lock, created with O_EXCL and removed when they end); a held lock refuses at
once, naming the file. The file holds its run's pid: one whose pid is dead is cleared, and one whose
pid can't be read is deleted by hand when no run is active.

A review is due when HEAD differs from the last review's head, or there is none, unless an
unfinished review was already requested for this HEAD. `due` answers that from the files alone, so a
`pr` that stopped after its push reviews those commits on its next run.

The folder is in the worktree root (`git rev-parse --show-toplevel`), whatever directory this runs in.
"""

import argparse
import contextlib
import functools
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections import namedtuple
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
FIRST_PROMPT = "first-review.md"
ROUND_PROMPT = "review-round.md"

UNSETTLED = ("open", "New")
STATES = ("open", "Fixed", "New")
SHA = re.compile(r"^[0-9a-f]{40}$")
ROUND_NAME = re.compile(r"^(0|[1-9][0-9]*)$")
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")
LINES = re.compile(r"^(\d+)(?:-(\d+))?$")


class ReviewError(Exception):
    pass


# --- the record's rules, pure ---


def choose_prompt(record):
    """The first review's prompt when the PR has no record, else the round's."""
    return FIRST_PROMPT if record is None else ROUND_PROMPT


def diff_range(base, record, head, head_reachable=True):
    """What to review, as of the commit head: the PR against its base, or what changed since the
    last reviewed head.

    A recorded head that is no longer in head's history (a rebase or force-push) can't bound a
    range, so the whole PR is reviewed again, minus the OK files whose blob is unchanged.
    """
    if record is None or not head_reachable:
        return f"origin/{base}...{head}"
    return f"{record['head']}..{head}"


def review_call(range_, files=None):
    """The exact review call: always an explicit range and --max-findings all.

    After a rebase the recorded head can't bound a range, and the call is the PR's whole range
    followed by `-- <files>`, the files not settled. The docs don't describe a range combined with
    paths; they say Claude "receives that text as the review target"
    (https://code.claude.com/docs/en/code-review-local.md#choose-what-to-review), and the prompt
    tells Claude to review only those files.

    With no target the review reads only the commits ahead of the upstream, which the push has just
    moved to HEAD, and a value passed in one run isn't remembered by the next.
    """
    call = f"/code-review high --max-findings all {range_}"
    return f"{call} -- {' '.join(shlex.quote(path) for path in files)}" if files else call


def rebased_files(record, blobs):
    """The files to review after a rebase: those not settled (files_to_read), and those of
    unsettled findings. Empty when nothing is left to review."""
    return sorted(set(files_to_read(record, blobs)) | unsettled_finding_files(record))


def unsettled_finding_files(record):
    return {parse_key(finding["key"])[0] for finding in unsettled_findings(record)}


def unsettled_findings(record):
    return [finding for finding in (record or {}).get("findings", []) if finding["disposition"] in UNSETTLED]


def ok_files(record):
    """{path: blob} of the files the last round found OK."""
    return {entry["path"]: entry["blob"] for entry in (record or {}).get("dispositions", []) if entry["verdict"] == "OK"}


ABSENT = "absent"  # the blob of a path the PR deletes, or that is no longer in the PR


def current_state(blobs, deleted):
    """{path: blob} of what the PR changes as of the requested commit, a deleted path as ABSENT."""
    return {**blobs, **{path: ABSENT for path in deleted}}


def absent_paths(record, current):
    """The paths the last record gave a disposition that the PR no longer changes at all."""
    return sorted({entry["path"] for entry in (record or {}).get("dispositions", [])} - set(current))


def files_to_read(record, current_blobs):
    """The changed files, {path: blob now}, to read: not an OK file whose blob is unchanged. A
    deleted file's blob is ABSENT, so one already found OK at ABSENT isn't read again."""
    settled = ok_files(record)
    return sorted(path for path, blob in current_blobs.items() if settled.get(path) != blob)


def review_round(numbers, unfinished):
    """The round a review opens: its own folder when one is unfinished, else the latest round plus
    one, and at least 1 (round 0 is the author's, whether or not its folder exists). numbers are
    the existing rounds, lowest first."""
    if unfinished:
        return unfinished["round"]
    return numbers[-1] + 1 if numbers else 1


def is_orange(record, head, round_unfinished):
    """A finished review of this head with unsettled findings waits for a person."""
    return bool(record) and record.get("head") == head and not round_unfinished and bool(unsettled_findings(record))


def parse_key(key):
    """(file, first line, last line, classification) of a finding key."""
    head, _, classification = key.rpartition(":")
    path, _, lines = head.rpartition(":")
    found = LINES.match(lines)
    if not (path and classification and found):
        raise ReviewError(f"finding key {key!r} is not file:lines:classification")
    first = int(found.group(1))
    return path, first, int(found.group(2) or first), classification


def record_problems(record, round_number, head):
    """Why a review.next.json can't be accepted, one line each; empty when it can."""
    if not isinstance(record, dict):
        return ["review.next.json is not a JSON object"]
    problems = []
    if record.get("head") != head:
        problems.append(f"head is {record.get('head')!r}, but this round's request is for {head}: the round was "
                        "restarted since this review began, so stop and edit nothing, and tell the author")
    if record.get("round") != round_number:
        problems.append(f"round is {record.get('round')!r}, this is round {round_number}")
    if not isinstance(record.get("base"), str) or not record.get("base"):
        problems.append("base is missing")
    dispositions = record.get("dispositions")
    if not isinstance(dispositions, list):
        problems.append("dispositions is not a list")
        dispositions = []
    for entry in dispositions:
        problems += disposition_problems(entry)
    findings = record.get("findings")
    if not isinstance(findings, list):
        problems.append("findings is not a list")
        findings = []
    for finding in findings:
        problems += finding_problems(finding)
    return problems


def disposition_problems(entry):
    if not isinstance(entry, dict):
        return ["a disposition is not an object"]
    path, verdict = entry.get("path", "?"), entry.get("verdict")
    if not (isinstance(entry.get("path"), str) and isinstance(entry.get("blob"), str) and isinstance(entry.get("passes"), list)):
        return [f"disposition {path}: needs path, blob and passes"]
    if verdict != "OK" and not (isinstance(verdict, list) and verdict):
        return [f"disposition {path}: verdict is OK or a list of finding keys"]
    return []


def finding_problems(finding):
    if not isinstance(finding, dict):
        return ["a finding is not an object"]
    name = finding.get("key", "?")
    problems = []
    try:
        parse_key(name)
    except (ReviewError, AttributeError, TypeError):
        problems.append(f"finding {name!r}: key is not file:lines:classification")
    if not finding.get("summary"):
        problems.append(f"finding {name}: summary is missing")
    if finding.get("disposition") not in STATES:
        problems.append(f"finding {name}: disposition is open, Fixed or New")
    if "commentId" in finding and not isinstance(finding["commentId"], int):
        problems.append(f"finding {name}: commentId is not a number")
    return problems


def with_locations(record, range_, call):
    """The record with each finding's file and line taken from its key, and the request's range and
    call: what the review page shows, which the reviewer need not write."""
    findings = []
    for finding in record["findings"]:
        path, first, last, _ = parse_key(finding["key"])
        findings.append({**finding, "file": path, "line": str(first) if first == last else f"{first}-{last}"})
    return {**record, "findings": findings, "range": range_, "call": call}


def posted_inline_keys(posted):
    return {key for review in posted.get("reviews", []) for key in review["keys"]}


def findings_to_post(record, posted=None):
    """The unsettled findings with no comment, and none in a review's body, on the PR yet: neither
    in the record nor in posted, the ids a `finish` that failed part way had already posted."""
    posted = posted or {}
    done = set(posted.get("comments", {})) | set(posted.get("bodies", {})) | posted_inline_keys(posted)
    return [f for f in unsettled_findings(record)
            if not (f.get("commentId") or f.get("reviewId") or f["key"] in done)]


def review_due(record, head, requested_head=None):
    """Whether HEAD needs a review: no record, or a record of another head, and no unfinished round
    already requested for this HEAD (requested_head is None when no round is unfinished)."""
    if requested_head == head:
        return False
    return record is None or record.get("head") != head


def not_due_reason(key, record, record_round, head, unfinished):
    """Why no review is due, as one line: the head was reviewed, or a round already requested for it
    is waiting to finish."""
    if unfinished and unfinished.get("head") == head:
        return (f"round {unfinished['round']} was requested for HEAD {head[:8]} and hasn't finished; "
                f"it runs in the Review {key} terminal of the plan window, or run plan review {key} to start it again")
    return f"HEAD {head[:8]} was reviewed in round {record_round}"


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
DIFF_HEADER = "diff --git "


C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}
QUOTED_TAIL = re.compile(r'"((?:[^"\\]|\\.)*)"$')


def unquote_c(text):
    """A C-quoted path's characters, as git writes one with a special or non-ASCII character."""
    out, i = bytearray(), 0
    while i < len(text):
        escaped = text[i + 1] if text[i] == "\\" and i + 1 < len(text) else None
        if escaped in C_ESCAPES:
            out.append(C_ESCAPES[escaped])
            i += 2
        elif escaped is not None and escaped in "0123":
            out.append(int(text[i + 1:i + 4], 8))
            i += 4
        else:
            out += text[i].encode()
            i += 1
    return out.decode("utf-8", "replace")


def diff_path(header):
    """The new path of a `diff --git a/X b/Y` line, or None when it can't be split.

    A path git quotes ends the line in quotes. An unquoted line with the same path twice, `a/P b/P`,
    splits by length, so a path holding " b/" is read whole; a rename's two paths differ, and its
    `rename to` line sets the path."""
    rest = header[len(DIFF_HEADER):]
    quoted = QUOTED_TAIL.search(rest)
    if quoted:
        return unquote_c(quoted.group(1)).removeprefix("b/")
    length = (len(rest) - 5) // 2
    if len(rest) % 2 == 1 and rest[:2] == "a/" and rest[2 + length:] == " b/" + rest[2:2 + length]:
        return rest[2:2 + length]
    _, found, path = rest.rpartition(" b/")
    return path if found else None


def renamed_to(line):
    """The new path of a `rename to` or `copy to` line."""
    text = line.split(" to ", 1)[1]
    return unquote_c(text[1:-1]) if text.startswith('"') else text


def header_path(text):
    """The path of a `+++ b/<path>` line's text (C-unquoted when quoted, a trailing tab dropped)."""
    text = text.rstrip("\t")
    path = unquote_c(text[1:-1]) if text.startswith('"') and text.endswith('"') else text
    return path.removeprefix("b/")


def read_header(block, line):
    """One header line of a file's block: the unambiguous lines (`rename to`, `copy to`, `+++ b/<path>`,
    `+++ /dev/null`, `deleted file mode`) set its path; `diff --git`'s own guess is only the fallback."""
    if line.startswith("rename to ") or line.startswith("copy to "):
        block["path"] = renamed_to(line)
    elif line == "+++ /dev/null" or line.startswith("deleted file mode"):
        block["deleted"] = True
    elif line.startswith("+++ "):
        block["path"] = header_path(line[4:])


def block_path(block):
    return None if block["deleted"] else block["path"] or block["candidate"]


def diff_lines(diff_text):
    """{path: set of new-file line numbers the diff shows}, from a unified diff.

    Every file the diff names is in the PR: one changed without hunks (a pure rename, a binary
    change) maps to an empty set. A deleted file is left out, having no new lines to comment on.
    A file's path comes from its unambiguous header lines, not from `diff --git a/X b/Y`, which
    can't be split when a path holds " b/". Between a `diff --git` line and its first hunk the
    lines are headers; inside a hunk every line is content, so a removed `-- x` beside an added
    `++ b/foo` is not read as a file header.
    """
    shown, block = {}, None
    for line in diff_text.splitlines():
        if line.startswith(DIFF_HEADER):
            if block and block_path(block):
                shown.setdefault(block_path(block), set())
            block = {"candidate": diff_path(line), "path": None, "deleted": False, "hunk": False}
        elif block and (found := HUNK.match(line)):
            block["hunk"] = True
            if block_path(block):
                first, count = int(found.group(1)), int(found.group(2) or 1)
                shown.setdefault(block_path(block), set()).update(range(first, first + count))
        elif block and not block["hunk"]:
            read_header(block, line)
    if block and block_path(block):
        shown.setdefault(block_path(block), set())
    return shown


def placement(finding, shown):
    """Where a finding can be posted: "inline" when every line it names is in the PR's diff,
    "file" when its file is in the diff but a line isn't, "body" when its file isn't in the PR."""
    path, first, last, _ = parse_key(finding["key"])
    if path not in shown:
        return "body"
    return "inline" if set(range(first, last + 1)) <= shown[path] else "file"


def placements(findings, shown):
    placed = {"inline": [], "file": [], "body": []}
    for finding in findings:
        placed[placement(finding, shown)].append(finding)
    return placed


MARKER = re.compile(r"<!-- review-finding: (.+?) -->")


def marker(key):
    return f"<!-- review-finding: {key} -->"


def marker_keys(text):
    """The finding keys a posted body carries a marker for."""
    return set(MARKER.findall(text or ""))


def comment_body(finding):
    parts = [f"**{finding['key']}**", finding["summary"]]
    if finding.get("fix"):
        parts.append(f"Recommended fix: {finding['fix']}")
    parts.append(marker(finding["key"]))
    return "\n\n".join(parts)


def inline_comment(finding):
    path, first, last, _ = parse_key(finding["key"])
    comment = {"path": path, "line": last, "side": "RIGHT", "body": comment_body(finding)}
    if first != last:
        comment.update(start_line=first, start_side="RIGHT")
    return comment


def file_comment(head, finding):
    """A file-level review comment, for a finding whose lines aren't in the PR's diff. GitHub's
    create-a-review-comment endpoint takes subject_type "file" with body, commit_id and path:
    https://docs.github.com/en/rest/pulls/comments#create-a-review-comment-for-a-pull-request
    The create-a-review endpoint's comments[] has no such field, so each goes in a call of its own."""
    path, _, _, _ = parse_key(finding["key"])
    return {"commit_id": head, "path": path, "subject_type": "file", "body": comment_body(finding)}


def body_line(finding):
    return f"- **{finding['key']}** (its file isn't in the PR): {finding['summary']} {marker(finding['key'])}"


def review_payload(round_number, head, inline, in_body):
    """One review: the inline findings as comments, the findings whose file isn't in the PR as
    lines of its body."""
    lines = [f"Review round {round_number}: {len(inline) + len(in_body)} finding(s) in this review."]
    return {
        "commit_id": head,
        "event": "COMMENT",
        "body": "\n".join(lines + [body_line(finding) for finding in in_body]),
        "comments": [inline_comment(finding) for finding in inline],
    }


def comment_ids(posted_comments):
    """{finding key: comment id} from the comments GitHub holds for the review."""
    ids = {}
    for comment in posted_comments:
        found = re.search(r"<!-- review-finding: (.+?) -->", comment.get("body", ""))
        if found:
            ids[found.group(1)] = comment["id"]
    return ids


def with_posted_ids(record, comment_ids_by_key, review_ids_by_key):
    """The record with each posted finding's comment id, or the id of the review whose body holds it."""
    findings = [
        {**finding, **posted_ids(finding["key"], comment_ids_by_key, review_ids_by_key)}
        for finding in record["findings"]
    ]
    return {**record, "findings": findings}


def posted_ids(key, comment_ids_by_key, review_ids_by_key):
    if key in comment_ids_by_key:
        return {"commentId": comment_ids_by_key[key]}
    if key in review_ids_by_key:
        return {"reviewId": review_ids_by_key[key]}
    return {}


def render(template, values):
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", str(value))
    return template


def rebase_scope(files):
    if not files:
        return ""
    return ("The last reviewed head is no longer in this branch's history (a rebase), so the call names "
            "the whole PR followed by the files not settled. Review only those files; nothing else is "
            "in this round.\n\n")


def file_lines(paths, current):
    """The files to read, one per line; one the PR deletes, or no longer changes, is marked."""
    marked = [f"- {path}" + (f' (deleted, or no longer in the PR; its disposition\'s blob is "{ABSENT}")' if current.get(path) == ABSENT else "")
              for path in paths]
    return "\n".join(marked) or "(none)"


def finding_line(finding):
    line = f"- {finding['key']} [{finding['disposition']}]: {finding['summary']}"
    return f"{line} Recommended fix: {finding['fix']}" if finding.get("fix") else line


def finding_lines(findings):
    return "\n".join(finding_line(finding) for finding in findings) or "(none)"


def ok_lines(record):
    settled = ok_files(record)
    return "\n".join(f"- {path} {blob}" for path, blob in sorted(settled.items())) or "(none)"


# --- the round folders of .tmp/<KEY>/rounds/ ---


def rounds_folder(root, key):
    """Where an issue's rounds are. The one place this path is written."""
    return Path(root) / ".tmp" / key / "rounds"


def round_numbers(rounds):
    """The existing rounds, lowest first; none when the folder doesn't exist."""
    if not rounds.is_dir():
        return []
    names = [name for name in os.listdir(rounds) if ROUND_NAME.match(name) and (rounds / name).is_dir()]
    return sorted(int(name) for name in names)


def round_dir(rounds, number):
    return rounds / str(number)


def read_record(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except ValueError as error:
        raise ReviewError(f"{path} is not valid JSON: {error}") from error


def last_record(rounds):
    """(round number, record) of the highest round whose review.json has a head, else (None, None)."""
    for number in reversed(round_numbers(rounds)):
        try:
            record = read_record(round_dir(rounds, number) / "review.json")
        except ReviewError:
            continue
        if isinstance(record, dict) and record.get("head"):
            return number, record
    return None, None


def unfinished_round(rounds):
    """{"round", "head"} of the highest round with a request.json and no review.json, or None. A
    request.json that is unreadable leaves the head None."""
    for number in reversed(round_numbers(rounds)):
        folder = round_dir(rounds, number)
        if (folder / "request.json").exists() and not (folder / "review.json").exists():
            try:
                return {**json.loads((folder / "request.json").read_text()), "round": number}
            except (OSError, ValueError):
                return {"round": number, "head": None}
    return None


def log_path(folder):
    return folder / "review.log"


def append_log(folder, line):
    folder.mkdir(parents=True, exist_ok=True)
    with open(log_path(folder), "a") as log:
        log.write(f"{line}\n")


def write_atomically(path, text):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


# --- the outside world: git and gh, injected so the tests run neither ---


class Sources:
    """What a round reads from git and GitHub. A test passes its own."""

    def root(self):
        """The worktree root, whatever directory the script runs in."""
        return Path(git("rev-parse", "--show-toplevel"))

    def head(self):
        return git("rev-parse", "HEAD")

    def pull_request(self):
        found = json.loads(run(["gh", "pr", "view", "--json", "number,baseRefName"]))
        return found["number"], found["baseRefName"]

    def reachable(self, sha, head):
        return subprocess.run(["git", "merge-base", "--is-ancestor", sha, head], capture_output=True).returncode == 0

    def changed(self, range_, head):
        """({path: blob sha at head}, [deleted paths]) of the files the range changes. One diff for
        the names (NUL-separated, never quoted) and one ls-tree for the blobs."""
        names = split_nul(run(["git", "-c", "core.quotePath=false", "diff", "--name-only", "-z", range_]))
        if not names:
            return {}, []
        listing = run(["git", "--literal-pathspecs", "ls-tree", "-z", "--full-tree", head, "--", *names])
        blobs = parse_ls_tree(listing)
        return blobs, sorted(set(names) - set(blobs))

    def claude_sessions(self):
        """The sessions `claude agents --json` lists; a ReviewError when it fails or isn't JSON."""
        try:
            sessions = json.loads(run(["claude", "agents", "--json"]))
        except ValueError as error:
            raise ReviewError(f"claude agents --json gave no JSON: {error}") from error
        return [session for session in sessions if isinstance(session, dict)]

    def own_pid(self):
        return os.getpid()

    def parent_pid(self, pid):
        """pid's parent, or None when ps can't tell."""
        try:
            return int(run(["ps", "-o", "ppid=", "-p", str(pid)]).strip())
        except (ReviewError, ValueError):
            return None

    def pr_diff(self, base, head):
        """The PR's unified diff as of head, the one GitHub comments against."""
        return git("-c", "core.quotePath=false", "diff", "--no-ext-diff", f"origin/{base}...{head}")

    def gh_api(self, endpoint, payload=None):
        """The parsed JSON of a gh api call; payload, when given, is POSTed as the body."""
        command = ["gh", "api", endpoint] + (["--method", "POST", "--input", "-"] if payload else [])
        try:
            return json.loads(run(command, json.dumps(payload) if payload else None))
        except ValueError as error:
            raise ReviewError(f"gh api {endpoint} gave no JSON: {error}") from error


    def reviews(self, pull_request):
        """Every review of the PR, all pages."""
        return self.all_pages(f"repos/{{owner}}/{{repo}}/pulls/{pull_request}/reviews")

    def pull_comments(self, pull_request):
        """Every review comment of the PR, all pages."""
        return self.all_pages(f"repos/{{owner}}/{{repo}}/pulls/{pull_request}/comments")

    def all_pages(self, endpoint):
        try:
            return flatten_pages(json.loads(run(["gh", "api", "--paginate", "--slurp", endpoint])))
        except ValueError as error:
            raise ReviewError(f"gh api {endpoint} gave no JSON: {error}") from error

    def review_comments(self, pull_request, review_id):
        """Every comment of a review: gh api pages by 30 unless told to fetch them all."""
        endpoint = f"repos/{{owner}}/{{repo}}/pulls/{pull_request}/reviews/{review_id}/comments"
        try:
            return flatten_pages(json.loads(run(["gh", "api", "--paginate", "--slurp", endpoint])))
        except ValueError as error:
            raise ReviewError(f"gh api {endpoint} gave no JSON: {error}") from error


def split_nul(output):
    return [name for name in output.split("\0") if name]


def parse_ls_tree(output):
    """{path: blob sha} from `git ls-tree -z`, whose entries are `<mode> <type> <sha>\\t<path>`."""
    blobs = {}
    for entry in split_nul(output):
        meta, _, path = entry.partition("\t")
        mode, kind, sha = meta.split()
        if kind == "blob":
            blobs[path] = sha
    return blobs


def flatten_pages(pages):
    """The items of `gh api --paginate --slurp`'s array of pages, as one list."""
    return [item for page in pages for item in (page if isinstance(page, list) else [page])]


def run(command, stdin=None):
    answer = subprocess.run(command, capture_output=True, text=True, input=stdin)
    if answer.returncode != 0:
        raise ReviewError(f"{' '.join(command[:3])} failed: {answer.stderr.strip()}")
    return answer.stdout


def git(*args):
    return run(["git", *args]).strip()


# --- the lock ---

LOCK_NAME = ".review.lock"


def lock_holder(path):
    """The pid in a lock file, or None when it can't be read."""
    try:
        return int(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def pid_is_dead(pid):
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def clear_dead_lock(path, holder):
    """Moves a lock whose pid is dead out of the way with one atomic rename, so of two runs that
    found it dead only one's rename succeeds. A run whose rename took a lock that is no longer the
    dead one's, because another run had already cleared it and created its own, puts that lock back."""
    aside = path.with_name(f"{path.name}.cleared.{os.getpid()}")
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        return
    moved = lock_holder(aside)
    if moved != holder and not pid_is_dead(moved):
        try:
            os.link(aside, path)
        except FileExistsError:
            pass
    aside.unlink(missing_ok=True)


def take_lock(path, action):
    """Creates the lock file exclusively, clearing first one whose pid is dead. Returns None when it
    was taken, else the pid that holds it."""
    for _ in range(3):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = lock_holder(path)
            if not pid_is_dead(holder):
                return holder if holder is not None else "unknown"
            clear_dead_lock(path, holder)
            continue
        os.write(descriptor, f"{os.getpid()} {action}\n".encode())
        os.close(descriptor)
        return None
    return "unknown"


@contextlib.contextmanager
def review_lock(rounds, action):
    """Exclusive, for the length of a request or a finish: created with O_EXCL in the rounds folder.
    It holds the pid of its run, and one whose pid is dead is cleared. A lock held by a live run
    refuses at once, naming the file; one whose pid can't be read is cleared by deleting the file.
    The run removes the file when it ends only if it still holds its own pid, so it never deletes
    another run's lock."""
    rounds.mkdir(parents=True, exist_ok=True)
    path = rounds / LOCK_NAME
    holder = take_lock(path, action)
    if holder is not None:
        raise ReviewError(f"another review run (pid {holder}) holds {path}; run again when it ends. If no run "
                          f"is active and the file is left over, delete {path}")
    try:
        yield
    finally:
        if lock_holder(path) == os.getpid():
            path.unlink(missing_ok=True)


# --- request ---


# path is None when nothing was written; token names the request this call wrote (None for a reopened one)
Outcome = namedtuple("Outcome", "path note token", defaults=(None,))


def request(key, sources, restart=False, prompts=PROMPTS, if_due=False):
    """Writes request.md for the next review, or says why it needs none.

    An unfinished review for this HEAD is reopened as it is, its files untouched. One for another
    head is refused unless restart, which writes it again in its own folder at HEAD. With none
    unfinished and no review due, nothing is written. After a rebase with nothing left to review,
    the last record moves to HEAD and nothing is written."""
    root = sources.root()
    rounds = rounds_folder(root, key)
    with review_lock(rounds, "request"):
        return write_request(key, root, rounds, sources, restart, prompts, if_due)


def live_sessions_by_pid(sources):
    return {session["pid"]: session for session in sources.claude_sessions() if "pid" in session}


def refuse_while_reviewer_runs(unfinished, key, sources):
    """A restart rewrites the request a claimed reviewer is working from, so it waits for that
    reviewer's terminal to close. A reviewer that never claimed, or whose pid is gone, doesn't hold it."""
    reviewer = unfinished.get("reviewer")
    if not reviewer:
        return
    try:
        live = reviewer["pid"] in live_sessions_by_pid(sources)
    except ReviewError as error:
        raise ReviewError(f"can't tell whether round {unfinished['round']}'s reviewer (pid {reviewer['pid']}) "
                          f"is still running: {error}; run plan review {key} --restart again when it works") from error
    if live:
        raise ReviewError(f"round {unfinished['round']}'s reviewer (pid {reviewer['pid']}) is still running; "
                          f"close its Review {key} terminal, then run plan review {key} --restart")


def write_request(key, root, rounds, sources, restart, prompts, if_due=False):
    """request's work, under the lock: the state is read, and due checked, with no other run able
    to change it. With if_due (`pr`'s call), a review that isn't due, an unfinished round for this
    HEAD included, writes and reopens nothing and says why."""
    last_number, record = last_record(rounds)
    unfinished = unfinished_round(rounds)
    head = sources.head()
    if if_due and not restart and not review_due(record, head, unfinished and unfinished.get("head")):
        return Outcome(None, not_due_reason(key, record, last_number, head, unfinished))
    if unfinished:
        folder = round_dir(rounds, unfinished["round"])
        same_head = unfinished.get("head") == head
        if same_head and not restart and (folder / "request.md").exists():
            append_log(folder, f"reopened: the request for {head} is unchanged")
            return Outcome(folder / "request.md", f"round {unfinished['round']}'s request reopened")
        if not same_head and not restart:
            raise ReviewError(
                f"round {unfinished['round']}'s review was requested for HEAD {str(unfinished.get('head'))[:8]}, "
                f"and HEAD is {head[:8]}; run plan review {key} --restart to restart it in its own folder")
        refuse_while_reviewer_runs(unfinished, key, sources)
        append_log(folder, f"restarted: its request is written again at {head}")
    elif not review_due(record, head):
        return Outcome(None, not_due_reason(key, record, last_number, head, None))
    round_number = review_round(round_numbers(rounds), unfinished)
    folder = round_dir(rounds, round_number)
    number, base = sources.pull_request()
    reachable = record is None or sources.reachable(record["head"], head)
    range_ = diff_range(base, record, head, reachable)
    blobs, deleted = sources.changed(range_, head)
    current = current_state(blobs, deleted)
    rebased = record is not None and not reachable
    if rebased:
        current.update({path: ABSENT for path in absent_paths(record, current)})
    only_files = rebased_files(record, current) if rebased else None
    if rebased and not only_files:
        settle_without_review(rounds, last_number, record, unfinished, head)
        return Outcome(None, "nothing to review: every file is OK at an unchanged blob; the last review's head now names HEAD")
    call = review_call(range_, only_files)
    values = {
        "KEY": key,
        "PR": number,
        "BASE": base,
        "HEAD": head,
        "DIR": folder,
        "RANGE": range_,
        "CALL": call,
        "SCOPE": rebase_scope(only_files),
        "ROOT": root,
        "ROUND": round_number,
        "LOG": log_path(folder),
        "RECORD": folder / "review.next.json",
        "LAST_RECORD": round_dir(rounds, last_number) / "review.json" if record else "(none)",
        "SCRIPTS": Path(__file__).resolve().parent,
        "FILES": file_lines(only_files or files_to_read(record, current), current),
        "UNSETTLED": finding_lines(unsettled_findings(record)),
        "OK_FILES": ok_lines(record),
    }
    text = render((prompts / choose_prompt(record)).read_text(), values)
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    stored = {"round": round_number, "head": head, "pr": number, "range": range_, "call": call, "token": token}
    write_atomically(folder / "request.json", json.dumps(stored) + "\n")
    write_atomically(folder / "request.md", text)
    append_log(folder, f"requested: reviewing {range_} at {head}")
    return Outcome(folder / "request.md", f"round {round_number}'s request written", token)


# --- withdraw ---


def withdraw(key, sources, token):
    """Takes back the request this token names, when its review never started, so the next `pr` writes
    the round again. Under the lock, and only while request.json still carries the token and no
    reviewer has claimed the round; a request since replaced or claimed is left alone. Returns what
    it did, one line."""
    rounds = rounds_folder(sources.root(), key)
    with review_lock(rounds, "withdraw"):
        unfinished = unfinished_round(rounds)
        if not unfinished or unfinished.get("token") != token:
            return "request left as it is: it is not the one this run wrote"
        if unfinished.get("reviewer"):
            return f"request left as it is: round {unfinished['round']}'s reviewer has claimed it"
        folder = round_dir(rounds, unfinished["round"])
        for name in ("request.json", "request.md"):
            (folder / name).unlink(missing_ok=True)
        append_log(folder, "withdrawn: its review did not start")
        return f"round {unfinished['round']}'s request withdrawn; the next review run writes it again"


# --- claim ---


def claude_ancestor(sources):
    """The session of the Claude this process runs under: the nearest ancestor whose pid
    `claude agents --json` lists. A ReviewError when no ancestor is one."""
    sessions = live_sessions_by_pid(sources)
    pid = sources.own_pid()
    while pid and pid > 1:
        pid = sources.parent_pid(pid)
        if pid in sessions:
            return sessions[pid]
    raise ReviewError("no Claude session is an ancestor of this process; run it from the Claude in the review's terminal")


def claim(key, sources):
    """Records this Claude, the reviewer, in the unfinished round's request.json. A rerun by the
    same Claude changes nothing; another Claude is refused while the recorded one is still running."""
    rounds = rounds_folder(sources.root(), key)
    with review_lock(rounds, "claim"):
        unfinished = unfinished_round(rounds)
        if not unfinished:
            raise ReviewError("no review is in progress: no round has a request.json and no review.json")
        session = claude_ancestor(sources)
        recorded = unfinished.get("reviewer")
        if recorded and recorded["pid"] == session["pid"]:
            return
        if recorded and recorded["pid"] in live_sessions_by_pid(sources):
            raise ReviewError(f"round {unfinished['round']} is claimed by the reviewer with pid {recorded['pid']}, "
                              "which is still running; stop")
        folder = round_dir(rounds, unfinished["round"])
        stored = json.loads((folder / "request.json").read_text())
        stored["reviewer"] = {"pid": session["pid"], "sessionId": session.get("sessionId")}
        write_atomically(folder / "request.json", json.dumps(stored) + "\n")
        append_log(folder, f"claimed by pid {session['pid']}")


# --- finish ---


def posted_findings(folder, posted):
    """The findings a restarted round had posted, from its review.next.json, each with its ids. One
    whose text isn't kept (review.next.json gone) comes back as a placeholder with its ids."""
    try:
        written = read_record(folder / "review.next.json") or {}
    except ReviewError:
        written = {}
    texts = {finding.get("key"): finding for finding in written.get("findings", []) if isinstance(finding, dict)}
    unread = {key: review["id"] for review in posted["reviews"] for key in review["keys"] if key not in posted["comments"]}
    reviewed = {**posted["bodies"], **unread}
    ids = with_posted_ids({"findings": [{"key": key} for key in {*posted["comments"], *reviewed}]},
                          posted["comments"], reviewed)["findings"]
    return [{"summary": "(posted before a restart; its text was not kept)", "disposition": "open",
             **texts.get(entry["key"], {}), **entry} for entry in ids]


def settle_without_review(rounds, last_number, record, unfinished, head):
    """Moves the last review's head to head. A restarted round that is unfinished is completed with
    that record, in its own folder, so it doesn't stay unfinished and keep the issue from turning
    orange, and the findings it had posted join the record with their ids."""
    if not unfinished:
        write_atomically(round_dir(rounds, last_number) / "review.json", json.dumps({**record, "head": head}, indent=2) + "\n")
        return
    folder = round_dir(rounds, unfinished["round"])
    findings = list(record.get("findings", []))
    kept = {finding["key"] for finding in findings}
    findings += [finding for finding in posted_findings(folder, read_posted(folder)) if finding["key"] not in kept]
    completed = {**record, "head": head, "round": unfinished["round"], "findings": findings}
    write_atomically(folder / "review.json", json.dumps(completed, indent=2) + "\n")
    for name in ("request.md", "request.json", "review.next.json", "posted.json"):
        (folder / name).unlink(missing_ok=True)
    append_log(folder, f"finished without a review: nothing was left to review at {head}")


def finish(key, sources):
    """Posts the review's findings as one PR review, then makes review.next.json the review.json.

    The record is checked against the head the review was requested for, which is also the commit
    the review is posted on, so a push made while it ran doesn't refuse its work."""
    rounds = rounds_folder(sources.root(), key)
    with review_lock(rounds, "finish"):
        return finish_locked(key, rounds, sources)


def finish_locked(key, rounds, sources):
    unfinished = unfinished_round(rounds)
    if not unfinished:
        raise ReviewError("no review is in progress: no round has a request.json and no review.json")
    round_number = unfinished["round"]
    folder = round_dir(rounds, round_number)
    try:
        head = unfinished["head"] or sources.head()
        record = read_record(folder / "review.next.json")
        if record is None:
            raise ReviewError("review.next.json is missing")
        problems = record_problems(record, round_number, head)
        if problems:
            raise ReviewError("review.next.json is refused: " + "; ".join(problems))
        record = with_locations(record, unfinished.get("range"), unfinished.get("call"))
        pull_request = functools.cache(lambda: unfinished.get("pr") or sources.pull_request()[0])
        record = post_findings(record, round_number, folder, pull_request, sources)
        write_atomically(folder / "review.json", json.dumps(record, indent=2) + "\n")
    except (ReviewError, OSError) as error:
        append_log(folder, f"failed: {error}; review.json is not written")
        raise
    for name in ("request.md", "request.json", "review.next.json", "posted.json"):
        (folder / name).unlink(missing_ok=True)
    append_log(folder, f"finished: head {record['head']}, {len(unsettled_findings(record))} unsettled")
    return record


def due(key, sources):
    """"" when a review is due, else the one-line reason it isn't."""
    rounds = rounds_folder(sources.root(), key)
    unfinished = unfinished_round(rounds)
    record_round, record = last_record(rounds)
    head = sources.head()
    if review_due(record, head, unfinished and unfinished["head"]):
        return ""
    return not_due_reason(key, record, record_round, head, unfinished)


def is_due(key, sources):
    return not due(key, sources)


def read_posted(folder):
    """What earlier runs of `finish` for this round had posted: {"reviews": [{"id", "keys"}],
    "comments": {key: comment id}, "bodies": {key: review id}, "intent": the POST in flight or None}. A round restarted at a new head
    keeps it, so what was posted before isn't posted again."""
    try:
        posted = json.loads((folder / "posted.json").read_text())
    except FileNotFoundError:
        posted = {}
    except ValueError as error:
        raise ReviewError(f"{folder / 'posted.json'} is not valid JSON: {error}") from error
    return {"comments": {}, "bodies": {}, "reviews": [], "missing": {}, "intent": None, **posted}


def post_findings(record, round_number, folder, pull_request, sources):
    """Posts what isn't posted yet, writing posted.json after each POST that succeeds, so a rerun
    after a failure, or the finish of a restarted round, posts only what is left. A round restarted
    after a review was posted posts its new findings in a further review. pull_request() is the PR's
    number, fetched once."""
    posted = read_posted(folder)
    settle_intent(posted, folder, round_number, record["head"], pull_request, sources)
    todo = findings_to_post(record, posted)
    placed = placements(todo, diff_lines(sources.pr_diff(record["base"], record["head"]))) if todo else None
    if placed and (placed["inline"] or placed["body"]):
        inline_keys, body_keys = [f["key"] for f in placed["inline"]], [f["key"] for f in placed["body"]]
        intend(posted, folder, {"kind": "review", "inline": inline_keys, "body": body_keys})
        review_id = post_review(record, round_number, placed, pull_request(), sources)
        record_review(posted, review_id, inline_keys, body_keys)
        save_posted(folder, posted)
    collect_inline_comment_ids(posted, folder, pull_request, sources, {f["key"] for f in unsettled_findings(record)})
    try:
        for finding in placed["file"] if placed else []:
            intend(posted, folder, {"kind": "file", "key": finding["key"]})
            posted["comments"][finding["key"]] = post_file_comment(record["head"], finding, pull_request(), sources)
            posted["intent"] = None
            save_posted(folder, posted)
    except ReviewError as error:
        reviews = posted["reviews"]
        raise ReviewError(f"{error} (review {reviews[-1]['id']} had been posted)" if reviews else str(error)) from error
    return with_posted_ids(record, posted["comments"], posted["bodies"])


def intend(posted, folder, intent):
    """Writes what is about to be POSTed before it is sent, so a rerun after a POST whose outcome
    is unknown can look for it on the PR instead of sending it again."""
    posted["intent"] = intent
    save_posted(folder, posted)


def record_review(posted, review_id, inline_keys, body_keys):
    posted["reviews"].append({"id": review_id, "keys": inline_keys})
    posted["bodies"].update({key: review_id for key in body_keys})
    posted["intent"] = None


def settle_intent(posted, folder, round_number, head, pull_request, sources):
    """An intent with no outcome is a POST whose result is unknown: it failed, or GitHub took it and
    the answer or the journal write was lost. Looks for it on the PR by the finding markers, records
    what is there, and clears the intent, so what is missing is posted and what is there is not."""
    intent = posted.get("intent")
    if not intent:
        return
    if intent["kind"] == "review":
        review_id = remote_review_id(posted, intent, round_number, head, pull_request(), sources)
        if review_id:
            record_review(posted, review_id, intent["inline"], intent["body"])
    else:
        comment_id = remote_file_comment_id(posted, intent["key"], head, pull_request(), sources)
        if comment_id:
            posted["comments"][intent["key"]] = comment_id
    posted["intent"] = None
    save_posted(folder, posted)


def remote_review_id(posted, intent, round_number, head, pull_request, sources):
    """The id of an unrecorded review of this round and head on the PR that carries every key of
    the intent, in its body or its comments; None when there is none."""
    wanted = set(intent["inline"]) | set(intent["body"])
    known = {review["id"] for review in posted["reviews"]} | set(posted["bodies"].values())
    for candidate in sources.reviews(pull_request):
        if candidate["id"] in known or candidate.get("commit_id") != head:
            continue
        if not (candidate.get("body") or "").startswith(f"Review round {round_number}:"):
            continue
        carried = marker_keys(candidate.get("body")) | {
            key for comment in sources.review_comments(pull_request, candidate["id"]) for key in marker_keys(comment.get("body"))}
        if wanted <= carried:
            return candidate["id"]
    return None


def remote_file_comment_id(posted, key, head, pull_request, sources):
    """The id of an unrecorded file-level comment for key on the PR at head; None when there is none."""
    known = set(posted["comments"].values())
    for comment in sources.pull_comments(pull_request):
        if (comment["id"] not in known and comment.get("subject_type") == "file"
                and comment.get("commit_id") == head and key in marker_keys(comment.get("body"))):
            return comment["id"]
    return None


def save_posted(folder, posted):
    write_atomically(folder / "posted.json", json.dumps(posted, indent=2) + "\n")


def collect_inline_comment_ids(posted, folder, pull_request, sources, needed):
    """The comment ids of the posted reviews' inline findings still without one. An id is required
    only for a finding the record still carries as unsettled (needed); one GitHub doesn't return for
    any other key, a finding since fixed, say, is recorded as missing and doesn't stop the finish."""
    missing_ids = posted.setdefault("missing", {})
    for review in posted["reviews"]:
        waiting = [key for key in review["keys"]
                   if key not in posted["comments"] and (key in needed or key not in missing_ids)]
        if not waiting:
            continue
        ids = comment_ids(sources.review_comments(pull_request(), review["id"]))
        posted["comments"].update({key: ids[key] for key in waiting if key in ids})
        missing_ids.update({key: review["id"] for key in waiting if key not in ids and key not in needed})
        save_posted(folder, posted)
        absent = [key for key in waiting if key not in ids and key in needed]
        if absent:
            raise ReviewError(f"review {review['id']} was posted but GitHub returned no comment for {', '.join(absent)}")


def post_review(record, round_number, placed, pull_request, sources):
    """Posts the inline findings and the body's as one review; returns its id."""
    endpoint = f"repos/{{owner}}/{{repo}}/pulls/{pull_request}/reviews"
    return posted_id(sources.gh_api(endpoint, review_payload(round_number, record["head"], placed["inline"], placed["body"])))


def post_file_comment(head, finding, pull_request, sources):
    posted = sources.gh_api(f"repos/{{owner}}/{{repo}}/pulls/{pull_request}/comments", file_comment(head, finding))
    return posted_id(posted)


def posted_id(posted):
    if not isinstance(posted, dict) or "id" not in posted:
        raise ReviewError("GitHub's answer to the review has no id")
    return posted["id"]


# --- command line ---


def main(argv=None, sources=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("request", "claim", "withdraw", "finish", "due"):
        command = commands.add_parser(name)
        command.add_argument("key")
    commands.choices["withdraw"].add_argument("--token", required=True)
    commands.choices["request"].add_argument("--restart", action="store_true")
    commands.choices["request"].add_argument("--if-due", action="store_true")
    args = parser.parse_args(argv)
    if not KEY_PATTERN.match(args.key):
        parser.error(f"{args.key!r} is not a Jira key (KEY-123)")
    sources = sources or Sources()
    try:
        if args.command == "request":
            outcome = request(args.key, sources, args.restart, if_due=args.if_due)
            print(json.dumps({"path": str(outcome.path) if outcome.path else None, "note": outcome.note, "token": outcome.token}))
        elif args.command == "withdraw":
            print(withdraw(args.key, sources, args.token))
        elif args.command == "claim":
            claim(args.key, sources)
            print("review: claimed by this Claude")
        elif args.command == "due":
            reason = due(args.key, sources)
            if reason:
                print(reason)
            sys.exit(1 if reason else 0)
        else:
            finish(args.key, sources)
            print("review: recorded in the round's review.json")
    except Exception as error:  # `due` answers 0 or 1, so any failure, an unreadable file included, is neither
        message = str(error) if isinstance(error, ReviewError) else f"{type(error).__name__}: {error}"
        print(f"review: {message}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
