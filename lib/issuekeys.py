# The issue key: what an issue is called in the issues file, the pulses file, branch names,
# worktree folders and `plan` arguments. This module is the one definition; jira, the file store,
# term, agents, pulses, ship, start and cli import it. docs/architecture.md §5 states the rule.
import re

# Capital letters, a dash, ASCII digits. CIAM-123 and SK-0063, not a bare number or a branch-release
# tag. The extension (vscode/plan-window/plan.js) and the shell and script key checks
# (skills/jira-issues, claude/hooks) cannot import this, so each carries the same string.
ISSUE_KEY_PATTERN = r"[A-Z]+-[0-9]+"
KEY_RE = re.compile(f"^{ISSUE_KEY_PATTERN}$")


def projectOf(key):
    return key.split("-")[0]
