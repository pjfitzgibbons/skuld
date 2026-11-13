# The issue key every store shares (lib/issue.py).
import unittest

from lib.issue import KEY_RE, projectOf


class KeyTests(unittest.TestCase):
    def test_key_re_and_project_of(self):
        self.assertTrue(KEY_RE.match("CIAM-123"))
        self.assertTrue(KEY_RE.match("SK-0003"))
        self.assertFalse(KEY_RE.match("123"))
        self.assertFalse(KEY_RE.match("release-2026-10"))
        self.assertEqual(projectOf("CIAM-123"), "CIAM")

    def test_key_re_requires_two_or_more_char_project_code(self):
        # A single letter before the dash is the shape of a stray token, not a real project code;
        # the unified pattern (lib/issue.py, pr.sh, ghbody.sh) rejects it everywhere the same way.
        self.assertFalse(KEY_RE.match("C-1"))
        self.assertTrue(KEY_RE.match("C1-1"))


if __name__ == "__main__":
    unittest.main()
