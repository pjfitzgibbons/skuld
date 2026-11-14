# lib/issuekeys.py: the one issue-key rule.
import unittest

from lib.issuekeys import ISSUE_KEY_PATTERN, KEY_RE, projectOf


class IssueKeyTests(unittest.TestCase):
    def test_the_pattern_is_exactly_the_one_the_extension_carries(self):
        # vscode/plan-window/plan.test.js asserts the same string, so the two copies cannot drift.
        self.assertEqual(ISSUE_KEY_PATTERN, "[A-Z]+-[0-9]+")

    def test_a_key_is_capital_letters_a_dash_and_ascii_digits(self):
        for key in ["CIAM-123", "SK-0063"]:
            self.assertTrue(KEY_RE.match(key), key)
        for key in ["123", "release-2026-10", "ab-1", "AB-", "AB_C-1", "AB-١٢", "AB2-1", "C1-1"]:
            self.assertFalse(KEY_RE.match(key), key)

    def test_project_of_is_the_prefix(self):
        self.assertEqual(projectOf("CIAM-123"), "CIAM")


if __name__ == "__main__":
    unittest.main()
