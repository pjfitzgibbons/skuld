"""Tests for review.py: the record's rules, and request and finish with git and gh faked."""

import contextlib
import io
import json
import shlex
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import review

HEAD = "b" * 40
OLD = "a" * 40


def finding(key="f.py:3-5:crash", state="open", **more):
    return {"key": key, "summary": "x crashes when y", "why": "y is the common case",
            "fixShows": "x survives y", "disposition": state, **more}


def without_reasoning(entry):
    """A finding as a record written before why and fixShows existed holds it."""
    return {name: value for name, value in entry.items() if name not in ("why", "fixShows")}


def record(head=OLD, round_number=1, findings=(), dispositions=()):
    return {"head": head, "base": "main", "round": round_number, "dispositions": list(dispositions),
            "findings": list(findings)}


def entry(path, blob, verdict="OK"):
    return {"path": path, "blob": blob, "passes": ["callers"], "verdict": verdict}


class Rules(unittest.TestCase):
    def test_no_record_is_a_first_review_of_the_whole_pr(self):
        self.assertEqual(review.choose_prompt(None), "first-review.md")
        self.assertEqual(review.diff_range("main", None, HEAD), f"origin/main...{HEAD}")

    def test_a_record_is_a_round_over_what_changed_since_its_head(self):
        self.assertEqual(review.choose_prompt(record()), "review-round.md")
        self.assertEqual(review.diff_range("main", record(), HEAD), f"{OLD}..{HEAD}")

    def test_a_recorded_head_no_longer_in_history_reviews_the_whole_pr(self):
        self.assertEqual(review.diff_range("main", record(), HEAD, bounded=False), f"origin/main...{HEAD}")

    def test_an_ok_file_with_an_unchanged_blob_is_not_read_again(self):
        last = record(dispositions=[entry("a.py", "1"), entry("b.py", "2"), entry("c.py", "3", ["c.py:1:crash"])])
        now = {"a.py": "1", "b.py": "9", "c.py": "3", "d.py": "4"}
        self.assertEqual(review.files_to_read(last, now), ["b.py", "c.py", "d.py"])
        self.assertEqual(review.files_to_read(None, now), ["a.py", "b.py", "c.py", "d.py"])

    def test_open_and_new_findings_are_unsettled_and_fixed_is_not(self):
        last = record(findings=[finding(state="open"), finding("g.py:1:x", "New"), finding("h.py:1:x", "Fixed")])
        self.assertEqual([f["key"] for f in review.unsettled_findings(last)], ["f.py:3-5:crash", "g.py:1:x"])

    def test_a_review_opens_the_round_after_the_latest_and_at_least_round_1(self):
        self.assertEqual(review.review_round([], None), 1)
        self.assertEqual(review.review_round([0], None), 1)
        self.assertEqual(review.review_round([0, 1, 2], None), 3)
        self.assertEqual(review.review_round([0, 1, 2], {"round": 2, "head": OLD}), 2)

    def test_a_finding_gets_its_file_and_line_from_its_key(self):
        done = review.with_locations(record(findings=[finding("a/b.py:12-20:x"), finding("c.py:7:y")]), "r", "call")
        self.assertEqual([(f["file"], f["line"]) for f in done["findings"]], [("a/b.py", "12-20"), ("c.py", "7")])
        self.assertEqual((done["range"], done["call"]), ("r", "call"))

    def test_the_rebase_call_names_the_whole_range_and_the_files_not_settled(self):
        self.assertEqual(
            review.review_call("origin/main...HEAD", ["a.py", "my file.py"]),
            "/code-review medium --max-findings all origin/main...HEAD -- a.py 'my file.py'",
        )
        last = record(findings=[finding("gone.py:1:x")], dispositions=[entry("ok.py", "1"), entry("b.py", "2", ["b.py:1:x"])])
        self.assertEqual(review.rebased_files(last, {"ok.py": "1", "b.py": "9", "n.py": "3"}), ["b.py", "gone.py", "n.py"])
        self.assertEqual(review.rebased_files(record(dispositions=[entry("ok.py", "1")]), {"ok.py": "1"}), [])

    def test_orange_needs_the_current_head_unsettled_findings_and_no_unfinished_round(self):
        open_findings = record(head=HEAD, findings=[finding()])
        self.assertTrue(review.is_orange(open_findings, HEAD, False))
        self.assertFalse(review.is_orange(open_findings, "c" * 40, False))
        self.assertFalse(review.is_orange(open_findings, HEAD, True))
        self.assertFalse(review.is_orange(record(head=HEAD, findings=[finding(state="Fixed")]), HEAD, False))
        self.assertFalse(review.is_orange(None, HEAD, False))

    def test_could_be_orange_is_orange_without_the_head_test(self):
        open_findings = record(head=OLD, findings=[finding()])
        self.assertTrue(review.could_be_orange(open_findings, False))
        self.assertFalse(review.could_be_orange(open_findings, True))
        self.assertFalse(review.could_be_orange(record(head=OLD, findings=[finding(state="Fixed")]), False))
        self.assertFalse(review.could_be_orange(None, False))

    def test_round_state_gives_the_last_record_and_the_unfinished_round_from_one_listing(self):
        with tempfile.TemporaryDirectory() as root:
            rounds = Path(root)
            (rounds / "1").mkdir()
            (rounds / "1" / "review.json").write_text(json.dumps(record(head=HEAD)))
            (rounds / "2").mkdir()
            (rounds / "2" / "request.json").write_text(json.dumps({"head": HEAD}))
            with mock.patch.object(review.os, "listdir", wraps=os.listdir) as listed:
                last, unfinished = review.round_state(rounds)
            self.assertEqual((last["head"], unfinished["round"]), (HEAD, 2))
            self.assertEqual(listed.call_count, 1)

    def test_a_finding_key_splits_into_file_lines_and_classification(self):
        self.assertEqual(review.parse_key("a/b.py:12-20:silent-loss"), ("a/b.py", 12, 20, "silent-loss"))
        self.assertEqual(review.parse_key("a.py:7:crash"), ("a.py", 7, 7, "crash"))
        with self.assertRaises(review.ReviewError):
            review.parse_key("a.py:crash")

    def test_a_record_is_accepted_only_for_this_head_and_round_in_full_shape(self):
        good = record(head=HEAD, round_number=2, findings=[finding()], dispositions=[entry("f.py", "1", ["f.py:3-5:crash"])])
        self.assertEqual(review.record_problems(good, 2, HEAD), [])
        problems = review.record_problems({**good, "head": OLD, "round": 1}, 2, HEAD)
        self.assertEqual(len(problems), 2)
        bad = record(head=HEAD, round_number=2, findings=[finding(key="nope", state="maybe")], dispositions=[{"path": "f.py", "blob": "1"}])
        self.assertTrue(any("needs path, blob and passes" in p for p in review.record_problems(bad, 2, HEAD)))
        self.assertTrue(any("disposition is" in p for p in review.record_problems(bad, 2, HEAD)))

    def test_only_unsettled_findings_without_a_comment_are_posted(self):
        last = record(findings=[finding(), finding("g.py:1:x", "New", commentId=5), finding("h.py:1:x", "Fixed")])
        self.assertEqual([f["key"] for f in review.findings_to_post(last)], ["f.py:3-5:crash"])

    def test_the_call_always_names_the_range_and_max_findings_all(self):
        self.assertEqual(review.review_call("origin/main...HEAD"), "/code-review medium --max-findings all origin/main...HEAD")
        self.assertEqual(review.review_call(f"{OLD}..HEAD"), f"/code-review medium --max-findings all {OLD}..HEAD")

    def test_diff_lines_come_from_the_hunk_headers_and_skip_deleted_files(self):
        diff = DIFF + "@@ -30 +40,2 @@\n+x\ndiff --git a/g.py b/g.py\n--- a/g.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        self.assertEqual(review.diff_lines(diff), {"f.py": set(range(1, 13)) | {40, 41}})
        self.assertEqual(review.diff_lines(""), {})

    def test_a_finding_is_placed_by_where_its_lines_and_file_are_in_the_diff(self):
        shown = {"f.py": set(range(1, 13)), "bin.dat": set()}
        place = lambda key: review.placement(finding(key), shown)
        self.assertEqual(place("f.py:3-5:crash"), "inline")
        self.assertEqual(place("f.py:12:crash"), "inline")
        self.assertEqual(place("f.py:10-14:crash"), "file")
        self.assertEqual(place("f.py:99:crash"), "file")
        self.assertEqual(place("bin.dat:1:crash"), "file")
        self.assertEqual(place("other.py:1:crash"), "body")

    def test_a_file_comment_names_the_file_with_subject_type_file_and_keeps_the_marker(self):
        comment = review.file_comment(HEAD, finding("f.py:99:crash"))
        self.assertEqual((comment["path"], comment["subject_type"], comment["commit_id"]), ("f.py", "file", HEAD))
        self.assertNotIn("line", comment)
        self.assertEqual(review.comment_ids([{"id": 1, "body": comment["body"]}]), {"f.py:99:crash": 1})

    def test_a_review_is_due_for_a_head_the_record_doesnt_name_unless_one_was_requested_for_it(self):
        self.assertTrue(review.review_due(None, HEAD))
        self.assertTrue(review.review_due(record(head=OLD), HEAD))
        self.assertFalse(review.review_due(record(head=HEAD), HEAD))
        self.assertFalse(review.review_due(record(head=OLD), HEAD, requested_head=HEAD))
        self.assertTrue(review.review_due(record(head=OLD), HEAD, requested_head=OLD))

    def test_a_pure_rename_and_a_binary_change_are_in_the_pr_with_no_lines(self):
        diff = ("diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
                "diff --git a/img.png b/img.png\nindex 1..2 100644\nBinary files a/img.png and b/img.png differ\n")
        self.assertEqual(review.diff_lines(diff), {"new.py": set(), "img.png": set()})

    def test_content_that_looks_like_file_headers_inside_a_hunk_is_not_one(self):
        diff = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n-- x\n++ b/foo\n+++ b/foo\n"
        self.assertEqual(review.diff_lines(diff), {"f.py": {1, 2}})

    def test_pages_of_comments_flatten_into_one_list(self):
        self.assertEqual(review.flatten_pages([[1, 2], [3]]), [1, 2, 3])
        self.assertEqual(review.flatten_pages([]), [])

    def test_a_new_finding_that_had_only_a_review_id_is_posted_once_both_ids_are_dropped(self):
        last = record(findings=[finding("g.py:1:x", "New", reviewId=3), finding("h.py:1:x", "New")])
        self.assertEqual([f["key"] for f in review.findings_to_post(last)], ["h.py:1:x"])
        dropped = record(findings=[finding("g.py:1:x", "New")])
        self.assertEqual([f["key"] for f in review.findings_to_post(dropped)], ["g.py:1:x"])

    def test_findings_already_in_posted_json_are_not_posted_again(self):
        last = record(findings=[finding("a.py:1:x"), finding("b.py:1:x"), finding("c.py:1:x")])
        posted = {"comments": {"a.py:1:x": 5}, "bodies": {"b.py:1:x": 9}}
        self.assertEqual([f["key"] for f in review.findings_to_post(last, posted)], ["c.py:1:x"])

    def test_a_finding_line_names_key_disposition_summary_reasoning_and_fix(self):
        self.assertEqual(review.finding_line(finding(fix="use y")),
                         "- f.py:3-5:crash [open]: x crashes when y\n  Why it matters: y is the common case"
                         "\n  Recommended fix: use y\n  A correct fix shows: x survives y")
        self.assertEqual(review.finding_lines([]), "(none)")

    def test_a_finding_line_from_a_record_without_reasoning_is_its_summary(self):
        self.assertEqual(review.finding_line(without_reasoning(finding())), "- f.py:3-5:crash [open]: x crashes when y")

    def test_a_finding_to_be_posted_needs_why_and_fix_shows(self):
        problems = review.finding_problems(without_reasoning(finding(state="New")))
        self.assertEqual(problems, ["finding f.py:3-5:crash: why (why it matters) is missing",
                                    "finding f.py:3-5:crash: fixShows (what a correct fix must show) is missing"])

    def test_a_finding_already_posted_or_settled_may_lack_reasoning(self):
        for posted in (finding(commentId=7), finding(reviewId=8), finding(state="Fixed")):
            self.assertEqual(review.finding_problems(without_reasoning(posted)), [])

    def test_reasoning_that_is_there_must_be_text(self):
        problems = review.finding_problems(finding(commentId=7, why=3, fixShows="  "))
        self.assertEqual(problems, ["finding f.py:3-5:crash: why is not text", "finding f.py:3-5:crash: fixShows is not text"])

    def test_a_record_written_before_reasoning_still_passes(self):
        old = {**record(head=HEAD, round_number=2, findings=[without_reasoning(finding(commentId=7))]),
               "dispositions": []}
        self.assertEqual(review.record_problems(old, 2, HEAD), [])

    def test_the_round_prompt_carries_each_unsettled_finding_s_reasoning(self):
        last = record(findings=[finding(commentId=7), finding("g.py:1:loss", "Fixed", commentId=8)])
        lines = review.finding_lines(review.unsettled_findings(last))
        self.assertIn("Why it matters: y is the common case", lines)
        self.assertIn("A correct fix shows: x survives y", lines)
        self.assertNotIn("g.py", lines)

    def test_the_files_of_unsettled_findings_leave_out_fixed_ones(self):
        last = record(findings=[finding("a.py:1:x"), finding("b.py:2:y", "New"), finding("c.py:3:z", "Fixed")])
        self.assertEqual(review.unsettled_finding_files(last), {"a.py", "b.py"})

    def test_a_path_with_a_space_or_b_slash_is_read_whole_from_its_header(self):
        self.assertEqual(review.diff_path("diff --git a/x y.py b/x y.py"), "x y.py")
        self.assertEqual(review.diff_path("diff --git a/a b/c.py b/a b/c.py"), "a b/c.py")

    def test_a_quoted_header_is_unquoted_including_octal_utf8(self):
        self.assertEqual(review.diff_path('diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"'), "caf\u00e9.py")
        self.assertEqual(review.unquote_c('a\\"b\\\\c\\td'), 'a"b\\c\td')

    def test_a_rename_between_odd_paths_follows_its_rename_to_line(self):
        diff = "diff --git a/old b/x.py b/new b/y.py\nsimilarity index 100%\nrename from old b/x.py\nrename to new b/y.py\n"
        self.assertEqual(review.diff_lines(diff), {"new b/y.py": set()})

    def test_names_and_blobs_are_read_from_nul_separated_git_output(self):
        self.assertEqual(review.split_nul("a b.py\0c.py\0"), ["a b.py", "c.py"])
        listing = "100644 blob " + "a" * 40 + "\tdir/x y.py\0" + "040000 tree " + "b" * 40 + "\tdir\0"
        self.assertEqual(review.parse_ls_tree(listing), {"dir/x y.py": "a" * 40})

    def test_a_finding_with_a_review_id_is_not_posted_again(self):
        last = record(findings=[finding(reviewId=3), finding("g.py:1:x")])
        self.assertEqual([f["key"] for f in review.findings_to_post(last)], ["g.py:1:x"])

    def test_a_range_becomes_a_multi_line_inline_comment_and_a_single_line_a_plain_one(self):
        ranged = review.inline_comment(finding("f.py:3-5:crash", fix="use y"))
        self.assertEqual((ranged["path"], ranged["start_line"], ranged["line"]), ("f.py", 3, 5))
        self.assertIn("Recommended fix: use y", ranged["body"])
        self.assertIn("Why it matters: y is the common case", ranged["body"])
        self.assertIn("A correct fix shows: x survives y", ranged["body"])
        single = review.inline_comment(finding("f.py:4:crash"))
        self.assertEqual(single["line"], 4)
        self.assertNotIn("start_line", single)

    def test_comment_ids_come_back_by_the_finding_marker(self):
        body = review.comment_body(finding())
        self.assertEqual(review.comment_ids([{"id": 9, "body": body}, {"id": 10, "body": "other"}]), {"f.py:3-5:crash": 9})


    def test_comment_ids_read_the_marker_marker_writes(self):
        with mock.patch.object(review, "MARKER", __import__("re").compile(r"<!-- moved: (.+?) -->")), mock.patch.object(
                review, "marker", lambda key: f"<!-- moved: {key} -->"):
            self.assertEqual(review.comment_ids([{"id": 9, "body": review.comment_body(finding())}]), {"f.py:3-5:crash": 9})

    def test_a_comment_with_no_body_carries_no_finding(self):
        self.assertEqual(review.comment_ids([{"id": 9, "body": None}]), {})

    def test_the_key_rule_is_lib_issuekeys_one(self):
        canonical = (Path(__file__).resolve().parents[3] / "lib" / "issuekeys.py").read_text()
        self.assertIn(f'ISSUE_KEY_PATTERN = r"{review.KEY_PATTERN.pattern[1:-1]}"', canonical)
        self.assertTrue(review.KEY_PATTERN.match("X-1"))
        self.assertFalse(review.KEY_PATTERN.match("AB2-5"))


class PullRequestDiff(unittest.TestCase):
    def test_the_diff_is_uncoloured_so_its_headers_and_hunks_are_read(self):
        with mock.patch.object(review, "git", return_value="") as git:
            review.Sources().pr_diff("main", HEAD)
        self.assertIn("--no-color", git.call_args.args)

    def test_review_comments_come_from_every_page(self):
        sources = review.Sources()
        with mock.patch.object(sources, "all_pages", return_value=[{"id": 1}]) as pages:
            self.assertEqual(sources.review_comments(7, 55), [{"id": 1}])
        self.assertEqual(pages.call_args.args, ("repos/{owner}/{repo}/pulls/7/reviews/55/comments",))

DIFF = """diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -1,3 +1,12 @@
 context
"""


class WorktreeRoot(unittest.TestCase):
    def test_the_root_is_the_worktree_top_from_any_subdirectory(self):
        with tempfile.TemporaryDirectory() as outer:
            top = Path(outer).resolve()
            subprocess.run(["git", "init", "-q", str(top)], check=True)
            (top / "a" / "b").mkdir(parents=True)
            before = os.getcwd()
            self.addCleanup(os.chdir, before)
            os.chdir(top / "a" / "b")
            self.assertEqual(review.Sources().root().resolve(), top)
            self.assertEqual(review.rounds_folder(top, "SK-9"), top / ".tmp" / "SK-9" / "rounds")


def ask(sources, restart=False):
    """The path request wrote or reopened, or None when it wrote nothing."""
    return review.request("SK-9", sources, restart).path


class FakeSources:
    def __init__(self, head=HEAD, reachable=True, blobs=None, post_error=None, diff=DIFF, deleted=()):
        self.head_sha, self.is_reachable, self.blob_map = head, reachable, blobs or {"f.py": "1"}
        self.deleted, self.blob_heads = list(deleted), []
        self.post_error, self.posts, self.ranges, self.diff = post_error, [], [], diff
        self.file_comments = []

    def root(self):
        return Path.cwd()

    def pr_diff(self, base, head):
        self.diffed = (base, head)
        return self.diff

    def review_comments(self, pull_request, review_id):
        base = 100 * (review_id - 54)
        return [{"id": base + n, "body": c["body"]} for n, c in enumerate(self.posts[review_id - 55]["comments"])]

    def head(self):
        return self.head_sha

    def claude_sessions(self):
        if getattr(self, "agents_error", None):
            raise review.ReviewError(self.agents_error)
        return getattr(self, "sessions", [])

    def own_pid(self):
        return 500

    def parent_pid(self, pid):
        return getattr(self, "parents", {}).get(pid)

    def pull_request(self):
        self.pr_calls = getattr(self, "pr_calls", 0) + 1
        return 7, "main"

    def reachable(self, sha, head):
        return self.is_reachable

    def merges_since(self, sha, head):
        return getattr(self, "merged", False)

    def changed(self, range_, head):
        self.ranges.append(range_)
        self.blob_heads.append(head)
        return self.blob_map, self.deleted

    def reviews(self, pull_request):
        return [{"id": 55 + n, "body": post["body"], "commit_id": post["commit_id"]} for n, post in enumerate(self.posts)]

    def pull_comments(self, pull_request):
        return [{"id": 201 + n, "body": c["body"], "commit_id": c["commit_id"], "subject_type": "file"}
                for n, c in enumerate(self.file_comments)]

    def gh_api(self, endpoint, payload=None):
        if endpoint.endswith("/comments") and payload:
            self.file_comments.append(payload)
            return {"id": 200 + len(self.file_comments)}
        if self.post_error:
            raise review.ReviewError(self.post_error)
        self.posts.append(payload)
        return {"id": 54 + len(self.posts)}


class InRounds(unittest.TestCase):
    def setUp(self):
        outer = tempfile.TemporaryDirectory()
        self.addCleanup(outer.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(outer.name)
        self.rounds = review.rounds_folder(Path.cwd(), "SK-9")

    def folder(self, n):
        return self.rounds / str(n)

    def write(self, n, name, value):
        self.folder(n).mkdir(parents=True, exist_ok=True)
        (self.folder(n) / name).write_text(value if isinstance(value, str) else json.dumps(value))

    def log(self, n):
        return (self.folder(n) / "review.log").read_text()

    def saved(self, n):
        return json.loads((self.folder(n) / "review.json").read_text())


class Layout(InRounds):
    def test_rounds_are_under_the_keys_tmp_folder(self):
        self.assertEqual(self.rounds, Path.cwd() / ".tmp" / "SK-9" / "rounds")

    def test_the_last_review_is_the_highest_round_whose_review_has_a_head(self):
        self.write(0, "review.json", {"findings": [{"disposition": "open"}]})
        self.assertEqual(review.last_record(self.rounds), (None, None))
        self.write(1, "review.json", record(head=OLD, round_number=1))
        self.write(2, "review.json", record(head=HEAD, round_number=2))
        self.write(3, "report.md", "the author's answers")
        self.assertEqual(review.last_record(self.rounds)[0], 2)

    def test_a_round_is_unfinished_with_a_request_and_no_review(self):
        self.write(1, "review.json", record())
        self.write(2, "request.json", {"round": 2, "head": HEAD})
        self.assertEqual(review.unfinished_round(self.rounds)["round"], 2)
        self.write(2, "review.json", record())
        self.assertIsNone(review.unfinished_round(self.rounds))


class DueCommand(InRounds):
    def run_due(self, sources):
        with self.assertRaises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            review.main(["due", "SK-9"], sources)
        return stopped.exception.code

    def test_a_head_that_is_due_exits_0_and_one_that_is_not_exits_1(self):
        self.assertEqual(self.run_due(FakeSources(head=HEAD)), 0)
        self.write(1, "review.json", record(head=HEAD))
        self.assertEqual(self.run_due(FakeSources(head=HEAD)), 1)

    def test_a_malformed_request_json_exits_2_not_1(self):
        self.write(1, "request.json", "[]")
        self.assertEqual(self.run_due(FakeSources(head=HEAD)), 2)


class Request(InRounds):
    def test_the_first_review_opens_round_1_with_the_whole_range(self):
        path = ask(FakeSources())
        text = path.read_text()
        self.assertEqual(path, self.folder(1) / "request.md")
        self.assertIn("First review of SK-9's PR #7 (round 1)", text)
        self.assertIn(f"git diff origin/main...{HEAD}", text)
        self.assertIn(f"`/code-review medium --max-findings all origin/main...{HEAD}`", text)
        self.assertIn("- f.py", text)
        self.assertNotIn("{{", text)
        self.assertIn("requested", self.log(1))

    def test_round_zeros_review_without_a_head_leaves_the_first_post_push_review_a_first_review(self):
        self.write(0, "review.json", {"findings": []})
        self.write(0, "report.md", "x")
        text = ask(FakeSources()).read_text()
        self.assertIn("First review of SK-9's PR #7 (round 1)", text)
        self.assertTrue((self.folder(1) / "request.md").exists())

    def test_a_later_review_opens_the_round_after_the_latest_and_reviews_since_the_last_head(self):
        self.write(1, "review.json", record(findings=[finding()], dispositions=[entry("ok.py", "1"), entry("f.py", "2", ["f.py:3-5:crash"])]))
        self.write(1, "report.md", "the author's answers to round 1")
        sources = FakeSources(blobs={"f.py": "3", "ok.py": "1"})
        path = ask(sources)
        text = path.read_text()
        self.assertEqual(path, self.folder(2) / "request.md")
        self.assertEqual(sources.ranges, [f"{OLD}..{HEAD}"])
        self.assertIn("Review round 2", text)
        self.assertIn(f"`/code-review medium --max-findings all {OLD}..{HEAD}`", text)
        self.assertIn("- f.py:3-5:crash [open]: x crashes when y", text)
        self.assertIn("- ok.py " + "1", text)
        self.assertIn(str(self.folder(1) / "review.json"), text)
        self.assertNotIn("{{", text)

    def test_the_prompt_s_commands_quote_a_path_with_a_space(self):
        spaced = Path.cwd() / "a checkout"
        spaced.mkdir()
        sources = FakeSources()
        sources.root = lambda: spaced
        text = ask(sources).read_text()
        for name in ("first-review.md", "review-round.md"):
            self.assertNotIn("{{SCRIPTS}}", (review.PROMPTS / name).read_text())
        claim = next(line for line in text.splitlines() if "review.py claim" in line).split("`")[1]
        self.assertEqual(shlex.split(claim)[1:], [str(Path(review.__file__).resolve()), "claim", "SK-9"])
        log = next(line for line in text.splitlines() if 'echo "..." >>' in line).split("`")[-2]
        self.assertEqual(shlex.split(log)[-1], str(review.log_path(review.round_dir(review.rounds_folder(spaced, "SK-9"), 1))))

    def test_the_prompt_names_absolute_paths_and_the_request_stores_its_head_range_and_call(self):
        text = ask(FakeSources()).read_text()
        self.assertTrue(self.folder(1).is_absolute())
        self.assertIn(str(self.folder(1) / "review.log"), text)
        self.assertIn(str(self.folder(1) / "review.next.json"), text)
        self.assertIn(f"`{Path.cwd()}`", text)
        stored = json.loads((self.folder(1) / "request.json").read_text())
        self.assertRegex(stored.pop("token"), "^[0-9a-f]{32}$")
        self.assertEqual(stored, {"round": 1, "head": HEAD, "pr": 7, "range": f"origin/main...{HEAD}",
                                  "call": f"/code-review medium --max-findings all origin/main...{HEAD}"})

    def test_an_unfinished_review_for_this_head_is_reopened_without_rewriting_its_files(self):
        path = ask(FakeSources(head=HEAD))
        before = {name: (self.folder(1) / name).read_text() for name in ("request.md", "request.json")}
        reopened = ask(FakeSources(head=HEAD, blobs={"other.py": "9"}))
        self.assertEqual(reopened, path)
        self.assertEqual({name: (self.folder(1) / name).read_text() for name in before}, before)
        self.assertEqual(review.round_numbers(self.rounds), [1])
        self.assertIn("reopened", self.log(1))

    def test_an_unfinished_review_for_an_older_head_is_refused_naming_restart(self):
        ask(FakeSources(head=OLD))
        before = (self.folder(1) / "request.md").read_text()
        with self.assertRaisesRegex(review.ReviewError, "run plan review SK-9 --restart"):
            ask(FakeSources(head=HEAD))
        self.assertEqual((self.folder(1) / "request.md").read_text(), before)

    def test_restart_writes_the_unfinished_review_again_in_its_own_folder_at_head(self):
        self.write(1, "review.json", record(round_number=1))
        ask(FakeSources(head="c" * 40))
        path = ask(FakeSources(head=HEAD), restart=True)
        self.assertEqual(path, self.folder(2) / "request.md")
        self.assertEqual(review.round_numbers(self.rounds), [1, 2])
        self.assertEqual(json.loads((self.folder(2) / "request.json").read_text())["head"], HEAD)
        self.assertIn("restarted", self.log(2))
        self.assertIn(f"Review round 2", path.read_text())

    def test_with_the_head_reviewed_and_no_unfinished_round_nothing_is_written_and_it_says_why(self):
        self.write(2, "review.json", record(head=HEAD, round_number=2))
        outcome = review.request("SK-9", FakeSources(head=HEAD))
        self.assertIsNone(outcome.path)
        self.assertEqual(outcome.note, f"HEAD {HEAD[:8]} was reviewed in round 2")
        self.assertEqual(review.round_numbers(self.rounds), [2])

    def test_the_request_stores_the_pr_number(self):
        ask(FakeSources())
        self.assertEqual(json.loads((self.folder(1) / "request.json").read_text())["pr"], 7)

    def test_after_a_rebase_the_call_names_the_whole_pr_and_only_the_files_not_settled(self):
        self.write(1, "review.json", record(dispositions=[entry("ok.py", "1"), entry("f.py", "2", ["f.py:3-5:crash"])], findings=[finding()]))
        sources = FakeSources(reachable=False, blobs={"ok.py": "1", "f.py": "3", "new.py": "4"})
        text = ask(sources).read_text()
        self.assertEqual(sources.ranges, [f"origin/main...{HEAD}"])
        self.assertIn(f"`/code-review medium --max-findings all origin/main...{HEAD} -- f.py new.py`", text)
        self.assertIn("can't start this round's range (a rebase, or a merge since)", text)
        self.assertNotIn("- ok.py\n", text.split("## Files to read")[1].split("## Files marked OK")[0])

    def test_after_a_merge_the_round_reviews_the_pr_s_range_not_the_base_s_changes(self):
        self.write(1, "review.json", record(dispositions=[entry("ok.py", "1"), entry("f.py", "2", ["f.py:3-5:crash"])], findings=[finding()]))
        sources = FakeSources(blobs={"ok.py": "1", "f.py": "3"})
        sources.merged = True
        text = ask(sources).read_text()
        self.assertEqual(sources.ranges, [f"origin/main...{HEAD}"])
        self.assertIn(f"`/code-review medium --max-findings all origin/main...{HEAD} -- f.py`", text)

    def test_a_merge_is_found_among_the_commits_since_the_last_head(self):
        with mock.patch.object(review, "git", return_value="") as git:
            self.assertFalse(review.Sources().merges_since(OLD, HEAD))
        self.assertEqual(git.call_args.args, ("rev-list", "--merges", "-n", "1", f"{OLD}..{HEAD}"))
        with mock.patch.object(review, "git", return_value="c" * 40):
            self.assertTrue(review.Sources().merges_since(OLD, HEAD))

    def test_after_a_rebase_with_nothing_left_to_review_the_record_moves_to_head_and_no_call_is_made(self):
        self.write(1, "review.json", record(dispositions=[entry("ok.py", "1")]))
        self.assertIsNone(ask(FakeSources(reachable=False, blobs={"ok.py": "1"})))
        self.assertEqual(self.saved(1)["head"], HEAD)
        self.assertFalse((self.folder(2)).exists())


class Pinned(InRounds):
    def test_a_request_made_at_one_commit_keeps_it_in_its_range_call_and_blobs_after_head_moves(self):
        class MovesAfterTheFirstRead(FakeSources):
            def head(self):
                answer = HEAD if not getattr(self, "read", False) else "c" * 40
                self.read = True
                return answer

        sources = MovesAfterTheFirstRead()
        path = ask(sources)
        text = path.read_text()
        self.assertEqual(sources.ranges, [f"origin/main...{HEAD}"])
        self.assertEqual(sources.blob_heads, [HEAD])
        self.assertIn(f"`/code-review medium --max-findings all origin/main...{HEAD}`", text)
        self.assertNotIn("...HEAD", text)
        self.assertNotIn("c" * 40, text)
        self.assertEqual(json.loads((self.folder(1) / "request.json").read_text())["head"], HEAD)

    def test_the_prompts_read_files_at_the_requested_commit_when_the_working_tree_moved(self):
        text = ask(FakeSources()).read_text()
        self.assertIn(f"`git show {HEAD}:<path>`", text)
        self.assertIn(f"`git rev-parse {HEAD}:<path>`", text)

    def test_blobs_are_read_at_the_requested_commit_not_at_head(self):
        def git(*args):
            return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        Path("a.py").write_text("1\n")
        git("add", "-A")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        Path("a.py").write_text("2\n")
        Path("gone.py").write_text("x\n")
        git("add", "-A")
        git("commit", "-qm", "requested")
        requested = git("rev-parse", "HEAD")
        wanted = git("rev-parse", f"{requested}:a.py")
        Path("a.py").write_text("3\n")
        git("rm", "-q", "gone.py")
        git("commit", "-qam", "moved on")
        blobs, deleted = review.Sources().changed(f"{base}..{requested}", requested)
        self.assertEqual(blobs["a.py"], wanted)
        self.assertEqual(deleted, [])
        _, deleted = review.Sources().changed(f"{requested}..HEAD", "HEAD")
        self.assertEqual(deleted, ["gone.py"])


class DiffParsing(unittest.TestCase):
    def test_a_rename_to_a_path_with_b_slash_does_not_swallow_another_files_lines(self):
        with tempfile.TemporaryDirectory() as outer:
            def git(*args):
                return subprocess.run(["git", *args], cwd=outer, check=True, capture_output=True, text=True).stdout

            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            (Path(outer) / "a.py").write_text("one\n")
            (Path(outer) / "base.py").write_text("same content\nof the file\n")
            git("add", "-A")
            git("commit", "-qm", "base")
            (Path(outer) / "a.py").write_text("changed\n")
            (Path(outer) / "z b").mkdir()
            git("mv", "base.py", "z b/a.py")
            git("add", "-A")
            git("commit", "-qm", "change")
            diff = git("-c", "core.quotePath=false", "diff", "--no-ext-diff", "-M", "HEAD~1..HEAD")
        self.assertIn("rename to z b/a.py", diff)
        self.assertEqual(review.diff_lines(diff), {"a.py": {1}, "z b/a.py": set()})

    def test_a_pure_copy_to_a_path_with_b_slash_is_in_the_pr_with_no_lines(self):
        with tempfile.TemporaryDirectory() as outer:
            def git(*args):
                return subprocess.run(["git", *args], cwd=outer, check=True, capture_output=True, text=True).stdout

            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            original = "".join(f"line {n}\n" for n in range(1, 6))
            (Path(outer) / "a.py").write_text(original)
            git("add", "-A")
            git("commit", "-qm", "base")
            (Path(outer) / "a.py").write_text(original + "six\n")
            (Path(outer) / "z b").mkdir()
            (Path(outer) / "z b" / "a.py").write_text(original)
            git("add", "-A")
            git("commit", "-qm", "copy")
            diff = git("-c", "diff.renames=copies", "-c", "core.quotePath=false", "diff", "--no-ext-diff", "HEAD~1..HEAD")
        self.assertIn("copy to z b/a.py", diff)
        self.assertEqual(review.diff_lines(diff), {"a.py": {3, 4, 5, 6}, "z b/a.py": set()})

    def test_added_changed_and_spaced_paths_come_from_the_plus_lines(self):
        diff = ("diff --git a/x y.py b/x y.py\nnew file mode 100644\n--- /dev/null\n+++ b/x y.py\t\n@@ -0,0 +1,2 @@\n+a\n+b\n"
                'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n@@ -3 +3 @@\n-a\n+b\n')
        self.assertEqual(review.diff_lines(diff), {"x y.py": {1, 2}, "caf\u00e9.py": {3}})


class PromptTests(unittest.TestCase):
    def test_the_round_prompt_drops_both_ids_from_a_new_finding(self):
        text = (review.PROMPTS / "review-round.md").read_text()
        self.assertIn('drop its `commentId` and its `reviewId`', text)
        self.assertIn('no `commentId` or `reviewId`', text)

    def test_both_prompts_stop_on_a_head_mismatch_or_a_lock_refusal_and_never_delete_the_lock(self):
        for name in ("first-review.md", "review-round.md"):
            text = (review.PROMPTS / name).read_text()
            self.assertIn("head mismatch", text)
            self.assertIn("stop: edit nothing, never delete `.review.lock`", text)


    def test_the_first_review_writes_each_finding_s_reasoning_for_a_reader_who_never_saw_it(self):
        text = (review.PROMPTS / "first-review.md").read_text()
        self.assertIn("- `why`: why it matters", text)
        self.assertIn("- `fixShows`: what a correct fix must show", text)
        self.assertIn('"why": "...", "fixShows": "..."', text)
        self.assertIn("The next round's reviewer is a new conversation", text)

    def test_the_round_judges_each_fix_against_its_reasoning_reply_and_diff_and_carries_the_reasoning(self):
        text = (review.PROMPTS / "review-round.md").read_text()
        self.assertIn("its reasoning above (`why`, `fixShows`), the\n   author's reply in its review thread", text)
        self.assertIn('Mark it `"Fixed"` when the fix shows what `fixShows` asks', text)
        self.assertIn("Copy each earlier finding's\n   `why` and `fixShows`", text)

    def test_the_round_prompt_keeps_gh_api_s_own_placeholders(self):
        text = review.render((review.PROMPTS / "review-round.md").read_text(), {"PR": 21})
        self.assertIn("gh api repos/{owner}/{repo}/pulls/21/comments", text)


class Deletions(InRounds):
    def last(self, *dispositions, findings=()):
        self.write(1, "review.json", record(dispositions=list(dispositions), findings=list(findings)))

    def test_a_deleted_file_is_in_the_scope_and_blocks_the_shortcut_after_a_rebase(self):
        self.last(entry("ok.py", "1"))
        sources = FakeSources(reachable=False, blobs={"ok.py": "1"}, deleted=["gone.py"])
        path = ask(sources)
        self.assertIsNotNone(path)
        text = path.read_text()
        self.assertIn(f"`/code-review medium --max-findings all origin/main...{HEAD} -- gone.py`", text)
        self.assertIn('- gone.py (deleted, or no longer in the PR; its disposition\'s blob is "absent")', text)
        self.assertEqual(self.saved(1)["head"], OLD)

    def test_a_file_reviewed_before_that_the_pr_no_longer_changes_is_in_the_scope(self):
        self.last(entry("ok.py", "1"), entry("dropped.py", "5"))
        text = ask(FakeSources(reachable=False, blobs={"ok.py": "1"})).read_text()
        self.assertIn("-- dropped.py`", text)
        self.assertIn("- dropped.py (deleted, or no longer in the PR", text)

    def test_a_deleted_file_already_found_ok_as_absent_is_not_reviewed_again(self):
        self.last(entry("ok.py", "1"), entry("gone.py", review.ABSENT))
        outcome = review.request("SK-9", FakeSources(reachable=False, blobs={"ok.py": "1"}, deleted=["gone.py"]))
        self.assertIsNone(outcome.path)
        self.assertEqual(self.saved(1)["head"], HEAD)

    def test_a_round_after_a_normal_push_reads_a_file_the_push_deleted(self):
        self.last(entry("a.py", "1"), entry("b.py", "2"))
        text = ask(FakeSources(blobs={"a.py": "9"}, deleted=["b.py"])).read_text()
        self.assertIn("- a.py", text)
        self.assertIn("- b.py (deleted", text)

    def test_the_state_marks_deleted_paths_absent(self):
        self.assertEqual(review.current_state({"a.py": "1"}, ["b.py"]), {"a.py": "1", "b.py": review.ABSENT})
        self.assertEqual(review.absent_paths(record(dispositions=[entry("a.py", "1"), entry("z.py", "2")]), {"a.py": "1"}), ["z.py"])


class Restart(InRounds):
    def test_a_restarted_round_posts_only_its_new_findings_and_can_always_finish(self):
        class NoCommentsYet(FakeSources):
            hide = True
            def review_comments(self, pull_request, review_id):
                return [] if self.hide else super().review_comments(pull_request, review_id)

        old, new = "b" * 40, "d" * 40
        first = NoCommentsYet(head=old)
        ask(first)
        self.write(1, "review.next.json", record(head=old, findings=[finding("f.py:3:crash")]))
        with self.assertRaisesRegex(review.ReviewError, "returned no comment"):
            review.finish("SK-9", first)
        self.assertEqual(len(first.posts), 1)
        first.head_sha, first.hide = new, False  # HEAD moved, and GitHub answers now
        later = first
        path = ask(later, restart=True)
        self.assertEqual(path, self.folder(1) / "request.md")
        self.write(1, "review.next.json", record(head=new, findings=[finding("f.py:3:crash"), finding("f.py:4:crash")]))
        review.finish("SK-9", later)
        self.assertEqual(len(later.posts), 2)  # the first review, and one more for the new finding only
        self.assertEqual(len(later.posts[1]["comments"]), 1)
        self.assertIn("f.py:4:crash", later.posts[1]["comments"][0]["body"])
        saved = {f["key"]: f.get("commentId") for f in self.saved(1)["findings"]}
        self.assertTrue(saved["f.py:3:crash"] and saved["f.py:4:crash"])
        self.assertFalse((self.folder(1) / "posted.json").exists())

    def test_a_restarted_round_finishes_when_a_missing_comment_belongs_to_a_finding_now_fixed(self):
        class NoComments(FakeSources):
            def review_comments(self, pull_request, review_id):
                return []

        old, new = "b" * 40, "d" * 40
        first = NoComments(head=old)
        ask(first)
        self.write(1, "review.next.json", record(head=old, findings=[finding("f.py:3:crash")]))
        with self.assertRaisesRegex(review.ReviewError, "returned no comment"):
            review.finish("SK-9", first)
        later = NoComments(head=new)
        ask(later, restart=True)
        self.write(1, "review.next.json", record(head=new, findings=[finding("f.py:3:crash", "Fixed")]))
        review.finish("SK-9", later)
        saved = self.saved(1)["findings"]
        self.assertEqual([(f["key"], f["disposition"], f.get("commentId")) for f in saved], [("f.py:3:crash", "Fixed", None)])

    def test_a_missing_comment_for_a_finding_the_record_still_carries_still_refuses(self):
        class NoComments(FakeSources):
            def review_comments(self, pull_request, review_id):
                return []

        ask(NoComments(head=HEAD))
        self.write(1, "review.next.json", record(head=HEAD, findings=[finding("f.py:3:crash")]))
        with self.assertRaisesRegex(review.ReviewError, "returned no comment for f.py:3:crash"):
            review.finish("SK-9", NoComments(head=HEAD))

    def test_the_rebase_shortcut_keeps_what_the_restarted_round_had_posted_with_its_ids(self):
        class FailsOnSecondFile(FakeSources):
            def gh_api(self, endpoint, payload=None):
                if endpoint.endswith("/pulls/7/comments") and payload and len(self.file_comments) == 1:
                    raise review.ReviewError("gh api failed")
                return super().gh_api(endpoint, payload)

        self.write(1, "review.json", record(dispositions=[entry("ok.py", "1")], findings=[finding("old.py:1:x", "Fixed", commentId=3)]))
        c = "c" * 40
        first = FailsOnSecondFile(head=c)
        ask(first)
        self.write(2, "review.next.json", record(head=c, round_number=2, findings=[finding("f.py:98:x"), finding("f.py:99:x")]))
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", first)
        outcome = review.request("SK-9", FakeSources(head=HEAD, reachable=False, blobs={"ok.py": "1"}), restart=True)
        self.assertIsNone(outcome.path)
        kept = {f["key"]: f for f in self.saved(2)["findings"]}
        self.assertEqual(sorted(kept), ["f.py:98:x", "old.py:1:x"])
        self.assertEqual(kept["f.py:98:x"]["commentId"], 201)
        self.assertEqual(kept["f.py:98:x"]["summary"], "x crashes when y")
        self.assertEqual(kept["old.py:1:x"]["commentId"], 3)
        self.assertIsNone(review.unfinished_round(self.rounds))

    def test_a_record_written_before_a_restart_is_refused_telling_the_reviewer_to_stop(self):
        ask(FakeSources(head=OLD))
        ask(FakeSources(head=HEAD), restart=True)
        self.write(1, "review.next.json", record(head=OLD, findings=[finding()]))
        with self.assertRaisesRegex(review.ReviewError, "the round was restarted.*stop and edit nothing"):
            review.finish("SK-9", FakeSources(head=HEAD))
        self.assertFalse((self.folder(1) / "review.json").exists())

    def test_the_rebase_shortcut_on_a_restarted_round_leaves_no_unfinished_round(self):
        self.write(1, "review.json", record(dispositions=[entry("ok.py", "1")]))
        ask(FakeSources(head="c" * 40))
        self.assertIsNotNone(review.unfinished_round(self.rounds))
        outcome = review.request("SK-9", FakeSources(head=HEAD, reachable=False, blobs={"ok.py": "1"}), restart=True)
        self.assertIsNone(outcome.path)
        self.assertIsNone(review.unfinished_round(self.rounds))
        self.assertEqual(review.last_record(self.rounds)[1]["head"], HEAD)
        self.assertEqual(self.saved(2)["round"], 2)
        for name in ("request.md", "request.json", "posted.json"):
            self.assertFalse((self.folder(2) / name).exists())

    def test_pr_s_call_opens_nothing_for_a_round_already_requested_but_a_manual_one_reopens_it(self):
        path = ask(FakeSources(head=HEAD))
        racing = review.request("SK-9", FakeSources(head=HEAD), if_due=True)
        self.assertIsNone(racing.path)
        self.assertIn("hasn't finished", racing.note)
        self.assertEqual(review.request("SK-9", FakeSources(head=HEAD)).path, path)

    def test_pr_s_call_re_checks_due_inside_the_lock_when_another_run_just_finished(self):
        self.write(1, "review.json", record(head=HEAD))
        outcome = review.request("SK-9", FakeSources(head=HEAD), if_due=True)
        self.assertIsNone(outcome.path)
        self.assertEqual(outcome.note, f"HEAD {HEAD[:8]} was reviewed in round 1")

    def test_pr_s_call_still_starts_a_review_that_is_due(self):
        self.assertIsNotNone(review.request("SK-9", FakeSources(head=HEAD), if_due=True).path)


class LockRace(InRounds):
    def lock(self):
        self.rounds.mkdir(parents=True, exist_ok=True)
        return self.rounds / review.LOCK_NAME

    def test_a_run_that_finds_a_dead_lock_after_another_cleared_it_and_took_its_own_does_not_take_it(self):
        gone = subprocess.Popen(["true"])
        gone.wait()
        held = self.lock()
        held.write_text(f"{gone.pid} request\n")
        real = review.clear_dead_lock

        def other_run_clears_and_takes_first(path, holder):
            path.unlink()
            path.write_text(f"{os.getppid()} request\n")  # a live run's lock
            real(path, holder)

        with mock.patch.object(review, "clear_dead_lock", other_run_clears_and_takes_first):
            taken = review.take_lock(held, "request")
        self.assertEqual(taken, os.getppid())
        self.assertEqual(held.read_text(), f"{os.getppid()} request\n")
        self.assertEqual([p.name for p in self.rounds.iterdir()], [review.LOCK_NAME])

    def test_a_run_removes_the_lock_at_the_end_only_while_it_holds_its_own_pid(self):
        held = self.lock()
        with review.review_lock(self.rounds, "request"):
            held.write_text(f"{os.getppid()} request\n")  # another run's lock replaced it
        self.assertTrue(held.exists())  # still the other run's
        held.unlink()
        with review.review_lock(self.rounds, "request"):
            self.assertEqual(review.lock_holder(held), os.getpid())
        self.assertFalse(held.exists())


class Locking(InRounds):
    def lock(self):
        self.rounds.mkdir(parents=True, exist_ok=True)
        return self.rounds / review.LOCK_NAME

    def test_a_held_lock_refuses_a_request_at_once_naming_the_file(self):
        held = self.lock()
        held.write_text("1 request\n")
        with self.assertRaisesRegex(review.ReviewError, f"holds {held}.*delete {held}"):
            ask(FakeSources())
        self.assertTrue(held.exists())
        self.assertEqual(review.round_numbers(self.rounds), [])

    def test_a_lock_whose_process_is_dead_is_cleared_and_the_run_goes_on(self):
        gone = subprocess.Popen(["true"])
        gone.wait()
        held = self.lock()
        held.write_text(f"{gone.pid} request\n")
        self.assertIsNotNone(ask(FakeSources()))
        self.assertFalse(held.exists())

    def test_a_lock_whose_process_is_alive_refuses(self):
        held = self.lock()
        held.write_text(f"{os.getpid()} request\n")
        with self.assertRaisesRegex(review.ReviewError, f"pid {os.getpid()}.*holds {held}"):
            ask(FakeSources())
        self.assertTrue(held.exists())

    def test_a_lock_with_no_readable_pid_refuses_naming_the_file(self):
        held = self.lock()
        held.write_text("")
        with self.assertRaisesRegex(review.ReviewError, f"delete {held}"):
            ask(FakeSources())

    def test_a_held_lock_refuses_finish_too(self):
        self.write(1, "request.json", {"round": 1, "head": HEAD})
        held = self.lock()
        held.write_text("1 request\n")
        with self.assertRaisesRegex(review.ReviewError, "another review run .*holds"):
            review.finish("SK-9", FakeSources())

    def test_the_lock_is_released_after_a_run_and_after_a_failed_one(self):
        ask(FakeSources())
        self.assertFalse(self.lock().exists())

        class Breaks(FakeSources):
            def pull_request(self):
                raise review.ReviewError("gh failed")

        self.write(1, "review.json", record(head=OLD))
        (self.folder(1) / "request.md").unlink(missing_ok=True)
        (self.folder(1) / "request.json").unlink(missing_ok=True)
        (self.folder(1) / "review.json").unlink()
        with self.assertRaises(review.ReviewError):
            ask(Breaks())
        self.assertFalse(self.lock().exists())

    def test_the_lock_is_released_after_finish_whether_it_posts_or_fails(self):
        ask(FakeSources())
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", FakeSources())
        self.assertFalse(self.lock().exists())
        self.write(1, "review.next.json", record(head=HEAD))
        review.finish("SK-9", FakeSources())
        self.assertFalse(self.lock().exists())

    def test_due_is_checked_again_inside_the_lock(self):
        sources = FakeSources(head=HEAD)
        self.assertEqual(review.due("SK-9", sources), "")  # due when the caller asked
        self.write(1, "review.json", record(head=HEAD, round_number=1))  # another run finished meanwhile
        outcome = review.request("SK-9", sources)
        self.assertIsNone(outcome.path)
        self.assertEqual(outcome.note, f"HEAD {HEAD[:8]} was reviewed in round 1")
        self.assertFalse((self.folder(2)).exists())

    def test_a_second_run_that_arrives_while_the_first_holds_the_lock_writes_nothing(self):
        outer = self

        class SecondRunArrives(FakeSources):
            def pull_request(self):
                with outer.assertRaises(review.ReviewError):
                    review.request("SK-9", FakeSources())
                return super().pull_request()

        ask(SecondRunArrives())
        self.assertEqual(review.round_numbers(self.rounds), [1])


class Finish(InRounds):
    def start_review(self, last=None, sources=None):
        if last:
            self.write(1, "review.json", last)
        ask(sources or FakeSources())
        return 2 if last else 1

    def test_findings_are_posted_as_one_review_and_review_json_written_in_the_round(self):
        n = self.start_review()
        sources = FakeSources()
        self.write(n, "review.next.json", record(head=HEAD, findings=[finding()], dispositions=[entry("f.py", "1", ["f.py:3-5:crash"])]))
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)
        self.assertEqual(sources.posts[0]["event"], "COMMENT")
        self.assertEqual(sources.posts[0]["comments"][0]["path"], "f.py")
        saved = self.saved(n)
        self.assertEqual(saved["findings"][0]["commentId"], 100)
        self.assertEqual((saved["findings"][0]["file"], saved["findings"][0]["line"]), ("f.py", "3-5"))
        self.assertEqual(saved["range"], f"origin/main...{HEAD}")
        self.assertTrue(saved["call"].startswith("/code-review medium"))
        for name in ("request.md", "request.json", "review.next.json"):
            self.assertFalse((self.folder(n) / name).exists())
        self.assertIn("finished", self.log(n))

    def test_the_review_never_writes_the_authors_report(self):
        n = self.start_review()
        self.write(n, "review.next.json", record(head=HEAD))
        review.finish("SK-9", FakeSources())
        self.assertFalse((self.folder(n) / "report.md").exists())

    def test_off_diff_findings_post_as_file_comments_and_off_pr_ones_in_the_review_body(self):
        n = self.start_review()
        sources = FakeSources()
        self.write(n, "review.next.json", record(head=HEAD, findings=[
            finding("f.py:3:crash"), finding("f.py:99:loss"), finding("elsewhere.py:1:loss")]))
        review.finish("SK-9", sources)
        review_post = sources.posts[0]
        self.assertEqual([c["path"] for c in review_post["comments"]], ["f.py"])
        self.assertIn("**elsewhere.py:1:loss** (its file isn't in the PR)", review_post["body"])
        self.assertIn("<!-- review-finding: elsewhere.py:1:loss -->", review_post["body"])
        self.assertEqual([c["subject_type"] for c in sources.file_comments], ["file"])
        saved = {f["key"]: f for f in self.saved(n)["findings"]}
        self.assertEqual(saved["f.py:3:crash"]["commentId"], 100)
        self.assertEqual(saved["f.py:99:loss"]["commentId"], 201)
        self.assertEqual(saved["elsewhere.py:1:loss"]["reviewId"], 55)
        self.assertNotIn("commentId", saved["elsewhere.py:1:loss"])

    def test_only_file_level_findings_post_no_review(self):
        n = self.start_review()
        sources = FakeSources()
        self.write(n, "review.next.json", record(head=HEAD, findings=[finding("f.py:99:loss")]))
        review.finish("SK-9", sources)
        self.assertEqual(sources.posts, [])
        self.assertEqual(len(sources.file_comments), 1)

    def test_a_file_comment_that_fails_after_the_review_names_the_review_in_the_log(self):
        class FailsOnFile(FakeSources):
            def gh_api(self, endpoint, payload=None):
                if endpoint.endswith("/pulls/7/comments") and payload:
                    raise review.ReviewError("gh api failed")
                return super().gh_api(endpoint, payload)
        n = self.start_review()
        self.write(n, "review.next.json", record(head=HEAD, findings=[finding("f.py:3:crash"), finding("f.py:99:loss")]))
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", FailsOnFile())
        self.assertFalse((self.folder(n) / "review.json").exists())
        self.assertIn("review 55 had been posted", self.log(n))

    def test_a_push_during_the_review_doesnt_lose_it(self):
        n = self.start_review(sources=FakeSources(head=HEAD))
        later = FakeSources(head="c" * 40)
        self.write(n, "review.next.json", record(head=HEAD, findings=[finding()]))
        review.finish("SK-9", later)
        self.assertEqual(later.posts[0]["commit_id"], HEAD)
        self.assertEqual(later.diffed, ("main", HEAD))
        self.assertEqual(self.saved(n)["head"], HEAD)
        self.assertTrue(not review.due("SK-9", later))

    def test_more_than_thirty_inline_findings_all_get_their_ids(self):
        n = self.start_review()
        many = [finding(f"f.py:{k}:crash") for k in range(1, 41)]
        self.write(n, "review.next.json", record(head=HEAD, findings=many))
        review.finish("SK-9", FakeSources(diff=DIFF.replace("+1,12", "+1,50")))
        saved = self.saved(n)["findings"]
        self.assertEqual(len(saved), 40)
        self.assertTrue(all(f.get("commentId") for f in saved))

    def test_a_finish_posting_a_review_and_file_comments_resolves_the_pr_once(self):
        n = self.start_review()
        stored = json.loads((self.folder(n) / "request.json").read_text())
        del stored["pr"]
        self.write(n, "request.json", stored)
        sources = FakeSources()
        self.write(n, "review.next.json", record(head=HEAD, findings=[
            finding("f.py:3:crash"), finding("f.py:97:loss"), finding("f.py:98:loss"), finding("f.py:99:loss")]))
        review.finish("SK-9", sources)
        self.assertEqual((len(sources.posts), len(sources.file_comments)), (1, 3))
        self.assertEqual(sources.pr_calls, 1)

    def test_the_blobs_of_all_changed_files_come_from_one_ls_tree_call(self):
        commands = []

        def fake_run(command, stdin=None):
            commands.append(command)
            if "diff" in command:
                return "a.py\0b.py\0c.py\0"
            return "".join(f"100644 blob {sha}\t{name}\0" for name, sha in (("a.py", "1"), ("b.py", "2"), ("c.py", "3")))
        with mock.patch.object(review, "run", fake_run):
            blobs, deleted = review.Sources().changed("main..HEAD", "HEAD")
        self.assertEqual((blobs, deleted), ({"a.py": "1", "b.py": "2", "c.py": "3"}, []))
        self.assertEqual(len([c for c in commands if "ls-tree" in c]), 1)
        self.assertFalse([c for c in commands if "rev-parse" in c])

    def test_a_review_is_due_from_the_files_alone(self):
        sources = FakeSources(head=HEAD)
        self.assertEqual(review.due("SK-9", sources), "")
        self.write(1, "review.json", record(head=HEAD))
        self.assertNotEqual(review.due("SK-9", sources), "")
        self.assertEqual(review.due("SK-9", FakeSources(head="c" * 40)), "")

    def test_a_head_already_reviewed_says_so(self):
        self.write(3, "review.json", record(head=HEAD, round_number=3))
        self.assertEqual(review.due("SK-9", FakeSources(head=HEAD)), f"HEAD {HEAD[:8]} was reviewed in round 3")

    def test_a_round_requested_for_this_head_and_unfinished_says_it_is_waiting(self):
        ask(FakeSources(head=HEAD))
        self.assertEqual(
            review.due("SK-9", FakeSources(head=HEAD)),
            f"round 1 was requested for HEAD {HEAD[:8]} and hasn't finished; "
            "it runs in the Review SK-9 terminal of the plan window, or run plan review SK-9 to start it again",
        )

    def test_a_due_review_gives_no_reason(self):
        self.assertEqual(review.due("SK-9", FakeSources(head=HEAD)), "")

    def test_a_file_comment_loop_that_fails_partway_posts_only_what_is_left_when_rerun(self):
        class FailsOnSecondFile(FakeSources):
            def gh_api(self, endpoint, payload=None):
                if endpoint.endswith("/pulls/7/comments") and payload and len(self.file_comments) == 1 and not getattr(self, "healed", False):
                    raise review.ReviewError("gh api failed")
                return super().gh_api(endpoint, payload)
        n = self.start_review()
        self.write(n, "review.next.json", record(head=HEAD, findings=[
            finding("f.py:3:crash"), finding("f.py:98:loss"), finding("f.py:99:loss")]))
        sources = FailsOnSecondFile()
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)
        self.assertEqual(len(sources.file_comments), 1)
        posted = json.loads((self.folder(n) / "posted.json").read_text())
        self.assertEqual(sorted(posted["comments"]), ["f.py:3:crash", "f.py:98:loss"])
        sources.healed = True
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)
        self.assertEqual(len(sources.file_comments), 2)
        saved = {f["key"]: f["commentId"] for f in self.saved(n)["findings"]}
        self.assertEqual(set(saved), {"f.py:3:crash", "f.py:98:loss", "f.py:99:loss"})
        self.assertFalse((self.folder(n) / "posted.json").exists())

    def test_a_review_posted_but_missing_comment_ids_is_not_posted_again_on_rerun(self):
        class NoCommentsYet(FakeSources):
            hide = True
            def review_comments(self, pull_request, review_id):
                return [] if self.hide else super().review_comments(pull_request, review_id)
        n = self.start_review()
        self.write(n, "review.next.json", record(head=HEAD, findings=[finding("f.py:3:crash")]))
        sources = NoCommentsYet()
        with self.assertRaisesRegex(review.ReviewError, "returned no comment"):
            review.finish("SK-9", sources)
        sources.hide = False
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)
        self.assertEqual(self.saved(n)["findings"][0]["commentId"], 100)

    def test_the_pr_number_is_fetched_at_most_once_and_not_at_all_when_the_request_holds_it(self):
        n = self.start_review()
        self.write(n, "review.next.json", record(head=HEAD, findings=[finding("f.py:3:crash"), finding("f.py:99:x"), finding("f.py:98:x")]))
        sources = FakeSources()
        before = getattr(sources, "pr_calls", 0)
        review.finish("SK-9", sources)
        self.assertEqual(getattr(sources, "pr_calls", 0), before)

    def test_without_a_pr_number_in_the_request_it_is_fetched_once(self):
        n = self.start_review()
        request = json.loads((self.folder(n) / "request.json").read_text())
        del request["pr"]
        self.write(n, "request.json", request)
        self.write(n, "review.next.json", record(head=HEAD, findings=[finding("f.py:3:crash"), finding("f.py:99:x"), finding("f.py:98:x")]))
        sources = FakeSources()
        review.finish("SK-9", sources)
        self.assertEqual(sources.pr_calls, 1)

    def test_a_review_with_nothing_to_post_posts_nothing(self):
        n = self.start_review()
        sources = FakeSources()
        self.write(n, "review.next.json", record(head=HEAD, dispositions=[entry("f.py", "1")]))
        review.finish("SK-9", sources)
        self.assertEqual(sources.posts, [])
        self.assertTrue((self.folder(n) / "review.json").exists())

    def test_a_failed_post_leaves_the_earlier_review_and_writes_no_review_json_and_says_why(self):
        last = record(round_number=1, findings=[finding("old.py:1:x", "Fixed", commentId=3)])
        n = self.start_review(last)
        before = (self.folder(1) / "review.json").read_text()
        self.write(n, "review.next.json", record(head=HEAD, round_number=2, findings=[finding()]))
        with self.assertRaisesRegex(review.ReviewError, "gh api failed"):
            review.finish("SK-9", FakeSources(post_error="gh api failed"))
        self.assertEqual((self.folder(1) / "review.json").read_text(), before)
        self.assertFalse((self.folder(n) / "review.json").exists())
        self.assertTrue((self.folder(n) / "request.md").exists())
        self.assertIn("failed: gh api failed; review.json is not written", self.log(n))

    def test_a_record_for_another_head_is_refused_and_logged(self):
        n = self.start_review()
        self.write(n, "review.next.json", record(head=OLD))
        with self.assertRaisesRegex(review.ReviewError, "refused"):
            review.finish("SK-9", FakeSources())
        self.assertFalse((self.folder(n) / "review.json").exists())
        self.assertIn("failed", self.log(n))

    def test_finish_with_no_review_in_progress_is_refused(self):
        with self.assertRaisesRegex(review.ReviewError, "no review is in progress"):
            review.finish("SK-9", FakeSources())


class Claim(InRounds):
    """The reviewer's claim on a round, and the restart it holds off. Claude sessions and the
    process tree are the fake sources'."""

    def sources(self, sessions=(), parents=None, agents_error=None, head=HEAD):
        sources = FakeSources(head=head)
        sources.sessions, sources.agents_error = list(sessions), agents_error
        sources.parents = {500: 400, 400: 300, 300: 1} if parents is None else parents
        return sources

    def reviewer_in_a_terminal(self, state="busy", pid=300):
        return {"pid": pid, "sessionId": f"s{pid}", "status": state}

    def claimed_round(self, pid=300, head=OLD):
        ask(FakeSources(head=head))
        review.claim("SK-9", self.sources([self.reviewer_in_a_terminal(pid=pid)], head=head))

    def stored(self):
        return json.loads((self.folder(1) / "request.json").read_text())

    def test_claim_records_the_pid_and_session_of_the_claude_ancestor(self):
        ask(FakeSources(head=HEAD))
        review.claim("SK-9", self.sources([self.reviewer_in_a_terminal()]))
        self.assertEqual(self.stored()["reviewer"], {"pid": 300, "sessionId": "s300"})
        self.assertIn("claimed by pid 300", self.log(1))

    def test_claim_fails_when_no_ancestor_is_a_claude_session(self):
        ask(FakeSources(head=HEAD))
        with self.assertRaisesRegex(review.ReviewError, "no Claude session is an ancestor"):
            review.claim("SK-9", self.sources([self.reviewer_in_a_terminal(pid=999)]))
        self.assertNotIn("reviewer", self.stored())

    def test_claim_from_the_command_line_exits_2_without_a_claude_ancestor(self):
        ask(FakeSources(head=HEAD))
        with self.assertRaises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()):
            review.main(["claim", "SK-9"], self.sources([]))
        self.assertEqual(stopped.exception.code, 2)

    def test_a_second_claim_by_the_same_pid_changes_nothing(self):
        self.claimed_round()
        before = (self.folder(1) / "request.json").read_text()
        review.claim("SK-9", self.sources([self.reviewer_in_a_terminal()]))
        self.assertEqual((self.folder(1) / "request.json").read_text(), before)

    def test_a_claim_by_a_different_pid_is_refused_while_the_first_is_live(self):
        self.claimed_round(pid=300)
        other = self.sources([self.reviewer_in_a_terminal(pid=300), self.reviewer_in_a_terminal(pid=400)])
        with self.assertRaisesRegex(review.ReviewError, "claimed by the reviewer with pid 300"):
            review.claim("SK-9", other)
        self.assertEqual(self.stored()["reviewer"]["pid"], 300)

    def test_a_claim_replaces_one_whose_pid_is_gone(self):
        self.claimed_round(pid=300)
        review.claim("SK-9", self.sources([self.reviewer_in_a_terminal(pid=400)]))
        self.assertEqual(self.stored()["reviewer"]["pid"], 400)

    def test_restart_is_refused_while_the_claimed_reviewer_is_live_in_any_state(self):
        for state in ("busy", "idle", "waiting"):
            with self.subTest(state=state):
                self.claimed_round()
                before = (self.folder(1) / "request.md").read_text()
                with self.assertRaisesRegex(review.ReviewError, "close its Review SK-9 terminal, then run plan review SK-9 --restart"):
                    ask(self.sources([self.reviewer_in_a_terminal(state)]), restart=True)
                self.assertEqual((self.folder(1) / "request.md").read_text(), before)

    def test_a_reopen_is_refused_while_the_claimed_reviewer_is_live(self):
        self.claimed_round(head=HEAD)
        with self.assertRaisesRegex(review.ReviewError, "still running; close its Review SK-9 terminal, then run plan review SK-9$"):
            ask(self.sources([self.reviewer_in_a_terminal()]))
        self.assertNotIn("reopened", self.log(1))

    def test_a_reopen_proceeds_when_the_claimed_pid_is_gone(self):
        self.claimed_round(head=HEAD)
        self.assertEqual(ask(self.sources([])), self.folder(1) / "request.md")
        self.assertIn("reopened", self.log(1))

    def test_a_reopen_removes_a_reviewer_that_is_not_a_live_claude_session_from_the_request(self):
        self.claimed_round(pid=300, head=HEAD)
        self.assertEqual(self.stored()["reviewer"]["pid"], 300)
        ask(self.sources([self.reviewer_in_a_terminal(pid=999)]))
        self.assertNotIn("reviewer", self.stored())
        self.assertIn("dropped from the request", self.log(1))

    def test_a_reopen_keeps_the_request_as_it_is_when_nobody_claimed_the_round(self):
        ask(FakeSources(head=HEAD))
        before = (self.folder(1) / "request.json").read_text()
        ask(self.sources([]))
        self.assertEqual((self.folder(1) / "request.json").read_text(), before)

    def test_restart_proceeds_when_the_claimed_pid_is_gone(self):
        self.claimed_round()
        path = ask(self.sources([]), restart=True)
        self.assertEqual(path, self.folder(1) / "request.md")
        self.assertNotIn("reviewer", self.stored())
        self.assertIn("restarted", self.log(1))

    def test_restart_proceeds_when_nobody_claimed_the_round(self):
        ask(FakeSources(head=OLD))
        sources = self.sources(agents_error="would not be asked")
        self.assertEqual(ask(sources, restart=True), self.folder(1) / "request.md")

    def test_restart_is_refused_when_claude_agents_fails(self):
        self.claimed_round()
        with self.assertRaisesRegex(review.ReviewError, "can't tell whether round 1's reviewer .*claude agents boom"):
            ask(self.sources(agents_error="claude agents boom"), restart=True)


class Withdraw(Claim):
    """Taking back a request whose review never started: only the one this run wrote, only while it is
    still the request and nobody has claimed it."""

    def written(self, head=HEAD):
        return review.request("SK-9", FakeSources(head=head), restart=False, if_due=True)

    def test_a_request_this_run_wrote_is_withdrawn_and_the_next_if_due_run_writes_it_again(self):
        outcome = self.written()
        said = review.withdraw("SK-9", FakeSources(), outcome.token)
        self.assertIn("withdrawn", said)
        self.assertFalse((self.folder(1) / "request.json").exists())
        self.assertFalse((self.folder(1) / "request.md").exists())
        self.assertIn("withdrawn", self.log(1))
        self.assertIsNotNone(self.written().path)

    def test_a_written_request_names_its_round_and_says_it_was_written(self):
        outcome = self.written()
        self.assertEqual((outcome.round, outcome.written), (1, True))
        self.assertEqual(self.stored()["token"], outcome.token)

    def test_a_reopened_request_carries_its_stored_token_for_the_link_but_was_not_written(self):
        first = self.written()
        reopened = review.request("SK-9", FakeSources(head=HEAD))
        self.assertEqual((reopened.token, reopened.round, reopened.written), (first.token, 1, False))
        self.assertEqual(self.stored()["token"], first.token)
        self.assertTrue((self.folder(1) / "request.json").exists())

    def test_the_request_command_prints_the_round_and_whether_it_was_written(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            review.main(["request", "SK-9"], sources=FakeSources())
        printed = json.loads(out.getvalue())
        self.assertEqual((printed["round"], printed["written"], printed["token"]), (1, True, self.stored()["token"]))

    def test_a_claimed_request_is_not_withdrawn(self):
        outcome = self.written()
        review.claim("SK-9", self.sources([self.reviewer_in_a_terminal()]))
        self.assertIn("claimed it", review.withdraw("SK-9", FakeSources(), outcome.token))
        self.assertTrue((self.folder(1) / "request.json").exists())

    def test_a_request_replaced_by_a_restart_is_not_withdrawn_by_the_old_token(self):
        old = self.written(head=OLD)
        replacement = review.request("SK-9", FakeSources(head=HEAD), restart=True)
        self.assertIn("not the one this run wrote", review.withdraw("SK-9", FakeSources(), old.token))
        self.assertEqual(self.stored()["token"], replacement.token)
        self.assertTrue((self.folder(1) / "request.md").exists())

    def test_a_withdrawn_restart_is_written_again_in_its_own_round_and_keeps_what_it_posted(self):
        self.write(1, "review.json", record(head=OLD))
        old = review.request("SK-9", FakeSources(head="c" * 40))
        posted = {"reviews": [{"id": 45, "keys": ["f.py:3:crash"]}], "comments": {"f.py:3:crash": 9}}
        self.write(old.round, "posted.json", posted)
        restarted = review.request("SK-9", FakeSources(head=HEAD), restart=True)
        review.withdraw("SK-9", FakeSources(), restarted.token)
        again = review.request("SK-9", FakeSources(head=HEAD), if_due=True)
        self.assertEqual((again.round, again.written), (restarted.round, True))
        self.assertEqual(json.loads((self.folder(again.round) / "posted.json").read_text())["comments"], {"f.py:3:crash": 9})
        self.assertFalse(self.folder(again.round + 1).exists())
        self.assertIn("rewritten", self.log(again.round))

    def test_a_withdrawn_first_request_is_written_again_as_the_same_round(self):
        review.withdraw("SK-9", FakeSources(), self.written().token)
        self.assertEqual(self.written().round, 1)

    def test_withdraw_takes_the_review_lock(self):
        outcome = self.written()
        (self.rounds / review.LOCK_NAME).write_text(f"{os.getpid()} finish\n")
        with self.assertRaisesRegex(review.ReviewError, "another review run"):
            review.withdraw("SK-9", FakeSources(), outcome.token)
        self.assertTrue((self.folder(1) / "request.json").exists())


class LostAnswers(InRounds):
    """A POST GitHub took whose answer, or the journal write after it, was lost is found on the PR
    by its finding markers on the rerun; one that never arrived is sent again."""

    def start(self, keys):
        ask(FakeSources())
        self.write(1, "review.next.json", record(head=HEAD, findings=[finding(key) for key in keys]))

    def lost_after_posting(self, kind):
        class Lost(FakeSources):
            armed = True

            def gh_api(self, endpoint, payload=None):
                answer = super().gh_api(endpoint, payload)
                is_file = endpoint.endswith("/pulls/7/comments")
                if self.armed and payload and is_file == (kind == "file"):
                    self.armed = False
                    raise review.ReviewError("connection lost after the POST")
                return answer
        return Lost()

    def test_a_review_posted_remotely_whose_answer_was_lost_is_not_posted_again(self):
        self.start(["f.py:3:crash"])
        sources = self.lost_after_posting("review")
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)
        self.assertEqual(self.saved(1)["findings"][0]["commentId"], 100)

    def test_a_review_whose_journal_write_was_lost_is_found_by_its_body_markers_too(self):
        self.start(["elsewhere.py:1:loss", "f.py:3:crash"])
        sources = self.lost_after_posting("review")
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", sources)
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)
        saved = {f["key"]: f for f in self.saved(1)["findings"]}
        self.assertEqual(saved["elsewhere.py:1:loss"]["reviewId"], 55)
        self.assertEqual(saved["f.py:3:crash"]["commentId"], 100)

    def test_a_file_comment_posted_remotely_whose_answer_was_lost_is_not_posted_again(self):
        self.start(["f.py:99:loss"])
        sources = self.lost_after_posting("file")
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", sources)
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.file_comments), 1)
        self.assertEqual(self.saved(1)["findings"][0]["commentId"], 201)

    def test_a_post_that_never_arrived_is_posted_on_the_rerun(self):
        self.start(["f.py:3:crash"])
        sources = FakeSources(post_error="gh api failed")
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", sources)
        self.assertEqual(json.loads((self.folder(1) / "posted.json").read_text())["intent"]["kind"], "review")
        sources.post_error = None
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)
        self.assertEqual(self.saved(1)["findings"][0]["commentId"], 100)

    def test_a_file_comment_that_never_arrived_is_posted_on_the_rerun(self):
        self.start(["f.py:99:loss"])

        class Refuses(FakeSources):
            refuse = True

            def gh_api(self, endpoint, payload=None):
                if self.refuse and payload and endpoint.endswith("/pulls/7/comments"):
                    raise review.ReviewError("gh api failed")
                return super().gh_api(endpoint, payload)
        sources = Refuses()
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", sources)
        sources.refuse = False
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.file_comments), 1)

    def test_a_review_of_an_earlier_round_with_the_same_key_is_not_taken_for_this_ones(self):
        self.start(["f.py:3:crash"])
        sources = FakeSources(post_error="gh api failed")
        sources.posts.append({"body": "Review round 0: 1 finding(s) in this review.\n<!-- review-finding: f.py:3:crash -->",
                              "commit_id": HEAD, "comments": []})
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", sources)
        sources.post_error = None
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 2)


WRITER = """| Lens | Question |
|---|---|
| L1 Security | What input is untrusted? |
| L3 Reliability and failure | What happens on partial failure? |
| L8 Testability | Can it be tested without the network? |
"""
REPO_WRITER = "| L3 Reliability | The repo's own question. |\n| L11 Store | Does the store stay consistent? |\n"
TICKET = """# SK-9 Thing

## Task
Do it.

## Guardrails
- Leaves docs/adr/ alone.

## Lenses
- [x] L1 Security: the link carries no prompt
- [ ] L2 Privacy and data
- [x] L3 Reliability and failure: a rerun posts nothing twice

## Done
- [ ] L1: checked
"""


class Lenses(unittest.TestCase):
    def test_a_ticket_file_gives_its_selected_lenses_with_what_each_asks(self):
        brief = review.lens_brief("/store/SK-9.md", TICKET, [WRITER])
        self.assertIn("- [x] L1 Security: the link carries no prompt\n- [x] L3 Reliability and failure: a rerun", brief)
        self.assertNotIn("L2 Privacy", brief)
        self.assertIn("| L1 Security | What input is untrusted? |", brief)
        self.assertNotIn("L8 Testability", brief)

    def test_the_repo_s_lens_table_adds_lenses_and_overrides_the_global_rows(self):
        rows = review.lens_rows([WRITER, REPO_WRITER])
        self.assertEqual(rows["L3"], "| L3 Reliability | The repo's own question. |")
        self.assertEqual(sorted(rows, key=lambda lens: int(lens[1:])), ["L1", "L3", "L8", "L11"])

    def test_a_jira_issue_names_its_page_and_lists_every_lens(self):
        brief = review.lens_brief("https://jira.example/browse/CIAM-1", None, [WRITER])
        self.assertIn("https://jira.example/browse/CIAM-1", brief)
        self.assertIn("`## Lenses`", brief)
        self.assertIn("| L8 Testability |", brief)

    def test_no_ticket_or_an_unreadable_one_falls_back_to_every_lens(self):
        self.assertIn("No ticket was named, so review against every lens", review.lens_brief(None, None, [WRITER]))
        unreadable = review.lens_brief("/gone.md", None, [WRITER])
        self.assertIn("The ticket `/gone.md` can't be read", unreadable)
        self.assertIn("| L1 Security |", unreadable)

    def test_a_selected_lens_takes_a_capital_x_and_any_bullet(self):
        text = "## Lenses\n- [X] L1 Security\n* [x] L3 Reliability\n+ [x] L8 Testability\n- [ ] L11 Retries\n"
        self.assertEqual(review.selected_lenses(text),
                         ["- [X] L1 Security", "* [x] L3 Reliability", "+ [x] L8 Testability"])

    def test_a_selected_lens_with_no_row_is_named_under_the_table(self):
        brief = review.lens_brief("/t.md", "## Lenses\n- [x] L1 Security\n- [x] L99 Unknown\n", [WRITER])
        self.assertIn("| L1 Security |", brief)
        self.assertNotIn("| L99", brief)
        self.assertIn("No question found for L99", brief)

    def test_a_ticket_selecting_no_lens_says_the_fan_out_has_no_lens_legs(self):
        self.assertIn("selects no lens", review.lens_brief("/t.md", "## Lenses\n- [ ] L1 Security\n", [WRITER]))


class LensesInTheRequest(InRounds):
    def test_the_request_carries_the_ticket_s_lenses_on_both_prompts(self):
        writer = Path.cwd() / "writer.md"
        writer.write_text(WRITER)
        ticket = Path.cwd() / "SK-9-thing.md"
        ticket.write_text(TICKET)
        with mock.patch.object(review, "GLOBAL_WRITER", writer):
            first = review.request("SK-9", FakeSources(), ticket=str(ticket)).path.read_text()
            self.assertIn("- [x] L3 Reliability and failure: a rerun posts nothing twice", first)
            self.assertIn("| L3 Reliability and failure |", first)
            self.assertNotIn("{{", first)
            self.write(1, "review.json", record(head=OLD, round_number=1))
            (self.folder(1) / "request.json").unlink()
            (self.folder(1) / "request.md").unlink()
            later = review.request("SK-9", FakeSources(), ticket=str(ticket)).path.read_text()
        self.assertIn("- [x] L1 Security: the link carries no prompt", later)
        self.assertNotIn("{{", later)

    def test_the_request_command_takes_the_ticket(self):
        with mock.patch.object(review, "request", return_value=review.Outcome(None, "n", None)) as asked, \
                contextlib.redirect_stdout(io.StringIO()):
            review.main(["request", "SK-9", "--ticket", "/store/SK-9.md"], sources=FakeSources())
        self.assertEqual(asked.call_args.kwargs["ticket"], "/store/SK-9.md")


SCOPE_RULE = "- Leaves docs/adr/ alone."
PLACEHOLDER_LEFT = r"\{\{[A-Z_]+\}\}"


class ScopeInTheRequest(InRounds):
    def request_text(self, ticket=None):
        return review.request("SK-9", FakeSources(), ticket=ticket).path.read_text()

    def start_round_2(self):
        self.write(1, "review.json", record(head=OLD, round_number=1))

    def test_the_request_renders_the_ticket_s_task_and_guardrails(self):
        ticket = Path.cwd() / "SK-9-thing.md"
        ticket.write_text(TICKET)
        first = self.request_text(str(ticket))
        self.assertIn(f"### Task\n\nDo it.\n\n### Guardrails\n\n{SCOPE_RULE}", first)
        (self.folder(1) / "request.json").unlink()
        self.start_round_2()
        later = self.request_text(str(ticket))
        self.assertIn("## The PR's scope", later)
        self.assertIn(f"### Guardrails\n\n{SCOPE_RULE}", later)

    def test_a_ticket_without_guardrails_says_so(self):
        self.assertIn("### Guardrails\n\n(none)", review.scope_brief("/t.md", "## Task\nDo it.\n"))

    def test_a_jira_ticket_gets_the_fallback_and_nothing_is_fetched(self):
        text = self.request_text("https://jira.example/browse/CIAM-1")
        self.assertIn("The issue is in Jira, https://jira.example/browse/CIAM-1.", text)
        self.assertIn("`## Task` and `## Guardrails`", text)

    def test_no_ticket_or_an_unreadable_one_rules_nothing_out(self):
        self.assertIn("No ticket was named, so no ticket line rules a finding out of scope", review.scope_brief(None, None))
        self.assertIn("The ticket `/gone.md` can't be read, so no ticket line", review.scope_brief("/gone.md", None))

    def test_the_request_renders_the_round_s_notes_md_when_it_is_there(self):
        self.start_round_2()
        self.write(2, review.AUTHOR_NOTES, "Round 2 leaves the tabled ADR alone.\n")
        path = review.request("SK-9", FakeSources()).path
        self.assertEqual(path, self.folder(2) / "request.md")
        self.assertIn("### The author's notes for this round\n\nRound 2 leaves the tabled ADR alone.\n", path.read_text())

    def test_the_request_says_none_when_the_round_has_no_notes(self):
        self.assertIn("### The author's notes for this review\n\nNone.\n", self.request_text())
        (self.folder(1) / "request.json").unlink()
        self.start_round_2()
        self.assertIn("### The author's notes for this round\n\nNone.\n", self.request_text())

    def test_the_rendered_prompts_leave_no_placeholder(self):
        ticket = Path.cwd() / "SK-9-thing.md"
        ticket.write_text(TICKET)
        self.assertNotRegex(self.request_text(str(ticket)), PLACEHOLDER_LEFT)
        (self.folder(1) / "request.json").unlink()
        self.start_round_2()
        self.write(2, review.AUTHOR_NOTES, "Leaves lib/ alone.")
        self.assertNotRegex(self.request_text(str(ticket)), PLACEHOLDER_LEFT)

    def test_a_placeholder_in_the_author_s_notes_is_left_as_written(self):
        self.write(1, review.AUTHOR_NOTES, "The template's {{HEAD}} stays.")
        self.assertIn("The template's {{HEAD}} stays.", self.request_text())

    def test_render_never_reads_a_value_for_placeholders(self):
        self.assertEqual(review.render("{{SCOPE_RULES}} {{AUTHOR_NOTES}}", {"SCOPE_RULES": "{{AUTHOR_NOTES}}", "AUTHOR_NOTES": "None."}),
                         "{{AUTHOR_NOTES}} None.")


class OutOfScope(InRounds):
    def round_2_answering(self, earlier, answer):
        self.write(1, "review.json", record(findings=[earlier]))
        ask(FakeSources())
        self.write(2, "review.next.json", record(head=HEAD, round_number=2, findings=[answer]))

    def test_finish_accepts_an_out_of_scope_finding_with_its_scope_rule_and_posts_nothing_for_it(self):
        self.round_2_answering(finding(commentId=7), finding(state="OutOfScope", commentId=7, scopeRule=SCOPE_RULE))
        sources = FakeSources()
        review.finish("SK-9", sources)
        self.assertEqual((sources.posts, sources.file_comments), ([], []))
        saved = self.saved(2)["findings"][0]
        self.assertEqual((saved["disposition"], saved["scopeRule"]), ("OutOfScope", SCOPE_RULE))

    def test_finish_refuses_an_out_of_scope_finding_without_its_scope_rule(self):
        self.round_2_answering(finding(commentId=7), finding(state="OutOfScope", commentId=7))
        with self.assertRaisesRegex(review.ReviewError, "an OutOfScope finding needs scopeRule"):
            review.finish("SK-9", FakeSources())
        self.assertFalse((self.folder(2) / "review.json").exists())

    def test_a_blank_scope_rule_is_refused_too(self):
        problems = review.finding_problems(finding(state="OutOfScope", commentId=7, scopeRule="  "))
        self.assertEqual(problems, ["finding f.py:3-5:crash: an OutOfScope finding needs scopeRule, the Guardrails "
                                    "or notes.md line that rules it out, quoted"])

    def test_an_out_of_scope_finding_is_settled_and_not_carried_to_the_next_request(self):
        ruled_out = finding("docs/adr/0006.md:4:stale-ref", "OutOfScope", commentId=7, scopeRule=SCOPE_RULE)
        self.write(1, "review.json", record(findings=[ruled_out, finding("g.py:1:x", commentId=8)]))
        self.assertEqual([f["key"] for f in review.unsettled_findings(self.saved(1))], ["g.py:1:x"])
        text = ask(FakeSources()).read_text()
        self.assertIn("- g.py:1:x [open]", text)
        self.assertNotIn("docs/adr/0006.md:4:stale-ref", text)

    def test_an_unknown_disposition_names_out_of_scope_among_the_four(self):
        self.assertIn("finding f.py:3-5:crash: disposition is open, Fixed, New or OutOfScope",
                      review.finding_problems(finding(state="Deferred")))


def round_rule(number):
    """Rule number of the round prompt's ## Rules, up to the next rule."""
    rules = (review.PROMPTS / "review-round.md").read_text().split("## Rules", 1)[1]
    return rules.split(f"\n{number}. ", 1)[1].split(f"\n{number + 1}. ", 1)[0]


class ScopePrompts(unittest.TestCase):
    def test_both_prompts_carry_the_pr_s_scope_and_the_out_of_scope_disposition(self):
        for name in ("first-review.md", "review-round.md"):
            text = (review.PROMPTS / name).read_text()
            self.assertIn("## The PR's scope", text)
            self.assertIn("{{SCOPE_RULES}}", text)
            self.assertIn("{{AUTHOR_NOTES}}", text)
            self.assertIn('`"OutOfScope"`', text)
            self.assertIn("the PR's scope section above, as it is", text)

    def test_the_round_raises_new_findings_in_its_diff_and_up_to_two_steps_out_within_scope(self):
        rule = " ".join(round_rule(3).split())
        self.assertIn("The round's reach is its diff, `git diff {{RANGE}}`, and the files one and two steps out", rule)
        self.assertIn("A new finding anchors on a line inside the diff, or in a file one or two steps out when "
                      "this round's change materially changes that file's behaviour or breaks it", rule)
        self.assertIn("Two steps is the limit.", rule)
        self.assertIn("a break in a file the Guardrails or the author's notes rule out, or one further than two "
                      "steps out, is not a finding but an advisory without a lens", rule)
        self.assertIn("a regression (something that held at the last reviewed head and fails now), a silent loss "
                      "or a crash", rule)

    def test_the_round_judges_a_fix_over_the_same_two_step_reach(self):
        self.assertIn("the round's reach, the code one and two steps out from the change", " ".join(round_rule(1).split()))


def advisory(lens="L11 Retries", seen=("a.py:1", "b.py:2", "c.py:3")):
    return {"concern": "a failed call is retried without a limit", "lens": f"{lens}: how often is a call retried?",
            "seen": list(seen)}


OUTSIDE = {"concern": "docs/adr/0006.md still names lib/gone.py", "seen": ["docs/adr/0006.md:4"]}


class Advisories(InRounds):
    def start(self, findings=(), advisories=()):
        ask(FakeSources())
        self.write(1, "review.next.json", {**record(head=HEAD, findings=list(findings)), "advisories": list(advisories)})

    def test_an_advisory_needs_a_concern_a_lens_and_three_places(self):
        self.assertEqual(review.advisory_problems(advisory()), [])
        self.assertIn("seen lists the 3 or more places", review.advisory_problems(advisory(seen=("a.py:1", "b.py:2")))[0])
        self.assertIn("needs concern and lens", review.advisory_problems({"lens": "", "seen": ["a", "b", "c"]})[0])
        self.assertIn("advisories is not a list",
                      review.record_problems({**record(head=HEAD), "advisories": {}}, 1, HEAD))

    def test_an_advisory_without_a_lens_is_a_break_outside_the_diff_with_one_place_or_more(self):
        self.assertEqual(review.advisory_problems(OUTSIDE), [])
        self.assertIn("seen lists the places the break was found", review.advisory_problems({**OUTSIDE, "seen": []})[0])
        self.assertIn("needs concern", review.advisory_problems({"seen": ["a.py:1"]})[0])

    def test_a_break_outside_the_diff_is_in_the_body_under_its_own_heading(self):
        self.start([], [advisory(), OUTSIDE])
        sources = FakeSources()
        review.finish("SK-9", sources)
        body = sources.posts[0]["body"]
        self.assertIn(review.ADVISORY_MARKER, body)
        self.assertIn("Advisory: concerns found more than twice, which could become lenses", body)
        self.assertIn("Advisory: breaks beyond this round's reach or the PR's scope; not findings, nothing to answer\n"
                      "- docs/adr/0006.md still names lib/gone.py (at docs/adr/0006.md:4)", body)

    def test_the_advisory_is_in_the_body_of_the_review_with_the_findings(self):
        self.start([finding("f.py:3:crash")], [advisory()])
        sources = FakeSources()
        review.finish("SK-9", sources)
        body = sources.posts[0]["body"]
        self.assertIn("Advisory: concerns found more than twice, which could become lenses", body)
        self.assertIn("- **L11 Retries: how often is a call retried?**: a failed call is retried without a limit "
                      "(seen 3 times: a.py:1, b.py:2, c.py:3)", body)
        self.assertEqual(self.saved(1)["advisories"], [advisory()])

    def test_an_advisory_with_no_finding_to_post_is_still_posted_as_a_review(self):
        self.start([], [advisory()])
        sources = FakeSources()
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)
        self.assertEqual(sources.posts[0]["comments"], [])
        self.assertIn(review.ADVISORY_MARKER, sources.posts[0]["body"])

    def test_a_rerun_after_a_lost_answer_does_not_post_the_advisory_twice(self):
        self.start([], [advisory()])

        class Lost(FakeSources):
            armed = True

            def gh_api(self, endpoint, payload=None):
                answer = super().gh_api(endpoint, payload)
                if self.armed:
                    self.armed = False
                    raise review.ReviewError("connection lost after the POST")
                return answer

        sources = Lost()
        with self.assertRaises(review.ReviewError):
            review.finish("SK-9", sources)
        review.finish("SK-9", sources)
        self.assertEqual(len(sources.posts), 1)

    def test_a_round_that_already_carried_its_advisory_at_this_head_does_not_post_it_again(self):
        self.start([finding("f.py:3:crash")], [advisory()])
        self.write(1, "posted.json", {"advisedHead": HEAD})
        sources = FakeSources()
        review.finish("SK-9", sources)
        self.assertNotIn(review.ADVISORY_MARKER, sources.posts[0]["body"])

    def test_a_restarted_round_posts_the_new_head_s_advisories_though_an_older_head_carried_some(self):
        self.start([finding("f.py:3:crash")], [advisory()])
        self.write(1, "posted.json", {"advised": True, "advisedHead": OLD})
        sources = FakeSources()
        review.finish("SK-9", sources)
        self.assertIn(review.ADVISORY_MARKER, sources.posts[0]["body"])

    def test_a_review_carries_the_advisory_when_the_intent_had_none_or_its_body_has_the_marker(self):
        self.assertTrue(review.carries_advisory({"body": "x"}, {"advisory": False}))
        self.assertTrue(review.carries_advisory({"body": f"x {review.ADVISORY_MARKER}"}, {"advisory": True}))
        self.assertFalse(review.carries_advisory({"body": "x"}, {"advisory": True}))

    def test_a_posted_json_from_before_advisedHead_reads_as_advised_at_no_head(self):
        self.start([finding("f.py:3:crash")], [advisory()])
        self.write(1, "posted.json", {"advised": True})
        self.assertIsNone(review.read_posted(self.folder(1))["advisedHead"])
        sources = FakeSources()
        review.finish("SK-9", sources)
        self.assertIn(review.ADVISORY_MARKER, sources.posts[0]["body"])


class FanOutPrompts(unittest.TestCase):
    def test_both_prompts_fan_out_to_sonnet_on_the_lenses_and_keep_the_owner_off_the_code(self):
        for name in ("first-review.md", "review-round.md"):
            text = (review.PROMPTS / name).read_text()
            self.assertIn("{{LENSES}}", text)
            self.assertIn('Sonnet subagents (`model: "sonnet"`)', text)
            self.assertIn("**One per selected lens.**", text)
            self.assertIn("not permission to change it", text)
            self.assertIn("`advisories`", text)

    def test_the_round_prompt_reviews_only_the_round_s_changes(self):
        text = (review.PROMPTS / "review-round.md").read_text()
        self.assertIn("This is not a review of the whole PR again.", text)
        self.assertIn("whether it fixes that finding", text)


if __name__ == "__main__":
    unittest.main()
