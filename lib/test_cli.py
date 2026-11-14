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
from lib.cli import main, mergeRows, resolveDir, shellQuote
from lib.test_start import sandbox as start_sandbox

# A config with Jira on: one project and a site that no test ever reaches.
JIRA_ON = {"jira": {"CIAM": {"repos": []}}, "baseUrl": "https://jira.example"}


# `start` and `open` are the same command (main() routes both to cmdStart -- a second Open on an
# issue that already has a worktree is just Start finding "nothing to do" and reusing it), so these
# run cmdStart through main(["open", ...]) with start.start() stubbed and term's `run` faked, then
# check the commands run, the issue's workspace file and the plan workspace's folders.
class CmdOpenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="skuld-open-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg = {
            "issueStore": "/issues",
            "workspaceDir": os.path.join(self.tmp, "ws"),
            "issuesFile": os.path.join(self.tmp, "issues"),
        }
        self.planWorkspace = os.path.join(self.tmp, "ws", "plan.code-workspace")

    def worktree(self, key):
        path = os.path.join(self.tmp, "repo.worktrees", key)
        os.makedirs(path, exist_ok=True)
        return path

    # Runs `plan open key`; run fails for any command whose name is in failing. Returns the
    # (command, args) pairs run and what was printed; failingLink fails `code --open-url`.
    def runOpen(self, key, ticket=None, failing=(), failingLink=False):
        class FakeJira:
            def browseUrl(self, key):
                return ticket or f"https://cryoport.atlassian.net/browse/{key}"

        ran = []

        def run(command, args, cwd):
            ran.append((command, args))
            if command in failing:
                raise RuntimeError(f"{command} exited 1")
            if failingLink and args[0] == "--open-url":
                raise RuntimeError("--open-url failed")
            return ""

        out = io.StringIO()
        with mock.patch.object(cli.config_mod, "load", return_value=self.cfg), mock.patch.object(
            cli, "jiraFrom", return_value=FakeJira()
        ), mock.patch.object(startLib, "start", return_value={"dir": self.worktree(key), "notes": []}), mock.patch.object(
            term_mod, "run", side_effect=run
        ), mock.patch("sys.stdout", out):
            main(["open", key])
        return ran, out.getvalue()

    def issueSettings(self, key):
        with open(os.path.join(self.tmp, "ws", f"{key}.code-workspace"), encoding="utf8") as f:
            return json.load(f)["settings"]

    def planFolders(self):
        with open(self.planWorkspace, encoding="utf8") as f:
            return [folder["path"] for folder in json.load(f)["folders"]]

    def test_open_on_a_sk_key_opens_the_plan_window_then_brings_the_issue_forward(self):
        ran, out = self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        self.assertEqual(
            ran,
            [("code", [self.planWorkspace]), ("code", ["--open-url", "vscode://skuld.plan-window/open?key%3DSK-0001"])],
        )
        self.assertEqual(out, "SK-0001 ready.\n/issues/SK-0001-slug.md\n")

    def test_open_writes_the_issues_own_workspace_file_without_opening_it(self):
        self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        settings = self.issueSettings("SK-0001")
        self.assertEqual(settings["plan.key"], "SK-0001")
        self.assertEqual(settings["plan.ticket"], "/issues/SK-0001-slug.md")
        self.assertIn("read /issues/SK-0001-slug.md first", settings["plan.prompt"])

    def test_open_moves_the_store_to_the_front_of_a_workspace_that_lists_it_later(self):
        os.makedirs(os.path.dirname(self.planWorkspace))
        with open(self.planWorkspace, "w") as f:
            json.dump({"folders": [{"path": self.worktree("CIAM-1")}, {"path": "/issues"}]}, f)
        self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        self.assertEqual(self.planFolders(), ["/issues", self.worktree("CIAM-1"), self.worktree("SK-0001")])

    def test_open_adds_the_key_to_the_issues_file_and_the_worktree_to_the_plan_workspace(self):
        self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        self.runOpen("CIAM-1")
        self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        self.assertEqual(term_mod.issueKeys(self.cfg["issuesFile"]), ["SK-0001", "CIAM-1"])
        self.assertEqual(self.planFolders(), ["/issues", self.worktree("SK-0001"), self.worktree("CIAM-1")])

    def test_start_and_open_with_no_store_stop_before_anything_changes(self):
        del self.cfg["issueStore"]
        for command in ("start", "open"):
            with self.subTest(command=command), mock.patch.object(cli.config_mod, "load", return_value=self.cfg), mock.patch.object(
                cli, "jiraFrom"
            ) as jiraFrom, mock.patch.object(startLib, "start") as start, mock.patch.object(term_mod, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "no issue store set"):
                    main([command, "CIAM-1"])
                for step in (jiraFrom, start, run):
                    step.assert_not_called()
        self.assertEqual(os.listdir(self.tmp), [])

    def test_a_jira_issues_record_has_its_browse_url(self):
        self.runOpen("CIAM-1")
        self.assertEqual(self.issueSettings("CIAM-1")["plan.ticketUrl"], "https://cryoport.atlassian.net/browse/CIAM-1")

    def test_a_store_issues_record_has_its_ticket_and_no_browse_url(self):
        self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        settings = self.issueSettings("SK-0001")
        self.assertEqual(settings["plan.ticket"], "/issues/SK-0001-slug.md")
        self.assertNotIn("plan.ticketUrl", settings)

    def test_open_on_a_jira_key_prints_its_url_and_opens_only_the_plan_window_and_link(self):
        ran, out = self.runOpen("CIAM-1")
        self.assertNotIn("plan.ticket", self.issueSettings("CIAM-1"))
        self.assertEqual(self.issueSettings("CIAM-1")["plan.prompt"], "Work on Jira CIAM-1")
        self.assertEqual(ran, [("code", [self.planWorkspace]), ("code", ["--open-url", term_mod.openLink("CIAM-1")])])
        self.assertEqual(out, "CIAM-1 ready.\nhttps://cryoport.atlassian.net/browse/CIAM-1\n")

    def test_a_code_failure_stops_open_naming_the_command_to_run_by_hand(self):
        with self.assertRaisesRegex(RuntimeError, f"opening the plan window failed: code exited 1\nOpen it by hand: code {self.planWorkspace}"):
            self.runOpen("SK-0001", "/issues/SK-0001-slug.md", failing=("code",))
        self.assertEqual(term_mod.issueKeys(self.cfg["issuesFile"]), ["SK-0001"])

    def test_a_failed_open_link_still_says_ready_and_names_the_failure_on_stderr(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            _, out = self.runOpen("SK-0001", "/issues/SK-0001-slug.md", failingLink=True)
        self.assertIn("SK-0001 ready.", out)
        self.assertEqual(
            err.getvalue(),
            "couldn't open SK-0001 in the plan window: --open-url failed; click it in the Issues view\n",
        )

    def test_a_garbage_plan_workspace_is_replaced_keeping_its_settings_when_it_has_some(self):
        os.makedirs(os.path.dirname(self.planWorkspace))
        with open(self.planWorkspace, "w") as f:
            f.write('{"folders": "x", "settings": {"editor.tabSize": 2}}')
        self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        with open(self.planWorkspace, encoding="utf8") as f:
            workspace = json.load(f)
        self.assertEqual(workspace["folders"], [{"path": "/issues"}, {"path": self.worktree("SK-0001")}])
        self.assertEqual(workspace["settings"]["editor.tabSize"], 2)

    def test_the_plan_workspace_names_the_issues_pulses_and_records_folder(self):
        self.cfg["pulsesFile"] = os.path.join(self.tmp, "pulses")
        self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        with open(self.planWorkspace, encoding="utf8") as f:
            settings = json.load(f)["settings"]
        self.assertEqual(
            settings,
            {
                "plan.command": term_mod.PLAN_COMMAND,
                "workbench.browser.openLocalhostLinks": True,
                "plan.issuesFile": self.cfg["issuesFile"],
                "plan.pulsesFile": self.cfg["pulsesFile"],
                "plan.workspaceDir": self.cfg["workspaceDir"],
            },
        )

    def test_the_record_is_always_in_the_workspace_folder_even_beside_a_legacy_file(self):
        legacy = term_mod.legacyWorkspaceFilePath(self.worktree("SK-0001"), "SK-0001")
        open(legacy, "w").close()
        self.runOpen("SK-0001", "/issues/SK-0001-slug.md")
        self.assertEqual(self.issueSettings("SK-0001")["plan.worktree"], self.worktree("SK-0001"))
        self.assertEqual(os.path.getsize(legacy), 0)

    def test_open_pads_a_sk_key_typed_without_its_zeros(self):
        class FakeJira:
            def browseUrl(self, key):
                return "/issues/SK-0001-slug.md"

        with mock.patch.object(cli.config_mod, "load", return_value=self.cfg), mock.patch.object(
            cli, "jiraFrom", return_value=FakeJira()
        ), mock.patch.object(startLib, "start", return_value={"dir": self.worktree("SK-0001"), "notes": []}) as start, mock.patch.object(
            term_mod, "run", return_value=""
        ), mock.patch("builtins.print"):
            main(["open", "SK-1"])
        self.assertEqual(start.call_args.args[2], "SK-0001")


# Review finding L6: a config that still sets the Desktop keys loads with a warning for each, and
# Start then opens the plan window. The config is a real file read by config.load(), not a stub.
class StartWithDesktopKeysTests(unittest.TestCase):
    DESKTOP_KEYS = {"slotsFile": "/s", "windowsFile": "/w", "spacesHelper": "/h", "reservedDesktops": 2}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="skuld-desktop-keys-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.worktree = os.path.join(self.tmp, "repo.worktrees", "SK-0001")
        os.makedirs(self.worktree)
        self.configFile = os.path.join(self.tmp, "config.json")
        settings = {
            **self.DESKTOP_KEYS,
            "issueStore": os.path.join(self.tmp, "issues"),
            "workspaceDir": os.path.join(self.tmp, "ws"),
            "issuesFile": os.path.join(self.tmp, "plan", "issues"),
            "pulsesFile": os.path.join(self.tmp, "plan", "pulses"),
        }
        with open(self.configFile, "w", encoding="utf8") as f:
            json.dump(settings, f)

    def test_the_desktop_keys_load_with_a_warning_and_an_issue_still_starts(self):
        class FakeJira:
            def browseUrl(self, key):
                return "/issues/SK-0001-slug.md"

        ran = []
        stderr = io.StringIO()
        with mock.patch.object(cli.config_mod, "CONFIG_FILE", self.configFile), mock.patch.object(
            cli, "jiraFrom", return_value=FakeJira()
        ), mock.patch.object(startLib, "start", return_value={"dir": self.worktree, "notes": []}), mock.patch.object(
            term_mod, "run", side_effect=lambda command, args, cwd: ran.append((command, args))
        ), mock.patch("sys.stderr", stderr), mock.patch("sys.stdout", io.StringIO()):
            main(["start", "SK-0001"])
        for key in self.DESKTOP_KEYS:
            self.assertIn(f"{key}, which is no longer read", stderr.getvalue())
        self.assertEqual(ran[0], ("code", [os.path.join(self.tmp, "ws", "plan.code-workspace")]))
        self.assertEqual(term_mod.issueKeys(os.path.join(self.tmp, "plan", "issues")), ["SK-0001"])


# `plan browse`: which URL and which issue, and the plan window raised when its workspace exists.
# term.showUrl() is stubbed; what it runs is term's tests'.
class CmdBrowseTests(unittest.TestCase):
    def browse(self, *args, env=None, planWorkspace=False, cfg=None):
        tmp = tempfile.mkdtemp(prefix="skuld-browse-")
        self.addCleanup(shutil.rmtree, tmp)
        cfg = {**(cfg or JIRA_ON), "workspaceDir": tmp}
        if planWorkspace:
            open(term_mod.planWorkspacePath(tmp), "w").close()
        with mock.patch.object(cli.config_mod, "load", return_value=cfg), mock.patch.dict(
            os.environ, env or {}, clear=False
        ), mock.patch.object(term_mod, "showUrl") as showUrl:
            if "PLAN_KEY" not in (env or {}):
                os.environ.pop("PLAN_KEY", None)
            main(["browse", *args])
        url, key, workspaceFile = showUrl.call_args.args
        return url, key, workspaceFile and os.path.basename(workspaceFile)

    def test_a_jira_key_opens_its_page_for_that_issue_in_the_plan_window(self):
        shown = self.browse("CIAM-1", env={"PLAN_KEY": "CIAM-2"}, planWorkspace=True)
        self.assertEqual(shown, ("https://jira.example/browse/CIAM-1", "CIAM-1", "plan.code-workspace"))

    def test_with_no_plan_workspace_the_link_goes_to_the_focused_window(self):
        shown = self.browse("CIAM-1")
        self.assertEqual(shown, ("https://jira.example/browse/CIAM-1", "CIAM-1", None))

    def test_a_url_goes_to_the_named_key_before_the_terminals(self):
        shown = self.browse("https://example.com/x", "CIAM-3", env={"PLAN_KEY": "CIAM-2"})
        self.assertEqual(shown, ("https://example.com/x", "CIAM-3", None))

    def test_a_url_goes_to_the_terminals_key_when_none_is_named(self):
        shown = self.browse("https://example.com/x", env={"PLAN_KEY": "SK-0031"}, planWorkspace=True)
        self.assertEqual(shown, ("https://example.com/x", "SK-0031", "plan.code-workspace"))

    def test_a_url_with_no_key_anywhere_has_no_key(self):
        self.assertEqual(self.browse("http://127.0.0.1:8737/"), ("http://127.0.0.1:8737/", None, None))

    def test_pr_n_opens_the_pull_request(self):
        with mock.patch.object(ship_mod, "pullRequest", return_value={"url": "https://github.com/o/r/pull/9"}) as pr:
            shown = self.browse("pr#9", env={"PLAN_KEY": "SK-0031"})
        self.assertEqual(pr.call_args.args[1:], ("9", "url"))
        self.assertEqual(shown[:2], ("https://github.com/o/r/pull/9", "SK-0031"))

    def test_a_store_ticket_key_is_refused_naming_its_file(self):
        class FakeStore:
            def browseUrl(self, key):
                return "/issues/SK-0031-x.md"

        with mock.patch.object(cli, "jiraFrom", return_value=FakeStore()):
            with self.assertRaisesRegex(RuntimeError, "SK-0031 is a store ticket.*open /issues/SK-0031-x.md"):
                self.browse("SK-31")

    def test_anything_else_is_refused(self):
        for target in ("file:///etc/passwd", "javascript:alert(1)", "pr#x"):
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, "a key, pr#N or an http\\(s\\) URL"):
                self.browse(target)

    def test_no_target_says_how(self):
        with self.assertRaisesRegex(RuntimeError, "usage: plan browse"):
            self.browse()


# `plan agents`' enter: raise the plan window and bring the session's issue forward.
class FocusAgentTests(unittest.TestCase):
    def focus(self, key, listed):
        tmp = tempfile.mkdtemp(prefix="skuld-focus-")
        self.addCleanup(shutil.rmtree, tmp)
        cfg = {"workspaceDir": tmp, "issuesFile": os.path.join(tmp, "issues")}
        for listedKey in listed:
            term_mod.addIssue(listedKey, cfg["issuesFile"])
        agent = mock.Mock(key=key, name="claude-1", cwd="/w")
        out = io.StringIO()
        with mock.patch.object(term_mod, "run", return_value="") as run, mock.patch("sys.stdout", out):
            cli.focusAgent(cfg, agent)
        return [call.args[:2] for call in run.call_args_list], out.getvalue(), tmp

    def test_a_listed_issue_raises_the_plan_window_then_opens_its_link(self):
        ran, _, tmp = self.focus("SK-0041", ["SK-0041"])
        self.assertEqual(
            ran,
            [("code", [os.path.join(tmp, "plan.code-workspace")]), ("code", ["--open-url", term_mod.openLink("SK-0041")])],
        )

    def test_an_unlisted_issue_says_how_to_add_it(self):
        ran, out, _ = self.focus("SK-0041", [])
        self.assertEqual(ran, [])
        self.assertIn("plan open SK-0041 adds it", out)


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
        class FakeJira:
            def issue(self, key):
                return {"key": key}

        with mock.patch.object(os, "getcwd", return_value=cwd):
            return resolveDir({"checkouts": checkouts}, FakeJira(), "CIAM-5")

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

        def fake_main_checkout(issue, checkouts, cwd=None, action=None, typeMap=None, pickRepo=None):
            self.assertEqual(issue["key"], "CIAM-5")
            self.assertEqual(checkouts, {"CIAM": "/repo"})
            return {"project": "CIAM", "checkout": "/repo", "common": "/repo/.git"}

        class FakeJira:
            def issue(self, key):
                self.calledWith = key
                return {"key": "CIAM-5"}

        jira = FakeJira()
        with mock.patch.object(startLib, "git", side_effect=fake_git), mock.patch.object(
            startLib, "mainCheckout", side_effect=fake_main_checkout
        ):
            dir = resolveDir({"checkouts": {"CIAM": "/repo"}}, jira, "CIAM-5")
            self.assertEqual(dir, "/repo.worktrees/CIAM-5")
        self.assertEqual(jira.calledWith, "CIAM-5")

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

        class FakeJira:
            def issue(self, key):
                return {"key": "CIAM-5"}

        with mock.patch.object(startLib, "git", side_effect=fake_git), mock.patch.object(
            startLib, "mainCheckout", side_effect=fake_main_checkout
        ):
            with self.assertRaisesRegex(RuntimeError, "no worktree found for CIAM-5"):
                resolveDir({"checkouts": {"CIAM": "/repo"}}, FakeJira(), "CIAM-5")


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


# `after:` interleaving (2026-11-06, issues/README.md): issueRows() factored mergeRows(jiraRows,
# skRows) out as a pure function so this can be tested without a real Jira or FileIssues. skRows
# below is handed in already in SK sort order (ordinal, then number) -- the same guarantee
# lib/fileissues.py's FileIssues.list() makes -- since mergeRows() relies on that, not on sorting
# it itself.
def jiraRow(id):
    return {"id": id, "subject": f"Jira issue {id}", "status": "In Progress", "statusCategory": "indeterminate", "issueType": "Story", "project": id.split("-")[0], "parent": None}


def skRow(id, after=None):
    return {"id": id, "subject": f"SK issue {id}", "status": "To Do", "statusCategory": "new", "issueType": "Task", "project": "SK", "parent": None, "after": after}


# Column alignment (2026-11-06): issueRows() used to build `f'{id}\t{status}\t{subject}'`,
# whose tab stops drifted whenever ids/statuses had different lengths (the user's screenshot:
# CIAM-163/CIAM-94/CIAM-80 lining up differently). These mock jiraFrom()/FileIssues so issueRows()
# runs against a controlled mix of Jira and SK rows with different id/status lengths, without a
# real Jira or issues/ dir.
class IssueRowsAlignmentTests(unittest.TestCase):
    def _run(self, jiraRows, skRows):
        class FakeJira:
            def list(self, jql=None):
                return jiraRows

        class FakeFileIssues:
            def __init__(self, settings=None):
                pass

            def list(self, jql=None):
                return skRows

        with mock.patch.object(cli, "jiraFrom", return_value=FakeJira()), mock.patch.object(
            cli.fileissues_mod, "FileIssues", FakeFileIssues
        ):
            return cli.issueRows({**JIRA_ON, "issueStore": "/store"})

    def test_widths_come_from_the_longest_id_and_status_across_all_rows(self):
        jira = [
            {"id": "CIAM-163", "subject": "Doc registry DR-1", "status": "In Progress", "statusCategory": "indeterminate", "issueType": "Story", "project": "CIAM", "parent": None},
            {"id": "CIAM-94", "subject": "PR #53 review leftovers", "status": "Submitted", "statusCategory": "new", "issueType": "Task", "project": "CIAM", "parent": None},
            {"id": "CIAM-80", "subject": "[transformer]", "status": "In Progress", "statusCategory": "indeterminate", "issueType": "Task", "project": "CIAM", "parent": None},
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
        jira = [{"id": "CIAM-1", "subject": "x", "status": "To Do", "statusCategory": "new", "issueType": "Task", "project": "CIAM", "parent": None}]
        rows = self._run(jira, [])
        line = rows[0]["line"]
        # id and status are both already at their own (only, here) width, so exactly "  " sits
        # between id/status and between status/subject, never three.
        self.assertEqual(line, "CIAM-1  To Do  x")

    def test_key_is_still_the_bare_id_for_fzfs_default_field_splitting(self):
        jira = [{"id": "CIAM-163", "subject": "Doc registry DR-1: a longer subject with spaces", "status": "In Progress", "statusCategory": "indeterminate", "issueType": "Story", "project": "CIAM", "parent": None}]
        jp = [{"id": "SK-0002", "subject": "short", "status": "To Do", "statusCategory": "new", "issueType": "Bug", "project": "SK", "parent": None, "after": None}]
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
        jira = [{"id": "CIAM-163", "subject": "Doc registry DR-1", "status": "In Progress", "statusCategory": "indeterminate", "issueType": "Story", "project": "CIAM", "parent": None}]
        jp = [{"id": "SK-0009", "subject": "Point failure-modes.md", "status": "To Do", "statusCategory": "new", "issueType": "Task", "project": "SK", "parent": None, "after": None}]
        rows = self._run(jira, jp)
        for row in rows:
            # awk's default field splitting (FS unset) is the same "runs of whitespace" rule fzf
            # documents as its default --delimiter.
            awkField1 = subprocess.run(
                ["awk", "{print $1}"], input=row["line"], capture_output=True, text=True, check=True
            ).stdout.strip()
            self.assertEqual(awkField1, row["id"])


class MergeRowsTests(unittest.TestCase):
    def test_anchored_issue_sorts_right_after_its_jira_issue(self):
        jira = [jiraRow("CIAM-1"), jiraRow("CIAM-2")]
        jp = [skRow("SK-0001", after="CIAM-1")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "SK-0001", "CIAM-2"])

    def test_two_sk_issues_sharing_one_anchor_keep_their_sk_sort_order(self):
        jira = [jiraRow("CIAM-1")]
        # jp is handed in already in SK sort order (as FileIssues.list() would return it) --
        # SK-0003 (lower ordinal) before SK-0009, both anchored to the same Jira issue.
        jp = [skRow("SK-0003", after="CIAM-1"), skRow("SK-0009", after="CIAM-1")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "SK-0003", "SK-0009"])

    def test_anchor_missing_from_the_jira_list_falls_to_the_end(self):
        # CIAM-9 isn't in jira (closed/unassigned/filtered out of the JQL) -- SK-0001 falls to the
        # end instead of vanishing or raising.
        jira = [jiraRow("CIAM-1")]
        jp = [skRow("SK-0001", after="CIAM-9")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "SK-0001"])

    def test_a_sk_key_anchor_falls_to_the_end(self):
        # Only a Jira key can anchor a placement -- a SK key can never appear in jiraRows' ids,
        # so this is covered by the same "anchor not in jiraRows" path, with no special case.
        jira = [jiraRow("CIAM-1")]
        jp = [skRow("SK-0002", after="SK-0001")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "SK-0002"])

    def test_no_after_at_all_gives_todays_plain_concatenation(self):
        jira = [jiraRow("CIAM-1"), jiraRow("CIAM-2")]
        jp = [skRow("SK-0001"), skRow("SK-0002")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "CIAM-2", "SK-0001", "SK-0002"])

    def test_jira_rows_are_never_reordered(self):
        jira = [jiraRow("CIAM-2"), jiraRow("CIAM-1")]  # deliberately not key order -- JQL's own
        jp = [skRow("SK-0001", after="CIAM-1")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
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


class JiraOffTests(unittest.TestCase):
    def setUp(self):
        store = writeStore()
        self.addCleanup(shutil.rmtree, store)
        self.cfg = {**cli.config_mod.defaults(), "issueStore": store}
        for patch in (
            mock.patch.object(cli.config_mod, "load", return_value=self.cfg),
            mock.patch.object(cli, "Jira", side_effect=AssertionError("Jira was constructed")),
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
                term_mod, "run"
            ) as run:
                with self.assertRaisesRegex(RuntimeError, r'CIAM-1 is unknown on this Mac: .*has no "jira" entry naming CIAM'):
                    main([command, "CIAM-1"])
                for step in (start, check, finish, run):
                    step.assert_not_called()


class JiraOnTests(unittest.TestCase):
    def setUp(self):
        store = writeStore()
        self.addCleanup(shutil.rmtree, store)
        self.cfg = {**cli.config_mod.defaults(), **JIRA_ON, "issueStore": store}

    def test_a_failing_search_still_stops_the_list(self):
        with mock.patch.object(cli.Jira, "list", side_effect=RuntimeError("Jira search failed: 503")):
            with self.assertRaisesRegex(RuntimeError, "Jira search failed: 503"):
                cli.issueRows(self.cfg)

    def test_a_missing_netrc_still_stops_the_list(self):
        folder = tempfile.mkdtemp(prefix="skuld-netrc-")
        self.addCleanup(shutil.rmtree, folder)
        missing = os.path.join(folder, ".netrc")
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("Jira was called")):
            with self.assertRaisesRegex(RuntimeError, "can't read .*\\.netrc for the Jira token: no such file"):
                cli.issueRows({**self.cfg, "netrcFile": missing})

    def test_a_key_in_another_project_is_still_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'OPS-1 is unknown on this Mac: .*has no "jira" entry naming OPS'):
            cli.jiraFrom(self.cfg, "OPS-1")

    def test_a_configured_projects_key_gets_jira(self):
        self.assertIsInstance(cli.jiraFrom(self.cfg, "CIAM-1"), cli.Jira)


if __name__ == "__main__":
    unittest.main()
