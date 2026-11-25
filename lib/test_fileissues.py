# lib/fileissues.py: SK-<nnnn> issues stored as files (issues/README.md's format), implementing the
# same surface lib/jira.py's Jira class does. Covers parsing, status mapping, the transition +
# read-back guard, the ## Log comment append, list() filtering, and the per-key dispatch
# (isFileBacked()) plus the merged list lib/cli.py's issueRows() builds from Jira + FileIssues.
import shutil
import tempfile
import unittest
from datetime import date
from unittest import mock
from pathlib import Path

from lib.fileissues import STORE_FOLDERS, FileIssues, _parse, canonicalKey, closingFields, initStore, isFileBacked, untickedBoxes


def writeIssue(dir, name, text):
    path = Path(dir) / "active" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf8")
    return path


OPEN_BUG = """---
status: open
type: bug
guide: §4.4
opened: 2026-11-01
closed:
---

# Something is wrong

The thing does the wrong thing, in `lib/thing.py:12`.
"""

IN_PROGRESS_TASK = """---
status: in-progress
type: task
guide: §4.5
opened: 2026-11-02
closed:
---

# A task already underway

Body text here.

## Log
- 2026-11-02: Started on `feat/SK-0002-a-task-already`, in /repo.worktrees/SK-0002.
"""

DONE_QUESTION = """---
status: done
type: question
guide: §4.6
opened: 2026-11-01
closed: 2026-11-03
---

# Already finished

Resolved by commit abc123.
"""


class FileIssuesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jira-plan-jp-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.fi = FileIssues({"dir": self.dir})
        # transition() and addComment() write through issue_update, which runs store-index.
        patcher = mock.patch("lib.issue_update.reindexStore")
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- parsing + status/type mapping -----------------------------------------------------

    def test_issue_parses_frontmatter_title_and_body(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        issue = self.fi.issue("SK-0001")
        self.assertEqual(issue["key"], "SK-0001")
        f = issue["fields"]
        self.assertEqual(f["summary"], "Something is wrong")
        self.assertEqual(f["status"], {"name": "To Do", "statusCategory": {"key": "new"}})
        self.assertEqual(f["issuetype"], {"name": "Bug"})
        self.assertIsNone(f["parent"])
        self.assertEqual(f["issuelinks"], [])
        self.assertIn("wrong thing", f["description"]["content"][0]["content"][0]["text"])
        self.assertEqual(f["comment"]["comments"], [])

    def test_issue_maps_in_progress_task_and_reads_its_log(self):
        writeIssue(self.dir, "SK-0002-a-task-already-underway.md", IN_PROGRESS_TASK)
        issue = self.fi.issue("SK-0002")
        f = issue["fields"]
        self.assertEqual(f["status"], {"name": "In Progress", "statusCategory": {"key": "indeterminate"}})
        self.assertEqual(f["issuetype"], {"name": "Task"})
        comments = f["comment"]["comments"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["author"]["displayName"], "plan")
        text = comments[0]["body"]["content"][0]["content"][0]["text"]
        self.assertIn("Started on `feat/SK-0002-a-task-already`", text)

    def test_issue_maps_done_and_wontfix_to_done_category(self):
        writeIssue(self.dir, "SK-0003-already-finished.md", DONE_QUESTION)
        issue = self.fi.issue("SK-0003")
        self.assertEqual(issue["fields"]["status"], {"name": "Done", "statusCategory": {"key": "done"}})
        self.assertEqual(issue["fields"]["issuetype"], {"name": "Task"})  # question -> Task

    def test_issue_finds_the_padded_file_for_an_unpadded_key(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        self.assertEqual(self.fi.issue("SK-1")["key"], "SK-0001")

    def test_issue_refuses_an_unknown_key(self):
        with self.assertRaises(RuntimeError):
            self.fi.issue("SK-0099")

    def test_blockers_is_always_empty(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        issue = self.fi.issue("SK-0001")
        self.assertEqual(self.fi.blockers(issue), [])

    # --- transition + read-back -------------------------------------------------------------

    def test_transition_to_in_progress_sets_status_and_reads_it_back(self):
        path = writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        issue = self.fi.transition("SK-0001", "In Progress")
        self.assertEqual(issue["fields"]["status"]["name"], "In Progress")
        self.assertIn("status: in-progress", path.read_text(encoding="utf8"))
        # Nothing else in the file changed shape.
        self.assertIn("type: bug", path.read_text(encoding="utf8"))

    def test_statusTransitionId_refuses_a_target_other_than_in_progress(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        with self.assertRaises(RuntimeError):
            self.fi.statusTransitionId("SK-0001", "Done")

    def test_statusTransitionId_refuses_from_a_non_open_status(self):
        writeIssue(self.dir, "SK-0002-a-task-already-underway.md", IN_PROGRESS_TASK)
        with self.assertRaises(RuntimeError):
            self.fi.statusTransitionId("SK-0002", "In Progress")

    def test_transition_refuses_when_the_read_back_does_not_match(self):
        # The same guard Jira.transition() applies after its POST (lib/jira.py): read the issue
        # back and refuse if it didn't actually move. Simulated here by a subclass whose issue()
        # keeps reporting "To Do" regardless of what the file says, standing in for whatever
        # would make a real write not take.
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)

        class StubbornFileIssues(FileIssues):
            def issue(self, key):
                result = super().issue(key)
                result["fields"]["status"] = {"name": "To Do", "statusCategory": {"key": "new"}}
                return result

        fi = StubbornFileIssues({"dir": self.dir})
        with self.assertRaises(RuntimeError):
            fi.transition("SK-0001", "In Progress")

    # --- comments: the ## Log heading ---------------------------------------------------------

    def test_addComment_creates_the_log_heading_once(self):
        path = writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        self.fi.addComment("SK-0001", self.fi.textComment("Started on `feat/SK-0001-x`, in /tmp/SK-0001."))
        text = path.read_text(encoding="utf8")
        self.assertEqual(text.count("## Log"), 1)
        self.assertIn("Started on `feat/SK-0001-x`, in /tmp/SK-0001.", text)
        self.assertRegex(text, r"- \d{4}-\d{2}-\d{2}: Started on")

    def test_addComment_appends_a_second_line_under_the_existing_heading(self):
        path = writeIssue(self.dir, "SK-0002-a-task-already-underway.md", IN_PROGRESS_TASK)
        self.fi.addComment("SK-0002", self.fi.textComment("Done: merged."))
        text = path.read_text(encoding="utf8")
        self.assertEqual(text.count("## Log"), 1)
        self.assertIn("Started on `feat/SK-0002-a-task-already`", text)  # the original line stays
        self.assertIn("Done: merged.", text)
        logEntries = self.fi.issue("SK-0002")["fields"]["comment"]["comments"]
        self.assertEqual(len(logEntries), 2)

    def test_addComment_accepts_a_plain_string_too(self):
        path = writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        self.fi.addComment("SK-0001", "a plain note")
        self.assertIn("a plain note", path.read_text(encoding="utf8"))

    # --- list(): open/in-progress only, done/wontfix excluded --------------------------------

    def test_list_includes_only_open_and_in_progress(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        writeIssue(self.dir, "SK-0002-a-task-already-underway.md", IN_PROGRESS_TASK)
        writeIssue(self.dir, "SK-0003-already-finished.md", DONE_QUESTION)
        rows = self.fi.list()
        ids = sorted(r["id"] for r in rows)
        self.assertEqual(ids, ["SK-0001", "SK-0002"])
        row1 = next(r for r in rows if r["id"] == "SK-0001")
        self.assertEqual(row1["status"], "To Do")
        self.assertEqual(row1["statusCategory"], "new")
        self.assertEqual(row1["project"], "SK")
        self.assertEqual(row1["subject"], "Something is wrong")
        self.assertIsNone(row1["after"])  # OPEN_BUG has no `after:` field

    def test_list_exposes_the_after_field(self):
        writeIssue(
            self.dir,
            "SK-0001-something-is-wrong.md",
            OPEN_BUG.replace("closed:\n---", "closed:\nafter: CIAM-163\n---"),
        )
        row = self.fi.list()[0]
        self.assertEqual(row["after"], "CIAM-163")

    # --- browseUrl -----------------------------------------------------------------

    def test_browseUrl_is_the_absolute_file_path(self):
        path = writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        self.assertEqual(self.fi.browseUrl("SK-0001"), str(path.resolve()))

    # --- checkClose / close --------------------------------------------------------------------

    def test_close_sets_status_done_and_closed_today_and_says_so(self):
        path = writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        self.fi.checkClose("SK-0001")
        note = self.fi.close("SK-0001")
        front = _parse(path.read_text(encoding="utf8"))[0]
        self.assertEqual((front["status"], front["closed"]), ("done", date.today().isoformat()))
        self.assertEqual(note, f"Closed SK-0001: status done, closed {front['closed']}.")

    def test_close_leaves_a_ticket_already_closed_with_its_date(self):
        text = OPEN_BUG.replace("status: open", "status: done").replace("closed:\n", "closed: 2026-11-03\n")
        path = writeIssue(self.dir, "SK-0001-something-is-wrong.md", text)
        self.assertEqual(self.fi.close("SK-0001"), "SK-0001 is already closed.")
        self.assertEqual(path.read_text(encoding="utf8"), text)

    def test_checkClose_refuses_an_unticked_done_box_naming_it(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG + "\n## Done\n\n- [ ] Not yet.\n")
        with self.assertRaisesRegex(RuntimeError, "SK-0001 can't be closed.*Not yet"):
            self.fi.checkClose("SK-0001")

    # --- list(): ordinal order -------------------------------------------------------------------

    def test_list_puts_issues_with_an_ordinal_first_in_ordinal_order(self):
        # SK-0002 ordered after SK-0001, even though SK-0001's filename/number would otherwise sort first.
        writeIssue(
            self.dir,
            "SK-0001-something-is-wrong.md",
            OPEN_BUG.replace("closed:\n---", "closed:\nordinal: 2\n---"),
        )
        writeIssue(
            self.dir,
            "SK-0002-a-task-already-underway.md",
            IN_PROGRESS_TASK.replace("closed:\n---", "closed:\nordinal: 1\n---"),
        )
        ids = [r["id"] for r in self.fi.list()]
        self.assertEqual(ids, ["SK-0002", "SK-0001"])

    def test_list_sorts_issues_without_an_ordinal_by_issue_number_not_filename(self):
        # No ordinal: field on either file. Past the padding, filename order would put SK-10000
        # before SK-9999; the issue number keeps them in order.
        writeIssue(self.dir, "SK-10000-ten-thousandth-issue.md", OPEN_BUG)
        writeIssue(self.dir, "SK-9999-a-task-already-underway.md", IN_PROGRESS_TASK)
        ids = [r["id"] for r in self.fi.list()]
        self.assertEqual(ids, ["SK-9999", "SK-10000"])

    def test_list_puts_ordered_issues_before_the_rest_regardless_of_number(self):
        # SK-0009 (high number, with an ordinal) sorts ahead of SK-0001 (low number, no ordinal: field at all).
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        writeIssue(
            self.dir,
            "SK-0009-a-task-already-underway.md",
            IN_PROGRESS_TASK.replace("closed:\n---", "closed:\nordinal: 1\n---"),
        )
        ids = [r["id"] for r in self.fi.list()]
        self.assertEqual(ids, ["SK-0009", "SK-0001"])

    def test_list_treats_a_missing_ordinal_field_as_unordered(self):
        # OPEN_BUG's frontmatter has no `ordinal:` line at all (not even an empty one) -- list()
        # must not raise and must sort it after any issue with an ordinal.
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        writeIssue(
            self.dir,
            "SK-0002-a-task-already-underway.md",
            IN_PROGRESS_TASK.replace("closed:\n---", "closed:\nordinal: 1\n---"),
        )
        ids = [r["id"] for r in self.fi.list()]
        self.assertEqual(ids, ["SK-0002", "SK-0001"])


class DispatchTests(unittest.TestCase):
    def test_isFileBacked_routes_SK_only(self):
        self.assertTrue(isFileBacked("SK-0001"))
        self.assertTrue(isFileBacked("SK-0042"))
        self.assertFalse(isFileBacked("CIAM-5"))

    def test_canonicalKey_pads_a_store_key_to_four_digits(self):
        self.assertEqual(canonicalKey("SK-3"), "SK-0003")
        self.assertEqual(canonicalKey("SK-0003"), "SK-0003")
        self.assertEqual(canonicalKey("SK-12345"), "SK-12345")

    def test_canonicalKey_leaves_other_keys_alone(self):
        self.assertEqual(canonicalKey("CIAM-5"), "CIAM-5")
        self.assertEqual(canonicalKey("SK-x"), "SK-x")


class MergedListTests(unittest.TestCase):
    # Decision #3: `plan list`/the picker merge Jira's JQL results with every open/in-progress
    # SK file. lib/cli.py's mergeRows() does the actual interleaving (lib/test_cli.py's
    # MergeRowsTests covers that in full); this exercises the same shape directly against
    # FileIssues, without needing a live Jira.
    def test_file_rows_and_jira_rows_concatenate_by_shape(self):
        dir = tempfile.mkdtemp(prefix="jira-plan-jp-merge-")
        self.addCleanup(shutil.rmtree, dir, ignore_errors=True)
        writeIssue(dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        fi = FileIssues({"dir": dir})

        jiraRows = [
            {
                "id": "CIAM-9",
                "subject": "A Jira issue",
                "status": "In Progress",
                "statusCategory": "indeterminate",
                "issueType": "Story",
                "project": "CIAM",
                "parent": None,
            }
        ]
        merged = jiraRows + fi.list()
        ids = sorted(r["id"] for r in merged)
        self.assertEqual(ids, ["CIAM-9", "SK-0001"])
        # Every row shares the core keys Jira's own rows carry, whichever backend it came from.
        # A SK row also carries `after:` (the interleaving anchor, issues/README.md, 2026-11-06) --
        # Jira rows don't need one, since lib/cli.py's mergeRows() reads it with a plain .get(),
        # defaulting to None, rather than requiring every row to share one identical key set.
        coreKeys = {"id", "subject", "status", "statusCategory", "issueType", "project", "parent"}
        for row in merged:
            self.assertTrue(coreKeys <= row.keys())
        skRow = next(r for r in merged if r["id"] == "SK-0001")
        self.assertIn("after", skRow)
        self.assertIsNone(skRow["after"])
        self.assertNotIn("after", jiraRows[0])

    # --- ticket_id -----------------------------------------------------------------------------

    def test_parse_reads_ticket_id_first_key_and_accepts_files_without_it(self):
        withId = "---\nticket_id: SK-0001\n" + OPEN_BUG[len("---\n"):]
        front = _parse(withId)[0]
        self.assertEqual(list(front)[0], "ticket_id")
        self.assertEqual(front["ticket_id"], "SK-0001")
        withoutId = _parse(OPEN_BUG)[0]
        self.assertNotIn("ticket_id", withoutId)
        self.assertEqual(withoutId["status"], "open")

    def test_fileissues_writes_no_issue_file_itself(self):
        source = (Path(__file__).parent / "fileissues.py").read_text(encoding="utf8")
        self.assertNotIn("write_text", source)
        self.assertNotIn("_setField", source)
        self.assertNotIn("_appendLog", source)


class StoreLayoutTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="skuld-layout-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def folders(self):
        return sorted(p.name for p in Path(self.dir).iterdir())

    def test_init_on_an_empty_folder_makes_every_store_folder(self):
        made = initStore(self.dir)
        self.assertEqual(made, list(STORE_FOLDERS))
        self.assertEqual(self.folders(), ["active", "archive", "milestones", "queries", "reviews"])

    def test_a_file_named_like_a_store_folder_is_refused_naming_it(self):
        (Path(self.dir) / "active").write_text("")
        with self.assertRaisesRegex(RuntimeError, "active is a file"):
            initStore(self.dir)

    def test_a_second_init_makes_nothing_and_changes_nothing(self):
        initStore(self.dir)
        ticket = writeIssue(self.dir, "SK-0001-a.md", OPEN_BUG)
        before = (self.folders(), ticket.read_text(encoding="utf8"))
        self.assertEqual(initStore(self.dir), [])
        self.assertEqual((self.folders(), ticket.read_text(encoding="utf8")), before)

    def test_a_half_made_store_is_finished_by_running_init_again(self):
        (Path(self.dir) / "active").mkdir()
        self.assertEqual(initStore(self.dir), ["milestones", "archive", "reviews", "queries"])

    def test_a_missing_store_root_is_refused_naming_it_and_not_made(self):
        root = Path(self.dir) / "no-such-store"
        with self.assertRaisesRegex(RuntimeError, "no-such-store"):
            initStore(root)
        self.assertFalse(root.exists())

    def test_a_key_in_both_folders_is_refused_whatever_the_file_names(self):
        for activeName, archiveName in (("SK-0001.md", "SK-0001.md"), ("SK-0001.md", "SK-0001-old.md"), ("SK-0001-a.md", "SK-0001-b.md")):
            with self.subTest(active=activeName, archive=archiveName):
                store = Path(tempfile.mkdtemp(prefix="skuld-dup-"))
                self.addCleanup(shutil.rmtree, store, ignore_errors=True)
                initStore(store)
                (store / "active" / activeName).write_text(OPEN_BUG, encoding="utf8")
                (store / "archive" / archiveName).write_text(OPEN_BUG, encoding="utf8")
                fi = FileIssues({"dir": str(store)})
                with self.assertRaisesRegex(RuntimeError, "more than one ticket file matches SK-0001"):
                    fi.issue("SK-0001")
                with self.assertRaisesRegex(RuntimeError, "more than one ticket file matches SK-0001"):
                    fi.addComment("SK-0001", fi.textComment("a note"))
                self.assertEqual((store / "active" / activeName).read_text(encoding="utf8"), OPEN_BUG)

    def test_active_and_archive_are_read_together(self):
        writeIssue(self.dir, "SK-0001-a.md", OPEN_BUG)
        archive = Path(self.dir) / "archive"
        archive.mkdir()
        (archive / "SK-0002-b.md").write_text(IN_PROGRESS_TASK, encoding="utf8")
        fi = FileIssues({"dir": self.dir})
        self.assertEqual([r["id"] for r in fi.list()], ["SK-0001", "SK-0002"])
        self.assertEqual(fi.issue("SK-0002")["key"], "SK-0002")

    def test_a_ticket_at_the_top_level_is_refused_naming_the_file_and_active(self):
        stray = Path(self.dir) / "SK-0003-stray.md"
        stray.write_text(OPEN_BUG, encoding="utf8")
        fi = FileIssues({"dir": self.dir})
        for read in (fi.list, lambda: fi.issue("SK-0003")):
            with self.assertRaisesRegex(RuntimeError, f"{stray.name}.*top level.*active/"):
                read()


class CloseHelperTests(unittest.TestCase):
    def test_closingFields_by_status(self):
        self.assertEqual(closingFields({"status": "in-progress", "closed": ""}), {"status": "done"})
        self.assertEqual(closingFields({"status": "done", "closed": ""}), {"status": "done"})
        self.assertEqual(closingFields({"status": "wontfix", "closed": ""}), {"status": "wontfix"})
        self.assertEqual(closingFields({"status": "done", "closed": "2026-11-03"}), {})
        self.assertEqual(closingFields({"status": "wontfix", "closed": "2026-11-03"}), {})

    def test_untickedBoxes_reads_the_done_section_outside_code_fences(self):
        text = (
            "# T\n\n## Task\n\n- [ ] not a Done box\n\n## Done\n\n- [x] one\n- [ ] two\n"
            "```\n- [ ] in a fence\n## Log\n```\n- [X] three\n\n## Log\n\n- 2026-11-09: x\n- [ ] after\n"
        )
        self.assertEqual(untickedBoxes(text), ["two"])


if __name__ == "__main__":
    unittest.main()
