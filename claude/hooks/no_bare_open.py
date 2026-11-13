#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash). Refuses system `open` on a page.

`open <url>`, `open -a <browser> <url>` and `open page.html` hand the page to
the default browser, outside every issue window. A page opens through
`plan browse`, which opens it in the issue window's integrated browser
(docs/architecture.md §7.6.1, §10 rule 7).

`open` on anything that isn't a page (a folder, a PDF, an app) is allowed.

Claude Code sends the pending tool call as JSON on stdin:
    {"tool_name": "Bash", "cwd": "...", "tool_input": {"command": "..."}}

Exit 0 allows the call. Exit 2 blocks it and returns stderr to the model.
"""

import json
import re
import shlex
import sys

# Where one shell command ends and the next begins. Splitting on these before
# shlex loses quoting across a separator, which only matters for a quoted
# argument that itself contains one; such an argument is not a page.
COMMAND_SEPARATOR = re.compile(r"\n|;|&&|\|\||\||\$\(|`|\(")
OPEN_COMMANDS = ("open", "/usr/bin/open")
PAGE_URL = re.compile(r"^(?:https?|file)://", re.IGNORECASE)
HTML_FILE = re.compile(r"\.html?(?:[?#].*)?$", re.IGNORECASE)

BLOCKED = (
    "BLOCKED: system `open` sends a page to the default browser, outside every "
    "issue window. Open it with `plan browse <KEY | KEY/report | pr#N | url> [KEY]`, which "
    "opens it in the issue window's integrated browser (docs/architecture.md §7.6.1). Where `plan browse` isn't "
    "available, print the URL for the person to open instead.\n"
)


def commands(command_line: str) -> list[list[str]]:
    """Each simple command in the line, as its words."""
    return [words(part) for part in COMMAND_SEPARATOR.split(command_line) if part.strip()]


def words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def is_page(argument: str) -> bool:
    return bool(PAGE_URL.match(argument) or HTML_FILE.search(argument))


def opens_a_page(command_words: list[str]) -> bool:
    if not command_words or command_words[0] not in OPEN_COMMANDS:
        return False
    return any(is_page(argument) for argument in command_words[1:])


def violation(command_line: str) -> str | None:
    """The whole rule. Returns the reason to block, or None to allow."""
    if any(opens_a_page(command_words) for command_words in commands(command_line)):
        return BLOCKED
    return None


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--check":
        # Hand-check from a shell, no JSON needed:
        #   python3 no_bare_open.py --check 'open https://example.com'
        command = " ".join(sys.argv[2:])
    else:
        payload = json.load(sys.stdin)
        command = (payload.get("tool_input") or {}).get("command") or ""
    reason = violation(command)
    if reason:
        sys.stderr.write(reason)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
