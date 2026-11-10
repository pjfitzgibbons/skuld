# The Claude status engine: which Claude session is on which issue's Desktop, in what state, and the
# SlotBar pulse each Desktop should show. `plan agents` (lib/cli.py) lists the sessions, and its
# LaunchAgent runs `plan agents tick --watch` to keep each Desktop pulsed (architecture §6.3, §7.1).
#
# It reads `claude agents --json`, the slots file, git, the waiting markers Claude Code's hooks
# keep (lib/waiting.py), and, for a listing's titles only, the store or Jira. It never writes to an issue or a session; its only output is pulses and its listing.
#
# Placing a session and choosing its Desktop's colour are pure functions over plain dicts, so the
# tests make sessions up; the few functions that call claude, git or SlotBar sit at the bottom.
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass

from . import slotbar, term, waiting
from .start import expandHome, git, run

DEFAULT_CLAUDE_COMMAND = "~/.local/bin/claude"
NO_DESKTOP = "-"
TICK_SECONDS = 3
WAKE_CHECK_SECONDS = 0.25

# A worktree plan made: <repo>.worktrees/<KEY>, and anything below it.
WORKTREE = re.compile(r"^(?P<root>.*\.worktrees/(?P<key>[A-Z][A-Z0-9]*-[0-9]+))(?:/|$)")

# Most urgent first: the order of the listing, and which state a Desktop with two Claudes shows.
URGENCY = ("waiting", "done", "busy", "blocked", "idle")
PULSE_OF_STATE = {"waiting": "red", "done": "green"}
SYMBOLS = {"waiting": "⏳", "done": "◆", "busy": "●", "blocked": "■", "idle": "○"}


@dataclass(frozen=True)
class Agent:
    key: str  # "" when the session's cwd isn't in an issue's worktree
    desktop: str  # NO_DESKTOP when the key has no slot
    state: str
    waitingFor: str
    name: str
    cwd: str
    worktree: str
    title: str = ""


# --- placing ---


# (worktree root, key) for a cwd inside <repo>.worktrees/<KEY>, else ("", "").
def worktreeOf(cwd):
    found = WORKTREE.match(cwd or "")
    return (found["root"], found["key"]) if found else ("", "")


# {KEY: Desktop} from the slots file's (slot, key) pairs.
def desktopsByKey(slotEntries):
    return {key: slot for slot, key in slotEntries}


# Claude's own word for the session: `status` for an interactive one, `state` for a background one.
def claudeState(session):
    return session.get("status") or session.get("state") or "?"


# An idle Claude whose issue has a report newer than the branch's last commit has declared its
# work done, even when its hook has since marked it waiting for its next prompt. Otherwise a session
# its hook marked (`marked`) is waiting, whatever `claude agents` says. reportIsFresh(worktree, key)
# is asked only for an idle Claude, so a busy one costs no git call.
def stateOf(session, worktree, key, reportIsFresh, marked=False):
    state = claudeState(session)
    if state == "idle" and key and reportIsFresh(worktree, key):
        return "done"
    return "waiting" if marked else state


def agentOf(session, desktops, reportIsFresh, waitingIds=frozenset()):
    worktree, key = worktreeOf(session.get("cwd"))
    marked = session.get("sessionId") in waitingIds
    return Agent(
        key=key,
        desktop=desktops.get(key, NO_DESKTOP),
        state=stateOf(session, worktree, key, reportIsFresh, marked),
        waitingFor=session.get("waitingFor", ""),
        name=session.get("name", ""),
        cwd=session.get("cwd", ""),
        worktree=worktree,
    )


def urgency(state):
    return URGENCY.index(state) if state in URGENCY else len(URGENCY)


def mostUrgentFirst(agents):
    return sorted(agents, key=lambda agent: urgency(agent.state))


# waitingIds: the session ids Claude Code's hooks marked waiting (lib/waiting.py).
def placeSessions(sessions, slotEntries, reportIsFresh, waitingIds=frozenset()):
    desktops = desktopsByKey(slotEntries)
    return mostUrgentFirst(agentOf(session, desktops, reportIsFresh, waitingIds) for session in sessions)


# --- colouring ---


# The pulse every slotted Desktop should show: its most urgent Claude's colour, or off when none
# of its Claudes needs a person, or it has no Claude at all.
def wantedPulses(agents, slotEntries):
    wanted = {slot: "off" for slot, _ in slotEntries}
    for agent in reversed(mostUrgentFirst(agents)):
        if agent.desktop in wanted:
            wanted[agent.desktop] = PULSE_OF_STATE.get(agent.state, "off")
    return wanted


# The pulses a tick sends: each Desktop whose wanted colour differs from the one last sent, and off
# for a Desktop last sent a colour that has since left the slots file (its issue is done).
def pulsesToSend(sent, wanted):
    changed = {desktop: color for desktop, color in wanted.items() if sent.get(desktop) != color}
    for desktop in leftTheSlots(sent, wanted):
        changed[desktop] = "off"
    return changed


def leftTheSlots(sent, wanted):
    return [desktop for desktop, color in sent.items() if desktop not in wanted and color != "off"]


# --- showing ---


def desktopLabel(desktop):
    return NO_DESKTOP if desktop == NO_DESKTOP else f"D{desktop}"


def desktopOrder(desktop):
    return (desktop == NO_DESKTOP, int(desktop) if desktop.isdigit() else 0)


# One line: each Desktop and its Claudes' states as symbols, most urgent first; unplaced last.
def statusLine(agents):
    marks = {}
    for agent in mostUrgentFirst(agents):
        marks[agent.desktop] = marks.get(agent.desktop, "") + SYMBOLS.get(agent.state, "?")
    shown = [f"{desktopLabel(desktop)}:{marks[desktop]}" for desktop in sorted(marks, key=desktopOrder)]
    return " ".join(shown) or "no Claude"


def stateText(agent):
    return f"{agent.state}: {agent.waitingFor}" if agent.waitingFor else agent.state


# The table, one session a line, in the order given.
def tableLines(agents):
    return [
        f"{stateText(agent):<22} {desktopLabel(agent.desktop):<4} {agent.key or NO_DESKTOP:<10} "
        f"{(agent.title or os.path.basename(agent.cwd)):<60.60} {agent.name}".rstrip()
        for agent in agents
    ]


def jsonRows(agents):
    return [asdict(agent) for agent in agents]


# --- titles (a listing only; never a tick) ---


# Each agent with its issue's title, from titleOf(key); a lookup that fails leaves it blank.
def withTitles(agents, titleOf):
    titles = {}
    for key in {agent.key for agent in agents if agent.key}:
        titles[key] = quietly(titleOf, key)
    return [Agent(**{**asdict(agent), "title": titles.get(agent.key, "")}) for agent in agents]


def quietly(lookup, key):
    try:
        return lookup(key) or ""
    except (RuntimeError, OSError, KeyError, ValueError):
        return ""


# --- the outside world: claude, git, SlotBar ---


def readSessions(claudeCommand=None):
    answer = run(expandHome(claudeCommand or DEFAULT_CLAUDE_COMMAND), ["agents", "--json"], os.getcwd())
    try:
        return json.loads(answer or "[]")
    except json.JSONDecodeError as error:
        raise RuntimeError(f"claude agents --json gave no JSON: {error}") from error


def reportPath(worktree, key):
    return os.path.join(worktree, ".tmp", key, "report.html")


# The report exists and was written after the branch's last commit. Any git failure is "not fresh".
def reportIsFresh(worktree, key):
    try:
        reportTime = os.path.getmtime(reportPath(worktree, key))
        lastCommit = int(git(worktree, "log", "-1", "--format=%ct").strip() or 0)
    except (OSError, RuntimeError, ValueError):
        return False
    return reportTime > lastCommit


def currentAgents(slotsFile=None, claudeCommand=None, waitingDir=None):
    sessions = readSessions(claudeCommand)
    return placeSessions(sessions, term.slotEntries(slotsFile), reportIsFresh, waiting.waitingSessions(waitingDir))


# Sends each pulse through SlotBar and returns (the record of what was sent, failure messages).
# A failed pulse most often means SlotBar isn't running, and a SlotBar that restarts has lost every
# tint, so any failure empties the record: the next tick sends every Desktop its pulse again.
def sendPulses(sent, toSend, pulse):
    record = dict(sent)
    failures = []
    for desktop, color in toSend.items():
        try:
            pulse(desktop, color)
            record[desktop] = color
        except RuntimeError as error:
            failures.append(str(error))
    return ({} if failures else record), failures


# One tick: read the sessions and slots, and pulse each Desktop whose colour changed since `sent`.
# Reads no store or Jira, since it runs every few seconds. Returns (new record, failure messages).
def tick(sent, slotsFile=None, claudeCommand=None, pulseCommand=None, waitingDir=None):
    entries = term.slotEntries(slotsFile)
    try:
        agents = placeSessions(readSessions(claudeCommand), entries, reportIsFresh, waiting.waitingSessions(waitingDir))
    except RuntimeError as error:
        return sent, [str(error)]
    toSend = pulsesToSend(sent, wantedPulses(agents, entries))
    return sendPulses(sent, toSend, lambda desktop, color: slotbar.pulseDesktop(desktop, color, pulseCommand))


# The pulses one tick sent: each Desktop whose recorded colour changed from `before` to `after`.
def pulsesSent(before, after):
    return {desktop: color for desktop, color in after.items() if before.get(desktop) != color}


def pulseLine(pulses):
    return "pulsed " + ", ".join(f"D{desktop} {color}" for desktop, color in sorted(pulses.items()))


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, file=sys.stderr, flush=True)


# Sleeps up to `seconds`, waking early when a hook writes or removes a waiting marker, so a Claude
# that stops for a person turns its Desktop red within a fraction of a second.
def sleepUntilMarkersChange(seconds, waitingDir=None):
    stamp = waiting.changeStamp(waitingDir)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(max(0, min(WAKE_CHECK_SECONDS, deadline - time.monotonic())))
        if waiting.changeStamp(waitingDir) != stamp:
            return


# The LaunchAgent's loop: a tick every few seconds, or as soon as a waiting marker changes. Its log (stderr) gets a line when it starts,
# one for each tick that sends a pulse, and a failure only when it differs from the last one, so a
# quiet Desktop or a SlotBar that stays quit adds nothing.
def watch(slotsFile=None, claudeCommand=None, pulseCommand=None, every=TICK_SECONDS):
    log(f"watching, a tick every {every}s")
    sent, lastFailures = {}, []
    while True:
        before = sent
        sent, failures = tick(sent, slotsFile, claudeCommand, pulseCommand)
        pulses = pulsesSent(before, sent)
        if pulses:
            log(pulseLine(pulses))
        if failures and failures != lastFailures:
            log("; ".join(failures))
        lastFailures = failures
        sleepUntilMarkersChange(every)
