# The issue window's plumbing: the VS Code workspace file and window, Aerospace workspaces, the
# issue-label slots file, SketchyBar's refresh trigger, and the browser window openUrl() moves
# alongside them. All via subprocess, like start.py's run(); no vscode import, no new deps.
# Aerospace and SketchyBar invocations marked (verified) were checked live against the installed
# versions (Aerospace 0.21.3-Beta); anything else is a judgment call, called out in its comment.
#
# Workspaces are never named after the issue key: Aerospace's own hotkeys are bound to fixed
# workspace names/numbers (~/.aerospace.toml), so a plan-chosen name never collides with one of
# those bindings. Instead Start/Open pick the first numbered workspace that isn't named in the
# config's "# Workspaces:" comment and has no windows, label it for the issue in a slots file
# (~/.cache/plan/workspaces by default), and let SketchyBar read that file to show "3 #CIAM-123" on
# the bar. See docs/architecture.md §7.1 and GUIDE.md §4.
import json
import os
import sys
import time
from contextlib import contextmanager

from .start import expandHome, run

VSCODE_BUNDLE_ID = "com.microsoft.VSCode"
DEFAULT_AEROSPACE_CONFIG = "~/.aerospace.toml"
DEFAULT_SLOTS_FILE = "~/.cache/plan/workspaces"
DEFAULT_BROWSER_APP = "Brave Browser"
DEFAULT_BROWSER_BUNDLE_ID = "com.brave.Browser"


# openUrl always prints url, so Peter can click or copy it from the terminal even when nothing
# else happens, and never opens it in whatever the terminal's own link-click or OS default handler
# would pick (a bare `open <url>` was reported opening the wrong window entirely -- user decision
# 2026-11-05: Brave, launched explicitly via `open -na "Brave Browser" --args --new-window <url>`
# (~/.local/bin/plan-open:121), is the only way this seam ever opens a URL).
#
# User decision 2026-11-05 (replaces this port's earlier "new window only" draft): openUrl acts on
# the Brave window in the SAME Aerospace workspace as the window that ran the plan command, not always a fresh window. Target workspace, in order:
#   1. `workspace`, when the caller passes one explicitly (Start does: cmdStart runs in whatever
#      terminal invoked `plan start`, not in the new issue's own workspace, so it must name it).
#   2. In an issue window's terminal ($PLAN_KEY set), that key's slot -- covers a command typed in
#      the issue's own window after focus moved elsewhere.
#   3. Otherwise `aerospace list-workspaces --focused` -- the workspace the command was typed in.
# In that workspace: if a com.brave.Browser window is already there, focus it and open the URL
# into it as a new tab (`open -a` with no `-n`, reusing the running process -- UNVERIFIED that
# Chromium opens a URL handed to an already-running instance as a new tab in the frontmost window;
# never confirmed live). Otherwise open a fresh window (`-na ... --new-window`) and move it there,
# the same before/after window-id diff openIssueWindow uses. Either way, focus returns to
# whatever window was focused before this ran (DECIDED-BY-AGENT: the exact refocus mechanism --
# `aerospace focus --window-id <id>`, captured before and restored after -- isn't settled by any
# doc; this port picks it so the Brave/focus juggling above never steals focus from the window
# running the command). Every step here is best-effort: a failure (aerospace or Brave missing, no
# new window within the poll) is swallowed, since the printed URL above already stands on its own.
def openUrl(url, workspace=None, browserApp=None, browserBundleId=None, slotsFile=None):
    print(url)
    try:
        _openUrlInWorkspace(url, workspace, browserApp, browserBundleId, slotsFile)
    except RuntimeError:
        pass


def _openUrlInWorkspace(url, workspace, browserApp, browserBundleId, slotsFile):
    browserApp = browserApp or DEFAULT_BROWSER_APP
    browserBundleId = browserBundleId or DEFAULT_BROWSER_BUNDLE_ID
    target = workspace or resolveCallerWorkspace(slotsFile)
    if not target:
        return
    try:
        originalWindowId = focusedWindowId()
    except RuntimeError:
        originalWindowId = None
    try:
        existing = windowIdsIn(target, browserBundleId)
    except RuntimeError:
        existing = []
    if existing:
        run("aerospace", ["focus", "--window-id", str(existing[0])], os.getcwd())
        run("open", ["-a", browserApp, url], os.getcwd())
    else:
        before = appWindowIds(browserBundleId)
        run("open", ["-na", browserApp, "--args", "--new-window", url], os.getcwd())
        try:
            windowId = newAppWindowId(before, browserBundleId)
        except RuntimeError:
            windowId = None
        if windowId is not None:
            run("aerospace", ["move-node-to-workspace", "--window-id", str(windowId), target], os.getcwd())
    if originalWindowId is not None:
        run("aerospace", ["focus", "--window-id", str(originalWindowId)], os.getcwd())


# The workspace the command that's calling openUrl() was typed in, when no explicit workspace is
# given -- see openUrl()'s "Target workspace" ordering above (steps 2 and 3).
def resolveCallerWorkspace(slotsFile=None):
    key = os.environ.get("PLAN_KEY")
    workspace = lookupSlot(key, slotsFile) if key else None
    if workspace:
        return workspace
    try:
        return run("aerospace", ["list-workspaces", "--focused"], os.getcwd()).strip()
    except RuntimeError:
        return None


# Every window of bundleId presently on workspace -- openUrl()'s "is there already a Brave window
# here" check, which appWindowIds() alone can't answer since it isn't scoped to one workspace.
def windowIdsIn(workspace, bundleId):
    out = run(
        "aerospace",
        ["list-windows", "--workspace", workspace, "--app-bundle-id", bundleId, "--json"],
        os.getcwd(),
    )
    ids = [w["window-id"] for w in json.loads(out)]
    return list(dict.fromkeys(ids))


# The window Aerospace currently has focus on, so openUrl() can give focus back to it once it's
# done moving/focusing Brave's window.
def focusedWindowId():
    out = run("aerospace", ["list-windows", "--focused", "--json"], os.getcwd())
    wins = json.loads(out)
    return wins[0]["window-id"] if wins else None


# (verified) Aerospace only lists windows of one bundle id with --monitor all; without it, a
# window on another monitor than the one aerospace considers "focused" is silently missing from
# the list. openUrl's browser window and openIssueWindow's VS Code window both diff before/after
# sets of these.
def appWindowIds(bundleId):
    out = run(
        "aerospace",
        ["list-windows", "--monitor", "all", "--app-bundle-id", bundleId, "--json"],
        os.getcwd(),
    )
    ids = [w["window-id"] for w in json.loads(out)]
    return list(dict.fromkeys(ids))  # order-preserving "set"


# A new window of bundleId takes a moment to register with Aerospace, so the window a call just
# opened is found by diffing the window-id list before and after, not by matching a title. Bounded
# to 20 tries * 150ms (3s): a window that still hasn't shown up by then means something's wrong
# (or, for openUrl, simply didn't open), so this gives up and raises rather than polling forever.
def newAppWindowId(before, bundleId, tries=20, intervalMs=150):
    before_set = set(before)
    for _ in range(tries):
        after = appWindowIds(bundleId)
        added = [id for id in after if id not in before_set]
        if len(added) == 1:
            return added[0]
        if len(added) > 1:
            return added[-1]  # the most recently listed is the newest
        time.sleep(intervalMs / 1000)
    raise RuntimeError("a new window didn't show up in Aerospace within 3s")


# The config's "# Workspaces: 1 work, 2 editors, C claude." comment line names every workspace an
# Aerospace hotkey already targets (~/.aerospace.toml:97); the first word of each comma-separated
# entry is the workspace name/number. Parsed the same way ~/.local/bin/plan-open:85-93 already
# does. A missing config file names nothing, so every workspace counts as free.
def namedWorkspaces(aerospaceConfig=None):
    path = expandHome(aerospaceConfig or DEFAULT_AEROSPACE_CONFIG)
    try:
        with open(path, "r", encoding="utf8") as f:
            text = f.read()
    except OSError:
        return set()
    for line in text.split("\n"):
        if line.startswith("# Workspaces:"):
            rest = line[len("# Workspaces:"):].strip().rstrip(".")
            names = set()
            for part in rest.split(","):
                part = part.strip()
                if not part:
                    continue
                names.add(part.split()[0])
            return names
    return set()


# Does workspace currently have any window (of bundleId, when given)? (verified) `aerospace
# list-windows --workspace <ws>` prints nothing when the workspace is empty.
def windowsIn(workspace, bundleId=None):
    args = ["list-windows", "--workspace", workspace]
    if bundleId:
        args += ["--app-bundle-id", bundleId]
    return bool(run("aerospace", args, os.getcwd()).strip())


# Rule 1: the first workspace `aerospace list-workspaces --all` lists that isn't named in the
# config's "# Workspaces:" comment and has no windows. Raises if none is free -- there's no
# sensible fallback, and a config with every numbered workspace already named/occupied is a setup
# problem for Peter to fix, not one this tool should silently paper over.
def freeWorkspace(aerospaceConfig=None):
    named = namedWorkspaces(aerospaceConfig)
    out = run("aerospace", ["list-workspaces", "--all"], os.getcwd())
    for ws in out.split():
        if ws in named:
            continue
        if not windowsIn(ws):
            return ws
    raise RuntimeError("no free Aerospace workspace is available; close one, then try again")


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


# The workspace currently labelled for key, or None. Rule 3's lookup: `grep` for the line ending
# "=<key>" in the slots file, as SketchyBar's workspaces.sh plugin already reads it.
def lookupSlot(key, slotsFile=None):
    _, lines = readSlots(slotsFile)
    for line in lines:
        if "=" not in line:
            continue
        ws, k = line.split("=", 1)
        if k == key:
            return ws
    return None


# Rule 2: write `<ws>=<KEY>`, replacing any existing line for that workspace (a workspace can only
# ever label one issue at a time), via write-temp-then-rename, then refresh SketchyBar.
def writeSlot(key, workspace, slotsFile=None):
    path, lines = readSlots(slotsFile)
    kept = [line for line in lines if not line.startswith(f"{workspace}=")]
    kept.append(f"{workspace}={key}")
    writeSlotsLines(path, kept)
    triggerSketchybar()


# Rule 4: drop key's line (if any) and refresh SketchyBar. A no-op write is skipped.
def removeSlot(key, slotsFile=None):
    path, lines = readSlots(slotsFile)
    kept = [line for line in lines if not line.endswith(f"={key}")]
    if kept != lines:
        writeSlotsLines(path, kept)
        triggerSketchybar()


# Rule 5: Aerospace has no window-closed callback, so this is the cleanup for an issue abandoned
# without Done -- every slot line whose workspace now has no windows is dropped. Called at the
# start of every plan command that touches workspaces (lib/cli.py's start/open/done/list/picker).
def sweepSlots(slotsFile=None):
    path, lines = readSlots(slotsFile)
    kept = [line for line in lines if "=" in line and windowsIn(line.split("=", 1)[0])]
    if kept != lines:
        writeSlotsLines(path, kept)


# (verified) `sketchybar --trigger plan_workspaces_change` fires the custom event
# ~/.config/sketchybar/plugins/workspaces.sh listens for. If sketchybar isn't installed or isn't
# running, run() raises (an OSError from a missing binary, or a nonzero exit); this skips it
# silently rather than failing a Start/Open/Done over a bar refresh.
def triggerSketchybar():
    try:
        run("sketchybar", ["--trigger", "plan_workspaces_change"], os.getcwd())
    except RuntimeError:
        pass


# The issue window's workspace file sits beside the worktree, in <repo>.worktrees/, outside every
# repo (architecture §7.1).
def workspaceFilePath(worktree, key):
    return os.path.join(os.path.dirname(worktree), f"{key}.code-workspace")


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
    }
    if ticket:
        settings["plan.ticket"] = ticket
    return {"folders": [{"path": worktree}, {"path": store}], "settings": settings}


def writeWorkspaceFile(path, workspace):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(workspace, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# A step of opening the issue window that failed stops Start with the step named and what to do by
# hand (GUIDE §4); whatever the earlier steps opened stays open.
@contextmanager
def step(name, byHand):
    try:
        yield
    except (RuntimeError, OSError) as error:
        raise RuntimeError(f"{name} failed: {error}\n{byHand}") from error


# Rule 3 (Open reuse): key's slot, if that workspace still has windows. Otherwise a free workspace
# (rule 1), labelled for key (rule 2) -- this also covers plain Start, which never has a slot yet.
def openIssueWorkspace(key, aerospaceConfig=None, slotsFile=None):
    workspace = lookupSlot(key, slotsFile)
    if workspace and windowsIn(workspace):
        return workspace
    workspace = freeWorkspace(aerospaceConfig)
    writeSlot(key, workspace, slotsFile)
    return workspace


# Start/Open's window: the issue's VS Code window on workspaceFile, on the issue's Aerospace
# workspace. A VS Code window already on that workspace is the issue's, so Open only focuses it.
# Otherwise `code -n` opens a new one, found by diffing VS Code's window ids before and after
# (never by its title), and moved to the workspace. The plan-window extension does the rest inside
# the window: the ticket tab, Claude's terminal and the shell.
def openIssueWindow(key, workspaceFile, aerospaceConfig=None, slotsFile=None):
    byHand = f"Open it by hand: code -n {workspaceFile}"
    with step("placing the issue on an Aerospace workspace", byHand):
        workspace = openIssueWorkspace(key, aerospaceConfig, slotsFile)
    if windowsIn(workspace, VSCODE_BUNDLE_ID):
        with step("focusing the issue's VS Code window", f"Switch to workspace {workspace}."):
            run("code", [workspaceFile], os.getcwd())
        return {"created": False, "workspace": workspace}
    moveByHand = f"Move it to workspace {workspace} by hand."
    with step("opening the issue's VS Code window", byHand):
        before = appWindowIds(VSCODE_BUNDLE_ID)
        run("code", ["-n", workspaceFile], os.getcwd())
    with step("finding the new VS Code window in Aerospace", moveByHand):
        windowId = newAppWindowId(before, VSCODE_BUNDLE_ID)
    with step("moving the VS Code window to its workspace", moveByHand):
        run("aerospace", ["move-node-to-workspace", "--window-id", str(windowId), workspace], os.getcwd())
    return {"created": True, "workspace": workspace}


# Start/Open's last step: switch to the issue's workspace and focus its VS Code window, so the
# human ends up at the issue rather than back in the terminal that ran the command -- openUrl()
# returns focus to that caller window, so this runs after it. Best-effort, like
# triggerSketchybar(): Start/Open has already done its work, so a missing aerospace or an empty
# listing just leaves focus where it is.
def focusIssueWorkspace(workspace):
    try:
        run("aerospace", ["workspace", workspace], os.getcwd())
        ids = windowIdsIn(workspace, VSCODE_BUNDLE_ID)
        if ids:
            run("aerospace", ["focus", "--window-id", str(ids[0])], os.getcwd())
        elif os.environ.get("PLAN_DEBUG") == "1":
            print(f"[PLAN_DEBUG] focusIssueWorkspace: no VS Code window in workspace {workspace}", file=sys.stderr)
    except (RuntimeError, ValueError) as error:
        if os.environ.get("PLAN_DEBUG") == "1":
            print(f"[PLAN_DEBUG] focusIssueWorkspace: aerospace focus swallowed: {error}", file=sys.stderr)


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
