# `plan claude-session` (lib/claude_session.py). The last tests run the line herdr types into the
# claude tab, in a real shell, with a fake `claude` on PATH.
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from lib import claude_session
from lib import herdr as herdr_mod
from lib import term as term_mod

FAKE_CLAUDE = f"""#!{sys.executable}
import json, os, sys
names = sorted(name for name in os.environ if name.startswith("CLAUDE") or name == "PLAN_KEY")
print(json.dumps({{"argv": sys.argv[1:], "env": names}}))
"""


class ClaudeSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="skuld-claude-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.worktree = self.folder("repo.worktrees", "SK-0001")
        self.config = self.folder("claude-config")

    def folder(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def addConversation(self):
        folder = claude_session.claudeProjectDir(self.worktree, {"CLAUDE_CONFIG_DIR": self.config})
        os.makedirs(folder)
        open(os.path.join(folder, "abc.jsonl"), "w").close()

    def test_only_claude_config_dir_survives_of_the_claude_variables(self):
        env = {"CLAUDE_PID": "1", "CLAUDECODE": "1", "CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin"}
        self.assertEqual(claude_session.withoutSessionVariables(env), {"CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin"})

    def test_the_project_folder_replaces_every_other_character_with_a_dash(self):
        self.assertEqual(
            claude_session.claudeProjectDir("/a/repo.worktrees/SK_1", {"CLAUDE_CONFIG_DIR": "/c"}),
            "/c/projects/-a-repo-worktrees-SK-1",
        )

    def test_a_worktree_with_too_long_a_name_has_no_conversation(self):
        self.assertFalse(claude_session.hasConversation("/" + "a" * 200, {"CLAUDE_CONFIG_DIR": self.config}))

    def runTab(self, prompt):
        bin = self.folder("bin")
        claude = os.path.join(bin, "claude")
        with open(claude, "w") as f:
            f.write(FAKE_CLAUDE)
        os.chmod(claude, 0o755)
        env = {
            "PATH": f"{bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": self.tmp,
            "PLAN_KEY": "SK-0001",
            "PLAN_PROMPT": prompt,
            "PLAN_COMMAND": term_mod.PLAN_COMMAND,
            "CLAUDE_CONFIG_DIR": self.config,
            "CLAUDE_PID": "1",
            "CLAUDE_CODE_CHILD_SESSION": "1",
        }
        result = subprocess.run(
            ["bash", "-c", herdr_mod.CLAUDE_COMMAND], cwd=self.worktree, env=env, capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)

    def test_a_new_worktrees_claude_gets_the_prompt_unchanged(self):
        marker = os.path.join(self.tmp, "ran")
        prompt = f"""Work on "SK-0001"; it's $(touch {marker}) `touch {marker}` \\ $HOME\nsecond line"""
        started = self.runTab(prompt)
        self.assertEqual(started["argv"], [prompt])
        self.assertFalse(os.path.exists(marker))

    def test_a_worktree_with_a_conversation_continues_it(self):
        self.addConversation()
        self.assertEqual(self.runTab("Work on SK-0001")["argv"], ["--continue"])

    def test_claude_starts_without_the_inherited_session_variables(self):
        self.assertEqual(self.runTab("Work on SK-0001")["env"], ["CLAUDE_CONFIG_DIR", "PLAN_KEY"])


if __name__ == "__main__":
    unittest.main()
