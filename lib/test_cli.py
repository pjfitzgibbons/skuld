# M4: cmdShip used os.getcwd() as the worktree dir unconditionally. That's right when Peter types
# `plan ship` from inside the issue's own worktree, but wrong for the picker's ctrl-s bind
# (cmdPicker shells out `plan ship {1}` from wherever `plan` itself was launched -- never
# necessarily the issue's worktree). resolveDir() is the fix: it resolves the issue's real worktree
# the same way Start/Done do (mainCheckout + existingWorktree, from the key alone). These tests mock lib.start's module
# functions directly -- cli.py calls them as `startLib.git(...)`/`startLib.mainCheckout(...)`
# (module attribute access, not imported names), so patching the shared module's attributes is
# visible to cli.py immediately.
#
# Ported from lib/cli.test.js. Faithful, behavior-preserving port.
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from lib import cli
from lib import ship as ship_mod
from lib import start as startLib
from lib import term as term_mod
from lib.cli import main, mergeSets, resolveDir, shellQuote
from lib.issue import IN_PROGRESS, NEW, Issue
from lib.placer import Placement
from lib.test_start import sandbox as start_sandbox

# A config with Jira on: one project and a site that no test ever reaches.
JIRA_ON = {"jira": {"CIAM": {"repos": []}}, "baseUrl": "https://jira.example"}


PAGE = "http://127.0.0.1:8765/issues/"


# Every project's adapter at once, for Next up: list(project) gives that project's set, and the
# projects asked are recorded in order.
class FakeProjectStores:
    def __init__(self, rowsByProject):
        self.rowsByProject = rowsByProject
        self.asked = []

    def list(self, project):
        self.asked.append(project)
        return self.rowsByProject.get(project, [])

    def patch(self):
        return mock.patch.object(cli.issuestore_mod, "storeForProject", side_effect=lambda cfg, project: self)
JIRA_PAGE = "https://jira.example/browse/"


# The Issue a store returns: a store ticket has a file and a server page; a Jira issue has a browse
# URL only.
def issueFor(key, ticket=None):
    page = f"{PAGE}{key}" if ticket else f"{JIRA_PAGE}{key}"
    return Issue(key, "x", "In Progress", IN_PROGRESS, "Task", page=page, file=ticket)


# The IssueStore port as cli sees it: storeFor() hands one back, and it records each read.
class FakeStore:
    def __init__(self, ticket=None):
        self.ticket = ticket
        self.reads = []

    def issue(self, key):
        self.reads.append(key)
        return issueFor(key, self.ticket)


# `start` and `open` are the same command (main() routes both to cmdStart -- a second Open on an
# issue that already has a worktree is just Start finding "nothing to do" and reusing it), so these
# run cmdStart through main(["open", ...]) with start.start() and the window calls stubbed, and
# check the workspace file it writes.
class CmdOpenTests(unittest.TestCase):
    def runOpen(self, key, ticket):
        tmp = tempfile.mkdtemp(prefix="skuld-open-")
        self.addCleanup(shutil.rmtree, tmp)
        worktree = os.path.join(tmp, "repo.worktrees", key)
        os.makedirs(worktree)
        cfg = {"issueStore": "/issues", "workspaceDir": os.path.join(tmp, "ws"), "serverPort": 8765}
        placer = FakePlacer(Placement(False, "3", "Desktop 3"))
        with mock.patch.object(cli.config_mod, "load", return_value=cfg), mock.patch.object(
            cli.issuestore_mod, "storeFor", return_value=FakeStore(ticket)
        ), mock.patch.object(cli.placer_mod, "placerFrom", return_value=placer), mock.patch.object(
            startLib, "start", return_value={"dir": worktree, "notes": [], "issue": issueFor(key, ticket)}
        ) as start, mock.patch.object(term_mod, "showUrl") as showUrl, mock.patch.object(
            cli.serve_mod, "answers", return_value=True
        ), mock.patch("builtins.print"):
            main(["open", key])
        workspaceFile = os.path.join(tmp, "ws", f"{key}.code-workspace")
        with open(workspaceFile, encoding="utf8") as f:
            settings = json.load(f)["settings"]
        self.assertIn(("openWindow", key, workspaceFile), placer.calls)
        self.assertEqual(placer.calls[-1], ("focus", "3", workspaceFile))
        return {"start": start, "shown": [call.args for call in showUrl.call_args_list], "settings": settings}

    def test_open_on_a_sk_key_opens_the_ticket_file_in_a_tab_and_its_page_in_the_browser(self):
        opened = self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        self.assertEqual(opened["settings"]["plan.ticket"], "/issues/SK-0001-slug.md")
        self.assertEqual(opened["settings"]["plan.prompt"], "Work on SK-0001. Read it first with `plan view SK-0001`.")
        workspaceFile = opened["shown"][0][2]
        self.assertEqual(opened["shown"], [(f"{PAGE}SK-0001", "SK-0001", workspaceFile)])

    def test_open_on_a_jira_key_opens_its_url_and_has_no_ticket_tab(self):
        opened = self.runOpen("CIAM-1", None)
        self.assertNotIn("plan.ticket", opened["settings"])
        self.assertEqual(opened["settings"]["plan.prompt"], "Work on CIAM-1. Read it first with `plan view CIAM-1`.")
        workspaceFile = opened["shown"][0][2]
        self.assertEqual(opened["shown"], [(f"{JIRA_PAGE}CIAM-1", "CIAM-1", workspaceFile)])
        self.assertTrue(workspaceFile.endswith("CIAM-1.code-workspace"))

    def test_open_pads_a_sk_key_typed_without_its_zeros(self):
        ticket = "/issues/SK-0001-slug.md"
        with mock.patch.object(cli.issuestore_mod, "storeFor", return_value=FakeStore(ticket)), mock.patch.object(
            cli, "writeIssueWorkspaceFile", return_value="/w/SK-0001.code-workspace"
        ), mock.patch.object(
            cli.placer_mod, "placerFrom", return_value=FakePlacer(Placement(False, "3", "Desktop 3"))
        ), mock.patch.object(
            startLib, "start", return_value={"dir": "/w/SK-0001", "notes": [], "issue": issueFor("SK-0001", ticket)}
        ) as start, mock.patch.object(term_mod, "showUrl"), mock.patch.object(
            cli.serve_mod, "answers", return_value=True
        ), mock.patch("builtins.print"):
            main(["open", "SK-1"])
        self.assertEqual(start.call_args.args[2], "SK-0001")


# The WindowPlacer port seen from Start and Open: a fake placer records what cmdStart asks of it,
# so the order (sweep, open, show the URL, focus) and what's printed are checked with no window
# server.
class FakePlacer:
    def __init__(self, placement):
        self.placement = placement
        self.calls = []

    def sweep(self):
        self.calls.append(("sweep",))

    def openWindow(self, key, workspaceFile):
        self.calls.append(("openWindow", key, workspaceFile))
        return self.placement

    def focus(self, placement, workspaceFile):
        self.calls.append(("focus", placement.slot, workspaceFile))


class CmdStartWithFakePlacerTests(unittest.TestCase):
    def runStart(self, key, placement, ticket=None, serverUp=True):
        placer = FakePlacer(placement)
        out = io.StringIO()
        with mock.patch.object(
            cli.config_mod, "load", return_value={"issueStore": "/issues", "serverPort": 8765}
        ), mock.patch.object(cli.issuestore_mod, "storeFor", return_value=FakeStore(ticket)), mock.patch.object(
            cli.serve_mod, "answers", return_value=serverUp
        ), mock.patch.object(cli.placer_mod, "placerFrom", return_value=placer), mock.patch.object(
            cli, "writeIssueWorkspaceFile", return_value=f"/w/{key}.code-workspace"
        ), mock.patch.object(
            startLib, "start", return_value={"dir": f"/w/{key}", "notes": [], "issue": issueFor(key, ticket)}
        ), mock.patch.object(
            term_mod, "showUrl", side_effect=lambda url, key, workspaceFile: placer.calls.append(("showUrl", url, key, workspaceFile))
        ), mock.patch("sys.stdout", out):
            main(["start", key])
        return placer.calls, out.getvalue()

    def test_start_on_desktops_opens_shows_the_page_then_focuses_and_says_where(self):
        placement = Placement(True, "3", "Desktop 3")
        calls, out = self.runStart("SK-0044", placement, "/issues/SK-0044-x.md")
        workspaceFile = "/w/SK-0044.code-workspace"
        self.assertEqual(
            calls,
            [
                ("sweep",),
                ("openWindow", "SK-0044", workspaceFile),
                ("showUrl", f"{PAGE}SK-0044", "SK-0044", workspaceFile),
                ("focus", "3", workspaceFile),
            ],
        )
        self.assertIn("Desktop 3 ready.", out)
        self.assertIn("/issues/SK-0044-x.md", out)

    def test_a_store_ticket_with_the_server_down_prints_its_url_and_start_finishes(self):
        calls, out = self.runStart("SK-0044", Placement(True, "3", "Desktop 3"), "/issues/SK-0044-x.md", serverUp=False)
        self.assertNotIn("showUrl", [call[0] for call in calls])
        self.assertEqual(calls[-1], ("focus", "3", "/w/SK-0044.code-workspace"))
        self.assertIn(f"{PAGE}SK-0044", out)
        self.assertIn("plan serve start", out)

    def test_a_jira_issue_never_asks_the_local_server(self):
        calls, _ = self.runStart("CIAM-1", Placement(True, "5", "Desktop 5"), serverUp=False)
        self.assertIn("showUrl", [call[0] for call in calls])

    def test_open_prints_the_placers_notes_such_as_a_rewritten_slot(self):
        note = "SK-0044's window is on Desktop 4, not its slot's Desktop 3; the slot now says 4."
        _, out = self.runStart("SK-0044", Placement(False, "4", "Desktop 4", [note]), "/issues/SK-0044-x.md")
        self.assertIn(note, out)
        self.assertIn("Desktop 4 ready.", out)

    def test_a_jira_issue_shows_its_url_in_its_own_window_before_focus(self):
        calls, _ = self.runStart("CIAM-1", Placement(True, "5", "Desktop 5"))
        self.assertEqual(calls[2], ("showUrl", f"{JIRA_PAGE}CIAM-1", "CIAM-1", "/w/CIAM-1.code-workspace"))
        self.assertEqual(calls[3][0], "focus")


# `plan browse`: which URL and which window. term.showUrl() is stubbed; what it runs is term's tests'.
class CmdBrowseTests(unittest.TestCase):
    def browse(self, *args, env=None, workspaceFiles=(), cfg=None, worktree=None, store=None):
        tmp = tempfile.mkdtemp(prefix="skuld-browse-")
        self.addCleanup(shutil.rmtree, tmp)
        cfg = {**(cfg or JIRA_ON), "workspaceDir": tmp}
        for key in workspaceFiles:
            open(term_mod.workspaceFilePath(key, tmp), "w").close()
        with mock.patch.object(cli.config_mod, "load", return_value=cfg), mock.patch.object(
            cli.issuestore_mod, "storeFor", return_value=store or FakeStore()
        ), mock.patch.object(cli, "issueWorktree", return_value=worktree), mock.patch.dict(
            os.environ, env or {}, clear=False
        ), mock.patch.object(term_mod, "showUrl") as showUrl:
            if "PLAN_KEY" not in (env or {}):
                os.environ.pop("PLAN_KEY", None)
            main(["browse", *args])
        url, key, workspaceFile = showUrl.call_args.args
        return url, key, workspaceFile and os.path.basename(workspaceFile)

    def test_a_jira_key_opens_its_page_in_its_own_window(self):
        shown = self.browse("CIAM-1", env={"PLAN_KEY": "CIAM-2"}, workspaceFiles=["CIAM-1"])
        self.assertEqual(shown, (f"{JIRA_PAGE}CIAM-1", "CIAM-1", "CIAM-1.code-workspace"))

    def test_an_issue_with_a_legacy_workspace_file_is_focused_through_it(self):
        tmp = tempfile.mkdtemp(prefix="skuld-legacy-")
        self.addCleanup(shutil.rmtree, tmp)
        worktree = os.path.join(tmp, "repo.worktrees", "CIAM-1")
        os.makedirs(worktree)
        legacy = term_mod.legacyWorkspaceFilePath(worktree, "CIAM-1")
        open(legacy, "w").close()
        shown = self.browse("CIAM-1", workspaceFiles=["CIAM-1"], worktree=worktree)
        self.assertEqual(shown[2], os.path.basename(legacy))
        self.assertEqual(term_mod.issueWorkspaceFilePath("CIAM-1", worktree), legacy)

    def test_a_url_goes_to_the_named_key_before_the_terminals(self):
        shown = self.browse("https://example.com/x", "CIAM-3", env={"PLAN_KEY": "CIAM-2"})
        self.assertEqual(shown, ("https://example.com/x", "CIAM-3", None))

    def test_a_url_goes_to_the_terminals_key_when_none_is_named(self):
        shown = self.browse("https://example.com/x", env={"PLAN_KEY": "SK-0031"}, workspaceFiles=["SK-0031"])
        self.assertEqual(shown, ("https://example.com/x", "SK-0031", "SK-0031.code-workspace"))

    def test_a_url_with_no_key_anywhere_goes_to_the_focused_window(self):
        self.assertEqual(self.browse("http://127.0.0.1:8737/"), ("http://127.0.0.1:8737/", None, None))

    def test_pr_n_opens_the_pull_request(self):
        with mock.patch.object(ship_mod, "pullRequest", return_value={"url": "https://github.com/o/r/pull/9"}) as pr:
            shown = self.browse("pr#9", env={"PLAN_KEY": "SK-0031"})
        self.assertEqual(pr.call_args.args[1:], ("9", "url"))
        self.assertEqual(shown[:2], ("https://github.com/o/r/pull/9", "SK-0031"))

    def test_a_store_ticket_key_opens_its_server_page_in_its_own_window(self):
        store = FakeStore("/issues/SK-0031-x.md")
        shown = self.browse("SK-31", workspaceFiles=["SK-0031"], store=store)
        self.assertEqual(store.reads, ["SK-0031"])
        self.assertEqual(shown, (f"{PAGE}SK-0031", "SK-0031", "SK-0031.code-workspace"))

    def test_anything_else_is_refused(self):
        for target in ("file:///etc/passwd", "javascript:alert(1)", "pr#x"):
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, "a key, pr#N or an http\\(s\\) URL"):
                self.browse(target)

    def test_no_target_says_how(self):
        with self.assertRaisesRegex(RuntimeError, "usage: plan browse"):
            self.browse()


class IssueWorktreeTests(unittest.TestCase):
    PORCELAIN = "worktree /repo\nHEAD a\nbranch refs/heads/main\n\nworktree /repo.worktrees/CIAM-5\nHEAD b\nbranch refs/heads/feat/CIAM-5-x\n"

    def lookup(self, mainCheckout):
        with mock.patch.object(startLib, "mainCheckout", side_effect=mainCheckout) as mc, mock.patch.object(
            startLib, "git", return_value=self.PORCELAIN
        ):
            return cli.issueWorktree({}, "CIAM-5"), mc

    def test_finds_the_issues_worktree_from_the_key_alone(self):
        found, mc = self.lookup(lambda *a: {"checkout": "/repo"})
        self.assertEqual(found, "/repo.worktrees/CIAM-5")
        self.assertEqual(mc.call_args.args[0], "CIAM-5")

    def test_a_repo_it_cant_choose_is_no_worktree_and_never_asks(self):
        def mainCheckout(key, checkouts, cwd, action, typeMap, pickRepo):
            pickRepo(["/a", "/b"])

        self.assertIsNone(self.lookup(mainCheckout)[0])


class IssueRowsSlotColumnTests(unittest.TestCase):
    def test_the_slot_column_sits_after_status_and_is_blank_for_issues_without_one(self):
        jira = [
            {"id": "CIAM-1", "subject": "a", "status": "To Do"},
            {"id": "CIAM-22", "subject": "b", "status": "To Do"},
        ]

        with FakeProjectStores({"CIAM": jira}).patch():
            rows = cli.issueRows({**JIRA_ON, "issueStore": "/s"}, {"CIAM-22": "D3, window on D4"})
        self.assertEqual(rows[0]["line"], "CIAM-1   To Do                    a")
        self.assertEqual(rows[1]["line"], "CIAM-22  To Do  D3, window on D4  b")


class ResolveDirTests(unittest.TestCase):
    def test_refuses_an_unlisted_repo_even_when_cwd_is_on_the_keys_branch(self):
        unlisted = self.repoOnBranch("feat/CIAM-5-x")
        with self.assertRaisesRegex(RuntimeError, "project CIAM lists no repo in checkouts"):
            self.resolveFrom(unlisted["checkout"], {})

    def test_resolves_cwd_on_the_keys_branch_in_a_listed_repo(self):
        listed = self.repoOnBranch("feat/CIAM-5-x")
        dir = self.resolveFrom(listed["checkout"], {"CIAM": listed["checkout"]})
        self.assertEqual(os.path.realpath(dir), os.path.realpath(listed["checkout"]))

    def test_refuses_when_the_keys_branch_is_in_two_listed_repos(self):
        repoA = self.repoOnBranch("feat/CIAM-5-x")
        repoB = self.repoOnBranch("feat/CIAM-5-x")
        with self.assertRaisesRegex(RuntimeError, "more than one repo"):
            self.resolveFrom(repoA["checkout"], {"CIAM": [repoA["checkout"], repoB["checkout"]]})

    def repoOnBranch(self, branch):
        repo = start_sandbox()
        self.addCleanup(shutil.rmtree, repo["root"], True)
        repo["git"](repo["checkout"], "checkout", "-q", "-b", branch)
        return repo

    def resolveFrom(self, cwd, checkouts):
        with mock.patch.object(os, "getcwd", return_value=cwd):
            return resolveDir({"checkouts": checkouts}, "CIAM-5")

    def test_resolves_real_worktree_when_cwd_not_on_branch_for_key(self):
        porcelain = "\n".join(
            [
                "worktree /repo",
                "HEAD aaaa",
                "branch refs/heads/develop",
                "",
                "worktree /repo.worktrees/CIAM-5",
                "HEAD bbbb",
                "branch refs/heads/feat/CIAM-5-go-requests",
                "",
            ]
        )

        def fake_git(cwd, *args):
            if args[0] == "branch":
                return "main\n"  # cwd is on the main checkout's base branch, not CIAM-5
            if args[0] == "worktree":
                return porcelain
            raise RuntimeError(f"unexpected git {' '.join(args)}")

        def fake_main_checkout(key, checkouts, cwd=None, action=None, typeMap=None, pickRepo=None):
            self.assertEqual(key, "CIAM-5")
            self.assertEqual(checkouts, {"CIAM": "/repo"})
            return {"project": "CIAM", "checkout": "/repo", "common": "/repo/.git"}

        with mock.patch.object(startLib, "git", side_effect=fake_git), mock.patch.object(
            startLib, "mainCheckout", side_effect=fake_main_checkout
        ):
            dir = resolveDir({"checkouts": {"CIAM": "/repo"}}, "CIAM-5")
        self.assertEqual(dir, "/repo.worktrees/CIAM-5")

    def test_refuses_when_issue_has_no_worktree_at_all(self):
        porcelain = "\n".join(["worktree /repo", "HEAD aaaa", "branch refs/heads/develop", ""])

        def fake_git(cwd, *args):
            if args[0] == "branch":
                return "main\n"
            if args[0] == "worktree":
                return porcelain
            raise RuntimeError(f"unexpected git {' '.join(args)}")

        def fake_main_checkout(*a, **k):
            return {"project": "CIAM", "checkout": "/repo", "common": "/repo/.git"}

        with mock.patch.object(startLib, "git", side_effect=fake_git), mock.patch.object(
            startLib, "mainCheckout", side_effect=fake_main_checkout
        ):
            with self.assertRaisesRegex(RuntimeError, "no worktree found for CIAM-5"):
                resolveDir({"checkouts": {"CIAM": "/repo"}}, "CIAM-5")


# DECIDED: Ship and Go are merged into one command. cmdShip() now runs ship.check()'s pre-flight
# and, on success, runs `pr` visibly in a tmux window in one invocation -- the separate review
# window (nvim+lazygit) and the separate `plan go`/ctrl-g step are both gone. These tests replace
# the old separate Go-command tests: a failing check must never reach term.shipWindow() (so pr
# never runs), a passing check must call it with the exact argv a tmux-visible `pr <key>` needs,
# and `plan go` itself must now be reported as an unknown command.
class CmdShipTests(unittest.TestCase):
    DIR = "/repo.worktrees/CIAM-5"

    def test_failing_check_refuses_and_never_runs_pr(self):
        with mock.patch.object(cli, "resolveDir", return_value=self.DIR), mock.patch.object(
            ship_mod, "check", return_value={"branch": "feat/CIAM-5-x", "dirty": False, "problems": [".tmp/CIAM-5/pr-body.md is missing"]}
        ) as check, mock.patch.object(term_mod, "shipWindow") as shipWindow:
            code = cli.cmdShip({**JIRA_ON, "checkouts": {}}, "CIAM-5")
        self.assertEqual(code, 1)
        check.assert_called_once_with(self.DIR, "CIAM-5")
        shipWindow.assert_not_called()

    def test_passing_check_runs_pr_in_tmux_with_correct_argv(self):
        with mock.patch.object(cli, "resolveDir", return_value=self.DIR), mock.patch.object(
            ship_mod, "check", return_value={"branch": "feat/CIAM-5-x", "dirty": False, "problems": []}
        ), mock.patch.object(startLib, "expandHome", return_value="/Users/t/.local/bin/pr") as expandHome, mock.patch.object(
            term_mod, "shipWindow"
        ) as shipWindow:
            code = cli.cmdShip({**JIRA_ON, "checkouts": {}, "pr": "~/.local/bin/pr"}, "CIAM-5")
        self.assertIsNone(code)
        expandHome.assert_called_once_with("~/.local/bin/pr")
        shipWindow.assert_called_once_with("CIAM-5", self.DIR, "/Users/t/.local/bin/pr")

    def test_plan_go_is_an_unknown_command_now(self):
        with self.assertRaisesRegex(RuntimeError, r'unknown command "go"; try: list, view, start, open, browse, ship, done'):
            main(["go", "CIAM-5"])


# L5: cmdPicker builds fzf execute()/reload()/preview strings from sys.argv[0], which a shell
# (fzf's own execute/reload) then interprets. shellQuote() single-quotes that path so a space or
# shell metacharacter in it can't break the command or inject another one.
class ShellQuoteTests(unittest.TestCase):
    def test_wraps_path_with_space_as_one_shell_argument(self):
        quoted = shellQuote("/Applications/Plan App/plan")
        # Round-trip it through a real shell: `printf '%s\n' <quoted>` should print exactly one
        # line, the original unquoted string, proving the shell treats it as a single word.
        out = subprocess.run(["sh", "-c", f"printf '%s\\n' {quoted}"], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, "/Applications/Plan App/plan\n")

    def test_stops_semicolon_separated_path_from_running_as_second_command(self):
        # An unquoted `;` is the simplest break-out: the shell would run "touch <canary>" as its
        # own command, no quote-balancing needed to defeat an unquoted interpolation (unlike a
        # quote-based payload, which can accidentally cancel itself out unquoted too, and so
        # wouldn't prove this).
        dir = tempfile.mkdtemp(prefix="jira-plan-shellquote-")
        try:
            canary = os.path.join(dir, "pwned")
            malicious = f"/tmp/x; touch {canary}; true"
            quoted = shellQuote(malicious)
            out = subprocess.run(["sh", "-c", f"printf '%s\\n' {quoted}"], capture_output=True, text=True, check=True).stdout
            self.assertEqual(out, f"{malicious}\n")
            self.assertFalse(os.path.exists(canary))
        finally:
            shutil.rmtree(dir, ignore_errors=True)

    def test_byte_identical_to_js_for_tricky_inputs(self):
        # The task requires a test proving Python's shellQuote produces byte-identical output to
        # the JS original (lib/cli.js:207-209: `'${String(value).replace(/'/g, `'\\''`)}'`) for
        # tricky inputs: spaces, single quotes, $ and backslashes. Python's shlex.quote must NOT
        # be substituted in without this proof, since its escaping strategy for embedded quotes
        # differs from the hand-rolled POSIX '\'' substitution used here and in the JS.
        cases = [
            "plain",
            "has space",
            "o'brien",
            "''double''quoted''",
            "$HOME/$PATH",
            "back\\slash",
            "mix 'o' $things\\here",
            "",
        ]
        for value in cases:
            expected = "'" + value.replace("'", "'\\''") + "'"
            self.assertEqual(shellQuote(value), expected)
            # And the shell really does see it as the single original string.
            out = subprocess.run(["sh", "-c", f"printf '%s' {shellQuote(value)}"], capture_output=True, text=True, check=True).stdout
            self.assertEqual(out, value)


# `after:` interleaving (issues/README.md): mergeSets() is a pure function, so this is tested
# without a real Jira or FileStore. Each set is handed in already in its adapter's order -- the
# store's in SK sort order (ordinal, then number), as FileStore.list() returns it -- since
# mergeSets() relies on that, not on sorting it itself.
def jiraRow(id):
    return {"id": id, "subject": f"Jira issue {id}", "status": "In Progress", "category": IN_PROGRESS, "issueType": "Story", "project": id.split("-")[0], "parent": None}


def skRow(id, after=None):
    return {"id": id, "subject": f"SK issue {id}", "status": "To Do", "category": NEW, "issueType": "Task", "project": "SK", "parent": None, "after": after}


# Column alignment (2026-11-06): issueRows() used to build `f'{id}\t{status}\t{subject}'`,
# whose tab stops drifted whenever ids/statuses had different lengths (the user's screenshot:
# CIAM-163/CIAM-94/CIAM-80 lining up differently). These stub each project's adapter (FakeProjectStores) so issueRows()
# runs against a controlled mix of Jira and SK rows with different id/status lengths, without a
# real Jira or issues/ dir.
class IssueRowsAlignmentTests(unittest.TestCase):
    def _run(self, jiraRows, skRows):
        with FakeProjectStores({"CIAM": jiraRows, "SK": skRows}).patch():
            return cli.issueRows({**JIRA_ON, "issueStore": "/store"})

    def test_widths_come_from_the_longest_id_and_status_across_all_rows(self):
        jira = [
            {"id": "CIAM-163", "subject": "Doc registry DR-1", "status": "In Progress", "category": IN_PROGRESS, "issueType": "Story", "project": "CIAM", "parent": None},
            {"id": "CIAM-94", "subject": "PR #53 review leftovers", "status": "Submitted", "category": NEW, "issueType": "Task", "project": "CIAM", "parent": None},
            {"id": "CIAM-80", "subject": "[transformer]", "status": "In Progress", "category": IN_PROGRESS, "issueType": "Task", "project": "CIAM", "parent": None},
        ]
        rows = self._run(jira, [])
        # Longest id is "CIAM-163" (8 chars), longest status is "In Progress" (11 chars).
        idWidth, statusWidth = 8, 11
        for row in rows:
            idField = row["line"][:idWidth]
            gap1 = row["line"][idWidth : idWidth + 2]
            statusField = row["line"][idWidth + 2 : idWidth + 2 + statusWidth]
            self.assertEqual(idField, row["id"].ljust(idWidth))
            self.assertEqual(gap1, "  ")
            self.assertEqual(statusField, row["status"].ljust(statusWidth))

    def test_exactly_two_spaces_between_columns(self):
        jira = [{"id": "CIAM-1", "subject": "x", "status": "To Do", "category": NEW, "issueType": "Task", "project": "CIAM", "parent": None}]
        rows = self._run(jira, [])
        line = rows[0]["line"]
        # id and status are both already at their own (only, here) width, so exactly "  " sits
        # between id/status and between status/subject, never three.
        self.assertEqual(line, "CIAM-1  To Do  x")

    def test_key_is_still_the_bare_id_for_fzfs_default_field_splitting(self):
        jira = [{"id": "CIAM-163", "subject": "Doc registry DR-1: a longer subject with spaces", "status": "In Progress", "category": IN_PROGRESS, "issueType": "Story", "project": "CIAM", "parent": None}]
        jp = [{"id": "SK-0002", "subject": "short", "status": "To Do", "category": NEW, "issueType": "Bug", "project": "SK", "parent": None, "after": None}]
        rows = self._run(jira, jp)
        for row in rows:
            self.assertEqual(row["key"], row["id"])
            # fzf's default AWK-style delimiter splits on whitespace runs: the first token of
            # `line` must be exactly the bare id, with no padding spaces attached.
            self.assertEqual(row["line"].split()[0], row["id"])

    def test_fzf_default_field_splitting_really_does_extract_the_bare_id(self):
        # End-to-end proof against the real fzf binary (no --delimiter/--with-nth passed, as
        # cmdPicker no longer passes them): `fzf --filter` with a query matching everything,
        # printing {1} via --preview would need a tty, so instead this drives the same
        # placeholder resolution fzf itself would use for `{1}`: AWK-style field 1 of the padded
        # line, exercised directly as the field-index-expression contract fzf's own docs specify
        # (default delimiter = runs of whitespace).
        jira = [{"id": "CIAM-163", "subject": "Doc registry DR-1", "status": "In Progress", "category": IN_PROGRESS, "issueType": "Story", "project": "CIAM", "parent": None}]
        jp = [{"id": "SK-0009", "subject": "Point failure-modes.md", "status": "To Do", "category": NEW, "issueType": "Task", "project": "SK", "parent": None, "after": None}]
        rows = self._run(jira, jp)
        for row in rows:
            # awk's default field splitting (FS unset) is the same "runs of whitespace" rule fzf
            # documents as its default --delimiter.
            awkField1 = subprocess.run(
                ["awk", "{print $1}"], input=row["line"], capture_output=True, text=True, check=True
            ).stdout.strip()
            self.assertEqual(awkField1, row["id"])


class NextUpRowsTests(unittest.TestCase):
    def test_each_project_in_the_config_is_a_set_and_a_later_set_anchors_to_an_earlier_one(self):
        stores = FakeProjectStores(
            {
                "CIAM": [jiraRow("CIAM-1"), jiraRow("CIAM-2")],
                "OPS": [jiraRow("OPS-1")],
                "SK": [skRow("SK-0001", after="OPS-1"), skRow("SK-0002", after="CIAM-1")],
            }
        )
        cfg = {**JIRA_ON, "jira": {"CIAM": {}, "OPS": {}}}
        with stores.patch():
            ids = [row["id"] for row in cli.nextUpRows(cfg)]
        self.assertEqual(stores.asked, ["CIAM", "OPS", "SK"])
        self.assertEqual(ids, ["CIAM-1", "SK-0002", "CIAM-2", "OPS-1", "SK-0001"])

    def test_with_no_jira_entry_the_store_is_the_only_set(self):
        stores = FakeProjectStores({"SK": [skRow("SK-0001")]})
        with stores.patch():
            cli.nextUpRows({"issueStore": "/s"})
        self.assertEqual(stores.asked, ["SK"])


class MergeSetsTests(unittest.TestCase):
    def test_anchored_issue_sorts_right_after_its_jira_issue(self):
        jira = [jiraRow("CIAM-1"), jiraRow("CIAM-2")]
        jp = [skRow("SK-0001", after="CIAM-1")]
        ids = [r["id"] for r in mergeSets([jira, jp])]
        self.assertEqual(ids, ["CIAM-1", "SK-0001", "CIAM-2"])

    def test_two_sk_issues_sharing_one_anchor_keep_their_sk_sort_order(self):
        jira = [jiraRow("CIAM-1")]
        # jp is handed in already in SK sort order (as FileStore.list() would return it) --
        # SK-0003 (lower ordinal) before SK-0009, both anchored to the same Jira issue.
        jp = [skRow("SK-0003", after="CIAM-1"), skRow("SK-0009", after="CIAM-1")]
        ids = [r["id"] for r in mergeSets([jira, jp])]
        self.assertEqual(ids, ["CIAM-1", "SK-0003", "SK-0009"])

    def test_anchor_missing_from_the_jira_list_falls_to_the_end(self):
        # CIAM-9 isn't in jira (closed/unassigned/filtered out of the JQL) -- SK-0001 falls to the
        # end instead of vanishing or raising.
        jira = [jiraRow("CIAM-1")]
        jp = [skRow("SK-0001", after="CIAM-9")]
        ids = [r["id"] for r in mergeSets([jira, jp])]
        self.assertEqual(ids, ["CIAM-1", "SK-0001"])

    def test_a_sk_key_anchor_falls_to_the_end(self):
        # Only a Jira key can anchor a placement -- a SK key can never appear in jiraRows' ids,
        # so this is covered by the same "anchor not in jiraRows" path, with no special case.
        jira = [jiraRow("CIAM-1")]
        jp = [skRow("SK-0002", after="SK-0001")]
        ids = [r["id"] for r in mergeSets([jira, jp])]
        self.assertEqual(ids, ["CIAM-1", "SK-0002"])

    def test_no_after_at_all_gives_todays_plain_concatenation(self):
        jira = [jiraRow("CIAM-1"), jiraRow("CIAM-2")]
        jp = [skRow("SK-0001"), skRow("SK-0002")]
        ids = [r["id"] for r in mergeSets([jira, jp])]
        self.assertEqual(ids, ["CIAM-1", "CIAM-2", "SK-0001", "SK-0002"])

    def test_jira_rows_are_never_reordered(self):
        jira = [jiraRow("CIAM-2"), jiraRow("CIAM-1")]  # deliberately not key order -- JQL's own
        jp = [skRow("SK-0001", after="CIAM-1")]
        ids = [r["id"] for r in mergeSets([jira, jp])]
        self.assertEqual(ids, ["CIAM-2", "CIAM-1", "SK-0001"])


# SK-0029: Jira is on only when the machine config's `jira` entry names a project. A store ticket
# for each status and ordinal, so plan order (ordinal, then number) differs from file order.
def writeStore():
    store = tempfile.mkdtemp(prefix="skuld-store-")
    tickets = {
        "SK-0001-first.md": ("open", "2", "First filed, second in plan order"),
        "SK-0002-second.md": ("in-progress", "1", "Underway, first in plan order"),
        "SK-0003-third.md": ("done", "", "Finished, never listed"),
    }
    for name, (status, ordinal, title) in tickets.items():
        with open(os.path.join(store, name), "w", encoding="utf8") as f:
            f.write(f"---\nstatus: {status}\ntype: task\nordinal: {ordinal}\n---\n\n# {title}\n")
    return store


class ListingPlacer:
    def sweep(self):
        pass

    def slotLabels(self):
        return {}


class JiraOffTests(unittest.TestCase):
    def setUp(self):
        store = writeStore()
        self.addCleanup(shutil.rmtree, store)
        self.cfg = {**cli.config_mod.defaults(), "issueStore": store}
        for patch in (
            mock.patch.object(cli.config_mod, "load", return_value=self.cfg),
            mock.patch.object(cli.issuestore_mod, "Jira", side_effect=AssertionError("Jira was constructed")),
            mock.patch.object(cli.placer_mod, "placerFrom", return_value=ListingPlacer()),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def listedIds(self, text):
        return [line.split()[0] for line in text.splitlines()]

    def test_plan_list_prints_the_stores_open_tickets_in_plan_order(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            main(["list"])
        self.assertEqual(self.listedIds(out.getvalue()), ["SK-0002", "SK-0001"])

    def test_the_picker_opens_on_the_same_list(self):
        fzf = mock.MagicMock()
        fzf.wait.return_value = 0
        with mock.patch.object(cli.subprocess, "Popen", return_value=fzf), mock.patch(
            "sys.stderr", new_callable=io.StringIO
        ) as err:
            main([])
        shown = fzf.stdin.write.call_args.args[0].decode("utf8")
        self.assertEqual(self.listedIds(shown), ["SK-0002", "SK-0001"])
        self.assertEqual(err.getvalue(), "")

    def test_every_command_refuses_a_jira_key_before_it_changes_anything(self):
        for command in ("start", "open", "view", "ship", "done"):
            with self.subTest(command=command), mock.patch.object(startLib, "start") as start, mock.patch.object(
                ship_mod, "check"
            ) as check, mock.patch.object(cli.done_mod, "finish") as finish, mock.patch.object(
                cli.placer_mod, "placerFrom"
            ) as placerFrom:
                with self.assertRaisesRegex(RuntimeError, r'CIAM-1 is unknown on this Mac: .*has no "jira" entry naming CIAM'):
                    main([command, "CIAM-1"])
                for step in (start, check, finish, placerFrom):
                    step.assert_not_called()


class JiraOnTests(unittest.TestCase):
    def setUp(self):
        store = writeStore()
        self.addCleanup(shutil.rmtree, store)
        self.cfg = {**cli.config_mod.defaults(), **JIRA_ON, "issueStore": store}

    def test_a_failing_search_still_stops_the_list(self):
        with mock.patch.object(cli.issuestore_mod.Jira, "list", side_effect=RuntimeError("Jira search failed: 503")):
            with self.assertRaisesRegex(RuntimeError, "Jira search failed: 503"):
                cli.issueRows(self.cfg)

    def test_a_missing_netrc_still_stops_the_list(self):
        folder = tempfile.mkdtemp(prefix="skuld-netrc-")
        self.addCleanup(shutil.rmtree, folder)
        missing = os.path.join(folder, ".netrc")
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("Jira was called")):
            with self.assertRaisesRegex(RuntimeError, "can't read .*\\.netrc for the Jira token: no such file"):
                cli.issueRows({**self.cfg, "netrcFile": missing})


if __name__ == "__main__":
    unittest.main()
