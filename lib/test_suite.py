import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from . import suite


def write(folder, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / suite.CONFIG_NAME).write_text(text)


REPO = Path(__file__).parent.parent


def run(start, flags=()):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = suite.runSuite(start, flags)
    return code, out.getvalue(), err.getvalue()


class SuiteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def test_finds_config_in_current_folder(self):
        write(self.root, "true\n")
        self.assertEqual(suite.findConfig(self.root), self.root / suite.CONFIG_NAME)

    def test_finds_config_in_parent(self):
        write(self.root, "true\n")
        child = self.root / "a" / "b"
        child.mkdir(parents=True)
        self.assertEqual(suite.findConfig(child), self.root / suite.CONFIG_NAME)

    def test_nearest_config_wins(self):
        write(self.root, "true\n")
        write(self.root / "inner", "true\n")
        self.assertEqual(suite.findConfig(self.root / "inner"), self.root / "inner" / suite.CONFIG_NAME)

    def test_none_found_refuses_naming_start_folder(self):
        code, out, err = run(self.root)
        self.assertEqual(code, 2)
        self.assertIn(str(self.root), err)
        self.assertIn(suite.CONFIG_NAME, err)

    def test_commands_run_in_order_in_the_config_folder(self):
        marker = self.root / "marker"
        write(self.root, f"pwd > {marker}\necho second >> {marker}\n")
        child = self.root / "child"
        child.mkdir()
        code, out, _ = run(child)
        self.assertEqual(code, 0)
        self.assertEqual(marker.read_text().split("\n")[:2], [str(self.root), "second"])
        self.assertIn("== echo second", out)
        self.assertIn("suite: passed", out)

    def test_stops_at_first_failure_with_its_code(self):
        marker = self.root / "marker"
        write(self.root, f"exit 7\ntouch {marker}\n")
        code, out, _ = run(self.root)
        self.assertEqual(code, 7)
        self.assertFalse(marker.exists())
        self.assertNotIn("suite: passed", out)

    def test_comments_and_blank_lines_ignored(self):
        write(self.root, "# note\n\n  \ntrue\n")
        self.assertEqual(suite.commandsIn(self.root / suite.CONFIG_NAME), ["true"])

    def test_empty_config_refused_naming_file(self):
        write(self.root, "# only a comment\n\n")
        code, _, err = run(self.root)
        self.assertEqual(code, 2)
        self.assertIn(str(self.root / suite.CONFIG_NAME), err)

    def test_dry_run_prints_config_and_commands_and_runs_none(self):
        marker = self.root / "marker"
        write(self.root, f"# note\ntouch {marker}\necho second\n")
        code, out, _ = run(self.root, ["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), [f"# {self.root / suite.CONFIG_NAME}", f"touch {marker}", "echo second"])
        self.assertFalse(marker.exists())

    def test_dry_run_with_no_config_refuses(self):
        code, out, err = run(self.root, ["--dry-run"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(suite.CONFIG_NAME, err)

    def test_unknown_flag_refused_with_usage(self):
        write(self.root, "true\n")
        code, out, err = run(self.root, ["--bogus"])
        self.assertEqual(code, 2)
        self.assertIn(suite.USAGE, err)
        self.assertEqual(out, "")

    def test_command_line_prints_before_the_commands_own_output(self):
        write(self.root, "echo child-output\n")
        script = f"from lib import suite; raise SystemExit(suite.runSuite({str(self.root)!r}))"
        done = subprocess.run([sys.executable, "-c", script], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(done.stdout.splitlines()[:2], ["== echo child-output", "child-output"])


if __name__ == "__main__":
    unittest.main()
