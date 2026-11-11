# SpacesPlacer (lib/spaces.py) against a fake Mac: spaces.run is patched, so the helper's JSON,
# `defaults export`, osascript and code are all answered here and nothing touches the window
# server. The slots and windows files are real temp files.
import json
import os
import plistlib
import tempfile
import unittest
from unittest import mock

from lib import spaces, term
from lib.placer import placerFrom
from lib.spaces import SpacesPlacer, SpacesUnknown, parseDesktopShortcuts, parseSnapshot

# skuld-spaces' output on macOS 27.0.1, one display, four Desktops, recorded 2026-11-09.
RECORDED_HELPER_OUTPUT = (
    '{"desktops":[{"display":"37D8832A-2D66-02CA-B9F7-8F30A301B230","number":1,"showing":false},'
    '{"display":"37D8832A-2D66-02CA-B9F7-8F30A301B230","number":2,"showing":true},'
    '{"display":"37D8832A-2D66-02CA-B9F7-8F30A301B230","number":3,"showing":false},'
    '{"display":"37D8832A-2D66-02CA-B9F7-8F30A301B230","number":4,"showing":false}],'
    '"windows":[{"desktop":2,"id":12058},{"desktop":1,"id":10437},{"desktop":1,"id":12010}],"front":12058}'
)
CONTROL = 262144


def shortcutsPlist(desktops):
    hotkeys = {
        str(117 + n): {"enabled": True, "value": {"parameters": [65535, 17 + n, CONTROL], "type": "standard"}}
        for n in desktops
    }
    return plistlib.dumps({"AppleSymbolicHotKeys": hotkeys}).decode("utf8")


class ParserTests(unittest.TestCase):
    def test_the_recorded_output_gives_desktops_the_showing_one_and_each_windows_desktop(self):
        snapshot = parseSnapshot(RECORDED_HELPER_OUTPUT)
        self.assertEqual(snapshot.desktops, [1, 2, 3, 4])
        self.assertEqual(snapshot.showing, {2})
        self.assertEqual(snapshot.desktopOf(12058), 2)
        self.assertEqual(snapshot.occupied(), {1, 2})
        self.assertEqual(snapshot.front, 12058)

    def test_a_missing_skylight_symbol_is_unknown_never_a_desktop(self):
        with self.assertRaisesRegex(SpacesUnknown, "CGSCopySpacesForWindows is missing"):
            parseSnapshot('{"unknown":"CGSCopySpacesForWindows"}')

    def test_a_window_off_the_numbered_desktops_has_no_desktop(self):
        snapshot = parseSnapshot('{"desktops":[{"display":"a","number":1,"showing":true}],"windows":[{"desktop":null,"id":5}]}')
        self.assertIsNone(snapshot.desktopOf(5))
        self.assertEqual(snapshot.occupied(), set())

    def test_only_shortcuts_that_are_on_are_read_with_their_key_and_modifiers(self):
        plist = plistlib.loads(shortcutsPlist([1, 2, 3]).encode("utf8"))
        plist["AppleSymbolicHotKeys"]["120"]["enabled"] = False
        shortcuts = parseDesktopShortcuts(plistlib.dumps(plist).decode("utf8"))
        self.assertEqual(sorted(shortcuts), [1, 2])
        self.assertEqual(
            shortcuts[2].appleScript(), 'tell application "System Events" to key code 19 using {control down}'
        )


# A Mac with numbered Desktops, VS Code and browser windows on them, and the ^N shortcuts that are
# on. ^N switches the showing Desktop. `code <workspace>` on a workspace in openWindows switches to
# that window's Desktop and brings it to the front; on any other workspace it opens window
# newWindowId on landOn, or on the showing Desktop when landOn is None. secondDisplay is a Desktop
# that shows all the time on another display. A stuck Mac ignores ^N; a fullScreen one opens the new
# window on no numbered Desktop. titles maps a window id to its title.
class FakeMac:
    def __init__(self, desktops=4, windows=None, shortcutsOn=(1, 2, 3, 4), newWindowId=900, landOn=None, unknown=None,
                 openWindows=None, secondDisplay=None, stuck=False, fullScreen=False, titles=None):
        self.desktops = list(range(1, desktops + 1))
        self.windows = dict(windows or {})
        self.shortcutsOn = shortcutsOn
        self.showing = 1
        self.front = None
        self.newWindowId = newWindowId
        self.landOn = landOn
        self.unknown = unknown
        self.openWindows = dict(openWindows or {})
        self.secondDisplay = secondDisplay
        self.stuck = stuck
        self.fullScreen = fullScreen
        self.titles = dict(titles or {})
        self.calls = []

    def run(self, command, args, cwd):
        self.calls.append([os.path.basename(command), *args])
        if command.endswith("skuld-spaces"):
            return self.helper(args[0])
        if command == "defaults":
            return shortcutsPlist(self.shortcutsOn)
        if command == "osascript" and "get name of front window" in args[1]:
            return self.titles.get(self.front, "")
        if command == "osascript":
            if not self.stuck:
                self.showing = int(args[1].split("key code ")[1].split()[0]) - 17
            return ""
        if command == "code":
            self.code(args[0])
            return ""
        if command == "open":
            return ""
        raise RuntimeError(f"unexpected {command} {' '.join(args)}")

    def code(self, workspace):
        if workspace in self.openWindows:
            self.front = self.openWindows[workspace]
            if self.windows[self.front] != self.secondDisplay:
                self.showing = self.windows[self.front]
            return
        self.windows[self.newWindowId] = None if self.fullScreen else (self.landOn or self.showing)
        self.openWindows[workspace] = self.front = self.newWindowId

    def helper(self, bundleId):
        if self.unknown:
            raise RuntimeError(f'/x/skuld-spaces {bundleId}: {{"unknown":"{self.unknown}"}}')
        desktops = [{"display": "d", "number": n, "showing": n in (self.showing, self.secondDisplay)} for n in self.desktops]
        windows = [{"id": wid, "desktop": d} for wid, d in self.windows.items()]
        front = self.front if bundleId == "com.microsoft.VSCode" else None
        return json.dumps({"desktops": desktops, "windows": windows, "front": front})

    def ran(self, *prefix):
        return any(call[: len(prefix)] == list(prefix) for call in self.calls)


# A SpacesPlacer on temp slots and windows files, talking to a FakeMac.
class FakeMacCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.slots = os.path.join(tmp.name, "workspaces")
        self.windowsFile = os.path.join(tmp.name, "windows")
        self.cfg = {"slotsFile": self.slots, "windowsFile": self.windowsFile, "spacesHelper": "/x/skuld-spaces", "reservedDesktops": [1]}

    def write(self, path, text):
        with open(path, "w", encoding="utf8") as f:
            f.write(text)

    def placer(self, mac):
        placer = SpacesPlacer(self.cfg)
        placer.windowWaitMs = 0
        patches = [
            mock.patch.object(SpacesPlacer, "helperIsBuilt", return_value=True),
            mock.patch.object(spaces, "run", side_effect=mac.run),
            mock.patch.object(term, "run", return_value=""),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return placer

    def windowsOnRecord(self):
        with open(self.windowsFile, encoding="utf8") as f:
            return f.read()


class SpacesPlacerTests(FakeMacCase):
    def test_start_switches_to_a_free_desktop_opens_the_window_and_writes_its_slot(self):
        mac = FakeMac(windows={10: 1, 11: 2})
        placement = self.placer(mac).openWindow("SK-0044", "/w/SK-0044.code-workspace")
        self.assertEqual((placement.created, placement.slot, placement.where, placement.notes), (True, "3", "Desktop 3", []))
        self.assertEqual(term.lookupSlot("SK-0044", self.slots), "3")
        self.assertEqual(mac.windows[900], 3)
        switch = mac.calls.index(["osascript", "-e", 'tell application "System Events" to key code 20 using {control down}'])
        self.assertLess(switch, mac.calls.index(["code", "/w/SK-0044.code-workspace"]))
        self.assertEqual(self.windowsOnRecord(), "SK-0044=900\n")

    def test_a_window_that_lands_elsewhere_gets_the_slot_of_where_it_is(self):
        mac = FakeMac(landOn=4)
        placement = self.placer(mac).openWindow("SK-0044", "/w/SK-0044.code-workspace")
        self.assertEqual(placement.slot, "4")
        self.assertEqual(term.lookupSlot("SK-0044", self.slots), "4")
        self.assertEqual(placement.notes, ["SK-0044's window opened on Desktop 4, not Desktop 2; its slot says 4."])

    def test_a_workspace_already_open_off_the_record_is_adopted_where_it_is(self):
        mac = FakeMac(windows={70: 3}, openWindows={"/w/k.code-workspace": 70})
        placement = self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertEqual((placement.created, placement.slot), (False, "3"))
        self.assertEqual(term.lookupSlot("SK-0044", self.slots), "3")
        self.assertEqual(self.windowsOnRecord(), "SK-0044=70\n")
        self.assertEqual(placement.notes, ["SK-0044's window was already open on Desktop 3; its slot says 3."])
        self.assertEqual(list(mac.windows), [70])

    def test_an_adopted_window_on_another_issues_slot_never_takes_that_slot(self):
        self.write(self.slots, "3=SK-0001\n4=SK-0044\n")
        mac = FakeMac(windows={70: 3, 71: 3}, openWindows={"/w/k.code-workspace": 70})
        placement = self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertEqual(term.slotEntries(self.slots), [("3", "SK-0001")])
        self.assertEqual(self.windowsOnRecord(), "SK-0044=70\n")
        self.assertEqual(
            placement.notes,
            ["SK-0044's window was already open on Desktop 3, which is SK-0001's slot. Drag the window to a free Desktop, then run: plan open SK-0044"],
        )

    def test_focus_runs_code_on_the_workspace_so_the_screen_ends_on_the_window(self):
        mac = FakeMac()
        placer = self.placer(mac)
        placer.focus(placer.openWindow("SK-0044", "/w/k.code-workspace"), "/w/k.code-workspace")
        self.assertEqual(mac.calls[-1], ["code", "/w/k.code-workspace"])

    def test_open_on_a_window_already_up_never_opens_or_moves_one(self):
        self.write(self.slots, "3=SK-0044\n")
        self.write(self.windowsFile, "SK-0044=55\n")
        mac = FakeMac(windows={55: 3})
        placement = self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertEqual((placement.created, placement.slot, placement.notes), (False, "3", []))
        self.assertFalse(mac.ran("code"))
        self.assertFalse(mac.ran("osascript"))

    def test_open_rewrites_the_slot_to_the_windows_real_desktop_and_says_so(self):
        self.write(self.slots, "3=SK-0044\n")
        self.write(self.windowsFile, "SK-0044=55\n")
        mac = FakeMac(windows={55: 4})
        placement = self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertEqual(placement.slot, "4")
        self.assertEqual(term.lookupSlot("SK-0044", self.slots), "4")
        self.assertEqual(placement.notes, ["SK-0044's window is on Desktop 4, not its slot's Desktop 3; its slot says 4."])

    def test_a_missing_skylight_symbol_stops_start_before_anything_is_placed(self):
        mac = FakeMac(unknown="CGSCopyManagedDisplaySpaces")
        with self.assertRaisesRegex(RuntimeError, "reading the Desktops failed: .*Desktop is unknown"):
            self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertFalse(mac.ran("osascript"))
        self.assertFalse(mac.ran("code"))

    def test_a_slot_on_a_desktop_that_doesnt_exist_is_refused_before_code_runs(self):
        self.write(self.slots, "6=SK-0044\n")
        mac = FakeMac()
        with self.assertRaisesRegex(RuntimeError, "Desktop 6 doesn't exist"):
            self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertFalse(mac.ran("code"))

    def test_a_slot_whose_shortcut_is_off_is_refused_before_code_runs(self):
        self.write(self.slots, "3=SK-0044\n")
        mac = FakeMac(shortcutsOn=(1, 2))
        with self.assertRaisesRegex(RuntimeError, "Desktop 3's shortcut is off"):
            self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertFalse(mac.ran("code"))

    def test_free_desktops_without_a_shortcut_are_named(self):
        mac = FakeMac(shortcutsOn=(1,))
        with self.assertRaisesRegex(RuntimeError, 'turn on "Switch to Desktop" 2, 3, 4'):
            self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertFalse(mac.ran("code"))

    def test_desktops_held_by_windows_or_other_slots_are_not_free(self):
        self.write(self.slots, "3=SK-0001\n")
        mac = FakeMac(windows={10: 2}, shortcutsOn=(1, 2, 3))
        with self.assertRaisesRegex(RuntimeError, "no free Desktop has its shortcut on: .* 4 "):
            self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")

    def test_a_desktop_that_never_shows_stops_start_before_code_runs(self):
        mac = FakeMac(stuck=True)
        with self.assertRaisesRegex(RuntimeError, "switching to Desktop 2 failed: Desktop 2 didn't show"):
            self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertFalse(mac.ran("code"))

    def test_with_a_desktop_per_display_the_switch_is_seen_on_either(self):
        mac = FakeMac(secondDisplay=4)
        placement = self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertEqual((placement.created, placement.slot), (True, "2"))

    def test_a_window_already_open_on_another_displays_desktop_is_adopted(self):
        mac = FakeMac(windows={70: 4}, openWindows={"/w/k.code-workspace": 70}, secondDisplay=4)
        placement = self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertEqual((placement.created, placement.slot), (False, "4"))
        self.assertEqual(self.windowsOnRecord(), "SK-0044=70\n")

    def test_an_issue_window_already_in_front_on_another_display_is_adopted_by_its_title(self):
        mac = FakeMac(windows={70: 4}, openWindows={"/w/k.code-workspace": 70}, secondDisplay=4,
                      titles={70: "SK-0044 - spaces.py"})
        mac.front = 70
        placement = self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertEqual((placement.created, placement.slot), (False, "4"))
        self.assertEqual(self.windowsOnRecord(), "SK-0044=70\n")

    def test_another_issues_window_in_front_is_never_adopted(self):
        mac = FakeMac(windows={70: 4}, openWindows={"/w/k.code-workspace": 70}, secondDisplay=4,
                      titles={70: "SK-00440 - spaces.py"})
        mac.front = 70
        with self.assertRaisesRegex(RuntimeError, "none was brought forward"):
            self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")

    def test_a_title_names_the_issue_only_when_its_key_is_whole(self):
        self.assertTrue(spaces.titleNamesIssue("SK-1 - x", "SK-1"))
        self.assertFalse(spaces.titleNamesIssue("Claude Code - SK-1 - x", "SK-1"))
        self.assertFalse(spaces.titleNamesIssue("", "SK-1"))
        self.assertFalse(spaces.titleNamesIssue("SK-12 - x", "SK-1"))
        self.assertFalse(spaces.titleNamesIssue("SK-2 - SK-1-notes.md", "SK-1"))

    def test_a_new_window_on_no_numbered_desktop_stops_start(self):
        mac = FakeMac(fullScreen=True)
        with self.assertRaisesRegex(RuntimeError, "isn't on a numbered Desktop .* plan open SK-0044"):
            self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertIsNone(term.lookupSlot("SK-0044", self.slots))

    def test_open_on_a_full_screen_window_stops_and_keeps_the_slot(self):
        self.write(self.slots, "3=SK-0044\n")
        self.write(self.windowsFile, "SK-0044=55\n")
        mac = FakeMac(windows={55: None})
        with self.assertRaisesRegex(RuntimeError, "take it out of full screen"):
            self.placer(mac).openWindow("SK-0044", "/w/k.code-workspace")
        self.assertEqual(term.lookupSlot("SK-0044", self.slots), "3")

    def test_sweep_drops_slots_whose_window_is_gone_and_keeps_the_rest(self):
        self.write(self.slots, "2=SK-0001\n3=SK-0002\n4=SK-0003\n")
        self.write(self.windowsFile, "SK-0001=55\nSK-0002=56\n")
        mac = FakeMac(windows={55: 2, 77: 4})  # SK-0002's window is gone; SK-0003 has no record
        self.placer(mac).sweep()
        self.assertEqual(term.slotEntries(self.slots), [("2", "SK-0001"), ("4", "SK-0003")])
        self.assertEqual(self.windowsOnRecord(), "SK-0001=55\n")

    def test_sweep_drops_nothing_when_the_desktops_are_unknown(self):
        self.write(self.slots, "2=SK-0001\n")
        self.placer(FakeMac(unknown="CGSMainConnectionID")).sweep()
        self.assertEqual(term.lookupSlot("SK-0001", self.slots), "2")

    def test_slot_labels_show_both_desktops_when_the_window_is_elsewhere(self):
        self.write(self.slots, "2=SK-0001\n3=SK-0002\n")
        self.write(self.windowsFile, "SK-0001=55\nSK-0002=56\n")
        labels = self.placer(FakeMac(windows={55: 2, 56: 4})).slotLabels()
        self.assertEqual(labels, {"SK-0001": "D2", "SK-0002": "D3, window on D4"})


class HelperBuildTests(unittest.TestCase):
    def test_an_unbuilt_configured_helper_says_how_to_build_it(self):
        with self.assertRaisesRegex(RuntimeError, "isn't built; run bin/build-spaces"):
            SpacesPlacer({"spacesHelper": "/nowhere/skuld-spaces"}).readSpaces()

    def test_the_default_helper_is_built_when_missing_before_it_runs(self):
        calls = []
        built = mock.patch.object(SpacesPlacer, "helperIsBuilt", side_effect=[False, True])
        fake = mock.patch.object(spaces, "run", side_effect=lambda c, a, cwd: calls.append(c) or RECORDED_HELPER_OUTPUT)
        with built, fake:
            SpacesPlacer({}).readSpaces()
        self.assertEqual(calls, [spaces.BUILD_HELPER, spaces.DEFAULT_HELPER])

    def test_the_default_helper_is_rebuilt_when_older_than_its_source(self):
        with mock.patch.object(SpacesPlacer, "helperIsBuilt", return_value=True), mock.patch.object(
            spaces.os.path, "getmtime", side_effect=lambda path: 1 if path == spaces.DEFAULT_HELPER else 2
        ):
            self.assertTrue(SpacesPlacer({}).helperIsStale())


class PlacerFromTests(unittest.TestCase):
    def test_the_placer_is_spaces(self):
        self.assertIsInstance(placerFrom({}), SpacesPlacer)


if __name__ == "__main__":
    unittest.main()
