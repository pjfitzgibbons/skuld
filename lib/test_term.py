# M3: tmux's `-t <name>` (no leading "=") prefix-matches, so `has-session -t CIAM-1` also matches
# a session named CIAM-12. term.py must target every tmux call with the exact-match "=name" form.
# term.py talks to tmux through start.py's run() (subprocess), so these tests patch
# `lib.term.run` directly (term.py did `from .start import run`, binding its own name at import
# time), and assert the exact argv each tool would see, never spawning a real process. The slots
# file itself is a real temp file, since its write-temp-then-rename logic is what's under test.
#
# See lib/term.py's module comment and docs/architecture.md §7.1.
import json
import os
import tempfile
import unittest
from unittest import mock

from lib import term


class TermTests(unittest.TestCase):
    def test_hasSession_targets_tmux_with_exact_match_not_bare_name(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                return ""
            raise RuntimeError(f"unexpected {command} {' '.join(args)}")

        with mock.patch.object(term, "run", side_effect=fake_run):
            found = term.hasSession("CIAM-1")
        self.assertTrue(found)
        self.assertEqual(calls, [["tmux", "has-session", "-t", "=CIAM-1"]])

    def test_hasSession_exact_match_target_not_satisfied_by_same_prefix_session(self):
        # Models real tmux: with the exact "=" form, has-session -t =CIAM-1 fails when only
        # CIAM-12 exists, where the old bare "-t CIAM-1" form would have wrongly matched it.
        def fake_run(command, args, cwd):
            if command == "tmux" and args[0] == "has-session":
                target = args[args.index("-t") + 1]
                if target == "=CIAM-12":
                    return ""
                raise RuntimeError("can't find session")  # tmux's real behavior for an exact miss
            raise RuntimeError(f"unexpected {command} {' '.join(args)}")

        with mock.patch.object(term, "run", side_effect=fake_run):
            self.assertFalse(term.hasSession("CIAM-1"))
            self.assertTrue(term.hasSession("CIAM-12"))

    def test_writeSlot_then_lookupSlot_round_trips_and_replaces_the_same_slots_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with mock.patch.object(term, "run", return_value=""):
                term.writeSlot("CIAM-1", "3", slots)
                term.writeSlot("CIAM-2", "3", slots)  # replaces CIAM-1's line for Desktop 3
            self.assertIsNone(term.lookupSlot("CIAM-1", slots))
            self.assertEqual(term.lookupSlot("CIAM-2", slots), "3")

    def test_writeSlot_moves_a_keys_slot_rather_than_giving_it_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with mock.patch.object(term, "run", return_value=""):
                term.writeSlot("CIAM-1", "3", slots)
                term.writeSlot("CIAM-1", "4", slots)
            self.assertEqual(term.slotEntries(slots), [("4", "CIAM-1")])

    def test_writeSlot_runs_no_command(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with mock.patch.object(term, "run", side_effect=lambda c, a, cwd: calls.append([c, *a])):
                term.writeSlot("CIAM-1", "3", slots)
        self.assertEqual(calls, [])

    def test_removeSlot_drops_only_the_matching_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with mock.patch.object(term, "run", return_value=""):
                term.writeSlot("CIAM-1", "3", slots)
                term.writeSlot("CIAM-2", "4", slots)
                term.removeSlot("CIAM-1", slots)
            self.assertIsNone(term.lookupSlot("CIAM-1", slots))
            self.assertEqual(term.lookupSlot("CIAM-2", slots), "4")

    def test_removeSlot_no_op_when_key_absent_does_not_write_or_trigger(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with open(slots, "w", encoding="utf8") as f:
                f.write("4=CIAM-2\n")
            with mock.patch.object(term, "run", side_effect=lambda c, a, cwd: calls.append([c, *a])):
                term.removeSlot("CIAM-1", slots)
            self.assertEqual(calls, [])
            self.assertEqual(term.lookupSlot("CIAM-2", slots), "4")

    # Ship and Go used to be separate terminal windows -- a "ship" window with nvim+lazygit for a
    # standalone review step, and a "go" window that ran `pr`. The review step is removed (DECIDED,
    # before Report.html exists) and shipWindow() now does what goWindow() used to: run `pr <key>`
    # visibly in a tmux window named "ship", no nvim/lazygit anywhere in its argv.
    def test_shipWindow_targets_new_select_window_calls_exactly(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                raise RuntimeError("no session")
            if command == "tmux" and args[0] == "new-window":
                return "@7\n"
            return ""

        with mock.patch.object(term, "run", side_effect=fake_run):
            term.shipWindow("CIAM-1", "/tmp/ciam-1", "/usr/local/bin/pr")
        newWindow = next(c for c in calls if c[1] == "new-window")
        selectWindow = next(c for c in calls if c[1] == "select-window")
        self.assertEqual(newWindow, ["tmux", "new-window", "-P", "-F", "#{window_id}", "-t", "=CIAM-1", "-n", "ship", "-c", "/tmp/ciam-1", "/usr/local/bin/pr", "CIAM-1"])
        self.assertEqual(selectWindow, ["tmux", "select-window", "-t", "@7"])
        self.assertFalse(any("nvim" in c or "lazygit" in c for c in calls))
        self.assertFalse(any(c[1] == "split-window" for c in calls))

class IssueWorkspaceFileTests(unittest.TestCase):
    def test_the_file_sits_in_the_workspace_folder_named_for_the_issue(self):
        self.assertEqual(term.workspaceFilePath("SK-0030", "/cache/ws"), "/cache/ws/SK-0030.code-workspace")

    def test_an_issue_with_a_legacy_file_keeps_it_so_its_open_window_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = os.path.join(tmp, "repo.worktrees", "SK-0030")
            workspaceDir = os.path.join(tmp, "ws")
            self.assertEqual(term.issueWorkspaceFilePath("SK-0030", worktree, workspaceDir), term.workspaceFilePath("SK-0030", workspaceDir))
            legacy = term.legacyWorkspaceFilePath(worktree, "SK-0030")
            os.makedirs(os.path.dirname(legacy))
            open(legacy, "w").close()
            self.assertEqual(term.issueWorkspaceFilePath("SK-0030", worktree, workspaceDir), legacy)

    def test_removing_takes_the_file_and_the_legacy_one_beside_the_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspaceDir = os.path.join(tmp, "ws")
            worktree = os.path.join(tmp, "repo.worktrees", "SK-0030")
            current = term.workspaceFilePath("SK-0030", workspaceDir)
            legacy = term.legacyWorkspaceFilePath(worktree, "SK-0030")
            for path in (current, legacy):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").close()
            self.assertEqual(term.removeWorkspaceFiles("SK-0030", worktree, workspaceDir), [])
            self.assertFalse(os.path.exists(current) or os.path.exists(legacy))
            self.assertEqual(term.removeWorkspaceFiles("SK-0030", worktree, workspaceDir), [])

    def test_a_file_that_wont_delete_is_a_warning_not_an_error(self):
        with mock.patch.object(term.os, "remove", side_effect=PermissionError(13, "Permission denied")):
            warnings = term.removeWorkspaceFiles("SK-0030", None, "/cache/ws")
        self.assertEqual(len(warnings), 1)
        self.assertRegex(warnings[0], r"^Warning: couldn't delete /cache/ws/SK-0030.code-workspace \(Permission denied\)")

    def test_writing_makes_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = term.workspaceFilePath("SK-0030", os.path.join(tmp, "new"))
            term.writeWorkspaceFile(path, {"folders": []})
            self.assertTrue(os.path.exists(path))

    def test_a_store_issue_lists_worktree_and_store_and_names_its_ticket(self):
        workspace = term.issueWorkspace("SK-0030", "/w/SK-0030", "/issues", "Work on SK-0030.", "/issues/SK-0030-x.md")
        self.assertEqual(workspace["folders"], [{"path": "/w/SK-0030"}, {"path": "/issues"}])
        settings = workspace["settings"]
        self.assertEqual(settings["terminal.integrated.env.osx"], {"PLAN_KEY": "SK-0030"})
        self.assertTrue(settings["window.title"].startswith("SK-0030"))
        self.assertEqual(settings["plan.key"], "SK-0030")
        self.assertEqual(settings["plan.worktree"], "/w/SK-0030")
        self.assertEqual(settings["plan.prompt"], "Work on SK-0030.")
        self.assertEqual(settings["plan.ticket"], "/issues/SK-0030-x.md")

    def test_a_jira_issue_has_no_ticket_setting(self):
        workspace = term.issueWorkspace("CIAM-1", "/w/CIAM-1", "/issues", "Work on Jira CIAM-1")
        self.assertNotIn("plan.ticket", workspace["settings"])

    def test_the_written_file_reads_back_as_the_same_workspace(self):
        workspace = term.issueWorkspace("CIAM-1", "/w/CIAM-1", "/issues", "p")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "CIAM-1.code-workspace")
            term.writeWorkspaceFile(path, workspace)
            with open(path, encoding="utf8") as f:
                self.assertEqual(json.load(f), workspace)
            self.assertEqual(os.listdir(tmp), ["CIAM-1.code-workspace"])


if __name__ == "__main__":
    unittest.main()
