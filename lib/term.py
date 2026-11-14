# The plan window's plumbing: each issue's workspace file (settings only), the plan workspace that
# holds the ticket store's folder, always first, and every started issue's worktree, opening the
# plan window with `code`, the issues file that the Issues view and the Claude status engine read,
# opening a page in the window's integrated browser, and Ship's tmux window. All via subprocess,
# like start.py's run(); no vscode import, no new deps.
# VS Code places the window: `code <plan workspace>` opens it, or raises it wherever it is
# (ADR 0004).
#
# The issues file (~/.cache/plan/issues by default) holds one `<KEY>` per line: each issue Started
# or Opened and not yet Done. See docs/architecture.md §7.1 and GUIDE.md §4.
import fcntl
import json
import os
import re
import secrets
import stat
import sys
from contextlib import contextmanager
from urllib.parse import quote, urlencode, urlparse

from .jira import KEY_RE
from .start import expandHome, run

DEFAULT_ISSUES_FILE = "~/.cache/plan/issues"
DEFAULT_PULSES_FILE = "~/.cache/plan/pulses"


def resolveIssuesFile(issuesFile=None):
    return expandHome(issuesFile or DEFAULT_ISSUES_FILE)


def resolvePulsesFile(pulsesFile=None):
    return expandHome(pulsesFile or DEFAULT_PULSES_FILE)


# The issues file's keys, in file order, each once. A missing file is empty, and a line that isn't
# a key (blank, or left by a hand edit) is skipped, so a damaged file never stops a reader.
def issueKeys(issuesFile=None):
    try:
        with open(resolveIssuesFile(issuesFile), "r", encoding="utf8") as f:
            lines = f.read().split("\n")
    except FileNotFoundError:
        return []
    return uniqueKeys(line.strip() for line in lines)


def uniqueKeys(lines):
    return list(dict.fromkeys(line for line in lines if KEY_RE.match(line)))


# Appends key unless the file already lists it. The Issues view rereads the file on its own.
def addIssue(key, issuesFile=None):
    path = resolveIssuesFile(issuesFile)
    with locked(path):
        keys = issueKeys(path)
        if key not in keys:
            writeLines(path, [*keys, key])


# Drops key's line, writing only when the file lists it.
def removeIssue(key, issuesFile=None):
    path = resolveIssuesFile(issuesFile)
    with locked(path):
        keys = issueKeys(path)
        if key in keys:
            writeLines(path, [k for k in keys if k != key])


# --- Writing the shared files: the issues file, the pulses file and the plan workspace ---
#
# Start, Open, Done, `plan pulse` and the status engine all change these files, from separate
# processes. Each change reads the file, changes it and writes it back while holding locked(path),
# so two writers never both build on the same old contents and lose one change. Each write goes to
# a temp file of its own beside the file, then is renamed over it, so a reader never sees a
# half-written file and two writers never share a temp file.

# Holds an exclusive lock on <path>.lock for the block; it is released when the block ends, or when
# the process dies. A lock file per shared file, so each file's writers wait only for each other.
@contextmanager
def locked(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f"{path}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


# One line per item.
def writeLines(path, lines):
    writeAtomically(path, "".join(f"{line}\n" for line in lines))


# The temp file is created with 0644 (the umask applies), then given the mode the target already
# has, so a file others read stays readable; mkstemp's 0600 would otherwise replace it.
def writeAtomically(path, text):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{os.path.basename(path)}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as f:
            keepMode(f.fileno(), path)
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        removeIfThere(tmp)
        raise


def keepMode(fd, path):
    try:
        os.fchmod(fd, stat.S_IMODE(os.stat(path).st_mode))
    except FileNotFoundError:
        pass


def removeIfThere(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# --- The plan window: one VS Code window on the plan workspace, a folder per started issue ---
#
# The plan workspace (<workspaceDir>/plan.code-workspace) lists the worktree of each issue in the
# issues file, and the ticket store's folder, which is always first. Start and Open add the issue's
# folder and the store's, and Done removes only the issue's. Every write also sets the plan.* settings that tell
# plan-window where the issues file, the pulses file and the issue records are (planSettings());
# every other key, and every other setting, is kept as it is.
PLAN_WORKSPACE_NAME = "plan.code-workspace"


def planWorkspacePath(workspaceDir=None):
    return os.path.join(expandHome(workspaceDir or DEFAULT_WORKSPACE_DIR), PLAN_WORKSPACE_NAME)


# The plan workspace as a dict with a `folders` list. A missing file is an empty workspace; so is
# one that isn't a JSON object, and a `folders` that isn't a list is an empty one, so a damaged
# file never stops Start, and the next write repairs it.
def readPlanWorkspace(path):
    try:
        with open(path, "r", encoding="utf8") as f:
            workspace = json.load(f)
    except (FileNotFoundError, ValueError):
        workspace = {}
    if not isinstance(workspace, dict):
        workspace = {}
    if not isinstance(workspace.get("folders"), list):
        workspace["folders"] = []
    return workspace


# Whether folder is worktree's entry. Paths are compared resolved: git lists a worktree by its real
# path (/private/var/... on macOS) where Start may have named it through a symlink (/var/...). A
# relative folder path is relative to the folder holding the workspace file (base), as in VS Code.
def isFolderOf(folder, worktree, base):
    if not (isinstance(folder, dict) and isinstance(folder.get("path"), str)):
        return False
    return os.path.realpath(os.path.join(base, folder["path"])) == os.path.realpath(worktree)


# This checkout's bin/plan, found from this file's own location as bin/plan finds lib: the plan
# that is running. plan-window runs it for `pulse <KEY> off` (plan.command).
PLAN_COMMAND = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin", "plan")


# The settings the plan workspace carries: the plan.* ones plan-window reads, each an expanded path
# (plan.command is this checkout's bin/plan), and the one VS Code setting that sends a clicked
# localhost link to the window's integrated browser. One place to add a setting: Start, Open and Done all write these.
def planSettings(issuesFile, pulsesFile, workspaceDir):
    return {
        "plan.command": PLAN_COMMAND,
        "workbench.browser.openLocalhostLinks": True,  # a clicked localhost link opens in the window
        "plan.issuesFile": resolveIssuesFile(issuesFile),
        "plan.pulsesFile": resolvePulsesFile(pulsesFile),
        "plan.workspaceDir": expandHome(workspaceDir or DEFAULT_WORKSPACE_DIR),
    }


# Appends each of paths as a folder unless the plan workspace already lists it, and sets settings.
# `first` is a folder kept first: added when missing, moved to the front when listed further down,
# so the folder Done removes is never the one VS Code names the workspace by.
def addFolders(paths, path, settings, first=None):
    base = os.path.dirname(path)

    def add(folders):
        for added in [*([first] if first else []), *paths]:
            if not any(isFolderOf(folder, added, base) for folder in folders):
                folders = [*folders, {"path": added}]
        return movedToFront(folders, first, base) if first else folders

    with locked(path):
        updatePlanWorkspace(path, add, settings)


# folders with the entry for `first` moved to the front; the rest keep their order.
def movedToFront(folders, first, base):
    at = next(i for i, folder in enumerate(folders) if isFolderOf(folder, first, base))
    return [folders[at], *folders[:at], *folders[at + 1:]]


# Drops worktree's folder and sets settings, writing only when the plan workspace lists it, so
# Done on an issue the file doesn't list leaves the file alone.
def removeFolder(worktree, path, settings):
    base = os.path.dirname(path)
    with locked(path):
        workspace = readPlanWorkspace(path)
        if any(isFolderOf(folder, worktree, base) for folder in workspace["folders"]):
            kept = [folder for folder in workspace["folders"] if not isFolderOf(folder, worktree, base)]
            saveIfChanged(path, workspace, kept, settings)


# Rewrites the plan workspace with changeFolders applied to its folders and settings merged into
# its settings, writing only when that changes it.
def updatePlanWorkspace(path, changeFolders, settings):
    workspace = readPlanWorkspace(path)
    saveIfChanged(path, workspace, changeFolders(workspace["folders"]), settings)


def saveIfChanged(path, workspace, folders, settings):
    changed = {**workspace, "folders": folders, "settings": withSettings(workspace.get("settings"), settings)}
    if changed != workspace:
        writeWorkspaceFile(path, changed)


# A settings value that isn't an object is replaced, like a damaged `folders`.
def withSettings(existing, settings):
    return {**(existing if isinstance(existing, dict) else {}), **settings}


# Opens the plan window, or raises it wherever it is. A failure stops Start, naming the command to
# run by hand.
def openPlanWindow(planWorkspace):
    with step("opening the plan window", f"Open it by hand: code {planWorkspace}"):
        run("code", [planWorkspace], os.getcwd())


# Asks plan-window to bring key's issue forward, sending the link with `code --open-url` as browse
# does: the OS `open` hands a vscode:// link to whichever window VS Code picks. Callers run
# openPlanWindow() just before, so the plan window is already forward. Best-effort: a failure prints
# one line, and the issue can be clicked in the Issues view.
def revealIssue(key):
    command, args = sendLinkCommand(openLink(key))
    try:
        run(command, args, os.getcwd())
    except (RuntimeError, OSError) as error:
        print(f"couldn't open {key} in the plan window: {error}; click it in the Issues view", file=sys.stderr)


# The issue's record: a throwaway workspace file, kept outside every repo in one folder,
# workspaceDir (config, default ~/.cache/plan/code-workspaces), named for the issue. plan-window
# reads an issue's plan.* settings from it and nowhere else; it is never opened. Done deletes it.
DEFAULT_WORKSPACE_DIR = "~/.cache/plan/code-workspaces"


def workspaceFilePath(key, workspaceDir=None):
    return os.path.join(expandHome(workspaceDir or DEFAULT_WORKSPACE_DIR), f"{key}.code-workspace")


# Where Start wrote the file before it moved to workspaceDir: beside the worktree, in
# <repo>.worktrees/. Nothing writes it now; Done still deletes one an older Start left behind.
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


# The issue's record: only the plan.* settings the plan-window extension reads (issueRecord in
# vscode/plan-window/plan.js) to open the ticket and start Claude. No window opens on it, so it has
# no folders and no window settings. A store issue has a ticket file (plan.ticket); a Jira issue
# has none, and has its browse URL (plan.ticketUrl) instead.
def issueWorkspace(key, worktree, prompt, ticket=None, ticketUrl=None):
    settings = {
        "plan.key": key,
        "plan.worktree": worktree,
        "plan.prompt": prompt,
    }
    if ticket:
        settings["plan.ticket"] = ticket
    elif ticketUrl:
        settings["plan.ticketUrl"] = ticketUrl
    return {"settings": settings}


def writeWorkspaceFile(path, workspace):
    writeAtomically(path, json.dumps(workspace, indent=2) + "\n")


# --- Opening a page in the plan window's integrated browser (architecture §7.6.1) ---
#
# VS Code sends a vscode:// link to the focused window, so `code <plan workspace>` first focuses the
# plan window, then `code --open-url` hands plan-window's /browse handler the page, with the key of
# the issue it belongs to.
PLAN_WINDOW_EXTENSION = "skuld.plan-window"


def isWebUrl(url):
    return urlparse(url).scheme in ("http", "https")


# The vscode:// link for url, aimed at key's window, or at whichever window has focus when key is
# None. VS Code percent-decodes a link's query once before the handler reads it, so the query is
# encoded twice: after that decode, a quote, space, &, = or # in url is still encoded, and stays
# inside the url parameter.
def browseLink(url, key=None):
    params = {"url": url}
    if key:
        params["key"] = key
    return planWindowLink("browse", params)


# The vscode:// link that has plan-window bring key's issue forward, encoded as browseLink() is.
def openLink(key):
    return planWindowLink("open", {"key": key})


def planWindowLink(handler, params):
    query = urlencode(params, quote_via=quote, safe="")
    return f"vscode://{PLAN_WINDOW_EXTENSION}/{handler}?{quote(query, safe='')}"


# The commands that open url for key, as (command, args) pairs run without a shell: focus the plan
# window when its workspace file is given, then send the link.
def browseCommands(url, key=None, workspaceFile=None):
    commands = [("code", [workspaceFile])] if workspaceFile else []
    commands.append(sendLinkCommand(browseLink(url, key)))
    return commands


# The command that has VS Code hand a vscode:// link to its extension.
def sendLinkCommand(link):
    return ("code", ["--open-url", link])


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


# A step of opening the plan window that failed stops Start with the step named and what to do by
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
