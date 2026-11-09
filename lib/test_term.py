# M3: tmux's `-t <name>` (no leading "=") prefix-matches, so `has-session -t CIAM-1` also matches
# a session named CIAM-12 -- Peter's CIAM-1 session would be reported as existing just because
# CIAM-12 is running. term.py must target every tmux call with the exact-match "=name" form. Since
# term.py talks to tmux/aerospace/code/sketchybar purely through start.py's run() (subprocess),
# these tests patch `lib.term.run` directly (term.py did `from .start import run`, binding its own
# name at import time -- same reason the JS original needed a fresh require() of term.js after
# mocking start.js's run: a destructured/imported binding doesn't see a later patch of the source
# module), and assert the exact argv tmux/aerospace/open would see, never spawning a real process.
# The slots file itself is a real temp file (tempfile.TemporaryDirectory), not mocked, since its
# write-temp-then-rename logic is exactly what's under test there.
#
# Ported from lib/term.test.js. Behavior changed by this port (numbered workspaces, the slots
# file, SketchyBar's trigger, openUrl's Brave window, and the VS Code issue window): see lib/term.py's module docstring and
# docs/architecture.md §7.1/§7.6.
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

    def test_namedWorkspaces_parses_the_hash_workspaces_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfgPath = os.path.join(tmp, "aerospace.toml")
            with open(cfgPath, "w", encoding="utf8") as f:
                f.write("# Workspaces: 1 work, 2 editors, C claude, S shell.\n")
            self.assertEqual(term.namedWorkspaces(cfgPath), {"1", "2", "C", "S"})

    def test_namedWorkspaces_empty_when_config_missing(self):
        self.assertEqual(term.namedWorkspaces("/does/not/exist.toml"), set())

    def test_freeWorkspace_skips_named_and_occupied_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfgPath = os.path.join(tmp, "aerospace.toml")
            with open(cfgPath, "w", encoding="utf8") as f:
                f.write("# Workspaces: 1 work, C claude.\n")

            def fake_run(command, args, cwd):
                if command == "aerospace" and args[0] == "list-workspaces":
                    return "1 2 3 C\n"
                if command == "aerospace" and args[0] == "list-windows":
                    ws = args[args.index("--workspace") + 1]
                    return "" if ws == "3" else "some-window\n"
                raise RuntimeError(f"unexpected {command} {' '.join(args)}")

            with mock.patch.object(term, "run", side_effect=fake_run):
                self.assertEqual(term.freeWorkspace(cfgPath), "3")

    def test_freeWorkspace_raises_when_none_free(self):
        def fake_run(command, args, cwd):
            if command == "aerospace" and args[0] == "list-workspaces":
                return "1\n"
            if command == "aerospace" and args[0] == "list-windows":
                return "some-window\n"
            raise RuntimeError(f"unexpected {command} {' '.join(args)}")

        with mock.patch.object(term, "run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                term.freeWorkspace("/does/not/exist.toml")

    def test_writeSlot_then_lookupSlot_round_trips_and_replaces_same_workspace_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with mock.patch.object(term, "run", return_value=""):
                term.writeSlot("CIAM-1", "3", slots)
                term.writeSlot("CIAM-2", "3", slots)  # replaces CIAM-1's line for workspace 3
            self.assertIsNone(term.lookupSlot("CIAM-1", slots))
            self.assertEqual(term.lookupSlot("CIAM-2", slots), "3")

    def test_writeSlot_triggers_sketchybar(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with mock.patch.object(term, "run", side_effect=lambda c, a, cwd: calls.append([c, *a])):
                term.writeSlot("CIAM-1", "3", slots)
        self.assertIn(["sketchybar", "--trigger", "plan_workspaces_change"], calls)

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

    def test_sweepSlots_drops_lines_for_empty_workspaces_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with open(slots, "w", encoding="utf8") as f:
                f.write("3=CIAM-1\n4=CIAM-2\n")

            def fake_run(command, args, cwd):
                if command == "aerospace" and args[0] == "list-windows":
                    ws = args[args.index("--workspace") + 1]
                    return "a-window\n" if ws == "3" else ""
                raise RuntimeError(f"unexpected {command} {' '.join(args)}")

            with mock.patch.object(term, "run", side_effect=fake_run):
                term.sweepSlots(slots)
            self.assertEqual(term.lookupSlot("CIAM-1", slots), "3")
            self.assertIsNone(term.lookupSlot("CIAM-2", slots))

    def test_triggerSketchybar_swallows_a_missing_sketchybar(self):
        with mock.patch.object(term, "run", side_effect=RuntimeError("sketchybar: not found")):
            term.triggerSketchybar()  # must not raise

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

    def test_openUrl_always_prints_the_url(self):
        with mock.patch.object(term, "run", side_effect=RuntimeError("no aerospace")):
            with mock.patch("builtins.print") as mock_print:
                term.openUrl("https://cryoport.atlassian.net/browse/CIAM-1")
        mock_print.assert_called_once_with("https://cryoport.atlassian.net/browse/CIAM-1")

    def test_openUrl_opens_a_new_brave_window_and_moves_it_when_workspace_has_none(self):
        calls = []
        list_windows_calls = {"n": 0}

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "aerospace" and args[0] == "list-windows" and "--focused" in args:
                return json.dumps([{"window-id": 99}])
            if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
                return json.dumps([])  # no brave window on workspace 3 yet
            if command == "aerospace" and args[0] == "list-windows":
                list_windows_calls["n"] += 1
                return "[]" if list_windows_calls["n"] == 1 else json.dumps([{"window-id": 55}])
            return ""

        with mock.patch.object(term, "run", side_effect=fake_run):
            term.openUrl("https://cryoport.atlassian.net/browse/CIAM-1", workspace="3")

        openCall = next(c for c in calls if c[0] == "open")
        self.assertEqual(
            openCall,
            ["open", "-na", "Brave Browser", "--args", "--new-window", "https://cryoport.atlassian.net/browse/CIAM-1"],
        )
        self.assertIn(["aerospace", "move-node-to-workspace", "--window-id", "55", "3"], calls)
        # Focus returns to whatever was focused (window 99) before openUrl ran.
        self.assertIn(["aerospace", "focus", "--window-id", "99"], calls)

    def test_openUrl_reuses_an_existing_brave_window_as_a_new_tab(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "aerospace" and args[0] == "list-windows" and "--focused" in args:
                return json.dumps([{"window-id": 99}])
            if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
                return json.dumps([{"window-id": 7}])  # a Brave window is already on workspace 3
            return ""

        with mock.patch.object(term, "run", side_effect=fake_run):
            term.openUrl("https://cryoport.atlassian.net/browse/CIAM-1", workspace="3")

        self.assertIn(["aerospace", "focus", "--window-id", "7"], calls)
        openCall = next(c for c in calls if c[0] == "open")
        self.assertEqual(openCall, ["open", "-a", "Brave Browser", "https://cryoport.atlassian.net/browse/CIAM-1"])
        self.assertFalse(any(c[0] == "open" and "-na" in c for c in calls))
        # Focus returns to window 99 afterward.
        self.assertEqual(calls[-1], ["aerospace", "focus", "--window-id", "99"])

    def test_openUrl_resolves_caller_workspace_from_plan_keys_slot_when_no_workspace_given(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "aerospace" and args[0] == "list-windows" and "--focused" in args:
                return json.dumps([])
            if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
                self.assertIn("5", args)
                return json.dumps([{"window-id": 7}])
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with open(slots, "w", encoding="utf8") as f:
                f.write("5=CIAM-9\n")
            with mock.patch.dict(os.environ, {"PLAN_KEY": "CIAM-9"}):
                with mock.patch.object(term, "run", side_effect=fake_run):
                    term.openUrl("https://cryoport.atlassian.net/browse/CIAM-9", slotsFile=slots)

        self.assertFalse(any(c[:2] == ["aerospace", "list-workspaces"] for c in calls))

    def test_openUrl_falls_back_to_focused_workspace_outside_an_issue_window(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "aerospace" and args[0] == "list-workspaces" and "--focused" in args:
                return "6\n"
            if command == "aerospace" and args[0] == "list-windows" and "--focused" in args:
                return json.dumps([])
            if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
                self.assertIn("6", args)
                return json.dumps([{"window-id": 7}])
            return ""

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLAN_KEY", None)
            with mock.patch.object(term, "run", side_effect=fake_run):
                term.openUrl("https://cryoport.atlassian.net/browse/CIAM-1")
        self.assertTrue(any(c[0] == "aerospace" and c[1] == "list-workspaces" for c in calls))




    def test_focusIssueWorkspace_switches_workspace_then_focuses_its_vscode_window(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if args[0] == "list-windows":
                return json.dumps([{"window-id": 42}])
            return ""

        with mock.patch.object(term, "run", side_effect=fake_run):
            term.focusIssueWorkspace("3")
        self.assertEqual(calls[0], ["aerospace", "workspace", "3"])
        self.assertIn(term.VSCODE_BUNDLE_ID, calls[1])
        self.assertEqual(calls[-1], ["aerospace", "focus", "--window-id", "42"])

    def test_focusIssueWorkspace_swallows_aerospace_failure(self):
        with mock.patch.object(term, "run", side_effect=RuntimeError("no aerospace")):
            term.focusIssueWorkspace("3")  # must not raise


class IssueWorkspaceFileTests(unittest.TestCase):
    def test_the_file_sits_beside_the_worktree_outside_the_repo(self):
        self.assertEqual(
            term.workspaceFilePath("/work/skuld.worktrees/SK-0030", "SK-0030"),
            "/work/skuld.worktrees/SK-0030.code-workspace",
        )

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


# A desktop for openIssueWindow(): Aerospace workspaces with the bundle ids of their windows, and a
# `code` that adds a VS Code window (id newWindowId) when it runs with -n, or fails when told to.
class FakeDesktop:
    def __init__(self, workspaces, newWindowId=9, codeFails=False):
        self.workspaces = workspaces
        self.newWindowId = newWindowId
        self.codeFails = codeFails
        self.opened = []
        self.calls = []

    def run(self, command, args, cwd):
        self.calls.append([command, *args])
        if command == "code":
            return self.code(args)
        if command == "aerospace" and args[0] == "list-workspaces":
            return " ".join(self.workspaces) + "\n"
        if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
            return self.windowsOn(args[args.index("--workspace") + 1], args)
        if command == "aerospace" and args[0] == "list-windows":
            return json.dumps([{"window-id": id} for id in self.opened])
        return ""

    def code(self, args):
        if self.codeFails:
            raise RuntimeError("code: command not found")
        if args[0] == "-n":
            self.opened.append(self.newWindowId)
        return ""

    def windowsOn(self, workspace, args):
        bundles = self.workspaces[workspace]
        if "--app-bundle-id" in args:
            bundles = [b for b in bundles if b == args[args.index("--app-bundle-id") + 1]]
        return "\n".join(bundles)


class OpenIssueWindowTests(unittest.TestCase):
    def openWindow(self, desktop, slotsLine=None):
        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            if slotsLine:
                with open(slots, "w", encoding="utf8") as f:
                    f.write(slotsLine + "\n")
            with mock.patch.object(term, "run", side_effect=desktop.run):
                result = term.openIssueWindow("SK-0030", "/w/SK-0030.code-workspace", "/no/aerospace.toml", slots)
            return result, term.lookupSlot("SK-0030", slots)

    def test_start_opens_a_new_window_and_moves_it_to_a_free_workspace(self):
        desktop = FakeDesktop({"3": [], "4": []})
        result, slot = self.openWindow(desktop)
        self.assertEqual(result, {"created": True, "workspace": "3"})
        self.assertEqual(slot, "3")
        self.assertIn(["code", "-n", "/w/SK-0030.code-workspace"], desktop.calls)
        self.assertIn(["aerospace", "move-node-to-workspace", "--window-id", "9", "3"], desktop.calls)

    def test_open_on_a_window_already_up_only_focuses_it(self):
        desktop = FakeDesktop({"3": [term.VSCODE_BUNDLE_ID], "4": []})
        result, _ = self.openWindow(desktop, "3=SK-0030")
        self.assertEqual(result, {"created": False, "workspace": "3"})
        self.assertIn(["code", "/w/SK-0030.code-workspace"], desktop.calls)
        self.assertFalse(any(c[:2] == ["code", "-n"] for c in desktop.calls))
        self.assertFalse(any("move-node-to-workspace" in c for c in desktop.calls))

    def test_a_slot_from_before_is_reused_for_a_new_window(self):
        # L6: a workspace labelled by the tmux-era Start still holds the issue's other windows.
        desktop = FakeDesktop({"3": [], "5": ["com.brave.Browser"]})
        result, slot = self.openWindow(desktop, "5=SK-0030")
        self.assertEqual(result, {"created": True, "workspace": "5"})
        self.assertEqual(slot, "5")
        self.assertIn(["aerospace", "move-node-to-workspace", "--window-id", "9", "5"], desktop.calls)

    def test_a_failing_code_names_the_step_and_how_to_open_it_by_hand(self):
        desktop = FakeDesktop({"3": []}, codeFails=True)
        with self.assertRaises(RuntimeError) as raised:
            self.openWindow(desktop)
        message = str(raised.exception)
        self.assertIn("opening the issue's VS Code window failed: code: command not found", message)
        self.assertIn("code -n /w/SK-0030.code-workspace", message)

    def test_a_window_aerospace_never_lists_says_to_move_it_not_open_another(self):
        desktop = FakeDesktop({"3": []})
        desktop.code = lambda args: ""  # code runs, but no window appears
        with mock.patch.object(term, "newAppWindowId", side_effect=RuntimeError("didn't show up")):
            with self.assertRaises(RuntimeError) as raised:
                self.openWindow(desktop)
        message = str(raised.exception)
        self.assertIn("finding the new VS Code window in Aerospace failed", message)
        self.assertIn("Move it to workspace 3 by hand.", message)
        self.assertNotIn("code -n", message)


if __name__ == "__main__":
    unittest.main()
