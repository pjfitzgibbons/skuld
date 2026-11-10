# SlotBar's side of plan: `plan pulse <KEY> <orange|green|off>` finds the issue's Desktop in the slots
# file and asks SlotBar (macos/slotbar/) to pulse it, through the slotbar-pulse command that
# bin/install-slotbar puts inside the installed bundle. The Claude status engine (lib/agents.py)
# calls the same command. slotbar-pulse validates again and exits nonzero when SlotBar isn't
# running; run() turns that into a RuntimeError carrying its message.
import os

from . import term
from .start import expandHome, run

DEFAULT_PULSE_COMMAND = "~/Applications/SlotBar.app/Contents/MacOS/slotbar-pulse"
COLORS = ("orange", "green", "off")


def pulse(key, color, slotsFile=None, pulseCommand=None):
    checkColor(color)
    slot = slotOf(key, slotsFile)
    pulseDesktop(slot, color, pulseCommand)
    return slot


# A Desktop by number, for the status engine (lib/agents.py), which also clears a Desktop whose key
# has left the slots file.
def pulseDesktop(desktop, color, pulseCommand=None):
    checkColor(color)
    run(expandHome(pulseCommand or DEFAULT_PULSE_COMMAND), [str(desktop), color], os.getcwd())


# The running SlotBar's process id, or "" when none runs. A new id means SlotBar restarted and
# has lost every tint.
def runningId():
    try:
        found = run("pgrep", ["-x", "SlotBar"], os.getcwd()).split()
    except RuntimeError:
        return ""
    return found[0] if found else ""


def checkColor(color):
    if color not in COLORS:
        raise RuntimeError(f'pulse: colour must be orange, green or off, not "{color}"')


def slotOf(key, slotsFile=None):
    slot = term.lookupSlot(key, slotsFile)
    if slot is None:
        raise RuntimeError(f"pulse: {key} has no Desktop in the slots file; Start or Open it first")
    return slot
