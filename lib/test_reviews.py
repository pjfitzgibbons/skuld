import subprocess
import unittest
from unittest import mock

from lib import reviews

COPILOT = {"login": "copilot-pull-request-reviewer"}


def pr(state="OPEN", head="b", reviewedAt=(), author=COPILOT):
    return {
        "state": state,
        "headRefOid": head,
        "reviews": [{"author": author, "commit": {"oid": oid}} for oid in reviewedAt],
    }


class CopilotReviewWaitingTests(unittest.TestCase):
    def test_copilot_on_the_head_commit_is_waiting(self):
        self.assertTrue(reviews.copilotReviewWaiting(pr(reviewedAt=("a", "b"))))

    def test_a_push_after_the_review_clears_it(self):
        self.assertFalse(reviews.copilotReviewWaiting(pr(reviewedAt=("a",))))

    def test_a_person_reviewing_the_head_is_not_copilot(self):
        self.assertFalse(reviews.copilotReviewWaiting(pr(reviewedAt=("b",), author={"login": "someone"})))

    def test_a_merged_or_closed_pr_is_not_waiting(self):
        self.assertFalse(reviews.copilotReviewWaiting(pr(state="MERGED", reviewedAt=("b",))))
        self.assertFalse(reviews.copilotReviewWaiting(pr(state="CLOSED", reviewedAt=("b",))))

    def test_no_pr_or_no_head_is_not_waiting(self):
        self.assertFalse(reviews.copilotReviewWaiting({}))
        self.assertFalse(reviews.copilotReviewWaiting(pr(head=None, reviewedAt=("b",))))


class ReviewedKeysTests(unittest.TestCase):
    def test_keys_whose_pr_has_a_review_waiting(self):
        prs = {"/w/SK-1": pr(reviewedAt=("b",)), "/w/SK-2": pr(reviewedAt=("a",)), "/w/SK-3": None}
        found = reviews.reviewedKeys({"SK-1": "/w/SK-1", "SK-2": "/w/SK-2", "SK-3": "/w/SK-3"}, prs.get)
        self.assertEqual(found, frozenset({"SK-1"}))


class ReadPrTests(unittest.TestCase):
    def test_gh_failing_or_no_pr_is_none(self):
        failed = subprocess.CalledProcessError(1, "gh", stderr='no pull requests found for branch "x"')
        with mock.patch.object(reviews.subprocess, "run", side_effect=failed):
            self.assertIsNone(reviews.readPr("/w"))

    def test_gh_that_hangs_is_given_up_as_none(self):
        hung = subprocess.TimeoutExpired("gh", reviews.GH_TIMEOUT_SECONDS)
        with mock.patch.object(reviews.subprocess, "run", side_effect=hung) as run:
            self.assertIsNone(reviews.readPr("/w"))
        self.assertEqual(run.call_args.kwargs["timeout"], reviews.GH_TIMEOUT_SECONDS)

    def test_gh_output_is_read_as_json_in_the_worktree(self):
        answer = subprocess.CompletedProcess("gh", 0, stdout='{"state": "OPEN"}')
        with mock.patch.object(reviews.subprocess, "run", return_value=answer) as run:
            self.assertEqual(reviews.readPr("/w"), {"state": "OPEN"})
        self.assertEqual(run.call_args.kwargs["cwd"], "/w")
        self.assertIn("state,reviews,headRefOid", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
