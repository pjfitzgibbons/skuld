#!/usr/bin/env python3
"""Claude's review of a PR, kept in the issue's latest round folder, .tmp/<KEY>/rounds/<n>/, of its worktree.

    review.py request KEY [--restart | --if-due] [--ticket T] # write request.md for the next review
    review.py withdraw KEY --token T                        # take back the request T names when its review never started
    review.py claim KEY                                     # record the reviewer's Claude in the unfinished round
    review.py finish KEY                                    # post review.next.json, then review.json
    review.py due KEY                                       # exit 0 when HEAD needs a review; 1, and why, when not

`pr` runs `plan review KEY`, which runs `request` and starts a fresh Claude on the line that starts
the review, in the plan window's "Review KEY" terminal in the worktree. The Claude reads request.md, runs `claim`, reviews, appends progress to the round's log, writes
review.next.json and runs `finish` as its last step.

Rounds are numbered from 0, one folder each, the highest the latest. Round 0 is the author's change,
reviewed with `/code-review medium`; each later round answers a review of the PR. A review after a
push opens the next round: n is the unfinished round's own number, else a withdrawn round's own
(a request whose review never started, taken back by `withdraw`), else the latest round plus one, and
at least 1. The author then answers
it in the same folder (report.md, replies/, commit.txt), which this script never writes. The
folder's location is rounds_folder(); the rounds move to the store with the rest of an issue's
files, and that function is the one place the path is written, but for the plan window's copy
(vscode/plan-window/plan.js roundRequestPath()), which its test checks against it.

A review's files, in rounds/<n>/:

    review.json        the finished review (absent until `finish`)
    review.next.json   the review in progress, written by the reviewer, read by `finish`
    request.md         the prompt of the unfinished review; `finish` deletes it
    request.json       {"round", "head", "pr", "range", "call", "token", "reviewer"} of that request,
                       "reviewer" ({"pid", "sessionId"}) added by `claim`; `finish` deletes it
    posted.json        the ids `finish` has posted so far; `finish` deletes it when it is done
    review.log         what the review did, one line per step

`notes.md` is not in a round's folder: it is one file in the folder that holds rounds/ (.tmp/<KEY>/notes.md),
which the author creates and edits whenever they like. request.md names its path, and the reviewer reads
it at the start of every review, if it exists, so it binds the review as the ticket's Guardrails do.

review.json, and review.next.json in the same shape (the reviewer leaves out file, line, range and
call, which `finish` adds):

    {"head": "<sha reviewed>", "base": "main", "round": 2, "range": "<rev range>", "call": "<the call>",
     "findings": [{"file": "<path>", "line": "12-20", "key": "<file>:<lines>:<classification>",
                   "summary": "what fails, and when", "fix": "recommended fix (optional)",
                   "why": "why it matters", "fixShows": "what a correct fix must show",
                   "disposition": "open" | "Fixed" | "New" | "OutOfScope",
                   "scopeRule": "the Guardrails or notes.md line that rules it out (OutOfScope only)",
                   "commentId": 123, "reviewId": 45}],
     "dispositions": [{"path": "<path>", "blob": "<blob sha at head>", "passes": ["callers", "docs"],
                       "verdict": "OK" | ["<finding key>", ...]}],
     "checks": [{"script": "<path>", "output": "what it printed"}],
     "advisories": [{"concern": "what recurs", "lens": "the lens it suggests, and its question",
                     "seen": ["<finding key or file:lines>", ...]},
                    {"kind": "break", "concern": "what breaks beyond the round's reach or the PR's scope",
                     "seen": ["<file:lines>"]}]}

advisories (optional) are of two kinds, told apart by value. One with a lens (text that isn't blank)
is a concern found more than twice, three or more places in "seen", that could become a lens. One
with "kind": "break" and no lens (absent, null or blank) is a break seen beyond the round's reach
(its diff and the files up to two steps out from it) or outside the PR's scope, one or more places in
"seen": a round raises findings only within its reach and scope. An advisory that is neither is
refused. `finish` puts both in the body of the review it posts, once per round, and under them the
findings that became OutOfScope this round, each with its key and scopeRule, which need no answer.

findings and dispositions are the lists the local server's review page reads; the rest is extra. A
review's findings include every earlier unsettled finding with its new disposition, so the latest
review is complete on its own.

why and fixShows are the reviewer's reasoning, passed on through the record because each round's
reviewer is a fresh conversation: a finding about to be posted (unsettled, with no commentId or
reviewId) needs both, and later rounds copy them with the rest of the finding and judge its fix
against fixShows. A record written before they existed loads as it is; a finding that carries one
needs it to be text.

The last review is the highest round whose review.json has a head, which only a finished review
after a push writes: round 0's review.json, from `/code-review medium`, has none, so the first
review after a push is a first review. A round is unfinished while its folder has request.json and
no review.json.

A finding key is file:lines:classification, lines being "12" or "12-20". A finding is settled when
Fixed, or OutOfScope: the ticket's Guardrails or the author's notes.md rule it out of the PR's scope,
and its scopeRule quotes that line. open and New are unsettled. `finish` posts each unsettled finding that has neither a commentId
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
list can't be read. A round nobody claimed, or whose reviewer's pid is gone, restarts, and a reopen
removes the gone reviewer from request.json, so the plan window's pid check sees only a reviewer
this script considers live. With none, and nothing due, it says why and writes
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
STATES = ("open", "Fixed", "New", "OutOfScope")
OUT_OF_SCOPE = "OutOfScope"
BREAK_KIND = "break"
SHA = re.compile(r"^[0-9a-f]{40}$")
ROUND_NAME = re.compile(r"^(0|[1-9][0-9]*)$")
# lib/issuekeys.py's ISSUE_KEY_PATTERN, the one issue-key rule, which this script can't import; test_review.py
# checks the two strings agree.
KEY_PATTERN = re.compile(r"^[A-Z]+-[0-9]+$")
LINES = re.compile(r"^(\d+)(?:-(\d+))?$")

# The lens table is outline.py's: the global outline's, plus the repo's docs/agents/technical-writer.md.
GLOBAL_WRITER = Path.home() / ".claude/agents/technical-writer.md"
REPO_WRITER = Path("docs/agents/technical-writer.md")
LENS_ROW = re.compile(r"^\|\s*(L\d+)\s[^|\n]*\|[^\n]*$", re.M)
SELECTED_LENS = re.compile(r"^[-*+] \[[xX]\] (L\d+)\b")
MIN_RECURRENCE = 3  # a concern found more than twice is an advisory for a new lens
ADVISORY_MARKER = "<!-- review-advisory -->"


class ReviewError(Exception):
    pass


# --- the record's rules, pure ---


def choose_prompt(record):
    """The first review's prompt when the PR has no record, else the round's."""
    return FIRST_PROMPT if record is None else ROUND_PROMPT


def diff_range(base, record, head, bounded=True):
    """What to review, as of the commit head: the PR against its base, or what changed since the
    last reviewed head.

    A recorded head that doesn't bound a range (last_head_bounds_range: a rebase, a force-push, or a
    merge since) gets the whole PR reviewed again, minus the OK files whose blob is unchanged.
    """
    if record is None or not bounded:
        return f"origin/{base}...{head}"
    return f"{record['head']}..{head}"


def review_call(range_, files=None):
    """The exact review call: always an explicit range and --max-findings all.

    The call is the general-correctness leg of the reviewer's fan-out; the PR's lenses have legs of
    their own. It runs at medium, the overall review: high would need an instruction that the lenses
    are reviewed elsewhere, and without ultra the text after the level and flags is all review
    target (https://code.claude.com/docs/en/code-review-local.md#text-after-the-effort-level-and-flags).

    After a rebase the recorded head can't bound a range, and the call is the PR's whole range
    followed by `-- <files>`, the files not settled. The docs don't describe a range combined with
    paths; they say Claude "receives that text as the review target"
    (https://code.claude.com/docs/en/code-review-local.md#choose-what-to-review), and the prompt
    tells Claude to review only those files.

    With no target the review reads only the commits ahead of the upstream, which the push has just
    moved to HEAD, and a value passed in one run isn't remembered by the next.
    """
    call = f"/code-review medium --max-findings all {range_}"
    return f"{call} -- {' '.join(shlex.quote(path) for path in files)}" if files else call


def last_head_bounds_range(record, head, sources):
    """Whether the last review's head can start this round's range: it is in head's history (no
    rebase or force-push since) and no merge commit has come in since. A merge of the base would put
    every change the base made into `<last-head>..<head>`; the PR's three-dot range leaves them out."""
    if record is None:
        return True
    return sources.reachable(record["head"], head) and not sources.merges_since(record["head"], head)


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
    return could_be_orange(record, round_unfinished) and record.get("head") == head


def could_be_orange(record, round_unfinished):
    """Everything is_orange needs but the head: a finished review with unsettled findings and no
    round unfinished. A caller that must spend a `git rev-parse` to know the head asks this first."""
    return bool(record) and not round_unfinished and bool(unsettled_findings(record))


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
    advisories = record.get("advisories", [])
    if not isinstance(advisories, list):
        problems.append("advisories is not a list")
        advisories = []
    for advisory in advisories:
        problems += advisory_problems(advisory)
    return problems


def advisory_problems(advisory):
    if not isinstance(advisory, dict):
        return ["an advisory is not an object"]
    if is_recurring_concern(advisory):
        return recurring_concern_problems(advisory)
    if is_outside_break(advisory):
        return outside_break_problems(advisory)
    return [f"advisory {advisory.get('concern')!r}: set lens for a recurring concern, or \"kind\": \"break\" "
            "(with no lens) for a break beyond the round's reach or the PR's scope"]


def is_recurring_concern(advisory):
    """An advisory with a lens, text that isn't blank: a concern found more than twice."""
    return is_text(advisory.get("lens"))


def is_outside_break(advisory):
    """An advisory marked "kind": "break" that carries no lens: a break seen beyond the round's reach or scope."""
    return advisory.get("kind") == BREAK_KIND and not is_recurring_concern(advisory)


def outside_break_problems(advisory):
    if not is_text(advisory.get("concern")):
        return ["advisory of kind break: needs concern, what breaks beyond the round's reach or the PR's scope"]
    seen = advisory.get("seen")
    if not (isinstance(seen, list) and seen and all(is_text(place) for place in seen)):
        return [f"advisory {advisory['concern']!r}: seen lists the places the break was found"]
    return []


def recurring_concern_problems(advisory):
    name = advisory.get("lens", "?")
    if not (isinstance(advisory.get("concern"), str) and advisory["concern"]
            and isinstance(advisory.get("lens"), str) and advisory["lens"]):
        return [f"advisory {name!r}: needs concern and lens"]
    seen = advisory.get("seen")
    if not (isinstance(seen, list) and all(isinstance(place, str) for place in seen) and len(seen) >= MIN_RECURRENCE):
        return [f"advisory {name!r}: seen lists the {MIN_RECURRENCE} or more places the concern was found"]
    return []


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
        problems.append(f"finding {name}: disposition is open, Fixed, New or OutOfScope")
    if is_ruled_out_without_a_rule(finding):
        problems.append(f"finding {name}: an OutOfScope finding needs scopeRule, the Guardrails or notes.md line "
                        "that rules it out, quoted")
    if "commentId" in finding and not isinstance(finding["commentId"], int):
        problems.append(f"finding {name}: commentId is not a number")
    return problems + reasoning_problems(finding, name)


def is_ruled_out_without_a_rule(finding):
    return finding.get("disposition") == OUT_OF_SCOPE and not is_text(finding.get("scopeRule"))


REASONING_FIELDS = {"why": "why it matters", "fixShows": "what a correct fix must show"}


def reasoning_problems(finding, name):
    """why and fixShows: required on a finding about to be posted, and text wherever they appear."""
    problems = []
    for field, meaning in REASONING_FIELDS.items():
        if field in finding and not is_text(finding[field]):
            problems.append(f"finding {name}: {field} is not text")
        elif field not in finding and is_to_be_posted(finding):
            problems.append(f"finding {name}: {field} ({meaning}) is missing")
    return problems


def is_text(value):
    return isinstance(value, str) and bool(value.strip())


def is_to_be_posted(finding):
    """An unsettled finding with no comment and no body line on the PR yet."""
    return finding.get("disposition") in UNSETTLED and "commentId" not in finding and "reviewId" not in finding


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
    if finding.get("why"):
        parts.append(f"Why it matters: {finding['why']}")
    if finding.get("fix"):
        parts.append(f"Recommended fix: {finding['fix']}")
    if finding.get("fixShows"):
        parts.append(f"A correct fix shows: {finding['fixShows']}")
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


def advisory_lines(advisories, ruled_out=()):
    """The body's tail, under the advisory marker: each recurring concern and the lens it suggests,
    each break seen beyond the round's reach or outside the PR's scope, then the findings ruled out of
    scope this round. Each heading follows a blank line, so Markdown starts it as its own paragraph."""
    if not (advisories or ruled_out):
        return []
    return (["", ADVISORY_MARKER] + recurring_concern_lines(advisories) + outside_break_lines(advisories)
            + ruled_out_lines(ruled_out))


def recurring_concern_lines(advisories):
    recurring = [advisory for advisory in advisories if is_recurring_concern(advisory)]
    if not recurring:
        return []
    return ["", "Advisory: concerns found more than twice, which could become lenses"] + [
        f"- **{advisory['lens']}**: {advisory['concern']} (seen {len(advisory['seen'])} times: {', '.join(advisory['seen'])})"
        for advisory in recurring]


def outside_break_lines(advisories):
    outside = [advisory for advisory in advisories if is_outside_break(advisory)]
    if not outside:
        return []
    return ["", "Advisory: breaks beyond this round's reach or the PR's scope; not findings, nothing to answer"] + [
        f"- {advisory['concern']} (at {', '.join(advisory['seen'])})" for advisory in outside]


def ruled_out_lines(ruled_out):
    if not ruled_out:
        return []
    return ["", "Ruled out of scope this round; their threads need no answer"] + [
        f"- **{finding['key']}**: {finding['scopeRule']}" for finding in ruled_out]


def ruled_out_this_round(record, previous):
    """The findings that are OutOfScope in record and were not in the previous round's record."""
    already = {finding["key"] for finding in (previous or {}).get("findings", []) if finding["disposition"] == OUT_OF_SCOPE}
    return [finding for finding in record["findings"]
            if finding["disposition"] == OUT_OF_SCOPE and finding["key"] not in already]


def review_payload(round_number, head, inline, in_body, advisories=(), ruled_out=()):
    """One review: the inline findings as comments, the findings whose file isn't in the PR as
    lines of its body, then the advisory and the findings ruled out of scope this round."""
    lines = [f"Review round {round_number}: {len(inline) + len(in_body)} finding(s) in this review."]
    return {
        "commit_id": head,
        "event": "COMMENT",
        "body": "\n".join(lines + [body_line(finding) for finding in in_body] + advisory_lines(advisories, ruled_out)),
        "comments": [inline_comment(finding) for finding in inline],
    }


def comment_ids(posted_comments):
    """{finding key: comment id} from the comments GitHub holds for the review."""
    ids = {}
    for comment in posted_comments:
        found = MARKER.search(comment.get("body") or "")
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


PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def render(template, values):
    """The template with each {{NAME}} of values replaced, in one pass: a value's own text (a ticket's
    section) is never read for placeholders."""
    return PLACEHOLDER.sub(lambda found: str(values.get(found.group(1), found.group(0))), template)


def rebase_scope(files):
    if not files:
        return ""
    return ("The last reviewed head can't start this round's range (a rebase, or a merge since), so the call names "
            "the whole PR followed by the files not settled. Review only those files; nothing else is "
            "in this round.\n\n")


def markdown_section(text, name):
    """The body of the `## name` section of a Markdown text, up to the next `## `; "" when absent."""
    found = re.search(rf"^## {re.escape(name)}\s*$(.*?)(?=^## |\Z)", text or "", re.M | re.S)
    return found.group(1).strip() if found else ""


def selected_lenses(ticket_text):
    """The [x] lines of the ticket's `## Lenses`, each with its note."""
    return [line.strip() for line in markdown_section(ticket_text, "Lenses").splitlines()
            if SELECTED_LENS.match(line.strip())]


def lens_rows(writer_texts):
    """{lens id: its row of the lens table}, the later text's row winning: the repo's
    docs/agents/technical-writer.md adds lenses to the global outline's, or overrides them."""
    rows = {}
    for text in writer_texts:
        rows.update({found.group(1): found.group(0).strip() for found in LENS_ROW.finditer(text or "")})
    return rows


def lens_table(rows, ids):
    """The rows of the lens ids, and a line naming the ids no writer table has a question for."""
    if not ids:
        return "(no lens table found)"
    table = "\n".join(["| Lens | Question |", "|---|---|"] + [rows[lens] for lens in ids if lens in rows])
    unknown = [lens for lens in ids if lens not in rows]
    if unknown:
        table += f"\n\nNo question found for {', '.join(unknown)}: no lens table row names it; say so in the log."
    return table


def lens_brief(ticket, ticket_text, writer_texts):
    """What request.md says about the PR's lenses: the ticket's selected lenses with what each asks,
    or, when they can't be read here, where to find them and every lens to fall back on."""
    rows = lens_rows(writer_texts)
    every = sorted(rows, key=lambda lens: int(lens[1:]))
    if ticket and is_web_url(ticket):
        return (f"The issue is in Jira, {ticket}. Its selected lenses are the `[x]` lines of its description's "
                "`## Lenses` section; read them there. If you can't, review against every lens below and say so "
                f"in the log.\n\n{lens_table(rows, every)}")
    if ticket_text is None:
        where = f"The ticket `{ticket}` can't be read" if ticket else "No ticket was named"
        return f"{where}, so review against every lens below, and say so in the log.\n\n{lens_table(rows, every)}"
    selected = selected_lenses(ticket_text)
    if not selected:
        return f"The ticket `{ticket}` selects no lens: the fan-out has no lens legs."
    ids = [SELECTED_LENS.match(line).group(1) for line in selected]
    return (f"The ticket `{ticket}` selects these, each with what it requires of the work:\n\n"
            + "\n".join(selected) + f"\n\nWhat each asks:\n\n{lens_table(rows, ids)}")


def scope_brief(ticket, ticket_text):
    """What request.md says about the PR's declared scope: the ticket's Task and Guardrails, or, when
    they can't be read here, where to find them."""
    if ticket and is_web_url(ticket):
        return (f"The issue is in Jira, {ticket}. Its scope is its description's `## Task` and `## Guardrails` "
                "sections; read them there. If you can't, say so in the log: then no ticket line rules a finding "
                "out of scope.")
    if ticket_text is None:
        where = f"The ticket `{ticket}` can't be read" if ticket else "No ticket was named"
        return f"{where}, so no ticket line rules a finding out of scope; say so in the log."
    return (f"The ticket `{ticket}` declares:\n\n### Task\n\n{ticket_section(ticket_text, 'Task')}"
            f"\n\n### Guardrails\n\n{ticket_section(ticket_text, 'Guardrails')}")


def ticket_section(ticket_text, name):
    return markdown_section(ticket_text, name) or "(none)"


def notes_path(rounds):
    """The author's notes.md, beside the rounds folder: one file, edited whenever the author likes, that
    the reviewer reads at the start of every review."""
    return rounds.parent / AUTHOR_NOTES


def is_web_url(text):
    return text.startswith(("http://", "https://"))


def file_lines(paths, current):
    """The files to read, one per line; one the PR deletes, or no longer changes, is marked."""
    marked = [f"- {path}" + (f' (deleted, or no longer in the PR; its disposition\'s blob is "{ABSENT}")' if current.get(path) == ABSENT else "")
              for path in paths]
    return "\n".join(marked) or "(none)"


def finding_line(finding):
    """One unsettled finding for the round's prompt: its summary, then each part of its reasoning it
    carries (a record written before why and fixShows has neither)."""
    parts = [f"- {finding['key']} [{finding['disposition']}]: {finding['summary']}"]
    for label, field in (("Why it matters", "why"), ("Recommended fix", "fix"), ("A correct fix shows", "fixShows")):
        if finding.get(field):
            parts.append(f"{label}: {finding[field]}")
    return "\n  ".join(parts)


def finding_lines(findings):
    return "\n".join(finding_line(finding) for finding in findings) or "(none)"


def ok_lines(record):
    settled = ok_files(record)
    return "\n".join(f"- {path} {blob}" for path, blob in sorted(settled.items())) or "(none)"


# --- the round folders of .tmp/<KEY>/rounds/ ---


AUTHOR_NOTES = "notes.md"


def rounds_folder(root, key):
    """Where an issue's rounds are. The one place this path is written, but for the plan window's
    roundRequestPath() (vscode/plan-window/plan.js), whose test checks the two agree."""
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


def round_state(rounds):
    """(last record or None, unfinished round or None), the rounds folder listed once: what a caller
    that polls many issues needs."""
    numbers = round_numbers(rounds)
    return last_record(rounds, numbers)[1], unfinished_round(rounds, numbers)


def last_record(rounds, numbers=None):
    """(round number, record) of the highest round whose review.json has a head, else (None, None).
    numbers are the rounds' numbers when the caller has listed them."""
    for number in reversed(round_numbers(rounds) if numbers is None else numbers):
        try:
            record = read_record(round_dir(rounds, number) / "review.json")
        except ReviewError:
            continue
        if isinstance(record, dict) and record.get("head"):
            return number, record
    return None, None


def withdrawn_round(rounds):
    """{"round"} of the highest round when its folder holds neither a request.json nor a review.json:
    its request was withdrawn because its review never started. The next request writes that round
    again in its own folder, so a restarted round keeps what it had posted (posted.json) and the round
    numbers don't skip. Round 0 is the author's and is never one."""
    numbers = round_numbers(rounds)
    if not numbers or numbers[-1] == 0:
        return None
    folder = round_dir(rounds, numbers[-1])
    if (folder / "request.json").exists() or (folder / "review.json").exists():
        return None
    return {"round": numbers[-1]}


def unfinished_round(rounds, numbers=None):
    """{"round", "head"} of the highest round with a request.json and no review.json, or None. A
    request.json that is unreadable leaves the head None. numbers are the rounds' numbers when the
    caller has listed them."""
    for number in reversed(round_numbers(rounds) if numbers is None else numbers):
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

    def merges_since(self, sha, head):
        """Whether a merge commit is among the commits head has and sha hasn't."""
        return bool(git("rev-list", "--merges", "-n", "1", f"{sha}..{head}"))

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
        return git("-c", "core.quotePath=false", "diff", "--no-ext-diff", "--no-color", f"origin/{base}...{head}")

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
        return self.all_pages(f"repos/{{owner}}/{{repo}}/pulls/{pull_request}/reviews/{review_id}/comments")


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


# What request.md says from the ticket: its lenses (lens_brief) and its declared scope (scope_brief).
TicketBriefs = namedtuple("TicketBriefs", "lenses scope", defaults=("", ""))

# path is None when there is no request to review. token and round name the request at path, which
# the plan window's review link carries; written says this call wrote it, so only it may withdraw it
# (a reopened request is someone else's).
Outcome = namedtuple("Outcome", "path note token round written", defaults=(None, None, False))


def request(key, sources, restart=False, prompts=PROMPTS, if_due=False, ticket=None):
    """Writes request.md for the next review, or says why it needs none.

    An unfinished review for this HEAD is reopened as it is, its files untouched. One for another
    head is refused unless restart, which writes it again in its own folder at HEAD. With none
    unfinished and no review due, nothing is written. After a rebase with nothing left to review,
    the last record moves to HEAD and nothing is written. ticket is where the issue's lenses and
    scope are: a store issue's ticket file, a Jira issue's page, or None."""
    root = sources.root()
    rounds = rounds_folder(root, key)
    briefs = ticket_briefs(ticket, root)
    with review_lock(rounds, "request"):
        return write_request(key, root, rounds, sources, restart, prompts, if_due, briefs)


def ticket_briefs(ticket, root):
    text = ticket_text(ticket)
    writers = [read_or_none(GLOBAL_WRITER), read_or_none(root / REPO_WRITER)]
    return TicketBriefs(lens_brief(ticket, text, writers), scope_brief(ticket, text))


def ticket_text(ticket):
    """A ticket file's text; None for a Jira page, no ticket, or a file that can't be read."""
    return None if not ticket or is_web_url(ticket) else read_or_none(Path(ticket))


def read_or_none(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def live_sessions_by_pid(sources):
    return {session["pid"]: session for session in sources.claude_sessions() if "pid" in session}


def refuse_while_reviewer_runs(unfinished, key, sources, command):
    """A restart rewrites the request a claimed reviewer is working from, and a reopen's link closes
    that reviewer's terminal, so both wait for it to close; command is the one to run again then. A
    reviewer that never claimed, or whose pid is gone, doesn't hold the round."""
    reviewer = unfinished.get("reviewer")
    if not reviewer:
        return
    try:
        live = reviewer["pid"] in live_sessions_by_pid(sources)
    except ReviewError as error:
        raise ReviewError(f"can't tell whether round {unfinished['round']}'s reviewer (pid {reviewer['pid']}) "
                          f"is still running: {error}; run {command} again when it works") from error
    if live:
        raise ReviewError(f"round {unfinished['round']}'s reviewer (pid {reviewer['pid']}) is still running; "
                          f"close its Review {key} terminal, then run {command}")


def drop_gone_reviewer(folder, unfinished):
    """Removes the reviewer from request.json once refuse_while_reviewer_runs has passed it: its
    session is gone, so the reopen's link must not meet the plan window's pid check on a pid some
    other process now holds. Only a reviewer this script considers live stays on request.json."""
    if not unfinished.get("reviewer"):
        return
    stored = json.loads((folder / "request.json").read_text())
    stored.pop("reviewer", None)
    write_atomically(folder / "request.json", json.dumps(stored) + "\n")
    append_log(folder, f"reviewer (pid {unfinished['reviewer'].get('pid')}) is not a Claude session: dropped from the request")


def write_request(key, root, rounds, sources, restart, prompts, if_due=False, briefs=TicketBriefs()):
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
            refuse_while_reviewer_runs(unfinished, key, sources, f"plan review {key}")
            drop_gone_reviewer(folder, unfinished)
            append_log(folder, f"reopened: the request for {head} is unchanged")
            return Outcome(folder / "request.md", f"round {unfinished['round']}'s request reopened",
                           unfinished.get("token"), unfinished["round"])
        if not same_head and not restart:
            raise ReviewError(
                f"round {unfinished['round']}'s review was requested for HEAD {str(unfinished.get('head'))[:8]}, "
                f"and HEAD is {head[:8]}; run plan review {key} --restart to restart it in its own folder")
        refuse_while_reviewer_runs(unfinished, key, sources, f"plan review {key} --restart")
        append_log(folder, f"restarted: its request is written again at {head}")
    elif not review_due(record, head):
        return Outcome(None, not_due_reason(key, record, last_number, head, None))
    reopened = unfinished or withdrawn_round(rounds)
    if reopened and not unfinished:
        append_log(round_dir(rounds, reopened["round"]), f"rewritten: its withdrawn request is written again at {head}")
    round_number = review_round(round_numbers(rounds), reopened)
    folder = round_dir(rounds, round_number)
    number, base = sources.pull_request()
    bounded = last_head_bounds_range(record, head, sources)
    range_ = diff_range(base, record, head, bounded)
    blobs, deleted = sources.changed(range_, head)
    current = current_state(blobs, deleted)
    rebased = record is not None and not bounded
    if rebased:
        current.update({path: ABSENT for path in absent_paths(record, current)})
    only_files = rebased_files(record, current) if rebased else None
    if rebased and not only_files:
        settle_without_review(rounds, last_number, record, reopened, head)
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
        "REVIEW_SCRIPT": shlex.quote(str(Path(__file__).resolve())),
        "LOG_ARG": shlex.quote(str(log_path(folder))),
        "FILES": file_lines(only_files or files_to_read(record, current), current),
        "UNSETTLED": finding_lines(unsettled_findings(record)),
        "OK_FILES": ok_lines(record),
        "LENSES": briefs.lenses,
        "SCOPE_RULES": briefs.scope,
        "NOTES": notes_path(rounds),
    }
    text = render((prompts / choose_prompt(record)).read_text(), values)
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    stored = {"round": round_number, "head": head, "pr": number, "range": range_, "call": call, "token": token}
    write_atomically(folder / "request.json", json.dumps(stored) + "\n")
    write_atomically(folder / "request.md", text)
    append_log(folder, f"requested: reviewing {range_} at {head}")
    return Outcome(folder / "request.md", f"round {round_number}'s request written", token, round_number, written=True)


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
        ruled_out = ruled_out_this_round(record, last_record(rounds)[1])
        record = post_findings(record, round_number, folder, pull_request, sources, ruled_out)
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


def read_posted(folder):
    """What earlier runs of `finish` for this round had posted: {"reviews": [{"id", "keys"}],
    "comments": {key: comment id}, "bodies": {key: review id}, "intent": the POST in flight or None,
    "advisedHead": the head a review carrying the round's advisories was posted at, or None}. A round
    restarted at a new head keeps it, so what was posted before isn't posted again, and its advisories
    are posted when that head differs. A posted.json from before advisedHead has "advised": true and no
    advisedHead; it reads as advised at no particular head, so the advisories are posted once more."""
    try:
        posted = json.loads((folder / "posted.json").read_text())
    except FileNotFoundError:
        posted = {}
    except ValueError as error:
        raise ReviewError(f"{folder / 'posted.json'} is not valid JSON: {error}") from error
    return {"comments": {}, "bodies": {}, "reviews": [], "missing": {}, "intent": None, "advisedHead": None, **posted}


def post_findings(record, round_number, folder, pull_request, sources, ruled_out=()):
    """Posts what isn't posted yet, writing posted.json after each POST that succeeds, so a rerun
    after a failure, or the finish of a restarted round, posts only what is left. A round restarted
    after a review was posted posts its new findings in a further review. A finding ruled out of scope this
    round (ruled_out) gets no comment; the review's body lists it, with the advisories. pull_request() is
    the PR's number, fetched once."""
    posted = read_posted(folder)
    settle_intent(posted, folder, round_number, record["head"], pull_request, sources)
    todo = findings_to_post(record, posted)
    placed = placements(todo, diff_lines(sources.pr_diff(record["base"], record["head"]))) if todo else placements([], {})
    advisories = advisories_to_post(record, posted)
    listed_out = [] if advised_at(posted, record["head"]) else list(ruled_out)
    has_closing = bool(advisories or listed_out)
    if placed["inline"] or placed["body"] or has_closing:
        inline_keys, body_keys = [f["key"] for f in placed["inline"]], [f["key"] for f in placed["body"]]
        intend(posted, folder, {"kind": "review", "inline": inline_keys, "body": body_keys, "advisory": has_closing})
        review_id = post_review(record, round_number, placed, (advisories, listed_out), pull_request(), sources)
        record_review(posted, review_id, inline_keys, body_keys, record["head"] if has_closing else None)
        save_posted(folder, posted)
    collect_inline_comment_ids(posted, folder, pull_request, sources, {f["key"] for f in unsettled_findings(record)})
    try:
        for finding in placed["file"]:
            intend(posted, folder, {"kind": "file", "key": finding["key"]})
            posted["comments"][finding["key"]] = post_file_comment(record["head"], finding, pull_request(), sources)
            posted["intent"] = None
            save_posted(folder, posted)
    except ReviewError as error:
        reviews = posted["reviews"]
        raise ReviewError(f"{error} (review {reviews[-1]['id']} had been posted)" if reviews else str(error)) from error
    return with_posted_ids(record, posted["comments"], posted["bodies"])


def advisories_to_post(record, posted):
    """The record's advisories, unless a review at this head already carried them."""
    return [] if advised_at(posted, record["head"]) else record.get("advisories", [])


def advised_at(posted, head):
    return posted["advisedHead"] is not None and posted["advisedHead"] == head


def intend(posted, folder, intent):
    """Writes what is about to be POSTed before it is sent, so a rerun after a POST whose outcome
    is unknown can look for it on the PR instead of sending it again."""
    posted["intent"] = intent
    save_posted(folder, posted)


def record_review(posted, review_id, inline_keys, body_keys, advised_head=None):
    """Records a posted review; advised_head is the head it carried the advisories at, None when it
    carried none."""
    posted["reviews"].append({"id": review_id, "keys": inline_keys})
    posted["bodies"].update({key: review_id for key in body_keys})
    if advised_head:
        posted["advisedHead"] = advised_head
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
            record_review(posted, review_id, intent["inline"], intent["body"], head if intent.get("advisory") else None)
    else:
        comment_id = remote_file_comment_id(posted, intent["key"], head, pull_request(), sources)
        if comment_id:
            posted["comments"][intent["key"]] = comment_id
    posted["intent"] = None
    save_posted(folder, posted)


def remote_review_id(posted, intent, round_number, head, pull_request, sources):
    """The id of an unrecorded review of this round and head on the PR that carries every key of
    the intent, in its body or its comments, and the advisory when the intent had one; None when
    there is none."""
    wanted = set(intent["inline"]) | set(intent["body"])
    known = {review["id"] for review in posted["reviews"]} | set(posted["bodies"].values())
    for candidate in sources.reviews(pull_request):
        if candidate["id"] in known or candidate.get("commit_id") != head:
            continue
        if not (candidate.get("body") or "").startswith(f"Review round {round_number}:"):
            continue
        carried = marker_keys(candidate.get("body")) | {
            key for comment in sources.review_comments(pull_request, candidate["id"]) for key in marker_keys(comment.get("body"))}
        if wanted <= carried and carries_advisory(candidate, intent):
            return candidate["id"]
    return None


def carries_advisory(candidate, intent):
    """Whether a review on the PR carries the advisory the intent had; true when the intent had none."""
    return not intent.get("advisory") or ADVISORY_MARKER in (candidate.get("body") or "")


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


def post_review(record, round_number, placed, closing, pull_request, sources):
    """Posts the inline findings, the body's, the advisory and the ruled-out findings as one review;
    closing is (advisories, ruled_out). Returns its id."""
    endpoint = f"repos/{{owner}}/{{repo}}/pulls/{pull_request}/reviews"
    payload = review_payload(round_number, record["head"], placed["inline"], placed["body"], *closing)
    return posted_id(sources.gh_api(endpoint, payload))


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
    commands.choices["request"].add_argument("--ticket", help="the issue's ticket file, or its Jira page")
    args = parser.parse_args(argv)
    if not KEY_PATTERN.match(args.key):
        parser.error(f"{args.key!r} is not a Jira key (KEY-123)")
    sources = sources or Sources()
    try:
        if args.command == "request":
            outcome = request(args.key, sources, args.restart, if_due=args.if_due, ticket=args.ticket)
            print(json.dumps({"path": str(outcome.path) if outcome.path else None, "note": outcome.note,
                              "token": outcome.token, "round": outcome.round, "written": outcome.written}))
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
