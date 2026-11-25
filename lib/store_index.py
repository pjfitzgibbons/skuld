# The ticket store's INDEX.md: the open tickets in the order to work them, written from the tickets'
# and milestones' front matter. The store holds data only (docs/adr/0001-file-ticket-store.md §3), so
# this script is skuld's, and `bin/store-index` runs it on the store.
#
#     store-index [STORE]           rewrite STORE/INDEX.md
#     store-index --check [STORE]   exit 1, changing nothing, when INDEX.md is out of date
#
# STORE defaults to `issueStore` in the machine config. Each open ticket is listed under its
# milestone, after the tickets it waits on: in progress ones marked so, and each ending with the open
# tickets it still waits on, in brackets.
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from . import config as config_mod
from .fileissues import _keyFromFilename

USAGE = "usage: store-index [--check] [STORE]"
CLOSED = {"done", "wontfix"}
NO_MILESTONE = ""
LAST = float("inf")
LINE_LENGTH = 120
HEADER = """\
# Next up

Open issues in the order to work them: milestones in order, each issue after everything it waits on, then issues
with no milestone. An issue being worked is marked **in progress**, and one that waits on open issues ends with them
in brackets; the rest are ready to start. Each milestone's exit criterion is in `milestones/`. Done and wontfix
issues move to `archive/` and leave the list.

This file is written by skuld's `store-index`; run it after changing a ticket's status, milestone, ordinal or
`blocked_by`."""


@dataclass
class Ticket:
    key: str
    file: str
    title: str
    status: str
    milestone: str
    ordinal: float
    blocked_by: list[str] = field(default_factory=list)


@dataclass
class Milestone:
    id: str
    name: str
    ordinal: float


def frontMatter(text: str) -> dict[str, str]:
    """The `name: value` lines between the opening `---` lines; a `#` starts a comment."""
    found = re.match(r"---\n(.*?)\n---\n", text, re.S)
    fields = {}
    for line in found[1].splitlines() if found else []:
        name, _, value = line.partition(":")
        fields[name.strip()] = value.split("#")[0].strip()
    return fields


def titleOf(text: str) -> str:
    found = re.search(r"^# (.+)$", text, re.M)
    return found[1].strip() if found else ""


def ordinalOf(value: str) -> float:
    return float(value) if value else LAST


def keysIn(value: str) -> list[str]:
    return [key.strip() for key in value.split(",") if key.strip()]


def readTicket(path: Path) -> Ticket:
    text = path.read_text()
    fields = frontMatter(text)
    return Ticket(
        key=_keyFromFilename(path.name),
        file=f"{path.parent.name}/{path.name}",  # the link from INDEX.md, relative to the store
        title=titleOf(text),
        status=fields.get("status", "open"),
        milestone=fields.get("milestone", NO_MILESTONE),
        ordinal=ordinalOf(fields.get("ordinal", "")),
        blocked_by=keysIn(fields.get("blocked_by", "")),
    )


def readMilestone(path: Path) -> Milestone:
    fields = frontMatter(path.read_text())
    return Milestone(id=fields["id"], name=fields["name"], ordinal=ordinalOf(fields.get("ordinal", "")))


def isOpen(ticket: Ticket) -> bool:
    return ticket.status not in CLOSED


def waitingOn(ticket: Ticket, tickets: dict[str, Ticket]) -> list[str]:
    """The tickets it waits on that aren't closed. A key with no ticket counts as open."""
    return [key for key in ticket.blocked_by if key not in tickets or isOpen(tickets[key])]


def workOrder(group: list[Ticket]) -> list[Ticket]:
    """By ordinal, except that a ticket comes after any ticket in the group it waits on."""
    left = sorted(group, key=lambda t: (t.ordinal, t.key))
    placed: list[Ticket] = []
    while left:
        keysLeft = {t.key for t in left}
        free = next((t for t in left if not keysLeft & set(t.blocked_by)), left[0])
        placed.append(free)
        left.remove(free)
    return placed


def progressMark(ticket: Ticket) -> str:
    return "**in progress** · " if ticket.status == "in-progress" else ""


def blockersMark(ticket: Ticket, tickets: dict[str, Ticket]) -> str:
    waits = waitingOn(ticket, tickets)
    return f" [{','.join(waits)}]" if waits else ""


def lineFor(ticket: Ticket, tickets: dict[str, Ticket]) -> str:
    text = f"- [{ticket.key}] {progressMark(ticket)}{ticket.title}{blockersMark(ticket, tickets)}"
    return textwrap.fill(text, LINE_LENGTH, subsequent_indent="  ", break_on_hyphens=False)


def section(heading: str, group: list[Ticket], tickets: dict[str, Ticket]) -> list[str]:
    return [f"## {heading}", "", *(lineFor(t, tickets) for t in group), ""]


def milestoneHeading(milestone: Milestone) -> str:
    return f"[{milestone.name}](milestones/{milestone.id}.md)"


def byMilestone(openTickets: list[Ticket], milestones: list[Milestone]) -> list[tuple[str, list[Ticket]]]:
    """Each milestone's tickets in work order, milestones in order, then tickets with no milestone."""
    groups = []
    for milestone in sorted(milestones, key=lambda m: m.ordinal):
        group = [t for t in openTickets if t.milestone == milestone.id]
        if group:
            groups.append((milestoneHeading(milestone), workOrder(group)))
    known = {m.id for m in milestones}
    rest = [t for t in openTickets if t.milestone not in known]
    if rest:
        groups.append(("No milestone", workOrder(rest)))
    return groups


def indexText(tickets: list[Ticket], milestones: list[Milestone]) -> str:
    byKey = {t.key: t for t in tickets}
    openTickets = [t for t in tickets if isOpen(t)]
    lines = [HEADER, ""]
    for heading, group in byMilestone(openTickets, milestones):
        lines += section(heading, group, byKey)
    lines += [f"[{t.key}]: {t.file}" for t in sorted(openTickets, key=lambda t: t.key)]
    return "\n".join(lines) + "\n"


def readStore(store: Path) -> tuple[list[Ticket], list[Milestone]]:
    # archive/ is read too, so an issue waiting on an archived one doesn't list it as open.
    paths = [*store.glob("active/SK-*.md"), *store.glob("archive/SK-*.md")]
    paths = [path for path in paths if _keyFromFilename(path.name)]  # a stray SK-*.md is not a ticket
    tickets = [readTicket(path) for path in sorted(paths, key=lambda p: p.name)]
    milestones = [readMilestone(path) for path in sorted((store / "milestones").glob("*.md"))]
    return tickets, milestones


def indexPath(store: Path) -> Path:
    return Path(store) / "INDEX.md"


def indexIsCurrent(store: Path) -> bool:
    path = indexPath(store)
    return path.exists() and path.read_text() == indexText(*readStore(Path(store)))


def writeIndex(store: Path) -> None:
    indexPath(store).write_text(indexText(*readStore(Path(store))))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    check = "--check" in args
    folders = [arg for arg in args if arg != "--check"]
    if len(folders) > 1 or any(arg.startswith("-") for arg in folders):
        sys.exit(USAGE)
    store = Path(folders[0]) if folders else Path(config_mod.issueStoreFrom(config_mod.load()))
    if check:
        if not indexIsCurrent(store):
            print("INDEX.md is out of date: run store-index", file=sys.stderr)
            return 1
        return 0
    writeIndex(store)
    return 0
