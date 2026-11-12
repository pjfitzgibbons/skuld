import json
import os
import tempfile
import unittest
from unittest import mock

from lib import reviews

HEAD = "a" * 40


def record(head=HEAD, states=("open",)):
    findings = [{"key": f"f.py:{n}:crash", "summary": "c", "disposition": state} for n, state in enumerate(states, 1)]
    return {"head": head, "base": "main", "round": 1, "dispositions": [], "findings": findings}


class ReviewWaitingTests(unittest.TestCase):
    def test_a_finished_round_of_the_head_with_open_findings_is_waiting(self):
        self.assertTrue(reviews.reviewWaiting((record(), HEAD, False)))
        self.assertTrue(reviews.reviewWaiting((record(states=("Fixed", "New")), HEAD, False)))

    def test_a_push_after_the_review_clears_it(self):
        self.assertFalse(reviews.reviewWaiting((record(head="b" * 40), HEAD, False)))

    def test_findings_all_fixed_or_none_are_not_waiting(self):
        self.assertFalse(reviews.reviewWaiting((record(states=("Fixed",)), HEAD, False)))
        self.assertFalse(reviews.reviewWaiting((record(states=()), HEAD, False)))

    def test_an_unfinished_round_is_not_waiting(self):
        self.assertFalse(reviews.reviewWaiting((record(), HEAD, True)))

    def test_no_record_or_no_head_is_not_waiting(self):
        self.assertFalse(reviews.reviewWaiting((None, HEAD, False)))
        self.assertFalse(reviews.reviewWaiting((record(), None, False)))


class ReviewedKeysTests(unittest.TestCase):
    def test_keys_whose_review_is_waiting(self):
        states = {
            "/w/SK-1": (record(), HEAD, False),
            "/w/SK-2": (record(states=("Fixed",)), HEAD, False),
            "/w/SK-3": (None, HEAD, False),
        }
        found = reviews.reviewedKeys(
            {"SK-1": "/w/SK-1", "SK-2": "/w/SK-2", "SK-3": "/w/SK-3"}, lambda worktree, key: states[worktree]
        )
        self.assertEqual(found, frozenset({"SK-1"}))


class ReadReviewStateTests(unittest.TestCase):
    def worktree(self, files):
        outer = tempfile.TemporaryDirectory()
        self.addCleanup(outer.cleanup)
        for name, text in files.items():
            path = os.path.join(outer.name, ".tmp", "SK-1", "rounds", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as file:
                file.write(text)
        return outer.name

    def test_the_last_review_and_an_unfinished_one_are_read_from_the_rounds(self):
        worktree = self.worktree({"1/review.json": json.dumps(record()), "2/request.json": "{}"})
        with mock.patch.object(reviews, "git", return_value=HEAD + "\n"):
            state = reviews.readReviewState(worktree, "SK-1")
        self.assertEqual(state, (record(), HEAD, True))

    def test_round_zeros_review_without_a_head_is_not_a_review_of_the_pr(self):
        worktree = self.worktree({"0/review.json": json.dumps({"findings": [{"disposition": "open"}]})})
        self.assertIsNone(reviews.readRecord(worktree, "SK-1"))

    def test_the_highest_round_with_a_head_is_the_last_review(self):
        worktree = self.worktree({"1/review.json": json.dumps(record(head="b" * 40)), "2/review.json": json.dumps(record())})
        self.assertEqual(reviews.readRecord(worktree, "SK-1")["head"], HEAD)

    def test_a_missing_or_broken_record_is_none(self):
        self.assertIsNone(reviews.readRecord(self.worktree({}), "SK-1"))
        self.assertIsNone(reviews.readRecord(self.worktree({"1/review.json": "{"}), "SK-1"))

    def test_a_git_failure_leaves_no_head(self):
        with mock.patch.object(reviews, "git", side_effect=RuntimeError("not a repo")):
            self.assertIsNone(reviews.headOf("/w"))


class ReviewPromptTests(unittest.TestCase):
    def test_the_line_names_the_key_and_the_absolute_request_path(self):
        self.assertEqual(
            reviews.reviewPrompt("SK-0062", "/w/SK-0062/.tmp/SK-0062/review/request.md"),
            "SK-0062 review: read /w/SK-0062/.tmp/SK-0062/review/request.md and do the review it describes.",
        )


class RequestRoundTests(unittest.TestCase):
    def test_the_script_runs_in_the_worktree_and_its_refusal_is_raised(self):
        from subprocess import CompletedProcess

        refused = CompletedProcess("x", 1, stdout="", stderr="review: round 1 is still running")
        with mock.patch.object(reviews.subprocess, "run", return_value=refused) as run:
            with self.assertRaisesRegex(RuntimeError, "still running"):
                reviews.requestRound("/w", "SK-1", "working")
        self.assertEqual(run.call_args.kwargs["cwd"], "/w")
        self.assertEqual(run.call_args.args[0][-4:], ["request", "SK-1", "--claude-state", "working"])


if __name__ == "__main__":
    unittest.main()
