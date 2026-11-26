# The herdr front end (lib/frontend.py): one herdr workspace per issue, labelled with its key, with
# a claude, an edit and a git tab in the issue's worktree. Every call is the `herdr` CLI, run
# without a shell; the one thing typed into a tab's shell is a fixed command line, which reads the
# plan path and the ticket path from the tab's environment.
import json
import os
import shlex

from . import config as config_mod
from . import term as term_mod
from .start import run

# Typed into each tab's shell. Fixed text: what varies arrives in the tab's environment.
CLAUDE_COMMAND = '"$PLAN_COMMAND" claude-session'
# Stock nvim: Lexplore lists the working directory (the worktree) beside the ticket.
# shortcut: needs netrw; revisit when an nvim config here replaces it with another file tree.
EDIT_TICKET_COMMAND = 'nvim -c "Lexplore ." -c "wincmd p" -- "$PLAN_TICKET"'
EDIT_COMMAND = "nvim ."
GIT_COMMAND = "lazygit"


class Herdr:
    # A workspace list needs a running server and changes nothing.
    def check(self, command, key):
        try:
            workspaces()
        except RuntimeError as error:
            raise RuntimeError(
                f"herdr can't be reached: {error}\n"
                f"Start it with `herdr`, then run plan {command} {key} again; or set "
                f'"frontEnd": "vscode" in {config_mod.CONFIG_FILE}'
            ) from error

    # An existing workspace is only focused, so nothing is started in it twice. The lock keeps two
    # runs at once from both finding none and each making one.
    def show(self, issue):
        with term_mod.locked(os.path.join(os.path.dirname(issue.planWorkspace), "herdr")):
            focus(reusedWorkspace(issue.key) or createWorkspace(issue))


def herdr(*args):
    return run("herdr", list(args), os.getcwd())


def herdrResult(*args):
    return json.loads(herdr(*args))["result"]


def workspaces():
    return herdrResult("workspace", "list")["workspaces"]


# The id of the workspace labelled label, or None.
def workspaceLabelled(label):
    return next((w["workspace_id"] for w in workspaces() if w["label"] == label), None)


# A failed step is named, with what to run by hand.
def attempt(name, byHand, call, *args):
    with term_mod.step(name, byHand):
        return call(*args)


# The id of key's workspace when it has one, saying it is left as it is, else None. A workspace
# herdr restored after a restart, or one with a tab closed, is not told apart from a working one.
def reusedWorkspace(key):
    byHand = f"Start herdr with `herdr`, then run: plan open {key}"
    workspace = attempt("listing the herdr workspaces", byHand, workspaceLabelled, key)
    if workspace:
        print(
            f"{key} has a herdr workspace already, so nothing is started in it. "
            f"If its tabs are empty or missing, {startOver(workspace, key)}"
        )
    return workspace


# What to run so that Open makes the workspace again: Open only focuses one that exists.
def startOver(workspace, key):
    return f"run: herdr workspace close {workspace}, then: plan open {key}"


def createWorkspace(issue):
    where = ["--cwd", issue.worktree, "--no-focus"]
    claude = ["workspace", "create", "--label", issue.key, *where, *envFlags(claudeEnv(issue))]
    byHand = f"Run: plan open {issue.key} (first closing a {issue.key} workspace if `herdr workspace list` shows one)"
    created = attempt(f"creating the {issue.key} herdr workspace", byHand, herdrResult, *claude)
    workspace = created["workspace"]["workspace_id"]
    # Running one failed command by hand would still skip the steps after it, so start over.
    byHand = f"To start over, {startOver(workspace, issue.key)}"
    attempt("naming the claude tab", byHand, herdr, "tab", "rename", created["tab"]["tab_id"], "claude")
    runInPane("claude", created["root_pane"]["pane_id"], CLAUDE_COMMAND, byHand)
    for label, env, command in (editTab(issue), ("git", keyEnv(issue), GIT_COMMAND)):
        create = ["tab", "create", "--workspace", workspace, "--label", label, *where, *envFlags(env)]
        tab = attempt(f"creating the {label} tab", byHand, herdrResult, *create)
        runInPane(label, tab["root_pane"]["pane_id"], command, byHand)
    return workspace


def keyEnv(issue):
    return {"PLAN_KEY": issue.key}


def claudeEnv(issue):
    return {**keyEnv(issue), "PLAN_PROMPT": issue.prompt, "PLAN_COMMAND": term_mod.PLAN_COMMAND}


# The edit tab's label, environment and command. A Jira issue has no ticket file to open.
def editTab(issue):
    if not issue.ticket:
        return "edit", keyEnv(issue), EDIT_COMMAND
    return "edit", {**keyEnv(issue), "PLAN_TICKET": issue.ticket}, EDIT_TICKET_COMMAND


def envFlags(env):
    return [part for name, value in env.items() for part in ("--env", f"{name}={value}")]


def runInPane(label, pane, command, byHand):
    attempt(f"starting the {label} tab's command", byHand, herdr, "pane", "run", pane, command)


def focus(workspace):
    args = ["workspace", "focus", workspace]
    attempt("focusing the herdr workspace", f"Run it by hand: {shlex.join(['herdr', *args])}", herdr, *args)
