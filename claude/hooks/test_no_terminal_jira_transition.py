#!/usr/bin/env python3
"""Tests for no_terminal_jira_transition.py: Claude never touches a Jira transitions endpoint or
transition-moving CLI verb from a shell command.

Run:  python3 .claude/hooks/test_no_terminal_jira_transition.py
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import no_terminal_jira_transition as hook  # noqa: E402

HOOK = Path(hook.__file__)

ALLOW = [
    ("read the issue", "curl -sS --netrc https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123"),
    ("jira-get", "jira-get /rest/api/3/issue/CIAM-123"),
    ("jira issue view", "jira issue view CIAM-123"),
    ("jira issue list", "jira issue list -p CIAM"),
    ("unrelated command", "ls -la && echo transitions"),
    ("search jql", "curl --netrc 'https://cryoport.atlassian.net/rest/api/3/search/jql?jql=project=CIAM'"),
    # Read-only GETs of a transitions endpoint are allowed -- bare curl (no body/method flag)
    # defaults to GET, and an explicit -X GET is the same.
    ("bare curl GET the transitions list", "curl -sS --netrc https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions"),
    ("explicit -X GET the transitions list", "curl -sS --netrc -X GET https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions"),
    ("chained after && -- both GET", "cd /tmp && curl --netrc https://cryoport.atlassian.net/rest/api/3/issue/CIAM-9/transitions"),
    ("jira-rest.sh GET transitions", "jira-rest.sh GET /rest/api/3/issue/CIAM-123/transitions"),
    ("http GET transitions", "http GET https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions"),
    ("requests.get transitions", 'requests.get("https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions")'),
    # M5: a key with no "#<word>" beside it, or a "#<word>" with no key, is an ordinary commit/PR.
    ("commit with a key, no hashtag", 'git commit -m "CIAM-9 fixes the thing, no hashtags"'),
    ("commit with a hashtag, no key", 'git commit -m "fixes the thing #3"'),
    ("gh pr create title with a key, no hashtag", 'gh pr create --title "CIAM-9 adds the thing" --body-file b.md'),
    ("gh pr edit body-file flag isn't --body", 'gh pr edit --body-file b.md'),
    ("git commit -F a file (checked by pr.sh itself, not this hook)", 'git commit -q -F .tmp/CIAM-9/commit.txt'),
]

BLOCK = [
    ("POST a transition", 'curl -sS --netrc -X POST -d \'{"transition":{"id":"31"}}\' '
                           "https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions"),
    ("-d body with no explicit method defaults to POST", 'curl -sS --netrc -d \'{"transition":{"id":"31"}}\' '
                           "https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions"),
    ("--data body blocked", 'curl -sS --netrc --data \'{"transition":{"id":"31"}}\' '
                           "https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions"),
    ("-X POST blocked", 'curl -sS --netrc -X POST https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions'),
    ("mixed GET && POST -- the POST segment still blocks",
     "curl -X GET https://cryoport.atlassian.net/rest/api/3/issue/CIAM-9 && "
     "curl -d '{}' https://cryoport.atlassian.net/rest/api/3/issue/CIAM-9/transitions"),
    ("jira-rest.sh transitions", "jira-rest.sh POST /rest/api/3/issue/CIAM-123/transitions payload.json"),
    ("jira issue move", "jira issue move CIAM-123 Done"),
    ("jira issue transition verb", "jira issue transition CIAM-123 'Closed'"),
    ("http POST transitions", "http POST https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions"),
    ("wget --post-data transitions", "wget --post-data='{}' https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions"),
    ("requests.post transitions", 'requests.post("https://cryoport.atlassian.net/rest/api/3/issue/CIAM-123/transitions", data={})'),
    ("node fetch method POST transitions", 'fetch(url, {method: "POST"}) /* .../issue/CIAM-9/transitions */'),
    ("opaque script, unknown method -- fails closed", "./run-script.sh https://cryoport.atlassian.net/rest/api/3/issue/CIAM-9/transitions"),
    # M5: smart-commit tokens written inline into a commit message, PR title or PR body.
    ("git commit -m smart commit", 'git commit -m "CIAM-9 #close fixes the thing"'),
    ("git commit --message smart commit", 'git commit --message "CIAM-9 #done fixes it"'),
    ("gh pr create --title smart commit", 'gh pr create --title "CIAM-9 #resolve done" --body-file b.md'),
    ("gh pr create --body smart commit", 'gh pr create --title "x" --body "CIAM-9 #comment all set"'),
    ("gh pr edit --body smart commit", 'gh pr edit --body "CIAM-9 #time 2h logged"'),
    ("hashtag at line start", 'git commit -m "CIAM-9\n#close"'),
]


class RuleTests(unittest.TestCase):
    def test_allows(self):
        for label, command in ALLOW:
            with self.subTest(label):
                self.assertFalse(hook.should_block(command), label)

    def test_blocks(self):
        for label, command in BLOCK:
            with self.subTest(label):
                self.assertTrue(hook.should_block(command), label)

    def test_block_message_names_the_ticket(self):
        reason = hook.violation("curl --netrc -X POST https://cryoport.atlassian.net/rest/api/3/issue/CIAM-42/transitions")
        self.assertIn("CIAM-42", reason)

    def test_block_message_without_a_ticket_is_still_blocked(self):
        reason = hook.violation("jira issue move ABC-1 Done")
        self.assertIsNotNone(reason)

    def test_get_the_transitions_list_is_allowed(self):
        self.assertIsNone(hook.violation("curl --netrc https://cryoport.atlassian.net/rest/api/3/issue/CIAM-42/transitions"))

    def test_explicit_get_transitions_message_is_none(self):
        self.assertIsNone(hook.violation("curl -X GET --netrc https://cryoport.atlassian.net/rest/api/3/issue/CIAM-42/transitions"))

    def test_smart_commit_block_message_names_the_ticket(self):
        reason = hook.violation('git commit -m "CIAM-42 #close fixes it"')
        self.assertIn("CIAM-42", reason)
        self.assertIn("smart commit", reason)

    def test_smart_commit_needs_both_a_token_and_a_key_on_the_same_command(self):
        # Neither half alone is a smart commit: a plain key, or a hashtag with no key, is routine.
        self.assertIsNone(hook.violation('git commit -m "CIAM-9 fixes the thing"'))
        self.assertIsNone(hook.violation('git commit -m "fixes issue #3"'))

    # N2: bypasses the reviewer listed -- combined short flags, gh's short -t/-b, and -F/--file
    # pointing at a file that exists and actually contains a smart commit.
    def test_combined_short_flag_am_is_caught(self):
        reason = hook.violation('git commit -am "CIAM-9 #close fixes the thing"')
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_gh_pr_create_short_title_and_body_flags_are_caught(self):
        self.assertIsNotNone(hook.violation('gh pr create -t "CIAM-9 #close" -b "done"'))
        self.assertIsNotNone(hook.violation('gh pr create -t "x" -b "CIAM-9 #comment all set"'))

    def test_gh_pr_edit_short_body_flag_is_caught(self):
        self.assertIsNotNone(hook.violation('gh pr edit -b "CIAM-9 #time 2h logged"'))

    def test_git_commit_dash_F_file_with_smart_commit_is_caught_when_file_exists(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "commit.txt").write_text("CIAM-9 #close fixes the thing\n")
            reason = hook.violation("git commit -q -F commit.txt", cwd=d)
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_git_commit_dash_F_file_missing_still_allows(self):
        # pr.sh's own file-existence check and smart-commit scan are the real guarantee for a file
        # this hook can't find (e.g. not yet written, or a path it can't resolve).
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(hook.violation("git commit -q -F .tmp/CIAM-9/commit.txt", cwd=d))

    def test_gh_pr_create_body_file_flag_with_smart_commit_is_caught_when_file_exists(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pr-body.md").write_text("See CIAM-9 #comment for details\n")
            reason = hook.violation('gh pr create --title "x" --body-file pr-body.md', cwd=d)
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_git_commit_dash_F_file_without_smart_commit_allows(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "commit.txt").write_text("CIAM-9 fixes the thing\n")
            self.assertIsNone(hook.violation("git commit -q -F commit.txt", cwd=d))

    # N11: bypasses the reviewer listed -- attached/"=" flag forms, a git global option before
    # "commit", and -F- reading the message from a heredoc instead of a file.
    def test_git_commit_dash_dash_file_equals_attached_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "msg.txt").write_text("CIAM-9 #close fixes the thing\n")
            reason = hook.violation("git commit --file=msg.txt", cwd=d)
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_git_commit_dash_F_attached_no_separator_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "msg.txt").write_text("CIAM-9 #close fixes the thing\n")
            reason = hook.violation("git commit -Fmsg.txt", cwd=d)
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_git_dash_C_global_option_before_commit_is_caught(self):
        reason = hook.violation('git -C /x commit -m "CIAM-9 #close sneaky"')
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_git_dash_c_config_global_option_before_commit_is_caught(self):
        reason = hook.violation('git -c user.name=bot commit -m "CIAM-9 #close sneaky"')
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_git_commit_dash_F_dash_heredoc_is_caught(self):
        command = 'git commit -F- <<EOF\nCIAM-9 #close sneaky\nEOF'
        reason = hook.violation(command)
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_git_commit_dash_F_dash_heredoc_without_smart_commit_allows(self):
        command = 'git commit -F- <<EOF\nCIAM-9 fixes the thing\nEOF'
        self.assertIsNone(hook.violation(command))

    def test_gh_pr_edit_body_file_equals_attached_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "msg.txt").write_text("CIAM-9 #close fixes the thing\n")
            reason = hook.violation("gh pr edit 3 --body-file=msg.txt", cwd=d)
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    # N8: gh api can write a PR's title/body directly, bypassing gh pr create/edit entirely.
    def test_gh_api_pulls_dash_f_title_smart_commit_is_caught(self):
        reason = hook.violation('gh api repos/o/r/pulls/5 -X PATCH -f title="CIAM-9 #close sneaky"')
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_gh_api_pulls_dash_F_body_smart_commit_is_caught(self):
        reason = hook.violation("gh api repos/o/r/pulls/5 -X PATCH -F body='CIAM-9 #comment all set'")
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_gh_api_pulls_field_at_file_smart_commit_is_caught_when_file_exists(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "body.txt").write_text("CIAM-9 #comment all set\n")
            reason = hook.violation("gh api repos/o/r/pulls/5 -X PATCH -F body=@body.txt", cwd=d)
        self.assertIsNotNone(reason)
        self.assertIn("CIAM-9", reason)

    def test_gh_api_pulls_without_smart_commit_allows(self):
        self.assertIsNone(hook.violation('gh api repos/o/r/pulls/5 -X PATCH -f title="CIAM-9 fixes it"'))

    def test_gh_api_without_pulls_endpoint_allows(self):
        # -f/-F on a gh api call that isn't touching pulls/<n> at all is out of scope for this check.
        self.assertIsNone(hook.violation('gh api repos/o/r/issues/5 -X PATCH -f title="CIAM-9 #close sneaky"'))


class ProcessTests(unittest.TestCase):
    @staticmethod
    def run_hook(command: str) -> subprocess.CompletedProcess:
        payload = json.dumps({"tool_name": "Bash", "cwd": ".", "tool_input": {"command": command}})
        return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True)

    def test_exit_0_allows(self):
        result = self.run_hook(ALLOW[0][1])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_exit_2_blocks_with_reason(self):
        result = self.run_hook(BLOCK[0][1])
        self.assertEqual(result.returncode, 2)
        self.assertIn("BLOCKED", result.stderr)

    def test_missing_command_allows(self):
        result = subprocess.run([sys.executable, str(HOOK)], input="{}", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
