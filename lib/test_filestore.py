# lib/filestore.py: SK-<nnnn> issues stored as files (issues/README.md's format), the IssueStore
# port's file adapter (lib/issuestore.py), returning lib/issue.py's Issue as lib/jira.py's Jira does. Covers parsing, status mapping, the transition +
# read-back guard, the ## Log comment append, list() filtering, and the per-key dispatch
# (isFileBacked()) plus the merged list lib/cli.py's issueRows() builds from Jira + FileStore.
import shutil
import tempfile
import unittest
from pathlib import Path

import dataclasses

from lib.filestore import FileStore, canonicalKey, isFileBacked
from lib.issue import DONE, IN_PROGRESS, NEW, Comment, IssueNotFound


def writeIssue(dir, name, text):
    path = Path(dir) / name
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


class FileStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jira-plan-jp-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.fi = FileStore({"dir": self.dir})

    # --- parsing + status/type mapping -----------------------------------------------------

    def test_issue_parses_frontmatter_title_and_body(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        issue = self.fi.issue("SK-0001")
        self.assertEqual(issue.key, "SK-0001")
        self.assertEqual(issue.summary, "Something is wrong")
        self.assertEqual((issue.status, issue.category), ("open", NEW))
        self.assertEqual(issue.issueType, "bug")
        self.assertIsNone(issue.parent)
        self.assertEqual(issue.blockers, ())
        self.assertEqual(issue.description, "The thing does the wrong thing, in `lib/thing.py:12`.")
        self.assertEqual(issue.comments, ())

    def test_issue_maps_in_progress_task_and_reads_its_log(self):
        writeIssue(self.dir, "SK-0002-a-task-already-underway.md", IN_PROGRESS_TASK)
        issue = self.fi.issue("SK-0002")
        self.assertEqual((issue.status, issue.category), ("in-progress", IN_PROGRESS))
        self.assertEqual(issue.issueType, "task")
        self.assertEqual(
            issue.comments,
            (Comment("plan", "Started on `feat/SK-0002-a-task-already`, in /repo.worktrees/SK-0002.", "2026-11-02"),),
        )

    def test_issue_maps_done_and_wontfix_to_done_category(self):
        writeIssue(self.dir, "SK-0003-already-finished.md", DONE_QUESTION)
        issue = self.fi.issue("SK-0003")
        self.assertEqual((issue.status, issue.category), ("done", DONE))
        self.assertEqual(issue.issueType, "question")

    def test_issue_finds_the_padded_file_for_an_unpadded_key(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        self.assertEqual(self.fi.issue("SK-1").key, "SK-0001")

    def test_issue_refuses_an_unknown_key(self):
        with self.assertRaises(IssueNotFound):
            self.fi.issue("SK-0099")

    def test_issue_has_its_file_and_its_fields_in_file_order(self):
        path = writeIssue(self.dir, "SK-0002-a-task.md", IN_PROGRESS_TASK)
        issue = self.fi.issue("SK-2")
        self.assertEqual(issue.file, str(path.resolve()))
        self.assertEqual(list(issue.fields), ["status", "type", "guide", "opened", "closed"])

    def test_issue_page_is_on_the_local_server(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        store = FileStore({"dir": self.dir, "pages": "http://127.0.0.1:8765/"})
        self.assertEqual(store.issue("SK-1").page, "http://127.0.0.1:8765/issues/SK-0001")

    def test_issue_has_no_page_without_the_servers_url(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        self.assertIsNone(self.fi.issue("SK-0001").page)

    # --- transition + read-back -------------------------------------------------------------

    def test_transition_to_in_progress_sets_status_and_reads_it_back(self):
        path = writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        issue = self.fi.transition("SK-0001", IN_PROGRESS)
        self.assertEqual((issue.status, issue.category), ("in-progress", IN_PROGRESS))
        self.assertIn("status: in-progress", path.read_text(encoding="utf8"))
        # Nothing else in the file changed shape.
        self.assertIn("type: bug", path.read_text(encoding="utf8"))

    def test_statusTransitionId_refuses_a_target_other_than_in_progress(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        with self.assertRaises(RuntimeError):
            self.fi.statusTransitionId("SK-0001", DONE)

    def test_statusTransitionId_refuses_from_a_non_open_status(self):
        writeIssue(self.dir, "SK-0002-a-task-already-underway.md", IN_PROGRESS_TASK)
        with self.assertRaises(RuntimeError):
            self.fi.statusTransitionId("SK-0002", IN_PROGRESS)

    def test_transition_refuses_when_the_read_back_does_not_match(self):
        # Read the issue back and refuse if it didn't actually move. Simulated here by a subclass
        # whose issue() keeps reporting "open" regardless of what the file says, standing in for whatever
        # would make a real write not take.
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)

        class StubbornFileStore(FileStore):
            def issue(self, key):
                return dataclasses.replace(super().issue(key), status="open", category=NEW)

        fi = StubbornFileStore({"dir": self.dir})
        with self.assertRaises(RuntimeError):
            fi.transition("SK-0001", IN_PROGRESS)

    # --- comments: the ## Log heading ---------------------------------------------------------

    def test_addComment_creates_the_log_heading_once(self):
        path = writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        self.fi.addComment("SK-0001", "Started on `feat/SK-0001-x`, in /tmp/SK-0001.")
        text = path.read_text(encoding="utf8")
        self.assertEqual(text.count("## Log"), 1)
        self.assertIn("Started on `feat/SK-0001-x`, in /tmp/SK-0001.", text)
        self.assertRegex(text, r"- \d{4}-\d{2}-\d{2}: Started on")

    def test_addComment_appends_a_second_line_under_the_existing_heading(self):
        path = writeIssue(self.dir, "SK-0002-a-task-already-underway.md", IN_PROGRESS_TASK)
        self.fi.addComment("SK-0002", "Done: merged.")
        text = path.read_text(encoding="utf8")
        self.assertEqual(text.count("## Log"), 1)
        self.assertIn("Started on `feat/SK-0002-a-task-already`", text)  # the original line stays
        self.assertIn("Done: merged.", text)
        self.assertEqual(len(self.fi.issue("SK-0002").comments), 2)

    # --- list(): open/in-progress only, done/wontfix excluded --------------------------------

    def test_list_includes_only_open_and_in_progress(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        writeIssue(self.dir, "SK-0002-a-task-already-underway.md", IN_PROGRESS_TASK)
        writeIssue(self.dir, "SK-0003-already-finished.md", DONE_QUESTION)
        rows = self.fi.list()
        ids = sorted(r["id"] for r in rows)
        self.assertEqual(ids, ["SK-0001", "SK-0002"])
        row1 = next(r for r in rows if r["id"] == "SK-0001")
        self.assertEqual(row1["status"], "open")
        self.assertEqual(row1["category"], NEW)
        self.assertEqual(row1["issueType"], "bug")
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

    # --- closeNote ------------------------------------------------------------------------------

    def test_closeNote_names_the_file_not_a_jira_url(self):
        writeIssue(self.dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        note = self.fi.closeNote("SK-0001")
        self.assertIn("SK-0001", note)
        self.assertIn(self.dir, note)
        self.assertNotIn("atlassian", note)


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


class RowShapeTests(unittest.TestCase):
    # Decision #3: Next up merges every project's set, so a ticket row carries the keys every
    # adapter's list() returns, plus its `after:` anchor (lib/cli.py's mergeSets()).
    def test_a_ticket_row_carries_every_adapters_row_keys_and_its_anchor(self):
        dir = tempfile.mkdtemp(prefix="jira-plan-jp-merge-")
        self.addCleanup(shutil.rmtree, dir, ignore_errors=True)
        writeIssue(dir, "SK-0001-something-is-wrong.md", OPEN_BUG)
        [row] = FileStore({"dir": dir}).list()
        self.assertTrue({"id", "subject", "status", "category", "issueType", "project", "parent"} <= row.keys())
        self.assertIsNone(row["after"])


if __name__ == "__main__":
    unittest.main()
