#!/usr/bin/env python3
"""Claude's review of a PR, kept in the issue's latest round folder, .tmp/<KEY>/rounds/<n>/, of its worktree.

    review.py request KEY --claude-state working|stopped   # write request.md for the next review
    review.py finish KEY                                    # post review.next.json, then review.json
    review.py due KEY                                       # exit 0 when HEAD needs a review, 1 when not

`pr` runs `plan review KEY`, which runs `request` and opens a Claude Code tab with the line that
starts the review. The Claude reads request.md, reviews, appends progress to the round's log, writes
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
    request.json       {"round", "head", "range", "call"} of that request; `finish` deletes it
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
doesn't lose its work. `request` restarts an unfinished review in its own folder, after saying in
its log that it was stopped, unless the issue's Claude is still working on it.

A review is due when HEAD differs from the last review's head, or there is none, unless an
unfinished review was already requested for this HEAD. `due` answers that from the files alone, so a
`pr` that stopped after its push reviews those commits on its next run.

The folder is in the worktree root (`git rev-parse --show-toplevel`), whatever directory this runs in.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
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


def diff_range(base, record, head_reachable=True):
    """What to review: the PR against its base, or what changed since the last reviewed head.

    A recorded head that is no longer in HEAD's history (a rebase or force-push) can't bound a
    range, so the whole PR is reviewed again, minus the OK files whose blob is unchanged.
    """
    if record is None or not head_reachable:
        return f"origin/{base}...HEAD"
    return f"{record['head']}..HEAD"


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
    findings_files = {parse_key(finding["key"])[0] for finding in unsettled_findings(record)}
    return sorted(set(files_to_read(record, blobs)) | findings_files)


def unsettled_findings(record):
    return [finding for finding in (record or {}).get("findings", []) if finding["disposition"] in UNSETTLED]


def ok_files(record):
    """{path: blob} of the files the last round found OK."""
    return {entry["path"]: entry["blob"] for entry in (record or {}).get("dispositions", []) if entry["verdict"] == "OK"}


def files_to_read(record, current_blobs):
    """The changed files, {path: blob now}, to read: not an OK file whose blob is unchanged."""
    settled = ok_files(record)
    return sorted(path for path, blob in current_blobs.items() if settled.get(path) != blob)


def review_round(numbers, unfinished):
    """The round a review opens: its own folder when one is unfinished, else the latest round plus
    one, and at least 1 (round 0 is the author's, whether or not its folder exists). numbers are
    the existing rounds, lowest first."""
    if unfinished:
        return unfinished["round"]
    return max(1, numbers[-1] + 1) if numbers else 1


def is_orange(record, head, round_unfinished):
    """A finished review of this head with unsettled findings waits for a person."""
    return bool(record) and record.get("head") == head and not round_unfinished and bool(unsettled_findings(record))


def claim_is_working(claude_state):
    return claude_state == "working"


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
        problems.append(f"head is {record.get('head')!r}, the review was requested for {head}")
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


def findings_to_post(record):
    """The unsettled findings with no comment, and none in a review's body, on the PR yet."""
    return [f for f in unsettled_findings(record) if not (f.get("commentId") or f.get("reviewId"))]


def review_due(record, head, requested_head=None):
    """Whether HEAD needs a review: no record, or a record of another head, and no unfinished round
    already requested for this HEAD (requested_head is None when no round is unfinished)."""
    if requested_head == head:
        return False
    return record is None or record.get("head") != head


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
DIFF_HEADER = "diff --git "


def diff_path(header):
    """The new path of a `diff --git a/X b/Y` line, or None when it can't be split."""
    _, found, path = header[len(DIFF_HEADER):].rpartition(" b/")
    return path if found else None


def diff_lines(diff_text):
    """{path: set of new-file line numbers the diff shows}, from a unified diff.

    Every file the diff names is in the PR: one changed without hunks (a pure rename, a binary
    change) maps to an empty set. A deleted file is left out, having no new lines to comment on.
    Between a `diff --git` line and its first hunk the lines are headers; inside a hunk every line
    is content, so a removed `-- x` beside an added `++ b/foo` is not read as a file header.
    """
    shown, path, in_hunk = {}, None, False
    for line in diff_text.splitlines():
        if line.startswith(DIFF_HEADER):
            path, in_hunk = diff_path(line), False
            if path:
                shown.setdefault(path, set())
        elif path and (found := HUNK.match(line)):
            in_hunk = True
            first, count = int(found.group(1)), int(found.group(2) or 1)
            shown[path].update(range(first, first + count))
        elif path and not in_hunk and (line.startswith("deleted file mode") or line == "+++ /dev/null"):
            del shown[path]
            path = None
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


def marker(key):
    return f"<!-- review-finding: {key} -->"


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


def finding_lines(findings):
    if not findings:
        return "(none)"
    return "\n".join(
        f"- {f['key']} [{f['disposition']}]: {f['summary']}" + (f" Recommended fix: {f['fix']}" if f.get("fix") else "")
        for f in findings
    )


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

    def reachable(self, sha):
        return subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], capture_output=True).returncode == 0

    def blobs(self, range_):
        """{path: blob sha at HEAD} of the files the range changes; a deleted file has none."""
        names = git("diff", "--name-only", range_).splitlines()
        return {name: blob for name in names if (blob := self.blob(name))}

    def pr_diff(self, base, head):
        """The PR's unified diff as of head, the one GitHub comments against."""
        return git("diff", f"origin/{base}...{head}")

    def blob(self, path):
        answer = subprocess.run(["git", "rev-parse", f"HEAD:{path}"], capture_output=True, text=True)
        return answer.stdout.strip() if answer.returncode == 0 else None

    def gh_api(self, endpoint, payload=None):
        """The parsed JSON of a gh api call; payload, when given, is POSTed as the body."""
        command = ["gh", "api", endpoint] + (["--method", "POST", "--input", "-"] if payload else [])
        try:
            return json.loads(run(command, json.dumps(payload) if payload else None))
        except ValueError as error:
            raise ReviewError(f"gh api {endpoint} gave no JSON: {error}") from error


    def review_comments(self, pull_request, review_id):
        """Every comment of a review: gh api pages by 30 unless told to fetch them all."""
        endpoint = f"repos/{{owner}}/{{repo}}/pulls/{pull_request}/reviews/{review_id}/comments"
        try:
            return flatten_pages(json.loads(run(["gh", "api", "--paginate", "--slurp", endpoint])))
        except ValueError as error:
            raise ReviewError(f"gh api {endpoint} gave no JSON: {error}") from error


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


# --- request ---


def request(key, claude_state, sources, prompts=PROMPTS):
    """Writes request.md for the next review and returns its path. After a rebase with nothing left
    to review, it moves the last record to HEAD and returns None."""
    root = sources.root()
    rounds = rounds_folder(root, key)
    last_number, record = last_record(rounds)
    unfinished = unfinished_round(rounds)
    if unfinished:
        if claim_is_working(claude_state):
            raise ReviewError(f"round {unfinished['round']}'s review is still running in the issue's Claude; no second review started")
        append_log(round_dir(rounds, unfinished["round"]), "stopped: its Claude is idle and the review never finished; it starts again from the last review")
    round_number = review_round(round_numbers(rounds), unfinished)
    folder = round_dir(rounds, round_number)
    number, base = sources.pull_request()
    reachable = record is None or sources.reachable(record["head"])
    range_ = diff_range(base, record, reachable)
    blobs = sources.blobs(range_)
    head = sources.head()
    rebased = record is not None and not reachable
    only_files = rebased_files(record, blobs) if rebased else None
    if rebased and not only_files:
        write_atomically(round_dir(rounds, last_number) / "review.json", json.dumps({**record, "head": head}, indent=2) + "\n")
        return None
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
        "FILES": "\n".join(f"- {path}" for path in (only_files or files_to_read(record, blobs))) or "(none)",
        "UNSETTLED": finding_lines(unsettled_findings(record)),
        "OK_FILES": ok_lines(record),
    }
    text = render((prompts / choose_prompt(record)).read_text(), values)
    folder.mkdir(parents=True, exist_ok=True)
    request = {"round": round_number, "head": head, "range": range_, "call": call}
    write_atomically(folder / "request.json", json.dumps(request) + "\n")
    write_atomically(folder / "request.md", text)
    append_log(folder, f"requested: reviewing {range_} at {head}")
    return folder / "request.md"


# --- finish ---


def finish(key, sources):
    """Posts the review's findings as one PR review, then makes review.next.json the review.json.

    The record is checked against the head the review was requested for, which is also the commit
    the review is posted on, so a push made while it ran doesn't refuse its work."""
    rounds = rounds_folder(sources.root(), key)
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
        record = post_findings(record, round_number, sources)
        write_atomically(folder / "review.json", json.dumps(record, indent=2) + "\n")
    except (ReviewError, OSError) as error:
        append_log(folder, f"failed: {error}; review.json is not written")
        raise
    for name in ("request.md", "request.json", "review.next.json"):
        (folder / name).unlink(missing_ok=True)
    append_log(folder, f"finished: head {record['head']}, {len(unsettled_findings(record))} unsettled")
    return record


def is_due(key, sources):
    rounds = rounds_folder(sources.root(), key)
    unfinished = unfinished_round(rounds)
    _, record = last_record(rounds)
    return review_due(record, sources.head(), unfinished and unfinished["head"])


def post_findings(record, round_number, sources):
    findings = findings_to_post(record)
    if not findings:
        return record
    placed = placements(findings, diff_lines(sources.pr_diff(record["base"], record["head"])))
    comment_ids_by_key, review_ids_by_key, review_id = {}, {}, None
    if placed["inline"] or placed["body"]:
        review_id, comment_ids_by_key = post_review(record, round_number, placed, sources)
        review_ids_by_key = {finding["key"]: review_id for finding in placed["body"]}
    try:
        for finding in placed["file"]:
            comment_ids_by_key[finding["key"]] = post_file_comment(record["head"], finding, sources)
    except ReviewError as error:
        raise ReviewError(f"{error} (review {review_id} had been posted)" if review_id else str(error)) from error
    return with_posted_ids(record, comment_ids_by_key, review_ids_by_key)


def post_review(record, round_number, placed, sources):
    """Posts the inline findings and the body's as one review: (review id, {key: comment id})."""
    pull_request, _ = sources.pull_request()
    endpoint = f"repos/{{owner}}/{{repo}}/pulls/{pull_request}/reviews"
    posted = sources.gh_api(endpoint, review_payload(round_number, record["head"], placed["inline"], placed["body"]))
    review_id = posted_id(posted)
    ids = comment_ids(sources.review_comments(pull_request, review_id)) if placed["inline"] else {}
    missing = [finding["key"] for finding in placed["inline"] if finding["key"] not in ids]
    if missing:
        raise ReviewError(f"review {review_id} was posted but GitHub returned no comment for {', '.join(missing)}")
    return review_id, ids


def post_file_comment(head, finding, sources):
    pull_request, _ = sources.pull_request()
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
    for name in ("request", "finish", "due"):
        command = commands.add_parser(name)
        command.add_argument("key")
    commands.choices["request"].add_argument("--claude-state", choices=("working", "stopped"), required=True)
    args = parser.parse_args(argv)
    if not KEY_PATTERN.match(args.key):
        parser.error(f"{args.key!r} is not a Jira key (KEY-123)")
    sources = sources or Sources()
    try:
        if args.command == "request":
            written = request(args.key, args.claude_state, sources)
            if written:
                print(written)
            else:
                print("review: nothing to review; the record now names HEAD", file=sys.stderr)
        elif args.command == "due":
            sys.exit(0 if is_due(args.key, sources) else 1)
        else:
            finish(args.key, sources)
            print("review: recorded in the round's review.json")
    except ReviewError as error:
        print(f"review: {error}", file=sys.stderr)
        sys.exit(2)  # `due` answers 0 or 1, so a failure is neither


if __name__ == "__main__":
    main()
