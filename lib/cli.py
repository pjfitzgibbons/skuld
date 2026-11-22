# plan's subcommands: the terminal replacement for extension.js's webview and commands. No vscode
# import, no new deps -- fzf/readline/argv parsing by hand, like the rest of this port.
#
# Ported from lib/cli.js. Faithful, behavior-preserving port: no change in logic.
import json
import os
import re
import subprocess
import sys

from . import agents as agents_mod
from . import config as config_mod
from . import done as done_mod
from . import issuestore as issuestore_mod
from . import pulses as pulses_mod
from . import reviews as reviews_mod
from . import serve as serve_mod
from . import ship as ship_mod
from . import start as startLib
from . import term as term_mod
from .issuekeys import ISSUE_KEY_PATTERN, KEY_RE


# y/n on stdin, for Start's confirm(blockers) -- the terminal stand-in for the old modal dialog.
def confirmBlockers(blockers):
    if not blockers:
        return True
    print("This issue is still open and blocks it:" if len(blockers) == 1 else "These issues are still open and block it:")
    for b in blockers:
        print(f"  {b.key}  {b.status}  {b.summary}")
    answer = input("Start anyway? [y/N] ")
    return bool(re.match(r"^y(es)?$", answer.strip(), re.IGNORECASE))


# The branch currently checked out at dir, to infer KEY for ship/go when none was typed.
def currentBranch(dir):
    return startLib.git(dir, "branch", "--show-current").strip()


# Ship's dir: the issue's worktree, found the way Start/Open/Done find it (mainCheckout +
# existingWorktree, from the key), so Ship follows docs/architecture.md §7.0 like they do. A cwd on
# the issue's branch gets no shortcut: an unlisted repo is refused, and work in two listed repos is
# refused by name. Ship never asks for a repo, since only a repo holding the work can be shipped.
def resolveDir(cfg, key):
    typeMap = cfg.get("typeMap")
    checkout = startLib.mainCheckout(key, cfg.get("checkouts"), os.getcwd(), "Ship", typeMap, refuseRepoPick)["checkout"]
    porcelain = startLib.git(checkout, "worktree", "list", "--porcelain")
    found = startLib.existingWorktree(porcelain, key, startLib.worktreeDir(checkout, key), "Ship", typeMap)
    if not found:
        raise RuntimeError(f"no worktree found for {key}; run plan start {key} first")
    return found["dir"]


# Pulls an issue key out of a branch name built by branchName() (<type>/<KEY>-<slug>) or a bare
# <KEY>-<slug>; raises if the branch doesn't carry one, since ship/go without a key need one.
def keyFromBranch(branch):
    key = branchKey(branch)
    if not key:
        raise RuntimeError(f'current branch "{branch}" doesn\'t carry an issue key; pass one: plan ship CIAM-123 or plan ship SK-0003')
    return key


# The issue key a branch name carries, or None: the key before a slug, after any <prefix>/.
def branchKey(branch):
    match = re.search(rf"(?:^|/)({ISSUE_KEY_PATTERN})-", branch)
    return match.group(1) if match else None


# Next up's rows: every project this Mac works (issuestore.projects()), each one's open issues
# from its own adapter, as one set per project, merged in project order by mergeSets().
def nextUpRows(cfg):
    sets = []
    for project in issuestore_mod.projects(cfg):
        sets.append(issuestore_mod.storeForProject(cfg, project).list(project))
    return mergeSets(sets)


# The sets as one list, each set kept in its adapter's order,
# never re-sorted. A row whose `after:` names a row of an earlier set goes right after that row;
# every other row follows the rows before its set. Anchors only point back to an earlier set, so an
# `after:` chain can never cycle.
def mergeSets(sets):
    merged = []
    for rows in sets:
        merged = placedAfterAnchors(merged, rows)
    return merged


# merged with rows added: each row anchored to one of merged's ids right after it, in rows' order,
# and the rest at the end.
def placedAfterAnchors(merged, rows):
    ids = {row["id"] for row in merged}
    anchored = {}
    leftover = []
    for row in rows:
        anchor = row.get("after")
        if anchor in ids:
            anchored.setdefault(anchor, []).append(row)
        else:
            leftover.append(row)
    placed = []
    for row in merged:
        placed.append(row)
        placed.extend(anchored.get(row["id"], []))
    return placed + leftover


# `plan list`/the picker's rows: Next up (nextUpRows()), padded into columns.
#
# Column alignment (2026-11-06): a plain `f'{id}\t{status}\t{subject}'` left every row's tab
# stops wherever the terminal happened to put them, so columns drifted out of line as soon as ids
# or statuses had different lengths (e.g. CIAM-163 vs CIAM-94). `line` is now one pre-padded
# string -- id, then status, each left-justified to the widest id/status across ALL rows (every
# project's set together) with `str.ljust`, with exactly two literal spaces between columns -- so it
# lines up the same way in `plan list`'s plain stdout and in the fzf picker, with no tab
# character left for a terminal's own tab stops to misplace.
#
# `line` has no tab (or other delimiter) in it at all now, which also keeps cmdPicker's `{1}` key
# extraction working with no extra plumbing: fzf's field splitting defaults to AWK-style (runs of
# whitespace) when `--delimiter` isn't given, so `{1}` already resolves to the first
# whitespace-delimited token -- the bare id, which never itself contains whitespace -- the same
# way it did under the old tab-delimited `--delimiter \t`/`--with-nth 1,2,3` setup. cmdPicker
# below no longer passes either option: it isn't needed to extract `{1}`, and dropping it avoids
# re-introducing a tab-joined display when fzf re-renders the (now space-padded) fields.
def issueRows(cfg):
    issues = nextUpRows(cfg)
    columns = [lambda issue: issue["id"], lambda issue: issue["status"]]
    widths = [max((len(column(issue)) for issue in issues), default=0) for column in columns]
    rows = []
    for issue in issues:
        row = dict(issue)
        row["key"] = issue["id"]
        padded = [column(issue).ljust(width) for column, width in zip(columns, widths)]
        row["line"] = "  ".join([*padded, issue["subject"]])
        rows.append(row)
    return rows


# `plan list`: the picker's rows, printed plainly -- already column-aligned (issueRows()).
def cmdList(cfg):
    rows = issueRows(cfg)
    for row in rows:
        print(row["line"])


# `plan view <KEY>`: one issue in full, the fzf preview pane's content and a standalone command.
def cmdView(cfg, key):
    startLib.checkId(key, "View")
    issue = issuestore_mod.storeFor(cfg, key).issue(key)
    print(f"{key}  {issue.issueType}  {issue.status}")
    print(issue.summary)
    if issue.parent:
        print(f"Parent: {issue.parent}")
    if issue.blockers:
        print(f"Blocked by: {blockerList(issue.blockers)}")
    if issue.description:
        print("")
        print(issue.description)
    if issue.comments:
        print("")
        print(f'{len(issue.comments)} comment{"" if len(issue.comments) == 1 else "s"}:')
        for comment in issue.comments[-5:]:
            print(f"  {comment.author}  {firstLine(comment.text)}")
    if issue.file:
        print("")
        print(f"Kept in {issue.file}: its status and log are there.")
    if issue.page:
        print("")
        print(issue.page)


# "CIAM-1 (In Progress), CIAM-2 (To Do)"
def blockerList(blockers):
    return ", ".join(f"{b.key} ({b.status})" for b in blockers)


def firstLine(text):
    return text.split("\n")[0]


# `plan start`/`plan open <KEY>`: start.start(), then write the issue's workspace file (the plan.*
# settings; it isn't opened), add the issue to the issues file and its worktree to the plan
# workspace, open or raise the plan window with `code <plan workspace>` (term.openPlanWindow; VS
# Code places it, ADR 0004), and ask plan-window to bring the issue forward. Open is the same call
# as Start (start.py's ACTION/startIn already handle "already has a worktree" by reusing it); the
# terminal tool doesn't need two code paths. A wrong extension link stops it first
# (term.checkExtensionLink), before anything changes. The issue is read once, by start(), and
# everything after uses what it read.
def cmdStart(cfg, key, command="start"):
    startLib.checkId(key, "Start")
    term_mod.checkExtensionLink(command, key)
    storeFolder = config_mod.issueStoreFrom(cfg)  # no store stops Start before anything changes
    store = issuestore_mod.storeFor(cfg, key)
    result = startLib.start(store, cfg.get("checkouts"), key, confirmBlockers, typeMap=cfg.get("typeMap"))
    if not result:
        print("Not started.")
        return
    for note in result["notes"]:
        print(note)
    issue = result["issue"]
    # A store issue has its ticket file (plan.ticket); an issue on a tracker has its page (plan.ticketUrl).
    writeIssueWorkspaceFile(cfg, key, result["dir"], issue.file, None if issue.file else issue.page)
    addToIssuesFile(cfg, key)
    planWorkspace = addToPlanWorkspace(cfg, result["dir"], storeFolder)
    term_mod.openPlanWindow(planWorkspace)
    term_mod.revealIssue(key)
    print(f"{key} ready.")
    # The /open link has plan-window open the ticket file in a tab, or the tracker's page
    # (plan.ticketUrl) in the integrated browser, so those are only printed; a store ticket's page
    # is on the local server, and is shown here.
    print(issue.file or issue.page)
    if issue.file:
        showIssuePage(cfg, issue, planWorkspace)


def addToIssuesFile(cfg, key):
    path = term_mod.resolveIssuesFile(cfg.get("issuesFile"))
    with term_mod.step("adding the issue to the issues file", f"Check that {os.path.dirname(path)} is writable."):
        term_mod.addIssue(key, cfg.get("issuesFile"))


# The issue's worktree, and the ticket store's folder, which the plan workspace keeps first: Done
# removes the worktree, and VS Code restarts its extension host when the first folder goes.
def addToPlanWorkspace(cfg, worktree, storeFolder):
    path = planWorkspaceFrom(cfg)
    with term_mod.step("adding the issue's folder to the plan workspace", f"Fix or delete {path} if it can't be read, check that {os.path.dirname(path)} is writable, then run the command again."):
        term_mod.addFolders([worktree], path, planSettingsFrom(cfg), first=storeFolder)
    return path


def planWorkspaceFrom(cfg):
    return term_mod.planWorkspacePath(cfg.get("workspaceDir"))


def planSettingsFrom(cfg):
    return term_mod.planSettings(cfg.get("issuesFile"), cfg.get("pulsesFile"), cfg.get("workspaceDir"))


# The issue's page beside Claude: on its tracker, or on the local server.
# Start runs in whatever terminal invoked `plan start`, not the new issue's own window, so the page
# is aimed at the issue's key rather than the caller's. A local server that isn't answering gets the
# URL printed and how to open it once it is, and Start goes on.
def showIssuePage(cfg, issue, workspaceFile):
    if not issue.page:
        return
    if servedLocally(cfg, issue.page) and not serve_mod.answers(serve_mod.portFrom(cfg)):
        print(issue.page)
        print(f"The local server isn't answering: run `plan serve start`, then `plan browse {issue.key}`.")
        return
    term_mod.showUrl(issue.page, issue.key, workspaceFile)


def servedLocally(cfg, url):
    return url.startswith(serve_mod.serverUrl(serve_mod.portFrom(cfg)))


# What Claude is first asked to do: read the issue through plan, which reads every tracker the same
# way, so the prompt never names one.
def claudePrompt(key):
    return f"Work on {key}. Read it first with `plan view {key}`."


# ticket is a store issue's ticket file; ticketUrl is an issue's page on its tracker, None otherwise.
def writeIssueWorkspaceFile(cfg, key, worktree, ticket, ticketUrl=None):
    path = term_mod.workspaceFilePath(key, cfg.get("workspaceDir"))
    workspace = term_mod.issueWorkspace(key, worktree, claudePrompt(key), ticket, ticketUrl)
    with term_mod.step("writing the issue's workspace file", f"Check that {os.path.dirname(path)} is writable."):
        term_mod.writeWorkspaceFile(path, workspace)
    return path


# `plan browse <KEY | pr#N | url> [KEY]`: print the page's URL, then open it in the plan window's
# integrated browser (architecture §7.6.1). The issue is the target's key, else the [KEY] given,
# else the calling terminal's PLAN_KEY; the plan window is raised first when its workspace exists,
# else the link goes to whichever VS Code window has focus.
def cmdBrowse(cfg, target, windowKey):
    if not target:
        raise RuntimeError("usage: plan browse <KEY | pr#N | url> [KEY]")
    url, targetKey = browseTarget(cfg, target)
    key = targetKey or windowKey or os.environ.get("PLAN_KEY") or None
    term_mod.showUrl(url, key, existingPlanWorkspace(cfg))


# Starts the round's review in the plan window. When the link can't be sent, a request this run
# wrote is withdrawn, so the next `pr` starts the round again; the script withdraws it only if it is
# still this run's and no reviewer has claimed it. A reopened request carries its stored token for
# the link but is left as it is, since another run wrote it. One with no token (written before
# requests had one) can't be linked to, so it is refused with the restart that writes it again.
def startReview(cfg, worktree, key, requested):
    if not requested.token:
        raise RuntimeError(f"round {requested.round}'s request has no token for the review link; run plan review {key} --restart")
    try:
        term_mod.startReview(key, requested.round, requested.token, reviewWindowWorkspace(cfg, key))
    except (RuntimeError, OSError):
        print(withdrawUnstarted(worktree, key, requested), file=sys.stderr)
        raise


# The plan window's workspace file, which the review link needs: without it the link goes to
# whichever window has focus, which has no Review terminal and refuses it.
def reviewWindowWorkspace(cfg, key):
    path = existingPlanWorkspace(cfg)
    if not path:
        raise RuntimeError(f"the plan window's workspace {planWorkspaceFrom(cfg)} doesn't exist, so no window would start the review; plan open {key} writes it")
    return path


# What became of the request whose link wasn't sent, one line. A failure to withdraw it is reported
# here, so the error the caller raises stays the link's, the reason the review didn't start.
def withdrawUnstarted(worktree, key, requested):
    if not requested.written:
        return "request left as it is: it was reopened"
    try:
        return reviews_mod.withdrawRequest(worktree, key, requested.token)
    except (RuntimeError, OSError) as error:
        return f"request not withdrawn ({error}); plan review {key} --restart writes it again"


# `plan review KEY [--restart]`, run in KEY's worktree (or a folder of it) by `pr`: writes the next
# review round's request (review.py request) with the issue's ticket, focuses the plan window when its workspace exists and
# sends it the review link, which starts a fresh Claude on the round's line in the window's
# "Review KEY" terminal, in the worktree; the round's prompt still stops unless its working
# directory is the worktree. An unfinished round for this HEAD is reopened as it is; one for an
# older head is refused unless --restart, which the script refuses while the round's claimed
# reviewer still runs; with nothing due, it says why and starts nothing. --if-due (`pr`'s call) also
# starts nothing for a round already requested for this HEAD, so two `pr` runs start one review. It
# refuses a worktree whose branch names another issue, since it writes there and posts to that
# branch's PR; a branch that names no issue is taken as KEY's, as `pr KEY` takes it.
def cmdReview(cfg, key, restart=False, ifDue=False):
    if not key:
        raise RuntimeError(REVIEW_USAGE)
    worktree = startLib.git(os.getcwd(), "rev-parse", "--show-toplevel").strip()
    branch = startLib.git(worktree, "branch", "--show-current").strip()
    if branchKey(branch) not in (None, key):
        raise RuntimeError(f"this worktree's branch is {branch or '(detached)'}, not {key}'s; run plan review {key} in {key}'s worktree")
    requested = reviews_mod.requestRound(worktree, key, restart, ifDue, issueTicket(cfg, key))
    if not requested.path:
        print(requested.note)
        return
    startReview(cfg, worktree, key, requested)
    print(f"review started in the Review {key} terminal of the plan window")


REVIEW_USAGE = "usage: plan review KEY [--restart | --if-due]"
REVIEW_FLAGS = ("--restart", "--if-due")


def unknownFlags(flags):
    return [flag for flag in flags if flag not in REVIEW_FLAGS]


# --restart and --if-due together contradict each other: one forces a restart, the other starts only when due.
def conflictingFlags(flags):
    return set(REVIEW_FLAGS) <= set(flags)


# (key, restart, ifDue) of `plan review`'s arguments; a flag it doesn't know, a second word, or
# --restart with --if-due is refused.
def reviewArguments(args):
    flags = [arg for arg in args if arg.startswith("--")]
    words = [arg for arg in args if not arg.startswith("--")]
    if unknownFlags(flags) or len(words) > 1 or conflictingFlags(flags):
        raise RuntimeError(f"{REVIEW_USAGE} (given: {' '.join(args)})")
    return (words[0] if words else None), "--restart" in flags, "--if-due" in flags


# Where the review reads the issue's lenses: a store issue's ticket file, a Jira issue's page. None
# when this Mac can't name it (no store, or a project with no "jira" entry); the review then uses
# every lens.
def issueTicket(cfg, key):
    try:
        issue = issuestore_mod.storeFor(cfg, key).issue(key)
    except RuntimeError:
        return None
    return issue.file or issue.page


PR_TARGET_RE = re.compile(r"^pr#(\d+)$")


# (url, the key it belongs to or None) for a browse target: a key, pr#N or an http(s) URL.
def browseTarget(cfg, target):
    if KEY_RE.match(target):
        key = issuestore_mod.canonicalKey(target)
        return issuestore_mod.storeFor(cfg, key).issue(key).page, key
    pr = PR_TARGET_RE.match(target)
    if pr:
        return ship_mod.pullRequest(os.getcwd(), pr.group(1), "url")["url"], None
    if term_mod.isWebUrl(target):
        return target, None
    raise RuntimeError(f'plan browse takes a key, pr#N or an http(s) URL, not "{target}"')


# The plan workspace when it exists, for `code` to raise the plan window; None before any Start.
def existingPlanWorkspace(cfg):
    path = planWorkspaceFrom(cfg)
    return path if os.path.exists(path) else None


def refuseRepoPick(options):
    raise RuntimeError(f"more than one repo could hold the work: {', '.join(options)}")


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
    issuestore_mod.refuseUnknownProject(cfg, key)
    dir = resolveDir(cfg, key)
    result = ship_mod.check(dir, key)
    if result["problems"]:
        print(f"{key} isn't ready to ship:", file=sys.stderr)
        for p in result["problems"]:
            print(f"  - {p}", file=sys.stderr)
        return 1
    term_mod.shipWindow(key, dir, startLib.expandHome(cfg.get("pr")))
    return None


# `plan done [KEY]`: done.finish(); Done never transitions the issue (decision #5) -- it prints
# the notes done.py returns, ending with the store's close note, and leaves closing it to Peter.
def cmdDone(cfg, key):
    dir = os.getcwd()
    if not key:
        key = keyFromBranch(currentBranch(dir))
    startLib.checkId(key, "Done")
    store = issuestore_mod.storeFor(cfg, key)
    result = done_mod.finish(
        store,
        cfg.get("checkouts"),
        key,
        disposable=cfg.get("disposable"),
        typeMap=cfg.get("typeMap"),
        issuesFile=cfg.get("issuesFile"),
        pulsesFile=cfg.get("pulsesFile"),
        workspaceDir=cfg.get("workspaceDir"),
        cwd=dir,
    )
    for note in result["notes"]:
        print(note)


# `plan` with no args: fzf over the picker's rows. enter starts/opens the highlighted issue;
# ctrl-s ships it (check, then pr, in one step -- Go's old ctrl-g bind is removed), ctrl-d marks
# it done, ctrl-r reloads the list. Each bind shells back out to `plan <subcommand> {1}` (the
# row's first field, the issue key) rather than duplicating any of cmdStart/cmdShip/cmdDone's logic
# inside the fzf process. `{1}` is fzf's default AWK-style (whitespace-run) field splitting --
# no `--delimiter`/`--with-nth` needed (2026-11-06): issueRows()'s `line` is already one padded,
# tab-free string, so the first whitespace-delimited token is the bare id.
# L5: fzf runs execute()/reload()/preview's string through a shell, so sys.argv[0] (plan's own
# path) needs POSIX single-quoting there -- unquoted, a path with a space or shell metacharacter
# (an install under "Application Support", say) would break the command or, worse, let a crafted
# path inject one. Single-quote the whole thing, escaping an embedded ' as the standard '\''.
def shellQuote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


# `plan pulse <KEY> <orange|green|off>`: colour the issue's item in the Issues view, or clear it.
def cmdPulse(cfg, key, color):
    startLib.checkId(key, "Pulse")
    pulses_mod.pulse(key, color, cfg.get("issuesFile"), cfg.get("pulsesFile"))


AGENTS_USAGE = "plan agents [status | list | pick | tick [--watch]] [--json]"
# The flags each `plan agents` action takes.
AGENTS_FLAGS = {"status": set(), "list": {"--json"}, "pick": {"--json"}, "tick": {"--watch"}}


# `plan agents`: every Claude session, its issue and state (lib/agents.py). `status` is one
# line; `list` (the default) is the table; `--json` is the listing as JSON; `pick` chooses a
# session in fzf and brings its issue forward in the plan window; `tick` pulses each issue once, and
# `tick --watch` keeps doing so, as the LaunchAgent does (bin/install-agents).
def cmdAgents(cfg, args):
    words = [arg for arg in args if not arg.startswith("--")]
    flags = {arg for arg in args if arg.startswith("--")}
    what = words[0] if words else "list"
    if what not in AGENTS_FLAGS or len(words) > 1 or not flags <= AGENTS_FLAGS[what]:
        raise RuntimeError(f"usage: {AGENTS_USAGE}")
    if what == "tick":
        return agentsTick(cfg, "--watch" in flags)
    if what == "status":
        print(agents_mod.statusLine(agents_mod.currentAgents(cfg.get("issuesFile"), cfg.get("claudeCommand"))))
        return None
    agents = listedAgents(cfg)
    if "--json" in flags:
        print(json.dumps(agents_mod.jsonRows(agents), indent=2))
    elif what == "pick":
        pickAgent(cfg, agents)
    else:
        print("\n".join(agents_mod.tableLines(agents)))
    return None


def listedAgents(cfg):
    agents = agents_mod.currentAgents(cfg.get("issuesFile"), cfg.get("claudeCommand"))
    return agents_mod.withTitles(agents, lambda key: issueTitle(cfg, key))


# A key's title from its store, when the config names its project (storeFor() refuses the rest,
# which leaves the title blank).
def issueTitle(cfg, key):
    return issuestore_mod.storeFor(cfg, key).issue(key).summary


# One tick writes what differs from the engine's sent record (lib/agents.py loadSent); the watch loop starts from it too.
def agentsTick(cfg, keepWatching):
    issuesFile, pulsesFile, claudeCommand = cfg.get("issuesFile"), cfg.get("pulsesFile"), cfg.get("claudeCommand")
    if keepWatching:
        return agents_mod.watch(issuesFile, pulsesFile, claudeCommand)  # runs until stopped
    sent = agents_mod.loadSent(pulsesFile)
    _, failures = agents_mod.rememberingTick(sent, issuesFile, pulsesFile, claudeCommand)
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else None


# fzf over the table; enter raises the plan window with `code` and brings the chosen session's issue
# forward in it, the way Open does (term.openPlanWindow(), term.revealIssue()).
def pickAgent(cfg, agents):
    menu = "\n".join(f"{i}\t{line}" for i, line in enumerate(agents_mod.tableLines(agents)))
    chosen = subprocess.run(
        ["fzf", "--with-nth=2..", "--delimiter=\t", "--no-sort", "--prompt=claude> "],
        input=menu,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    if chosen:
        focusAgent(cfg, agents[int(chosen.split("\t")[0])])


def focusAgent(cfg, agent):
    if not agent.key:
        print(f"{agent.name} isn't in an issue's worktree; it runs in {agent.cwd}")
        return
    if agent.key not in term_mod.issueKeys(cfg.get("issuesFile")):
        print(f"{agent.key} isn't in the plan window; plan open {agent.key} adds it")
        return
    term_mod.openPlanWindow(planWorkspaceFrom(cfg))
    term_mod.revealIssue(agent.key)


# `plan serve start|stop|restart|status [--port N]`: control the local server's LaunchAgent
# (lib/serve.py). --port is the only thing it takes after the action.
def cmdServe(cfg, args):
    action, options = (args[0], args[1:]) if args else (None, [])
    if options and (options[0] != serve_mod.PORT_FLAG or len(options) > 2):
        raise RuntimeError(f"usage: {serve_mod.USAGE}")
    print(serve_mod.control(cfg, action, serve_mod.portIn(options)))


def cmdPicker(cfg, argv0=None):
    if argv0 is None:
        argv0 = sys.argv[0]
    self_ = shellQuote(argv0)

    def bind(keys, action):
        return f"{keys}:execute({self_} {action} {{1}} > /dev/tty 2>&1)+reload({self_} list)"

    args = [
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


# How many arguments each command takes after its name; `agents`, `review` and `serve` check their own.
MOST_ARGS = {"list": 0, "view": 1, "start": 1, "open": 1, "browse": 2, "ship": 1, "done": 1, "pulse": 2}


# Refuses arguments past what cmd takes, so a mistyped or unsupported one is never dropped silently.
def refuseExtraArgs(cmd, args):
    most = MOST_ARGS.get(cmd)
    if most is None or len(args) <= most:
        return
    takes = "no arguments" if most == 0 else f"at most {most} argument{'' if most == 1 else 's'}"
    raise RuntimeError(f"plan {cmd} takes {takes}; remove {' '.join(args[most:])}")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    cfg = config_mod.load()
    cmd = argv[0] if argv else None
    if cmd and KEY_RE.match(cmd):  # `plan CIAM-123` is shorthand for view
        argv = ["view", *argv]
        cmd = "view"
    refuseExtraArgs(cmd, argv[1:])
    if cmd != "serve":  # serve start uses --port or serverPort, not where the server ran last
        cfg = serve_mod.withServedPort(cfg)
    rest = [issuestore_mod.canonicalKey(arg) for arg in argv[1:]]
    if not cmd:
        return cmdPicker(cfg)
    if cmd == "list":
        return cmdList(cfg)
    if cmd == "view":
        return cmdView(cfg, rest[0] if rest else None)
    if cmd in ("start", "open"):
        return cmdStart(cfg, rest[0] if rest else None, cmd)
    if cmd == "browse":
        return cmdBrowse(cfg, rest[0] if rest else None, rest[1] if len(rest) > 1 else None)
    if cmd == "review":
        return cmdReview(cfg, *reviewArguments(rest))
    if cmd == "ship":
        return cmdShip(cfg, rest[0] if rest else None)
    if cmd == "done":
        return cmdDone(cfg, rest[0] if rest else None)
    if cmd == "pulse":
        return cmdPulse(cfg, rest[0] if rest else None, rest[1] if len(rest) > 1 else None)
    if cmd == "agents":
        return cmdAgents(cfg, argv[1:])
    if cmd == "serve":
        return cmdServe(cfg, argv[1:])
    raise RuntimeError(f'unknown command "{cmd}"; try: list, view, start, open, browse, review, ship, done, serve, pulse, agents')
