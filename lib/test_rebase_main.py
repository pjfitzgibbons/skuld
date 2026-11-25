# lib/rebase_main.py through bin/rebase-main, against throwaway git repos (an origin and a clone).
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "bin" / "rebase-main"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
       "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com", "GIT_CONFIG_GLOBAL": "/dev/null"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


def commit(cwd, name, text, message):
    (cwd / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message)


def merge(cwd, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, env=ENV, capture_output=True, text=True)


def conflicted(origin, work):
    commit(work, "a.txt", "one\nTWO feature\nthree\n", "feature edits two")
    commit(origin, "a.txt", "one\nTWO main\nthree\n", "main edits two")


def pushed(work):
    """work's branch pushed to origin, as Ship leaves it."""
    git(work, "push", "-q", "-u", "origin", "feat/1-x")


class RebaseMainTest(unittest.TestCase):
    def setUp(self):
        """origin with main; work cloned from it on feat/1-x; main then moves on in each test."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.origin, self.work = self.tmp / "origin", self.tmp / "work"
        self.origin.mkdir()
        git(self.origin, "init", "-q", "-b", "main")
        commit(self.origin, "a.txt", "one\ntwo\nthree\n", "base")
        git(self.tmp, "clone", "-q", str(self.origin), str(self.work))
        git(self.work, "checkout", "-q", "-b", "feat/1-x")

    def testMergesMainInKeepingEveryBranchCommit(self):
        commit(self.work, "b.txt", "feature\n", "feature")
        commit(self.work, "d.txt", "feature 2\n", "feature 2")
        oldCommits = git(self.work, "rev-list", "origin/main..HEAD").split()
        oldTip = git(self.work, "rev-parse", "HEAD")
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        r = merge(self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        mainTip = git(self.work, "rev-parse", "origin/main")
        self.assertEqual(git(self.work, "rev-parse", "HEAD^1"), oldTip)
        self.assertEqual(git(self.work, "rev-parse", "HEAD^2"), mainTip)
        self.assertEqual(git(self.work, "log", "-1", "--format=%s"), "Merge origin/main into feat/1-x")
        self.assertLessEqual(set(oldCommits), set(git(self.work, "rev-list", "HEAD").split()))
        self.assertIn("origin has no feat/1-x; not pushed", r.stdout)
        self.assertNotEqual(git(self.work, "rev-parse", "main"), mainTip)  # local main untouched

    def testBranchWithNoCommitsOfItsOwnFastForwards(self):
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        r = merge(self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), git(self.work, "rev-parse", "origin/main"))
        self.assertEqual(git(self.work, "log", "--merges", "--oneline"), "")

    def testAlreadyHoldingMainDoesNothing(self):
        commit(self.work, "b.txt", "feature\n", "feature")
        head = git(self.work, "rev-parse", "HEAD")
        r = merge(self.work)
        self.assertEqual((r.returncode, git(self.work, "rev-parse", "HEAD")), (0, head))
        self.assertIn("nothing to merge", r.stdout)

    def assertRefusesWithoutChanging(self, setup, says):
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        setup(self.work)
        head = git(self.work, "rev-parse", "HEAD")
        r = merge(self.work)
        self.assertEqual((r.returncode, git(self.work, "rev-parse", "HEAD")), (2, head))
        self.assertIn(says, r.stderr)

    def testRefusesOnTheBaseBranch(self):
        self.assertRefusesWithoutChanging(lambda w: git(w, "checkout", "-q", "main"), "on main")

    def testRefusesWithUncommittedChanges(self):
        self.assertRefusesWithoutChanging(lambda w: (w / "a.txt").write_text("dirty\n"), "uncommitted changes")

    def testConflictStopsThenContinueRefusesMarkersAndFinishes(self):
        conflicted(self.origin, self.work)
        before = git(self.work, "rev-parse", "HEAD")

        r = merge(self.work)
        self.assertEqual(r.returncode, 1)
        self.assertIn("a.txt", r.stdout)
        self.assertIn("main edits two", r.stdout)
        self.assertEqual(git(self.work, "rev-parse", "refs/rebase-main/feat/1-x"), before)
        self.assertEqual(git(self.work, "rev-parse", "MERGE_HEAD"), git(self.work, "rev-parse", "origin/main"))
        self.assertEqual(merge(self.work, "status").returncode, 1)

        self.assertEqual(merge(self.work, "continue").returncode, 2)  # still unmerged
        git(self.work, "add", "a.txt")  # staged with the markers still in it
        r = merge(self.work, "continue")
        self.assertEqual(r.returncode, 2)
        self.assertIn("conflict markers", r.stderr)

        (self.work / "a.txt").write_text("one\nTWO main and feature\nthree\n")
        git(self.work, "add", "a.txt")
        r = merge(self.work, "continue")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD^1"), before)
        self.assertEqual(git(self.work, "rev-parse", "HEAD^2"), git(self.work, "rev-parse", "origin/main"))

    def testRefusesToStartMidMerge(self):
        conflicted(self.origin, self.work)
        self.assertEqual(merge(self.work).returncode, 1)
        r = merge(self.work)
        self.assertEqual(r.returncode, 2)
        self.assertIn("merge is already under way", r.stderr)

    def testAbortPutsTheBranchBack(self):
        conflicted(self.origin, self.work)
        before = git(self.work, "rev-parse", "HEAD")
        self.assertEqual(merge(self.work).returncode, 1)
        r = merge(self.work, "abort")
        self.assertEqual((r.returncode, git(self.work, "rev-parse", "HEAD"), git(self.work, "branch", "--show-current")),
                         (0, before, "feat/1-x"))

    def testPushesTheMergeWithoutForceWhenOriginHasTheBranch(self):
        commit(self.work, "b.txt", "feature\n", "feature")
        pushed(self.work)
        shipped = git(self.work, "rev-parse", "HEAD")
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        git(self.origin, "config", "receive.denyNonFastForwards", "true")  # a force push would be refused
        r = merge(self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pushed feat/1-x", r.stdout)
        self.assertEqual(git(self.origin, "rev-parse", "feat/1-x"), git(self.work, "rev-parse", "HEAD"))
        self.assertEqual(git(self.work, "rev-parse", "HEAD^1"), shipped)

    def testAnotherClonesPushIsNeverOverwritten(self):
        commit(self.work, "b.txt", "feature\n", "feature")
        pushed(self.work)
        other = self.tmp / "other"
        git(self.tmp, "clone", "-q", "-b", "feat/1-x", str(self.origin), str(other))
        commit(other, "d.txt", "other clone\n", "other clone's work")
        git(other, "push", "-q", "origin", "feat/1-x")
        theirs = git(self.origin, "rev-parse", "feat/1-x")
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        r = merge(self.work)
        self.assertEqual(r.returncode, 3)
        self.assertIn("hasn't seen", r.stderr)
        self.assertEqual(git(self.origin, "rev-parse", "feat/1-x"), theirs)

    def testUnshippedCommitsAreMergedButNotPushed(self):
        commit(self.work, "b.txt", "feature\n", "feature")
        pushed(self.work)
        shipped = git(self.origin, "rev-parse", "feat/1-x")
        commit(self.work, "e.txt", "not shipped\n", "not shipped yet")
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        r = merge(self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ship pushes them", r.stdout)
        self.assertEqual(git(self.origin, "rev-parse", "feat/1-x"), shipped)

    def testStatusWorksMidMergeAndOutsideOne(self):
        r = merge(self.work, "status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no merge under way", r.stdout)
        conflicted(self.origin, self.work)
        merge(self.work)
        r = merge(self.work, "status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("rebase-main: merging", r.stdout)

    def testBaseFlagNamesAnotherBranch(self):
        git(self.origin, "checkout", "-q", "-b", "develop")
        commit(self.origin, "c.txt", "develop moved\n", "develop moved")
        r = merge(self.work, "--base", "develop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), git(self.work, "rev-parse", "origin/develop"))

    def testContinueFinishesTheMergeStartWasGivenNotTheDefaultBase(self):
        git(self.origin, "checkout", "-q", "-b", "develop")
        commit(self.origin, "a.txt", "one\nTWO develop\nthree\n", "develop edits two")
        git(self.origin, "checkout", "-q", "main")
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        commit(self.work, "a.txt", "one\nTWO feature\nthree\n", "feature edits two")
        git(self.work, "fetch", "-q", "origin")  # origin/main is now ahead of the branch, off the merge
        self.assertEqual(merge(self.work, "--base", "develop").returncode, 1)
        (self.work / "a.txt").write_text("one\nTWO both\nthree\n")
        git(self.work, "add", "a.txt")
        r = merge(self.work, "continue")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD^2"), git(self.work, "rev-parse", "origin/develop"))
        self.assertNotIn("didn't start", r.stderr)

    def testABaseFlagOnContinueStatusOrAbortIsRefused(self):
        conflicted(self.origin, self.work)
        self.assertEqual(merge(self.work).returncode, 1)
        for command in ("continue", "status", "abort"):
            r = merge(self.work, command, "--base", "develop")
            self.assertEqual(r.returncode, 2, command)
            self.assertIn("--base applies to start only", r.stderr)
        self.assertEqual(merge(self.work, "status").returncode, 1)  # the merge is still under way

    def testAbortThatGitRefusesIsReportedAndTheMergeStays(self):
        commit(self.origin, "c.txt", "main added\n", "main adds c")
        commit(self.origin, "a.txt", "one\nTWO main\nthree\n", "main edits two")
        commit(self.work, "a.txt", "one\nTWO feature\nthree\n", "feature edits two")
        self.assertEqual(merge(self.work).returncode, 1)
        (self.work / "c.txt").write_text("edited after the merge stopped\n")  # git won't reset over it
        r = merge(self.work, "abort")
        self.assertEqual(r.returncode, 2)
        self.assertIn("couldn't abort", r.stderr)
        self.assertNotIn("aborted;", r.stdout)
        self.assertEqual(merge(self.work, "status").returncode, 1)

    def testACommitMadeAfterAFailedPushIsNotPushedByTheRetry(self):
        commit(self.work, "b.txt", "feature\n", "feature")
        pushed(self.work)
        shipped = git(self.origin, "rev-parse", "feat/1-x")
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        hook = self.origin / ".git" / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.assertEqual(merge(self.work).returncode, 3)  # merged; the push is refused
        hook.unlink()
        commit(self.work, "e.txt", "made after the merge\n", "new work")
        r = merge(self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("since the merge", r.stdout)
        self.assertEqual(git(self.origin, "rev-parse", "feat/1-x"), shipped)

    def testARetryWithNothingNewPushesTheMerge(self):
        commit(self.work, "b.txt", "feature\n", "feature")
        pushed(self.work)
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        hook = self.origin / ".git" / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.assertEqual(merge(self.work).returncode, 3)
        hook.unlink()
        r = merge(self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pushed feat/1-x", r.stdout)
        self.assertEqual(git(self.origin, "rev-parse", "feat/1-x"), git(self.work, "rev-parse", "HEAD"))

    def assertRefusedUnchanged(self, *args):
        commit(self.origin, "c.txt", "main moved\n", "main moved")
        head = git(self.work, "rev-parse", "HEAD")
        r = merge(self.work, *args)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage: rebase-main", r.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.work, "for-each-ref", "refs/rebase-main"), "")

    def testAMisspelledFlagAfterTheCommandIsRefused(self):
        self.assertRefusedUnchanged("start", "--bsae", "main")

    def testALeftoverArgumentAfterBaseIsRefused(self):
        self.assertRefusedUnchanged("--base", "main", "extra")

    def testABareBaseFlagIsRefused(self):
        self.assertRefusedUnchanged("start", "--base")


if __name__ == "__main__":
    unittest.main()
