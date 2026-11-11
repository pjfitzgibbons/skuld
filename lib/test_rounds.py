# lib/rounds.py: a round's data read from fixture round folders in a temp dir.
import json
import os
import tempfile
import unittest

from lib import rounds


class RoundsTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.folder = rounds.roundsFolder(self.dir.name, "SK-0001")

    def round(self, number, files=None):
        path = os.path.join(self.folder, str(number))
        os.makedirs(path)
        for name, text in (files or {}).items():
            with open(os.path.join(path, name), "w", encoding="utf8") as f:
                f.write(text)
        return path


class RoundNumberTests(RoundsTestCase):
    def test_rounds_folder_is_in_the_issues_tmp(self):
        self.assertEqual(self.folder, os.path.join(self.dir.name, ".tmp", "SK-0001", "rounds"))

    def test_numbers_are_the_numbered_folders_in_order(self):
        for number in (2, 10, 1):
            self.round(number)
        os.makedirs(os.path.join(self.folder, "draft"))
        open(os.path.join(self.folder, "3"), "w").close()
        self.assertEqual(rounds.roundNumbers(self.folder), [1, 2, 10])
        self.assertEqual(rounds.latestRound(self.folder), 10)

    def test_no_folder_is_no_rounds(self):
        self.assertEqual(rounds.roundNumbers(self.folder), [])
        self.assertIsNone(rounds.latestRound(self.folder))

    def test_a_round_number_is_digits_from_0_without_a_leading_zero(self):
        for text in ("0", "1", "12"):
            self.assertTrue(rounds.isRoundNumber(text))
        for text in ("00", "01", "..", "../1", "1/..", "", "-1", "1a"):
            self.assertFalse(rounds.isRoundNumber(text), text)


class ReadRoundTests(RoundsTestCase):
    def test_reads_the_report_findings_and_dispositions(self):
        review = {
            "findings": [{"file": "lib/a.py", "line": 3, "summary": "s", "disposition": "fixed"}],
            "dispositions": [{"path": "lib/a.py", "blob": "abc", "passes": ["bugs"], "verdict": "OK"}],
        }
        path = self.round(1, {"report.md": "# Round one\n", "review.json": json.dumps(review)})
        found = rounds.readRound(path, 1)
        self.assertEqual(found.report, "# Round one\n")
        self.assertEqual(found.findings, review["findings"])
        self.assertEqual(found.dispositions, review["dispositions"])
        self.assertEqual(found.notes, [])

    def test_an_empty_round_names_both_missing_files(self):
        found = rounds.readRound(self.round(1), 1)
        self.assertIsNone(found.report)
        self.assertEqual(found.notes, ["report.md is missing from this round.", "review.json is missing from this round."])

    def test_malformed_review_json_is_a_note(self):
        found = rounds.readRound(self.round(1, {"report.md": "r", "review.json": "{nope"}), 1)
        self.assertEqual(found.findings, [])
        self.assertRegex(found.notes[0], "^review.json can't be read")

    def test_review_json_that_isnt_an_object_is_a_note(self):
        found = rounds.readRound(self.round(1, {"report.md": "r", "review.json": "[]"}), 1)
        self.assertEqual(found.notes, ["review.json isn't a JSON object."])

    def test_entries_that_arent_objects_are_left_out_with_a_note(self):
        review = {"findings": "none", "dispositions": [{"path": "a"}, "b", 3]}
        found = rounds.readRound(self.round(1, {"report.md": "r", "review.json": json.dumps(review)}), 1)
        self.assertEqual(found.findings, [])
        self.assertEqual(found.dispositions, [{"path": "a"}])
        self.assertEqual(
            found.notes,
            ['review.json\'s "findings" isn\'t a list.', 'review.json\'s "dispositions" has 2 entries that aren\'t objects, left out.'],
        )

    def test_a_report_that_isnt_utf8_is_a_note(self):
        path = self.round(1, {"review.json": "{}"})
        with open(os.path.join(path, "report.md"), "wb") as f:
            f.write(b"\xff\xfe")
        found = rounds.readRound(path, 1)
        self.assertIsNone(found.report)
        self.assertRegex(found.notes[0], "^report.md can't be read")


if __name__ == "__main__":
    unittest.main()
