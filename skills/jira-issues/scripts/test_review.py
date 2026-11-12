"""Tests for review.py: the record's rules, and request and finish with git and gh faked."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import review

HEAD = "b" * 40
OLD = "a" * 40


def finding(key="f.py:3-5:crash", state="open", **more):
    return {"key": key, "summary": "x crashes when y", "disposition": state, **more}


def record(head=OLD, round_number=1, findings=(), dispositions=()):
    return {"head": head, "base": "main", "round": round_number, "dispositions": list(dispositions),
            "findings": list(findings)}


def entry(path, blob, verdict="OK"):
    return {"path": path, "blob": blob, "passes": ["callers"], "verdict": verdict}


class Rules(unittest.TestCase):
    def test_no_record_is_a_first_review_of_the_whole_pr(self):
        self.assertEqual(review.choose_prompt(None), "first-review.md")
        self.assertEqual(review.diff_range("main", None), "origin/main...HEAD")

    def test_a_record_is_a_round_over_what_changed_since_its_head(self):
        self.assertEqual(review.choose_prompt(record()), "review-round.md")
        self.assertEqual(review.diff_range("main", record()), f"{OLD}..HEAD")

    def test_a_recorded_head_no_longer_in_history_reviews_the_whole_pr(self):
        self.assertEqual(review.diff_range("main", record(), head_reachable=False), "origin/main...HEAD")

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
            "/code-review high --max-findings all origin/main...HEAD -- a.py 'my file.py'",
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
        self.assertEqual(review.review_call("origin/main...HEAD"), "/code-review high --max-findings all origin/main...HEAD")
        self.assertEqual(review.review_call(f"{OLD}..HEAD"), f"/code-review high --max-findings all {OLD}..HEAD")

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

    def test_a_finding_with_a_review_id_is_not_posted_again(self):
        last = record(findings=[finding(reviewId=3), finding("g.py:1:x")])
        self.assertEqual([f["key"] for f in review.findings_to_post(last)], ["g.py:1:x"])

    def test_a_range_becomes_a_multi_line_inline_comment_and_a_single_line_a_plain_one(self):
        ranged = review.inline_comment(finding("f.py:3-5:crash", fix="use y"))
        self.assertEqual((ranged["path"], ranged["start_line"], ranged["line"]), ("f.py", 3, 5))
        self.assertIn("Recommended fix: use y", ranged["body"])
        single = review.inline_comment(finding("f.py:4:crash"))
        self.assertEqual(single["line"], 4)
        self.assertNotIn("start_line", single)

    def test_comment_ids_come_back_by_the_finding_marker(self):
        body = review.comment_body(finding())
        self.assertEqual(review.comment_ids([{"id": 9, "body": body}, {"id": 10, "body": "other"}]), {"f.py:3-5:crash": 9})


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


class FakeSources:
    def __init__(self, head=HEAD, reachable=True, blobs=None, post_error=None, diff=DIFF):
        self.head_sha, self.is_reachable, self.changed = head, reachable, blobs or {"f.py": "1"}
        self.post_error, self.posts, self.ranges, self.diff = post_error, [], [], diff
        self.file_comments = []

    def root(self):
        return Path.cwd()

    def pr_diff(self, base, head):
        self.diffed = (base, head)
        return self.diff

    def review_comments(self, pull_request, review_id):
        return [{"id": 100 + n, "body": c["body"]} for n, c in enumerate(self.posts[-1]["comments"])]

    def head(self):
        return self.head_sha

    def pull_request(self):
        return 7, "main"

    def reachable(self, sha):
        return self.is_reachable

    def blobs(self, range_):
        self.ranges.append(range_)
        return self.changed

    def gh_api(self, endpoint, payload=None):
        if endpoint.endswith("/comments") and payload:
            self.file_comments.append(payload)
            return {"id": 200 + len(self.file_comments)}
        if self.post_error:
            raise review.ReviewError(self.post_error)
        self.posts.append(payload)
        return {"id": 55}


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


class Request(InRounds):
    def test_the_first_review_opens_round_1_with_the_whole_range(self):
        path = review.request("SK-9", "stopped", FakeSources())
        text = path.read_text()
        self.assertEqual(path, self.folder(1) / "request.md")
        self.assertIn("First review of SK-9's PR #7 (round 1)", text)
        self.assertIn("git diff origin/main...HEAD", text)
        self.assertIn("`/code-review high --max-findings all origin/main...HEAD`", text)
        self.assertIn("- f.py", text)
        self.assertNotIn("{{", text)
        self.assertIn("requested", self.log(1))

    def test_round_zeros_review_without_a_head_leaves_the_first_post_push_review_a_first_review(self):
        self.write(0, "review.json", {"findings": []})
        self.write(0, "report.md", "x")
        text = review.request("SK-9", "stopped", FakeSources()).read_text()
        self.assertIn("First review of SK-9's PR #7 (round 1)", text)
        self.assertTrue((self.folder(1) / "request.md").exists())

    def test_a_later_review_opens_the_round_after_the_latest_and_reviews_since_the_last_head(self):
        self.write(1, "review.json", record(findings=[finding()], dispositions=[entry("ok.py", "1"), entry("f.py", "2", ["f.py:3-5:crash"])]))
        self.write(1, "report.md", "the author's answers to round 1")
        sources = FakeSources(blobs={"f.py": "3", "ok.py": "1"})
        path = review.request("SK-9", "stopped", sources)
        text = path.read_text()
        self.assertEqual(path, self.folder(2) / "request.md")
        self.assertEqual(sources.ranges, [f"{OLD}..HEAD"])
        self.assertIn("Review round 2", text)
        self.assertIn(f"`/code-review high --max-findings all {OLD}..HEAD`", text)
        self.assertIn("- f.py:3-5:crash [open]: x crashes when y", text)
        self.assertIn("- ok.py " + "1", text)
        self.assertIn(str(self.folder(1) / "review.json"), text)
        self.assertNotIn("{{", text)

    def test_the_prompt_names_absolute_paths_and_the_request_stores_its_head_range_and_call(self):
        text = review.request("SK-9", "stopped", FakeSources()).read_text()
        self.assertTrue(self.folder(1).is_absolute())
        self.assertIn(str(self.folder(1) / "review.log"), text)
        self.assertIn(str(self.folder(1) / "review.next.json"), text)
        self.assertIn(f"`{Path.cwd()}`", text)
        stored = json.loads((self.folder(1) / "request.json").read_text())
        self.assertEqual(stored, {"round": 1, "head": HEAD, "range": "origin/main...HEAD",
                                  "call": "/code-review high --max-findings all origin/main...HEAD"})

    def test_a_review_still_running_in_the_issues_claude_starts_no_second_one(self):
        review.request("SK-9", "stopped", FakeSources())
        with self.assertRaisesRegex(review.ReviewError, "round 1's review is still running"):
            review.request("SK-9", "working", FakeSources())

    def test_a_review_left_unfinished_by_an_idle_claude_is_logged_stopped_and_redone_in_its_folder(self):
        self.write(1, "review.json", record(round_number=1))
        review.request("SK-9", "stopped", FakeSources())
        review.request("SK-9", "stopped", FakeSources())
        self.assertIn("stopped", self.log(2))
        self.assertIn("Review round 2", (self.folder(2) / "request.md").read_text())
        self.assertEqual(review.round_numbers(self.rounds), [1, 2])
        self.assertEqual(self.saved(1)["round"], 1)

    def test_after_a_rebase_the_call_names_the_whole_pr_and_only_the_files_not_settled(self):
        self.write(1, "review.json", record(dispositions=[entry("ok.py", "1"), entry("f.py", "2", ["f.py:3-5:crash"])], findings=[finding()]))
        sources = FakeSources(reachable=False, blobs={"ok.py": "1", "f.py": "3", "new.py": "4"})
        text = review.request("SK-9", "stopped", sources).read_text()
        self.assertEqual(sources.ranges, ["origin/main...HEAD"])
        self.assertIn("`/code-review high --max-findings all origin/main...HEAD -- f.py new.py`", text)
        self.assertIn("no longer in this branch's history", text)
        self.assertNotIn("- ok.py\n", text.split("## Files to read")[1].split("## Files marked OK")[0])

    def test_after_a_rebase_with_nothing_left_to_review_the_record_moves_to_head_and_no_call_is_made(self):
        self.write(1, "review.json", record(dispositions=[entry("ok.py", "1")]))
        self.assertIsNone(review.request("SK-9", "stopped", FakeSources(reachable=False, blobs={"ok.py": "1"})))
        self.assertEqual(self.saved(1)["head"], HEAD)
        self.assertFalse((self.folder(2)).exists())


class Finish(InRounds):
    def start_review(self, last=None, sources=None):
        if last:
            self.write(1, "review.json", last)
        review.request("SK-9", "stopped", sources or FakeSources())
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
        self.assertEqual(saved["range"], "origin/main...HEAD")
        self.assertTrue(saved["call"].startswith("/code-review high"))
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
        self.assertTrue(review.is_due("SK-9", later))

    def test_more_than_thirty_inline_findings_all_get_their_ids(self):
        n = self.start_review()
        many = [finding(f"f.py:{k}:crash") for k in range(1, 41)]
        self.write(n, "review.next.json", record(head=HEAD, findings=many))
        review.finish("SK-9", FakeSources(diff=DIFF.replace("+1,12", "+1,50")))
        saved = self.saved(n)["findings"]
        self.assertEqual(len(saved), 40)
        self.assertTrue(all(f.get("commentId") for f in saved))

    def test_a_review_is_due_from_the_files_alone(self):
        sources = FakeSources(head=HEAD)
        self.assertTrue(review.is_due("SK-9", sources))
        self.write(1, "review.json", record(head=HEAD))
        self.assertFalse(review.is_due("SK-9", sources))
        self.assertTrue(review.is_due("SK-9", FakeSources(head="c" * 40)))

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


if __name__ == "__main__":
    unittest.main()
