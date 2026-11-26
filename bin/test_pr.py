#!/usr/bin/env python3
"""Tests for `pr` and `ghbody` (bin/), against a fake gh and plan. No network, no GitHub writes.

Run: python3 -m unittest test_pr -v   (from bin/)
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).parent
HELPERS = BIN / "pr-helpers"


# ---------------------------------------------------------------------------
# pr (the merged-PR tail only; everything else is gh/git plumbing unchanged from the source)
# ---------------------------------------------------------------------------

FAKE_GH = """#!/usr/bin/env python3
import os
import sys
argv = sys.argv[1:]
if argv[:2] == ["repo", "view"]:
    print("main")
elif argv[:2] == ["pr", "view"]:
    field = argv[argv.index("--json") + 1]
    if field == "state":
        print("MERGED")
    elif field == "headRefOid":
        print(open(os.environ["HEAD_OID_FILE"]).read().strip())
    elif field == "url":
        print("https://github.com/o/r/pull/9")
elif argv[:2] == ["pr", "edit"]:
    pass
sys.exit(0)
"""

# M5: a fake gh for exercising pr's commit step, which the MERGED-branch FAKE_GH above never
# reaches (it always reports state MERGED, so pr exits before ever looking at commit.txt).
# `gh pr view` fails here -- no PR exists yet for the branch -- so pr's `state` comes back
# empty and falls through to the uncommitted-changes branch instead.
FAKE_GH_NO_PR = """#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if argv[:2] == ["repo", "view"]:
    print("main")
elif argv[:2] == ["pr", "view"]:
    sys.exit(1)
sys.exit(0)
"""

# Same as FAKE_GH_NO_PR, but also used past the commit step; pr still tries `git push` next
# (there's no fake for that -- it's real git, and the test repo has no remote), so that test reads
# commit.txt's fate from git log/the filesystem rather than from pr's own exit code.
FAKE_GH_NO_PR_NO_PUSH = FAKE_GH_NO_PR


class PrDone(unittest.TestCase):
    def test_merged_pr_tells_to_run_plan_done(self):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            head = run_git("rev-parse", "HEAD").stdout.strip()
            (outer / "HEAD_OID").write_text(head)

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["HEAD_OID_FILE"] = str(outer / "HEAD_OID")

            result = subprocess.run([str(BIN / "pr")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("run plan done CIAM-9 to clean up", result.stdout)

            # The done flag comes before the key.
            result = subprocess.run([str(BIN / "pr"), "done", "CIAM-9"], cwd=root,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("run plan done CIAM-9 to clean up", result.stdout)

    def test_merged_pr_tells_to_run_plan_done_unprefixed_branch(self):
        # L2: pr's key-from-branch sed required a <prefix>/ segment before KEY-slug; a bare
        # CIAM-9-x branch (no feat/, fix/, chore/, ...) must resolve its key the same way
        # lib/start.py's issueBranch() and lib/issuekeys.py's ISSUE_KEY_PATTERN already treat an unprefixed branch.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "CIAM-9-x")
            head = run_git("rev-parse", "HEAD").stdout.strip()
            (outer / "HEAD_OID").write_text(head)

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["HEAD_OID_FILE"] = str(outer / "HEAD_OID")

            result = subprocess.run([str(BIN / "pr")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("run plan done CIAM-9 to clean up", result.stdout)

    def test_refuses_commit_message_with_smart_commit_token(self):
        # M5: a #<word> next to a Jira key in .tmp/KEY/commit.txt would run as a Jira smart commit
        # once pushed -- Jira's GitHub integration reads it off the commit, not off anything pr
        # itself calls, so pr must refuse before committing rather than relying on anything
        # downstream to catch it.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("update-ref", "refs/remotes/origin/main", "HEAD")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            (root / "f.txt").write_text("changed\n")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "commit.txt").write_text("CIAM-9 #close fixes the bug\n")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"

            result = subprocess.run([str(BIN / "pr")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            # Refused before committing: no new commit, and the file it flagged is still there.
            log = run_git("log", "--oneline").stdout
            self.assertEqual(len(log.strip().splitlines()), 1)
            self.assertTrue((tmp_dir / "commit.txt").exists())

    def test_commits_a_message_with_a_key_but_no_smart_commit_token(self):
        # The flip side of the refusal above: a commit message can still name the ticket (which
        # almost every commit.txt does) as long as it has no "#<word>" beside it.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("update-ref", "refs/remotes/origin/main", "HEAD")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            (root / "f.txt").write_text("changed\n")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "commit.txt").write_text("CIAM-9 fixes the bug, no hashtags here\n")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR_NO_PUSH)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["GIT_TERMINAL_PROMPT"] = "0"

            result = subprocess.run([str(BIN / "pr")], cwd=root, env=env,
                                     capture_output=True, text=True)
            # No remote is configured, so the push after the commit fails -- that's fine, the
            # point of this test is only that the commit itself was allowed to happen.
            log = run_git("log", "--oneline").stdout
            self.assertEqual(len(log.strip().splitlines()), 2, result.stderr)
            self.assertFalse((tmp_dir / "commit.txt").exists())

    def test_refuses_smart_commit_already_on_branch_not_just_commit_txt(self):
        # N2: refuse_smart_commits_file only ever saw commit.txt, the ONE new message pr was
        # about to write. A commit already on the branch -- made with `git commit -am "..."`,
        # never passing through commit.txt at all -- would reach origin on push unseen. origin/
        # <base> must actually exist locally for the range scan to run, so push a bare remote's
        # main once before branching.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            origin = outer / "origin.git"
            root = outer / "repo"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            run_git("remote", "add", "origin", str(origin))
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("push", "-q", "-u", "origin", "main")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            # Bypasses refuse_smart_commits_file entirely: committed directly, never through
            # .tmp/CIAM-9/commit.txt.
            (root / "f.txt").write_text("bad\n")
            run_git("commit", "-q", "-am", "CIAM-9 #close already on the branch")
            (root / "f.txt").write_text("good\n")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "commit.txt").write_text("CIAM-9 fixes the bug, no hashtags here\n")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"

            result = subprocess.run([str(BIN / "pr")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("a commit already on", result.stderr)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            # The new commit.txt-based commit was still allowed (it's clean); only the push was
            # stopped, so nothing bad reached origin.
            log = run_git("log", "--oneline").stdout
            self.assertEqual(len(log.strip().splitlines()), 3, result.stderr)
            origin_log = subprocess.run(["git", "log", "--oneline", "main"], cwd=origin,
                                         capture_output=True, text=True).stdout
            self.assertEqual(len(origin_log.strip().splitlines()), 1)

    def test_refuses_smart_commit_when_origin_base_ref_is_missing_locally(self):
        # N7: reviewer's exact repro. refuse_smart_commits_range used to run only when
        # `origin/$base` resolved locally, reasoning the push itself would fail or need a fetch
        # first otherwise -- false: deleting the remote-tracking ref (git update-ref -d) doesn't
        # stop `git push` from working, so a smart commit slipped straight through to origin.
        # Fixed to scan `HEAD --not --remotes=origin`, which needs no particular origin ref to
        # exist, and to run unconditionally.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            origin = outer / "origin.git"
            root = outer / "repo"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            run_git("remote", "add", "origin", str(origin))
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("push", "-q", "-u", "origin", "main")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            # The reviewer's exact repro: delete the local origin/main remote-tracking ref, then
            # commit a smart commit token directly (bypassing commit.txt), and run pr.
            run_git("update-ref", "-d", "refs/remotes/origin/main")
            (root / "f.txt").write_text("bad\n")
            run_git("commit", "-q", "-am", "CIAM-9 #close sneaky")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"

            # With no origin/main the provenance check can't name the change, so it is skipped
            # here; the smart-commit scan must work without that ref.
            result = subprocess.run([str(BIN / "pr"), "--no-provenance"], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            # Nothing reached origin: the bare remote still has only the one "init" commit.
            origin_log = subprocess.run(["git", "log", "--oneline", "--all"], cwd=origin,
                                         capture_output=True, text=True).stdout
            self.assertEqual(len(origin_log.strip().splitlines()), 1)

    def test_refuses_a_path_shaped_key_argument(self):
        # N10: n (the KEY argument, or the branch-derived fallback) is used to build .tmp/$n
        # paths before anything downstream (ghbody, checklist.py) would ever validate it -- an
        # unanchored/path-shaped value like "../x" must be refused immediately, not threaded into
        # a path.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"

            result = subprocess.run([str(BIN / "pr"), "../x"], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("isn't a Jira key", result.stderr)
            self.assertFalse((outer / "x").exists())
            self.assertFalse((root.parent / "x").exists())

    def test_refuses_on_default_branch(self):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH)
            fake_gh.chmod(0o755)
            (outer / "HEAD_OID").write_text("unused")

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["HEAD_OID_FILE"] = str(outer / "HEAD_OID")

            result = subprocess.run([str(BIN / "pr")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("switch to the issue's branch", result.stderr)


# ---------------------------------------------------------------------------
# ghbody
# ---------------------------------------------------------------------------


class GhBody(unittest.TestCase):
    def test_pr_edit_resolves_key_from_unprefixed_branch(self):
        # L2: ghbody's key-from-branch sed required a <prefix>/ segment before KEY-slug, same as
        # pr's; a bare CIAM-9-x branch (no feat/, fix/, chore/, ...) must resolve too.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "CIAM-9-x")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "pr-body.md").write_text("the body\n")

            env = dict(os.environ)
            env["GHBODY_DRY"] = "1"
            result = subprocess.run(
                [str(BIN / "ghbody"), "pr-edit"], cwd=root, env=env,
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("gh pr edit --body-file", result.stdout)
            self.assertIn(str(tmp_dir / "pr-body.md"), result.stdout)

    def test_pr_edit_refuses_a_body_with_a_smart_commit_token(self):
        # M5: a #<word> beside a Jira key in the PR body would run as a Jira smart commit once
        # Jira's GitHub integration reads it off the PR, so ghbody must refuse before gh pr
        # edit ever sends it.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "CIAM-9-x")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "pr-body.md").write_text("CIAM-9 #comment all set\n")

            env = dict(os.environ)
            env["GHBODY_DRY"] = "1"
            result = subprocess.run(
                [str(BIN / "ghbody"), "pr-edit"], cwd=root, env=env,
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            self.assertNotIn("gh pr edit", result.stdout)

    def test_pr_create_refuses_a_title_with_a_smart_commit_token(self):
        # M5: pr-create's title comes from the branch's first commit subject, a different source
        # than the body file -- the title needs the same check, since Jira's integration reads
        # smart commits off PR titles too.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            bare = outer / "bare.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            run_git("remote", "add", "origin", str(bare))
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            # Local ref standing in for origin/main, without a real push: pr-create's title comes
            # from commits ahead of this, same as git log --reverse origin/main..HEAD would see.
            main_oid = run_git("rev-parse", "HEAD").stdout.strip()
            run_git("update-ref", "refs/remotes/origin/main", main_oid)
            run_git("checkout", "-q", "-b", "CIAM-9-x")
            (root / "f.txt").write_text("changed\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "CIAM-9 #done fixes the bug")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "pr-body.md").write_text("an ordinary body\n")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "argv = sys.argv[1:]\n"
                "if argv[:2] == ['repo', 'view']:\n"
                "    print('main')\n"
                "sys.exit(0)\n"
            )
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["GHBODY_DRY"] = "1"
            env["PATH"] = f"{bindir}:{env['PATH']}"
            result = subprocess.run(
                [str(BIN / "ghbody"), "pr-create"], cwd=root, env=env,
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            self.assertNotIn("gh pr create", result.stdout)


    # The machine config's reviewer is read from $HOME/.config/skuld/config.json; config is its text,
    # or None for no config file.
    def run_ghbody(self, kind, config):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = new_pr_branch(outer)
            env = fake_gh_env(outer, GHBODY_DRY="1", HOME=str(outer / "home"))
            if config is not None:
                (outer / "home" / ".config" / "skuld").mkdir(parents=True)
                (outer / "home" / ".config" / "skuld" / "config.json").write_text(config)
            return subprocess.run([str(BIN / "ghbody"), kind], cwd=root, env=env,
                                  capture_output=True, text=True)

    def test_pr_create_asks_copilot_when_reviewer_is_copilot(self):
        result = self.run_ghbody("pr-create", '{"reviewer": "copilot"}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gh pr create", result.stdout)
        self.assertIn("gh pr edit --add-reviewer @copilot", result.stdout)
        self.assertIn("reviewer is copilot", result.stdout)

    def test_pr_create_asks_copilot_when_the_config_has_no_reviewer(self):
        for config in (None, "{}"):
            result = self.run_ghbody("pr-create", config)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("gh pr edit --add-reviewer @copilot", result.stdout)

    def test_pr_create_asks_no_one_when_reviewer_is_claude(self):
        result = self.run_ghbody("pr-create", '{"reviewer": "claude"}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gh pr create", result.stdout)
        self.assertNotIn("--add-reviewer", result.stdout)

    def test_pr_edit_asks_no_one(self):
        result = self.run_ghbody("pr-edit", '{"reviewer": "copilot"}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gh pr edit --body-file", result.stdout)
        self.assertNotIn("--add-reviewer", result.stdout)

    def test_an_unknown_reviewer_refuses_both_commands_naming_the_field(self):
        for kind in ("pr-create", "pr-edit"):
            result = self.run_ghbody(kind, '{"reviewer": "bogus"}')
            self.assertEqual(result.returncode, 2)
            self.assertIn('"reviewer" is \'bogus\'', result.stderr)
            self.assertIn("copilot, claude", result.stderr)
            self.assertNotIn("gh pr", result.stdout)

    def test_a_failed_copilot_request_does_not_fail_pr_create(self):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = new_pr_branch(outer)
            env = fake_gh_env(outer, HOME=str(outer / "home"))
            # answers `repo view` and `pr create`, and fails every other gh call, the Copilot request among them
            (outer / "bin" / "gh").write_text(
                "#!/bin/sh\n[ \"$1 $2\" = 'repo view' ] && { echo main; exit 0; }\n"
                "[ \"$1 $2\" = 'pr create' ] && exit 0\nexit 1\n")
            result = subprocess.run([str(BIN / "ghbody"), "pr-create"], cwd=root, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ask by hand: gh pr edit --add-reviewer @copilot", result.stderr)


# pr's review step on an open PR: a fake gh that answers the PR's state, head and URL, a bare
# origin to push to, and a fake plan that records `plan review KEY` and exits as FAKE_PLAN_EXIT says.
class PrReview(unittest.TestCase):
    FAKE_GH = (
        "#!/usr/bin/env python3\nimport os, sys\n"
        "a = sys.argv[1:]\n"
        "open(os.environ['GH_CALLS'], 'a').write(' '.join(a) + '\\n')\n"
        "if a[:2] == ['repo', 'view']: print('main')\n"
        "elif a[:2] == ['pr', 'view']:\n"
        "    if a[4] == 'state' and os.path.exists(os.environ['NO_PR']): sys.exit(1)\n"
        "    print({'state': 'OPEN', 'headRefOid': open(os.environ['HEAD_OID_FILE']).read().strip(),\n"
        "           'url': 'https://example.test/pull/7'}[a[4]])\n"
        "elif a[:2] == ['pr', 'edit']: sys.exit(1 if os.path.exists(os.environ['ASK_FAILS']) else 0)\n"
        "elif a[:1] == ['pb']: sys.exit(1 if os.path.exists(os.environ['PB_FAILS']) else 0)\n"
    )
    FAKE_PLAN = (
        "#!/bin/sh\necho \"$@\" >> \"$PLAN_CALLS\"\n"
        "[ \"${FAKE_PLAN_EXIT:-0}\" = 0 ] || { echo 'round 1 was requested for an older HEAD; run plan review CIAM-9 --restart' >&2; exit 1; }\n"
        "echo \"review requested of CIAM-9's Claude\"\n"
    )

    FAKE_PROOF = "#!/bin/sh\necho \"$@\" >> \"$PROOF_CALLS\"\nexit \"${FAKE_PROOF_EXIT:-0}\"\n"

    # where: "own" puts the fake plan at <repo>/bin/plan beside a copy of pr, "path" on PATH, "none" nowhere;
    # "both" puts a different fake on PATH too.
    # reviewed: the review record already names HEAD. pb_fails_first: gh pb fails on the first run, and
    # a second run follows; the result is the second's.
    # reviewer: the machine config's value, in $HOME/.config/skuld/config.json; None writes no config,
    # and config, when given, is the file's text. Claude is the default here, since the tests of its
    # review start from it. moved: the PR's head on GitHub differs from HEAD, as when the push added
    # commits. no_pr: the branch has no PR yet. ask_fails: `gh pr edit`, the Copilot request, fails.
    # The gh calls are left in self.gh_calls.
    def run_pr(self, reviewed, plan_exit="0", where="own", with_replies=False, pb_fails_first=False,
               unfinished=False, reviewer="claude", moved=False, no_pr=False, ask_fails=False, config=None,
               proof_exit="0", arguments=(), dirty=False, message=True):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = new_pr_branch(outer)
            git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "init", "-q", "--bare", str(outer / "origin.git")], check=True)
            git("remote", "add", "origin", str(outer / "origin.git"))
            head = git("rev-parse", "HEAD").stdout.strip()
            if reviewed:
                folder = root / ".tmp" / "CIAM-9" / "rounds" / "1"
                folder.mkdir(parents=True)
                (folder / "review.json").write_text(json.dumps({"head": head, "base": "main", "round": 1, "dispositions": [], "findings": []}))
            (root / ".git" / "info" / "exclude").write_text(".tmp/\n")
            bindir = outer / "bin"
            bindir.mkdir()
            fakes = [(bindir, "gh", self.FAKE_GH)]
            plan_home = outer / "skuld"
            fakes.append((plan_home / "bin", "provenance-check", self.FAKE_PROOF))
            if dirty:
                (root / "f.txt").write_text("uncommitted\n")
                if message:
                    (root / ".tmp" / "CIAM-9" / "commit.txt").write_text("More of the fix (CIAM-9)\n")
            script_dir = plan_home / "bin"
            (script_dir / "pr-helpers").mkdir(parents=True)
            shutil.copy(BIN / "pr", script_dir / "pr")
            for name in ("pr_threads.py", "ghread.py", "checklist.py", "review.py", "reviewer.py"):
                shutil.copy(HELPERS / name, script_dir / "pr-helpers" / name)
            if where in ("own", "both"):
                fakes.append((plan_home / "bin", "plan", self.FAKE_PLAN))
            if where == "path":
                fakes.append((bindir, "plan", self.FAKE_PLAN))
            if where == "both":
                fakes.append((bindir, "plan", self.FAKE_PLAN.replace("echo \"$@\"", "echo \"PATH $@\"")))
            for directory, name, text in fakes:
                (directory / name).write_text(text)
                (directory / name).chmod(0o755)
            (outer / "HEAD_OID").write_text("0" * 40 if moved else head)
            if config is None and reviewer is not None:
                config = json.dumps({"reviewer": reviewer})
            if config is not None:
                (outer / "home" / ".config" / "skuld").mkdir(parents=True)
                (outer / "home" / ".config" / "skuld" / "config.json").write_text(config)
            for flag, wanted in (("no-pr", no_pr), ("ask-fails", ask_fails)):
                if wanted:
                    (outer / flag).write_text("")
            if unfinished:
                folder = root / ".tmp" / "CIAM-9" / "rounds" / "1"
                folder.mkdir(parents=True)
                (folder / "request.json").write_text(json.dumps({"round": 1, "head": head}))
            if with_replies:
                # a replies folder with a file that isn't a comment id fails pr_threads.py --post
                (root / ".tmp" / "CIAM-9" / "replies").mkdir()
                (root / ".tmp" / "CIAM-9" / "replies" / "nope.md").write_text("Fixed\n")
            calls = outer / "plan-calls"
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "HEAD_OID_FILE": str(outer / "HEAD_OID"),
                   "PLAN_CALLS": str(calls), "FAKE_PLAN_EXIT": plan_exit,
                   "PB_FAILS": str(outer / "pb-fails"), "HOME": str(outer / "home"),
                   "GH_CALLS": str(outer / "gh-calls"), "NO_PR": str(outer / "no-pr"),
                   "ASK_FAILS": str(outer / "ask-fails"),
                   "PROOF_CALLS": str(outer / "proof-calls"), "FAKE_PROOF_EXIT": proof_exit}
            if pb_fails_first:
                (outer / "pb-fails").write_text("")
                self.first = subprocess.run([str(script_dir / "pr")], cwd=root, env=env, capture_output=True, text=True)
                self.calls_after_first = calls.read_text() if calls.exists() else ""
                (outer / "pb-fails").unlink()
            result = subprocess.run([str(script_dir / "pr"), *arguments], cwd=root, env=env, capture_output=True, text=True)
            gh_calls = outer / "gh-calls"
            self.gh_calls = gh_calls.read_text() if gh_calls.exists() else ""
            proofs = outer / "proof-calls"
            self.proof_calls = proofs.read_text() if proofs.exists() else ""
            self.commits = len(git("log", "--oneline").stdout.splitlines())
            self.pushed = bool(git("ls-remote", "origin").stdout)
            return result, calls.read_text() if calls.exists() else ""

    def test_provenance_is_checked_against_the_default_branch_before_anything_is_committed(self):
        result, _calls = self.run_pr(reviewed=True, dirty=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.proof_calls, "--if-code --base origin/main CIAM-9\n")
        self.assertLess(result.stdout.index("pr: checking provenance"), result.stdout.index("More of the fix"))

    def test_a_missing_commit_message_is_refused_before_the_check_runs(self):
        result, _calls = self.run_pr(reviewed=True, dirty=True, message=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn(".tmp/CIAM-9/commit.txt is missing", result.stderr)
        self.assertEqual(self.proof_calls, "")

    def test_a_refused_check_stops_pr_with_nothing_committed_or_pushed(self):
        for exit_code in ("1", "2"):
            with self.subTest(exit_code=exit_code):
                result, calls = self.run_pr(reviewed=False, dirty=True, proof_exit=exit_code)
                self.assertEqual(result.returncode, 2)
                self.assertIn("pr --no-provenance CIAM-9", result.stderr)
                self.assertEqual((self.commits, self.pushed, calls), (2, False, ""))

    def test_no_provenance_skips_the_check_says_so_and_ships(self):
        for arguments in (("--no-provenance",), ("--no-provenance", "CIAM-9"), ("CIAM-9", "--no-provenance")):
            with self.subTest(arguments=arguments):
                result, _calls = self.run_pr(reviewed=True, dirty=True, proof_exit="1", arguments=arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("pr: --no-provenance: provenance is not checked", result.stdout)
                self.assertEqual((self.proof_calls, self.commits, self.pushed), ("", 3, True))

    # --no-provenance given x what pr has that the PR lacks x the check's exit: whether pr goes on,
    # and whether it ran the check
    PROVENANCE_CASES = [
        (False, "nothing", "0", True, False), (False, "nothing", "1", True, False),
        (False, "uncommitted", "0", True, True), (False, "uncommitted", "1", False, True),
        (False, "unpushed", "0", True, True), (False, "unpushed", "1", False, True),
        (False, "no PR", "0", True, True), (False, "no PR", "1", False, True),
        (True, "nothing", "0", True, False), (True, "nothing", "1", True, False),
        (True, "uncommitted", "0", True, False), (True, "uncommitted", "1", True, False),
        (True, "unpushed", "0", True, False), (True, "unpushed", "1", True, False),
        (True, "no PR", "0", True, False), (True, "no PR", "1", True, False),
    ]
    NEW_WORK = {"nothing": {}, "uncommitted": {"dirty": True}, "unpushed": {"moved": True}, "no PR": {"no_pr": True}}

    def test_pr_goes_on_only_with_new_work_proven_unless_told_to_skip_the_check(self):
        for skip, work, exit_code, goes_on, checks in self.PROVENANCE_CASES:
            with self.subTest(skip=skip, work=work, exit_code=exit_code):
                arguments = ("--no-provenance",) if skip else ()
                result, _calls = self.run_pr(reviewed=True, proof_exit=exit_code, arguments=arguments,
                                             **self.NEW_WORK[work])
                self.assertEqual((result.returncode == 0, bool(self.proof_calls)), (goes_on, checks), result.stderr)

    def test_a_run_with_nothing_new_says_the_check_was_not_needed(self):
        result, _calls = self.run_pr(reviewed=True, proof_exit="1")
        self.assertIn("pr: nothing new to commit or push, so provenance isn't checked", result.stdout)

    def test_a_push_that_adds_commits_starts_the_review_after_printing_the_url(self):
        result, calls = self.run_pr(reviewed=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "review CIAM-9 --if-due\n")
        self.assertLess(result.stdout.index("https://example.test/pull/7"), result.stdout.index("review requested"))

    def test_a_push_that_adds_nothing_starts_no_review(self):
        result, calls = self.run_pr(reviewed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"pr: no review: HEAD ", result.stdout)
        self.assertIn("was reviewed in round 1", result.stdout)
        self.assertEqual(calls, "")

    def test_a_pr_that_failed_after_its_push_reviews_those_commits_on_its_next_run(self):
        result, calls = self.run_pr(reviewed=False, pb_fails_first=True)
        self.assertNotEqual(self.first.returncode, 0)
        self.assertEqual(self.calls_after_first, "")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "review CIAM-9 --if-due\n")

    def test_a_round_requested_for_this_head_and_unfinished_is_named_as_the_reason(self):
        result, calls = self.run_pr(reviewed=False, unfinished=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pr: no review: round 1 was requested for HEAD ", result.stdout)
        self.assertIn("hasn't finished; it runs in the Review CIAM-9 terminal of the plan window, or run plan review CIAM-9", result.stdout)
        self.assertEqual(calls, "")

    def test_a_review_that_cannot_start_says_why_and_does_not_fail_pr(self):
        result, calls = self.run_pr(reviewed=False, plan_exit="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no review started: round 1 was requested for an older HEAD; run plan review CIAM-9 --restart", result.stderr)
        self.assertIn("plan review CIAM-9", result.stderr)

    def test_the_review_runs_the_plan_of_the_scripts_own_repo_not_the_one_on_path(self):
        result, calls = self.run_pr(reviewed=False, where="both")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "review CIAM-9 --if-due\n")

    def test_plan_on_path_is_used_when_the_repo_has_no_bin_plan(self):
        result, calls = self.run_pr(reviewed=False, where="path")
        self.assertEqual(calls, "review CIAM-9 --if-due\n")

    def test_a_failed_send_does_not_skip_the_review(self):
        result, calls = self.run_pr(reviewed=False, with_replies=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, "review CIAM-9 --if-due\n")

    def test_pr_without_plan_on_the_path_says_how_to_start_the_review(self):
        result, calls = self.run_pr(reviewed=False, where="none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("plan review CIAM-9", result.stderr)


    ASK = "pr edit CIAM-9-x --add-reviewer @copilot\n"

    def test_copilot_is_asked_for_a_push_that_moved_the_branch_and_claude_is_not_started(self):
        result, calls = self.run_pr(reviewed=False, reviewer="copilot", moved=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.ASK, self.gh_calls)
        self.assertIn("pr: reviewer is copilot; asking Copilot to review the new commits", result.stdout)
        self.assertEqual(calls, "")

    def test_a_second_pr_after_another_push_asks_copilot_again(self):
        first, _ = self.run_pr(reviewed=False, reviewer="copilot", moved=True)
        second, _ = self.run_pr(reviewed=True, reviewer="copilot", moved=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn(self.ASK, self.gh_calls)

    def test_copilot_is_not_asked_when_nothing_was_pushed(self):
        result, calls = self.run_pr(reviewed=False, reviewer="copilot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--add-reviewer", self.gh_calls)
        self.assertIn("no new commits, so Copilot isn't asked again", result.stdout)
        self.assertEqual(calls, "")

    def test_a_new_pr_leaves_the_copilot_request_to_gh_pc_and_says_so(self):
        result, calls = self.run_pr(reviewed=False, reviewer="copilot", no_pr=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pc CIAM-9\n", self.gh_calls)
        self.assertNotIn("--add-reviewer", self.gh_calls)
        self.assertIn("pr: reviewer is copilot; gh pc asks Copilot to review the new PR", result.stdout)
        self.assertEqual(calls, "")

    def test_claude_starts_its_review_and_never_asks_copilot(self):
        for moved in (False, True):
            result, calls = self.run_pr(reviewed=False, reviewer="claude", moved=moved)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls, "review CIAM-9 --if-due\n")
            self.assertIn("pr: reviewer is claude", result.stdout)
            self.assertNotIn("--add-reviewer", self.gh_calls)

    def test_a_missing_reviewer_behaves_as_copilot(self):
        for kwargs in ({"reviewer": None}, {"config": "{}"}):
            result, calls = self.run_pr(reviewed=False, moved=True, **kwargs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(self.ASK, self.gh_calls)
            self.assertEqual(calls, "")

    def test_a_failed_copilot_request_leaves_the_pr_updated_and_prints_the_retry(self):
        result, calls = self.run_pr(reviewed=False, reviewer="copilot", moved=True, ask_fails=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ask by hand: gh pr edit CIAM-9-x --add-reviewer @copilot", result.stderr)
        self.assertIn("pb CIAM-9\n", self.gh_calls)
        self.assertIn("https://example.test/pull/7", result.stdout)

    def test_an_unknown_reviewer_refuses_before_the_commit_naming_the_field(self):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = new_pr_branch(outer)
            (root / "f.txt").write_text("more\n")
            (root / ".tmp" / "CIAM-9" / "commit.txt").write_text("More (CIAM-9)\n")
            env = fake_gh_env(outer, HOME=str(outer / "home"))
            (outer / "home" / ".config" / "skuld").mkdir(parents=True)
            (outer / "home" / ".config" / "skuld" / "config.json").write_text('{"reviewer": "bogus"}')
            result = subprocess.run([str(BIN / "pr")], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('"reviewer" is \'bogus\'', result.stderr)
            subject = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=root, capture_output=True, text=True)
            self.assertEqual(subject.stdout.strip(), "Fix the bug (CIAM-9)")
            self.assertTrue((root / ".tmp" / "CIAM-9" / "commit.txt").exists())


# A repo on branch CIAM-9-x, one commit ahead of a local origin/main, with a drafted PR body.
class PrWithTheRealCheck(unittest.TestCase):
    """pr and provenance-check together, nothing faked but gh and the push going to a bare repo."""

    def run_pr(self, manifest_row):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = new_pr_branch(outer)
            git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout
            subprocess.run(["git", "init", "-q", "--bare", str(outer / "origin.git")], check=True)
            git("remote", "add", "origin", str(outer / "origin.git"))
            (root / ".git" / "info" / "exclude").write_text(".tmp/\n")
            git("update-ref", "refs/remotes/origin/main", "HEAD")  # the change is double.py and its test
            (root / "double.py").write_text("def double(x):\n    return x * 2\n")
            (root / "test_double.py").write_text(
                "import unittest\n\nimport double\n\n\nclass Double(unittest.TestCase):\n"
                "    def test_double(self):\n        self.assertEqual(double.double(2), 4)\n")
            (root / ".tmp" / "CIAM-9" / "commit.txt").write_text("Add double (CIAM-9)\n")
            if manifest_row:
                (root / ".tmp" / "CIAM-9" / "provenance.md").write_text(manifest_row + "\n")
            result = subprocess.run([str(BIN / "pr")], cwd=root, env=fake_gh_env(outer, HOME=str(outer)),
                                    capture_output=True, text=True)
            return result, len(git("log", "--oneline").splitlines()), git("ls-remote", "origin")

    def test_an_unproven_change_is_neither_committed_nor_pushed(self):
        result, commits, remote = self.run_pr(None)
        self.assertEqual(result.returncode, 2)
        self.assertIn("double.py::double changed, and no row names it", result.stderr)
        self.assertEqual((commits, remote), (2, ""))

    def test_a_proven_change_is_committed_and_pushed(self):
        result, commits, remote = self.run_pr(
            "| double.py::double | plain | test: test_double.py::Double.test_double |")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok row 1 (double.py::double, plain)", result.stdout)
        self.assertEqual(commits, 3)
        self.assertIn("refs/heads/CIAM-9-x", remote)


def new_pr_branch(outer):
    root = outer / "repo"
    root.mkdir()
    run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True)
    run_git("init", "-q", "-b", "main")
    run_git("config", "user.email", "t@t.com")
    run_git("config", "user.name", "t")
    (root / "f.txt").write_text("x\n")
    run_git("add", "-A")
    run_git("commit", "-q", "-m", "init")
    run_git("update-ref", "refs/remotes/origin/main", run_git("rev-parse", "HEAD").stdout.strip())
    run_git("checkout", "-q", "-b", "CIAM-9-x")
    (root / "f.txt").write_text("changed\n")
    run_git("commit", "-qam", "Fix the bug (CIAM-9)")
    tmp_dir = root / ".tmp" / "CIAM-9"
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "pr-body.md").write_text("an ordinary body\n")
    return root


# The environment for a gh that answers only `gh repo view` (the default branch, main).
def fake_gh_env(outer, **extra):
    bindir = outer / "bin"
    bindir.mkdir(exist_ok=True)
    fake_gh = bindir / "gh"
    fake_gh.write_text("#!/usr/bin/env python3\nimport sys\n"
                       "if sys.argv[1:3] == ['repo', 'view']:\n    print('main')\n")
    fake_gh.chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", **extra}


if __name__ == "__main__":
    unittest.main()
