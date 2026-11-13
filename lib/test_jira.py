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
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer

from lib.issue import DONE, IN_PROGRESS, NEW, Blocker, Comment
from lib.jira import Jira, adfToText, readNetrc, textComment


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
                return 200, rawIssue("CIAM-80", "In Progress")
            raise AssertionError(f"unexpected {method} {path}")

        jira, requests, cleanup = fakeJira(handler)
        try:
            issue = jira.transition("CIAM-80", IN_PROGRESS)
            self.assertEqual(issue.status, "In Progress")
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
                return 200, rawIssue("CIAM-80", "Blocked")
            raise AssertionError(f"unexpected {method} {path}")

        jira, _requests, cleanup = fakeJira(handler)
        try:
            with self.assertRaisesRegex(RuntimeError, "kept CIAM-80 Blocked, not In Progress"):
                jira.transition("CIAM-80", IN_PROGRESS)
        finally:
            cleanup()

    def test_statusTransitionId_names_every_available_transition_when_target_isnt_one(self):
        def handler(method, path, body):
            self.assertEqual(path, "/rest/api/3/issue/CIAM-80/transitions")
            return 200, {
                "transitions": [
                    {"id": "1", "name": "Start", "to": {"name": "Doing"}},
                    {"id": "2", "name": "Block", "to": {"name": "Blocked"}},
                ]
            }

        jira, _requests, cleanup = fakeJira(handler)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                jira.statusTransitionId("CIAM-80", IN_PROGRESS)
            message = str(ctx.exception)
            self.assertRegex(message, 'no transition to "In Progress"')
            self.assertRegex(message, '"Start" → Doing')
            self.assertRegex(message, '"Block" → Blocked')
        finally:
            cleanup()

    def test_a_move_to_a_category_plan_never_makes_is_refused_before_any_call(self):
        jira = Jira({"baseUrl": "https://jira.example"})
        with mock.patch.object(jira, "get") as get, self.assertRaisesRegex(RuntimeError, f"can't be moved to {DONE}"):
            jira.statusTransitionId("CIAM-80", DONE)
        get.assert_not_called()

    def test_write_that_sets_no_status_reads_nothing_back(self):
        def handler(method, path, body):
            self.assertEqual(path, "/rest/api/3/issue/CIAM-80/comment")
            self.assertEqual(method, "POST")
            self.assertEqual(body, {"body": textComment("hi")})
            return 201, {}

        jira, requests, cleanup = fakeJira(handler)
        try:
            jira.addComment("CIAM-80", "hi")
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

    def test_search_follows_nextPageToken_until_last_page(self):
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
            rows = jira.search("project = CIAM")
            self.assertEqual([r["id"] for r in rows], ["CIAM-1", "CIAM-2"])
            self.assertEqual(rows[0]["category"], NEW)
            self.assertEqual(rows[0]["project"], "CIAM")
        finally:
            cleanup()

    def test_list_runs_the_projects_own_jql(self):
        jira = Jira({"baseUrl": "https://jira.example", "jql": {"CIAM": "project in (CIAM)", "OPS": "key = OPS-1"}})
        with mock.patch.object(jira, "search", return_value=[]) as search:
            jira.list("OPS")
        search.assert_called_once_with("key = OPS-1")

    def test_list_runs_next_up_for_a_project_with_no_jql(self):
        jira = Jira({"baseUrl": "https://jira.example", "jql": {"CIAM": None}})
        with mock.patch.object(jira, "search", return_value=[]) as search:
            jira.list("CIAM")
        search.assert_called_once_with(
            "project in (CIAM) AND assignee = currentUser() AND statusCategory != Done ORDER BY Rank"
        )

    def test_issue_reads_one_get_into_an_Issue(self):
        raw = rawIssue("CIAM-80", "In Progress", "indeterminate")
        raw["fields"].update(
            {
                "parent": {"key": "CIAM-1"},
                "description": textComment("First line\nSecond line"),
                "comment": {
                    "comments": [
                        {"author": {"displayName": "Ann"}, "body": textComment("Looks good"), "created": "2026-11-02T10:00:00.000+0000"}
                    ]
                },
            }
        )

        jira, requests, cleanup = fakeJira(lambda method, path, body: (200, raw))
        try:
            issue = jira.issue("CIAM-80")
            self.assertEqual(len(requests), 1)
        finally:
            cleanup()
        self.assertEqual((issue.key, issue.summary, issue.issueType), ("CIAM-80", "A Jira issue", "Story"))
        self.assertEqual((issue.status, issue.category), ("In Progress", IN_PROGRESS))
        self.assertEqual(issue.parent, "CIAM-1")
        self.assertEqual(issue.description, "First line\nSecond line")
        self.assertEqual(issue.comments, (Comment("Ann", "Looks good", "2026-11-02"),))
        self.assertEqual(issue.page, f"{jira.settings['baseUrl']}/browse/CIAM-80")
        self.assertIsNone(issue.file)

    def test_issue_blockers_are_only_unresolved_blocks_links_naming_inward_issue(self):
        jira = Jira({"baseUrl": "https://jira.example"})
        raw = rawIssue("CIAM-80", "To Do")
        raw["fields"]["issuelinks"] = [
            {"type": {"name": "Blocks"}, "inwardIssue": {"key": "CIAM-1", "fields": {"summary": "x", "status": {"name": "To Do", "statusCategory": {"key": "new"}}}}},
            {"type": {"name": "Blocks"}, "inwardIssue": {"key": "CIAM-2", "fields": {"summary": "y", "status": {"name": "Done", "statusCategory": {"key": "done"}}}}},
            {"type": {"name": "Blocks"}, "outwardIssue": {"key": "CIAM-3", "fields": {}}},
            {"type": {"name": "Relates"}, "inwardIssue": {"key": "CIAM-4", "fields": {"summary": "z", "status": {"name": "To Do", "statusCategory": {"key": "new"}}}}},
        ]
        self.assertEqual(jira.issueFrom(raw).blockers, (Blocker("CIAM-1", "x", "To Do"),))


# Jira's issue JSON, with just the fields Jira.issue() reads.
def rawIssue(key, status, category="new"):
    return {
        "key": key,
        "fields": {
            "summary": "A Jira issue",
            "status": {"name": status, "statusCategory": {"key": category}},
            "issuetype": {"name": "Story"},
        },
    }


def adfDoc(*content):
    return {"type": "doc", "version": 1, "content": list(content)}


def linkedText(text, href):
    return {"type": "text", "text": text, "marks": [{"type": "link", "attrs": {"href": href}}]}


class AdfToTextTests(unittest.TestCase):
    def test_headings_keep_their_level_as_markdown(self):
        doc = adfDoc(
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Outline"}]},
            {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "Sub"}]},
        )
        self.assertEqual(adfToText(doc), "## Outline\n### Sub")

    def test_task_items_are_separate_lines_with_their_state(self):
        item = lambda state, text: {
            "type": "taskItem",
            "attrs": {"state": state},
            "content": [{"type": "text", "text": text}],
        }
        doc = adfDoc({"type": "taskList", "content": [item("DONE", "one"), item("TODO", "two")]})
        self.assertEqual(adfToText(doc), "- [x] one\n- [ ] two")

    def test_a_link_keeps_its_destination(self):
        doc = adfDoc({"type": "paragraph", "content": [linkedText("abcdef1", "https://github.com/a/b/commit/abcdef1")]})
        self.assertEqual(adfToText(doc), "[abcdef1](https://github.com/a/b/commit/abcdef1)")

    def test_a_link_whose_text_is_its_destination_shows_it_once(self):
        doc = adfDoc({"type": "paragraph", "content": [linkedText("https://x.test/", "https://x.test/")]})
        self.assertEqual(adfToText(doc), "https://x.test/")

    def test_block_and_embed_cards_show_their_url(self):
        for cardType in ("blockCard", "embedCard"):
            doc = adfDoc({"type": cardType, "attrs": {"url": "https://github.com/a/b/commit/abcdef1"}})
            self.assertEqual(adfToText(doc), "https://github.com/a/b/commit/abcdef1")


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


if __name__ == "__main__":
    unittest.main()
