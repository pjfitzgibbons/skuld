# `rebase-main [start|continue|abort|status] [--base <branch>]` (bin/rebase-main).
# Merges origin/<base> into the current branch with a merge commit, so every existing commit on the
# branch keeps its SHA, then pushes the branch when origin has it. The branch's own history never
# reaches the base (PRs squash-merge), so it needn't be linear. Never touches the base branch.
#
#   start     (default) refuses on the base branch, with uncommitted changes, mid-merge or mid-rebase;
#             fetches origin, records the branch's tip as refs/rebase-main/<branch>, merges and pushes.
#             A branch with no commits of its own is fast-forwarded to origin/<base> instead.
#   continue  after the conflicts are resolved and staged: refuses while any file still has
#             conflict markers or is unmerged, then concludes the merge and pushes
#   abort     puts the branch back as it was before start
#   status    the base commit being merged and the files in conflict
#
# It pushes only when origin's branch is at the tip the merge started from, so nothing unshipped
# goes out; a branch origin doesn't have isn't pushed, since pr creates it. The merge result is a
# descendant of origin's branch, so the push is never forced.
#
# Exit 0: merged (and pushed, if origin has the branch), or already holding the base's tip.
# 1: stopped on a conflict (status printed). 2: refused, nothing changed. 3: merged, not pushed.
import os
import subprocess
import sys
from pathlib import Path

COMMANDS = ("start", "continue", "abort", "status")
USAGE = "usage: rebase-main [start|continue|abort|status] [--base <branch>]"


class Exit(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def say(message):
    print(f"rebase-main: {message}", flush=True)


def complain(message, code):
    print(f"rebase-main: {message}", file=sys.stderr, flush=True)
    raise Exit(code)


def git(*args, capture=True):
    """The finished git process; git's own output goes to the terminal unless captured."""
    return subprocess.run(["git", *args], capture_output=capture, text=True)


def gitOut(*args):
    return git(*args).stdout.strip()


def gitOk(*args):
    return git(*args).returncode == 0


def short(ref):
    return gitOut("rev-parse", "--short", ref)


def currentBranch():
    return gitOut("branch", "--show-current")


def isMerging():
    return gitOk("rev-parse", "-q", "--verify", "MERGE_HEAD")


def isRebasing():
    gitDir = Path(gitOut("rev-parse", "--git-dir"))
    return (gitDir / "rebase-merge").is_dir() or (gitDir / "rebase-apply").is_dir()


def unmergedFiles():
    return gitOut("diff", "--name-only", "--diff-filter=U").splitlines()


def isAncestor(older, newer):
    return gitOk("merge-base", "--is-ancestor", older, newer)


def commitsAhead(base, ref="HEAD"):
    return gitOut("rev-list", "--count", f"origin/{base}..{ref}")


def defaultBase():
    head = gitOut("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return head.removeprefix("origin/") or "main"


def parseArgs(argv):
    """(command, base or None); `start` when no command is given."""
    args = list(argv)
    command = args.pop(0) if args[:1] and args[0] in COMMANDS else "start"
    base = None
    if args[:1] == ["--base"]:
        if len(args) < 2:
            complain("--base needs a branch", 2)
        base = args[1]
    return command, base, args


def originsTip(branch):
    """origin's tip for the branch, "" when origin has none."""
    listed = git("ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if listed.returncode != 0:
        complain(f"couldn't read origin's {branch}; not pushed. Rerun to push", 3)
    return listed.stdout.split("\t")[0]


def hasUnshippedCommits(remote, shipped):
    """origin's tip is behind the branch (or behind the tip the merge started from): the branch holds
    commits origin lacks."""
    if isAncestor(remote, "HEAD"):
        return True
    return bool(shipped) and isAncestor(remote, shipped)


def push(branch):
    """Pushes the branch, never forced, when origin has it at the tip this script merged from, so
    only the merge goes out: commits origin's branch lacks were never shipped, and go out by Ship."""
    remote = originsTip(branch)
    shipped = gitOut("rev-parse", "-q", "--verify", f"refs/rebase-main/{branch}")
    if not remote:
        say(f"origin has no {branch}; not pushed")
    elif remote == gitOut("rev-parse", "HEAD"):
        say(f"origin's {branch} is already at {short('HEAD')}")
    elif remote != shipped and hasUnshippedCommits(remote, shipped):
        say(f"{branch} has commits origin's {branch} lacks, which haven't been shipped; not pushed. Ship pushes them")
    elif remote != shipped:
        unseenCommits(branch)
    elif git("push", "-q", "origin", branch).returncode == 0:
        say(f"pushed {branch} to origin ({short(remote)} -> {short('HEAD')})")
    else:
        complain("the push failed; check the network and rerun", 3)


def unseenCommits(branch):
    print(f"rebase-main: origin's {branch} has commits this checkout hasn't seen; not pushed.", file=sys.stderr)
    complain(f"git fetch origin {branch}, look at git log HEAD..origin/{branch}, take what's needed, then rerun", 3)


def reportStatus(base):
    branch = currentBranch()
    backup = gitOut("rev-parse", "--short", f"refs/rebase-main/{branch}") or "none"
    say(f"merging origin/{base} into {branch}; backup refs/rebase-main/{branch} = {backup}")
    say(f"merging {gitOut('log', '-1', '--format=%h %s', 'MERGE_HEAD')}")
    files = unmergedFiles()
    if files:
        say("in conflict:")
        for name in files:
            print(f"  {name}", flush=True)


def refuseUnsafeStart(branch, base):
    if isMerging():
        complain("a merge is already under way; continue, abort or status", 2)
    if isRebasing():
        complain("a rebase is under way; finish or abort it with git first", 2)
    if not branch:
        complain("not on a branch", 2)
    if branch == base:
        complain(f"on {base}; switch to the branch to merge into", 2)
    if gitOut("status", "--porcelain"):
        print("rebase-main: uncommitted changes; commit or stash them first:", file=sys.stderr)
        print(gitOut("status", "--short"), file=sys.stderr, flush=True)
        raise Exit(2)


def conflictMarkers():
    checked = git("diff", "--cached", "--check")
    lines = (checked.stdout + checked.stderr).splitlines()
    return [line for line in lines if "conflict marker" in line.lower()]


def refuseUnresolved():
    unmerged = unmergedFiles()
    if unmerged:
        print("rebase-main: still unmerged; resolve and git add:", file=sys.stderr)
        for name in unmerged:
            print(f"  {name}", file=sys.stderr)
        raise Exit(2)
    markers = conflictMarkers()
    if markers:
        print("rebase-main: conflict markers left:", file=sys.stderr)
        print("\n".join(markers), file=sys.stderr, flush=True)
        raise Exit(2)


def mergeBaseIn(branch, base):
    """Fast-forwards a branch with no commits of its own; otherwise merges, keeping its commits as
    they are. A conflict leaves the merge under way, which finish() reports."""
    target = f"origin/{base}"
    if isAncestor("HEAD", target):
        git("merge", "-q", "--ff-only", target, capture=False)
    else:
        git("merge", "-q", "--no-ff", "-m", f"Merge {target} into {branch}", target, capture=False)


def finish(branch, base):
    """After mergeBaseIn or merge --continue: stop for conflicts, refuse a merge git couldn't start,
    else push."""
    if isMerging():
        reportStatus(base)
        raise Exit(1)
    if not isAncestor(f"origin/{base}", "HEAD"):
        complain("the merge didn't start (git's message is above); nothing changed", 2)
    say(f"{branch} holds origin/{base} ({short(f'origin/{base}')}), {commitsAhead(base)} commits ahead")
    push(branch)


def runStatus(base):
    if not isMerging():
        say(f"no merge under way on {currentBranch()}")
        return 0
    reportStatus(base)
    return 1


def runAbort():
    if not isMerging():
        complain("no merge under way", 2)
    git("merge", "--abort")
    say(f"aborted; {currentBranch()} is back at {short('HEAD')}")
    return 0


def runContinue(base):
    if not isMerging():
        complain("no merge under way", 2)
    refuseUnresolved()
    subprocess.run(["git", "merge", "--continue"], env={**os.environ, "GIT_EDITOR": "true"})
    finish(currentBranch(), base)
    return 0


def runStart(base):
    branch = currentBranch()
    refuseUnsafeStart(branch, base)
    if git("fetch", "-q", "origin", base).returncode != 0:
        complain(f"fetching origin/{base} failed; nothing changed", 2)
    target = f"origin/{base}"
    if isAncestor(target, "HEAD"):
        say(f"{branch} already holds {target} ({short(target)}); nothing to merge")
        push(branch)
        return 0
    git("update-ref", f"refs/rebase-main/{branch}", "HEAD")
    say(f"merging {target} ({short(target)}) into {branch} ({commitsAhead(base)} commits of its own); backup refs/rebase-main/{branch}")
    mergeBaseIn(branch, base)
    finish(branch, base)
    return 0


def rebaseMain(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    try:
        command, base, args = parseArgs(argv)
        base = base or defaultBase()
        if command == "status":
            return runStatus(base)
        if command == "abort":
            return runAbort()
        if command == "continue":
            return runContinue(base)
        return runStart(base)
    except Exit as stop:
        return stop.code
