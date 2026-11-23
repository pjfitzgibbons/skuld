# A temp ticket store for tests: a git repo holding two tickets and milestones/, data only, with the
# INDEX.md that lib/store_index.py writes for it.
import subprocess
from datetime import date
from pathlib import Path
from unittest import mock

from .store_index import writeIndex

TICKET = """---
status: {status}
type: task
guide: §6
opened: 2026-11-09
closed:
ordinal: 152
blocked_by:
milestone: issue-round
---

# A ticket

## Task

Do the thing.

## Done

{done}
## Log

- 2026-11-09: Filed.
"""

TODAY = "2026-11-21"


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls.fromisoformat(TODAY)


# Makes today TODAY for the issue_update and fileissues modules until the test ends.
def freezeToday(testcase):
    for module in ("lib.fileissues.date", "lib.issue_update.date"):
        patch = mock.patch(module, FixedDate)
        patch.start()
        testcase.addCleanup(patch.stop)


KEY = "SK-0047"
OTHER_KEY = "SK-0048"
FILE_NAME = f"{KEY}-a-ticket.md"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


# Makes the store under root, commits it, and returns its folder.
def makeStore(root, status="in-progress", done="- [x] It works.\n"):
    store = Path(root) / "store"
    (store / "milestones").mkdir(parents=True)
    for name in ("issue-round", "plan-start"):
        (store / "milestones" / f"{name}.md").write_text(f"---\nid: {name}\nname: {name}\nstatus: open\n---\n")
    (store / "active").mkdir()
    (store / "active" / FILE_NAME).write_text(TICKET.format(status=status, done=done))
    (store / "active" / f"{OTHER_KEY}-other.md").write_text(TICKET.format(status="open", done="- [ ] Not yet.\n"))
    writeIndex(store)
    git(store, "init", "--quiet", "-b", "main")
    git(store, "config", "user.email", "t@example.com")
    git(store, "config", "user.name", "T")
    git(store, "add", "-A")
    git(store, "commit", "-q", "-m", "init")
    return store


def head(store):
    return git(store, "rev-parse", "HEAD").strip()
