# Ported from reference/vscode/test/redmine.test.js: Redmine's write-then-read-back drop guard is
# now Jira's transition() read-back guard (lib/jira.py), tested the same way -- a real local HTTP
# server standing in for Jira Cloud, with the exact request sequence asserted so a silent extra
# request (or a missing one) fails the test, not just the wrong behavior.
#
# Ported from lib/jira.test.js. Faithful, behavior-preserving port.
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from lib.jira import Jira, KEY_RE, projectOf, readNetrc


class _FakeJiraServer:
    """A local HTTP server standing in for cryoport.atlassian.net. handler(method, path, body)
    decides each response, returning (status, bodyOrNone). Every request is recorded as
    "METHOD /path" in .requests, in order, so a test can assert the exact sequence the client
    made (and nothing more)."""

    def __init__(self, handler):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw.decode("utf8")) if raw else None
                outer.requests.append(f"{self.command} {self.path}")
                status, resp_body = handler(self.command, self.path, body)
                payload = b"" if resp_body is None else json.dumps(resp_body).encode("utf8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            do_GET = _handle
            do_POST = _handle
            do_PUT = _handle

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def fakeJira(handler):
    """Returns (jira, requests, cleanup). cleanup() stops the server and removes the temp netrc dir."""
    server = _FakeJiraServer(handler)
    base_url = f"http://127.0.0.1:{server.port}"
    dir = tempfile.mkdtemp(prefix="jira-plan-netrc-")
    netrc_file = os.path.join(dir, ".netrc")
    with open(netrc_file, "w", encoding="utf8") as f:
        f.write(f"machine 127.0.0.1:{server.port}\nlogin peter\npassword tok\n")
    jira = Jira({"baseUrl": base_url, "netrcFile": netrc_file})

    def cleanup():
        server.close()
        shutil.rmtree(dir, ignore_errors=True)

    return jira, server.requests, cleanup


class JiraTests(unittest.TestCase):
    def test_transition_moves_issue_and_reads_it_back(self):
        def handler(method, path, body):
            if path == "/rest/api/3/issue/CIAM-80/transitions" and method == "GET":
                return 200, {"transitions": [{"id": "31", "name": "Start", "to": {"name": "In Progress"}}]}
            if path == "/rest/api/3/issue/CIAM-80/transitions" and method == "POST":
                return 204, None
            if path.startswith("/rest/api/3/issue/CIAM-80?") and method == "GET":
                return 200, {"fields": {"status": {"name": "In Progress"}}}
            raise AssertionError(f"unexpected {method} {path}")

        jira, requests, cleanup = fakeJira(handler)
        try:
            issue = jira.transition("CIAM-80", "In Progress")
            self.assertEqual(issue["fields"]["status"]["name"], "In Progress")
            self.assertEqual(
                requests,
                [
                    "GET /rest/api/3/issue/CIAM-80/transitions",
                    "POST /rest/api/3/issue/CIAM-80/transitions",
                    "GET /rest/api/3/issue/CIAM-80?fields=summary,status,issuetype,parent,issuelinks,description,comment",
                ],
            )
        finally:
            cleanup()

    def test_transition_refuses_when_jira_accepts_post_but_keeps_old_status(self):
        def handler(method, path, body):
            if path == "/rest/api/3/issue/CIAM-80/transitions" and method == "GET":
                return 200, {"transitions": [{"id": "31", "name": "Start", "to": {"name": "In Progress"}}]}
            if path == "/rest/api/3/issue/CIAM-80/transitions" and method == "POST":
                return 204, None  # accepted, but a workflow condition silently blocks it
            if path.startswith("/rest/api/3/issue/CIAM-80?") and method == "GET":
                return 200, {"fields": {"status": {"name": "Blocked"}}}
            raise AssertionError(f"unexpected {method} {path}")

        jira, _requests, cleanup = fakeJira(handler)
        try:
            with self.assertRaisesRegex(RuntimeError, "kept CIAM-80 Blocked, not In Progress"):
                jira.transition("CIAM-80", "In Progress")
        finally:
            cleanup()

    def test_statusTransitionId_names_every_available_transition_when_target_isnt_one(self):
        def handler(method, path, body):
            self.assertEqual(path, "/rest/api/3/issue/CIAM-80/transitions")
            return 200, {
                "transitions": [
                    {"id": "1", "name": "Start", "to": {"name": "In Progress"}},
                    {"id": "2", "name": "Block", "to": {"name": "Blocked"}},
                ]
            }

        jira, _requests, cleanup = fakeJira(handler)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                jira.statusTransitionId("CIAM-80", "Done")
            message = str(ctx.exception)
            self.assertRegex(message, 'no transition to "Done"')
            self.assertRegex(message, '"Start" → In Progress')
            self.assertRegex(message, '"Block" → Blocked')
        finally:
            cleanup()

    def test_write_that_sets_no_status_reads_nothing_back(self):
        def handler(method, path, body):
            self.assertEqual(path, "/rest/api/3/issue/CIAM-80/comment")
            self.assertEqual(method, "POST")
            return 201, {}

        jira, requests, cleanup = fakeJira(handler)
        try:
            jira.addComment("CIAM-80", jira.textComment("hi"))
            self.assertEqual(requests, ["POST /rest/api/3/issue/CIAM-80/comment"])
        finally:
            cleanup()

    def test_send_refuses_bad_credentials_with_specific_message(self):
        def handler(method, path, body):
            return 401, {}

        jira, _requests, cleanup = fakeJira(handler)
        try:
            with self.assertRaisesRegex(RuntimeError, r"refused the credentials in ~/.netrc"):
                jira.get("/rest/api/3/issue/CIAM-80")
        finally:
            cleanup()

    def test_send_names_reasons_a_write_was_refused_for(self):
        def handler(method, path, body):
            return 400, {"errorMessages": ["bad input"], "errors": {"summary": "is required"}}

        jira, _requests, cleanup = fakeJira(handler)
        try:
            with self.assertRaisesRegex(RuntimeError, "bad input; is required"):
                jira.send("POST", "/rest/api/3/issue", {})
        finally:
            cleanup()

    def test_list_follows_nextPageToken_until_last_page(self):
        page = {"n": 0}

        def handler(method, path, body):
            page["n"] += 1
            if page["n"] == 1:
                self.assertIsNone(body.get("nextPageToken"))
                return 200, {
                    "issues": [
                        {
                            "key": "CIAM-1",
                            "fields": {
                                "summary": "a",
                                "status": {"name": "To Do", "statusCategory": {"key": "new"}},
                                "issuetype": {"name": "Task"},
                            },
                        }
                    ],
                    "nextPageToken": "p2",
                }
            self.assertEqual(body.get("nextPageToken"), "p2")
            return 200, {
                "issues": [
                    {
                        "key": "CIAM-2",
                        "fields": {
                            "summary": "b",
                            "status": {"name": "Done", "statusCategory": {"key": "done"}},
                            "issuetype": {"name": "Bug"},
                        },
                    }
                ]
            }

        jira, _requests, cleanup = fakeJira(handler)
        try:
            rows = jira.list("project = CIAM")
            self.assertEqual([r["id"] for r in rows], ["CIAM-1", "CIAM-2"])
            self.assertEqual(rows[0]["statusCategory"], "new")
            self.assertEqual(rows[0]["project"], "CIAM")
        finally:
            cleanup()

    def test_blockers_keeps_only_unresolved_blocks_links_naming_inward_issue(self):
        jira = Jira()
        issue = {
            "fields": {
                "issuelinks": [
                    {"type": {"name": "Blocks"}, "inwardIssue": {"key": "CIAM-1", "fields": {"summary": "x", "status": {"statusCategory": {"key": "new"}}}}},
                    {"type": {"name": "Blocks"}, "inwardIssue": {"key": "CIAM-2", "fields": {"summary": "y", "status": {"statusCategory": {"key": "done"}}}}},
                    {"type": {"name": "Blocks"}, "outwardIssue": {"key": "CIAM-3", "fields": {}}},
                    {"type": {"name": "Relates"}, "inwardIssue": {"key": "CIAM-4", "fields": {"summary": "z", "status": {"statusCategory": {"key": "new"}}}}},
                ]
            }
        }
        self.assertEqual([b["key"] for b in jira.blockers(issue)], ["CIAM-1"])


class ReadNetrcTests(unittest.TestCase):
    def _write(self, content):
        dir = tempfile.mkdtemp(prefix="jira-plan-netrc-")
        self.addCleanup(shutil.rmtree, dir, True)
        file = os.path.join(dir, ".netrc")
        with open(file, "w", encoding="utf8") as f:
            f.write(content)
        return file

    def test_refuses_missing_machine_entry(self):
        file = self._write("machine example.com\nlogin a\npassword b\n")
        with self.assertRaisesRegex(RuntimeError, 'no "machine cryoport.atlassian.net" entry'):
            readNetrc("cryoport.atlassian.net", file)

    def test_skips_leading_comment_line(self):
        file = self._write("# personal tokens below\nmachine cryoport.atlassian.net\nlogin a@b.com\npassword tok\n")
        entry = readNetrc("cryoport.atlassian.net", file)
        self.assertEqual(entry, {"machine": "cryoport.atlassian.net", "login": "a@b.com", "password": "tok"})

    def test_handles_account_token_alongside_login_password(self):
        file = self._write("machine cryoport.atlassian.net\nlogin a@b.com\naccount work\npassword tok\n")
        entry = readNetrc("cryoport.atlassian.net", file)
        self.assertEqual(entry["login"], "a@b.com")
        self.assertEqual(entry["password"], "tok")

    def test_skips_macdef_block_without_misreading_its_body_as_tokens(self):
        # The macdef body below contains the words "machine"/"login"/"password" precisely to prove
        # a naive whitespace tokenizer (which would misparse them as real entry tokens) is not
        # what runs.
        file = self._write(
            "machine other.example.com\nmacdef init\nmachine bogus login x password y\n\n"
            "machine cryoport.atlassian.net\nlogin a@b.com\npassword tok\n"
        )
        entry = readNetrc("cryoport.atlassian.net", file)
        self.assertEqual(entry, {"machine": "cryoport.atlassian.net", "login": "a@b.com", "password": "tok"})

    def test_skips_commented_out_old_entry_before_real_one(self):
        # A whole machine entry commented out line-by-line (as leaving a retired token in place,
        # rather than deleting it, would look) must not be read as live -- each line starts with
        # "#", so the per-line comment-stripping in readNetrc drops all three before the real
        # entry is ever reached.
        file = self._write(
            "# machine cryoport.atlassian.net\n# login old@b.com\n# password oldtok\n"
            "machine cryoport.atlassian.net\nlogin a@b.com\npassword tok\n"
        )
        entry = readNetrc("cryoport.atlassian.net", file)
        self.assertEqual(entry, {"machine": "cryoport.atlassian.net", "login": "a@b.com", "password": "tok"})

    def test_handles_account_token_appearing_before_login(self):
        file = self._write("machine cryoport.atlassian.net\naccount work\nlogin a@b.com\npassword tok\n")
        entry = readNetrc("cryoport.atlassian.net", file)
        self.assertEqual(entry["login"], "a@b.com")
        self.assertEqual(entry["password"], "tok")


class KeyRETests(unittest.TestCase):
    def test_key_re_and_project_of(self):
        self.assertTrue(KEY_RE.match("CIAM-123"))
        self.assertFalse(KEY_RE.match("123"))
        self.assertFalse(KEY_RE.match("release-2026-10"))
        self.assertEqual(projectOf("CIAM-123"), "CIAM")

    def test_key_re_requires_two_or_more_char_project_code(self):
        # A single letter before the dash is the shape of a stray token, not a real Jira project
        # code; the unified pattern (jira.py, pr.sh, ghbody.sh) rejects it everywhere the same way.
        self.assertFalse(KEY_RE.match("C-1"))
        self.assertTrue(KEY_RE.match("C1-1"))


if __name__ == "__main__":
    unittest.main()
