# Which Claude sessions are waiting on a person, as Claude Code's own hooks report it. The status
# engine (lib/agents.py) reads `claude agents --json`, which doesn't call a session at a permission
# prompt `waiting`; the Notification hook that shows the system notification does fire then. So
# bin/claude-waiting, run by Claude Code's hooks, keeps one marker file per waiting session here,
# and the engine counts a marked session as waiting.
#
# Kept to os and json: the hook runs on every tool call of every Claude on the machine.
import json
import os

DEFAULT_WAITING_DIR = "~/.cache/plan/waiting"

# The Notification kinds that put a session on hold for a person, as notify_waiting.sh's matcher
# names them; auth_success and the rest don't.
WAITING_NOTIFICATIONS = ("permission_prompt", "idle_prompt", "elicitation_dialog", "agent_needs_input")

# Events that mean the session has moved on: a person answered, a tool runs, the turn ended, or the
# session closed.
MOVED_ON_EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")

# The hook events bin/install-agents registers bin/claude-waiting for.
HOOK_EVENTS = ("Notification",) + MOVED_ON_EVENTS


def waitingDir(path=None):
    return os.path.expanduser(path or DEFAULT_WAITING_DIR)


# What a hook event does to its session's marker: "mark", "clear", or None to leave it.
def markerChange(event):
    name = event.get("hook_event_name")
    if name == "Notification" and event.get("notification_type") in WAITING_NOTIFICATIONS:
        return "mark"
    if name in MOVED_ON_EVENTS:
        return "clear"
    return None


def markerPath(directory, sessionId):
    return os.path.join(directory, os.path.basename(sessionId))


# Applies one hook event. Writing or removing a marker changes the directory's mtime, which wakes
# the engine's watch loop at once.
def record(event, directory=None):
    sessionId = event.get("session_id")
    change = markerChange(event)
    if not sessionId or not change:
        return
    directory = waitingDir(directory)
    path = markerPath(directory, sessionId)
    if change == "mark":
        os.makedirs(directory, exist_ok=True)
        with open(path, "w") as marker:
            json.dump({"notification": event.get("notification_type"), "cwd": event.get("cwd")}, marker)
    elif os.path.exists(path):
        os.remove(path)


# The session ids marked waiting; none when the directory is missing.
def waitingSessions(directory=None):
    try:
        return frozenset(os.listdir(waitingDir(directory)))
    except OSError:
        return frozenset()


# The directory's mtime, which changes whenever a marker is written or removed; 0 when missing.
def changeStamp(directory=None):
    try:
        return os.stat(waitingDir(directory)).st_mtime_ns
    except OSError:
        return 0
