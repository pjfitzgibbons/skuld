# Ported from reference/vscode/test/start.test.js. Redmine's numeric issue ids (80, 81, ...) become
# Jira keys (CIAM-80, CIAM-81, ...); tracker.name becomes issuetype.name; the fake `redmine` object
# becomes a fake IssueStore adapter (lib/issuestore.py) returning lib/issue.py's Issue. Redmine's start_date custom-field write has no CIAM equivalent and isn't ported
# (confirmed absent from lib/start.py's startIn()). The fault-injection matrix is reduced to the
# steps that exist in the ported start() (no "the Redmine update" row -- Start's single Jira write
# is the transition+comment, already covered by its own fault-injection case below), not every row
# of the 454-line source.
#
# Ported from lib/start.test.js. Faithful, behavior-preserving port.
import dataclasses
import os
import shutil
import subprocess
import tempfile
import unittest

from lib.issue import DONE, IN_PROGRESS, NEW, Blocker, Issue

from lib.start import (
    DEFAULT_TYPE_MAP,
    branchName,
    existingWorktree,
    issueBranch,
    lock,
    mainCheckout,
    prefixFor,
    settingUp,
    slug,
    start,
    worktreeDir,
    worktrees,
)


class PureFunctionTests(unittest.TestCase):
    def test_slug_takes_first_five_words_lowercased_hyphenated(self):
        self.assertEqual(slug("Go requests time out under load, sometimes"), "go-requests-time-out-under")
        self.assertEqual(slug("Fix: the thing!"), "fix-the-thing")

    def test_branchName_maps_issue_type_to_prefix_else_chore(self):
        self.assertEqual(branchName(typed("Story")), "feat/CIAM-9-go-requests-time-out")
        self.assertEqual(branchName(typed("Bug")), "fix/CIAM-9-go-requests-time-out")
        self.assertEqual(branchName(typed("Epic")), "chore/CIAM-9-go-requests-time-out")

    def test_type_names_match_without_regard_to_case(self):
        self.assertEqual(branchName(typed("bug")), "fix/CIAM-9-go-requests-time-out")
        self.assertEqual(branchName(typed("task")), "feat/CIAM-9-go-requests-time-out")
        self.assertEqual(prefixFor("BUG", {"Bug": "hotfix"}), "hotfix")

    def test_a_type_the_map_lacks_gets_chore(self):
        self.assertEqual(prefixFor("question"), "chore")

    def test_worktreeDir_sits_beside_checkout(self):
        self.assertEqual(worktreeDir("/home/p/code/widgets", "CIAM-9"), "/home/p/code/widgets.worktrees/CIAM-9")

    def test_issueBranch_matches_any_configured_prefix_or_none_but_not_unrelated(self):
        re_ = issueBranch("CIAM-9", DEFAULT_TYPE_MAP)
        self.assertTrue(re_.match("feat/CIAM-9-go-requests"))
        self.assertTrue(re_.match("fix/CIAM-9-x"))
        self.assertTrue(re_.match("chore/CIAM-9-x"))
        self.assertTrue(re_.match("CIAM-9-x"))
        self.assertFalse(re_.match("release/CIAM-90-x"))
        self.assertFalse(re_.match("feat/CIAM-99-x"))

    def test_issueBranch_only_accepts_configured_typemap_prefixes_chore_or_none(self):
        # M2 made issueBranch independent of typeMap -- any single prefix segment, a prefix absent
        # from the configured map included -- so Start wouldn't orphan a branch recognized under a
        # stale typeMap. That was too permissive: Start would then adopt a TEAMMATE's unrelated
        # branch (hotfix/CIAM-5-x) as if it were this issue's own (the reviewer's example). N3
        # restricts acceptance to the configured typeMap's values, the "chore" fallback
        # branchName() also uses, or no prefix at all -- never an arbitrary one -- and every
        # caller now threads the real, current config typeMap through instead, which keeps M2's
        # "don't orphan a real branch" goal.
        #
        # N9 added DEFAULT_TYPE_MAP's own values ("feat", "fix") to what's always accepted (below),
        # so this test's customTypeMap case no longer refuses feat/ -- see the N9 test for that.
        customTypeMap = {"Story": "story"}
        re_ = issueBranch("CIAM-9", customTypeMap)
        self.assertTrue(re_.match("story/CIAM-9-go-requests"))
        self.assertTrue(re_.match("chore/CIAM-9-x"))  # the fallback prefix branchName() uses when unmapped
        self.assertTrue(re_.match("CIAM-9-x"))  # unprefixed
        self.assertFalse(re_.match("hotfix/CIAM-9-x"))  # a teammate's unrelated branch, never adoptable
        self.assertFalse(re_.match("feat/CIAM-99-x"))

    def test_issueBranch_still_accepts_default_type_map_prefixes_under_narrower_configured_map(self):
        # config.py's load() merges typeMap shallowly: a config overriding ONE issue type (Story ->
        # story) entirely replaces the default map, losing Task/Bug's "feat"/"fix". Without this,
        # Start/Done would then refuse branches made before that config change as if they were a
        # teammate's unrelated branch -- exactly the "orphan a real branch" failure N3 fixed, just
        # triggered by a config edit instead of a stale caller. hotfix/ (truly unrelated, in
        # neither map) must still be refused.
        customTypeMap = {"Story": "story"}
        re_ = issueBranch("CIAM-9", customTypeMap)
        self.assertTrue(re_.match("story/CIAM-9-go-requests"))  # the new, configured prefix
        self.assertTrue(re_.match("feat/CIAM-9-x"))  # DEFAULT_TYPE_MAP's prefix, from before the config change
        self.assertTrue(re_.match("fix/CIAM-9-x"))  # DEFAULT_TYPE_MAP's other prefix
        self.assertFalse(re_.match("hotfix/CIAM-9-x"))  # in neither map -- still refused
        self.assertFalse(re_.match("feat/CIAM-99-x"))  # right prefix, wrong key

    def test_worktrees_parses_porcelain_main_first_detached_branch_null(self):
        porcelain = "\n".join(
            [
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n",
                "worktree /repo.worktrees/CIAM-9\nHEAD def\nbranch refs/heads/feat/CIAM-9-x\n",
                "worktree /repo.worktrees/CIAM-8\nHEAD ghi\ndetached\n",
            ]
        )
        trees = worktrees(porcelain)
        self.assertTrue(trees[0]["main"])
        self.assertEqual(trees[0]["branch"], "main")
        self.assertEqual(trees[1]["branch"], "feat/CIAM-9-x")
        self.assertIsNone(trees[2]["branch"])
        self.assertFalse(trees[2]["main"])

    def test_existingWorktree_finds_worktree_already_on_issues_branch(self):
        porcelain = "\n".join(
            [
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n",
                "worktree /repo.worktrees/CIAM-9\nHEAD def\nbranch refs/heads/feat/CIAM-9-x\n",
            ]
        )
        found = existingWorktree(porcelain, "CIAM-9", "/repo.worktrees/CIAM-9")
        self.assertEqual(found, {"dir": "/repo.worktrees/CIAM-9", "branch": "feat/CIAM-9-x", "main": False})

    def test_existingWorktree_refuses_to_adopt_teammates_unrelated_prefix_branch(self):
        # hotfix/ isn't in DEFAULT_TYPE_MAP's values or the "chore" fallback, so a worktree sitting
        # on hotfix/CIAM-9-x (a teammate's branch, not one Start or branchName() would ever make
        # for this issue type) must not be treated as this issue's own -- a different dir on it
        # falls through to the "checked out on an unrelated branch" refusal the function already
        # raises for that case.
        porcelain = "\n".join(
            [
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n",
                "worktree /repo.worktrees/other\nHEAD def\nbranch refs/heads/hotfix/CIAM-9-x\n",
            ]
        )
        found = existingWorktree(porcelain, "CIAM-9", "/repo.worktrees/CIAM-9")
        self.assertIsNone(found)

    def test_existingWorktree_adopts_configured_prefix_under_custom_typemap(self):
        customTypeMap = {"Story": "story"}
        porcelain = "\n".join(
            [
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n",
                "worktree /repo.worktrees/CIAM-9\nHEAD def\nbranch refs/heads/story/CIAM-9-x\n",
            ]
        )
        found = existingWorktree(porcelain, "CIAM-9", "/repo.worktrees/CIAM-9", "Start", customTypeMap)
        self.assertEqual(found, {"dir": "/repo.worktrees/CIAM-9", "branch": "story/CIAM-9-x", "main": False})

    def test_existingWorktree_refuses_worktree_at_target_dir_on_unrelated_branch(self):
        porcelain = "\n".join(
            [
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n",
                "worktree /repo.worktrees/CIAM-9\nHEAD def\nbranch refs/heads/some-other-thing\n",
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "is checked out on some-other-thing, not a branch for CIAM-9"):
            existingWorktree(porcelain, "CIAM-9", "/repo.worktrees/CIAM-9")


# Real temp git repos, exactly as the source tests do: a bare "origin" plus a clone, never mocked.
def sandbox():
    root = tempfile.mkdtemp(prefix="jira-plan-start-")
    bare = os.path.join(root, "origin.git")
    checkout = os.path.join(root, "demo")
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", bare], check=True)

    def git(cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout

    scratch = os.path.join(root, "scratch")
    git(root, "clone", "--quiet", bare, "scratch")
    with open(os.path.join(scratch, "a.txt"), "w") as f:
        f.write("a\n")
    git(scratch, "config", "user.email", "t@example.com")
    git(scratch, "config", "user.name", "T")
    git(scratch, "add", "-A")
    git(scratch, "commit", "-q", "-m", "init")
    git(scratch, "push", "-q", "origin", "main")
    git(root, "clone", "--quiet", bare, "demo")
    git(checkout, "config", "user.email", "t@example.com")
    git(checkout, "config", "user.name", "T")
    return {"root": root, "checkout": checkout, "git": git, "checkouts": {"CIAM": checkout}}


# A fake IssueStore adapter with the surface start() uses: issue(), statusTransitionId(),
# transition(), addComment(). issues: dict<key, Issue>. failTransition fails the transition itself
# (L4's pre-check, statusTransitionId, still succeeds), so the resumable-after-failure test still
# exercises a worktree that got made before a later step failed, not a Start refused before
# anything was created.
class FakeStore:
    def __init__(self, issues, failTransition=False, noTransitionAvailable=False):
        self.issues = issues
        self.comments = []
        self.transitions = []
        self.reads = 0
        self.failTransition = failTransition
        self.noTransitionAvailable = noTransitionAvailable

    def issue(self, key):
        self.reads += 1
        issue = self.issues.get(key)
        if not issue:
            raise RuntimeError(f"no such issue {key}")
        return issue

    def statusTransitionId(self, key, category):
        if self.noTransitionAvailable:
            raise RuntimeError(f"{key} can't be moved to {category}")
        return "31"

    def transition(self, key, category):
        if self.failTransition:
            raise RuntimeError("transition failed")
        self.transitions.append([key, category])
        self.issues[key] = inProgress(self.issues[key])
        return self.issues[key]

    def addComment(self, key, text):
        self.comments.append([key, text])


def toDoIssue(key, summary="Go requests time out", issuetype="Story", blockers=()):
    return Issue(key=key, summary=summary, status="To Do", category=NEW, issueType=issuetype, blockers=tuple(blockers))


# CIAM-9 "Go requests time out" with the given type name.
def typed(issueType):
    return toDoIssue("CIAM-9", "Go requests time out", issueType)


def inProgress(issue):
    return dataclasses.replace(issue, status="In Progress", category=IN_PROGRESS)


class StartTests(unittest.TestCase):
    def setUp(self):
        self.s = sandbox()
        self.addCleanup(shutil.rmtree, self.s["root"], True)

    def test_makes_worktree_from_origins_default_branch_and_transitions_issue(self):
        jira = FakeStore({"CIAM-9": toDoIssue("CIAM-9")})
        result = start(jira, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=self.s["checkout"])
        self.assertTrue(result["created"])
        self.assertTrue(result["started"])
        self.assertTrue(os.path.exists(result["dir"]))
        self.assertEqual(result["branch"], "feat/CIAM-9-go-requests-time-out")
        self.assertEqual(jira.transitions, [["CIAM-9", IN_PROGRESS]])
        self.assertEqual(len(jira.comments), 1)

    def test_reads_the_issue_once_and_returns_it_as_it_left_it(self):
        jira = FakeStore({"CIAM-9": toDoIssue("CIAM-9")})
        result = start(jira, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=self.s["checkout"])
        self.assertEqual(jira.reads, 1)
        self.assertEqual(result["issue"].status, "In Progress")

    def test_is_noop_second_time_reuses_worktree_doesnt_transition_again(self):
        jira = FakeStore({"CIAM-9": toDoIssue("CIAM-9")})
        first = start(jira, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=self.s["checkout"])
        jira.issues["CIAM-9"] = inProgress(jira.issues["CIAM-9"])
        second = start(jira, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=self.s["checkout"])
        self.assertFalse(second["created"])
        self.assertEqual(second["dir"], first["dir"])
        self.assertEqual(jira.transitions, [["CIAM-9", IN_PROGRESS]])

    def test_asks_confirm_about_blockers_before_making_new_worktree_and_stops_on_no(self):
        blocker = Blocker("CIAM-1", "blocker", "To Do")
        issue = toDoIssue("CIAM-9", "x", "Story", [blocker])
        jira = FakeStore({"CIAM-9": issue})
        seen = []

        def confirm(blockers):
            seen.append(blockers)
            return False

        result = start(jira, self.s["checkouts"], "CIAM-9", confirm, cwd=self.s["checkout"])
        self.assertIsNone(result)
        self.assertEqual(seen, [[blocker]])
        self.assertFalse(os.path.exists(self.s["checkout"] + ".worktrees"))

    def test_refuses_issue_thats_already_done(self):
        issue = Issue(key="CIAM-9", summary="x", status="Done", category=DONE, issueType="Bug")
        jira = FakeStore({"CIAM-9": issue})
        with self.assertRaisesRegex(RuntimeError, "CIAM-9 is Done; refresh Next up"):
            start(jira, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=self.s["checkout"])

    def test_refuses_key_with_no_checkout_configured_for_its_project(self):
        jira = FakeStore({"OTHER-1": toDoIssue("OTHER-1")})
        notARepo = tempfile.mkdtemp(prefix="jira-plan-norepo-")
        self.addCleanup(shutil.rmtree, notARepo, True)
        with self.assertRaisesRegex(RuntimeError, "project OTHER lists no repo in checkouts; set checkouts.OTHER"):
            start(jira, {}, "OTHER-1", lambda blockers: True, cwd=notARepo)

    def test_refuses_from_an_unlisted_repo_before_any_branch_worktree_or_status_change(self):
        jira = FakeStore({"OTHER-1": toDoIssue("OTHER-1")})
        branchesBefore = self.s["git"](self.s["checkout"], "branch", "--list")
        with self.assertRaisesRegex(RuntimeError, "project OTHER lists no repo in checkouts"):
            start(jira, self.s["checkouts"], "OTHER-1", lambda blockers: True, cwd=self.s["checkout"])
        self.assertEqual(self.s["git"](self.s["checkout"], "branch", "--list"), branchesBefore)
        self.assertFalse(os.path.exists(self.s["checkout"] + ".worktrees"))
        self.assertEqual(jira.transitions, [])
        self.assertEqual(jira.comments, [])

    def test_new_work_run_from_an_unlisted_repo_goes_in_the_listed_one(self):
        # The issue store case: `plan start` run from a repo the project doesn't list.
        unlisted = sandbox()
        self.addCleanup(shutil.rmtree, unlisted["root"], True)
        jira = FakeStore({"CIAM-9": toDoIssue("CIAM-9")})
        result = start(jira, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=unlisted["checkout"])
        self.assertEqual(
            os.path.realpath(result["dir"]),
            os.path.realpath(os.path.join(self.s["checkout"] + ".worktrees", "CIAM-9")),
        )
        self.assertFalse(os.path.exists(unlisted["checkout"] + ".worktrees"))
        self.assertEqual(unlisted["git"](unlisted["checkout"], "branch", "--list"), "* main\n")

    def test_resumable_after_worktree_add_succeeds_but_jira_transition_fails(self):
        issues = {"CIAM-9": toDoIssue("CIAM-9")}
        failing = FakeStore(issues, failTransition=True)
        with self.assertRaises(RuntimeError):
            start(failing, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=self.s["checkout"])
        # The worktree exists, and the setting-up marker is still there, so the retry finishes the job.
        working = FakeStore(issues)
        result = start(working, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=self.s["checkout"])
        self.assertFalse(result["created"])
        self.assertTrue(result["started"])
        self.assertEqual(working.transitions, [["CIAM-9", IN_PROGRESS]])

    def test_refuses_before_creating_anything_when_in_progress_transition_isnt_available(self):
        jira = FakeStore({"CIAM-9": toDoIssue("CIAM-9")}, noTransitionAvailable=True)
        with self.assertRaisesRegex(RuntimeError, f"can't be moved to {IN_PROGRESS}"):
            start(jira, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=self.s["checkout"])
        # Nothing was created: no worktree directory, no branch, no setting-up marker.
        self.assertFalse(os.path.exists(self.s["checkout"] + ".worktrees"))
        self.assertEqual(jira.transitions, [])

    def test_refuses_when_issues_branch_is_checked_out_in_main_checkout(self):
        self.s["git"](self.s["checkout"], "checkout", "-q", "-b", "feat/CIAM-9-go-requests-time-out")
        jira = FakeStore({"CIAM-9": toDoIssue("CIAM-9")})
        with self.assertRaisesRegex(RuntimeError, "is checked out in the main checkout"):
            start(jira, self.s["checkouts"], "CIAM-9", lambda blockers: True, cwd=self.s["checkout"])


class LockTests(unittest.TestCase):
    def test_lock_excludes_second_start_on_same_key_and_release_clears_it(self):
        dir = tempfile.mkdtemp(prefix="jira-plan-lock-")
        self.addCleanup(shutil.rmtree, dir, True)
        release = lock(dir, "CIAM-9", "Start")
        with self.assertRaisesRegex(RuntimeError, "being started in another window"):
            lock(dir, "CIAM-9", "Start")
        release()
        lock(dir, "CIAM-9", "Start")()  # doesn't raise

    def test_settingUp_names_marker_path_inside_git_common_dir(self):
        self.assertEqual(settingUp("/repo/.git", "CIAM-9"), "/repo/.git/plan-setting-up-CIAM-9")


class MainCheckoutTests(unittest.TestCase):
    def test_resolves_project_root_and_git_dir_from_configured_checkout_path_when_cwd_isnt_a_repo(self):
        # cwd isn't inside any git repo, and exactly one repo is listed for the project's
        # checkouts, so mainCheckout() uses it without prompting (no pickRepo needed).
        s = sandbox()
        self.addCleanup(shutil.rmtree, s["root"], True)
        notARepo = tempfile.mkdtemp(prefix="jira-plan-norepo-")
        self.addCleanup(shutil.rmtree, notARepo, True)
        issue = toDoIssue("CIAM-9")
        result = mainCheckout(issue.key, s["checkouts"], notARepo)
        self.assertEqual(result["project"], "CIAM")
        self.assertEqual(os.path.realpath(result["checkout"]), os.path.realpath(s["checkout"]))

    def test_unlisted_cwd_repo_is_not_used_for_new_work_when_another_repo_is_listed(self):
        cwdRepo = sandbox()
        self.addCleanup(shutil.rmtree, cwdRepo["root"], True)
        listedRepo = sandbox()
        self.addCleanup(shutil.rmtree, listedRepo["root"], True)
        issue = toDoIssue("CIAM-9")
        result = mainCheckout(issue.key, {"CIAM": listedRepo["checkout"]}, cwdRepo["checkout"])
        self.assertEqual(os.path.realpath(result["checkout"]), os.path.realpath(listedRepo["checkout"]))

    def test_cwd_inside_a_listed_repo_resolves_that_repo_without_a_pick(self):
        repoA = sandbox()
        self.addCleanup(shutil.rmtree, repoA["root"], True)
        repoB = sandbox()
        self.addCleanup(shutil.rmtree, repoB["root"], True)

        def refusePick(options):
            raise AssertionError("no pick expected when cwd is a listed repo")

        issue = toDoIssue("CIAM-9")
        result = mainCheckout(issue.key, {"CIAM": [repoA["checkout"], repoB["checkout"]]}, repoB["checkout"], pickRepo=refusePick)
        self.assertEqual(os.path.realpath(result["checkout"]), os.path.realpath(repoB["checkout"]))

    def test_cwd_in_a_linked_worktree_of_a_listed_repo_resolves_its_main_checkout(self):
        repoA = sandbox()
        self.addCleanup(shutil.rmtree, repoA["root"], True)
        repoB = sandbox()
        self.addCleanup(shutil.rmtree, repoB["root"], True)
        linked = os.path.join(repoB["root"], "demo.worktrees", "other")
        repoB["git"](repoB["checkout"], "worktree", "add", "-q", "-b", "other", linked)

        def refusePick(options):
            raise AssertionError("no pick expected when cwd is in a listed repo's worktree")

        issue = toDoIssue("CIAM-9")
        result = mainCheckout(issue.key, {"CIAM": [repoA["checkout"], repoB["checkout"]]}, linked, pickRepo=refusePick)
        self.assertEqual(os.path.realpath(result["checkout"]), os.path.realpath(repoB["checkout"]))

    def test_existing_work_in_a_listed_repo_wins_over_cwds_own_repo(self):
        # The "started in A, opened from B" case: CIAM-9 already has a worktree in the listed
        # repo A; running from cwd repo B must still find and use A, since the worktree is the
        # record of which repo the issue lives in.
        cwdRepo = sandbox()
        self.addCleanup(shutil.rmtree, cwdRepo["root"], True)
        listedRepo = sandbox()
        self.addCleanup(shutil.rmtree, listedRepo["root"], True)
        jira = FakeStore({"CIAM-9": toDoIssue("CIAM-9")})
        start(jira, {"CIAM": listedRepo["checkout"]}, "CIAM-9", lambda blockers: True, cwd=listedRepo["checkout"])

        issue = toDoIssue("CIAM-9")
        result = mainCheckout(issue.key, {"CIAM": listedRepo["checkout"]}, cwdRepo["checkout"])
        self.assertEqual(os.path.realpath(result["checkout"]), os.path.realpath(listedRepo["checkout"]))

    def test_existing_work_in_more_than_one_repo_is_refused_naming_both(self):
        repoA = sandbox()
        self.addCleanup(shutil.rmtree, repoA["root"], True)
        repoB = sandbox()
        self.addCleanup(shutil.rmtree, repoB["root"], True)
        jira = FakeStore({"CIAM-9": toDoIssue("CIAM-9")})
        start(jira, {"CIAM": repoA["checkout"]}, "CIAM-9", lambda blockers: True, cwd=repoA["checkout"])
        start(jira, {"CIAM": repoB["checkout"]}, "CIAM-9", lambda blockers: True, cwd=repoB["checkout"])

        issue = toDoIssue("CIAM-9")
        with self.assertRaisesRegex(RuntimeError, "more than one repo"):
            mainCheckout(issue.key, {"CIAM": [repoA["checkout"], repoB["checkout"]]}, repoA["checkout"])

    def test_accepts_a_bare_checkout_string_as_a_one_item_list(self):
        s = sandbox()
        self.addCleanup(shutil.rmtree, s["root"], True)
        notARepo = tempfile.mkdtemp(prefix="jira-plan-norepo-")
        self.addCleanup(shutil.rmtree, notARepo, True)
        issue = toDoIssue("CIAM-9")
        result = mainCheckout(issue.key, {"CIAM": s["checkout"]}, notARepo)
        self.assertEqual(os.path.realpath(result["checkout"]), os.path.realpath(s["checkout"]))

    def test_prompts_with_fzf_when_cwd_isnt_a_repo_and_several_repos_are_listed(self):
        repoA = sandbox()
        self.addCleanup(shutil.rmtree, repoA["root"], True)
        repoB = sandbox()
        self.addCleanup(shutil.rmtree, repoB["root"], True)
        notARepo = tempfile.mkdtemp(prefix="jira-plan-norepo-")
        self.addCleanup(shutil.rmtree, notARepo, True)
        seen = []

        def fakePick(options):
            seen.append(sorted(os.path.realpath(o) for o in options))
            return options[1]

        issue = toDoIssue("CIAM-9")
        result = mainCheckout(
            issue.key,
            {"CIAM": [repoA["checkout"], repoB["checkout"]]},
            notARepo,
            pickRepo=fakePick,
        )
        self.assertEqual(
            seen,
            [sorted([os.path.realpath(repoA["checkout"]), os.path.realpath(repoB["checkout"])])],
        )
        self.assertEqual(os.path.realpath(result["checkout"]), os.path.realpath(repoB["checkout"]))

    def test_refuses_when_cwd_isnt_a_repo_and_nothing_is_listed(self):
        notARepo = tempfile.mkdtemp(prefix="jira-plan-norepo-")
        self.addCleanup(shutil.rmtree, notARepo, True)
        issue = toDoIssue("OTHER-1")
        with self.assertRaisesRegex(RuntimeError, "project OTHER lists no repo in checkouts; set checkouts.OTHER"):
            mainCheckout(issue.key, {}, notARepo)

    def test_refuses_when_cwd_is_an_unlisted_repo_and_nothing_is_listed(self):
        s = sandbox()
        self.addCleanup(shutil.rmtree, s["root"], True)
        issue = toDoIssue("OTHER-1")
        with self.assertRaisesRegex(RuntimeError, "project OTHER lists no repo in checkouts; set checkouts.OTHER"):
            mainCheckout(issue.key, {}, s["checkout"])


if __name__ == "__main__":
    unittest.main()
