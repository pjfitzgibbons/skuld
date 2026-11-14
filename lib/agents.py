# The Claude status engine: which Claude session is on which issue, in what state, and the colour
# each issue's item in the Issues view should show (the pulses file, lib/pulses.py). `plan agents` (lib/cli.py) lists the sessions, and its
# LaunchAgent runs `plan agents tick --watch` to keep each issue pulsed (architecture §6.3, §7.1).
#
# It reads `claude agents --json`, the issues file, git, the waiting markers Claude Code's hooks
# keep (lib/waiting.py), once a minute each listed issue's PR (lib/reviews.py), and, for a
# listing's titles only, the store or Jira. It never writes to an issue or a session; its only
# output is the pulses file and its listing.
#
# Placing a session and choosing its issue's colour are pure functions over plain dicts, so the
# tests make sessions up; the few functions that call claude, git or the pulses file sit at the
# bottom.
import json
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass

from . import pulses, reviews, term, waiting
from .jira import KEY_PATTERN, KEY_RE
from .start import expandHome, git, run

DEFAULT_CLAUDE_COMMAND = "~/.local/bin/claude"
NO_ISSUE = "-"
TICK_SECONDS = 3
WAKE_CHECK_SECONDS = 0.25

# A worktree plan made: <repo>.worktrees/<KEY>, and anything below it.
WORKTREE = re.compile(rf"^(?P<root>.*\.worktrees/(?P<key>{KEY_PATTERN}))(?:/|$)")

# Most urgent first: the order of the listing, and which state an issue with two Claudes shows.
URGENCY = ("waiting", "done", "busy", "blocked", "idle")
PULSE_OF_STATE = {"waiting": "orange", "done": "green"}
# A Copilot review waiting on an issue's PR asks for a person too, so it outranks done.
PULSE_OF_REVIEW = "orange"
SYMBOLS = {"waiting": "⏳", "done": "◆", "busy": "●", "blocked": "■", "idle": "○"}


@dataclass(frozen=True)
class Agent:
    key: str  # "" when the session's cwd isn't in an issue's worktree
    issue: str  # key while the issues file lists it, else NO_ISSUE
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


# The issue a session is placed on: its worktree's key while the issues file lists it.
def issueOf(key, listedKeys):
    return key if key and key in listedKeys else NO_ISSUE


def agentOf(session, listedKeys, reportIsFresh, waitingIds=frozenset()):
    worktree, key = worktreeOf(session.get("cwd"))
    marked = session.get("sessionId") in waitingIds
    return Agent(
        key=key,
        issue=issueOf(key, listedKeys),
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
# listedKeys: the issues file's keys (term.issueKeys()).
def placeSessions(sessions, listedKeys, reportIsFresh, waitingIds=frozenset()):
    listed = frozenset(listedKeys)
    return mostUrgentFirst(agentOf(session, listed, reportIsFresh, waitingIds) for session in sessions)


# --- colouring ---


# The pulse every listed issue should show: orange when its PR has a Copilot review waiting
# (reviewedKeys), else its most urgent Claude's colour, or off when none of its Claudes needs a
# person, or it has no Claude at all.
def wantedPulses(agents, listedKeys, reviewedKeys=frozenset()):
    wanted = {key: "off" for key in listedKeys}
    for agent in reversed(mostUrgentFirst(agents)):
        if agent.issue in wanted:
            wanted[agent.issue] = PULSE_OF_STATE.get(agent.state, "off")
    for key in listedKeys:
        if key in reviewedKeys:
            wanted[key] = PULSE_OF_REVIEW
    return wanted


# {KEY: worktree} for each listed issue with a Claude in its worktree: the PRs to check.
def listedWorktrees(agents):
    return {agent.issue: agent.worktree for agent in agents if agent.issue != NO_ISSUE}


# The pulses a tick sends: each issue whose wanted colour differs from the one last sent, and off
# for an issue last sent a colour that has since left the issues file (it is done). An issue with no
# record counts as last sent off, since a missing line shows no colour: off for it sends nothing.
def pulsesToSend(sent, wanted):
    changed = {key: color for key, color in wanted.items() if sent.get(key, "off") != color}
    for key in leftTheIssuesFile(sent, wanted):
        changed[key] = "off"
    return changed


def leftTheIssuesFile(sent, wanted):
    return [key for key, color in sent.items() if key not in wanted and color != "off"]


# --- showing ---


# Issues by key, the unplaced (NO_ISSUE, with or without its worktree's key) last.
def issueOrder(issue):
    return (issue.startswith(NO_ISSUE), issue)


# One line: each issue and its Claudes' states as symbols, most urgent first; unplaced last.
def statusLine(agents):
    marks = {}
    for agent in mostUrgentFirst(agents):
        label = statusLabel(agent)
        marks[label] = marks.get(label, "") + SYMBOLS.get(agent.state, "?")
    shown = [f"{issue}:{marks[issue]}" for issue in sorted(marks, key=issueOrder)]
    return " ".join(shown) or "no Claude"


# The issue in the status line: "-(CIAM-9)" for a session in an unlisted issue's worktree.
def statusLabel(agent):
    if agent.issue == NO_ISSUE and agent.key:
        return f"{NO_ISSUE}({agent.key})"
    return agent.issue


def stateText(agent):
    return f"{agent.state}: {agent.waitingFor}" if agent.waitingFor else agent.state


# The issue column: the issue, or for a session in an unlisted issue's worktree the key it is in,
# as "- (CIAM-9)", so the key isn't lost.
def issueText(agent):
    if agent.issue == NO_ISSUE and agent.key:
        return f"{NO_ISSUE} ({agent.key})"
    return agent.issue


# The table, one session a line, in the order given.
def tableLines(agents):
    return [
        f"{stateText(agent):<22} {issueText(agent):<14} "
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


# --- the outside world: claude, git, the pulses file ---


def readSessions(claudeCommand=None):
    answer = run(expandHome(claudeCommand or DEFAULT_CLAUDE_COMMAND), ["agents", "--json"], os.getcwd())
    try:
        return json.loads(answer)
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


def currentAgents(issuesFile=None, claudeCommand=None, waitingDir=None):
    sessions = readSessions(claudeCommand)
    return placeSessions(sessions, term.issueKeys(issuesFile), reportIsFresh, waiting.waitingSessions(waitingDir))


# What the record holds for a key whose last write failed: it matches no colour, so the next tick
# writes the key again, whether its colour is wanted or it has left the issues file (off).
UNSENT = "unsent"
SENT_COLORS = ("orange", "green")


# --- the sent record on disk: what the engine last wrote, kept across restarts ---
#
# <pulses file>.sent holds {KEY: colour} for each issue the engine last pulsed orange or green. A
# colour a person cleared with a click has no line in the pulses file, so without this record a
# restarted engine would see "wanted orange, nothing sent" and write it back; the design pulses an
# issue again only when its own colour changes. An off or UNSENT key is not stored: both are
# written again when wanted, and a key left out of the file reads as off.

def sentFile(pulsesFile=None):
    return f"{term.resolvePulsesFile(pulsesFile)}.sent"


# The record to start from: the pulses file's colours, with the record on disk over them. With no
# record on disk (or a damaged one) that is the pulses file alone.
def loadSent(pulsesFile=None):
    return {**pulses.readPulses(pulsesFile), **readSentFile(sentFile(pulsesFile))}


def readSentFile(path):
    try:
        with open(path, "r", encoding="utf8") as f:
            stored = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {key: color for key, color in stored.items() if KEY_RE.match(key) and color in SENT_COLORS}


def storable(record):
    return {key: color for key, color in record.items() if color in SENT_COLORS}


def saveSent(record, pulsesFile=None):
    path = sentFile(pulsesFile)
    with term.locked(path):
        term.writeAtomically(path, json.dumps(storable(record), indent=2, sort_keys=True) + "\n")


# Writes each pulse and returns (the record of what was written, failure messages). A key whose
# write failed is recorded UNSENT; every other key keeps its record, so a colour cleared in the
# Issues view since it was written isn't written back.
def sendPulses(sent, toSend, pulse):
    record = dict(sent)
    failures = []
    for key, color in toSend.items():
        try:
            pulse(key, color)
            record[key] = color
        except (RuntimeError, OSError) as error:
            record[key] = UNSENT
            failures.append(str(error))
    return record, failures


# One tick: read the sessions and the issues file, and write each issue whose colour changed since
# `sent` to the pulses file. Reads no store or Jira, since it runs every few seconds. Returns (new
# record, failure messages).
def tick(sent, issuesFile=None, pulsesFile=None, claudeCommand=None, waitingDir=None, reviewedKeys=frozenset()):
    listedKeys = term.issueKeys(issuesFile)
    try:
        agents = placeSessions(readSessions(claudeCommand), listedKeys, reportIsFresh, waiting.waitingSessions(waitingDir))
    except RuntimeError as error:
        return sent, [str(error)]
    toSend = pulsesToSend(sent, wantedPulses(agents, listedKeys, reviewedKeys))
    return sendPulses(sent, toSend, lambda key, color: pulses.setPulse(key, color, pulsesFile))


# tick(), then saves the record when the tick changed what is stored. A save that fails is a
# failure message, never a crash: the engine runs on, and the next tick that changes it saves again.
def rememberingTick(sent, issuesFile=None, pulsesFile=None, claudeCommand=None, waitingDir=None, reviewedKeys=frozenset()):
    record, failures = tick(sent, issuesFile, pulsesFile, claudeCommand, waitingDir, reviewedKeys)
    if storable(record) == storable(sent):
        return record, failures
    try:
        saveSent(record, pulsesFile)
    except OSError as error:
        failures = [*failures, f"couldn't save {sentFile(pulsesFile)}: {error.strerror or error}"]
    return record, failures


# The pulses one tick wrote: each issue whose recorded colour changed from `before` to `after`.
def pulsesSent(before, after):
    return {key: color for key, color in after.items() if before.get(key) != color and color != UNSENT}


def pulseLine(written):
    return "pulsed " + ", ".join(f"{key} {color}" for key, color in sorted(written.items()))


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, file=sys.stderr, flush=True)


# Sleeps up to `seconds`, waking early when a hook writes or removes a waiting marker, so a Claude
# that stops for a person turns its issue orange within a fraction of a second.
def sleepUntilMarkersChange(seconds, waitingDir=None):
    stamp = waiting.changeStamp(waitingDir)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(max(0, min(WAKE_CHECK_SECONDS, deadline - time.monotonic())))
        if waiting.changeStamp(waitingDir) != stamp:
            return


# The keys whose PR has a Copilot review waiting, read afresh; `previous` when the sessions can't
# be read, so a failing `claude` doesn't clear a review's colour.
def checkReviews(previous, issuesFile=None, claudeCommand=None, readPr=reviews.readPr):
    try:
        agents = currentAgents(issuesFile, claudeCommand)
    except RuntimeError:
        return previous
    return reviews.reviewedKeys(listedWorktrees(agents), readPr)


def reviewLine(keys):
    return "Copilot review waiting: " + ", ".join(sorted(keys))


# checkReviews, logging each key whose review has just started waiting.
def refreshReviews(reviewed, issuesFile=None, claudeCommand=None):
    fresh = checkReviews(reviewed, issuesFile, claudeCommand)
    if fresh - reviewed:
        log(reviewLine(fresh - reviewed))
    return fresh


# The keys whose PR has a Copilot review waiting, as the last finished check found them. Its own
# thread checks once a minute, so a slow or stalled `gh` or `claude` never holds up a tick.
class ReviewSnapshot:
    def __init__(self, issuesFile=None, claudeCommand=None):
        self.issuesFile, self.claudeCommand = issuesFile, claudeCommand
        self.keys = frozenset()

    def refresh(self):
        self.keys = refreshReviews(self.keys, self.issuesFile, self.claudeCommand)

    def refreshForever(self, every=reviews.REVIEW_CHECK_SECONDS):
        while True:
            self.refresh()
            time.sleep(every)

    def startInBackground(self):
        threading.Thread(target=self.refreshForever, name="reviews", daemon=True).start()
        return self


# The LaunchAgent's loop: a tick every few seconds, or as soon as a waiting marker changes, and a
# look at each listed issue's PR once a minute on a thread of its own. Its log (stderr) gets a line when it starts, one
# when a Copilot review starts waiting, one for each tick that sends a pulse, and a failure only
# when it differs from the last one, so a quiet issue or a failure that persists adds nothing. It
# starts from the sent record on disk over the pulses file's colours (loadSent), so a line left for
# an issue no longer listed is removed and a colour a person cleared isn't written back.
def watch(issuesFile=None, pulsesFile=None, claudeCommand=None, every=TICK_SECONDS):
    log(f"watching, a tick every {every}s, PRs every {reviews.REVIEW_CHECK_SECONDS}s")
    sent, lastFailures = loadSent(pulsesFile), []
    reviewed = ReviewSnapshot(issuesFile, claudeCommand).startInBackground()
    while True:
        before = sent
        sent, failures = rememberingTick(sent, issuesFile, pulsesFile, claudeCommand, reviewedKeys=reviewed.keys)
        written = pulsesSent(before, sent)
        if written:
            log(pulseLine(written))
        if failures and failures != lastFailures:
            log("; ".join(failures))
        lastFailures = failures
        sleepUntilMarkersChange(every)
