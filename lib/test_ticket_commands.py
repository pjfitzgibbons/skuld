# `plan new` and `plan set` (lib/cli.py) on a temporary store: they write only through
# lib/issue_update.py. No network, no `plan` binary, no editor, no store-index.
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import cli, store_projects
from lib.fileissues import FileIssues, _parse

STORE_YML = """projects:
  - key: SK
    name: Skuld
    repos:
      - git@github.com:pjfitzgibbons/skuld.git
archiveAfterDays: 14
"""

TICKET = """---
ticket_id: SK-0001
status: open
type: task
opened: 2026-11-01
closed:
blocked_by: SK-0002
---

# A ticket

## Log

- 2026-11-01: Filed.
"""

OTHER = TICKET.replace("SK-0001", "SK-0002").replace("blocked_by: SK-0002", "blocked_by:")
THIRD = OTHER.replace("SK-0002", "SK-0003")


class CommandCase(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="plan-ticket-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.store = root / "store"
        (self.store / "active").mkdir(parents=True)
        (self.store / "milestones").mkdir()
        self.storeYml = root / "store.yml"
        self.storeYml.write_text(STORE_YML, encoding="utf8")
        self.cfg = {"issueStore": str(self.store)}
        self.editor = mock.Mock()
        for patcher in (
            mock.patch.object(cli, "STORE_YML", self.storeYml),
            mock.patch.object(cli, "openInEditor", self.editor),
            mock.patch("lib.issue_update.reindexStore"),
            mock.patch("lib.issue_update.outlineProblems", return_value=[]),
            mock.patch.object(cli.config_mod, "load", return_value=self.cfg),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, text in (("SK-0001-a-ticket.md", TICKET), ("SK-0002-other.md", OTHER), ("SK-0003-third.md", THIRD)):
            (self.store / "active" / name).write_text(text, encoding="utf8")

    def run_plan(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def read(self, name):
        return (self.store / "active" / name).read_text(encoding="utf8")


class StoreProjectsTests(unittest.TestCase):
    def test_reads_the_project_keys_and_ignores_the_rest(self):
        folder = Path(tempfile.mkdtemp(prefix="store-yml-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        path = folder / "store.yml"
        path.write_text(STORE_YML + "  - key: GH\n    name: Github\n", encoding="utf8")
        self.assertEqual(store_projects.projectKeys(path), ["SK", "GH"])

    def test_a_missing_file_refuses_naming_it(self):
        with self.assertRaisesRegex(RuntimeError, "store.yml"):
            store_projects.projectKeys(Path("/nonexistent/store.yml"))


class PlanNewTests(CommandCase):
    def test_makes_the_next_ticket_prints_its_path_and_opens_the_editor(self):
        out = self.run_plan("new", "SK", "Make a thing")
        path = self.store / "active" / "SK-0004-make-a-thing.md"
        self.assertEqual(out.strip(), str(path))
        front = _parse(path.read_text(encoding="utf8"))[0]
        self.assertEqual((front["ticket_id"], front["status"]), ("SK-0004", "open"))
        self.editor.assert_called_once_with(path)

    def test_the_new_file_is_checked_with_outline_py_and_findings_are_printed(self):
        with mock.patch("lib.issue_update.outlineProblems", return_value=["Done: 0 conditions"]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.run_plan("new", "SK", "Make a thing")
        self.assertIn("Done: 0 conditions", err.getvalue())
        self.editor.assert_called_once()

    def test_an_unlisted_prefix_refuses_naming_store_yml_and_writes_nothing(self):
        before = sorted(p.name for p in (self.store / "active").iterdir())
        with self.assertRaisesRegex(RuntimeError, "store.yml"):
            self.run_plan("new", "ZZ", "Make a thing")
        self.assertEqual(sorted(p.name for p in (self.store / "active").iterdir()), before)
        self.editor.assert_not_called()

    def test_wrong_arguments_refuse_with_the_usage(self):
        for argv in (["new"], ["new", "SK"], ["new", "SK", "a", "b"]):
            with self.assertRaisesRegex(RuntimeError, "usage: plan new"):
                self.run_plan(*argv)


class PlanSetTests(CommandCase):
    def test_changes_a_field_and_nothing_else(self):
        before = self.read("SK-0001-a-ticket.md").splitlines()
        out = self.run_plan("set", "SK-0001", "status=in-progress")
        after = self.read("SK-0001-a-ticket.md").splitlines()
        self.assertEqual([(b, a) for b, a in zip(before, after) if b != a], [("status: open", "status: in-progress")])
        self.assertEqual(len(before), len(after))
        self.assertEqual(out.strip(), str(self.store / "active" / "SK-0001-a-ticket.md"))

    def test_an_unpadded_key_finds_its_ticket(self):
        self.run_plan("set", "SK-1", "ordinal=250")
        self.assertEqual(_parse(self.read("SK-0001-a-ticket.md"))[0]["ordinal"], "250")

    def test_a_milestone_that_has_a_file_is_taken_and_one_that_has_none_refuses(self):
        (self.store / "milestones" / "m5-terminal.md").write_text("---\nid: m5-terminal\n---\n", encoding="utf8")
        self.run_plan("set", "SK-0001", "milestone=m5-terminal", "ordinal=250")
        front = _parse(self.read("SK-0001-a-ticket.md"))[0]
        self.assertEqual((front["milestone"], front["ordinal"]), ("m5-terminal", "250"))
        before = self.read("SK-0001-a-ticket.md")
        with self.assertRaisesRegex(RuntimeError, 'field "milestone".*nope'):
            self.run_plan("set", "SK-0001", "milestone=nope")
        self.assertEqual(self.read("SK-0001-a-ticket.md"), before)

    def test_blocked_by_plus_adds_one_blocker_and_minus_removes_one(self):
        self.run_plan("set", "SK-0001", "blocked_by+=SK-0003")
        self.assertEqual(_parse(self.read("SK-0001-a-ticket.md"))[0]["blocked_by"], "SK-0002, SK-0003")
        self.run_plan("set", "SK-0001", "blocked_by-=SK-0002")
        self.assertEqual(_parse(self.read("SK-0001-a-ticket.md"))[0]["blocked_by"], "SK-0003")
        self.run_plan("set", "SK-0001", "blocked_by-=SK-0003")
        self.assertEqual(_parse(self.read("SK-0001-a-ticket.md"))[0]["blocked_by"], "")

    def test_a_bracketed_blocked_by_is_malformed_so_a_blocker_edit_refuses_naming_it(self):
        path = self.store / "active" / "SK-0001-a-ticket.md"
        path.write_text(TICKET.replace("blocked_by: SK-0002", "blocked_by: [SK-0002]"), encoding="utf8")
        before = path.read_bytes()
        for edit in ("blocked_by+=SK-0003", "blocked_by-=SK-0002"):
            with self.subTest(edit=edit), self.assertRaisesRegex(RuntimeError, 'field "blocked_by".*\\[SK-0002\\]'):
                self.run_plan("set", "SK-0001", edit)
            self.assertEqual(path.read_bytes(), before)

    def test_reading_a_file_with_a_bracketed_blocked_by_does_not_fail(self):
        (self.store / "active" / "SK-0001-a-ticket.md").write_text(
            TICKET.replace("blocked_by: SK-0002", "blocked_by: [SK-0002]"), encoding="utf8"
        )
        fi = FileIssues({"dir": str(self.store)})
        self.assertEqual(sorted(row["id"] for row in fi.list()), ["SK-0001", "SK-0002", "SK-0003"])
        self.assertEqual(fi.issue("SK-0001")["key"], "SK-0001")

    def test_blocked_by_set_directly_takes_only_the_comma_form_and_writes_it_normalized(self):
        self.run_plan("set", "SK-0001", "blocked_by=SK-0002,SK-0003")
        self.assertEqual(_parse(self.read("SK-0001-a-ticket.md"))[0]["blocked_by"], "SK-0002, SK-0003")
        self.run_plan("set", "SK-0001", "blocked_by=")
        self.assertEqual(_parse(self.read("SK-0001-a-ticket.md"))[0]["blocked_by"], "")

    def test_malformed_blocked_by_values_refuse_naming_the_field_and_the_value(self):
        path = self.store / "active" / "SK-0001-a-ticket.md"
        before = path.read_bytes()
        for value in ("[SK-0002]", '"SK-0002"', "sk-0002", "SK-0002,", "SK-0002; SK-0003", "SK-0002, SK-0002", "SK-0002 SK-0003"):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'field "blocked_by"'):
                self.run_plan("set", "SK-0001", f"blocked_by={value}")
            self.assertEqual(path.read_bytes(), before)

    def test_a_jira_key_may_block_and_is_not_looked_up(self):
        self.run_plan("set", "SK-0001", "blocked_by+=CIAM-163")
        self.assertIn("CIAM-163", self.read("SK-0001-a-ticket.md"))

    def test_bad_requests_refuse_naming_the_cause_with_the_file_byte_identical(self):
        path = self.store / "active" / "SK-0001-a-ticket.md"
        before = path.read_bytes()
        cases = (
            (["status=bogus"], 'field "status"'),
            (["ordinal=abc"], 'field "ordinal"'),
            (["nosuchfield=x"], "nosuchfield"),
            (["status=done", "nosuchfield=x"], "nosuchfield"),
            (["blocked_by+=SK-9999"], "SK-9999"),
            (["blocked_by+=SK-0001"], "SK-0001"),
            (["blocked_by+=SK-0002"], "SK-0002"),
            (["blocked_by-=SK-0003"], "SK-0003"),
            (["status+=done"], 'field "status"'),
            (["blocked_by+=nonsense"], "nonsense"),
            (["status=done", "blocked_by+=SK-9999"], "SK-9999"),
        )
        for args, word in cases:
            with self.subTest(args=args), self.assertRaisesRegex(RuntimeError, word):
                self.run_plan("set", "SK-0001", *args)
            self.assertEqual(path.read_bytes(), before)

    def test_a_jira_key_refuses_naming_the_key(self):
        with self.assertRaisesRegex(RuntimeError, "CIAM-1"):
            self.run_plan("set", "CIAM-1", "status=done")

    def test_no_change_asked_refuses_with_the_usage(self):
        for argv in (["set"], ["set", "SK-0001"]):
            with self.assertRaisesRegex(RuntimeError, "usage: plan set"):
                self.run_plan(*argv)


class OpenInEditorTests(unittest.TestCase):
    def test_runs_the_editor_with_its_own_arguments_on_the_path(self):
        with mock.patch.dict("os.environ", {"EDITOR": "nvim -R"}), mock.patch("lib.cli.subprocess.run") as run:
            cli.openInEditor(Path("/x/SK-0001.md"))
        run.assert_called_once_with(["nvim", "-R", "/x/SK-0001.md"])

    def test_without_an_editor_it_says_where_the_file_is(self):
        out = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(out):
            cli.openInEditor(Path("/x/SK-0001.md"))
        self.assertIn("/x/SK-0001.md", out.getvalue())


class DocumentedBehaviourTests(unittest.TestCase):
    def test_plan_has_no_ticket_writer_of_its_own(self):
        source = (Path(cli.__file__)).read_text(encoding="utf8")
        self.assertNotIn("write_text", source.split("def cmdNew")[1].split("def main")[0])


if __name__ == "__main__":
    unittest.main()
