# lib/claude_hooks.py: registering bin/claude-waiting in Claude Code's settings, on made-up
# settings, keeping every other hook.
import json
import os
import tempfile
import unittest

from lib import claude_hooks

COMMAND = "/usr/bin/python3 /p/skuld/bin/claude-waiting"
OTHER = {"hooks": [{"type": "command", "command": "/x/cc-status"}]}


class HookSettingsTests(unittest.TestCase):
    def test_install_adds_one_entry_per_event_and_keeps_the_others(self):
        settings = claude_hooks.withOurs({"model": "m", "hooks": {"PreToolUse": [OTHER]}}, COMMAND)
        self.assertEqual(settings["model"], "m")
        self.assertEqual(settings["hooks"]["PreToolUse"][0], OTHER)
        self.assertEqual(set(settings["hooks"]), {"Notification", "UserPromptSubmit", "PreToolUse",
                                                  "PostToolUse", "Stop", "SessionEnd"})
        self.assertEqual(settings["hooks"]["Notification"][0]["matcher"],
                         "permission_prompt|idle_prompt|elicitation_dialog|agent_needs_input")

    def test_installing_twice_adds_nothing(self):
        once = claude_hooks.withOurs({}, COMMAND)
        self.assertEqual(claude_hooks.withOurs(once, COMMAND), once)

    def test_uninstall_leaves_only_the_others(self):
        installed = claude_hooks.withOurs({"hooks": {"PreToolUse": [OTHER]}}, COMMAND)
        self.assertEqual(claude_hooks.withoutOurs(installed), {"hooks": {"PreToolUse": [OTHER]}})

    def test_a_command_sharing_our_entry_is_kept_with_its_matcher(self):
        ours = {"type": "command", "command": COMMAND}
        shared = {"matcher": "Bash", "hooks": [OTHER["hooks"][0], ours]}
        cleaned = claude_hooks.withoutOurs({"hooks": {"PreToolUse": [shared]}})
        self.assertEqual(cleaned, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": OTHER["hooks"]}]}})

    def test_main_rewrites_the_file_and_starts_one_when_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "settings.json")
            claude_hooks.main("install", path, COMMAND)
            with open(path) as f:
                self.assertIn("Stop", json.load(f)["hooks"])
            claude_hooks.main("uninstall", path)
            with open(path) as f:
                self.assertEqual(json.load(f), {"hooks": {}})
            self.assertEqual(os.listdir(directory), ["settings.json"])


if __name__ == "__main__":
    unittest.main()
