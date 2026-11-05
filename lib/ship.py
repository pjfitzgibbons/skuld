# Shipping an issue: what Ship checks before running `pr`, and the PR it puts on the issue once pr
# succeeds.
# Ported from lib/ship.js. Faithful, behavior-preserving port: no change in logic.
import json
import os
import re
from urllib.parse import urlparse

from .start import git, run

# A round's files for pr, in .tmp/<KEY>/: beside the commit message and PR body, the review replies
# pr posts, one <comment id>.md each, and issue.md, the issue's description with the boxes pr
# ticks. A reply's name is digits, as pr_threads.py reads it. The key is a Jira key, CIAM-123, not
# Redmine's bare issue number: it names the whole folder, so .tmp/CIAM-123/ replaces .tmp/<n>/.
ROUND = r"(?:commit\.txt|pr-body\.md|issue\.md|replies/[0-9]+\.md)"
REVIEW_FILE = re.compile(rf"^(.+)/\.tmp/([A-Z][A-Z0-9]*-[0-9]+)/{ROUND}$")
ROUND_FILE = re.compile(rf"^{ROUND}$")


# The issue and its checkout from a round file's path, <root>/.tmp/<KEY>/<round file>; None for any
# other file. Field is named key, not id: a Jira key is a string (CIAM-123), never a number, and
# calling it id invites someone to int() it the way the Redmine version did.
def reviewFile(file):
    m = REVIEW_FILE.match(file)
    return {"root": m.group(1), "key": m.group(2)} if m else None


def isRoundFile(file, dir):
    rel = os.path.relpath(file, dir)
    return bool(ROUND_FILE.match(rel))


def isFile(file):
    return os.path.isfile(file)


def _parseIntLeading(s):
    m = re.match(r"^\s*[+-]?\d+", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


# What pr has yet to send from .tmp/<KEY>/ (dir): issue.md and the edits keepEdits saved beside it,
# and every file in replies/ but dotfiles such as .DS_Store, since pr deletes each reply it sends
# and refuses or skips a misnamed one. A folder in replies/ is never sent, so it's left alone.
# Replies are in comment id order.
def unsent(dir):
    def listdir(d):
        return os.listdir(d) if os.path.isdir(d) else []

    replies = os.path.join(dir, "replies")
    names = [n for n in listdir(replies) if not n.startswith(".")]

    def sort_value(name):
        v = _parseIntLeading(name)
        return v if v else float("inf")  # parseInt(a,10) || Infinity: 0 is falsy in JS too

    names.sort(key=lambda n: (sort_value(n), n))
    kept = sorted(n for n in listdir(dir) if re.match(r"^issue\.md\.unsent[0-9]*$", n))
    paths = [os.path.join(dir, "issue.md")] + [os.path.join(dir, n) for n in kept] + [os.path.join(replies, n) for n in names]
    return [p for p in paths if isFile(p)]


# Saves text, edits made to a round file after pr sent and deleted it, beside it as
# <file>.unsent, or .unsent2 and on if that's taken; never over a file. Returns the path. unsent()
# lists it, so Done keeps the worktree until it's dealt with.
def keepEdits(file, text):
    n = 1
    while True:
        kept = f"{file}.unsent{n if n > 1 else ''}"
        try:
            fd = os.open(kept, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(text)
            return kept
        except FileExistsError:
            n += 1


# What pr has yet to send from every issue's .tmp/<KEY>/ in root, as paths relative to root.
def unsentIn(root):
    tmp = os.path.join(root, ".tmp")
    if os.path.isdir(tmp):
        keys = [n for n in os.listdir(tmp) if re.match(r"^[A-Z][A-Z0-9]*-[0-9]+$", n)]
    else:
        keys = []
    result = []
    for key in keys:
        for f in unsent(os.path.join(tmp, key)):
            result.append(os.path.relpath(f, root))
    return result


# The round's files that exist, in the order they open, the PR body last so it's in front; none
# until the PR body and something else for pr are both written.
def roundFiles(dir):
    def at(name):
        return os.path.join(dir, name)

    if not isFile(at("pr-body.md")):
        return []
    files = [at("commit.txt"), *unsent(dir)]
    files = [f for f in files if isRoundFile(f, dir) and isFile(f)]
    return [*files, at("pr-body.md")] if files else []


# shipTab was extension.js's way of recognizing a VS Code tab Ship had opened, so it could decide
# whether a window was still showing Ship's round. The terminal front end has no tabs -- Ship opens
# nvim and lazygit in a tmux window instead, and there is nothing to recognize after the fact -- so
# this has no caller in the new tool. Kept only so a test ported from test/ship.test.js that still
# exercises it has something to call; drop it along with that test once the port is settled.
def shipTab(input, root, key):
    def inRoot(uri):
        return bool(uri and uri.get("fsPath")) and uri["fsPath"].startswith(f"{root}/")

    if not input:
        return False
    if input.get("uri"):
        return isRoundFile(input["uri"]["fsPath"], os.path.join(root, ".tmp", key))
    if input.get("modified"):
        return inRoot(input["modified"])
    if isinstance(input.get("textDiffs"), list):
        diffs = input["textDiffs"]
        return len(diffs) == 0 or any(inRoot(d.get("modified")) for d in diffs)
    return False


# What pr will find in root for issue key: its branch, whether the tree has changes, and each reason
# it can't run yet. .tmp/ is ignored, so the review files never count as changes.
def check(root, key):
    branch = git(root, "branch", "--show-current").strip()
    dirty = git(root, "status", "--porcelain").strip() != ""
    problems = []
    if not re.match(rf"^(?:[\w-]+/)?{re.escape(key)}-", branch):
        problems.append(f"the branch {branch or '(none)'} isn't {key}'s")
    if not os.path.exists(os.path.join(root, ".tmp", key, "pr-body.md")):
        problems.append(f".tmp/{key}/pr-body.md is missing")
    if dirty and not os.path.exists(os.path.join(root, ".tmp", key, "commit.txt")):
        problems.append(f"the tree has changes and .tmp/{key}/commit.txt is missing")
    # pr_threads.py would refuse these only after pr has pushed, or post two replies to one thread.
    dir = os.path.join(root, ".tmp", key)
    threads = {}
    for file in [f for f in unsent(dir) if f != os.path.join(dir, "issue.md")]:
        rel = os.path.relpath(file, dir)
        if not isRoundFile(file, dir):
            problems.append(f".tmp/{key}/{rel} isn't a reply pr can send; name it replies/<comment id>.md")
            continue
        with open(file, "r", encoding="utf8") as f:
            content = f.read().strip()
        if not content:
            problems.append(f".tmp/{key}/{rel} is empty")
        thread = _parseIntLeading(os.path.basename(rel))
        if thread in threads:
            problems.append(f".tmp/{key}/{threads[thread]} and {rel} both reply to comment {thread}")
        else:
            threads[thread] = rel
    return {"branch": branch, "dirty": dirty, "problems": problems}


# The PR for ref, a branch or a PR's URL, as gh reports it: fields, by default { url, number, title }.
def pullRequest(root, ref, fields="url,number,title"):
    return json.loads(run("gh", ["pr", "view", ref, "--json", fields], root))


# A PR as ghbody.sh's title convention links it: [<repo>#<n>](<url>). The PR title itself (ending
# `(CIAM-123)` rather than Redmine's `(fixes #n)`) is built in ghbody.sh, not here.
def prLink(pr):
    path_parts = urlparse(pr["url"]).path.split("/")
    return f'[{path_parts[2]}#{pr["number"]}]({pr["url"]})'


# Decision #4 drops Redmine's "Pull requests" custom field: Jira has no field for this, because its
# development panel already shows every PR whose branch name or commit carries the issue's key.
# There is nothing for Ship to write back to Jira once a PR exists, so linkPullRequest has no
# replacement here; Done finds the PR straight from the branch (lib/done.js's prFromBranch).
