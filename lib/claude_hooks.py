# Registers bin/claude-waiting in Claude Code's settings (~/.claude/settings.json) for each hook
# event lib/waiting.py names, and removes it again; bin/install-agents runs it. It changes only its
# own entries, found by the script's path, so the settings' other hooks are kept as they are, and
# running it twice adds nothing.
#   python3 lib/claude_hooks.py install|uninstall <settings file> <hook command>
import json
import os
import sys

if __package__:
    from .waiting import HOOK_EVENTS, WAITING_NOTIFICATIONS
else:  # run as a script by bin/install-agents
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    from lib.waiting import HOOK_EVENTS, WAITING_NOTIFICATIONS

SCRIPT_NAME = "claude-waiting"
HOOK_TIMEOUT_SECONDS = 5


def isOurs(entry):
    return any(SCRIPT_NAME in hook.get("command", "") for hook in entry.get("hooks", []))


def ourEntry(eventName, command):
    entry = {"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}]}
    if eventName == "Notification":
        entry = {"matcher": "|".join(WAITING_NOTIFICATIONS), **entry}
    return entry


def withoutOurs(settings):
    hooks = {
        eventName: [entry for entry in entries if not isOurs(entry)]
        for eventName, entries in settings.get("hooks", {}).items()
    }
    return {**settings, "hooks": {eventName: entries for eventName, entries in hooks.items() if entries}}


# The settings with one entry of ours per hook event, replacing any earlier one of ours.
def withOurs(settings, command):
    hooks = dict(withoutOurs(settings)["hooks"])
    for eventName in HOOK_EVENTS:
        hooks[eventName] = hooks.get(eventName, []) + [ourEntry(eventName, command)]
    return {**settings, "hooks": hooks}


def readSettings(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# Written to a temp file and renamed over the settings, so a Claude reading them never sees half.
def writeSettings(path, settings):
    temp = f"{path}.claude-waiting.tmp"
    with open(temp, "w") as f:
        f.write(json.dumps(settings, indent=2) + "\n")
    os.replace(temp, path)


def main(action, path, command=None):
    settings = readSettings(path)
    writeSettings(path, withOurs(settings, command) if action == "install" else withoutOurs(settings))


if __name__ == "__main__":
    main(*sys.argv[1:])
