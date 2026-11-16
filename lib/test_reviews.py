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


class RobustnessTests(unittest.TestCase):
    def test_a_state_that_cannot_be_read_is_not_waiting_and_spares_the_other_issues(self):
        def readState(worktree, key):
            if key == "SK-1":
                raise PermissionError("rounds folder")
            return record(), HEAD, False

        found = reviews.reviewedKeys({"SK-1": "/w/SK-1", "SK-2": "/w/SK-2"}, readState)
        self.assertEqual(found, frozenset({"SK-2"}))

    def test_a_record_the_rule_cannot_read_is_not_waiting(self):
        broken = {"head": HEAD, "findings": [{"key": "f:1:x"}]}
        self.assertEqual(reviews.reviewedKeys({"SK-1": "/w"}, lambda worktree, key: (broken, HEAD, False)), frozenset())

    def test_an_unreadable_worktree_has_no_review_state(self):
        with mock.patch.object(reviews, "readRecord", side_effect=PermissionError("denied")):
            self.assertEqual(reviews.readReviewState("/w", "SK-1"), (None, None, False))

    def test_review_py_is_loaded_on_first_use_and_once(self):
        reviews.review.cache_clear()
        self.addCleanup(reviews.review.cache_clear)
        with mock.patch.object(reviews, "loadReviewScript", return_value=object()) as load:
            first, second = reviews.review(), reviews.review()
        self.assertIs(first, second)
        load.assert_called_once()

    def test_a_missing_review_py_fails_only_the_review_features(self):
        reviews.review.cache_clear()
        self.addCleanup(reviews.review.cache_clear)
        with mock.patch.object(reviews, "REVIEW_SCRIPT", "/nonexistent/review.py"):
            with mock.patch.object(reviews, "loadReviewScript", side_effect=FileNotFoundError):
                self.assertEqual(reviews.reviewedKeys({"SK-1": "/w"}, lambda w, k: (record(), HEAD, False)), frozenset())


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

        refused = CompletedProcess("x", 1, stdout="", stderr="review: run plan review SK-1 --restart")
        with mock.patch.object(reviews.subprocess, "run", return_value=refused) as run:
            with self.assertRaisesRegex(RuntimeError, "--restart"):
                reviews.requestRound("/w", "SK-1")
        self.assertEqual(run.call_args.kwargs["cwd"], "/w")
        self.assertEqual(run.call_args.args[0][-2:], ["request", "SK-1"])

    def test_restart_is_passed_on_and_the_outcome_read(self):
        from subprocess import CompletedProcess

        answer = CompletedProcess("x", 0, stdout='{"path": "/w/r.md", "note": "written"}\n', stderr="")
        with mock.patch.object(reviews.subprocess, "run", return_value=answer) as run:
            self.assertEqual(reviews.requestRound("/w", "SK-1", restart=True), ("/w/r.md", "written"))
        self.assertEqual(run.call_args.args[0][-3:], ["request", "SK-1", "--restart"])

    def test_if_due_is_passed_on(self):
        from subprocess import CompletedProcess

        answer = CompletedProcess("x", 0, stdout='{"path": null, "note": "n"}', stderr="")
        with mock.patch.object(reviews.subprocess, "run", return_value=answer) as run:
            reviews.requestRound("/w", "SK-1", ifDue=True)
        self.assertEqual(run.call_args.args[0][-3:], ["request", "SK-1", "--if-due"])

    def test_nothing_written_comes_back_as_no_path(self):
        from subprocess import CompletedProcess

        answer = CompletedProcess("x", 0, stdout='{"path": null, "note": "HEAD abc was reviewed in round 1"}', stderr="")
        with mock.patch.object(reviews.subprocess, "run", return_value=answer):
            self.assertEqual(reviews.requestRound("/w", "SK-1"), (None, "HEAD abc was reviewed in round 1"))


if __name__ == "__main__":
    unittest.main()
