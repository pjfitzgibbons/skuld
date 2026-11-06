# plan's subcommands: the terminal replacement for extension.js's webview and commands. No vscode
# import, no new deps -- fzf/readline/argv parsing by hand, like the rest of this port.
#
# Ported from lib/cli.js. Faithful, behavior-preserving port: no change in logic.
import os
import re
import subprocess
import sys

from . import config as config_mod
from . import done as done_mod
from . import fileissues as fileissues_mod
from . import ship as ship_mod
from . import start as startLib
from . import term as term_mod
from .jira import KEY_RE, Jira


# The client key's commands go through: FileIssues for a JP-<n> key (lib/fileissues.py, user
# decision 2026-11-05 -- JP issues are this repo's own backlog, stored as files, not Jira), Jira
# for every other project. Every call site below already knows its key before it needs a client
# (checkId() runs first), so dispatch is a plain "which class for this key", not a wrapping
# router object: each returned client implements the same surface (list/issue/blockers/
# statusTransitionId/transition/textComment/addComment/browseUrl/closeNote), so start.py/done.py/
# ship.py need no branch of their own. key=None (the picker's own merged list, which has none)
# always gets Jira, since FileIssues.list() is merged in separately (issueRows(), below).
def jiraFrom(cfg, key=None):
    if key is not None and fileissues_mod.isFileBacked(key):
        return fileissues_mod.FileIssues({"dir": cfg.get("issuesDir")})
    return Jira(
        {
            "baseUrl": cfg.get("baseUrl"),
            "project": cfg.get("project"),
            "jql": cfg.get("jql"),
            "netrcFile": cfg.get("netrcFile"),
        }
    )


# y/n on stdin, for Start's confirm(blockers) -- the terminal stand-in for the old modal dialog.
def confirmBlockers(blockers):
    if not blockers:
        return True
    print("This issue is still open and blocks it:" if len(blockers) == 1 else "These issues are still open and block it:")
    for b in blockers:
        print(f'  {b["id"]}  {b["status"]}  {b["subject"]}')
    answer = input("Start anyway? [y/N] ")
    return bool(re.match(r"^y(es)?$", answer.strip(), re.IGNORECASE))


# The branch currently checked out at dir, to infer KEY for ship/go when none was typed.
def currentBranch(dir):
    return startLib.git(dir, "branch", "--show-current").strip()


# M4: Ship's dir used to be a blind os.getcwd() -- fine when Peter types `plan ship` inside
# the issue's own worktree, but wrong for the picker's ctrl-s bind (cmdPicker, below), which
# shells out `plan ship {1}` from wherever `plan` itself was launched, not from the issue's
# worktree. When cwd's current branch doesn't already match key, this resolves the
# issue's actual worktree the same way Start/Done do (mainCheckout + existingWorktree, from the
# key), instead of operating on whatever directory happened to be current.
def resolveDir(cfg, jira, key):
    cwd = os.getcwd()
    try:
        branch = currentBranch(cwd)
    except RuntimeError:
        branch = ""
    if branch and startLib.issueBranch(key, cfg.get("typeMap")).match(branch):
        return cwd
    issue = jira.issue(key)
    mc = startLib.mainCheckout(issue, cfg.get("checkouts"), cwd, "Start", cfg.get("typeMap"))
    checkout = mc["checkout"]
    porcelain = startLib.git(checkout, "worktree", "list", "--porcelain")
    dir = startLib.worktreeDir(checkout, key)
    found = startLib.existingWorktree(porcelain, key, dir, "Start", cfg.get("typeMap"))
    if not found:
        raise RuntimeError(f"no worktree found for {key}; run plan start {key} first")
    return found["dir"]


# Pulls a Jira key out of a branch name built by branchName() (<type>/<KEY>-<slug>) or a bare
# <KEY>-<slug>; raises if the branch doesn't carry one, since ship/go without a key need one.
def keyFromBranch(branch):
    match = re.search(r"(?:^|/)([A-Z][A-Z0-9]*-[0-9]+)-", branch)
    if not match:
        raise RuntimeError(f'current branch "{branch}" doesn\'t carry a Jira key; pass one: plan ship CIAM-123')
    return match.group(1)


# A minimal ADF document -> plain text renderer, for plan view's description/comments. Only the
# node types md_to_adf.py and Jira's own editor actually produce: paragraphs, text (with a bold/
# code mark folded in as-is, no ANSI), hard breaks, bullet/ordered lists, and headings. Anything
# else just recurses into its content, so an unrecognized node still shows its text rather than
# vanishing silently.
def adfText(node, indent=""):
    if not node:
        return ""
    if node.get("type") == "text":
        return node.get("text") or ""
    if node.get("type") == "hardBreak":
        return "\n"
    children = "".join(adfText(child, indent) for child in node.get("content") or [])
    node_type = node.get("type")
    if node_type == "paragraph":
        return f"{indent}{children}\n"
    if node_type == "heading":
        return f"{indent}{children.upper()}\n"
    if node_type == "listItem":
        return f"{indent}- {children.strip()}\n"
    if node_type in ("bulletList", "orderedList"):
        return children
    return children


def adfToText(doc):
    if not doc:
        return ""
    return "".join(adfText(node) for node in doc.get("content") or []).strip()


# Jira's JQL results plus every JP file whose status is open or in-progress (decision #3,
# 2026-11-05) -- `plan list` and the picker show one merged list, not two.
def issueRows(cfg):
    issues = jiraFrom(cfg).list(cfg.get("jql"))
    issues = issues + fileissues_mod.FileIssues({"dir": cfg.get("issuesDir")}).list()
    rows = []
    for issue in issues:
        row = dict(issue)
        row["line"] = f'{issue["id"]}\t{issue["status"]}\t{issue["subject"]}'
        rows.append(row)
    return rows


# `plan list`: the picker's rows, printed plainly.
def cmdList(cfg):
    term_mod.sweepSlots(cfg.get("slotsFile"))
    rows = issueRows(cfg)
    for row in rows:
        print(row["line"])


# `plan view <KEY>`: one issue in full, the fzf preview pane's content and a standalone command.
def cmdView(cfg, key):
    startLib.checkId(key, "View")
    jira = jiraFrom(cfg, key)
    issue = jira.issue(key)
    f = issue["fields"]
    print(f'{key}  {f["issuetype"]["name"]}  {f["status"]["name"]}')
    print(f["summary"])
    if f.get("parent"):
        print(f'Parent: {f["parent"]["key"]}')
    blockers = jira.blockers(issue)
    if blockers:
        parts = [f'{b["key"]} ({b["fields"]["status"]["name"]})' for b in blockers]
        print(f"Blocked by: {', '.join(parts)}")
    if f.get("description"):
        print("")
        print(adfToText(f["description"]))
    comments = (f.get("comment") or {}).get("comments") or []
    if comments:
        print("")
        print(f'{len(comments)} comment{"" if len(comments) == 1 else "s"}:')
        for c in comments[-5:]:
            author = (c.get("author") or {}).get("displayName") or "?"
            first_line = adfToText(c.get("body")).split("\n")[0]
            print(f"  {author}  {first_line}")
    print("")
    print(jira.browseUrl(key))


# `plan start`/`plan open <KEY>`: start.start(), then open or focus the issue's tmux/Ghostty/
# Aerospace workspace. Open is the same call as Start (start.py's ACTION/startIn already handle
# "already has a worktree" by reusing it); the terminal tool doesn't need two code paths.
def cmdStart(cfg, key):
    startLib.checkId(key, "Start")
    jira = jiraFrom(cfg, key)
    term_mod.sweepSlots(cfg.get("slotsFile"))
    result = startLib.start(jira, cfg.get("checkouts"), key, confirmBlockers, typeMap=cfg.get("typeMap"))
    if not result:
        print("Not started.")
        return
    for note in result["notes"]:
        print(note)
    prompt = None
    if fileissues_mod.isFileBacked(key):
        prompt = (
            f"Work on {key}. It is a local file-backed issue, not a Jira ticket: read "
            f"{jira.browseUrl(key)} first. Its status and log live in that file."
        )
    outcome = term_mod.openIssueWorkspace(
        key, result["dir"], cfg.get("ghosttyApp"), cfg.get("aerospaceConfig"), cfg.get("slotsFile"), prompt
    )
    print(f'Workspace {outcome["workspace"]} ready.')
    if fileissues_mod.isFileBacked(key):
        # User decision 2026-11-05 (revised 2026-11-05): a file-backed issue has no browse URL to
        # open in a browser -- instead a tmux pane split off the issue's "claude" window opens the
        # issue's own file (the issues/ copy FileIssues reads and writes, not the worktree's) in
        # nvim, and the path still prints. Start and Open share this same call (both run cmdStart),
        # so a second Open on an issue already started reuses the pane rather than splitting
        # another one -- openTicketPane()'s own tagged-pane check.
        term_mod.openTicketPane(key, result["dir"], jira.browseUrl(key))
        print(jira.browseUrl(key))
    else:
        # Start runs in whatever terminal invoked `plan start`, not the new issue's own
        # workspace, so it must name that workspace explicitly rather than let openUrl() infer
        # the caller's own.
        term_mod.openUrl(
            jira.browseUrl(key),
            outcome["workspace"],
            cfg.get("browserApp"),
            cfg.get("browserBundleId"),
            cfg.get("slotsFile"),
        )
    term_mod.focusIssueWorkspace(outcome["workspace"], key)


# `plan ship [KEY]`: ship.check()'s pre-flight, then `pr <KEY>` visibly in a tmux window. KEY, when
# omitted, comes from the checkout's current branch -- the same checkout plan start put it in.
# DECIDED: this used to be two commands, Ship (check, then open the round files in nvim+lazygit for
# review) and Go (re-run the check, then run pr). The separate review step is removed -- repeated
# Go has never in practice failed to stage the right files, so the confirmation step was redundant
# -- and `plan go` no longer exists; see docs/architecture.md §7.2. Safe to re-run: a Ship with
# nothing left to send still runs pr, which sends nothing (GUIDE.md §8, "every step runs only when
# it has something to do").
def cmdShip(cfg, key):
    if not key:
        key = keyFromBranch(currentBranch(os.getcwd()))
    startLib.checkId(key, "Ship")
    jira = jiraFrom(cfg, key)
    dir = resolveDir(cfg, jira, key)
    result = ship_mod.check(dir, key)
    if result["problems"]:
        print(f"{key} isn't ready to ship:", file=sys.stderr)
        for p in result["problems"]:
            print(f"  - {p}", file=sys.stderr)
        return 1
    term_mod.shipWindow(key, dir, startLib.expandHome(cfg.get("pr")))
    return None


# `plan done [KEY]`: done.finish(); Done never transitions the Jira issue (decision #5) -- it
# prints the notes done.py returns, including the "Close KEY in Jira" line, and leaves closing
# it to Peter.
def cmdDone(cfg, key):
    dir = os.getcwd()
    if not key:
        key = keyFromBranch(currentBranch(dir))
    startLib.checkId(key, "Done")
    jira = jiraFrom(cfg, key)
    term_mod.sweepSlots(cfg.get("slotsFile"))
    result = done_mod.finish(
        jira,
        cfg.get("checkouts"),
        key,
        disposable=cfg.get("disposable"),
        typeMap=cfg.get("typeMap"),
        slotsFile=cfg.get("slotsFile"),
        cwd=dir,
    )
    for note in result["notes"]:
        print(note)


# `plan` with no args: fzf over the picker's rows. enter starts/opens the highlighted issue;
# ctrl-s ships it (check, then pr, in one step -- Go's old ctrl-g bind is removed), ctrl-d marks
# it done, ctrl-r reloads the list. Each bind shells back out to `plan <subcommand> {1}` (the
# row's first field, the Jira key) rather than duplicating any of cmdStart/cmdShip/cmdDone's logic
# inside the fzf process.
# L5: fzf runs execute()/reload()/preview's string through a shell, so sys.argv[0] (plan's own
# path) needs POSIX single-quoting there -- unquoted, a path with a space or shell metacharacter
# (an install under "Application Support", say) would break the command or, worse, let a crafted
# path inject one. Single-quote the whole thing, escaping an embedded ' as the standard '\''.
def shellQuote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def cmdPicker(cfg, argv0=None):
    if argv0 is None:
        argv0 = sys.argv[0]
    self_ = shellQuote(argv0)

    def bind(keys, action):
        return f"{keys}:execute({self_} {action} {{1}} > /dev/tty 2>&1)+reload({self_} list)"

    args = [
        "--delimiter",
        "\t",
        "--with-nth",
        "1,2,3",
        "--preview",
        f"{self_} view {{1}}",
        "--bind",
        bind("enter", "start"),
        "--bind",
        bind("ctrl-s", "ship"),
        "--bind",
        bind("ctrl-d", "done"),
        "--bind",
        f"ctrl-r:reload({self_} list)",
    ]
    term_mod.sweepSlots(cfg.get("slotsFile"))
    fzf = subprocess.Popen(["fzf", *args], stdin=subprocess.PIPE, stdout=None, stderr=None)
    try:
        rows = issueRows(cfg)
        fzf.stdin.write("\n".join(r["line"] for r in rows).encode("utf8"))
        fzf.stdin.close()
    except Exception as error:
        fzf.stdin.close()
        print(str(error), file=sys.stderr)
    code = fzf.wait()
    if code not in (0, 130):
        raise RuntimeError(f"fzf exited {code}")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    cfg = config_mod.load()
    cmd = argv[0] if argv else None
    rest = argv[1:]
    if not cmd:
        return cmdPicker(cfg)
    if cmd == "list":
        return cmdList(cfg)
    if cmd == "view":
        return cmdView(cfg, rest[0] if rest else None)
    if cmd in ("start", "open"):
        return cmdStart(cfg, rest[0] if rest else None)
    if cmd == "ship":
        return cmdShip(cfg, rest[0] if rest else None)
    if cmd == "done":
        return cmdDone(cfg, rest[0] if rest else None)
    if KEY_RE.match(cmd):  # `plan CIAM-123` is shorthand for view
        return cmdView(cfg, cmd)
    raise RuntimeError(f'unknown command "{cmd}"; try: list, view, start, open, ship, done')
