# Ported from reference/vscode/test/done.test.js, reworked around decision #5 (the single most
# safety-critical invariant in this port): Done must NEVER transition the Jira issue. Every source
# assertion of the shape `issue.status.name === "Closed"` becomes a status-UNCHANGED assertion plus
# a comment-posted + "Close KEY in Jira" assertion. linkedPullRequest and its tests are dropped
# entirely (no Jira analogue: decision #4 removes the "Pull requests" custom field). Redmine's
# fetchCommits/Keychain test is dropped entirely (no CIAM equivalent). The fault-injection matrix's
# "reading the status id"/"the Redmine update" rows become "posting the comment"; there is no
# "transition" row because finish() must never call jira.transition at all -- that is asserted
# directly, across every scenario, via a fake jira whose transition() raises.
#
# Ported from lib/done.test.js. Faithful, behavior-preserving port.
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from lib import done as done_mod
from lib import pulses, term
from lib.done import DISPOSABLE, branchForKey, doneNote, finish
from lib.start import worktreeDir

KEY = "CIAM-81"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


# L1: prFromBranch's `gh pr list --head` fallback (used once `gh pr view` fails, e.g. a branch gh
# can no longer resolve) must pass --state all, since list defaults to open PRs only and would
# otherwise miss a merged PR silently. done.py imports `run` from .start directly (`from .start
# import ... run ...`), so (like term.py's run) mocking lib.start.run after the fact doesn't reach
# done.py's own binding; patch lib.done.run directly instead.
class PrFromBranchTests(unittest.TestCase):
    def test_falls_back_to_gh_pr_list_head_state_all_and_prefers_merged(self):
        calls = []
        mergedPr = {"number": 5, "state": "MERGED", "url": "https://github.com/acme/widgets/pull/5"}
        closedPr = {"number": 3, "state": "CLOSED", "url": "https://github.com/acme/widgets/pull/3"}

        def fake_run(command, args, cwd):
            calls.append(args)
            if args[0] == "pr" and args[1] == "view":
                raise RuntimeError("no pull requests found for branch")
            if args[0] == "pr" and args[1] == "list":
                self.assertIn("--state", args, "gh pr list --head must pass --state all (L1)")
                self.assertEqual(args[args.index("--state") + 1], "all")
                # Two PRs on one branch: an older closed-unmerged attempt, then the real merged one
                # -- gh pr list returns newest first by default, so put the merged one second to
                # prove the code picks it by state, not by list order.
                return json.dumps([closedPr, mergedPr])
            raise RuntimeError(f"unexpected gh {' '.join(args)}")

        with mock.patch.object(done_mod, "run", side_effect=fake_run):
            pr = done_mod.prFromBranch("/repo", "feat/CIAM-81-go-requests")
        self.assertEqual(pr["number"], 5)
        self.assertEqual(pr["state"], "MERGED")

    def test_falls_back_to_newest_pr_when_none_on_branch_merged(self):
        older = {"number": 2, "state": "CLOSED"}
        newer = {"number": 4, "state": "OPEN"}

        def fake_run(command, args, cwd):
            if args[0] == "pr" and args[1] == "view":
                raise RuntimeError("no pull requests found for branch")
            if args[0] == "pr" and args[1] == "list":
                return json.dumps([older, newer])
            raise RuntimeError(f"unexpected gh {' '.join(args)}")

        with mock.patch.object(done_mod, "run", side_effect=fake_run):
            pr = done_mod.prFromBranch("/repo", "feat/CIAM-81-go-requests")
        self.assertEqual(pr["number"], 4)


# A fake Jira matching the surface finish() uses: issue(), textComment(), addComment(). transition()
# is present only so a wrongful call can be caught and fail the test loudly, never because finish()
# is expected to call it.
class FakeJira:
    def __init__(self, issue):
        self.settings = {"baseUrl": "https://cryoport.atlassian.net"}
        self._issue = issue
        self.comments = []
        self.transitioned = []

    def issue(self, key=None):
        return self._issue

    def textComment(self, text):
        return {"type": "doc", "text": text}

    def addComment(self, key, body):
        self.comments.append([key, body])

    def transition(self, key, target):
        self.transitioned.append([key, target])
        raise RuntimeError("finish() must never call jira.transition -- decision #5")

    def closeNote(self, key):
        return f'Close {key} in Jira: {self.settings["baseUrl"]}/browse/{key}'


def inProgressIssue(key=KEY):
    return {"key": key, "fields": {"status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}, "comment": {"comments": []}}}


# Real git: a bare "origin", a clone simulating GitHub's side where the PR gets squash-merged, and
# the main checkout with the issue's worktree, exactly as the source's sandbox() does.
def sandbox(merged=True):
    root = tempfile.mkdtemp(prefix="jira-plan-done-")
    bare = os.path.join(root, "origin.git")
    checkout = os.path.join(root, "demo")
    git(None, "init", "--quiet", "--bare", "-b", "main", bare)

    seed = os.path.join(root, "seed")
    git(root, "clone", "--quiet", bare, "seed")
    git(seed, "config", "user.email", "t@example.com")
    git(seed, "config", "user.name", "T")
    with open(os.path.join(seed, "a.txt"), "w") as f:
        f.write("a\n")
    # A configured repo ignores .tmp/, where pr's round files live.
    with open(os.path.join(seed, ".gitignore"), "w") as f:
        f.write(".tmp/\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "push", "-q", "origin", "main")

    git(root, "clone", "--quiet", bare, "demo")
    git(checkout, "config", "user.email", "t@example.com")
    git(checkout, "config", "user.name", "T")

    branch = f"feat/{KEY}-go-requests"
    dir = worktreeDir(checkout, KEY)
    os.makedirs(os.path.dirname(dir), exist_ok=True)
    git(checkout, "worktree", "add", "--quiet", "-b", branch, dir, "origin/main")
    with open(os.path.join(dir, "b.txt"), "w") as f:
        f.write("feature\n")
    git(dir, "add", "-A")
    git(dir, "commit", "-q", "-m", "feature work")
    git(dir, "push", "-q", "-u", "origin", branch)
    headRefOid = git(dir, "rev-parse", "HEAD").strip()

    mergeCommit = None
    if merged:
        # Simulate GitHub's squash merge by merging the branch into main on a second clone of
        # origin, standing in for GitHub's own copy, then pushing -- same as a real
        # squash-and-merge would leave.
        github = os.path.join(root, "github")
        git(root, "clone", "--quiet", bare, "github")
        git(github, "config", "user.email", "t@example.com")
        git(github, "config", "user.name", "T")
        git(github, "fetch", "-q", "origin", branch)
        git(github, "merge", "-q", "--squash", f"origin/{branch}")
        git(github, "commit", "-q", "-m", f"feature work (#1) ({KEY})")
        git(github, "push", "-q", "origin", "main")
        mergeCommit = git(github, "rev-parse", "HEAD").strip()

    # checkMerged()/finishIn() require the PR's repo to match origin's; origin here is a local bare
    # repo, so the fake PR's url is built to resolve to the same trailing owner/repo-shaped segments
    # repoOf() (done.py) would derive from it, rather than a real GitHub URL.
    originUrl = git(checkout, "remote", "get-url", "origin").strip()
    m = re.search(r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", originUrl)
    repoPart = "/".join(m.groups()).lower() if m else ""
    pr = {
        "url": f"https://github.com/{repoPart}/pull/1",
        "number": 1,
        "title": f"feature work ({KEY})",
        "headRefName": branch,
        "headRefOid": headRefOid,
        "baseRefName": "main",
        "mergeCommit": {"oid": mergeCommit} if mergeCommit else None,
        "isCrossRepository": False,
        "state": "MERGED" if merged else "OPEN",
    }

    def view(cwd, branch_):
        return pr

    return {
        "root": root,
        "checkout": checkout,
        "dir": dir,
        "branch": branch,
        "pr": pr,
        "view": view,
        "checkouts": {"CIAM": checkout},
    }


class DoneNoteTests(unittest.TestCase):
    def test_links_pr_and_merge_commit(self):
        pr = {"url": "https://github.com/acme/widgets/pull/1", "number": 1, "mergeCommit": {"oid": "abcdef1234567890"}}
        self.assertEqual(
            doneNote(pr),
            "Done: [widgets#1](https://github.com/acme/widgets/pull/1) merged as [abcdef1](https://github.com/acme/widgets/commit/abcdef1234567890).",
        )


class FinishTests(unittest.TestCase):
    # Every file finish() keeps outside the repo defaults to a temp folder, so no test reads or
    # writes the real ~/.cache/plan.
    def setUp(self):
        self._dirs = []
        self.cache = tempfile.mkdtemp(prefix="skuld-done-cache-")
        self._dirs.append(self.cache)
        for name, value in (
            ("DEFAULT_ISSUES_FILE", os.path.join(self.cache, "issues")),
            ("DEFAULT_WORKSPACE_DIR", os.path.join(self.cache, "code-workspaces")),
            ("DEFAULT_PULSES_FILE", os.path.join(self.cache, "pulses")),
        ):
            patch = mock.patch.object(term, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def sandbox(self, merged=True):
        s = sandbox(merged)
        self._dirs.append(s["root"])
        return s

    def test_removes_worktree_pulls_main_never_transitions(self):
        s = self.sandbox()
        issue = inProgressIssue()
        jira = FakeJira(issue)
        result = finish(jira, s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertFalse(os.path.exists(s["dir"]))
        self.assertEqual(git(s["checkout"], "rev-parse", "main").strip(), s["pr"]["mergeCommit"]["oid"])
        # The invariant: Jira's status is untouched, and nothing in finish() called transition().
        self.assertEqual(issue["fields"]["status"]["name"], "In Progress")
        self.assertEqual(jira.transitioned, [])
        self.assertEqual(len(jira.comments), 1)
        self.assertRegex(jira.comments[0][1]["text"], r"Done: \[origin#1\]")
        self.assertTrue(any(f"Close {KEY} in Jira" in n for n in result["notes"]))
        self.assertTrue(any(f"Close {KEY} in Jira: https://cryoport.atlassian.net/browse/{KEY}" in n for n in result["notes"]))

    def test_takes_the_issue_out_of_the_issues_pulses_and_plan_workspace_files(self):
        s = self.sandbox()
        planWorkspace = term.planWorkspacePath()
        term.addIssue("SK-0041")
        term.addIssue(KEY)
        pulses.setPulse(KEY, "green")
        pulses.setPulse("SK-0041", "orange")
        term.addFolders(["/w/SK-0041"], planWorkspace, {})
        term.addFolders([s["dir"]], planWorkspace, {})
        finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertEqual(term.issueKeys(), ["SK-0041"])
        self.assertEqual(pulses.readPulses(), {"SK-0041": "orange"})
        self.assertEqual(term.readPlanWorkspace(planWorkspace)["folders"], [{"path": "/w/SK-0041"}])

    def test_the_store_folder_stays(self):
        s = self.sandbox()
        planWorkspace = term.planWorkspacePath()
        term.addFolders([s["dir"], "/issues"], planWorkspace, {})
        finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertEqual(term.readPlanWorkspace(planWorkspace)["folders"], [{"path": "/issues"}])

    def test_an_issue_the_files_dont_list_leaves_them_unwritten(self):
        s = self.sandbox()
        term.addIssue("SK-0041")
        before = os.stat(term.resolveIssuesFile()).st_mtime_ns
        finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertEqual(os.stat(term.resolveIssuesFile()).st_mtime_ns, before)
        self.assertFalse(os.path.exists(term.resolvePulsesFile()))
        self.assertFalse(os.path.exists(term.planWorkspacePath()))

    def test_the_plan_workspace_in_a_configured_workspace_folder_loses_the_folder(self):
        s = self.sandbox()
        workspaceDir = os.path.join(s["root"], "ws")
        planWorkspace = term.planWorkspacePath(workspaceDir)
        term.addFolders([s["dir"]], planWorkspace, {})
        finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"], workspaceDir=workspaceDir)
        self.assertEqual(term.readPlanWorkspace(planWorkspace)["folders"], [])

    def test_deletes_the_issue_windows_workspace_files(self):
        s = self.sandbox()
        workspaceDir = os.path.join(s["root"], "ws")
        current = term.workspaceFilePath(KEY, workspaceDir)
        legacy = term.legacyWorkspaceFilePath(s["dir"], KEY)
        term.writeWorkspaceFile(current, {})
        open(legacy, "w").close()
        finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"], workspaceDir=workspaceDir)
        self.assertFalse(os.path.exists(current) or os.path.exists(legacy))

    def test_deletes_the_legacy_file_beside_a_worktree_moved_from_its_usual_place(self):
        s = self.sandbox()
        moved = os.path.join(s["root"], "elsewhere", KEY)
        os.makedirs(os.path.dirname(moved))
        git(s["checkout"], "worktree", "move", s["dir"], moved)
        legacy = term.legacyWorkspaceFilePath(moved, KEY)
        open(legacy, "w").close()
        finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"], workspaceDir=os.path.join(s["root"], "ws"))
        self.assertFalse(os.path.exists(moved))
        self.assertFalse(os.path.exists(legacy))

    def test_a_workspace_file_that_wont_delete_warns_and_done_still_finishes(self):
        s = self.sandbox()
        workspaceDir = os.path.join(s["root"], "ws")
        os.makedirs(os.path.join(workspaceDir, f"{KEY}.code-workspace"))  # a folder: os.remove refuses it
        result = finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"], workspaceDir=workspaceDir)
        self.assertFalse(os.path.exists(s["dir"]))
        self.assertTrue(any(n.startswith("Warning: couldn't delete") for n in result["notes"]))

    def test_is_idempotent_second_run_changes_nothing_and_posts_no_second_comment(self):
        s = self.sandbox()
        issue = inProgressIssue()
        jira = FakeJira(issue)
        finish(jira, s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        # The comment is now on the issue, as a real Jira GET would show on a rerun.
        repoUrl = re.sub(r"/pull/\d+$", "", s["pr"]["url"])
        oid = s["pr"]["mergeCommit"]["oid"]
        issue["fields"]["comment"]["comments"].append(
            {"body": json.dumps({"text": f"...{oid[:7]}...{repoUrl}/commit/{oid}"})}
        )
        result = finish(jira, s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertEqual(len(jira.comments), 1)
        self.assertEqual(jira.transitioned, [])
        self.assertIsNone(result["dir"])  # nothing left to remove

    def test_refuses_pr_that_hasnt_merged_changing_nothing(self):
        s = self.sandbox(merged=False)
        jira = FakeJira(inProgressIssue())
        with self.assertRaisesRegex(RuntimeError, "still open; nothing changed"):
            finish(jira, s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertTrue(os.path.exists(s["dir"]))
        self.assertEqual(jira.transitioned, [])
        self.assertEqual(jira.comments, [])

    def test_refuses_worktree_with_unpushed_work_merged_pr_lacks(self):
        s = self.sandbox()
        with open(os.path.join(s["dir"], "c.txt"), "w") as f:
            f.write("extra\n")
        git(s["dir"], "add", "-A")
        git(s["dir"], "commit", "-q", "-m", "unpushed")
        jira = FakeJira(inProgressIssue())
        with self.assertRaisesRegex(RuntimeError, r"with commits .* lacks"):
            finish(jira, s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertTrue(os.path.exists(s["dir"]))
        self.assertEqual(jira.transitioned, [])

    def test_refuses_when_worktree_has_uncommitted_changes(self):
        s = self.sandbox()
        with open(os.path.join(s["dir"], "b.txt"), "w") as f:
            f.write("dirty\n")
        jira = FakeJira(inProgressIssue())
        with self.assertRaisesRegex(RuntimeError, "has changes the merged PR lacks"):
            finish(jira, s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertTrue(os.path.exists(s["dir"]))

    def test_refuses_issue_thats_still_to_do(self):
        s = self.sandbox()
        issue = {"key": KEY, "fields": {"status": {"name": "To Do", "statusCategory": {"key": "new"}}, "comment": {"comments": []}}}
        jira = FakeJira(issue)
        with self.assertRaisesRegex(RuntimeError, f"{KEY} is To Do; refresh Next up"):
            finish(jira, s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])

    def test_refuses_when_unsent_replies_or_checklist_ticks_still_in_worktree(self):
        s = self.sandbox()
        os.makedirs(os.path.join(s["dir"], ".tmp", KEY, "replies"), exist_ok=True)
        with open(os.path.join(s["dir"], ".tmp", KEY, "replies", "9.md"), "w") as f:
            f.write("reply\n")
        jira = FakeJira(inProgressIssue())
        with self.assertRaisesRegex(RuntimeError, "has replies or checklist ticks pr hasn't sent"):
            finish(jira, s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertTrue(os.path.exists(s["dir"]))

    def test_deletes_merged_branch_with_no_worktree_left_resumed_after_partial_done(self):
        s = self.sandbox()
        # Simulate an earlier Done that removed the worktree but stopped before the branch delete.
        git(s["checkout"], "worktree", "remove", "--force", s["dir"])
        jira = FakeJira(inProgressIssue())
        result = finish(jira, s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertIsNone(result["dir"])
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(["git", "rev-parse", "--verify", s["branch"]], cwd=s["checkout"], capture_output=True, check=True)
        self.assertEqual(jira.transitioned, [])

    def test_refuses_pr_whose_repo_isnt_this_checkouts_origin(self):
        s = self.sandbox()
        pr = {**s["pr"], "url": "https://github.com/other/unrelated/pull/9"}
        jira = FakeJira(inProgressIssue())
        with self.assertRaisesRegex(RuntimeError, "isn't in .* origin"):
            finish(jira, s["checkouts"], KEY, view=lambda cwd, branch: pr, cwd=s["checkout"])


class DisposableTests(unittest.TestCase):
    def test_lists_ignored_names_worktree_removal_may_delete_unasked(self):
        self.assertIn(".tmp/", DISPOSABLE)
        self.assertIn("node_modules/", DISPOSABLE)


# N3: branchForKey(checkout, key, typeMap) threads typeMap into issueBranch() the same way
# existingWorktree() does, so Done accepts a configured prefix and refuses an unrelated one, not
# just "any prefixed branch" (the bug the reviewer found in start.js's regex).
def branchSandbox(branchName):
    checkout = tempfile.mkdtemp(prefix="jira-plan-done-branchkey-")
    git(checkout, "init", "--quiet", "-b", "main", ".")
    git(checkout, "config", "user.email", "t@example.com")
    git(checkout, "config", "user.name", "T")
    with open(os.path.join(checkout, "a.txt"), "w") as f:
        f.write("a\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "init")
    git(checkout, "branch", branchName)
    return checkout


class BranchForKeyTests(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def mk(self, branchName):
        checkout = branchSandbox(branchName)
        self._dirs.append(checkout)
        return checkout

    def test_accepts_configured_typemap_prefix(self):
        branch = f"story/{KEY}-go-requests"
        checkout = self.mk(branch)
        found = branchForKey(checkout, KEY, {"Story": "story"})
        self.assertEqual(found, branch)

    def test_refuses_unrelated_prefix_not_in_typemap(self):
        checkout = self.mk(f"hotfix/{KEY}-go-requests")
        found = branchForKey(checkout, KEY, {"Story": "story"})
        self.assertIsNone(found)

    def test_still_accepts_default_type_map_prefix_under_narrower_configured_typemap(self):
        # Same config.py shallow-merge hazard as N9's start.test.js case, exercised on Done's own
        # lookup: a branch made under the default map (feat/) before a config override narrowed
        # typeMap to { Story: "story" } must still resolve, not be treated as an orphaned branch.
        branch = f"feat/{KEY}-go-requests"
        checkout = self.mk(branch)
        found = branchForKey(checkout, KEY, {"Story": "story"})
        self.assertEqual(found, branch)


if __name__ == "__main__":
    unittest.main()
