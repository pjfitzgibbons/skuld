# A temp ticket store for tests: a git repo holding two tickets, milestones/ and a stand-in
# scripts/index.py (the real one is in the store's own repo). The stand-in writes INDEX.md as one
# line per ticket whose status isn't done or wontfix, so a test sees whether the script ran and
# what it was shown.
import subprocess
from pathlib import Path

INDEX_SCRIPT = """\
import re
from pathlib import Path

store = Path(__file__).resolve().parent.parent
lines = []
for path in sorted(store.glob("SK-*.md")):
    status = re.search(r"(?m)^status: *(\\S+)", path.read_text()).group(1)
    if status not in ("done", "wontfix"):
        lines.append(f"- {path.name}")
(store / "INDEX.md").write_text("\\n".join(lines) + "\\n")
"""

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

## Log

- 2026-11-09: Filed.
"""

KEY = "SK-0047"
OTHER_KEY = "SK-0048"
FILE_NAME = f"{KEY}-a-ticket.md"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


# Makes the store under root, commits it, and returns its folder.
def makeStore(root, status="in-progress"):
    store = Path(root) / "store"
    (store / "scripts").mkdir(parents=True)
    (store / "milestones").mkdir()
    (store / "scripts" / "index.py").write_text(INDEX_SCRIPT)
    for name in ("issue-round", "plan-start"):
        (store / "milestones" / f"{name}.md").write_text(f"---\nid: {name}\nname: {name}\n---\n")
    (store / FILE_NAME).write_text(TICKET.format(status=status))
    (store / f"{OTHER_KEY}-other.md").write_text(TICKET.format(status="open"))
    git(store, "init", "--quiet", "-b", "main")
    git(store, "config", "user.email", "t@example.com")
    git(store, "config", "user.name", "T")
    git(store, "add", "-A")
    git(store, "commit", "-q", "-m", "init")
    subprocess.run(["python3", str(store / "scripts" / "index.py")], check=True)
    return store


def head(store):
    return git(store, "rev-parse", "HEAD").strip()
