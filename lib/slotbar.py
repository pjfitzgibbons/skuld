# SlotBar's side of plan: `plan pulse <KEY> <red|green|off>` finds the issue's Desktop in the slots
# file and asks SlotBar (macos/slotbar/) to pulse it, through the slotbar-pulse command that
# bin/install-slotbar puts inside the installed bundle. SK-0041's status engine calls the same
# command. slotbar-pulse validates again and exits nonzero when SlotBar isn't running; run() turns
# that into a RuntimeError carrying its message.
import os

from . import term
from .start import expandHome, run

DEFAULT_PULSE_COMMAND = "~/Applications/SlotBar.app/Contents/MacOS/slotbar-pulse"
COLORS = ("red", "green", "off")


def pulse(key, color, slotsFile=None, pulseCommand=None):
    checkColor(color)
    slot = slotOf(key, slotsFile)
    run(expandHome(pulseCommand or DEFAULT_PULSE_COMMAND), [slot, color], os.getcwd())
    return slot


def checkColor(color):
    if color not in COLORS:
        raise RuntimeError(f'pulse: colour must be red, green or off, not "{color}"')


def slotOf(key, slotsFile=None):
    slot = term.lookupSlot(key, slotsFile)
    if slot is None:
        raise RuntimeError(f"pulse: {key} has no Desktop in the slots file; Start or Open it first")
    return slot
