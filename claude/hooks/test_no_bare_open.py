#!/usr/bin/env python3
"""Tests for no_bare_open.py: a page never opens through system `open`.

Run:  python3 .claude/hooks/test_no_bare_open.py
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import no_bare_open as hook  # noqa: E402

HOOK = Path(hook.__file__)

ALLOW = [
    ("a folder", "open ."),
    ("a pdf", "open report.pdf"),
    ("an app", 'open -a "Brave Browser"'),
    ("plan browse", "plan browse CIAM-123"),
    ("plan browse a url", "plan browse https://example.com SK-12"),
    ("open in prose", 'echo "run open https://example.com yourself"'),
    ("grep for open", "grep -n 'open https://' lib/term.py"),
    ("html not opened", "cat page.html"),
    ("opener word", "xdg-open-like page.html"),
]

BLOCK = [
    ("bare url", "open https://example.com"),
    ("http url", "open http://127.0.0.1:8000/issues/SK-12/report"),
    ("file url", "open file:///tmp/report.html"),
    ("html file", "open .tmp/SK-12/report.html"),
    ("htm file", "open index.HTM"),
    ("html with fragment", "open page.html#top"),
    ("named browser", 'open -a "Brave Browser" https://example.com'),
    ("new window", 'open -na "Brave Browser" --args --new-window https://example.com'),
    ("full path", "/usr/bin/open https://example.com"),
    ("after &&", "cd /tmp && open report.html"),
    ("after ;", "true; open https://example.com"),
    ("second line", "cd /tmp\nopen https://example.com"),
    ("inside $( )", "x=$(open https://example.com)"),
    ("unbalanced quote", "open https://example.com 'oops"),
]


class RuleTests(unittest.TestCase):
    """The pure function, no process boundary."""

    def test_allows(self):
        for label, command in ALLOW:
            with self.subTest(label):
                self.assertIsNone(hook.violation(command), label)

    def test_blocks(self):
        for label, command in BLOCK:
            with self.subTest(label):
                self.assertEqual(hook.violation(command), hook.BLOCKED, label)


class ProcessTests(unittest.TestCase):
    """The hook as Claude Code runs it: JSON on stdin, exit code out."""

    @staticmethod
    def run_hook(command: str) -> subprocess.CompletedProcess:
        payload = json.dumps({"tool_name": "Bash", "cwd": os.getcwd(), "tool_input": {"command": command}})
        return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True)

    def test_exit_0_allows(self):
        result = self.run_hook(ALLOW[0][1])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_exit_2_blocks_with_reason(self):
        result = self.run_hook(BLOCK[0][1])
        self.assertEqual(result.returncode, 2)
        self.assertIn("plan browse", result.stderr)

    def test_missing_command_allows(self):
        result = subprocess.run([sys.executable, str(HOOK)], input="{}", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
