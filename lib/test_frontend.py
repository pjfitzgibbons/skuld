# Both front-end adapters (lib/frontend.py) against fakes: `herdr` and `code` are never run.
import contextlib
import fcntl
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from lib import frontend
from lib import herdr as herdr_mod
from lib import term as term_mod

TICKET = "/issues/active/SK-0001-slug.md"
PROMPT = 'Work on SK-0001; "quoted" $(touch never) `ls`'
# Herdr takes a lock file beside the plan workspace.
WORKSPACES = tempfile.TemporaryDirectory(prefix="skuld-frontend-")
PLAN_WORKSPACE = os.path.join(WORKSPACES.name, "plan.code-workspace")


def issue(ticket=TICKET):
    return frontend.IssueToShow("SK-0001", "/repo.worktrees/SK-0001", PROMPT, ticket, PLAN_WORKSPACE)


# The `herdr` CLI's workspace, tab and pane commands, answering in its JSON shapes. failing names
# the first two words of a command that fails.
class FakeHerdr:
    def __init__(self, labels=(), failing=None, during=lambda verb: None):
        self.workspaces = [{"workspace_id": f"w{n}", "label": label} for n, label in enumerate(labels, 1)]
        self.failing = failing
        self.during = during
        self.calls = []
        self.panes = 0

    def run(self, command, args, cwd):
        assert command == "herdr", command
        self.calls.append(args)
        verb = " ".join(args[:2])
        self.during(verb)
        if verb == self.failing:
            raise RuntimeError(f"herdr {verb} failed")
        return json.dumps({"result": self.result(verb, args)})

    def result(self, verb, args):
        if verb == "workspace list":
            return {"workspaces": self.workspaces}
        if verb == "workspace create":
            label = args[args.index("--label") + 1]
            self.workspaces.append({"workspace_id": "w9", "label": label})
            return {"workspace": {"workspace_id": "w9"}, **self.newTab()}
        if verb == "tab create":
            return self.newTab()
        return {}

    def newTab(self):
        self.panes += 1
        return {"tab": {"tab_id": f"w9:t{self.panes}"}, "root_pane": {"pane_id": f"w9:p{self.panes}"}}

    def verbs(self):
        return [" ".join(args[:2]) for args in self.calls]

    def flagValues(self, args, flag):
        return [value for name, value in zip(args, args[1:]) if name == flag]

    # { tab label: (env, command run in it) }, from the calls.
    def tabs(self):
        creates = [args for args in self.calls if " ".join(args[:2]) in ("workspace create", "tab create")]
        commands = [args[3] for args in self.calls if args[:2] == ["pane", "run"]]
        labels = ["claude", *[self.flagValues(args, "--label")[0] for args in creates[1:]]]
        envs = [dict(value.split("=", 1) for value in self.flagValues(args, "--env")) for args in creates]
        return dict(zip(labels, zip(envs, commands)))


class HerdrTests(unittest.TestCase):
    def use(self, fake):
        patch = mock.patch.object(herdr_mod, "run", side_effect=fake.run)
        patch.start()
        self.addCleanup(patch.stop)
        return fake

    def test_a_new_issue_gets_a_workspace_with_three_tabs_in_its_worktree_and_is_focused(self):
        fake = self.use(FakeHerdr(labels=["skuld"]))
        herdr_mod.Herdr().show(issue())
        self.assertEqual(
            fake.verbs(),
            [
                "workspace list",
                "workspace create",
                "tab rename",
                "pane run",
                "tab create",
                "pane run",
                "tab create",
                "pane run",
                "workspace focus",
            ],
        )
        self.assertEqual(fake.calls[2], ["tab", "rename", "w9:t1", "claude"])
        self.assertEqual(fake.calls[-1], ["workspace", "focus", "w9"])
        for args in fake.calls:
            if args[1] == "create":
                self.assertEqual(fake.flagValues(args, "--cwd"), ["/repo.worktrees/SK-0001"])
        self.assertEqual(
            fake.tabs(),
            {
                "claude": (
                    {"PLAN_KEY": "SK-0001", "PLAN_PROMPT": PROMPT, "PLAN_COMMAND": term_mod.PLAN_COMMAND},
                    '"$PLAN_COMMAND" claude-session',
                ),
                "edit": (
                    {"PLAN_KEY": "SK-0001", "PLAN_TICKET": TICKET},
                    'nvim -c "Lexplore ." -c "wincmd p" -- "$PLAN_TICKET"',
                ),
                "git": ({"PLAN_KEY": "SK-0001"}, "lazygit"),
            },
        )

    def test_no_prompt_or_ticket_path_is_typed_into_a_shell(self):
        fake = self.use(FakeHerdr())
        herdr_mod.Herdr().show(issue())
        for args in fake.calls:
            if args[:2] == ["pane", "run"]:
                self.assertNotIn("SK-0001", args[3])
                self.assertNotIn("/issues", args[3])

    def test_a_jira_issues_edit_tab_opens_nvim_on_the_worktree_alone(self):
        fake = self.use(FakeHerdr())
        herdr_mod.Herdr().show(issue(ticket=None))
        self.assertEqual(fake.tabs()["edit"], ({"PLAN_KEY": "SK-0001"}, "nvim ."))

    def test_an_existing_workspace_is_only_focused_saying_how_to_start_it_again(self):
        fake = self.use(FakeHerdr(labels=["skuld", "SK-0001"]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            herdr_mod.Herdr().show(issue())
        self.assertEqual(fake.calls, [["workspace", "list"], ["workspace", "focus", "w2"]])
        self.assertIn("nothing is started in it", out.getvalue())
        self.assertIn("run: herdr workspace close w2, then: plan open SK-0001", out.getvalue())

    def test_a_second_run_waits_while_the_workspace_is_looked_up_and_made(self):
        free = []

        def during(verb):
            with open(f"{os.path.join(WORKSPACES.name, 'herdr')}.lock", "a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    free.append(verb)
                except BlockingIOError:
                    pass

        fake = self.use(FakeHerdr(during=during))
        herdr_mod.Herdr().show(issue())
        self.assertEqual(free, [])
        self.assertIn("workspace create", fake.verbs())

    def test_a_longer_key_is_not_taken_for_the_issues_workspace(self):
        fake = self.use(FakeHerdr(labels=["SK-00012"]))
        herdr_mod.Herdr().show(issue())
        self.assertIn("workspace create", fake.verbs())

    def test_no_server_refuses_naming_herdr_the_setting_and_the_command_to_re_run(self):
        self.use(FakeHerdr(failing="workspace list"))
        with self.assertRaises(RuntimeError) as refusal:
            herdr_mod.Herdr().check("start", "SK-0001")
        self.assertIn("Start it with `herdr`, then run plan start SK-0001 again", str(refusal.exception))
        self.assertIn('"frontEnd": "vscode"', str(refusal.exception))

    def failureOf(self, failing, labels=()):
        self.use(FakeHerdr(labels=labels, failing=failing))
        with self.assertRaises(RuntimeError) as failure, contextlib.redirect_stdout(io.StringIO()):
            herdr_mod.Herdr().show(issue())
        return str(failure.exception)

    def test_a_server_gone_since_the_check_says_to_start_it_and_open_again(self):
        message = self.failureOf("workspace list")
        self.assertIn("listing the herdr workspaces failed", message)
        self.assertIn("Start herdr with `herdr`, then run: plan open SK-0001", message)

    def test_a_failed_create_names_the_step_and_says_to_open_again(self):
        message = self.failureOf("workspace create")
        self.assertIn("creating the SK-0001 herdr workspace failed", message)
        self.assertIn("Run: plan open SK-0001", message)
        self.assertNotIn("herdr workspace create --label", message)

    def test_a_failure_after_the_workspace_exists_says_how_to_start_over(self):
        steps = {
            "tab rename": "naming the claude tab",
            "pane run": "starting the claude tab's command",
            "tab create": "creating the edit tab",
        }
        for failing, step in steps.items():
            with self.subTest(failing=failing):
                message = self.failureOf(failing)
                self.assertIn(f"{step} failed", message)
                self.assertIn("To start over, run: herdr workspace close w9, then: plan open SK-0001", message)

    def test_a_failed_focus_names_the_command_to_run_by_hand(self):
        message = self.failureOf("workspace focus", labels=["SK-0001"])
        self.assertTrue(message.endswith("Run it by hand: herdr workspace focus w1"), message)


class VSCodeTests(unittest.TestCase):
    def test_show_opens_the_plan_window_then_brings_the_issue_forward(self):
        ran = []
        with mock.patch.object(term_mod, "run", side_effect=lambda c, a, cwd: ran.append((c, a))):
            frontend.VSCode().show(issue())
        self.assertEqual(
            ran,
            [("code", [PLAN_WORKSPACE]), ("code", ["--open-url", term_mod.openLink("SK-0001")])],
        )

    def test_check_is_the_extension_link_check(self):
        with mock.patch.object(term_mod, "checkExtensionLink") as checkLink:
            frontend.VSCode().check("open", "SK-0001")
        checkLink.assert_called_once_with("open", "SK-0001")


class FrontEndForTests(unittest.TestCase):
    def test_vscode_is_the_default(self):
        self.assertIsInstance(frontend.frontEndFor({}), frontend.VSCode)

    def test_each_name_picks_its_adapter(self):
        self.assertIsInstance(frontend.frontEndFor({"frontEnd": "vscode"}), frontend.VSCode)
        self.assertIsInstance(frontend.frontEndFor({"frontEnd": "herdr"}), herdr_mod.Herdr)

    def test_any_other_value_is_refused_naming_the_key_and_the_two_values(self):
        for value in ("other", "Herdr", None, ["herdr"]):
            with self.subTest(value=value), self.assertRaisesRegex(
                RuntimeError, r'"frontEnd" in .*config\.json must be "vscode" or "herdr", not '
            ):
                frontend.frontEndFor({"frontEnd": value})


if __name__ == "__main__":
    unittest.main()
