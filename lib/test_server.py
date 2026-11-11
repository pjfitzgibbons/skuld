# lib/server.py: the routes through Flask's test client, with no running server. Flask is in skuld's
# uv venv only, so under a bare python3 these tests skip: run `uv run python -m unittest discover -s
# lib -t .` to include them.
import importlib.util
import os
import socket
import tempfile
import unittest
from unittest import mock

HAS_FLASK = importlib.util.find_spec("flask") is not None
if HAS_FLASK:
    from lib import server, term
    from lib.filestore import FileStore
    from lib.issue import Comment


def ticket(status, title):
    return f"---\nstatus: {status}\ntype: task\nordinal: 1\n---\n\n# {title}\n\n## Log\n"


TICKET_PAGE = """---
status: in-progress
type: task
ordinal: 1
closed:
---

# Show the ticket

## Task

Render the page.

## Guardrails

- Read-only.

## Log

- 2026-11-10: Started on the branch.
"""


@unittest.skipUnless(HAS_FLASK, "Flask is in skuld's uv venv; run under `uv run`")
class MarkdownTests(unittest.TestCase):
    def test_renders_headings_paragraphs_and_inline_markup(self):
        html = server.bodyHtml("## Task\n\nAdd the route, so it\nreads `plan view` **well**.\n\n### Current state")
        self.assertIn("<h2>Task</h2>", html)
        self.assertIn("<p>Add the route, so it\nreads <code>plan view</code> <strong>well</strong>.</p>", html)
        self.assertIn("<h3>Current state</h3>", html)

    def test_renders_checkboxes_disabled(self):
        html = server.bodyHtml("- [x] L3 Reliability\n- [ ] L4 Performance\n- plain")
        self.assertIn('<input class="task-list-item-checkbox" checked="checked" disabled="disabled" type="checkbox"> L3', html)
        self.assertIn('<input class="task-list-item-checkbox" disabled="disabled" type="checkbox"> L4', html)
        self.assertIn("<li>plain</li>", html)

    def test_renders_tables_and_code_blocks(self):
        html = server.bodyHtml("| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nplan serve start\n```")
        self.assertIn("<td>1</td>", html)
        self.assertIn("<pre><code>plan serve start\n</code></pre>", html)

    def test_escapes_raw_html_and_refuses_script_links(self):
        html = server.bodyHtml("<img src=x onerror=alert(1)>\n\n[x](javascript:alert(1))")
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn('href="javascript:', html)

    def test_a_log_entry_renders_inline(self):
        entry = server.logEntry(Comment(author="plan", text="Ran `pr`.", date="2026-11-10"))
        self.assertEqual(entry, {"date": "2026-11-10", "text": "Ran <code>pr</code>."})


@unittest.skipUnless(HAS_FLASK, "Flask is in skuld's uv venv; run under `uv run`")
class RouteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = self.dir.name

    def tearDown(self):
        self.dir.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.store, name), "w", encoding="utf8") as f:
            f.write(text)

    def client(self, cfg=None):
        return server.createApp({"serverPort": 8765, **(cfg if cfg is not None else {"issueStore": self.store})}).test_client()

    def test_health_answers_as_skuld(self):
        response = self.client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"app": "skuld", "status": "ok"})

    INDEX = """# Next up

Open issues in the order to work them.

## [Plan start](milestones/plan-start.md)

- [SK-0002] `plan open` with no key prints `not "None"`
- [SK-0005] **in progress** · Start uses the current folder's repo [SK-0002]

[SK-0002]: SK-0002-plan-open-none.md
[SK-0005]: SK-0005-one-issue-two-repos.md
"""

    def test_index_renders_the_stores_index(self):
        self.write("INDEX.md", self.INDEX)
        page = self.client().get("/").get_data(as_text=True)
        self.assertIn("<h1>Next up</h1>", page)
        self.assertEqual(page.count("<h1>"), 1)
        self.assertIn("<code>plan open</code>", page)
        self.assertIn("<strong>in progress</strong>", page)

    def test_index_links_each_ticket_to_its_page(self):
        self.write("INDEX.md", self.INDEX)
        page = self.client().get("/").get_data(as_text=True)
        self.assertIn('<a href="/issues/SK-0002">SK-0002</a>', page)
        self.assertEqual(page.count('href="/issues/SK-0002"'), 2)
        self.assertIn('<a href="/issues/SK-0005">SK-0005</a>', page)

    def test_index_unlinks_store_files_without_a_page(self):
        self.write("INDEX.md", self.INDEX)
        page = self.client().get("/").get_data(as_text=True)
        self.assertIn("<h2>Plan start</h2>", page)
        self.assertNotIn("milestones/", page)

    def test_index_escapes_raw_html(self):
        self.write("INDEX.md", "- [SK-0001] <script>alert(1)</script>\n\n[SK-0001]: SK-0001-a.md\n")
        page = self.client().get("/").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_index_without_an_index_file_shows_the_store_error(self):
        response = self.client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("The ticket store's index can't be read", response.get_data(as_text=True))
        self.assertIn("INDEX.md", response.get_data(as_text=True))

    def test_index_without_a_store_says_so_rather_than_failing(self):
        response = self.client({}).get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no issue store set", response.get_data(as_text=True))

    def test_answers_loopback_host_names(self):
        for host in ("127.0.0.1:8765", "localhost:8765"):
            self.assertEqual(self.client().get("/health", headers={"Host": host}).status_code, 200)

    def test_refuses_any_other_host_name(self):
        response = self.client().get("/", headers={"Host": "rebind.example:8765"})
        self.assertEqual(response.status_code, 400)

    def test_issue_page_shows_fields_sections_and_log(self):
        self.write("SK-0034-a.md", TICKET_PAGE)
        response = self.client().get("/issues/SK-34")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("SK-0034", page)
        self.assertIn("Show the ticket", page)
        self.assertIn("<h2>Task</h2>", page)
        self.assertIn("<h2>Guardrails</h2>", page)
        self.assertIn("<th>status</th><td>in-progress</td>", page)
        self.assertIn("<th>closed</th><td>—</td>", page)
        self.assertIn("2026-11-10", page)
        self.assertIn("Started on the branch.", page)
        self.assertIn('<form method="post" action="/issues/SK-0034/open">', page)

    def test_issue_page_escapes_ticket_data(self):
        self.write("SK-0001-a.md", TICKET_PAGE.replace("Render the page.", "<img src=x onerror=alert(1)>"))
        page = self.client().get("/issues/SK-0001").get_data(as_text=True)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)

    def post_open(self, key, origin="http://localhost", cfg=None):
        headers = {"Origin": origin} if origin else {}
        return self.client(cfg).post(f"/issues/{key}/open", headers=headers)

    def test_open_sends_plan_windows_open_link_for_the_file_and_returns_to_the_page(self):
        self.write("SK-0034-a.md", TICKET_PAGE)
        with mock.patch.object(server.subprocess, "run") as run:
            response = self.post_open("SK-34")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/issues/SK-34")
        path = os.path.join(os.path.realpath(self.store), "SK-0034-a.md")
        run.assert_called_once_with(["/usr/local/bin/code", "--open-url", term.openFileLink(path)], check=True, capture_output=True, timeout=server.CODE_SECONDS)

    def test_open_runs_the_configured_code(self):
        self.write("SK-0034-a.md", TICKET_PAGE)
        with mock.patch.object(server.subprocess, "run") as run:
            self.post_open("SK-34", cfg={"issueStore": self.store, "codeCommand": "/opt/code"})
        self.assertEqual(run.call_args[0][0][0], "/opt/code")

    def test_open_refuses_a_post_from_another_origin_or_none(self):
        self.write("SK-0034-a.md", TICKET_PAGE)
        for origin in ("http://evil.example", None):
            with mock.patch.object(server.subprocess, "run") as run:
                self.assertEqual(self.post_open("SK-34", origin=origin).status_code, 403)
            run.assert_not_called()

    def test_open_says_when_code_fails(self):
        self.write("SK-0034-a.md", TICKET_PAGE)
        with mock.patch.object(server.subprocess, "run", side_effect=FileNotFoundError("no such file: code")):
            response = self.post_open("SK-34")
        self.assertEqual(response.status_code, 500)
        self.assertIn("VS Code didn&#39;t open it: no such file: code", response.get_data(as_text=True))

    def test_open_on_an_unknown_ticket_is_a_404(self):
        with mock.patch.object(server.subprocess, "run") as run:
            self.assertEqual(self.post_open("SK-0099").status_code, 404)
        run.assert_not_called()

    def test_an_unknown_ticket_gets_a_404_page_that_names_it(self):
        response = self.client().get("/issues/SK-0099")
        self.assertEqual(response.status_code, 404)
        self.assertIn("no issue file for SK-0099", response.get_data(as_text=True))

    def test_a_key_that_isnt_the_stores_gets_a_404_without_reading_the_store(self):
        for key in ("CIAM-1", "SK-*", "sk-1"):
            with self.subTest(key=key), mock.patch.object(FileStore, "issue") as read:
                response = self.client().get(f"/issues/{key}")
                self.assertEqual(response.status_code, 404)
                read.assert_not_called()

    def test_an_unreadable_ticket_shows_the_store_error(self):
        with mock.patch.object(FileStore, "issue", side_effect=PermissionError("denied")):
            response = self.client().get("/issues/SK-0001")
        self.assertEqual(response.status_code, 500)
        self.assertIn("denied", response.get_data(as_text=True))



@unittest.skipUnless(HAS_FLASK, "Flask is in skuld's uv venv; run under `uv run`")
class MainTests(unittest.TestCase):
    def run_main(self, port):
        with mock.patch.object(server.config_mod, "load", return_value={"serverPort": port}), \
                mock.patch.object(server, "make_server") as make_server, \
                mock.patch.object(server.logging, "basicConfig"):
            return server.main(), make_server

    def free_port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def test_listens_on_loopback_only(self):
        with server.listeningSocket(self.free_port()) as listener:
            self.assertEqual(listener.getsockname()[0], "127.0.0.1")

    def test_serves_the_configured_port_on_loopback(self):
        port = self.free_port()
        code, make_server = self.run_main(port)
        self.assertEqual(code, 0)
        host, bound_port = make_server.call_args.args[:2]
        self.assertEqual((host, bound_port), ("127.0.0.1", port))

    def test_a_port_in_use_is_refused_naming_the_port_and_the_process(self):
        port = self.free_port()
        with server.listeningSocket(port), \
                mock.patch.object(server.serve_mod, "portHolder", return_value="nginx (pid 42)"), \
                self.assertLogs("skuld.server", "ERROR") as logs:
            code, make_server = self.run_main(port)
        self.assertEqual(code, 1)
        make_server.assert_not_called()
        self.assertIn(f"port {port} is in use by nginx (pid 42)", logs.output[0])


if __name__ == "__main__":
    unittest.main()
