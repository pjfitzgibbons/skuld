# The pulses file: each issue's colour in the Issues view of the plan-window extension
# (vscode/plan-window). `plan pulse <KEY> <orange|green|off>`, the Claude status engine
# (lib/agents.py) and Done write it; the view reads it.
#
# The file (~/.cache/plan/pulses by default) holds `<KEY>=orange` or `<KEY>=green` lines; `off`
# removes the key's line, so a missing file means no colours. A line that isn't one of those is
# ignored on read and dropped on the next write. The file persists, so a viewer that restarts
# reads every colour back.
import re

from . import term
from .issuekeys import ISSUE_KEY_PATTERN

COLORS = ("orange", "green", "off")
PULSE_LINE = re.compile(rf"^(?P<key>{ISSUE_KEY_PATTERN})=(?P<color>orange|green)$")


# `plan pulse`: the key must be one the issues file lists, since only a listed issue has an item.
def pulse(key, color, issuesFile=None, pulsesFile=None):
    checkColor(color)
    if key not in term.issueKeys(issuesFile):
        raise RuntimeError(f"{key} is not in the issues file; Start or Open it first")
    setPulse(key, color, pulsesFile)


# Sets key's colour, or removes its line for off; writes only when the file changes. The read and
# the write happen under the file's lock (term.locked()), since Done, `plan pulse` and the engine
# write it from separate processes.
def setPulse(key, color, pulsesFile=None):
    checkColor(color)
    path = term.resolvePulsesFile(pulsesFile)
    with term.locked(path):
        before = readPulses(path)
        after = withColor(before, key, color)
        if after != before:
            writePulses(after, path)


def clearPulse(key, pulsesFile=None):
    setPulse(key, "off", pulsesFile)


def withColor(pulses, key, color):
    after = {k: c for k, c in pulses.items() if k != key}
    if color != "off":
        after[key] = color
    return after


def checkColor(color):
    if color not in COLORS:
        raise RuntimeError(f'pulse: colour must be orange, green or off, not "{color}"')


# {KEY: colour} in file order, from the valid lines; a later line for a key wins.
def readPulses(pulsesFile=None):
    return {found["key"]: found["color"] for found in map(PULSE_LINE.match, readLines(pulsesFile)) if found}


def readLines(pulsesFile=None):
    try:
        with open(term.resolvePulsesFile(pulsesFile), "r", encoding="utf8") as f:
            return [line.strip() for line in f.read().split("\n") if line.strip()]
    except FileNotFoundError:
        return []


def writePulses(pulses, pulsesFile=None):
    term.writeLines(term.resolvePulsesFile(pulsesFile), [f"{key}={color}" for key, color in pulses.items()])
