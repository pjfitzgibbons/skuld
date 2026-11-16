#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash). Jira issues are never transitioned to a terminal status
(Done, Closed, Cancelled, Resolved, ...) by Claude running a shell command. That move happens
only in the Jira UI, by Peter's hand (GUIDE.md, "Done differently").

Jira Plan's own code (lib/jira.py) legitimately POSTs a transition once, during Start, to move an
issue to "In Progress" — a non-terminal status — and that happens inside the application, not as
a Bash tool call Claude runs directly. This hook only guards Claude's own shell commands: it never
sees, and does not need to see, what lib/jira.py does at runtime.

Deliberately broad, matching the conservative stance of the real attribution hook this one sits
beside: `jira issue move`/`transition` is blocked outright, regardless of HTTP method, because
jira-cli has no read-only form of that verb. A Jira transitions endpoint URL is narrower: only a
POST (which actually performs a transition) is blocked. A read-only GET of the transitions list
— listing which transitions are available from the issue's current status — is allowed, mirroring
the live `~/.claude/scripts/no-jira-cancel-pretooluse-hook.py` this one is kept in step with.
Method detection fails closed: anything that isn't positively a GET is treated as a POST. False
positives are the accepted cost of never silently letting a terminal move through.

Claude Code sends the pending tool call as JSON on stdin:
    {"tool_name": "Bash", "cwd": "...", "tool_input": {"command": "..."}}

Exit 0 allows the call. Exit 2 blocks it and returns stderr to the model, which then rewrites the
message or asks Peter to run the command himself.
"""

import json
import re
import sys
from pathlib import Path

# A REST call (curl, the jira-rest.sh wrapper, wget, ...) whose path mentions the transitions
# sub-resource. Matches /rest/api/3/issue/<KEY>/transitions and similar. Method is checked
# separately by _command_is_post() below -- only a POST to this path is blocked.
TRANSITIONS_PATH = re.compile(r"/transitions\b", re.IGNORECASE)

# jira-cli verbs that move an issue's status directly by name. Always blocked, regardless of
# method, since jira-cli has no read-only form of "move"/"transition" -- kept separate from
# TRANSITIONS_PATH, which is method-gated.
TRANSITION_VERB = re.compile(r"\bjira\s+issue\s+(?:move|transition)\b", re.IGNORECASE)

# Jira issue keys, so the message can name which ticket the command touches, if any.
ISSUE_KEY = re.compile(r"\b[A-Z]+-[0-9]+\b")

# N11: `git` accepts global options BEFORE the subcommand -- "-C <dir>" (run as if cd'd there
# first) and "-c <key>=<value>" (a one-off config override) are the two Claude is realistically
# going to type. "git -C /x commit -m ..." is exactly as much a commit as a bare "git commit -m
# ...", so both SMART_COMMIT_VERB and FILE_FLAG below match through any number of them.
GIT_COMMIT = r"\bgit\b(?:\s+(?:-C\s+\S+|-c\s+\S+))*\s+commit\b"

# N2/M5/N11: a command that writes a commit message or a PR title/body directly on the command
# line -- git commit -m/-am/--message (a combined short-flag cluster like "-am" still carries "m"'s
# meaning: pick the index up AND take the message from the command line), or gh pr create/edit's
# -t/--title/-b/--body (not --body-file/-F, which pr.sh and ghbody.sh already check at the file
# itself, and which this hook now also reads below -- the negative lookahead keeps "--body-file"
# from matching "--body"). This only catches the inline forms Claude could type straight into a
# Bash call; the file-based forms are handled by FILE_FLAG/GH_FILE_FLAG below.
SMART_COMMIT_VERB = re.compile(
    rf"{GIT_COMMIT}\b[^|;&\n]*(?:-[a-zA-Z]*m\b|--message\b)"
    r"|\bgh\s+pr\s+(?:create|edit)\b[^|;&\n]*(?:-t\b|--title\b|-b\b|--body\b(?!-file))",
    re.IGNORECASE,
)

# N2/N11: the message/title/body text lives in a FILE for these forms -- git commit -F/--file
# <path> (also attached as -Fpath, -F=path, or --file=path), or gh pr create/edit's -F/--body-file
# <path> (also --body-file=path) -- so there is no inline text for SMART_COMMIT_VERB to see at
# all. pr.sh and ghbody.sh are the real guarantee for their own files (GUIDE.md); this is
# belt-and-braces for whatever path Claude happens to pass on the command line. A quoted path has
# its quotes stripped; a missing file is not an error here (it may not exist yet, or may be a path
# this hook can't resolve) -- silently allow and let pr.sh/ghbody.sh catch it once it does exist.
# The captured value "-" (git commit -F- / --file=-) means "read the message from stdin", which
# for an inline Bash call is a heredoc right there in the command text -- _file_flag_text() reads
# that instead of a file.
# N11: the (?<!\S) guard before each flag is load-bearing, not decorative -- case-insensitively,
# the bare letter "-F" is a substring of "--file" and of "--body-file" ("...y-File..."), so without
# it the "-F" alternative would match itself halfway through "--file"/"--body-file" and the capture
# group would grab the flag's own tail ("ile", "ile=msg.txt") as if it were the path. Requiring "not
# preceded by a non-whitespace character" means a flag only matches at its own start (after
# whitespace or the start of the scanned text), never embedded inside a longer flag.
FILE_FLAG = re.compile(
    rf"{GIT_COMMIT}\b[^|;&\n]*(?<!\S)(?:-F(?:=|\s+)?|--file(?:=|\s+))(\"[^\"]+\"|'[^']+'|\S+)", re.IGNORECASE
)
GH_FILE_FLAG = re.compile(
    r"\bgh\s+pr\s+(?:create|edit)\b[^|;&\n]*(?<!\S)(?:-F(?:=|\s+)?|--body-file(?:=|\s+))(\"[^\"]+\"|'[^']+'|\S+)",
    re.IGNORECASE,
)

# N11: `git commit -F- <<EOF\n...\nEOF` reads the message from a heredoc right there in the same
# command string -- there's no file to read, but the body is in `command` itself. Matches bash's
# heredoc forms: <<EOF, <<'EOF', <<"EOF", and <<-EOF (the "-" variant allows an indented
# terminator line). DOTALL so "." crosses the body's newlines.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\n(.*?)\n[ \t]*\2\b", re.DOTALL)

# N8: `gh api` can write a PR's title/body directly -- `gh api repos/.../pulls/<n> -X PATCH -f
# title=... -F body=...` -- bypassing `gh pr create`/`edit` (and therefore SMART_COMMIT_VERB/
# FILE_FLAG/GH_FILE_FLAG above, none of which look at `gh api` at all) entirely. Verb and field
# flags are matched separately (not anchored to each other's position) because gh accepts its
# flags before or after the path argument. `-f`/`-F`/`--field` all take `key=value`; `-F`'s value
# can additionally be `@path` (read a file) per gh's own docs -- _gh_api_field_texts() below reads
# that file the same way FILE_FLAG's path is read.
GH_API_VERB = re.compile(r"\bgh\s+api\b", re.IGNORECASE)
PULLS_ENDPOINT = re.compile(r"\bpulls/\d+\b", re.IGNORECASE)
GH_API_FIELD = re.compile(r"(?:-f|-F|--field)(?:=|\s+)(?:title|body)=(\"[^\"]+\"|'[^']+'|\S+)", re.IGNORECASE)

# Jira smart commits (#close, #comment, #time, ...) are "#<word>" tokens that Jira's GitHub
# integration reads straight off a commit message, PR title or PR body -- not something git or gh
# themselves act on. Strict rule, deliberately broad rather than a fixed list of command words
# (Jira's own list can grow): any "#<word>" right after whitespace or line-start.
# https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/
SMART_COMMIT_TOKEN = re.compile(r"(?:^|\s)#[A-Za-z]")

BLOCKED = """\
BLOCKED: this command POSTs to a Jira transitions endpoint, or uses a transition-moving CLI verb.
Jira issues are moved between statuses only by Peter, in the Jira UI — never from a shell command
or script. State the recommendation and the ticket key, and ask Peter to make the move himself, or
run the command by hand if he wants to. (Read-only GETs to a transitions endpoint are allowed.)
"""

SMART_COMMIT_BLOCKED = """\
BLOCKED: this command writes a commit message, PR title or PR body that looks like a Jira smart
commit (a "#<word>" token next to a ticket key). Jira's GitHub integration reads smart commits off
that text directly -- not off anything this repo calls -- so this would transition or comment on
the ticket from outside Peter's own hand (the same thing Done is built to never do on its own).
Remove the #<word>, or ask Peter first if he actually wants it to fire.
See https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/
"""


def _unquote(path: str) -> str:
    if len(path) >= 2 and path[0] == path[-1] and path[0] in "\"'":
        return path[1:-1]
    return path


def _file_flag_text(command: str, cwd: str) -> str | None:
    """N2/N11: the text a -F/--file or -F/--body-file flag points at. Normally that's a path, read
    from disk relative to cwd; None if neither flag is present or the file can't be read (missing,
    not a Jira near-miss -- pr.sh/ghbody.sh are the real guarantee for files that do exist by the
    time they run). But "-F-"/"--file=-" means "read the message from stdin", and for an inline
    Bash call the only way that stdin is actually fed is a heredoc right there in the same command
    string (git commit -F- <<EOF ... EOF) -- there's no file on disk at all, so read the heredoc
    body out of `command` itself instead."""
    m = FILE_FLAG.search(command) or GH_FILE_FLAG.search(command)
    if not m:
        return None
    value = _unquote(m.group(1))
    if value == "-":
        h = HEREDOC.search(command)
        return h.group(3) if h else None
    path = Path(cwd) / value
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _gh_api_pulls_field_texts(command: str, cwd: str) -> list[str]:
    """N8: every title=/body= value on a `gh api ... pulls/<n>` call -- inline text as-is, or the
    content of a file for `-F field=@path` (gh's "read this field from a file" syntax). Empty list
    if this isn't a `gh api` call touching a specific PR at all."""
    if not (GH_API_VERB.search(command) and PULLS_ENDPOINT.search(command)):
        return []
    texts = []
    for m in GH_API_FIELD.finditer(command):
        value = _unquote(m.group(1))
        if value.startswith("@"):
            path = Path(cwd) / value[1:]
            try:
                texts.append(path.read_text(encoding="utf-8"))
            except OSError:
                pass
        else:
            texts.append(value)
    return texts


def _has_post_or_unknown_signal(cmd: str) -> bool:
    """True if ANY substring of cmd indicates a POST (or an undeterminable method), scanned
    independently of where any GET signal also appears.

    Mirrors ~/.claude/scripts/no-jira-cancel-pretooluse-hook.py: a command can be a
    pipeline/chain (`a && b`, `a ; b`, `a | b`) where one segment is a harmless GET and another
    segment is the real POST -- so this must not stop looking once it sees a GET elsewhere in the
    string. This is checked across the WHOLE command before any GET signal is allowed to win, so
    `curl -X GET a && curl -d '{}' .../transitions` is still caught."""
    # Explicit POST method flags (curl, http/httpie).
    if re.search(r"(-X\s*POST\b|-XPOST\b|--request[= ]?POST\b)", cmd, re.IGNORECASE):
        return True
    if re.search(r"--method[= ]?POST\b", cmd, re.IGNORECASE):
        return True
    if re.search(r"\bhttp\s+POST\b", cmd, re.IGNORECASE):
        return True

    # jira-rest.sh helper: `jira-rest.sh <METHOD> <path>`. Check every invocation, not just the
    # first.
    for m in re.finditer(r"jira-rest\.sh\s+(\S+)", cmd):
        if m.group(1).upper() != "GET":
            return True  # POST, or an unrecognized method -> fail closed

    # wget.
    if re.search(r"--post-(?:data|file)\b", cmd, re.IGNORECASE):
        return True

    # python requests / urllib.
    if re.search(r"requests\.post\s*\(", cmd):
        return True

    # node fetch(url, {method: "POST"}).
    if re.search(r"method\s*:\s*['\"]POST['\"]", cmd, re.IGNORECASE):
        return True

    # curl: a body flag with no explicit -X/--request defaults to POST.
    if re.search(
        r"(?:^|\s)(?:-d\b|--data\b|--data-raw\b|--data-binary\b|"
        r"--data-urlencode\b|--json\b|-F\b|--form\b|-T\b|--upload-file\b)",
        cmd,
    ):
        return True

    return False


def _has_explicit_get_signal(cmd: str) -> bool:
    """True only if cmd carries an unambiguous, explicit GET indicator."""
    if re.search(r"(-X\s*GET\b|-XGET\b|--request[= ]?GET\b)", cmd, re.IGNORECASE):
        return True
    if re.search(r"--method[= ]?GET\b", cmd, re.IGNORECASE):
        return True
    if re.search(r"\bhttp\s+GET\b", cmd, re.IGNORECASE):
        return True
    if re.search(r"requests\.get\s*\(", cmd):
        return True
    if re.search(r"method\s*:\s*['\"]GET['\"]", cmd, re.IGNORECASE):
        return True
    # jira-rest.sh GET explicitly, and no other jira-rest.sh invocation in the command used a
    # non-GET method (that case is already caught as a POST/unknown signal above, which
    # short-circuits before this runs).
    if re.search(r"jira-rest\.sh\s+GET\b", cmd, re.IGNORECASE):
        return True
    return False


def _command_is_post(cmd: str) -> bool:
    """Best-effort HTTP-method detection for a shell command string.

    Returns True when the command appears to issue (or might issue) a POST. Fails CLOSED: if the
    method can't be positively determined to be a safe GET, this returns True (block).

    Every POST/body/unknown-method signal is checked across the WHOLE command string before any
    GET signal is allowed to win -- a command can chain multiple sub-commands (`&&`, `;`, `|`),
    and an innocuous GET in one segment must never mask a POST in another."""
    if _has_post_or_unknown_signal(cmd):
        return True
    if _has_explicit_get_signal(cmd):
        return False

    # Plain curl with no method flag and no body flag defaults to GET.
    if re.search(r"\bcurl\b", cmd, re.IGNORECASE):
        return False

    # Unknown tool, unknown method (e.g. an opaque script) -> fail closed.
    return True


def violation(command: str, cwd: str = ".") -> str | None:
    """The whole rule. Returns the reason to block, or None to allow."""
    if TRANSITION_VERB.search(command):
        keys = ISSUE_KEY.findall(command)
        return BLOCKED + (f"\nTicket(s) in the command: {', '.join(sorted(set(keys)))}\n" if keys else "")
    if TRANSITIONS_PATH.search(command) and _command_is_post(command):
        keys = ISSUE_KEY.findall(command)
        return BLOCKED + (f"\nTicket(s) in the command: {', '.join(sorted(set(keys)))}\n" if keys else "")
    if (
        SMART_COMMIT_VERB.search(command)
        and ISSUE_KEY.search(command)
        and SMART_COMMIT_TOKEN.search(command)
    ):
        keys = ISSUE_KEY.findall(command)
        return SMART_COMMIT_BLOCKED + (f"\nTicket(s) in the command: {', '.join(sorted(set(keys)))}\n" if keys else "")
    file_text = _file_flag_text(command, cwd)
    if file_text is not None and ISSUE_KEY.search(file_text) and SMART_COMMIT_TOKEN.search(file_text):
        keys = ISSUE_KEY.findall(file_text)
        return SMART_COMMIT_BLOCKED + (f"\nTicket(s) in the command: {', '.join(sorted(set(keys)))}\n" if keys else "")
    for field_text in _gh_api_pulls_field_texts(command, cwd):
        if ISSUE_KEY.search(field_text) and SMART_COMMIT_TOKEN.search(field_text):
            keys = ISSUE_KEY.findall(field_text)
            return SMART_COMMIT_BLOCKED + (f"\nTicket(s) in the command: {', '.join(sorted(set(keys)))}\n" if keys else "")
    return None


def should_block(command: str, cwd: str = ".") -> bool:
    return violation(command, cwd) is not None


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--check":
        # Hand-check from a shell, no JSON needed:
        #   python3 no_terminal_jira_transition.py --check 'curl ... /transitions'
        command = " ".join(sys.argv[2:])
        cwd = "."
    else:
        payload = json.load(sys.stdin)
        command = (payload.get("tool_input") or {}).get("command") or ""
        cwd = payload.get("cwd") or "."
    reason = violation(command, cwd)
    if reason:
        sys.stderr.write(reason)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
