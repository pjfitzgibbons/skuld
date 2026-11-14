# Finishing an issue once its PR has merged: its worktree and branch gone, the main checkout's base
# at the merge, and a comment on the issue linking the merge, with a reminder to close it by hand.
# Ported from lib/done.js. Faithful, behavior-preserving port: no change in logic. Each step runs
# only when it has something to do, so a Done that stopped part way is finished by the next, and a
# second Done changes nothing.
#
# INVARIANT (decision #5): Done never transitions the Jira issue, to Done or anything else. Peter's
# PreToolUse hook already blocks any Bash command that looks like it moves an issue to a terminal
# status, and a terminal transition needs a human regardless -- Jira issues can carry required-field
# prompts or workflow conditions on close that only a person reviewing the issue can satisfy. So
# finish() has no status write at all: it posts an ADF comment (jira.addComment) linking the PR and
# merge commit, and its last note is the literal string "Close <KEY> in Jira: <browse URL>" for
# Peter to act on himself. No function in this file calls jira.transition, and none should ever be
# added that does.
import json
import os
import re
import stat as statmod

from .start import (
    DEFAULT_TYPE_MAP,
    checkId,
    existingWorktree,
    git,
    issueBranch,
    lock,
    mainCheckout,
    run,
    settingUp,
    worktreeDir,
    worktrees,
)
from .ship import prLink, unsentIn
from . import pulses, term

# The status categories Done accepts: "indeterminate" (In Progress) and "done", either of which a
# merge's comment or an earlier Done's cleanup may still have work left to do under. "new" (To Do)
# means Ship never ran.
FINISHES = {"indeterminate", "done"}
FIELDS = "state,url,number,title,headRefName,headRefOid,baseRefName,mergeCommit,isCrossRepository"


# The PR for branch, as gh reports it. Decision #4: Jira has no "Pull requests" field to read, so
# Done finds the PR from the branch itself, the same way a person would. gh pr view works once the
# branch is resolvable against the repo (a local branch, or one gh can still look up by name); gh pr
# list --head searches GitHub's side instead, which is the one that still finds a merged PR after
# its branch is gone locally (Done's own branch-sweep, further down, deletes merged branches) --
# but only with --state all (L1): gh pr list defaults to open PRs only, so without it a merged PR
# on a since-deleted branch would silently come back empty, exactly in the case Done needs it most.
# A branch can carry more than one PR over its life (an earlier one closed unmerged, then the real
# one that got merged), so among what --state all returns, a MERGED PR is preferred, newest by
# number if more than one; otherwise the newest PR of any state, so Done's refusal names a real PR
# rather than claiming none exists.
def prFromBranch(cwd, branch, fields=FIELDS):
    try:
        return json.loads(run("gh", ["pr", "view", branch, "--json", fields], cwd))
    except RuntimeError as view_error:
        try:
            listed = json.loads(
                run("gh", ["pr", "list", "--head", branch, "--state", "all", "--json", fields, "--limit", "20"], cwd)
            )
        except RuntimeError as list_error:
            raise RuntimeError(
                f"no PR found for branch {branch}: {view_error}; and gh pr list --head failed too: {list_error}"
            ) from list_error
        if listed:
            by_newest = sorted(listed, key=lambda pr: pr["number"], reverse=True)
            merged = next((pr for pr in by_newest if pr["state"] == "MERGED"), None)
            return merged if merged is not None else by_newest[0]
        raise RuntimeError(f"no PR found for branch {branch} (gh pr view: {view_error})")


# The PR's repo on GitHub.
def repoUrl(pr):
    return re.sub(r"/pull/\d+$", "", pr["url"])


def commitUrl(pr):
    return f'{repoUrl(pr)}/commit/{pr["mergeCommit"]["oid"]}'


# The closing comment: the PR and its merge commit, each linked. Posted with jira.addComment, not a
# status-changing update -- see the invariant at the top of this file.
def doneNote(pr):
    return f'Done: {prLink(pr)} merged as [{pr["mergeCommit"]["oid"][:7]}]({commitUrl(pr)}).'


# owner/repo, lower-cased: a URL's last two path segments, so an ssh host alias or GitHub's ssh on
# port 443 reads as the repo it names.
def repoOf(url):
    m = re.search(r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


# Each local branch and its tip. lstrip, not short: short names a branch heads/<name> when a tag
# shares its name.
def branches(cwd):
    out = git(cwd, "for-each-ref", "--format=%(refname:lstrip=2) %(objectname)", "refs/heads")
    result = {}
    for line in out.strip().split("\n"):
        if not line:
            continue
        name, oid = line.split(" ", 1)
        result[name] = oid
    return result


# The path in the way of a file git will add: something at the file itself, or a file where one of
# its folders goes. None when the way is clear.
def blocking(cwd, file):
    parts = file.split("/")
    for i in range(1, len(parts) + 1):
        rel = "/".join(parts[:i])
        try:
            st = os.lstat(os.path.join(cwd, rel))
        except FileNotFoundError:
            return None
        if i == len(parts) or not statmod.S_ISDIR(st.st_mode):
            return rel
    return None


# Does commit oid hold nothing the PR lacks: is it the PR's merged head, or behind it, as after a
# suggestion accepted on GitHub? A head this repo lacks is fetched from the PR's own ref, which
# GitHub keeps after the branch is deleted, into FETCH_HEAD only; a failed fetch stops Done, rather
# than reading as commits the PR lacks.
def inPullRequest(cwd, oid, pr):
    if oid == pr["headRefOid"]:
        return True
    if not fetchHead(cwd, pr):
        return False
    try:
        git(cwd, "merge-base", "--is-ancestor", oid, pr["headRefOid"])
        return True
    except RuntimeError:
        return False


# Has cwd the PR's merged head, fetching it if not? Done calls it before its first change, so the
# branch sweep, after the changes, never needs origin.
def fetchHead(cwd, pr):
    def has():
        try:
            git(cwd, "cat-file", "-e", f'{pr["headRefOid"]}^{{commit}}')
            return True
        except RuntimeError:
            return False

    if has():
        return True
    try:
        git(cwd, "fetch", "--quiet", "origin", f'refs/pull/{pr["number"]}/head')
    except RuntimeError as error:
        raise RuntimeError(
            f'{prLink(pr)}\'s merged head {pr["headRefOid"][:7]} isn\'t in {cwd}, and fetching it failed; '
            f"Done again once origin can be reached: {error}"
        ) from error
    return has()


# The ignored paths a worktree's removal may take without asking, as plan.disposable defaults them:
# what tools make again. "name/" is a folder of that name at any depth, "name" a file or folder.
# .wdio-vscode-service/ is the e2e suite's downloaded VS Code; the next run downloads it again.
DISPOSABLE = [
    ".tmp/", "node_modules/", ".venv/", "__pycache__/", ".pytest_cache/", ".coverage", ".ruby-lsp/", "htmlcov/", "dist/", "build/",
    ".wdio-vscode-service/",
]

# The refusal names this many ignored entries, then counts the rest.
MAX_LISTED = 10


# Is an ignored path, as git lists it ("dir/" for a whole folder), under a disposable name?
def disposed(entry, disposable):
    parts = [p for p in entry.split("/") if p]
    folders = parts if entry.endswith("/") else parts[:-1]
    for name in disposable:
        if name.endswith("/"):
            if name[:-1] in folders:
                return True
        else:
            if name in parts:
                return True
    return False


# Does an ignored entry hold anything outside disposable? git lists a folder whose files are all
# ignored as one entry, so a folder of anything else is walked, and its disposable names skipped.
def blocksRemoval(dir, entry, disposable):
    if disposed(entry, disposable):
        return False
    if not entry.endswith("/"):
        return True
    for name in os.listdir(os.path.join(dir, entry)):
        full = os.path.join(dir, entry, name)
        child_entry = f"{entry}{name}{'/' if os.path.isdir(full) else ''}"
        if blocksRemoval(dir, child_entry, disposable):
            return True
    return False


# The blocking entries as git lists them, a folder once, capped, with the rest counted.
def listed(blocking):
    shown = blocking[:MAX_LISTED]
    if len(blocking) > MAX_LISTED:
        shown.append(f"and {len(blocking) - MAX_LISTED} more")
    return "\n".join(shown)


# A tree Done may remove or switch away from: clean, and at or behind the PR's merged head. Throws
# naming why not. removing: count untracked files, which a switch would carry over but a removal
# would lose, and ignored ones outside disposable, which git removes unasked.
def checkMerged(dir, pr, removing, disposable):
    args = ["status", "--porcelain"] + ([] if removing else ["--untracked-files=no"])
    changes = git(dir, *args).strip()
    if changes:
        raise RuntimeError(f"{dir} has changes the merged PR lacks; commit or discard them, then Done again:\n{changes}")
    if removing:
        out = git(dir, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory")
        ignored = [e for e in out.split("\0") if e]
        blocking = [entry for entry in ignored if blocksRemoval(dir, entry, disposable)]
        if blocking:
            raise RuntimeError(
                f"{dir} has ignored files its removal would delete; move them out, or add them to plan.disposable, "
                f"then Done again:\n" + listed(blocking)
            )
    head = git(dir, "rev-parse", "HEAD").strip()
    if not inPullRequest(dir, head, pr):
        raise RuntimeError(
            f'{dir} is at {head[:7]}, with commits {prLink(pr)}\'s merged head {pr["headRefOid"][:7]} lacks; '
            f"open a new PR for them or drop them, then Done again"
        )


# Finish issue key. view(cwd, branch) resolves the PR from the branch, prFromBranch by default, or a
# fake in a test. Returns { dir, notes }: the worktree it removed, or None, and what each step did.
# A Done that fails part way raises with that result as error.result, so Peter learns what it changed.
#
# Decision #4 reorders this from the Redmine version: Redmine kept the PR's URL on the issue itself,
# so finish() could look the PR up before touching git at all. Jira has nothing to read it from -- the
# PR is only findable from the branch -- so the worktree/branch lookup (existingWorktree) must happen
# FIRST here, and the PR lookup (prFromBranch) moves after it, inside finishIn, once a branch name
# is in hand. If no worktree and no branch for the key can be found at all, there is nothing to
# resolve a PR from, and finish() must refuse rather than guess at a branch name.
def finish(jira, checkouts, key, view=None, disposable=None, typeMap=None, issuesFile=None, cwd=None, pickRepo=None, workspaceDir=None, pulsesFile=None):
    if view is None:
        view = prFromBranch
    if disposable is None:
        disposable = DISPOSABLE
    if typeMap is None:
        typeMap = DEFAULT_TYPE_MAP
    if cwd is None:
        cwd = os.getcwd()
    checkId(key, "Done")
    issue = jira.issue(key)
    # The panel's row can be older than the issue.
    category = issue["fields"]["status"]["statusCategory"]["key"]
    if category not in FINISHES:
        raise RuntimeError(f'{key} is {issue["fields"]["status"]["name"]}; refresh Next up')
    mc = mainCheckout(issue, checkouts, cwd, "Done", typeMap, pickRepo)
    project, checkout, common = mc["project"], mc["checkout"], mc["common"]

    porcelain = git(checkout, "worktree", "list", "--porcelain")
    dir = worktreeDir(checkout, key)
    found = existingWorktree(porcelain, key, dir, "Done", typeMap)
    branch = (found["branch"] if found else None) or branchForKey(checkout, key, typeMap)
    if not branch:
        raise RuntimeError(f"{key} has no worktree and no branch found for it; Ship it first")

    pr = view(checkout, branch)
    if pr["state"] != "MERGED":
        raise RuntimeError(f'{prLink(pr)} is {"still open" if pr["state"] == "OPEN" else "closed unmerged"}; nothing changed')
    release = lock(common, key, "Done")
    result = {"dir": None, "notes": []}
    try:
        finishIn(jira, key, project, checkout, common, pr, found, porcelain, result, disposable, typeMap, issuesFile, workspaceDir, pulsesFile)
        return result
    except Exception as error:
        error.result = result
        raise
    finally:
        release()


# A branch matching the key's pattern, local or on origin, when no worktree holds one: the issue may
# have been Shipped and merged from a branch whose worktree Done already removed on an earlier,
# partial run.
def branchForKey(checkout, key, typeMap=None):
    if typeMap is None:
        typeMap = DEFAULT_TYPE_MAP
    pattern = issueBranch(key, typeMap)

    def refs(prefix):
        out = git(checkout, "for-each-ref", "--format=%(refname:short)", prefix)
        for name in out.split("\n"):
            name = re.sub(r"^origin/", "", name)
            if pattern.match(name):
                return name
        return None

    return refs("refs/heads") or refs("refs/remotes/origin")


# Runs one of Done's last writes. A write that fails is a warning naming what to do by hand, not a
# failure: the branch and worktree are already gone, so the remaining steps still run. None when it worked.
def unwritten(name, byHand, write):
    try:
        with term.step(name, byHand):
            write()
    except RuntimeError as error:
        return f"Warning: {error}"
    return None


# The issue's worktree path: the one Done resolved, where Start and Open wrote the legacy workspace
# file beside it, else the one Start would make.
def issueWorktreePath(resolved, checkout, key):
    if resolved and not resolved["main"]:
        return resolved["dir"]
    return worktreeDir(checkout, key)


def finishIn(jira, key, project, checkout, common, pr, found, porcelain, result, disposable, typeMap=None, issuesFile=None, workspaceDir=None, pulsesFile=None):
    if typeMap is None:
        typeMap = DEFAULT_TYPE_MAP
    notes = result["notes"]
    link = prLink(pr)
    base = pr["baseRefName"]
    upstream = f"origin/{base}"
    # In full, so a local branch named origin/<base> can't stand in for it.
    upstreamRef = f"refs/remotes/{upstream}"

    # Every check before the first change, so a tree holding work the PR lacks, or a step bound to
    # fail, stops Done with nothing removed. The PR's own ref is fetched from origin, and the merge
    # looked for there, so they must be one repo: as configured, or as an insteadOf rewrites it.
    origin = git(checkout, "remote", "get-url", "origin").strip()
    named = git(checkout, "config", "--get", "remote.origin.url").strip()
    if repoOf(origin) != repoOf(repoUrl(pr)) and repoOf(named) != repoOf(repoUrl(pr)):
        raise RuntimeError(f"{link} isn't in {checkout}'s origin, {named}; link {key}'s PR from its own repo, then Done again")

    # The issue's worktree is the one on the PR's head branch, whatever its name, so the branch can be
    # deleted; else the one Start would find. A head name counts only from this repo, in a worktree of
    # its own: a fork's, or a long-lived branch such as develop in the main checkout, isn't the issue's.
    trees = worktrees(porcelain)
    head = None if (pr.get("isCrossRepository") or pr["headRefName"] == base) else pr["headRefName"]
    resolved = next((t for t in trees if not t["main"] and head and t["branch"] == head), None) or found
    if resolved and os.path.exists(resolved["dir"]):
        checkMerged(resolved["dir"], pr, not resolved["main"], disposable)
    # .tmp/ is disposable, so the worktree's removal would delete, unsent, the review replies and
    # checklist ticks pr sends, this issue's or another's.
    if resolved and not resolved["main"]:
        left = unsentIn(resolved["dir"])
        if left:
            scripts = "python3 ~/.claude/skills/jira-issues/scripts"
            raise RuntimeError(
                f'{resolved["dir"]} has replies or checklist ticks pr hasn\'t sent; send them from there with '
                f"{scripts}/pr_threads.py <PR> --post .tmp/<KEY>/replies and {scripts}/checklist.py <KEY> "
                f'.tmp/<KEY>/issue.md (for {key}, the PR is {pr["number"]}), not pr, whose cleanup skips Done\'s '
                f"checks, or delete them, then Done again:\n" + "\n".join(left)
            )
    if resolved and not resolved["main"]:
        tree = next((t for t in trees if t["dir"] == resolved["dir"]), None)
        if tree and tree.get("locked"):
            raise RuntimeError(f'{resolved["dir"]} is locked; run git worktree unlock {resolved["dir"]}, then Done again')

    # The main checkout moves to the base only from the issue's branch; on any other, Peter's work
    # stays checked out, and only the base is brought up to date, unless another worktree has it.
    current = git(checkout, "branch", "--show-current").strip()
    onBase = current == base or bool(resolved and resolved.get("main"))
    if current == base:
        changes = git(checkout, "status", "--porcelain", "--untracked-files=no").strip()
        if changes:
            raise RuntimeError(f"{checkout} has changes on {base}; commit or stash them, then Done again:\n{changes}")
    elsewhere = next((t for t in trees if not t["main"] and t["branch"] == base), None)
    if elsewhere and not os.path.exists(elsewhere["dir"]):
        raise RuntimeError(f"git lists {elsewhere['dir']} on {base}, but the folder is gone; run git worktree prune, then Done again")
    # git won't switch to a branch another worktree has checked out.
    if elsewhere and resolved and resolved.get("main"):
        raise RuntimeError(f"{checkout} is on {current}, and {base} is checked out in {elsewhere['dir']}; switch that worktree off {base}, then Done again")
    git(checkout, "fetch", "--quiet", "--prune", "origin")
    try:
        git(checkout, "rev-parse", "--verify", "--quiet", upstreamRef)
    except RuntimeError:
        raise RuntimeError(f"origin has no {base}, {link}'s base, so Done can't bring it up; finish {key} by hand")
    try:
        git(checkout, "merge-base", "--is-ancestor", pr["mergeCommit"]["oid"], upstreamRef)
    except RuntimeError:
        raise RuntimeError(f"{upstream} doesn't hold the merge commit {pr['mergeCommit']['oid'][:7]} yet; Done again shortly")
    tips = branches(checkout)
    local = tips.get(base)
    if local and not elsewhere:
        try:
            git(checkout, "merge-base", "--is-ancestor", local, upstreamRef)
        except RuntimeError:
            raise RuntimeError(f"{base} in {checkout} has commits {upstream} lacks, so it can't fast-forward; push or drop them, then Done again")
    fetchHead(checkout, pr)

    # The base first: git checks a switch or fast-forward whole before it writes a file, so an
    # untracked file in its way refuses Done before the worktree goes.
    target = git(checkout, "rev-parse", upstreamRef).strip()
    if onBase:
        # git overwrites an ignored file, unasked, where the merge adds a tracked one, or a folder on
        # its path. A rename's new path counts as added, and -z gives each path as it is, not quoted.
        added_out = git(checkout, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=A", "HEAD", upstreamRef)
        added = [a for a in added_out.split("\0") if a]
        inWay = []
        seen = set()
        for file in added:
            b = blocking(checkout, file)
            if b and b not in seen:
                seen.add(b)
                inWay.append(b)
        if inWay:
            raise RuntimeError(f"{checkout} has files where {upstream} adds tracked ones; move them aside, then Done again:\n" + "\n".join(inWay))
        if current != base:
            # The base moves first, so the switch goes straight from HEAD to the merge, the two trees the
            # scan compared, never through the old base.
            if local and local != target:
                git(checkout, "update-ref", f"refs/heads/{base}", target, local)
                notes.append(f"Brought {base} up to the merge.")
            git(checkout, "switch", "--quiet", base)
            notes.append(f"Switched {checkout} to {base}.")
        elif local != target:
            git(checkout, "merge", "--quiet", "--ff-only", upstreamRef)
            notes.append(f"Brought {base} up to the merge.")
    elif elsewhere:
        # git won't move a branch another worktree has checked out.
        notes.append(f"Left {checkout} on {current or 'a detached HEAD'}; {base} is checked out in {elsewhere['dir']}, so pull it there.")
    elif not local:
        notes.append(f"Left {checkout} on {current or 'a detached HEAD'}; it has no {base} to bring up.")
    elif local != target:
        # The old value makes git refuse if the base moved since it was checked.
        git(checkout, "update-ref", f"refs/heads/{base}", target, local)
        notes.append(f"Left {checkout} on {current or 'a detached HEAD'}, and brought {base} up to date.")

    if resolved and not resolved["main"]:
        # git removes a worktree whose folder was deleted by hand as well, and refuses one with
        # untracked files; ignored ones, such as .tmp/ and .venv, go with it. Start's setup marker
        # goes too, so a reopened issue's next Start sets its new worktree up.
        git(checkout, "worktree", "remove", resolved["dir"])
        result["dir"] = resolved["dir"]
        try:
            os.remove(settingUp(common, key))
        except FileNotFoundError:
            pass
        notes.append(f"Removed the worktree {resolved['dir']}.")

    # git can't see a squash merge, so -d always refuses: -D, once a branch holds nothing the PR
    # lacks. Every branch named for the issue goes, so a Done that stopped after removing a worktree
    # on another of them still finds it, and the found worktree's branch; but not one another
    # worktree has checked out, nor the base, which a stacked PR's or a fork's may be named like.
    pattern = issueBranch(key, typeMap)
    busy = {t["branch"]: t["dir"] for t in trees if t["dir"] != (resolved["dir"] if resolved else None)}
    for branch, tip in list(tips.items()):
        if branch == base or not (pattern.match(branch) or (head and branch == (resolved["branch"] if resolved else None))):
            continue
        if branch in busy:
            d = busy[branch]
            notes.append(
                f"Kept the branch {branch}: it's checked out in {d}."
                if os.path.exists(d)
                else f"Kept the branch {branch}: git lists it in {d}, but the folder is gone; run git worktree prune, then Done again."
            )
            continue
        if inPullRequest(checkout, tip, pr):
            # Against the tip checked, so git refuses a branch that moved since, as by a commit made in
            # the worktree while Done ran; then its config, which update-ref leaves.
            try:
                git(checkout, "update-ref", "-d", f"refs/heads/{branch}", tip)
            except RuntimeError as error:
                try:
                    now = git(checkout, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").strip()
                except RuntimeError:
                    now = ""
                if now and now != tip:
                    raise RuntimeError(f"the branch {branch} moved from {tip[:7]} while Done ran; check it, then Done again")
                raise error
            try:
                git(checkout, "config", "--remove-section", f"branch.{branch}")
            except RuntimeError:
                pass
            notes.append(f"Deleted the branch {branch}.")
        else:
            notes.append(f"Kept the branch {branch}: it's at {tip[:7]}, with commits {link}'s merged head lacks.")

    # No fetchCommits here: Redmine's version pulled commits from the repo itself so a "fixes #n"
    # commit could close the issue on its own. Jira has no equivalent for this tool to call -- the
    # GitHub for Jira app links commits/PRs to issues on its own, via a webhook, outside this process
    # -- and closing is a human's job regardless (see the invariant at the top of this file). So the
    # only Jira write left is a comment, never a status change.
    issue = jira.issue(key)
    comments = (issue["fields"].get("comment") or {}).get("comments") or []
    noted = any(commitUrl(pr) in json.dumps(c.get("body") or "") for c in comments)
    if not noted:
        jira.addComment(key, jira.textComment(doneNote(pr)))
        notes.append(f"Added the note to {key}.")
    notes.append(jira.closeNote(key))

    # Rule 4: the issue is finished, so it leaves the issues file that Start/Open added it to
    # (term.addIssue), its colour leaves the pulses file, and its worktree's folder leaves the plan
    # workspace (term.addFolders); the store's folder stays for the other issues. Done opens no
    # browser -- only the "Close KEY in Jira" note above, once, ever points Peter at the issue.
    worktree = issueWorktreePath(resolved, checkout, key)
    settings = term.planSettings(issuesFile, pulsesFile, workspaceDir)
    planWorkspace = term.planWorkspacePath(workspaceDir)
    notes.extend(filter(None, [
        unwritten(f"removing {key} from the issues file", f"Delete the {key} line from {term.resolveIssuesFile(issuesFile)}.",
                  lambda: term.removeIssue(key, issuesFile)),
        unwritten(f"clearing {key}'s colour", f"Run `plan pulse {key} off`.",
                  lambda: pulses.clearPulse(key, pulsesFile)),
        unwritten(f"removing {key}'s folder from the plan workspace", f"Remove {worktree} from the folders in {planWorkspace}.",
                  lambda: term.removeFolder(worktree, planWorkspace, settings)),
    ]))
    # The issue's workspace file is a throwaway: one that won't delete is a warning, not a failure.
    notes.extend(term.removeWorkspaceFiles(key, worktree, workspaceDir))
