# Starting an issue: its branch and worktree off the latest base, then In Progress in Jira.
# Ported from lib/start.js. Faithful, behavior-preserving port: no change in logic. Everything
# here is synchronous (stdlib subprocess/os/fs calls are sync in Python; there is no async/await
# equivalent needed), unlike the JS original which used node's Promise-based APIs throughout --
# the control flow and effects are otherwise identical, step for step.
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .jira import KEY_RE, projectOf

# Jira issue types vary per project and have no fixed branch convention, unlike Redmine's trackers.
# The map is config (plan.typeMap), not a hardcoded constant; this is only its default.
DEFAULT_TYPE_MAP = {"Story": "feat", "Task": "feat", "Bug": "fix"}


def prefixFor(issueType, typeMap=None):
    if typeMap is None:
        typeMap = DEFAULT_TYPE_MAP
    return typeMap.get(issueType) or "chore"


# The statuses Start accepts, keyed by Jira's statusCategory rather than by status name. Redmine
# had one fixed vocabulary ("New", "In Progress") to key off; Jira's CIAM project alone has many
# status names per issue type in each category (Story: "Requested"/"Drafted"/"Approved" are all To
# Do, "In Progress"/"Blocked"/"QA Testing" are all In Progress), so a name-keyed lookup like
# Redmine's can't cover every issue type. statusCategory.key is the one thing every workflow agrees
# on: "new" (To Do), "indeterminate" (In Progress), "done".
def ACTION(issue):
    key = issue["fields"]["status"]["statusCategory"]["key"]
    if key == "new":
        return "Start"
    if key == "indeterminate":
        return "Open"
    return None  # done: refuse


def checkId(key, action="Start"):
    if not KEY_RE.match(str(key)):
        raise RuntimeError(f"{action} needs a Jira key like CIAM-123, not {json.dumps(str(key))}")


# The subject's first five words, lowercase, joined by hyphens, as worktask made them.
def slug(subject):
    parts = [p for p in re.split(r"[^a-z0-9]+", subject.lower()) if p]
    return "-".join(parts[:5])


# <type>/<KEY>-<slug>; an issue type absent from the map gets "chore".
def branchName(issue, typeMap=None):
    if typeMap is None:
        typeMap = DEFAULT_TYPE_MAP
    prefix = prefixFor(issue["fields"]["issuetype"]["name"], typeMap)
    return f'{prefix}/{issue["key"]}-{slug(issue["fields"]["summary"])}'


# Beside the checkout: <parent>/<repo>.worktrees/<KEY>.
def worktreeDir(checkout, key):
    checkout = str(checkout)
    parent = os.path.dirname(checkout)
    base = os.path.basename(checkout)
    return os.path.join(parent, f"{base}.worktrees", str(key))


def escapeRegex(s):
    return re.sub(r"([.*+?^${}()|\[\]\\])", r"\\\1", s)


# A branch name for issue key, whatever its slug became: <KEY>-... with one of the CONFIGURED
# prefix segments (typeMap's values, or the "chore" fallback branchName() also uses) or none, so
# release/2026-10 is no branch of CIAM-2026.
#
# N3/N9: every caller threads the real, current config typeMap through, and this always unions it
# with DEFAULT_TYPE_MAP's own prefixes too (so a config that narrows typeMap to customize one
# issue type never orphans a branch made under the wider default map before the config changed),
# plus the "chore" fallback, or no prefix at all -- never an arbitrary one.
def issueBranch(key, typeMap=None):
    if typeMap is None:
        typeMap = DEFAULT_TYPE_MAP
    seen = []
    for p in list(DEFAULT_TYPE_MAP.values()) + list(typeMap.values()) + ["chore"]:
        if p not in seen:
            seen.append(p)
    prefixes = [escapeRegex(p) for p in seen]
    pattern = f'^(?:(?:{"|".join(prefixes)})/)?{escapeRegex(str(key))}-'
    return re.compile(pattern)


# Each worktree git lists: { dir, branch, main, locked }, the main checkout first; branch is null on
# a detached HEAD.
def worktrees(porcelain):
    blocks = porcelain.strip().split("\n\n")
    trees = []
    for i, block in enumerate(blocks):
        lines = block.split("\n")
        branch = None
        for line in lines:
            if line.startswith("branch refs/heads/"):
                branch = line[len("branch refs/heads/"):]
                break
        locked = any(line == "locked" or line.startswith("locked ") for line in lines)
        d = re.sub(r"^worktree ", "", lines[0], count=1)
        trees.append({"dir": d, "branch": branch, "main": i == 0, "locked": locked})
    return trees


# The worktree already holding a branch for issue key, or at dir, where Start puts it, whatever its
# HEAD: a rebase or bisect detaches it, and branch is then null. main: it's the main checkout,
# listed first. action names the command a refusal tells Peter to run again.
def existingWorktree(porcelain, key, dir, action="Start", typeMap=None):
    if typeMap is None:
        typeMap = DEFAULT_TYPE_MAP
    pattern = issueBranch(key, typeMap)
    for tree in worktrees(porcelain):
        branch = tree["branch"] if tree["branch"] and pattern.match(tree["branch"]) else None
        if not branch and not tree["main"] and tree["dir"] == dir and tree["branch"]:
            raise RuntimeError(
                f'{tree["dir"]} is checked out on {tree["branch"]}, not a branch for {key}; switch it back, then {action} again'
            )
        if branch or (not tree["main"] and tree["dir"] == dir):
            return {"dir": tree["dir"], "branch": branch, "main": tree["main"]}
    return None


def expandHome(dir):
    return re.sub(r"^~(?=$|/)", str(Path.home()), dir)


# Opt-in debug tracing (env PLAN_DEBUG=1): every run() call -- every code/osascript/tmux/git/etc
# invocation in the whole tool goes through here -- prints its argv to stderr before running, and
# its outcome (returned stdout, or the raised RuntimeError) after. The placer's best-effort steps
# (SpacesPlacer.showUrl() and focus()) swallow their errors, so this is how to see which call
# failed.
def _debugEnabled():
    return os.environ.get("PLAN_DEBUG") == "1"


def run(command, args, cwd):
    if _debugEnabled():
        print(f"[PLAN_DEBUG] run: {command} {' '.join(args)} (cwd={cwd})", file=sys.stderr)
    try:
        result = subprocess.run([command, *args], cwd=cwd, capture_output=True, text=True)
    except OSError as error:
        if _debugEnabled():
            print(f"[PLAN_DEBUG]   -> OSError: {error}", file=sys.stderr)
        raise RuntimeError(f'{command} {" ".join(args)}: {error}') from error
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        message = stderr or (result.stdout or "").strip() or f"exited with code {result.returncode}"
        if _debugEnabled():
            print(f"[PLAN_DEBUG]   -> exit {result.returncode}: {message}", file=sys.stderr)
        raise RuntimeError(f'{command} {" ".join(args)}: {message}')
    if _debugEnabled():
        out = (result.stdout or "").strip()
        print(f"[PLAN_DEBUG]   -> ok: {out[:200]!r}", file=sys.stderr)
    return result.stdout


def git(cwd, *args):
    return run("git", list(args), cwd)


# One Start or Done per issue across windows, so Open and Done on the same row exclude each
# other: an exclusive file in the repo's git dir, holding the pid of the process that took it and
# its action. One left by a crash is never taken over, since two windows taking it at once could
# both win; the refusal names it for Peter to delete. Returns its release.
DOING = {"Start": "started", "Done": "finished"}


def lock(common, key, action="Start"):
    file = os.path.join(common, f"plan-busy-{key}")
    try:
        fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(f"{os.getpid()} {action}\n")
    except FileExistsError:
        with open(file, "r", encoding="utf8") as f:
            content = f.read().strip()
        parts = content.split(" ")
        pid_text = parts[0] if parts else ""
        held = parts[1] if len(parts) > 1 else action
        pid_num = None
        try:
            pid_num = int(pid_text)
            valid_pid = True
        except ValueError:
            valid_pid = False
        if not valid_pid or alive(pid_num):
            raise RuntimeError(f"{key} is being {DOING.get(held) or DOING.get(action)} in another window; if none is, delete {file}")
        raise RuntimeError(f"a {held if DOING.get(held) else action} of {key} that stopped without finishing left {file}; delete it, then {action} again")

    def release():
        try:
            os.remove(file)
        except FileNotFoundError:
            pass

    return release


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


# In the repo's git dir, outside every tree: written just before Start makes an issue's worktree,
# removed if that fails, and removed once its setup has all succeeded. A worktree without it is set
# up, including one made before this marker or by another tool, so Open leaves it alone.
def settingUp(common, key):
    return os.path.join(common, f"plan-setting-up-{key}")


# checkouts[project] is a list of repo paths serving that project (several repos can carry one
# Jira project); a bare string is accepted too, as a one-item list, for back-compat with configs
# written before this port's project-maps-to-many-repos change.
def checkoutsFor(checkouts, project):
    val = (checkouts or {}).get(project)
    if not val:
        return []
    if isinstance(val, str):
        return [val]
    return list(val)


# cwd's own main checkout, whatever folder of it (or of a linked worktree) cwd names -- worktrees
# go beside it, never in its tree, and a cwd inside one of them still resolves to the one root git
# itself already knows: `rev-parse --git-common-dir` always answers from the main checkout's own
# .git, never a worktree's. None when cwd isn't inside a git repo at all.
def gitCommonDir(cwd):
    try:
        return git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    except RuntimeError:
        return None


# Every repo checkouts lists for project, each resolved to ITS OWN main checkout (a listed path may
# itself name a worktree), existing paths only, de-duplicated in listed order.
def listedCheckouts(checkouts, project):
    roots = []
    for raw in checkoutsFor(checkouts, project):
        named = expandHome(raw)
        if not os.path.exists(named):
            continue
        common = gitCommonDir(named)
        root = os.path.realpath(os.path.dirname(common)) if common else os.path.realpath(named)
        if root not in roots:
            roots.append(root)
    return roots


# Default repo picker, used only when cwd isn't inside a git repo and the issue's project lists
# more than one candidate repo and none of them already holds the issue's work (mainCheckout,
# below): the same fzf plumbing cmdPicker already depends on, over the candidate paths instead of
# issue rows. A non-zero exit (esc, no match) refuses rather than guessing.
def pickRepoWithFzf(options):
    result = subprocess.run(
        ["fzf", "--prompt", "Which repo holds this issue's work? "],
        input="\n".join(options),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("no repo chosen; run plan from the repo the work belongs in")
    return result.stdout.strip()


# DECIDED (user decision 2026-11-05): several repos can serve one Jira project, so the repo is no
# longer looked up by project alone, and no longer assumed to be wherever `plan` happens to run --
# both can be true of the same issue over its life. Two rules, in order:
#
#   1. Existing work wins. Every candidate repo -- cwd's own main checkout (if any) and every repo
#      checkouts lists for the issue's project (listedCheckouts, each itself resolved to its own
#      main checkout) -- is searched with the same existingWorktree() lookup Start/Open/Done always
#      ran within one repo, so a worktree or branch already made for the issue is found wherever it
#      actually lives. The worktree is the record of which repo an issue belongs to; nothing extra
#      is written to track it. Found in more than one repo is refused by name -- EDGE CASE,
#      see docs/failure-modes.md: this is also how "started in repo A, `plan open` run from repo B"
#      resolves correctly today, as long as A is listed, since A's existing worktree outranks B's
#      cwd.
#   2. No existing work anywhere: the repo is cwd's own main checkout, if `plan` was run from
#      inside one -- `git rev-parse --git-common-dir`, so a cwd inside a linked worktree still
#      resolves to the main checkout, never a worktree's own tree (worktrees go beside the main
#      checkout, never in it). `checkouts` is now only a FALLBACK, used when cwd isn't inside a git
#      repo at all: one listed repo is used outright; more than one asks `pickRepo` (default: fzf);
#      none listed is a refusal naming both ways to fix it.
#
# OPEN (not solved here, see docs/architecture.md): whether one issue may legitimately span two
# repos. OPTIONAL-LATER (not solved here): a Jira component/label like `repo:<name>` preselecting
# the repo at Start, instead of relying on cwd.
def mainCheckout(issue, checkouts, cwd, action="Start", typeMap=None, pickRepo=None):
    if pickRepo is None:
        pickRepo = pickRepoWithFzf
    project = projectOf(issue["key"])
    key = issue["key"]

    cwdCommon = gitCommonDir(cwd)
    cwdRoot = os.path.realpath(os.path.dirname(cwdCommon)) if cwdCommon else None

    candidates = ([cwdRoot] if cwdRoot else []) + [
        r for r in listedCheckouts(checkouts, project) if r != cwdRoot
    ]

    foundIn = []
    for checkout in candidates:
        common = gitCommonDir(checkout)
        if not common:
            continue
        porcelain = git(checkout, "worktree", "list", "--porcelain")
        found = existingWorktree(porcelain, key, worktreeDir(checkout, key), action, typeMap)
        if found:
            foundIn.append((checkout, common))

    if len(foundIn) > 1:
        names = ", ".join(c for c, _ in foundIn)
        raise RuntimeError(
            f"{key} already has a worktree or branch in more than one repo: {names}; "
            f"remove the extra one, or move the work, before running {action} again"
        )
    if foundIn:
        checkout, common = foundIn[0]
        return {"project": project, "checkout": checkout, "common": common}

    if cwdRoot:
        return {"project": project, "checkout": cwdRoot, "common": cwdCommon}

    listed = listedCheckouts(checkouts, project)
    if not listed:
        raise RuntimeError(
            f"Run plan from the repo the work belongs in "
            f"(or set checkouts.{project} in ~/.config/skuld/config.json)"
        )
    named = listed[0] if len(listed) == 1 else pickRepo(listed)
    common = gitCommonDir(named)
    if not common:
        raise RuntimeError(f"the configured checkout for \"{project}\" isn't a git repo: {named}")
    checkout = os.path.realpath(os.path.dirname(common))
    return {"project": project, "checkout": checkout, "common": common}


# Start issue key, or reopen its worktree. Safe to repeat: each step runs only when it has something
# to do. confirm(blockers) is asked only before a new worktree; false stops with nothing changed.
# handOff(result), when given, is the last step of setup, so a handoff that fails runs setup again
# (cmdStart opens the issue window after start() returns instead, so it passes none).
# Returns { dir, branch, created, started, notes }, or None when Peter said no. started: this Start
# finished the worktree's setup, here or after an earlier one stopped part way.
def start(jira, checkouts, key, confirm, handOff=None, typeMap=None, cwd=None, pickRepo=None):
    if handOff is None:
        handOff = lambda result: None
    if typeMap is None:
        typeMap = DEFAULT_TYPE_MAP
    if cwd is None:
        cwd = os.getcwd()
    checkId(key)
    issue = jira.issue(key)
    # The panel's row can be older than the issue: one closed or moved on since keeps its status.
    action = ACTION(issue)
    if not action:
        raise RuntimeError(f'{key} is {issue["fields"]["status"]["name"]}; refresh Next up')
    mc = mainCheckout(issue, checkouts, cwd, action, typeMap, pickRepo)
    checkout, common = mc["checkout"], mc["common"]

    release = lock(common, key)
    try:
        return startIn(jira, key, confirm, handOff, issue, checkout, common, typeMap)
    finally:
        release()


def startIn(jira, key, confirm, handOff, issue, checkout, common, typeMap):
    notes = []
    dir = worktreeDir(checkout, key)
    found = existingWorktree(git(checkout, "worktree", "list", "--porcelain"), key, dir, "Start", typeMap)
    created = False
    if found and found["main"]:
        raise RuntimeError(f'{found["branch"]} is checked out in the main checkout, {checkout}; switch that off it, then Start again')
    if found and not os.path.exists(found["dir"]):
        raise RuntimeError(f"git still lists {found['dir']} as a worktree, but it's gone; run git worktree prune in {checkout}, then Start again")
    if not found:
        # L4: refuse before anything is created if the In Progress transition (made below, only once
        # the worktree is set up) isn't even available -- a read-only check (GET transitions), so
        # nothing is created, moved or written to Jira if it's going to fail anyway.
        if issue["fields"]["status"]["statusCategory"]["key"] == "new":
            jira.statusTransitionId(key, "In Progress")
        # A blocker Peter can't read, moved to a project hidden from him, is listed as unknown.
        blockers = [
            {"id": b["key"], "subject": b["fields"]["summary"], "status": b["fields"]["status"]["name"]}
            for b in jira.blockers(issue)
        ]
        if blockers and not confirm(blockers):
            return None

        # A new branch starts from the latest base or not at all; a failed fetch stops before
        # anything changes. --prune drops a branch origin has deleted, so a gone develop is no base.
        git(checkout, "fetch", "--quiet", "--prune", "origin")
        # develop, else origin's default branch, else main.
        base = "main"
        try:
            ref = git(checkout, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
            base = re.sub(r"^origin/", "", ref.strip())
        except RuntimeError:
            pass
        try:
            git(checkout, "show-ref", "--verify", "--quiet", "refs/remotes/origin/develop")
            base = "develop"
        except RuntimeError:
            pass
        if os.path.exists(dir):
            raise RuntimeError(f"{dir} exists but isn't a worktree of {checkout}")
        os.makedirs(os.path.dirname(dir), exist_ok=True)

        # The issue's branch, if it has one, here or pushed from elsewhere, is reused as it is: it may
        # hold work, so it keeps its own base.
        def refs(prefix):
            out = git(checkout, "for-each-ref", "--format=%(refname:short)", prefix)
            pattern = issueBranch(key, typeMap)
            for name in out.split("\n"):
                name = re.sub(r"^origin/", "", name)
                if pattern.match(name):
                    return name
            return None

        local = refs("refs/heads")
        pushed = None if local else refs("refs/remotes/origin")
        branch = local or pushed or branchName(issue, typeMap)
        now = datetime.now(timezone.utc)
        with open(settingUp(common, key), "w", encoding="utf8") as f:
            f.write(now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z\n")
        try:
            if local:
                git(checkout, "worktree", "add", "--quiet", dir, branch)
                notes.append(f"Reused the branch {branch}, on its own base, in {dir}.")
            elif pushed:
                git(checkout, "worktree", "add", "--quiet", "--track", "-b", branch, dir, f"origin/{branch}")
                notes.append(f"Checked out {branch} from origin in {dir}.")
            else:
                git(checkout, "worktree", "add", "--quiet", "--no-track", "-b", branch, dir, f"origin/{base}")
                notes.append(f"Made {branch} from origin/{base} in {dir}.")
        except RuntimeError:
            # A failed hook leaves the worktree made, and its setup still to finish. With no worktree,
            # there's no setup to finish, and one made later by hand is set up.
            try:
                left = existingWorktree(git(checkout, "worktree", "list", "--porcelain"), key, dir, "Start", typeMap)
            except RuntimeError:
                left = True
            if not left:
                try:
                    os.remove(settingUp(common, key))
                except FileNotFoundError:
                    pass
            raise

        found = {"dir": dir, "branch": branch}
        created = True

    # A sync or update that failed part way leaves the marker, so the next Start runs setup again.
    marker = settingUp(common, key)
    started = os.path.exists(marker)
    if started and os.path.exists(os.path.join(found["dir"], "uv.lock")):
        run("uv", ["sync", "--quiet"], found["dir"])
        notes.append("Synced the uv environment.")

    if issue["fields"]["status"]["statusCategory"]["key"] == "new":
        # transition() reads the issue back and refuses if Jira didn't move it, so a workflow condition
        # or validator that silently keeps the old status stops Start here rather than lying about it.
        # Jira has no start_date field for CIAM; that Redmine custom-field write is dropped, not ported.
        jira.transition(key, "In Progress")
        on_branch = f' on `{found["branch"]}`' if found.get("branch") else ""
        jira.addComment(key, jira.textComment(f"Started{on_branch}, in {found['dir']}."))
        notes.append(f"{key} is In Progress.")

    result = {**found, "created": created, "started": started, "notes": notes}
    if started:
        handOff(result)
        try:
            os.remove(marker)
        except FileNotFoundError:
            pass
    return result
