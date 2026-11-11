# The IssueStore port's model (docs/architecture.md §6.1): what every adapter's issue(key) returns,
# read in one fetch, so Start, Open, Ship, Done, view, browse and the page server read an issue
# the same way whichever tracker holds it. These are the port's own words: each adapter translates
# its tracker's shapes and vocabulary into them, and none of a tracker's leaks past its adapter.
import re
from dataclasses import dataclass, field

# An issue key, in every store: a project code of two or more letters/digits starting with a
# letter, a dash, a number. CIAM-123 or SK-0003, not a bare number or a branch-release tag. This is
# the one pattern every other key check in the repo (the scripts' KEY_RE-equivalents, pr.sh,
# ghbody.sh) must match (L2).
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def projectOf(key):
    return key.split("-")[0]


# Status categories: where an issue is in its work, whatever its tracker calls the status. Every
# tracker's statuses sort into these three. A command moves an issue by category, never by name.
NEW = "new"
IN_PROGRESS = "in-progress"
DONE = "done"


# A key the store has no ticket for.
class IssueNotFound(RuntimeError):
    pass


# A comment, or a store ticket's log line. date is "YYYY-MM-DD" when the adapter knows it.
@dataclass(frozen=True)
class Comment:
    author: str
    text: str
    date: str = None


# An issue still open that blocks this one.
@dataclass(frozen=True)
class Blocker:
    key: str
    summary: str
    status: str


# One issue in full.
#   status       the tracker's own name for the status, for display only
#   category     NEW, IN_PROGRESS or DONE
#   description  plain text; a store ticket's Markdown body as written
#   comments     oldest first; a store ticket's log
#   blockers     the open issues blocking this one
#   issueType    the tracker's own name for the type; typeMap turns it into a branch prefix
#   page         the issue's page, on its tracker or the local server
#   file         the file the issue is kept in, when its tracker keeps one
#   fields       the issue's own fields as its file sets them, in file order; empty with no file
@dataclass(frozen=True)
class Issue:
    key: str
    summary: str
    status: str
    category: str
    issueType: str
    parent: str = None
    description: str = ""
    comments: tuple = ()
    blockers: tuple = ()
    page: str = None
    file: str = None
    fields: dict = field(default_factory=dict)

    def isNew(self):
        return self.category == NEW

    def isInProgress(self):
        return self.category == IN_PROGRESS
