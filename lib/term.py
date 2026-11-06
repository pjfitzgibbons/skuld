# The terminal front end's window/pane plumbing: Ghostty windows, Aerospace workspaces, tmux
# sessions and windows, the issue-label slots file, SketchyBar's refresh trigger, and the browser
# window openUrl() moves alongside them. All via subprocess, like start.py's run(); no vscode
# import, no new deps. Every ghostty/aerospace/tmux/sketchybar invocation below is marked
# (verified) where its exact syntax was checked live against the installed versions (Ghostty
# 1.4.0, Aerospace 0.21.3-Beta, tmux 3.4); anything else is a judgment call, called out in its
# comment.
#
# Ported from lib/term.js. DECIDED (this port): workspaces are no longer named after the issue
# key -- Aerospace's own hotkeys are bound to fixed workspace names/numbers (~/.aerospace.toml),
# so a plan-chosen name never collides with one of those bindings. Instead Start/Open pick the
# first numbered workspace that isn't named in the config's "# Workspaces:" comment and has no
# windows, label it for the issue in a slots file (~/.cache/plan/workspaces by default), and let
# SketchyBar read that file to show "3 #CIAM-123" on the bar. See docs/architecture.md §7.1 and
# GUIDE.md §4.4.
import json
import os
import shlex
import shutil
import time

from .start import expandHome, run

GHOSTTY_BUNDLE_ID = "com.mitchellh.ghostty"
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
# the Brave window in the SAME Aerospace workspace as the Ghostty/tmux window that ran the plan
# command, not always a fresh window. Target workspace, in order:
#   1. `workspace`, when the caller passes one explicitly (Start does: cmdStart runs in whatever
#      terminal invoked `plan start`, not in the new issue's own workspace, so it must name it).
#   2. Inside tmux ($TMUX set), the session name, if it's a key presently in the slots file --
#      covers a command typed from the issue's own tmux session after focus moved elsewhere.
#   3. Otherwise `aerospace list-workspaces --focused` -- the workspace the command was typed in.
# In that workspace: if a com.brave.Browser window is already there, focus it and open the URL
# into it as a new tab (`open -a` with no `-n`, reusing the running process -- UNVERIFIED that
# Chromium opens a URL handed to an already-running instance as a new tab in the frontmost window;
# never confirmed live). Otherwise open a fresh window (`-na ... --new-window`) and move it there,
# the same before/after window-id diff openGhosttyWindow uses. Either way, focus returns to
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
    if os.environ.get("TMUX"):
        try:
            session = run("tmux", ["display-message", "-p", "#S"], os.getcwd()).strip()
        except RuntimeError:
            session = ""
        if session:
            ws = lookupSlot(session, slotsFile)
            if ws:
                return ws
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


# (verified) Aerospace only lists windows of one bundle id with --monitor all; without it, a
# window on another monitor than the one aerospace considers "focused" is silently missing from
# the list. Generalized from the old ghosttyWindowIds() (N6, this port) so openUrl's browser
# window and openGhosttyWindow's Ghostty window both diff before/after sets the same way.
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


# (verified) Ghostty has no `+new-window` CLI on macOS ("not supported on this platform"); the
# workaround is `open -na Ghostty.app --args -e <argv...>`, which launches a fresh process (-n)
# with its own window running the given command. The command goes in as separate argv words, not
# one string: macOS Ghostty hands -e to `login`, which execs a single string as a program name
# ("login: tmux attach -t =CIAM-163: No such file or directory", reported 2026-11-05). argv[0]
# should be an absolute path, since login doesn't search the caller's PATH.
def openGhosttyWindow(ghosttyApp, argv):
    before = appWindowIds(GHOSTTY_BUNDLE_ID)
    run("open", ["-na", ghosttyApp, "--args", "-e", *argv], os.getcwd())
    return newAppWindowId(before, GHOSTTY_BUNDLE_ID)


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


# Start/Open's terminal: a tmux session named for the issue's key, create-or-attach so a second
# Open on the same issue reuses it rather than starting a duplicate; a Claude window asking it to
# work the issue, and a plain shell window beside it. The whole thing runs inside a new Ghostty
# window moved to an Aerospace workspace, or that workspace is just focused if the window (and so
# the session) already exists.
#
# Rule 3 (Open reuse): look up key's slot; if that workspace still has windows, reuse it as-is.
# Otherwise pick a fresh free workspace (rule 1) and relabel it (rule 2) -- this also covers plain
# Start, which never has an existing slot for a new issue.
#
# Judgment call (carried over): the tmux session name is the bare Jira key (e.g. "CIAM-123"),
# matching hasSession/new-session's -t and worktreeDir's directory name, so the two are always
# found by the same string. The Claude window is named "claude", the shell "shell" (tmux
# new-window -n); names, not positions, since a dead window closed by hand shouldn't shift what
# "the Claude window" means to a later command.
def openIssueWorkspace(key, dir, ghosttyApp=None, aerospaceConfig=None, slotsFile=None, prompt=None):
    workspace = lookupSlot(key, slotsFile)
    if not workspace or not windowsIn(workspace):
        workspace = freeWorkspace(aerospaceConfig)
        writeSlot(key, workspace, slotsFile)
    if hasSession(key):
        # (verified) aerospace workspace <name> focuses it, creating it if it doesn't exist (it
        # will, since the session already does).
        run("aerospace", ["workspace", workspace], os.getcwd())
        # JP-1: a session can outlive its Ghostty window (a failed launch, a window closed by
        # hand), so a live session alone doesn't mean there's anything to see. No Ghostty window in
        # the workspace -> attach a new one. JP-7 extends this to every other missing piece.
        if not windowsIn(workspace, GHOSTTY_BUNDLE_ID):
            attachGhosttyWindow(key, workspace, ghosttyApp)
        return {"created": False, "workspace": workspace}
    # Built the same way shipWindow builds its own calls: every tmux call runs directly from this
    # process via run(), talking to the tmux server regardless of which terminal is attached to it.
    # That sidesteps the UNVERIFIED compound-command/\;-chaining/quoting question a single `-e
    # "tmux ... \; ..."` string would raise (Ghostty's -e execs its argument; whether that argument
    # gets shell-interpreted for \; and quotes to work was never confirmed live). The session is
    # created first, named windows are added to it, and only then does the new Ghostty window open,
    # simply attaching to the now-fully-set-up session.
    run("tmux", ["new-session", "-d", "-s", key, "-c", dir, "-n", "shell"], os.getcwd())
    run("tmux", ["new-window", "-t", exact(key), "-n", "claude", "-c", dir], os.getcwd())
    # prompt: what the new Claude is asked to do. A file-backed issue (JP-*) passes its own, naming
    # the issue file -- "Work on Jira JP-3" sent Claude looking for a Jira ticket (2026-11-05).
    # shlex.quote, since send-keys types this into a shell.
    claudePrompt = prompt or f"Work on Jira {key}"
    run("tmux", ["send-keys", "-t", exact(f"{key}:claude"), f"claude {shlex.quote(claudePrompt)}", "Enter"], os.getcwd())
    run("tmux", ["select-window", "-t", exact(f"{key}:claude")], os.getcwd())
    # open -na Ghostty.app --args -e <argv...> launches a fresh window running <argv>.
    # N5: missed the M3 exact-match fix at this call site -- a bare {key} here prefix-matches, so
    # CIAM-1 would attach to a CIAM-12 session instead. exact() builds the "=<name>" form.
    attachGhosttyWindow(key, workspace, ghosttyApp)
    return {"created": True, "workspace": workspace}


# A new Ghostty window attached to key's tmux session, moved to workspace.
def attachGhosttyWindow(key, workspace, ghosttyApp=None):
    windowId = openGhosttyWindow(ghosttyApp, [shutil.which("tmux") or "tmux", "attach", "-t", exact(key)])
    run("aerospace", ["move-node-to-workspace", "--window-id", str(windowId), workspace], os.getcwd())


# Start/Open's last step: switch to the issue's workspace, focus its Ghostty (tmux) window, and
# select the session's "claude" tmux window, so the human ends up at the issue's Claude rather than
# back in the terminal that ran the command -- openUrl() returns focus to that caller window, so
# this runs after it. User decisions 2026-11-05. Best-effort, like triggerSketchybar(): Start/Open
# has already done its work, so a missing aerospace/tmux, an empty listing, or a session whose
# claude window was closed by hand just leaves focus where it is.
def focusIssueWorkspace(workspace, key):
    try:
        run("tmux", ["select-window", "-t", exact(f"{key}:claude")], os.getcwd())
    except (RuntimeError, OSError):
        pass
    try:
        run("aerospace", ["workspace", workspace], os.getcwd())
        out = run(
            "aerospace",
            ["list-windows", "--workspace", workspace, "--app-bundle-id", GHOSTTY_BUNDLE_ID, "--json"],
            os.getcwd(),
        )
        ids = [w["window-id"] for w in json.loads(out or "[]")]
        if ids:
            run("aerospace", ["focus", "--window-id", str(ids[0])], os.getcwd())
    except (RuntimeError, OSError, ValueError):
        pass


# Ship's terminal: `pr <key>` in a visible tmux window of the issue's session, so Peter watches it
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


# Start/Open on a file-backed (JP-<n>) issue, user decision 2026-11-05: besides printing the
# issue's file path (in place of the Jira browse URL openUrl() would open), a "ticket" tmux window
# in the issue's session, cwd the worktree, runs `nvim <issuePath>` -- issuePath is the issues/
# copy FileIssues itself reads and writes, not whatever the worktree happens to hold. Create-if-
# missing: a second Open on the same issue reuses the window rather than opening a duplicate, the
# same rule openIssueWorkspace() already applies to the session and its claude/shell windows.
# Called before focusIssueWorkspace(), so focus still ends on the claude window, not this one.
def openTicketWindow(key, dir, issuePath):
    if not hasSession(key):
        run("tmux", ["new-session", "-d", "-s", key, "-c", dir, "-n", "shell"], os.getcwd())
    names = run("tmux", ["list-windows", "-t", exact(key), "-F", "#{window_name}"], os.getcwd()).split("\n")
    if "ticket" in names:
        return
    run("tmux", ["new-window", "-t", exact(key), "-n", "ticket", "-c", dir], os.getcwd())
    run("tmux", ["send-keys", "-t", exact(f"{key}:ticket"), f"nvim {shlex.quote(issuePath)}", "Enter"], os.getcwd())
