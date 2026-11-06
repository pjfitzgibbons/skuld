# M3: tmux's `-t <name>` (no leading "=") prefix-matches, so `has-session -t CIAM-1` also matches
# a session named CIAM-12 -- Peter's CIAM-1 session would be reported as existing just because
# CIAM-12 is running. term.py must target every tmux call with the exact-match "=name" form. Since
# term.py talks to tmux/aerospace/ghostty/sketchybar purely through start.py's run() (subprocess),
# these tests patch `lib.term.run` directly (term.py did `from .start import run`, binding its own
# name at import time -- same reason the JS original needed a fresh require() of term.js after
# mocking start.js's run: a destructured/imported binding doesn't see a later patch of the source
# module), and assert the exact argv tmux/aerospace/open would see, never spawning a real process.
# The slots file itself is a real temp file (tempfile.TemporaryDirectory), not mocked, since its
# write-temp-then-rename logic is exactly what's under test there.
#
# Ported from lib/term.test.js. Behavior changed by this port (numbered workspaces, the slots
# file, SketchyBar's trigger, and openUrl's Brave window): see lib/term.py's module docstring and
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

    def test_openIssueWorkspace_picks_a_free_workspace_writes_slot_and_targets_tmux_exactly(self):
        calls = []
        list_windows_calls = {"n": 0}

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                raise RuntimeError("no session")
            if command == "aerospace" and args[0] == "list-workspaces" and "--all" in args:
                return "3 4\n"
            if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
                return ""  # every workspace is free
            if command == "aerospace" and args[0] == "list-windows":
                list_windows_calls["n"] += 1
                # Models a new Ghostty window showing up in Aerospace right after "open" runs:
                # empty before, one window after, so newAppWindowId() resolves on its first poll.
                return "[]" if list_windows_calls["n"] == 1 else json.dumps([{"window-id": 1}])
            if command == "aerospace":
                return ""
            if command in ("open", "sketchybar"):
                return ""
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with mock.patch.object(term, "run", side_effect=fake_run):
                result = term.openIssueWorkspace("CIAM-1", "/tmp/ciam-1", "Ghostty", "/no/such/aerospace.toml", slots)
            self.assertEqual(term.lookupSlot("CIAM-1", slots), "3")
        self.assertTrue(result["created"])
        self.assertEqual(result["workspace"], "3")
        targeted = [c for c in calls if c[0] == "tmux" and "-t" in c]
        for call in targeted:
            target = call[call.index("-t") + 1]
            if call[1] in ("has-session", "new-window", "send-keys", "select-window"):
                self.assertTrue(target.startswith("="), f"expected exact-match target, got {call}")
        sendKeys = next(c for c in calls if c[1] == "send-keys")
        self.assertEqual(sendKeys[:4], ["tmux", "send-keys", "-t", "=CIAM-1:claude"])
        selectWindow = next(c for c in calls if c[1] == "select-window")
        self.assertEqual(selectWindow, ["tmux", "select-window", "-t", "=CIAM-1:claude"])
        newWindow = next(c for c in calls if c[1] == "new-window")
        self.assertEqual(newWindow[:4], ["tmux", "new-window", "-t", "=CIAM-1"])
        moveNode = next(c for c in calls if c[1] == "move-node-to-workspace")
        self.assertEqual(moveNode, ["aerospace", "move-node-to-workspace", "--window-id", "1", "3"])

    def test_openIssueWorkspace_targets_ghostty_windows_tmux_attach_exactly(self):
        # M3's exact() fix was missed at this one call site: the Ghostty window's shell command
        # built its own `tmux attach -t ${key}` string by hand instead of going through exact(),
        # so CIAM-1 would attach to a CIAM-12 session if one existed. Asserts the
        # "open ... --args -e <cmd>" call carries "=CIAM-1", not the bare key.
        calls = []
        list_windows_calls = {"n": 0}

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                raise RuntimeError("no session")
            if command == "aerospace" and args[0] == "list-workspaces" and "--all" in args:
                return "3\n"
            if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
                return ""
            if command == "aerospace" and args[0] == "list-windows":
                list_windows_calls["n"] += 1
                return "[]" if list_windows_calls["n"] == 1 else json.dumps([{"window-id": 1}])
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with mock.patch.object(term, "run", side_effect=fake_run):
                term.openIssueWorkspace("CIAM-1", "/tmp/ciam-1", "Ghostty", "/no/such/aerospace.toml", slots)
        open_call = next(c for c in calls if c[0] == "open")
        e_index = open_call.index("-e")
        self.assertNotEqual(e_index, -1)
        # Separate argv words after -e, not one string: login execs a single string as a path.
        self.assertTrue(open_call[e_index + 1].endswith("tmux"))
        self.assertEqual(open_call[e_index + 2:], ["attach", "-t", "=CIAM-1"])

    def test_openIssueWorkspace_just_focuses_workspace_when_session_already_exists(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                return ""  # exists
            if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
                return "some-window\n"  # workspace 3 (the existing slot) still has windows
            if command == "aerospace":
                return ""
            raise RuntimeError(f"unexpected {command} {' '.join(args)}")

        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with open(slots, "w", encoding="utf8") as f:
                f.write("3=CIAM-1\n")
            with mock.patch.object(term, "run", side_effect=fake_run):
                result = term.openIssueWorkspace("CIAM-1", "/tmp/ciam-1", "Ghostty", "/no/such/aerospace.toml", slots)
        self.assertFalse(result["created"])
        self.assertEqual(result["workspace"], "3")
        # The slot already names workspace 3, and windowsIn("3") (the lookup's reuse check) was
        # True: verified by the plain `aerospace workspace 3` focus call below, never touching
        # freeWorkspace/writeSlot.
        self.assertIn(["tmux", "has-session", "-t", "=CIAM-1"], calls)
        self.assertIn(["aerospace", "workspace", "3"], calls)

    def test_openIssueWorkspace_sends_custom_claude_prompt_quoted(self):
        # JP-3 (2026-11-05): "Work on Jira JP-3" sent the new Claude to Jira for a file issue.
        calls = []
        list_windows_calls = {"n": 0}

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                raise RuntimeError("no session")
            if command == "aerospace" and args[0] == "list-workspaces" and "--all" in args:
                return "3\n"
            if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
                return ""
            if command == "aerospace" and args[0] == "list-windows":
                list_windows_calls["n"] += 1
                return "[]" if list_windows_calls["n"] == 1 else json.dumps([{"window-id": 1}])
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with mock.patch.object(term, "run", side_effect=fake_run):
                term.openIssueWorkspace("JP-3", "/tmp/jp-3", "Ghostty", "/no/such/aerospace.toml", slots, "Work on JP-3: read /x/JP-3.md")
        send = next(c for c in calls if c[:2] == ["tmux", "send-keys"])
        self.assertEqual(send[4], "claude 'Work on JP-3: read /x/JP-3.md'")

    def test_openIssueWorkspace_reattaches_ghostty_when_session_has_no_window(self):
        # JP-1: CIAM-163's session outlived a failed Ghostty launch; the workspace held only the
        # Brave tab, and Open switched to it without opening a terminal.
        calls = []
        list_windows_calls = {"n": 0}

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                return ""  # exists
            if command == "aerospace" and args[0] == "list-windows" and "--workspace" in args:
                return "" if term.GHOSTTY_BUNDLE_ID in args else "brave-window\n"
            if command == "aerospace" and args[0] == "list-windows":
                list_windows_calls["n"] += 1
                return "[]" if list_windows_calls["n"] == 1 else json.dumps([{"window-id": 7}])
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            slots = os.path.join(tmp, "workspaces")
            with open(slots, "w", encoding="utf8") as f:
                f.write("3=CIAM-1\n")
            with mock.patch.object(term, "run", side_effect=fake_run):
                result = term.openIssueWorkspace("CIAM-1", "/tmp/ciam-1", "Ghostty", "/no/such/aerospace.toml", slots)
        self.assertFalse(result["created"])
        open_call = next(c for c in calls if c[0] == "open")
        self.assertEqual(open_call[open_call.index("-e") + 2:], ["attach", "-t", "=CIAM-1"])
        self.assertIn(["aerospace", "move-node-to-workspace", "--window-id", "7", "3"], calls)
        self.assertFalse(any(c[:2] == ["tmux", "new-session"] for c in calls))

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

    def test_openTicketWindow_creates_the_ticket_window_once(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                return ""
            if command == "tmux" and args[0] == "list-windows":
                return "shell\nclaude\n"
            return ""

        with mock.patch.object(term, "run", side_effect=fake_run):
            term.openTicketWindow("JP-1", "/repo.worktrees/JP-1", "/repo/issues/JP-1-slug.md")
        newWindow = next(c for c in calls if c[1] == "new-window")
        sendKeys = next(c for c in calls if c[1] == "send-keys")
        self.assertEqual(newWindow, ["tmux", "new-window", "-t", "=JP-1", "-n", "ticket", "-c", "/repo.worktrees/JP-1"])
        self.assertEqual(sendKeys, ["tmux", "send-keys", "-t", "=JP-1:ticket", "nvim /repo/issues/JP-1-slug.md", "Enter"])
        self.assertFalse(any(c[1] == "new-session" for c in calls))

    def test_openTicketWindow_reuses_an_existing_ticket_window(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                return ""
            if command == "tmux" and args[0] == "list-windows":
                return "shell\nclaude\nticket\n"
            return ""

        with mock.patch.object(term, "run", side_effect=fake_run):
            term.openTicketWindow("JP-1", "/repo.worktrees/JP-1", "/repo/issues/JP-1-slug.md")
        self.assertFalse(any(c[1] == "new-window" for c in calls))
        self.assertFalse(any(c[1] == "send-keys" for c in calls))

    # openUrl: always prints, then acts on a Brave window (never a bare `open <url>`), in
    # the SAME workspace the caller (or an explicit workspace, as Start passes) names.
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

    def test_openUrl_resolves_caller_workspace_from_tmux_session_slot_when_no_workspace_given(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "display-message":
                return "CIAM-9\n"
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
            with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-0/default,1234,0"}):
                with mock.patch.object(term, "run", side_effect=fake_run):
                    term.openUrl("https://cryoport.atlassian.net/browse/CIAM-9", slotsFile=slots)

        self.assertTrue(any(c[0] == "tmux" and c[1] == "display-message" for c in calls))

    def test_openUrl_falls_back_to_focused_workspace_outside_tmux(self):
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
            os.environ.pop("TMUX", None)
            with mock.patch.object(term, "run", side_effect=fake_run):
                term.openUrl("https://cryoport.atlassian.net/browse/CIAM-1")
        self.assertTrue(any(c[0] == "aerospace" and c[1] == "list-workspaces" for c in calls))


    def test_focusIssueWorkspace_switches_workspace_then_focuses_its_ghostty_window(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if args[0] == "list-windows":
                return json.dumps([{"window-id": 42}])
            return ""

        with mock.patch.object(term, "run", side_effect=fake_run):
            term.focusIssueWorkspace("3", "CIAM-1")
        self.assertEqual(calls[0], ["tmux", "select-window", "-t", "=CIAM-1:claude"])
        self.assertEqual(calls[1], ["aerospace", "workspace", "3"])
        self.assertEqual(calls[-1], ["aerospace", "focus", "--window-id", "42"])

    def test_focusIssueWorkspace_swallows_aerospace_failure(self):
        with mock.patch.object(term, "run", side_effect=RuntimeError("no aerospace")):
            term.focusIssueWorkspace("3", "CIAM-1")  # must not raise


if __name__ == "__main__":
    unittest.main()
