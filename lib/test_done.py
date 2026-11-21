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
from datetime import date
from unittest import mock

from lib import done as done_mod
from lib.fileissues import FileIssues
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

    def browseUrl(self, key):
        return f'{self.settings["baseUrl"]}/browse/{key}'

    def closeNote(self, key):
        return f'Close {key} in Jira: {self.browseUrl(key)}'


def inProgressIssue(key=KEY):
    return {"key": key, "fields": {"status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}, "comment": {"comments": []}}}


# Real git: a bare "origin", a clone simulating GitHub's side where the PR gets squash-merged, and
# the main checkout with the issue's worktree, exactly as the source's sandbox() does.
def sandbox(merged=True, key=KEY):
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

    branch = f"feat/{key}-go-requests"
    dir = worktreeDir(checkout, key)
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
        git(github, "commit", "-q", "-m", f"feature work (#1) ({key})")
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
        "title": f"feature work ({key})",
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
        "checkouts": {key.split("-")[0]: checkout},
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

        # The plan window is never reached: askWindow records each ask and answers with self.answer.
        self.asked = []
        self.answer = lambda route, key: [f"ran: window did {route}"]
        patch = mock.patch.object(term, "askWindow", side_effect=lambda route, key, workspaceDir=None: self.ask(route, key))
        patch.start()
        self.addCleanup(patch.stop)

    def ask(self, route, key):
        self.asked.append(route)
        return self.answer(route, key)

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def sandbox(self, merged=True, key=KEY):
        s = sandbox(merged, key)
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

    def test_a_failing_last_write_warns_what_to_do_by_hand_and_done_still_finishes(self):
        s = self.sandbox()
        planWorkspace = term.planWorkspacePath()
        term.addIssue(KEY)
        pulses.setPulse(KEY, "green")
        term.addFolders([s["dir"]], planWorkspace, {})
        with open(planWorkspace, "w") as f:
            f.write('{\n  // mine\n  "folders": [],\n}\n')  # JSONC: the plan workspace can't be read
        with mock.patch.object(term, "removeIssue", side_effect=OSError("disk full")):
            result = finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        warnings = [n for n in result["notes"] if n.startswith("Warning:")]
        self.assertEqual(len(warnings), 2)
        self.assertIn(f"removing {KEY} from the issues file failed: disk full", warnings[0])
        self.assertIn("Delete the", warnings[0])
        self.assertIn("could not be read", warnings[1])
        self.assertIn(f"Remove {os.path.realpath(s['dir'])} from the folders in", warnings[1])
        self.assertEqual(pulses.readPulses(), {})  # the step between the two failures still ran
        self.assertFalse(os.path.exists(s["dir"]))
        self.assertTrue(any(f"Close {KEY} in Jira" in n for n in result["notes"]))

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

    # --- the teardown of the issue's windows and ticket (SK-0108) ---

    STORE_TICKET = "---\nstatus: in-progress\ntype: task\nopened: 2026-11-21\nclosed:\n---\n\n# A ticket\n"
    STORE_KEY = "SK-0108"

    def storeSandbox(self):
        s = self.sandbox(key=self.STORE_KEY)
        store = tempfile.mkdtemp(prefix="skuld-done-store-")
        self._dirs.append(store)
        s["ticket"] = os.path.join(store, f"{self.STORE_KEY}-a-ticket.md")
        with open(s["ticket"], "w") as f:
            f.write(self.STORE_TICKET)
        s["store"] = FileIssues({"dir": store})
        return s

    def ticketText(self, s):
        with open(s["ticket"]) as f:
            return f.read()

    def finishStore(self, s, **kwargs):
        return finish(s["store"], s["checkouts"], self.STORE_KEY, view=s["view"], cwd=s["checkout"], **kwargs)

    def test_a_store_issue_is_torn_down_in_order_terminals_pr_tab_worktree_ticket_tab_index(self):
        s = self.storeSandbox()
        events = []
        term.writeWorkspaceFile(term.workspaceFilePath(self.STORE_KEY), {"settings": {"plan.key": self.STORE_KEY}})

        def answer(route, key):
            workspace = term.readPlanWorkspace(term.workspaceFilePath(key))
            events.append((route, os.path.exists(s["dir"]), "status: done" in self.ticketText(s), workspace["settings"].get("plan.prUrl")))
            return [f"ran: {route}"]

        self.answer = answer
        with mock.patch.object(FileIssues, "reindex", lambda store: events.append("reindex") or "ran: indexed"):
            result = self.finishStore(s)
        prUrl = s["pr"]["url"]
        self.assertEqual(events, [("done", True, False, prUrl), ("done-tab", False, True, prUrl), "reindex"])
        notes = result["notes"]
        order = ["ran: done", "Removed the worktree", "ran: set SK-0108 to status: done", "ran: done-tab", "ran: indexed"]
        positions = [next(i for i, n in enumerate(notes) if n.startswith(o) or o in n) for o in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(f"closed: {date.today().isoformat()}", self.ticketText(s))

    def test_a_terminal_that_wont_exit_is_reported_and_the_later_steps_still_run(self):
        s = self.storeSandbox()
        self.answer = lambda route, key: (
            [f"failed: Claude {key} is still running after Ctrl-C twice; left open, close it by hand"] if route == "done" else ["ran: tab"]
        )
        with mock.patch.object(FileIssues, "reindex", lambda store: "ran: indexed"):
            result = self.finishStore(s)
        self.assertIn(f"failed: Claude {self.STORE_KEY} is still running after Ctrl-C twice; left open, close it by hand", result["notes"])
        self.assertEqual(self.asked, ["done", "done-tab"])
        self.assertFalse(os.path.exists(s["dir"]))
        self.assertIn("status: done", self.ticketText(s))
        self.assertIn("ran: indexed", result["notes"])

    def test_a_second_done_changes_nothing_and_raises_nothing(self):
        s = self.storeSandbox()
        with mock.patch.object(FileIssues, "reindex", lambda store: "skipped: current"):
            self.finishStore(s)
            first = self.ticketText(s)
            self.answer = lambda route, key: ["skipped: already closed"]
            result = self.finishStore(s)
        self.assertEqual(self.ticketText(s), first)
        self.assertIn(f"skipped: {self.STORE_KEY} is already done and closed {date.today().isoformat()}.", result["notes"])
        self.assertIsNone(result["dir"])

    def test_with_vs_code_unreachable_each_window_step_is_skipped_by_name_and_the_ticket_is_still_closed(self):
        s = self.storeSandbox()

        def unreachable(route, key):
            raise RuntimeError("VS Code wasn't reached (no code)")

        self.answer = unreachable
        events = []
        with mock.patch.object(FileIssues, "reindex", lambda store: events.append("reindex") or "ran: indexed"):
            result = self.finishStore(s)
        skipped = [n for n in result["notes"] if n.startswith("skipped:") and "VS Code wasn't reached" in n]
        self.assertEqual(len(skipped), 5)  # Shell, Claude, Review, the PR tab, the ticket tab
        self.assertTrue(all("by hand" in n or "close the tab" in n for n in skipped))
        self.assertIn("status: done", self.ticketText(s))
        self.assertEqual(events, ["reindex"])
        self.assertFalse(os.path.exists(s["dir"]))

    def test_a_window_that_didnt_answer_is_waited_for_once(self):
        s = self.storeSandbox()

        def silent(route, key):
            raise RuntimeError("the plan window didn't answer within 60 seconds")

        self.answer = silent
        with mock.patch.object(FileIssues, "reindex", lambda store: "ran: indexed"):
            result = self.finishStore(s)
        self.assertEqual(self.asked, ["done"])  # done-tab is skipped without a second wait
        self.assertEqual(len([n for n in result["notes"] if n.startswith("skipped:") and "didn't answer" in n]), 5)

    def test_a_second_done_after_a_finished_one_says_so_and_runs_the_tail(self):
        s = self.storeSandbox()
        events = []
        with mock.patch.object(FileIssues, "reindex", lambda store: events.append("reindex") or "skipped: current"):
            self.finishStore(s)
            # the PR's branch is gone from origin too, as GitHub's delete-on-merge and fetch --prune leave it
            git(s["checkout"], "update-ref", "-d", f"refs/remotes/origin/{s['branch']}")
            self.asked.clear()
            result = self.finishStore(s)
        self.assertIn(f"{self.STORE_KEY} is already done; its worktree and branch are gone.", result["notes"])
        self.assertIn(f"skipped: {self.STORE_KEY} is already done and closed {date.today().isoformat()}.", result["notes"])
        self.assertEqual(self.asked, ["done-tab"])
        self.assertEqual(events, ["reindex", "reindex"])

    def test_a_jira_issue_with_dones_note_and_no_branch_is_already_done_one_without_is_not_shipped(self):
        s = self.sandbox()
        git(s["checkout"], "worktree", "remove", s["dir"])
        git(s["checkout"], "branch", "-D", s["branch"])
        git(s["checkout"], "update-ref", "-d", f"refs/remotes/origin/{s['branch']}")
        issue = inProgressIssue()
        with self.assertRaisesRegex(RuntimeError, "Ship it first"):
            finish(FakeJira(issue), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        issue["fields"]["comment"]["comments"].append({"body": {"text": "Done: [o#1](u) merged as [abcdef1](c)."}})
        result = finish(FakeJira(issue), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertIn(f"{KEY} is already done; its worktree and branch are gone.", result["notes"])
        self.assertEqual(self.asked, ["done-tab"])

    def test_an_unknown_store_key_is_still_an_error(self):
        s = self.storeSandbox()
        with self.assertRaisesRegex(RuntimeError, "no issue file for SK-0999"):
            finish(s["store"], s["checkouts"], "SK-0999", view=s["view"], cwd=s["checkout"])

    def test_a_jira_issue_has_its_windows_closed_and_no_status_or_index_written(self):
        s = self.sandbox()
        result = finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertEqual(self.asked, ["done", "done-tab"])  # FakeJira has no close() or reindex() to call
        self.assertTrue(any(f"Close {KEY} in Jira" in n for n in result["notes"]))
        self.assertTrue(any(n.startswith("skipped: no store index") for n in result["notes"]))

    def test_a_done_stopped_after_the_terminals_is_finished_by_the_next(self):
        s = self.storeSandbox()

        class Stopped(Exception):
            pass

        def stop(route, key):
            raise Stopped()

        self.answer = stop
        with self.assertRaises(Stopped):
            self.finishStore(s)
        self.assertTrue(os.path.exists(s["dir"]))
        self.assertNotIn("status: done", self.ticketText(s))
        self.answer = lambda route, key: [f"ran: {route}"]
        with mock.patch.object(FileIssues, "reindex", lambda store: "ran: indexed"):
            result = self.finishStore(s)
        self.assertFalse(os.path.exists(s["dir"]))
        self.assertIn("status: done", self.ticketText(s))
        self.assertEqual(self.asked, ["done", "done", "done-tab"])
        self.assertIn("ran: indexed", result["notes"])

    def test_refuses_from_inside_the_issues_worktree_changing_nothing(self):
        s = self.storeSandbox()
        before = self.ticketText(s)
        with self.assertRaisesRegex(RuntimeError, re.escape(f"inside {self.STORE_KEY}'s worktree {os.path.realpath(s['dir'])}")):
            finish(s["store"], s["checkouts"], self.STORE_KEY, view=s["view"], cwd=os.path.join(s["dir"], "sub", ".."))
        self.assertEqual(self.asked, [])
        self.assertTrue(os.path.exists(s["dir"]))
        self.assertIn(s["branch"], git(s["checkout"], "branch", "--list", s["branch"]))
        self.assertEqual(self.ticketText(s), before)

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

    def test_removes_worktree_holding_the_e2e_suites_downloaded_vscode(self):
        s = self.sandbox()
        writeIgnoredFolder(s["dir"], "vscode/plan-window/e2e/.wdio-vscode-service", 5)
        finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertFalse(os.path.exists(s["dir"]))

    def test_removes_worktree_holding_ruffs_cache(self):
        s = self.sandbox()
        writeIgnoredFolder(s["dir"], ".ruff_cache", 3)
        finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        self.assertFalse(os.path.exists(s["dir"]))

    def test_refusal_names_an_ignored_folder_once_and_lists_none_of_its_files(self):
        s = self.sandbox()
        writeIgnoredFolder(s["dir"], "cache", 50)
        with self.assertRaises(RuntimeError) as raised:
            finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        lines = str(raised.exception).splitlines()
        self.assertIn("has ignored files its removal would delete", lines[0])
        self.assertEqual(lines[1:], ["cache/"])
        self.assertTrue(os.path.exists(s["dir"]))

    def test_refusal_caps_the_list_and_counts_the_rest(self):
        s = self.sandbox()
        for i in range(13):
            writeIgnoredFolder(s["dir"], f"cache{i:02d}", 1)
        with self.assertRaises(RuntimeError) as raised:
            finish(FakeJira(inProgressIssue()), s["checkouts"], KEY, view=s["view"], cwd=s["checkout"])
        lines = str(raised.exception).splitlines()
        self.assertEqual(len(lines), 1 + 10 + 1)
        self.assertEqual(lines[-1], "and 3 more")


def writeIgnoredFolder(dir, relative, count):
    folder = os.path.join(dir, relative)
    os.makedirs(os.path.join(folder, "sub"), exist_ok=True)
    # The repo's exclude file ignores the folder, as the repo's .gitignore does for a tool's cache.
    exclude = git(dir, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude").strip()
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    with open(exclude, "a") as f:
        f.write(f"/{relative}/\n")
    for i in range(count):
        with open(os.path.join(folder, "sub", f"f{i}.txt"), "w") as f:
            f.write("x\n")


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
