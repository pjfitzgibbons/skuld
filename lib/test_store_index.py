import tempfile
import unittest
from pathlib import Path

from lib.store_index import Milestone, Ticket, frontMatter, indexText, lineFor, main, readStore, workOrder


def ticket(key, status="open", milestone="m1", ordinal=100.0, blocked_by=(), title="A task"):
    return Ticket(key, f"{key}-a-task.md", title, status, milestone, ordinal, list(blocked_by))


def byKey(*tickets):
    return {t.key: t for t in tickets}


class LineTest(unittest.TestCase):
    def test_in_progress_is_marked_and_still_lists_its_blockers(self):
        t = ticket("SK-0002", status="in-progress", blocked_by=["SK-0001"])
        self.assertEqual(lineFor(t, byKey(t, ticket("SK-0001"))), "- [SK-0002] **in progress** · A task [SK-0001]")

    def test_ready_has_no_mark_when_everything_it_waits_on_is_closed(self):
        t = ticket("SK-0002", blocked_by=["SK-0001", "SK-0003"])
        closed = [ticket("SK-0001", status="done"), ticket("SK-0003", status="wontfix")]
        self.assertEqual(lineFor(t, byKey(t, *closed)), "- [SK-0002] A task")

    def test_blockers_name_only_the_open_ones_and_unknown_keys(self):
        t = ticket("SK-0004", blocked_by=["SK-0001", "SK-0002", "SK-0099"])
        others = [ticket("SK-0001", status="done"), ticket("SK-0002")]
        self.assertEqual(lineFor(t, byKey(t, *others)), "- [SK-0004] A task [SK-0002,SK-0099]")


class WorkOrderTest(unittest.TestCase):
    def test_by_ordinal(self):
        order = workOrder([ticket("SK-0002", ordinal=200), ticket("SK-0001", ordinal=300)])
        self.assertEqual([t.key for t in order], ["SK-0002", "SK-0001"])

    def test_after_what_it_waits_on_despite_a_lower_ordinal(self):
        first = ticket("SK-0001", ordinal=100, blocked_by=["SK-0002"])
        order = workOrder([first, ticket("SK-0002", ordinal=200)])
        self.assertEqual([t.key for t in order], ["SK-0002", "SK-0001"])

    def test_a_cycle_still_lists_everything(self):
        order = workOrder([ticket("SK-0001", blocked_by=["SK-0002"]), ticket("SK-0002", blocked_by=["SK-0001"])])
        self.assertEqual(sorted(t.key for t in order), ["SK-0001", "SK-0002"])


class IndexTextTest(unittest.TestCase):
    def test_milestones_in_order_then_no_milestone_and_closed_left_out(self):
        tickets = [
            ticket("SK-0001", milestone="m2"),
            ticket("SK-0002", milestone=""),
            ticket("SK-0003", milestone="m1"),
            ticket("SK-0004", status="done"),
        ]
        milestones = [Milestone("m2", "Second", 200), Milestone("m1", "First", 100)]
        text = indexText(tickets, milestones)
        self.assertLess(text.index("[First](milestones/m1.md)"), text.index("[Second](milestones/m2.md)"))
        self.assertLess(text.index("[Second]"), text.index("## No milestone"))
        self.assertNotIn("SK-0004", text)
        self.assertIn("[SK-0001]: SK-0001-a-task.md", text)

    def test_long_lines_wrap(self):
        text = indexText([ticket("SK-0001", title="word " * 40)], [Milestone("m1", "First", 100)])
        self.assertTrue(all(len(line) <= 120 for line in text.splitlines()))


class FrontMatterTest(unittest.TestCase):
    def test_comments_and_empty_values(self):
        fields = frontMatter("---\nstatus: open   # open | done\nclosed:\n---\n# Title\n")
        self.assertEqual(fields, {"status": "open", "closed": ""})


class ReadStoreTests(unittest.TestCase):
    def test_tickets_come_from_active_and_archive_and_link_through_their_folder(self):
        with tempfile.TemporaryDirectory() as root:
            for folder, name in (("active", "SK-0001-a.md"), ("archive", "SK-0002-b.md")):
                (Path(root) / folder).mkdir()
                (Path(root) / folder / name).write_text("---\nstatus: open\n---\n\n# T\n")
            (Path(root) / "milestones").mkdir()
            tickets, _ = readStore(Path(root))
        self.assertEqual([t.file for t in tickets], ["active/SK-0001-a.md", "archive/SK-0002-b.md"])



class CommandTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        store = Path(self.root.name)
        (store / "active").mkdir()
        (store / "milestones").mkdir()
        (store / "active" / "SK-0001-a.md").write_text("---\nstatus: open\n---\n\n# T\n")
        self.store = str(store)

    def test_check_fails_until_the_index_is_written_and_changes_nothing(self):
        self.assertEqual(main(["--check", self.store]), 1)
        self.assertFalse((Path(self.store) / "INDEX.md").exists())
        self.assertEqual(main([self.store]), 0)
        self.assertIn("[SK-0001]: active/SK-0001-a.md", (Path(self.store) / "INDEX.md").read_text())
        self.assertEqual(main(["--check", self.store]), 0)

    def test_an_unknown_option_exits_with_the_usage(self):
        with self.assertRaises(SystemExit) as raised:
            main(["--nope", self.store])
        self.assertIn("usage: store-index", str(raised.exception))
