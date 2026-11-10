# lib/waiting.py and bin/claude-waiting: the hook events that mark and clear a session's waiting
# marker, in a temp directory.
import json
import os
import subprocess
import tempfile
import unittest

from lib import waiting

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def event(name, sessionId="s1", **more):
    return {"hook_event_name": name, "session_id": sessionId, "cwd": "/p/a.worktrees/SK-0041", **more}


class MarkerChangeTests(unittest.TestCase):
    def test_a_permission_prompt_or_question_marks_the_session(self):
        for kind in ("permission_prompt", "elicitation_dialog", "idle_prompt", "agent_needs_input"):
            self.assertEqual(waiting.markerChange(event("Notification", notification_type=kind)), "mark", kind)

    def test_other_notifications_leave_it(self):
        self.assertIsNone(waiting.markerChange(event("Notification", notification_type="auth_success")))

    def test_moving_on_clears_it(self):
        for name in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"):
            self.assertEqual(waiting.markerChange(event(name)), "clear", name)

    def test_other_events_leave_it(self):
        self.assertIsNone(waiting.markerChange(event("SessionStart")))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "waiting")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_notification_marks_and_a_tool_call_clears(self):
        waiting.record(event("Notification", notification_type="permission_prompt"), self.dir)
        self.assertEqual(waiting.waitingSessions(self.dir), {"s1"})
        waiting.record(event("PreToolUse"), self.dir)
        self.assertEqual(waiting.waitingSessions(self.dir), set())

    def test_marking_or_clearing_changes_the_stamp_the_engine_watches(self):
        before = waiting.changeStamp(self.dir)
        waiting.record(event("Notification", notification_type="permission_prompt"), self.dir)
        marked = waiting.changeStamp(self.dir)
        self.assertNotEqual(before, marked)
        os.utime(self.dir, ns=(0, 0))
        waiting.record(event("Stop"), self.dir)
        self.assertNotEqual(waiting.changeStamp(self.dir), 0)

    def test_a_missing_directory_has_no_waiting_session(self):
        self.assertEqual(waiting.waitingSessions(self.dir), set())
        waiting.record(event("Stop"), self.dir)
        self.assertFalse(os.path.exists(self.dir))

    def test_a_session_id_cannot_name_a_path_outside_the_directory(self):
        waiting.record(event("Notification", "../escape", notification_type="permission_prompt"), self.dir)
        self.assertEqual(waiting.waitingSessions(self.dir), {"escape"})


class HookScriptTests(unittest.TestCase):
    def run_hook(self, stdin, home):
        return subprocess.run(
            [os.path.join(ROOT, "bin", "claude-waiting")],
            input=stdin, capture_output=True, text=True, env={**os.environ, "HOME": home},
        )

    def test_the_hook_marks_from_stdin_prints_nothing_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as home:
            done = self.run_hook(json.dumps(event("Notification", notification_type="permission_prompt")), home)
            self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
            self.assertEqual(waiting.waitingSessions(os.path.join(home, ".cache/plan/waiting")), {"s1"})

    def test_bad_input_never_fails_the_hook(self):
        with tempfile.TemporaryDirectory() as home:
            done = self.run_hook("not json", home)
            self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
