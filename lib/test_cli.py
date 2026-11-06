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
from lib.cli import main, mergeRows, resolveDir, shellQuote


# `start` and `open` are the same command (main() routes both to cmdStart -- a second Open on an
# issue that already has a worktree is just Start finding "nothing to do" and reusing it), so
# there is no separate cmdOpen to give its own file-backed branch: cmdStart's is the only one, and
# it already runs on an Open. This test proves exactly that "Open on a JP key opens the ticket
# pane" case, calling cmdStart through main(["open", ...]) rather than main(["start", ...]), on a
# key fileissues.isFileBacked() reports True for, with term.openIssueWorkspace() already reporting
# an existing session/workspace (created: False) -- the idempotent case a second Open on the same
# issue hits.
class CmdOpenFileBackedTests(unittest.TestCase):
    def test_open_on_a_jp_key_opens_the_ticket_pane(self):
        class FakeJira:
            def browseUrl(self, key):
                return "/repo/issues/JP-1-slug.md"

        with mock.patch.object(cli, "jiraFrom", return_value=FakeJira()), mock.patch.object(
            term_mod, "sweepSlots"
        ), mock.patch.object(
            startLib, "start", return_value={"dir": "/repo.worktrees/JP-1", "notes": []}
        ), mock.patch.object(
            term_mod, "openIssueWorkspace", return_value={"created": False, "workspace": "3"}
        ) as openIssueWorkspace, mock.patch.object(
            term_mod, "openTicketPane"
        ) as openTicketPane, mock.patch.object(
            term_mod, "openUrl"
        ) as openUrl, mock.patch.object(
            term_mod, "focusIssueWorkspace"
        ):
            main(["open", "JP-1"])
        openIssueWorkspace.assert_called_once()
        openTicketPane.assert_called_once_with("JP-1", "/repo.worktrees/JP-1", "/repo/issues/JP-1-slug.md")
        openUrl.assert_not_called()  # a JP key has no browse URL to open in a browser


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


# `after:` interleaving (2026-11-06, issues/README.md): issueRows() factored mergeRows(jiraRows,
# jpRows) out as a pure function so this can be tested without a real Jira or FileIssues. jpRows
# below is handed in already in JP sort order (rank, then number) -- the same guarantee
# lib/fileissues.py's FileIssues.list() makes -- since mergeRows() relies on that, not on sorting
# it itself.
def jiraRow(id):
    return {"id": id, "subject": f"Jira issue {id}", "status": "In Progress", "statusCategory": "indeterminate", "issueType": "Story", "project": id.split("-")[0], "parent": None}


def jpRow(id, after=None):
    return {"id": id, "subject": f"JP issue {id}", "status": "To Do", "statusCategory": "new", "issueType": "Task", "project": "JP", "parent": None, "after": after}


# Column alignment (2026-11-06): issueRows() used to build `f'{id}\t{status}\t{subject}'`,
# whose tab stops drifted whenever ids/statuses had different lengths (the user's screenshot:
# CIAM-163/CIAM-94/CIAM-80 lining up differently). These mock jiraFrom()/FileIssues so issueRows()
# runs against a controlled mix of Jira and JP rows with different id/status lengths, without a
# real Jira or issues/ dir.
class IssueRowsAlignmentTests(unittest.TestCase):
    def _run(self, jiraRows, jpRows):
        class FakeJira:
            def list(self, jql=None):
                return jiraRows

        class FakeFileIssues:
            def __init__(self, settings=None):
                pass

            def list(self, jql=None):
                return jpRows

        with mock.patch.object(cli, "jiraFrom", return_value=FakeJira()), mock.patch.object(
            cli.fileissues_mod, "FileIssues", FakeFileIssues
        ):
            return cli.issueRows({})

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
        jp = [{"id": "JP-2", "subject": "short", "status": "To Do", "statusCategory": "new", "issueType": "Bug", "project": "JP", "parent": None, "after": None}]
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
        jp = [{"id": "JP-9", "subject": "Point failure-modes.md", "status": "To Do", "statusCategory": "new", "issueType": "Task", "project": "JP", "parent": None, "after": None}]
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
        jp = [jpRow("JP-1", after="CIAM-1")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "JP-1", "CIAM-2"])

    def test_two_jp_issues_sharing_one_anchor_keep_their_jp_sort_order(self):
        jira = [jiraRow("CIAM-1")]
        # jp is handed in already in JP sort order (as FileIssues.list() would return it) --
        # JP-3 (lower rank) before JP-9, both anchored to the same Jira issue.
        jp = [jpRow("JP-3", after="CIAM-1"), jpRow("JP-9", after="CIAM-1")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "JP-3", "JP-9"])

    def test_anchor_missing_from_the_jira_list_falls_to_the_end(self):
        # CIAM-9 isn't in jira (closed/unassigned/filtered out of the JQL) -- JP-1 falls to the
        # end instead of vanishing or raising.
        jira = [jiraRow("CIAM-1")]
        jp = [jpRow("JP-1", after="CIAM-9")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "JP-1"])

    def test_a_jp_key_anchor_falls_to_the_end(self):
        # Only a Jira key can anchor a placement -- a JP key can never appear in jiraRows' ids,
        # so this is covered by the same "anchor not in jiraRows" path, with no special case.
        jira = [jiraRow("CIAM-1")]
        jp = [jpRow("JP-2", after="JP-1")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "JP-2"])

    def test_no_after_at_all_gives_todays_plain_concatenation(self):
        jira = [jiraRow("CIAM-1"), jiraRow("CIAM-2")]
        jp = [jpRow("JP-1"), jpRow("JP-2")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-1", "CIAM-2", "JP-1", "JP-2"])

    def test_jira_rows_are_never_reordered(self):
        jira = [jiraRow("CIAM-2"), jiraRow("CIAM-1")]  # deliberately not key order -- JQL's own
        jp = [jpRow("JP-1", after="CIAM-1")]
        ids = [r["id"] for r in mergeRows(jira, jp)]
        self.assertEqual(ids, ["CIAM-2", "CIAM-1", "JP-1"])


if __name__ == "__main__":
    unittest.main()
