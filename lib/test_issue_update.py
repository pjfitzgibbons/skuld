# lib/issue_update.py and bin/issue_update, run on a temp store (lib/store_sandbox.py): what each
# run writes to the ticket's front matter and ## Log, whether INDEX.md was regenerated, and that no
# commit was made.
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from lib.fileissues import _parse
from lib.issue_update import LOG_WIDTH, parseArgs, updateTicket
from lib.store_sandbox import FILE_NAME, KEY, OTHER_KEY, head, makeStore

TODAY = date.today().isoformat()
BIN = Path(__file__).resolve().parent.parent / "bin" / "issue_update"


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="skuld-issue-update-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = makeStore(self.root)
        self.ticket = self.store / FILE_NAME
        self.index = self.store / "INDEX.md"

    def front(self):
        return _parse(self.ticket.read_text())[0]

    def log(self):
        return _parse(self.ticket.read_text())[3]

    def staleIndex(self):
        self.index.write_text("stale\n")


class ParseArgsTests(unittest.TestCase):
    def test_key_fields_and_log(self):
        self.assertEqual(
            parseArgs(["SK-0047", "status=done", "ordinal=5", "--log", "x y"]),
            ("SK-0047", {"status": "done", "ordinal": "5"}, "x y"),
        )

    def test_a_bare_word_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "not field=value"):
            parseArgs(["SK-0047", "done"])

    def test_log_without_text_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "--log needs"):
            parseArgs(["SK-0047", "--log"])


class UpdateTicketTests(StoreCase):
    def test_changes_both_fields_adds_the_log_line_reindexes_and_commits_nothing(self):
        before = head(self.store)
        self.staleIndex()
        path = updateTicket(self.store, KEY, {"milestone": "plan-start", "ordinal": "160"}, "x")
        self.assertEqual(path, self.ticket.resolve())
        front = self.front()
        self.assertEqual((front["milestone"], front["ordinal"]), ("plan-start", "160"))
        self.assertEqual(self.log()[-1], {"date": TODAY, "text": "x"})
        self.assertEqual(self.index.read_text(), f"- {FILE_NAME}\n- {OTHER_KEY}-other.md\n")
        self.assertEqual(head(self.store), before)

    def test_a_bad_status_refuses_naming_status_and_changes_nothing(self):
        original = self.ticket.read_text()
        self.staleIndex()
        with self.assertRaisesRegex(RuntimeError, "status"):
            updateTicket(self.store, KEY, {"status": "bogus", "ordinal": "1"}, "x")
        self.assertEqual(self.ticket.read_text(), original)
        self.assertEqual(self.index.read_text(), "stale\n")

    def test_an_unknown_field_refuses_naming_it_and_changes_nothing(self):
        original = self.ticket.read_text()
        with self.assertRaisesRegex(RuntimeError, "colour"):
            updateTicket(self.store, KEY, {"ordinal": "1", "colour": "red"})
        self.assertEqual(self.ticket.read_text(), original)

    def test_a_milestone_without_a_file_refuses_naming_milestone(self):
        original = self.ticket.read_text()
        with self.assertRaisesRegex(RuntimeError, "milestone"):
            updateTicket(self.store, KEY, {"milestone": "nowhere"})
        self.assertEqual(self.ticket.read_text(), original)

    def test_a_log_alone_still_regenerates_index(self):
        self.staleIndex()
        updateTicket(self.store, KEY, {}, "only a log")
        self.assertEqual(self.index.read_text(), f"- {FILE_NAME}\n- {OTHER_KEY}-other.md\n")
        self.assertEqual(self.log()[-1]["text"], "only a log")

    def test_status_done_sets_closed_today(self):
        updateTicket(self.store, KEY, {"status": "done"})
        self.assertEqual((self.front()["status"], self.front()["closed"]), ("done", TODAY))
        self.assertNotIn(FILE_NAME, self.index.read_text())

    def test_status_done_keeps_a_closed_the_caller_gave(self):
        updateTicket(self.store, KEY, {"status": "done", "closed": "2026-01-02"})
        self.assertEqual(self.front()["closed"], "2026-01-02")

    def test_a_field_the_ticket_lacks_is_added_to_its_front_matter(self):
        updateTicket(self.store, KEY, {"pr": "https://github.com/a/b/pull/1"})
        self.assertEqual(self.front()["pr"], "https://github.com/a/b/pull/1")
        self.assertTrue(self.ticket.read_text().startswith("---\n"))
        self.assertIn("# A ticket", self.ticket.read_text())

    def test_a_blank_value_clears_a_field_without_trailing_space(self):
        updateTicket(self.store, KEY, {"ordinal": ""})
        self.assertIn("\nordinal:\n", self.ticket.read_text())

    def test_a_long_log_wraps_at_the_house_width_and_reads_back_whole(self):
        text = " ".join(["word"] * 60)
        updateTicket(self.store, KEY, {}, text)
        lines = self.ticket.read_text().rstrip("\n").split("\n")
        entry = lines[lines.index("- 2026-11-09: Filed.") + 1 :]
        self.assertGreater(len(entry), 1)
        self.assertTrue(all(len(line) <= LOG_WIDTH for line in entry))
        self.assertTrue(all(line.startswith("  ") for line in entry[1:]))
        self.assertEqual(self.log()[-1]["text"], text)

    def test_a_jira_key_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "SK tickets only"):
            updateTicket(self.store, "CIAM-5", {"ordinal": "1"})

    def test_an_unknown_ticket_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "no issue file for SK-0999"):
            updateTicket(self.store, "SK-0999", {"ordinal": "1"})

    def test_a_store_without_the_index_script_is_refused_before_any_write(self):
        original = self.ticket.read_text()
        (self.store / "scripts" / "index.py").unlink()
        with self.assertRaisesRegex(RuntimeError, "index.py"):
            updateTicket(self.store, KEY, {"ordinal": "1"})
        self.assertEqual(self.ticket.read_text(), original)

    def test_the_padding_may_be_left_off_the_key(self):
        updateTicket(self.store, "SK-47", {"ordinal": "7"})
        self.assertEqual(self.front()["ordinal"], "7")


class CommandLineTests(StoreCase):
    def run_bin(self, *args):
        home = Path(self.root) / "home"
        (home / ".config" / "skuld").mkdir(parents=True, exist_ok=True)
        (home / ".config" / "skuld" / "config.json").write_text(json.dumps({"issueStore": str(self.store)}))
        env = {**os.environ, "HOME": str(home)}
        return subprocess.run([sys.executable, str(BIN), *args], env=env, capture_output=True, text=True)

    def test_prints_the_tickets_path_and_changes_it(self):
        result = self.run_bin(KEY, "ordinal=160", "--log", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.ticket.resolve()))
        self.assertEqual(self.front()["ordinal"], "160")

    def test_a_bad_field_exits_nonzero_naming_it_and_changes_nothing(self):
        original = self.ticket.read_text()
        result = self.run_bin(KEY, "status=bogus", "ordinal=1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status", result.stderr)
        self.assertEqual(self.ticket.read_text(), original)

    def test_no_arguments_prints_usage_and_exits_nonzero(self):
        result = self.run_bin()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage: issue_update", result.stderr)
