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

from lib.fileissues import LOG_WIDTH, _parse
from lib.issue_update import parseArgs, updateTicket
from lib.store_index import indexIsCurrent
from lib.store_sandbox import FILE_NAME, KEY, OTHER_KEY, TODAY, freezeToday, git, head, makeStore

BIN = Path(__file__).resolve().parent.parent / "bin" / "issue_update"


class StoreCase(unittest.TestCase):
    def setUp(self):
        freezeToday(self)
        self.root = tempfile.mkdtemp(prefix="skuld-issue-update-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = makeStore(self.root)
        self.ticket = self.store / "active" / FILE_NAME
        self.index = self.store / "INDEX.md"

    def front(self):
        return _parse(self.ticket.read_text())[0]

    def log(self):
        return _parse(self.ticket.read_text())[3]

    def staleIndex(self):
        self.index.write_text("stale\n")

    def bytesOfEverything(self):
        return {p.name: p.read_bytes() for p in self.store.iterdir() if p.is_file()}

    def assertRefusedChangingNothing(self, pattern, fields, log=None):
        before = self.bytesOfEverything()
        self.staleIndex()
        stale = self.index.read_bytes()
        before["INDEX.md"] = stale
        with self.assertRaisesRegex(RuntimeError, pattern):
            updateTicket(self.store, KEY, fields, log)
        self.assertEqual(self.bytesOfEverything(), before)


class ParseArgsTests(unittest.TestCase):
    def test_key_fields_and_log(self):
        self.assertEqual(
            parseArgs(["SK-0047", "status=done", "ordinal=5", "--log", "x y"]),
            ("SK-0047", {"status": "done", "ordinal": "5"}, "x y"),
        )

    def test_log_equals_text_is_the_log(self):
        self.assertEqual(parseArgs(["SK-0047", "--log=x y"])[2], "x y")

    def test_an_empty_or_blank_log_is_refused(self):
        for text in ([""], ["  "]):
            with self.assertRaisesRegex(RuntimeError, "--log needs"):
                parseArgs(["SK-0047", "--log", *text])
        with self.assertRaisesRegex(RuntimeError, "--log needs"):
            parseArgs(["SK-0047", "--log="])

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
        path, written = updateTicket(
            self.store, KEY, {"milestone": "plan-start", "ordinal": "160", "points": "3"}, "x"
        )
        self.assertEqual(path, self.ticket.resolve())
        self.assertEqual(written, {"milestone": "plan-start", "ordinal": "160", "points": "3"})
        front = self.front()
        self.assertEqual((front["milestone"], front["ordinal"], front["points"]), ("plan-start", "160", "3"))
        self.assertEqual(self.log()[-1], {"date": TODAY, "text": "x"})
        self.assertTrue(indexIsCurrent(self.store))
        self.assertEqual(head(self.store), before)
        self.assertEqual(git(self.store, "diff", "--cached", "--name-only"), "")
        self.assertEqual(
            sorted(line[3:] for line in git(self.store, "status", "--porcelain").splitlines()), ["INDEX.md", f"active/{FILE_NAME}"]
        )

    def test_a_bad_value_refuses_naming_its_field_and_changes_nothing(self):
        bad = {
            "status": "bogus",
            "points": "4",
            "ordinal": "abc",
            "blocked_by": "SK-9999",
            "parent": "SK-9999",
            "milestone": "nowhere",
            "closed": "2026-13-45",
            "opened": "yesterday",
            "colour": "red",
        }
        for field, value in bad.items():
            with self.subTest(field=field):
                self.assertRefusedChangingNothing(field, {"ordinal": "1", field: value}, "x")

    def test_an_open_ticket_is_refused_a_closed_milestone_and_a_closed_one_is_not(self):
        (self.store / "milestones" / "shipped.md").write_text("---\nid: shipped\nname: Shipped\nstatus: closed\n---\n")
        self.assertRefusedChangingNothing("closed", {"milestone": "shipped"}, "x")
        updateTicket(self.store, KEY, {"milestone": "shipped", "status": "done"})
        self.assertEqual(self.front()["milestone"], "shipped")

    def test_a_blocker_and_a_parent_that_are_tickets_in_the_store_are_taken(self):
        updateTicket(self.store, KEY, {"blocked_by": OTHER_KEY, "parent": OTHER_KEY, "host": "work-mac"})
        front = self.front()
        self.assertEqual((front["blocked_by"], front["parent"], front["host"]), (OTHER_KEY, OTHER_KEY, "work-mac"))

    def test_a_ticket_in_archive_counts_as_in_the_store(self):
        (self.store / "archive").mkdir()
        (self.store / "archive" / "SK-0002-old.md").write_text("---\nstatus: done\n---\n# Old\n")
        updateTicket(self.store, KEY, {"blocked_by": f"SK-0002, {OTHER_KEY}"})
        self.assertEqual(self.front()["blocked_by"], f"SK-0002, {OTHER_KEY}")

    def test_a_log_alone_still_regenerates_index(self):
        self.staleIndex()
        updateTicket(self.store, KEY, {}, "only a log")
        self.assertTrue(indexIsCurrent(self.store))
        self.assertEqual(self.log()[-1]["text"], "only a log")

    def test_status_done_sets_closed_today(self):
        _path, written = updateTicket(self.store, KEY, {"status": "done"})
        self.assertEqual(written, {"status": "done", "closed": TODAY})
        self.assertEqual((self.front()["status"], self.front()["closed"]), ("done", TODAY))
        self.assertTrue(indexIsCurrent(self.store))
        self.assertNotIn(f"[{KEY}]", self.index.read_text())

    def test_wontfix_sets_closed_today_too(self):
        updateTicket(self.store, KEY, {"status": "wontfix"})
        self.assertEqual(self.front()["closed"], TODAY)

    def test_an_empty_closed_with_a_closing_status_gets_today(self):
        updateTicket(self.store, KEY, {"status": "done", "closed": ""})
        self.assertEqual(self.front()["closed"], TODAY)

    def test_status_done_keeps_a_closed_the_caller_gave(self):
        updateTicket(self.store, KEY, {"status": "done", "closed": "2026-01-02"})
        self.assertEqual(self.front()["closed"], "2026-01-02")

    def test_an_open_status_clears_closed(self):
        updateTicket(self.store, KEY, {"status": "done"})
        updateTicket(self.store, KEY, {"status": "in-progress"})
        self.assertEqual((self.front()["status"], self.front()["closed"]), ("in-progress", ""))
        self.assertIn(f"[{KEY}]", self.index.read_text())

    def test_closed_with_an_open_status_is_refused(self):
        self.assertRefusedChangingNothing("closed", {"closed": "2026-01-02"})

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

    def test_a_wrapped_entry_with_a_spaced_dash_at_the_wrap_reads_back_whole(self):
        text = "word " * 17 + "x - then the rest of it"
        updateTicket(self.store, KEY, {}, text)
        self.assertEqual(self.log()[-1]["text"], text.strip())
        self.assertTrue(all(len(line) <= LOG_WIDTH for line in self.ticket.read_text().splitlines()))

    def test_a_value_with_a_newline_is_refused_naming_its_field(self):
        self.assertRefusedChangingNothing("pr", {"pr": "x\nstatus: done"})

    def test_a_jira_key_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "SK tickets only"):
            updateTicket(self.store, "CIAM-5", {"ordinal": "1"})

    def test_a_log_inside_a_code_fence_is_not_the_log(self):
        fenced = self.ticket.read_text().replace(
            "## Done", "```\n## Log\n```\n\n## Done"
        )
        self.ticket.write_text(fenced)
        updateTicket(self.store, KEY, {}, "new")
        self.assertEqual(self.ticket.read_text(), fenced.rstrip("\n") + f"\n- {TODAY}: new\n")
        self.assertEqual([e["text"] for e in self.log()], ["Filed.", "new"])

    def test_a_log_heading_only_in_a_code_fence_gets_a_real_log(self):
        text = self.ticket.read_text().replace("## Log\n\n- 2026-11-09: Filed.\n", "```\n## Log\n```\n")
        self.ticket.write_text(text)
        updateTicket(self.store, KEY, {}, "new")
        self.assertEqual([e["text"] for e in self.log()], ["new"])

    def test_an_indented_sub_bullet_reads_back_apart_from_the_entry_above(self):
        self.ticket.write_text(self.ticket.read_text() + "  - a detail\n")
        self.assertEqual(self.log(), [{"date": "2026-11-09", "text": "Filed."}])

    def test_an_unknown_ticket_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "no issue file for SK-0999"):
            updateTicket(self.store, "SK-0999", {"ordinal": "1"})

    def test_an_index_that_cant_be_written_says_what_changed_and_the_error(self):
        (self.store / "milestones" / "broken.md").write_text("---\nstatus: open\n---\n")
        with self.assertRaisesRegex(RuntimeError, r"changed \(ordinal, a log line\).*KeyError.*store-index"):
            updateTicket(self.store, KEY, {"ordinal": "1"}, "x")
        self.assertEqual(self.front()["ordinal"], "1")

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
        self.assertEqual(result.stdout.splitlines()[0], str(self.ticket.resolve()))
        self.assertEqual(self.front()["ordinal"], "160")

    def test_prints_the_path_then_each_field_written_and_the_log(self):
        result = self.run_bin(KEY, "status=done", "--log", "x")
        self.assertEqual(
            result.stdout.splitlines(),
            [str(self.ticket.resolve()), "status: done", f"closed: {date.today().isoformat()}", "log: added"],
        )

    def test_help_prints_the_usage_fields_and_statuses(self):
        result = self.run_bin("--help")
        self.assertEqual(result.returncode, 0)
        for text in ("usage: issue_update", "points", "host", "parent", "wontfix"):
            self.assertIn(text, result.stdout)

    def test_a_jira_key_is_refused(self):
        result = self.run_bin("CIAM-1", "--log", "x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a store ticket", result.stderr)

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
