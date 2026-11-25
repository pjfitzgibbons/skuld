# lib/issue_update.py: issue-create and issue-update against a temporary store, no network, no
# `plan`, and no store-index (reindex is a stub).
import contextlib
import importlib.util
import io
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from lib import issue_update
from lib.fileissues import _parse
from lib.issue_update import LOG_WIDTH, closeProblems, createMain, createTicket, setField, updateMain, updateTicket

OUTLINE_PY = Path(__file__).parent.parent / "skills" / "jira-issues" / "scripts" / "outline.py"

TICKET = """---
ticket_id: SK-0001
status: open
type: task
opened: 2026-11-01
closed:
---

# A ticket

## Task

Do it.

## Log

- 2026-11-01: Filed.
"""

NO_TICKET_ID = TICKET.replace("ticket_id: SK-0001\n", "")
NO_LOG = TICKET.split("## Log")[0]


def noIndex(store):
    pass


def outlineModule():
    spec = importlib.util.spec_from_file_location("outline", OUTLINE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.store = Path(tempfile.mkdtemp(prefix="issue-update-"))
        self.addCleanup(shutil.rmtree, self.store, ignore_errors=True)
        (self.store / "active").mkdir()
        (self.store / "archive").mkdir()

    def write(self, name, text, folder="active"):
        path = self.store / folder / name
        path.write_text(text, encoding="utf8")
        return path

    def update(self, key, fields=None, log=None):
        return updateTicket(self.store, key, fields or {}, log, reindex=noIndex)


class UpdateTests(StoreCase):
    def test_status_changes_that_line_and_nothing_else(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        self.update("SK-0001", {"status": "in-progress"})
        before, after = TICKET.splitlines(), path.read_text(encoding="utf8").splitlines()
        changed = [(b, a) for b, a in zip(before, after) if b != a]
        self.assertEqual(changed, [("status: open", "status: in-progress")])
        self.assertEqual(len(before), len(after))

    def test_returns_the_path_written_and_finds_a_ticket_in_archive(self):
        path = self.write("SK-0001-a-ticket.md", TICKET, folder="archive")
        self.assertEqual(self.update("SK-1", {"status": "done"}), path)

    def test_log_appends_a_dated_line_under_the_log(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        self.update("SK-0001", log="Built it.")
        text = path.read_text(encoding="utf8")
        self.assertTrue(text.endswith(f"- 2026-11-01: Filed.\n- {date.today().isoformat()}: Built it.\n"))

    def test_log_adds_the_heading_when_the_file_has_none(self):
        path = self.write("SK-0001-a-ticket.md", NO_LOG)
        self.update("SK-0001", log="First.")
        text = path.read_text(encoding="utf8")
        self.assertEqual(text.count("## Log"), 1)
        self.assertTrue(text.endswith(f"## Log\n- {date.today().isoformat()}: First.\n"))

    def test_unknown_field_refuses_names_it_and_leaves_the_file_unchanged(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "nosuchfield"):
            self.update("SK-0001", {"status": "done", "nosuchfield": "x"})
        self.assertEqual(path.read_bytes(), before)

    def test_ticket_id_is_not_an_updatable_field(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "ticket_id"):
            self.update("SK-0001", {"ticket_id": "SK-0002"})
        self.assertEqual(path.read_bytes(), before)

    def test_bad_status_and_multiline_value_refuse_naming_the_field(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'field "status": "bogus"'):
            self.update("SK-0001", {"status": "bogus"})
        with self.assertRaisesRegex(RuntimeError, 'field "pr": the value is more than one line'):
            self.update("SK-0001", {"pr": "a\nb"})
        self.assertEqual(path.read_bytes(), before)

    def test_ordinal_type_and_date_values_are_checked_and_refuse_naming_the_field(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        for field, value in (("ordinal", "abc"), ("type", "epic"), ("opened", "yesterday"), ("closed", "2026-13-40")):
            with self.assertRaisesRegex(RuntimeError, f'field "{field}"'):
                self.update("SK-0001", {field: value})
        self.assertEqual(path.read_bytes(), before)

    def test_good_values_and_empty_values_are_taken(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        self.update("SK-0001", {"ordinal": "250", "type": "bug", "opened": "2026-11-02", "closed": ""})
        front = _parse(path.read_text(encoding="utf8"))[0]
        self.assertEqual((front["ordinal"], front["type"], front["opened"]), ("250", "bug", "2026-11-02"))

    def test_a_value_with_a_space_hash_refuses_because_a_reader_would_cut_it(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'field "pr"'):
            self.update("SK-0001", {"pr": "https://example.test/pull/1 #2"})
        self.assertEqual(path.read_bytes(), before)

    def test_a_multiline_log_refuses(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "--log"):
            self.update("SK-0001", log="one\ntwo")
        self.assertEqual(path.read_bytes(), before)

    def test_a_jira_key_refuses_naming_the_key(self):
        with self.assertRaisesRegex(RuntimeError, "CIAM-1"):
            self.update("CIAM-1", {"status": "done"})

    def test_an_unknown_ticket_refuses_naming_the_key(self):
        with self.assertRaisesRegex(RuntimeError, "SK-0099"):
            self.update("SK-0099", {"status": "done"})

    def test_a_file_without_ticket_id_updates_without_gaining_one(self):
        path = self.write("SK-0001-a-ticket.md", NO_TICKET_ID)
        self.update("SK-0001", {"status": "in-progress"}, log="x")
        self.assertNotIn("ticket_id", path.read_text(encoding="utf8"))
        self.assertEqual(_parse(path.read_text(encoding="utf8"))[0]["status"], "in-progress")

    def test_a_file_with_ticket_id_keeps_it_first(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        self.update("SK-0001", {"status": "done", "closed": "2026-11-23"})
        self.assertEqual(list(_parse(path.read_text(encoding="utf8"))[0])[0], "ticket_id")

    def test_a_known_field_the_file_lacks_is_added_inside_the_front_matter(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        self.update("SK-0001", {"pr": "https://example.test/pull/1"})
        front = _parse(path.read_text(encoding="utf8"))[0]
        self.assertEqual(front["pr"], "https://example.test/pull/1")
        self.assertIn("pr: https://example.test/pull/1\n---\n", path.read_text(encoding="utf8"))

    def test_an_empty_value_clears_the_field(self):
        path = self.write("SK-0001-a-ticket.md", TICKET.replace("closed:", "closed: 2026-11-02"))
        self.update("SK-0001", {"closed": ""})
        self.assertIn("\nclosed:\n", path.read_text(encoding="utf8"))

    def test_an_interrupted_write_leaves_the_original_intact_and_no_temp_file(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        with mock.patch("lib.issue_update.os.replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                self.update("SK-0001", {"status": "done"}, log="x")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.store / "active")), ["SK-0001-a-ticket.md"])

    def test_a_failure_while_writing_the_text_leaves_the_original_intact(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        with mock.patch("lib.issue_update.os.fsync", side_effect=OSError("no space")):
            with self.assertRaises(OSError):
                self.update("SK-0001", {"status": "done"})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.store / "active")), ["SK-0001-a-ticket.md"])

    def test_the_store_is_reindexed_after_the_write(self):
        self.write("SK-0001-a-ticket.md", TICKET)
        seen = []
        updateTicket(self.store, "SK-0001", {"status": "done"}, reindex=seen.append)
        self.assertEqual(seen, [self.store])

    def test_reindex_says_so_on_stderr_when_store_index_is_missing(self):
        err = io.StringIO()
        with mock.patch("lib.issue_update.shutil.which", return_value=None), contextlib.redirect_stderr(err):
            issue_update.reindexStore(self.store)
        self.assertIn("store-index", err.getvalue())


    def test_a_ticket_at_the_stores_top_level_refuses_and_nothing_is_written(self):
        stray = self.store / "SK-0001-stray.md"
        stray.write_text(TICKET, encoding="utf8")
        with self.assertRaisesRegex(RuntimeError, r"SK-0001-stray\.md.*active/"):
            self.update("SK-0001", {"status": "done"})
        self.assertEqual(stray.read_text(encoding="utf8"), TICKET)
        self.assertEqual(os.listdir(self.store / "active"), [])


class CreateTests(StoreCase):
    def create(self, title="Make a thing", fields=None):
        return createTicket(self.store, title, fields, reindex=noIndex)

    def test_writes_the_next_free_key_ordinal_and_ticket_id_first(self):
        self.write("SK-0007-old.md", TICKET.replace("closed:", "closed:\nordinal: 165"), folder="archive")
        path = self.create()
        self.assertEqual(path, self.store / "active" / "SK-0008-make-a-thing.md")
        text = path.read_text(encoding="utf8")
        self.assertTrue(text.startswith("---\nticket_id: SK-0008\n"))
        front = _parse(text)[0]
        self.assertEqual((front["status"], front["ordinal"]), ("open", "200"))

    def test_the_first_ticket_is_one(self):
        self.assertEqual(self.create().name, "SK-0001-make-a-thing.md")

    def test_a_second_create_takes_the_next_key(self):
        self.create()
        self.assertEqual(self.create("Another").name, "SK-0002-another.md")

    def test_log_has_the_filed_line_and_the_four_sections(self):
        text = self.create().read_text(encoding="utf8")
        self.assertIn(f"## Log\n\n- {date.today().isoformat()}: Filed.\n", text)
        for section in ("## Task", "## Guardrails", "## Lenses", "## Done"):
            self.assertIn(section, text)

    def test_the_new_file_passes_outline_py(self):
        body = self.create().read_text(encoding="utf8")
        lenses = [f"L{n}" for n in range(1, 11)]
        self.assertEqual(outlineModule().check(body, lenses, []), [])

    def test_fields_given_replace_the_defaults(self):
        text = self.create(fields={"type": "bug", "points": "3"}).read_text(encoding="utf8")
        front = _parse(text)[0]
        self.assertEqual((front["type"], front["points"]), ("bug", "3"))

    def test_an_unknown_field_refuses_naming_it_and_writes_nothing(self):
        with self.assertRaisesRegex(RuntimeError, "nosuchfield"):
            self.create(fields={"nosuchfield": "x"})
        self.assertEqual(os.listdir(self.store / "active"), [])

    def test_the_ticket_is_filled_from_templates_issue_md(self):
        template = Path(issue_update.TEMPLATE)
        self.assertEqual(template.name, "issue.md")
        text = self.create().read_text(encoding="utf8")
        for lens in range(1, 11):
            self.assertIn(f"- [ ] L{lens} ", text)
        self.assertIn("ticket_id: SK-0001\nstatus: open\n", text)

    def test_a_field_the_template_lacks_is_added_to_the_front_matter(self):
        front = _parse(self.create(fields={"after": "CIAM-163"}).read_text(encoding="utf8"))[0]
        self.assertEqual(front["after"], "CIAM-163")

    def test_a_bad_ordinal_refuses_on_create_and_writes_nothing(self):
        with self.assertRaisesRegex(RuntimeError, 'field "ordinal"'):
            self.create(fields={"ordinal": "abc"})
        self.assertEqual(os.listdir(self.store / "active"), [])

    def test_a_stray_sk_file_that_is_not_key_shaped_is_skipped(self):
        (self.store / "active" / "SK-notes.md").write_text("notes", encoding="utf8")
        self.assertEqual(self.create().name, "SK-0001-make-a-thing.md")

    def test_an_ordinal_line_in_the_body_is_not_the_ordinal(self):
        self.write("SK-0003-old.md", TICKET + "\nordinal: 9000\n")
        self.assertEqual(_parse(self.create().read_text(encoding="utf8"))[0]["ordinal"], "100")

    def test_two_creates_at_once_never_take_the_same_key(self):
        slow = issue_update.nextOrdinal

        def slowOrdinal(store):
            result = slow(store)
            time.sleep(0.2)  # the window between picking a key and writing the file
            return result

        paths = []
        with mock.patch("lib.issue_update.nextOrdinal", slowOrdinal):
            threads = [threading.Thread(target=lambda n=n: paths.append(self.create(f"Ticket {n}"))) for n in "ab"]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        keys = sorted(p.name.split("-")[1] for p in paths)
        self.assertEqual(keys, ["0001", "0002"])
        for key in ("SK-0001", "SK-0002"):
            issue_update.ticketPath(self.store, key)

    def test_a_title_that_is_not_one_line_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "title"):
            self.create("two\nlines")

    def test_an_existing_file_is_never_overwritten(self):
        taken = self.store / "active" / "SK-0001-make-a-thing.md"
        with mock.patch("lib.issue_update.nextKey", return_value="SK-0001"):
            self.create()
            before = taken.read_bytes()
            with self.assertRaises(FileExistsError):
                self.create()
        self.assertEqual(taken.read_bytes(), before)
        self.assertEqual(os.listdir(self.store / "active"), ["SK-0001-make-a-thing.md"])

    def test_a_ticket_at_the_stores_top_level_refuses_and_nothing_is_written(self):
        stray = self.store / "SK-0001-stray.md"
        stray.write_text(TICKET, encoding="utf8")
        with self.assertRaisesRegex(RuntimeError, r"SK-0001-stray\.md.*active/"):
            self.create()
        self.assertEqual(os.listdir(self.store / "active"), [])
        self.assertEqual(sorted(os.listdir(self.store)), ["SK-0001-stray.md", "active", "archive"])

    def test_a_store_without_active_gets_the_folder_and_the_ticket_is_readable(self):
        shutil.rmtree(self.store / "active")
        path = self.create()
        self.assertEqual(path.parent, self.store / "active")
        self.assertEqual(issue_update.ticketPath(self.store, "SK-0001"), path)
        self.assertEqual(self.create().name, "SK-0002-make-a-thing.md")


class ReviewRoundTests(StoreCase):
    def test_a_tab_before_a_hash_refuses_because_the_reader_cuts_there_too(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'field "pr"'):
            self.update("SK-0001", {"pr": "https://example.test/pull/1\t#note"})
        self.assertEqual(path.read_bytes(), before)

    def test_blocked_by_set_directly_refuses_the_same_ticket_spelled_two_ways(self):
        self.write("SK-0002-other.md", NO_TICKET_ID.replace("SK-0001", "SK-0002"))
        path = self.write("SK-0001-a-ticket.md", TICKET)
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'field "blocked_by".*twice'):
            self.update("SK-0001", {"blocked_by": "SK-2, SK-0002"})
        self.assertEqual(path.read_bytes(), before)

    def test_blocked_by_set_directly_is_written_with_padded_keys(self):
        self.write("SK-0002-other.md", TICKET)
        path = self.write("SK-0001-a-ticket.md", TICKET)
        self.update("SK-0001", {"blocked_by": "SK-2"})
        self.assertEqual(_parse(path.read_text(encoding="utf8"))[0]["blocked_by"], "SK-0002")

    def blockedTicket(self):
        self.write("SK-0002-other.md", TICKET)
        self.write("SK-0003-third.md", TICKET)
        return self.write("SK-0001-a-ticket.md", TICKET.replace("closed:", "closed:\nblocked_by: SK-0002"))

    def test_blocker_edits_compare_tickets_not_spellings(self):
        path = self.blockedTicket()
        before = path.read_bytes()
        for spelled, word in (("SK-1", "itself"), ("SK-2", "already")):
            with self.subTest(add=spelled), self.assertRaisesRegex(RuntimeError, word):
                updateTicket(self.store, "SK-0001", {}, edits=[("blocked_by", "+", spelled)], reindex=noIndex)
            self.assertEqual(path.read_bytes(), before)
        updateTicket(self.store, "SK-0001", {}, edits=[("blocked_by", "-", "SK-2")], reindex=noIndex)
        self.assertEqual(_parse(path.read_text(encoding="utf8"))[0]["blocked_by"], "")

    def test_a_blocker_added_by_a_short_key_is_written_padded(self):
        path = self.blockedTicket()
        updateTicket(self.store, "SK-0001", {}, edits=[("blocked_by", "+", "SK-3")], reindex=noIndex)
        self.assertEqual(_parse(path.read_text(encoding="utf8"))[0]["blocked_by"], "SK-0002, SK-0003")

    def test_an_indexer_that_cannot_be_launched_is_a_warning_not_a_failure(self):
        err = io.StringIO()
        with (
            mock.patch("lib.issue_update.shutil.which", return_value="/gone/store-index"),
            mock.patch("lib.issue_update.subprocess.run", side_effect=OSError("bad interpreter")),
            contextlib.redirect_stderr(err),
        ):
            issue_update.reindexStore(self.store)
        self.assertIn("bad interpreter", err.getvalue())
        self.assertIn("store-index", err.getvalue())

    def stubChecker(self, body):
        script = self.store / "stub_outline.py"
        script.write_text(body, encoding="utf8")
        return mock.patch("lib.issue_update.OUTLINE_PY", script)

    def test_the_outline_check_opens_a_ticket_named_by_a_relative_path(self):
        self.write("SK-0001-a-ticket.md", TICKET)
        stub = "import sys\nprint('read ' + open(sys.argv[1]).readline().strip())\nsys.exit(1)\n"
        relative = Path("active") / "SK-0001-a-ticket.md"
        cwd = os.getcwd()
        os.chdir(self.store)
        self.addCleanup(os.chdir, cwd)
        with self.stubChecker(stub):
            self.assertEqual(issue_update.outlineProblems(relative), ["read ---"])

    def test_a_checker_that_fails_without_findings_reports_its_error(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        stub = "import sys\nsys.stderr.write('boom: cannot start\\n')\nsys.exit(2)\n"
        with self.stubChecker(stub):
            self.assertEqual(issue_update.outlineProblems(path), ["the outline check failed: boom: cannot start"])


class ClosedFollowsStatusTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.path = self.write("SK-0001-a-ticket.md", TICKET)
        self.today = date.today().isoformat()

    def front(self):
        return _parse(self.path.read_text(encoding="utf8"))[0]

    def addMilestone(self, name, status):
        (self.store / "milestones").mkdir(exist_ok=True)
        (self.store / "milestones" / f"{name}.md").write_text(f"---\nid: {name}\nstatus: {status}\n---\n", encoding="utf8")

    def assertRefusedChangingNothing(self, pattern, fields):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, pattern):
            self.update("SK-0001", fields)
        self.assertEqual(self.path.read_bytes(), before)

    def test_status_done_sets_closed_today(self):
        self.update("SK-0001", {"status": "done"})
        self.assertEqual((self.front()["status"], self.front()["closed"]), ("done", self.today))

    def test_wontfix_sets_closed_today_too(self):
        self.update("SK-0001", {"status": "wontfix"})
        self.assertEqual(self.front()["closed"], self.today)

    def test_an_empty_closed_with_a_closing_status_gets_today(self):
        self.update("SK-0001", {"status": "done", "closed": ""})
        self.assertEqual(self.front()["closed"], self.today)

    def test_closed_follows_status_for_every_combination(self):
        today, given, kept = self.today, "2026-01-02", "2026-11-03"
        refused = "closed"
        noClosed = ("open", "")
        # (status given, closed given, ticket before) -> (status, closed) written, or the refusal
        rows = [
            # an open status
            ("in-progress", given, "open", refused),
            ("in-progress", given, "done", refused),
            ("in-progress", "", "open", ("in-progress", "")),
            ("in-progress", "", "done", ("in-progress", "")),
            ("in-progress", None, "open", ("in-progress", "")),
            ("in-progress", None, "done", ("in-progress", "")),
            # a closing status
            ("done", given, "open", ("done", given)),
            ("done", given, "done", ("done", given)),
            ("done", "", "open", ("done", today)),
            ("done", "", "done", ("done", today)),
            ("done", None, "open", ("done", today)),
            ("done", None, "done", ("done", kept)),
            # no status given: closed is judged against the ticket's own status
            (None, given, "open", refused),
            (None, given, "done", ("done", given)),
            (None, "", "open", noClosed),
            (None, "", "done", "closed ticket needs the date"),
            (None, None, "open", noClosed),
            (None, None, "done", ("done", kept)),
        ]
        before = {"open": TICKET, "done": TICKET.replace("status: open", "status: done").replace("closed:", f"closed: {kept}")}
        for status, closed, existing, expected in rows:
            with self.subTest(status=status, closed=closed, existing=existing):
                self.path.write_text(before[existing], encoding="utf8")
                fields = {name: value for name, value in (("status", status), ("closed", closed)) if value is not None}
                if isinstance(expected, str):
                    self.assertRefusedChangingNothing(expected, fields)
                else:
                    self.update("SK-0001", fields)
                    self.assertEqual((self.front()["status"], self.front()["closed"]), expected)

    def test_status_done_keeps_a_closed_the_caller_gave(self):
        self.update("SK-0001", {"status": "done", "closed": "2026-01-02"})
        self.assertEqual(self.front()["closed"], "2026-01-02")

    def test_an_open_status_clears_closed(self):
        self.update("SK-0001", {"status": "done"})
        self.update("SK-0001", {"status": "in-progress"})
        self.assertEqual((self.front()["status"], self.front()["closed"]), ("in-progress", ""))

    def test_closed_with_an_open_status_is_refused(self):
        self.assertRefusedChangingNothing("closed", {"closed": "2026-01-02"})

    def test_an_open_ticket_is_refused_a_closed_milestone_and_a_closed_one_is_not(self):
        self.addMilestone("shipped", "closed")
        self.assertRefusedChangingNothing('milestone: "shipped" is closed', {"milestone": "shipped"})
        self.update("SK-0001", {"milestone": "shipped", "status": "done"})
        self.assertEqual(self.front()["milestone"], "shipped")

    def test_a_log_line_alone_does_not_re_judge_a_ticket_with_a_closed_milestone(self):
        self.addMilestone("shipped", "closed")
        self.path.write_text(self.path.read_text(encoding="utf8").replace("closed:\n", "closed:\nmilestone: shipped\n"), encoding="utf8")
        self.update("SK-0001", log="noted")
        self.assertIn("noted", self.path.read_text(encoding="utf8"))

    def test_a_blocker_edit_re_judges_the_ticket_and_a_log_line_alone_does_not(self):
        self.addMilestone("shipped", "closed")
        self.write("SK-0002-other.md", TICKET.replace("ticket_id: SK-0001", "ticket_id: SK-0002"))
        self.path.write_text(self.path.read_text(encoding="utf8").replace("closed:\n", "closed:\nmilestone: shipped\n"), encoding="utf8")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'milestone: "shipped" is closed'):
            updateTicket(self.store, "SK-0001", {}, edits=[("blocked_by", "+", "SK-0002")], reindex=noIndex)
        self.assertEqual(self.path.read_bytes(), before)
        self.update("SK-0001", log="noted")
        self.assertIn("noted", self.path.read_text(encoding="utf8"))

    def test_a_new_ticket_is_refused_a_closed_milestone(self):
        self.addMilestone("shipped", "closed")
        with self.assertRaisesRegex(RuntimeError, "shipped"):
            createTicket(self.store, "New one", {"milestone": "shipped"}, reindex=noIndex)

    def test_story_points_are_fibonacci(self):
        self.assertRefusedChangingNothing('field "points"', {"points": "4"})
        self.update("SK-0001", {"points": "5"})
        self.assertEqual(self.front()["points"], "5")


class CloseProblemsTests(StoreCase):
    DONE_TICKET = TICKET.replace("## Log", "## Done\n\n- [x] One.\n{more}\n## Log")

    def problems(self, text):
        return closeProblems(self.store, text)

    def test_a_ticked_ticket_with_valid_fields_has_none(self):
        self.assertEqual(self.problems(self.DONE_TICKET.format(more="")), [])

    def test_an_unticked_box_is_named(self):
        found = self.problems(self.DONE_TICKET.format(more="- [ ] Two.\n"))
        self.assertEqual(found, ['the Done box "Two." is not ticked'])

    def test_a_field_the_checker_rejects_is_named(self):
        found = self.problems(self.DONE_TICKET.format(more="").replace("closed:\n", "closed:\nblocked_by: SK-9999\n"))
        self.assertEqual(len(found), 1)
        self.assertIn('field "blocked_by"', found[0])

    def test_a_ticket_already_closed_with_its_date_has_none(self):
        text = self.DONE_TICKET.format(more="- [ ] Two.\n").replace("status: open", "status: done").replace("closed:\n", "closed: 2026-11-02\n")
        self.assertEqual(self.problems(text), [])


class FrontMatterAndLogTests(StoreCase):
    def test_setField_adds_a_field_the_block_lacks_and_replaces_one_it_has(self):
        self.assertIn("closed:\npr: https://example.test/pull/1\n---\n", setField(TICKET, "pr", "https://example.test/pull/1"))
        self.assertIn("status: done\n", setField(TICKET, "status", "done"))

    def test_a_long_log_wraps_at_the_house_width_and_reads_back_whole(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        text = " ".join(["word"] * 60)
        self.update("SK-0001", log=text)
        lines = path.read_text(encoding="utf8").rstrip("\n").split("\n")
        entry = lines[lines.index("- 2026-11-01: Filed.") + 1 :]
        self.assertGreater(len(entry), 1)
        self.assertTrue(all(len(line) <= LOG_WIDTH for line in entry))
        self.assertTrue(all(line.startswith("  ") for line in entry[1:]))
        self.assertEqual(_parse(path.read_text(encoding="utf8"))[3][-1]["text"], text)

    def test_a_wrapped_entry_with_a_spaced_dash_at_the_wrap_reads_back_whole(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        text = "word " * 17 + "x - then the rest of it"
        self.update("SK-0001", log=text)
        self.assertEqual(_parse(path.read_text(encoding="utf8"))[3][-1]["text"], text.strip())
        self.assertTrue(all(len(line) <= LOG_WIDTH for line in path.read_text(encoding="utf8").splitlines()))

    def test_a_log_heading_inside_a_code_fence_is_not_the_log(self):
        fenced = TICKET.replace("## Log", "```\n## Log\n```\n\n## Log")
        path = self.write("SK-0001-a-ticket.md", fenced)
        self.update("SK-0001", log="new")
        self.assertEqual(path.read_text(encoding="utf8"), fenced.rstrip("\n") + f"\n- {date.today().isoformat()}: new\n")
        self.assertEqual([e["text"] for e in _parse(path.read_text(encoding="utf8"))[3]], ["Filed.", "new"])

    def test_a_log_heading_only_in_a_code_fence_gets_a_real_log(self):
        path = self.write("SK-0001-a-ticket.md", NO_LOG + "```\n## Log\n```\n")
        self.update("SK-0001", log="new")
        self.assertEqual([e["text"] for e in _parse(path.read_text(encoding="utf8"))[3]], ["new"])

    def test_an_indented_sub_bullet_reads_back_apart_from_the_entry_above(self):
        text = TICKET + "  - a detail\n"
        self.assertEqual(_parse(text)[3], [{"date": "2026-11-01", "text": "Filed."}])


class CommandLineTests(StoreCase):
    def run_command(self, command, argv):
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("lib.issue_update.storeFromConfig", return_value=str(self.store)),
            mock.patch("lib.issue_update.reindexStore"),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = command(argv)
        return code, out.getvalue()

    def test_help_shows_one_usage_line_each(self):
        for command, word in ((updateMain, "issue-update"), (createMain, "issue-create")):
            code, out = self.run_command(command, ["--help"])
            self.assertEqual(code, 0)
            self.assertEqual(len(out.strip().splitlines()), 1)
            self.assertTrue(out.startswith(f"usage: {word}"))

    def test_update_prints_the_path_it_wrote(self):
        path = self.write("SK-0001-a-ticket.md", TICKET)
        code, out = self.run_command(updateMain, ["SK-0001", "status=in-progress", "--log", "Started."])
        self.assertEqual(out.strip(), str(path))
        self.assertIn("status: in-progress", path.read_text(encoding="utf8"))

    def test_create_prints_the_path_it_wrote(self):
        code, out = self.run_command(createMain, ["Make a thing", "type=bug"])
        self.assertEqual(Path(out.strip()), self.store / "active" / "SK-0001-make-a-thing.md")

    def test_a_title_with_an_equals_sign_is_still_the_title(self):
        code, out = self.run_command(createMain, ["Set a=b right"])
        self.assertTrue(Path(out.strip()).name.startswith("SK-0001-set-a-b-right"))

    def test_update_with_nothing_to_do_or_a_stray_word_refuses_with_the_usage(self):
        self.write("SK-0001-a-ticket.md", TICKET)
        for argv in (["SK-0001"], ["SK-0001", "stray"], []):
            with self.assertRaisesRegex(RuntimeError, "usage: issue-update"):
                self.run_command(updateMain, argv)


class CallersTests(StoreCase):
    def test_start_and_done_notes_go_through_updateTicket(self):
        from lib.fileissues import FileIssues

        path = self.write("SK-0001-a-ticket.md", TICKET)
        fi = FileIssues({"dir": str(self.store)})
        with mock.patch("lib.issue_update.reindexStore"):
            fi.transition("SK-0001", "In Progress")
            fi.addComment("SK-0001", "Started.")
        text = path.read_text(encoding="utf8")
        self.assertIn("status: in-progress", text)
        self.assertRegex(text, r"- \d{4}-\d{2}-\d{2}: Started\.\n$")

    def test_no_module_but_issue_update_writes_an_issue_file(self):
        lib = Path(__file__).parent
        writers = [
            p.name
            for p in lib.glob("*.py")
            if not p.name.startswith("test_") and re.search(r"_setField|_appendLog", p.read_text(encoding="utf8"))
        ]
        self.assertEqual(writers, [])


if __name__ == "__main__":
    unittest.main()
