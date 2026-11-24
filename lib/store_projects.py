# The project keys store.yml lists (docs/plan-file-issue-store.md §4.3). store.yml is a restricted
# YAML subset (§6, open question 2), so this reads only the `- key: <PREFIX>` lines under
# `projects:`; the project's name, repos and the other settings are read by whoever needs them.
import re
from pathlib import Path

PROJECT_KEY_RE = re.compile(r"^\s*-\s+key:\s*([A-Z]+)\s*(?:#.*)?$")


def projectKeys(path):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"no store.yml at {path}; it lists the store's projects")
    return [m.group(1) for line in path.read_text(encoding="utf8").splitlines() if (m := PROJECT_KEY_RE.match(line))]
