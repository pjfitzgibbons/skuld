# SpacesPlacer: the WindowPlacer adapter (lib/placer.py) for native macOS Desktops. SK-0043 and
# SK-0044 found what works with SIP on:
#
#   Place  switch to Desktop N with its "Switch to Desktop N" shortcut, sent through System Events,
#          then run `code <workspace file>`. A workspace that isn't open gets a new window on the
#          showing Desktop, found by window-id diff. A workspace that's already open (a window
#          from before the windows file, or after VS Code restarted with new ids) has its window
#          brought forward on its own Desktop instead, and that window is adopted.
#   Find   `code <workspace file>` focuses the window and switches to its Desktop.
#   Read   Desktops, the showing one, each window's Desktop and the frontmost window on screen, from
#          the skuld-spaces helper (macos/spaces.swift), which reads them through read-only
#          SkyLight calls. The helper is built from its source when missing or out of date.
#
# No public call moves an existing window to another Space, so this never tries. A window found on
# a Desktop other than its slot has its slot rewritten to where it is, and Open says so.
#
# The slots file holds `<Desktop number>=<KEY>`. A second file, windowsFile, holds
# `<KEY>=<window id>`, so Open and the sweep know which window is the issue's.
import json
import os
import plistlib
import time
from dataclasses import dataclass

from . import term
from .placer import Placement
from .start import expandHome, run

DEFAULT_WINDOWS_FILE = "~/.cache/plan/windows"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HELPER = os.path.join(REPO_ROOT, "macos", "build", "skuld-spaces")
HELPER_SOURCE = os.path.join(REPO_ROOT, "macos", "spaces.swift")
BUILD_HELPER = os.path.join(REPO_ROOT, "bin", "build-spaces")

# AppleSymbolicHotKeys ids for "Switch to Desktop 1" through 9, and the modifier bits their
# parameters carry.
FIRST_DESKTOP_HOTKEY = 118
LAST_SWITCHABLE_DESKTOP = 9
MODIFIERS = {131072: "shift down", 262144: "control down", 524288: "option down", 1048576: "command down"}


class SpacesUnknown(RuntimeError):
    pass


@dataclass
class Snapshot:
    desktops: list  # Desktop numbers, in ^N order
    showing: set  # the Desktops on screen, one per display when displays have separate Spaces
    windows: dict  # window id -> Desktop number, or None when not on a numbered Desktop
    front: int = None  # the app's frontmost window on screen, or None

    def windowIds(self):
        return set(self.windows)

    def desktopOf(self, windowId):
        return self.windows.get(windowId)

    def occupied(self):
        return {desktop for desktop in self.windows.values() if desktop is not None}


# The helper's one line of JSON, or {"unknown": "<symbol>"} when a SkyLight symbol is missing.
def parseSnapshot(text):
    data = json.loads(text)
    if "unknown" in data:
        raise SpacesUnknown(f"can't read Desktops: the SkyLight call {data['unknown']} is missing, so the Desktop is unknown")
    desktops = [d["number"] for d in data["desktops"]]
    showing = {d["number"] for d in data["desktops"] if d["showing"]}
    windows = {w["id"]: w["desktop"] for w in data["windows"]}
    return Snapshot(desktops, showing, windows, data.get("front"))


# A slot is kept while its issue's window exists. With no window on record (a slots file from
# before), it's kept while its Desktop has any VS Code window.
def slotIsLive(slot, key, windows, live, snapshot):
    if key in live:
        return True
    if key in windows:
        return False  # its window is on record and gone
    return slot.isdigit() and int(slot) in snapshot.occupied()


@dataclass
class Shortcut:
    keyCode: int
    modifiers: list

    def appleScript(self):
        using = ", ".join(self.modifiers)
        return f'tell application "System Events" to key code {self.keyCode} using {{{using}}}'


# {Desktop number: Shortcut} for each "Switch to Desktop N" shortcut that's turned on, from
# `defaults export com.apple.symbolichotkeys -`. A shortcut that's off, or was never set, is absent.
def parseDesktopShortcuts(plistText):
    hotkeys = plistlib.loads(plistText.encode("utf8")).get("AppleSymbolicHotKeys", {})
    shortcuts = {}
    for desktop in range(1, LAST_SWITCHABLE_DESKTOP + 1):
        entry = hotkeys.get(str(FIRST_DESKTOP_HOTKEY + desktop - 1))
        if entry and entry.get("enabled"):
            shortcuts[desktop] = shortcutFrom(entry["value"]["parameters"])
    return shortcuts


def shortcutFrom(parameters):
    _, keyCode, modifierMask = parameters
    return Shortcut(keyCode, [name for bit, name in MODIFIERS.items() if modifierMask & bit])


# The newest window id that wasn't there before, or None.
def newWindow(snapshot, before):
    added = sorted(snapshot.windowIds() - before.windowIds())
    return added[-1] if added else None


# `code` brought an open window forward: the target Desktop was switched away from, or (with a
# Desktop per display) the frontmost window on screen changed.
def broughtForward(snapshot, before, desktop):
    if snapshot.front not in snapshot.windows:
        return False
    return desktop not in snapshot.showing or snapshot.front != before.front


def offTheDesktops(key):
    return (
        f"{key}'s window isn't on a numbered Desktop (it may be full screen): "
        f"take it out of full screen, then run: plan open {key}"
    )


class SpacesPlacer:
    def __init__(self, cfg):
        self.slotsFile = cfg.get("slotsFile")
        self.windowsFile = cfg.get("windowsFile") or DEFAULT_WINDOWS_FILE
        self.helper = expandHome(cfg.get("spacesHelper") or DEFAULT_HELPER)
        self.reserved = {int(d) for d in cfg.get("reservedDesktops") or []}
        self.browserApp = cfg.get("browserApp") or "Brave Browser"
        self.windowWaitTries = 40
        self.windowWaitMs = 150

    # --- the port ---

    # If the Desktops can't be read, nothing is dropped: an unknown is never taken for "gone".
    def sweep(self):
        try:
            snapshot = self.readSpaces()
        except RuntimeError:
            return
        windows = dict(self.windowEntries())
        live = {key: wid for key, wid in windows.items() if wid in snapshot.windows}
        entries = term.slotEntries(self.slotsFile)
        kept = [(slot, key) for slot, key in entries if slotIsLive(slot, key, windows, live, snapshot)]
        if kept != entries:
            self.writeSlots(kept)
        if live != windows:
            self.writeWindows(live)

    def openWindow(self, key, workspaceFile):
        with term.step("reading the Desktops", f"Open it by hand: code {workspaceFile}"):
            snapshot = self.readSpaces()
        windowId = self.windowOf(key)
        if windowId in snapshot.windows:
            return self.reuseWindow(key, windowId, snapshot)
        return self.placeWindow(key, workspaceFile, snapshot)

    # The URL is printed first, so it stands on its own if anything after fails. Then the browser
    # opens it in a new window on the issue's Desktop, the one showing after the switch. A tab
    # would go to the browser's last-used window, which can be on any Desktop. Every step past the
    # print is best-effort.
    def showUrl(self, url, placement):
        print(url)
        try:
            self.switchTo(int(placement.slot), self.readShortcuts())
            run("open", ["-na", self.browserApp, "--args", "--new-window", url], os.getcwd())
        except (RuntimeError, OSError, ValueError, KeyError):
            pass

    # `code <workspace file>` switches to the window's Desktop and brings it forward, in front of
    # the browser showUrl() just opened beside it.
    def focus(self, placement, workspaceFile):
        try:
            run("code", [workspaceFile], os.getcwd())
        except RuntimeError:
            pass

    def slotLabels(self):
        try:
            snapshot = self.readSpaces()
        except RuntimeError:
            snapshot = None
        windows = dict(self.windowEntries())
        return {key: self.slotLabel(slot, windows.get(key), snapshot) for slot, key in term.slotEntries(self.slotsFile)}

    # --- Open on a window that's on record ---

    def reuseWindow(self, key, windowId, snapshot):
        slot = term.lookupSlot(key, self.slotsFile)
        actual = snapshot.desktopOf(windowId)
        if actual is None:
            raise RuntimeError(offTheDesktops(key))
        if str(actual) == slot:
            return Placement(False, slot, f"Desktop {slot}", [])
        moved = f"{key}'s window is on Desktop {actual}, not its slot's Desktop {slot}"
        return self.claimDesktop(key, actual, False, moved)

    # --- Start: a window on a free Desktop ---

    def placeWindow(self, key, workspaceFile, snapshot):
        shortcuts = self.readShortcuts()
        desktop = self.desktopFor(key, snapshot, shortcuts)
        byHand = f"Switch to Desktop {desktop} and open it by hand: code {workspaceFile}"
        with term.step(f"switching to Desktop {desktop}", byHand):
            self.switchTo(desktop, shortcuts)
            before = self.readSpaces()
        with term.step("opening the issue's VS Code window", byHand):
            run("code", [workspaceFile], os.getcwd())
        with term.step("finding the issue's VS Code window", f"Find it with: code {workspaceFile}"):
            windowId, landed, adopted = self.issueWindow(before, desktop)
        self.recordWindow(key, windowId)
        if landed is None:
            raise RuntimeError(offTheDesktops(key))
        if landed == desktop and not adopted:
            return self.claimDesktop(key, desktop, True, None)
        if adopted:
            return self.claimDesktop(key, landed, False, f"{key}'s window was already open on Desktop {landed}")
        return self.claimDesktop(key, landed, True, f"{key}'s window opened on Desktop {landed}, not Desktop {desktop}")

    # Writes key's slot as desktop, unless another issue's slot already names it: that slot is never
    # taken over, so key keeps no slot until its window is somewhere free. why says why the window
    # isn't where its slot was, or is None when it is.
    def claimDesktop(self, key, desktop, created, why):
        holder = self.slotHolder(desktop, key)
        if holder:
            term.removeSlot(key, self.slotsFile)
            where = why or f"{key}'s window is on Desktop {desktop}"
            notes = [f"{where}, which is {holder}'s slot. Drag the window to a free Desktop, then run: plan open {key}"]
        else:
            term.writeSlot(key, str(desktop), self.slotsFile)
            notes = [f"{why}; its slot says {desktop}."] if why else []
        return Placement(created, str(desktop), f"Desktop {desktop}", notes)

    def slotHolder(self, desktop, key):
        for slot, k in term.slotEntries(self.slotsFile):
            if slot == str(desktop) and k != key:
                return k
        return None

    # The key's own slot when its Desktop is empty of VS Code windows, else the first free one.
    # Either way the Desktop must exist and have its ^N shortcut on, checked before `code` runs.
    def desktopFor(self, key, snapshot, shortcuts):
        slot = term.lookupSlot(key, self.slotsFile)
        if slot and slot.isdigit() and int(slot) not in snapshot.occupied():
            self.refuseUnswitchable(int(slot), snapshot, shortcuts)
            return int(slot)
        return self.freeDesktop(key, snapshot, shortcuts)

    def freeDesktop(self, key, snapshot, shortcuts):
        candidates = self.unclaimedDesktops(key, snapshot)
        for desktop in candidates:
            if desktop in shortcuts:
                return desktop
        if candidates:
            off = ", ".join(str(d) for d in candidates)
            raise RuntimeError(
                f"no free Desktop has its shortcut on: turn on \"Switch to Desktop\" {off} in System Settings → "
                "Keyboard → Keyboard Shortcuts → Mission Control"
            )
        raise RuntimeError("no free Desktop: add one in Mission Control, or close an issue window, then try again")

    # Desktops that exist, aren't reserved, hold no VS Code window, and no other issue's slot names.
    def unclaimedDesktops(self, key, snapshot):
        claimed = {slot for slot, k in term.slotEntries(self.slotsFile) if k != key}
        return [
            d for d in snapshot.desktops
            if d not in self.reserved and d not in snapshot.occupied() and str(d) not in claimed
        ]

    def refuseUnswitchable(self, desktop, snapshot, shortcuts):
        if desktop not in snapshot.desktops:
            raise RuntimeError(f"Desktop {desktop} doesn't exist: add Desktops in Mission Control up to {desktop}, then try again")
        if desktop not in shortcuts:
            raise RuntimeError(
                f"Desktop {desktop}'s shortcut is off: turn on \"Switch to Desktop {desktop}\" in System Settings → "
                "Keyboard → Keyboard Shortcuts → Mission Control"
            )

    # Sends ^N and waits for the Desktop to show, so the `code` that follows opens its window there.
    # A Desktop that never shows stops here, before `code` opens a window on the wrong one.
    def switchTo(self, desktop, shortcuts):
        if desktop in self.readSpaces().showing:
            return
        run("osascript", ["-e", shortcuts[desktop].appleScript()], os.getcwd())
        for _ in range(self.windowWaitTries):
            if desktop in self.readSpaces().showing:
                return
            self.pause()
        raise RuntimeError(f"Desktop {desktop} didn't show within {self.waitSeconds():g}s of its shortcut")

    # (window id, its Desktop, adopted) for the window `code` brought up, given the snapshot taken
    # just before it ran. A new id is a new window, taken once its Desktop is known. With no new id,
    # `code` brought forward a window that was already open, and the frontmost one on screen is it.
    def issueWindow(self, before, desktop):
        for _ in range(self.windowWaitTries):
            snapshot = self.readSpaces()
            added = newWindow(snapshot, before)
            if added is not None and snapshot.desktopOf(added) is not None:
                return added, snapshot.desktopOf(added), False
            if added is None and broughtForward(snapshot, before, desktop):
                return snapshot.front, snapshot.desktopOf(snapshot.front), True
            self.pause()
        if added is not None:
            return added, None, False
        raise RuntimeError(f"no new VS Code window showed up within {self.waitSeconds():g}s, and none was brought forward")

    def waitSeconds(self):
        return self.windowWaitTries * self.windowWaitMs / 1000

    def pause(self):
        time.sleep(self.windowWaitMs / 1000)

    # --- reading the system ---

    def readSpaces(self, bundleId=term.VSCODE_BUNDLE_ID):
        self.ensureHelper()
        try:
            out = run(self.helper, [bundleId], os.getcwd())
        except RuntimeError as error:
            message = str(error)
            if '{"unknown"' in message:
                return parseSnapshot(message[message.index('{"unknown"'):])
            raise
        return parseSnapshot(out)

    # The default helper is rebuilt from macos/spaces.swift when it's missing or older than its
    # source, so a fresh checkout or a pull needs no build step. A configured helper is used as is.
    def ensureHelper(self):
        if self.helper == DEFAULT_HELPER and self.helperIsStale():
            with term.step("building the skuld-spaces helper", "Build it by hand: bin/build-spaces"):
                run(BUILD_HELPER, [], os.getcwd())
        if not self.helperIsBuilt():
            raise RuntimeError(f"{self.helper} isn't built; run bin/build-spaces")

    def helperIsStale(self):
        if not self.helperIsBuilt():
            return True
        return os.path.getmtime(self.helper) < os.path.getmtime(HELPER_SOURCE)

    def helperIsBuilt(self):
        return os.path.exists(self.helper)

    def readShortcuts(self):
        with term.step("reading the Switch to Desktop shortcuts", "Check System Settings → Keyboard → Keyboard Shortcuts → Mission Control."):
            return parseDesktopShortcuts(run("defaults", ["export", "com.apple.symbolichotkeys", "-"], os.getcwd()))

    # --- the files ---

    def slotLabel(self, slot, windowId, snapshot):
        actual = snapshot.desktopOf(windowId) if snapshot and windowId is not None else None
        if actual is not None and str(actual) != slot:
            return f"D{slot}, window on D{actual}"
        return f"D{slot}"

    def windowOf(self, key):
        return dict(self.windowEntries()).get(key)

    def windowEntries(self):
        _, lines = term.readSlots(self.windowsFile)
        entries = []
        for line in lines:
            key, _, windowId = line.partition("=")
            if windowId.isdigit():
                entries.append((key, int(windowId)))
        return entries

    def recordWindow(self, key, windowId):
        windows = dict(self.windowEntries())
        windows[key] = windowId
        self.writeWindows(windows)

    def writeWindows(self, windows):
        term.writeSlotsLines(term.resolveSlotsFile(self.windowsFile), [f"{k}={w}" for k, w in windows.items()])

    def writeSlots(self, entries):
        term.writeSlotsLines(term.resolveSlotsFile(self.slotsFile), [f"{slot}={key}" for slot, key in entries])
        term.triggerSketchybar()
