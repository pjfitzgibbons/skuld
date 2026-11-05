# Ported from reference/vscode/test/ship.test.js. prLink/linkPullRequest and their tests are
# dropped: decision #4 removes Redmine's "Pull requests" custom field entirely, and Jira has no
# field for Ship to write a PR link back to, so there is nothing to port for linkPullRequest.
# reviewFile() now returns { root, key } (a Jira key string) rather than { root, id } (a bare
# number) -- every fixture path below uses a CIAM-### key, not a Redmine id.
#
# Ported from lib/ship.test.js. Faithful, behavior-preserving port.
import os
import shutil
import subprocess
import tempfile
import unittest

from lib.ship import check, keepEdits, prLink, reviewFile, roundFiles, shipTab, unsent, unsentIn


def sandbox(branch="feat/CIAM-81-go-requests"):
    dir = tempfile.mkdtemp(prefix="jira-plan-ship-")

    def git(*args):
        return subprocess.run(["git", *args], cwd=dir, capture_output=True, text=True, check=True).stdout

    git("init", "--quiet", "-b", branch)
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    with open(os.path.join(dir, ".gitignore"), "w") as f:
        f.write(".tmp/\n")
    with open(os.path.join(dir, "a.txt"), "w") as f:
        f.write("a\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")

    def write(name, text):
        file = os.path.join(dir, name)
        os.makedirs(os.path.dirname(file), exist_ok=True)
        with open(file, "w") as f:
            f.write(text)
        return file

    return dir, git, write


class ShipTests(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def sandbox(self, branch="feat/CIAM-81-go-requests"):
        dir, git, write = sandbox(branch)
        self._dirs.append(dir)
        return dir, git, write

    def test_reviewFile_recognizes_every_round_file_under_tmp_key(self):
        self.assertEqual(reviewFile("/repo/.tmp/CIAM-81/pr-body.md"), {"root": "/repo", "key": "CIAM-81"})
        self.assertEqual(reviewFile("/repo/.tmp/CIAM-81/issue.md"), {"root": "/repo", "key": "CIAM-81"})
        self.assertEqual(reviewFile("/repo/.tmp/CIAM-81/commit.txt"), {"root": "/repo", "key": "CIAM-81"})
        self.assertEqual(reviewFile("/repo/.tmp/CIAM-81/replies/42.md"), {"root": "/repo", "key": "CIAM-81"})
        self.assertIsNone(reviewFile("/repo/.tmp/CIAM-81/notes.md"))
        self.assertIsNone(reviewFile("/repo/src/a.js"))

    def test_roundFiles_orders_commit_unsent_then_pr_body_last(self):
        dir, git, write = self.sandbox()
        sub = os.path.join(dir, ".tmp", "CIAM-81")
        write(".tmp/CIAM-81/commit.txt", "msg\n")
        write(".tmp/CIAM-81/issue.md", "- [x] a\n")
        write(".tmp/CIAM-81/replies/9.md", "reply\n")
        write(".tmp/CIAM-81/pr-body.md", "body\n")
        self.assertEqual(
            roundFiles(sub),
            [
                os.path.join(sub, "commit.txt"),
                os.path.join(sub, "issue.md"),
                os.path.join(sub, "replies", "9.md"),
                os.path.join(sub, "pr-body.md"),
            ],
        )

    def test_roundFiles_empty_until_pr_body_exists(self):
        dir, git, write = self.sandbox()
        sub = os.path.join(dir, ".tmp", "CIAM-81")
        write(".tmp/CIAM-81/commit.txt", "msg\n")
        self.assertEqual(roundFiles(sub), [])

    def test_unsent_lists_issue_kept_edits_and_replies_in_comment_id_order(self):
        dir, git, write = self.sandbox()
        sub = os.path.join(dir, ".tmp", "CIAM-81")
        write(".tmp/CIAM-81/issue.md", "x")
        write(".tmp/CIAM-81/replies/10.md", "x")
        write(".tmp/CIAM-81/replies/2.md", "x")
        write(".tmp/CIAM-81/replies/.DS_Store", "x")
        rels = [os.path.relpath(f, sub) for f in unsent(sub)]
        self.assertEqual(rels, ["issue.md", os.path.join("replies", "2.md"), os.path.join("replies", "10.md")])

    def test_keepEdits_never_overwrites_numbering_unsent_unsent2(self):
        dir, git, write = self.sandbox()
        file = os.path.join(dir, ".tmp", "CIAM-81", "issue.md")
        os.makedirs(os.path.dirname(file), exist_ok=True)
        first = keepEdits(file, "a")
        second = keepEdits(file, "b")
        self.assertEqual(first, f"{file}.unsent")
        self.assertEqual(second, f"{file}.unsent2")
        with open(first) as f:
            self.assertEqual(f.read(), "a")
        with open(second) as f:
            self.assertEqual(f.read(), "b")

    def test_unsentIn_finds_unsent_work_across_every_issues_tmp_key(self):
        dir, git, write = self.sandbox()
        write(".tmp/CIAM-81/issue.md", "x")
        write(".tmp/CIAM-99/replies/3.md", "x")
        self.assertEqual(
            sorted(unsentIn(dir)),
            sorted([os.path.join(".tmp", "CIAM-81", "issue.md"), os.path.join(".tmp", "CIAM-99", "replies", "3.md")]),
        )

    def test_check_passes_clean_tree_with_pr_body(self):
        dir, git, write = self.sandbox()
        write(".tmp/CIAM-81/pr-body.md", "body\n")
        result = check(dir, "CIAM-81")
        self.assertEqual(result["branch"], "feat/CIAM-81-go-requests")
        self.assertFalse(result["dirty"])
        self.assertEqual(result["problems"], [])

    def test_check_requires_commit_txt_when_tree_is_dirty(self):
        dir, git, write = self.sandbox()
        write(".tmp/CIAM-81/pr-body.md", "body\n")
        write("b.txt", "new\n")
        result = check(dir, "CIAM-81")
        self.assertTrue(result["dirty"])
        self.assertTrue(any(p == "the tree has changes and .tmp/CIAM-81/commit.txt is missing" for p in result["problems"]))

    def test_check_names_missing_pr_body_and_branch_that_isnt_issues(self):
        dir, git, write = self.sandbox("main")
        result = check(dir, "CIAM-81")
        self.assertTrue(any("pr-body.md is missing" in p for p in result["problems"]))
        self.assertTrue(any("isn't CIAM-81's" in p for p in result["problems"]))

    def test_check_flags_empty_reply_and_kept_edits_that_arent_a_reply_pr_can_send(self):
        dir, git, write = self.sandbox()
        write(".tmp/CIAM-81/pr-body.md", "body\n")
        write(".tmp/CIAM-81/replies/9.md", "")
        write(".tmp/CIAM-81/issue.md.unsent", "edited\n")
        result = check(dir, "CIAM-81")
        self.assertTrue(any("is empty" in p for p in result["problems"]))
        self.assertTrue(any("isn't a reply pr can send" in p for p in result["problems"]))

    def test_shipTab_recognizes_round_files_own_tab_and_diff_touching_root(self):
        self.assertTrue(shipTab({"uri": {"fsPath": "/repo/.tmp/CIAM-81/pr-body.md"}}, "/repo", "CIAM-81"))
        self.assertFalse(shipTab({"uri": {"fsPath": "/repo/.tmp/CIAM-81/other.txt"}}, "/repo", "CIAM-81"))
        self.assertTrue(shipTab({"modified": {"fsPath": "/repo/src/a.js"}}, "/repo", "CIAM-81"))
        self.assertFalse(shipTab(None, "/repo", "CIAM-81"))

    def test_prLink_builds_repo_number_markdown_link(self):
        self.assertEqual(
            prLink({"url": "https://github.com/acme/widgets/pull/111", "number": 111}),
            "[widgets#111](https://github.com/acme/widgets/pull/111)",
        )


if __name__ == "__main__":
    unittest.main()
