# M4: cmdShip used os.getcwd() as the worktree dir unconditionally. That's right when Peter types
# `plan ship` from inside the issue's own worktree, but wrong for the picker's ctrl-s bind
# (cmdPicker shells out `plan ship {1}` from wherever `plan` itself was launched -- never
# necessarily the issue's worktree). resolveDir() is the fix: it keeps cwd when cwd is already on
# a branch for the key, and otherwise resolves the issue's real worktree the same way Start/Done do
# (mainCheckout + existingWorktree, from the key alone). These tests mock lib.start's module
# functions directly -- cli.py calls them as `startLib.git(...)`/`startLib.mainCheckout(...)`
# (module attribute access, not imported names), so patching the shared module's attributes is
# visible to cli.py immediately.
#
# Ported from lib/cli.test.js. Faithful, behavior-preserving port.
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
from lib.cli import main, resolveDir, shellQuote


class ResolveDirTests(unittest.TestCase):
    def test_keeps_cwd_when_already_on_branch_for_key(self):
        def fake_git(cwd, *args):
            self.assertEqual(list(args), ["branch", "--show-current"])
            return "feat/CIAM-5-go-requests\n"

        def fake_main_checkout(*a, **k):
            raise RuntimeError("resolveDir should not need mainCheckout when cwd already matches")

        class FakeJira:
            def issue(self, key):
                raise RuntimeError("resolveDir should not need jira.issue when cwd already matches")

        with mock.patch.object(startLib, "git", side_effect=fake_git), mock.patch.object(
            startLib, "mainCheckout", side_effect=fake_main_checkout
        ) as mc:
            dir = resolveDir({"checkouts": {}}, FakeJira(), "CIAM-5")
            self.assertEqual(dir, os.getcwd())
            self.assertEqual(mc.call_count, 0)

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
    def _onBranch(self, branch):
        def fake_git(cwd, *args):
            self.assertEqual(list(args), ["branch", "--show-current"])
            return f"{branch}\n"

        return fake_git

    def test_failing_check_refuses_and_never_runs_pr(self):
        with mock.patch.object(startLib, "git", side_effect=self._onBranch("feat/CIAM-5-x")), mock.patch.object(
            ship_mod, "check", return_value={"branch": "feat/CIAM-5-x", "dirty": False, "problems": [".tmp/CIAM-5/pr-body.md is missing"]}
        ) as check, mock.patch.object(term_mod, "shipWindow") as shipWindow:
            code = cli.cmdShip({"checkouts": {}}, "CIAM-5")
        self.assertEqual(code, 1)
        check.assert_called_once_with(os.getcwd(), "CIAM-5")
        shipWindow.assert_not_called()

    def test_passing_check_runs_pr_in_tmux_with_correct_argv(self):
        with mock.patch.object(startLib, "git", side_effect=self._onBranch("feat/CIAM-5-x")), mock.patch.object(
            ship_mod, "check", return_value={"branch": "feat/CIAM-5-x", "dirty": False, "problems": []}
        ), mock.patch.object(startLib, "expandHome", return_value="/Users/t/.local/bin/pr") as expandHome, mock.patch.object(
            term_mod, "shipWindow"
        ) as shipWindow:
            code = cli.cmdShip({"checkouts": {}, "pr": "~/.local/bin/pr"}, "CIAM-5")
        self.assertIsNone(code)
        expandHome.assert_called_once_with("~/.local/bin/pr")
        shipWindow.assert_called_once_with("CIAM-5", os.getcwd(), "/Users/t/.local/bin/pr")

    def test_plan_go_is_an_unknown_command_now(self):
        with self.assertRaisesRegex(RuntimeError, r'unknown command "go"; try: list, view, start, open, ship, done'):
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


if __name__ == "__main__":
    unittest.main()
