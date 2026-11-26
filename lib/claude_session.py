# `plan claude-session`: becomes the issue's Claude in the herdr claude tab. A herdr pane inherits
# the herdr server's environment, which a client can add to but not unset, so the start rules run
# here, in the pane. They are the ones vscode/plan-window/plan.js applies to its Claude terminal;
# the two must stay equal.
import os
import re
from pathlib import Path

# Names where Claude keeps its config and conversations, not a session.
KEPT_CLAUDE_VARIABLES = ("CLAUDE_CONFIG_DIR",)
# Claude Code cuts a longer project folder name and adds a hash it doesn't document.
MAX_PROJECT_DIR_NAME = 200


def withoutSessionVariables(env):
    return {name: value for name, value in env.items() if not isSessionVariable(name)}


def isSessionVariable(name):
    return name.startswith("CLAUDE") and name not in KEPT_CLAUDE_VARIABLES


# Where Claude Code keeps worktree's conversations, or None when the name is too long to know.
# https://code.claude.com/docs/en/sessions#where-transcripts-are-stored
def claudeProjectDir(worktree, env):
    name = re.sub(r"[^A-Za-z0-9]", "-", worktree)
    if len(name) > MAX_PROJECT_DIR_NAME:
        return None
    config = env.get("CLAUDE_CONFIG_DIR") or os.path.join(Path.home(), ".claude")
    return os.path.join(config, "projects", name)


# Whether Claude has run in the worktree before, so `claude --continue` has something to reopen.
def hasConversation(worktree, env):
    folder = claudeProjectDir(worktree, env)
    if not folder:
        return False
    try:
        return any(name.endswith(".jsonl") for name in os.listdir(folder))
    except OSError:
        return False


def claudeArguments(worktree, env):
    if hasConversation(worktree, env):
        return ["claude", "--continue"]
    prompt = env.get("PLAN_PROMPT")
    if not prompt:
        raise RuntimeError("claude-session needs PLAN_PROMPT, which plan start sets on a herdr claude tab")
    return ["claude", prompt]


# Replaces this process with Claude, started in the current folder, the tab's worktree.
def runClaude():
    env = dict(os.environ)
    arguments = claudeArguments(os.getcwd(), env)
    os.execvpe(arguments[0], arguments, withoutSessionVariables(env))
