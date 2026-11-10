# The issue window's plumbing: the VS Code workspace file, the issue-label slots file that
# SlotBar reads, opening a page in the window's integrated browser, and Ship's tmux window. All via subprocess, like
# start.py's run(); no vscode import, no new deps. Where an issue's window goes on screen is the
# WindowPlacer's job (lib/placer.py, lib/spaces.py); this module only reads and writes the files.
#
# The slots file (~/.cache/plan/workspaces by default) holds `<Desktop number>=<KEY>` lines, so
# SlotBar (macos/slotbar/) can mark Desktop 3 as CIAM-123's. See docs/architecture.md §7.1 and GUIDE.md §4.
import json
import os
from contextlib import contextmanager
from urllib.parse import quote, urlencode, urlparse

from .start import expandHome, run

VSCODE_BUNDLE_ID = "com.microsoft.VSCode"
DEFAULT_SLOTS_FILE = "~/.cache/plan/workspaces"


def resolveSlotsFile(slotsFile=None):
    return expandHome(slotsFile or DEFAULT_SLOTS_FILE)


def readSlots(slotsFile=None):
    path = resolveSlotsFile(slotsFile)
    try:
        with open(path, "r", encoding="utf8") as f:
            lines = [line for line in f.read().split("\n") if line.strip()]
    except FileNotFoundError:
        lines = []
    return path, lines


def writeSlotsLines(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp, path)  # write-then-rename, so a reader never sees a half-written file.


# The slot (a Desktop number) currently labelled for key, or None: the line ending "=<key>" in the
# slots file.
def lookupSlot(key, slotsFile=None):
    for slot, k in slotEntries(slotsFile):
        if k == key:
            return slot
    return None


# Every `<slot>=<KEY>` line, as (slot, key) pairs in file order; a line without "=" is skipped.
def slotEntries(slotsFile=None):
    _, lines = readSlots(slotsFile)
    return [tuple(line.split("=", 1)) for line in lines if "=" in line]


# Write `<slot>=<KEY>`, replacing any existing line for that slot (a slot labels one issue at a
# time) and the key's own old line (an issue has one slot), via write-temp-then-rename. SlotBar
# rereads the file on its own.
def writeSlot(key, slot, slotsFile=None):
    path, lines = readSlots(slotsFile)
    kept = [line for line in lines if not line.startswith(f"{slot}=") and not line.endswith(f"={key}")]
    kept.append(f"{slot}={key}")
    writeSlotsLines(path, kept)


# Drop key's line (if any). A no-op write is skipped.
def removeSlot(key, slotsFile=None):
    path, lines = readSlots(slotsFile)
    kept = [line for line in lines if not line.endswith(f"={key}")]
    if kept != lines:
        writeSlotsLines(path, kept)


# The issue window's workspace file is a throwaway, kept outside every repo in one folder,
# workspaceDir (config, default ~/.cache/plan/code-workspaces), named for the issue. Done deletes it.
DEFAULT_WORKSPACE_DIR = "~/.cache/plan/code-workspaces"


def workspaceFilePath(key, workspaceDir=None):
    return os.path.join(expandHome(workspaceDir or DEFAULT_WORKSPACE_DIR), f"{key}.code-workspace")


# The file Start and Open write and run `code` on. VS Code knows a window by its workspace file's
# path, so an issue whose legacy file still exists keeps it until Done deletes it: a new path would
# open a second window beside the one already open.
def issueWorkspaceFilePath(key, worktree, workspaceDir=None):
    legacy = legacyWorkspaceFilePath(worktree, key)
    if os.path.exists(legacy):
        return legacy
    return workspaceFilePath(key, workspaceDir)


# Where Start wrote the file before it moved to workspaceDir: beside the worktree, in <repo>.worktrees/.
def legacyWorkspaceFilePath(worktree, key):
    return os.path.join(os.path.dirname(worktree), f"{key}.code-workspace")


# Delete the issue's workspace files, the current one and any legacy one. A file that can't be
# deleted is a warning to print, never a failure: it's a throwaway.
def removeWorkspaceFiles(key, worktree=None, workspaceDir=None):
    paths = [workspaceFilePath(key, workspaceDir)]
    if worktree:
        paths.append(legacyWorkspaceFilePath(worktree, key))
    return [warning for warning in map(removeThrowaway, paths) if warning]


def removeThrowaway(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        return f"Warning: couldn't delete {path} ({error.strerror}); delete it by hand."
    return None


# The issue window's workspace file contents: the worktree and the ticket store as its folders, the
# key in the window title and in every terminal's environment, and the plan.* settings the
# plan-window extension reads (vscode/plan-window) to open the ticket and start Claude. ticket is
# None for a Jira issue, which has no ticket file.
def issueWorkspace(key, worktree, store, prompt, ticket=None):
    settings = {
        "window.title": f"{key}${{separator}}${{activeEditorShort}}",
        "terminal.integrated.env.osx": {"PLAN_KEY": key},
        "plan.key": key,
        "plan.worktree": worktree,
        "plan.prompt": prompt,
        "workbench.browser.openLocalhostLinks": True,  # a clicked localhost link opens in the window
    }
    if ticket:
        settings["plan.ticket"] = ticket
    return {"folders": [{"path": worktree}, {"path": store}], "settings": settings}


def writeWorkspaceFile(path, workspace):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(workspace, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# --- Opening a page in the issue window's integrated browser (architecture §7.6.1) ---
#
# VS Code sends a vscode:// link to the focused window, so `code <workspace file>` first focuses the
# issue's window, then `code --open-url` hands plan-window's /browse handler the page. The link
# carries the key, and the handler refuses it in any window whose plan.key differs, so a link that
# lands in the wrong window after focus moved is refused rather than opened.
PLAN_WINDOW_EXTENSION = "skuld.plan-window"


def isWebUrl(url):
    return urlparse(url).scheme in ("http", "https")


# The vscode:// link for url, aimed at key's window, or at whichever window has focus when key is
# None. Every character that isn't unreserved is percent-encoded, so a quote, space, & or # in url
# stays inside the url parameter.
def browseLink(url, key=None):
    query = {"url": url}
    if key:
        query["key"] = key
    return f"vscode://{PLAN_WINDOW_EXTENSION}/browse?{urlencode(query, quote_via=quote, safe='')}"


# The commands that open url in key's window, as (command, args) pairs run without a shell: focus
# the window when its workspace file is known, then send the link.
def browseCommands(url, key=None, workspaceFile=None):
    commands = [("code", [workspaceFile])] if workspaceFile else []
    commands.append(("code", ["--open-url", browseLink(url, key)]))
    return commands


# Prints url, then opens it in key's window. Every step after the print is best-effort: the printed
# URL stands on its own, and a failure leaves nothing else behind. A URL that isn't http(s) is only
# printed, since the handler would refuse it.
def showUrl(url, key=None, workspaceFile=None):
    print(url)
    if not isWebUrl(url):
        return
    try:
        for command, args in browseCommands(url, key, workspaceFile):
            run(command, args, os.getcwd())
    except (RuntimeError, OSError):
        pass


# A step of opening the issue window that failed stops Start with the step named and what to do by
# hand (GUIDE §4); whatever the earlier steps opened stays open.
@contextmanager
def step(name, byHand):
    try:
        yield
    except (RuntimeError, OSError) as error:
        raise RuntimeError(f"{name} failed: {error}\n{byHand}") from error


# M3: tmux's -t without a leading "=" prefix-matches (has-session -t CIAM-1 matches a session
# named CIAM-12 too), so every target string here uses the exact-match form.
def exact(name):
    return f"={name}"


# (verified) tmux has-session -t <name> exits 0 if the session exists, nonzero otherwise; run()
# raises on a nonzero exit, so existence is "did it raise", not its stdout. -t uses exact()
# (M3): CIAM-1 must never match a CIAM-12 session.
def hasSession(name):
    try:
        run("tmux", ["has-session", "-t", exact(name)], os.getcwd())
        return True
    except RuntimeError:
        return False


# Ship's terminal, still tmux until Ship moves to the issue window's terminal (architecture §7.2):
# `pr <key>` in a visible tmux window of the issue's session, so Peter watches it
# run rather than it happening invisibly in the background (it posts/updates the PR). This used to
# be Go's terminal, run only after a separate Ship step had opened the round files in nvim+lazygit
# for review; that review window is removed (DECIDED, before Report.html exists to replace it) and
# Ship now runs the pre-flight check (lib/ship.py's check(), called by cli.py before this) and
# `pr` in one step. The window is still named "ship" to match the merged command, not "go".
def shipWindow(key, dir, prPath):
    windowName = "ship"
    if not hasSession(key):
        run("tmux", ["new-session", "-d", "-s", key, "-c", dir], os.getcwd())
    # Select by window id, not name: a second Ship while an earlier "ship" window is still open
    # would make the name ambiguous and select-window would fail.
    windowId = run("tmux", ["new-window", "-P", "-F", "#{window_id}", "-t", exact(key), "-n", windowName, "-c", dir, prPath, key], os.getcwd()).strip()
    run("tmux", ["select-window", "-t", windowId], os.getcwd())
