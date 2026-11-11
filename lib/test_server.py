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
    from lib import server
    from lib.filestore import FileStore


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
class BodySectionTests(unittest.TestCase):
    def test_splits_at_level_two_headings(self):
        sections = server.bodySections("Intro.\n\n## Task\n\nDo it.\n\n## Empty")
        self.assertEqual(
            sections,
            [
                {"heading": None, "blocks": [{"kind": "paragraph", "text": "Intro."}]},
                {"heading": "Task", "blocks": [{"kind": "paragraph", "text": "Do it."}]},
                {"heading": "Empty", "blocks": []},
            ],
        )

    def test_a_body_that_starts_with_a_heading_has_no_untitled_section(self):
        self.assertEqual([s["heading"] for s in server.bodySections("## Task\nDo it.")], ["Task"])

    def test_joins_a_wrapped_paragraph_and_reads_subheadings(self):
        blocks = server.textBlocks(["Add the route, so it", "reads well.", "", "### Current state", "", "A tab."])
        self.assertEqual(
            blocks,
            [
                {"kind": "paragraph", "text": "Add the route, so it reads well."},
                {"kind": "subheading", "text": "Current state"},
                {"kind": "paragraph", "text": "A tab."},
            ],
        )

    def test_reads_list_items_their_continuations_and_checkboxes(self):
        blocks = server.textBlocks(["- In scope: the route,", "  and its tests.", "- [x] L3 Reliability", "- [ ] L4 Performance"])
        self.assertEqual(
            blocks,
            [
                {
                    "kind": "list",
                    "items": [
                        {"checked": None, "text": "In scope: the route, and its tests."},
                        {"checked": True, "text": "L3 Reliability"},
                        {"checked": False, "text": "L4 Performance"},
                    ],
                }
            ],
        )


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

    def test_index_lists_the_tickets_in_progress(self):
        self.write("SK-0001-a.md", ticket("in-progress", "Serve pages"))
        self.write("SK-0002-b.md", ticket("open", "Not started"))
        self.write("SK-0003-c.md", ticket("done", "Finished"))
        page = self.client().get("/").get_data(as_text=True)
        self.assertIn("SK-0001", page)
        self.assertIn("Serve pages", page)
        self.assertNotIn("SK-0002", page)
        self.assertNotIn("SK-0003", page)

    def test_index_escapes_ticket_data(self):
        self.write("SK-0001-a.md", ticket("in-progress", "<script>alert(1)</script>"))
        page = self.client().get("/").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_index_without_a_store_says_so_rather_than_failing(self):
        response = self.client({}).get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no issue store set", response.get_data(as_text=True))

    def test_index_with_a_malformed_ticket_shows_the_store_error(self):
        self.write("SK-0001-a.md", ticket("in-progress", "Serve pages").replace("ordinal: 1", "ordinal: soon"))
        response = self.client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("The ticket store can't be read", response.get_data(as_text=True))

    def test_index_with_an_unreadable_ticket_shows_the_store_error(self):
        with mock.patch.object(FileStore, "list", side_effect=PermissionError("denied")):
            response = self.client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("denied", response.get_data(as_text=True))

    def test_answers_loopback_host_names(self):
        for host in ("127.0.0.1:8765", "localhost:8765"):
            self.assertEqual(self.client().get("/health", headers={"Host": host}).status_code, 200)

    def test_refuses_any_other_host_name(self):
        response = self.client().get("/", headers={"Host": "rebind.example:8765"})
        self.assertEqual(response.status_code, 400)

    def test_index_links_each_ticket_to_its_page(self):
        self.write("SK-0001-a.md", ticket("in-progress", "Serve pages"))
        self.assertIn('href="/issues/SK-0001"', self.client().get("/").get_data(as_text=True))

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

    def test_issue_page_escapes_ticket_data(self):
        self.write("SK-0001-a.md", TICKET_PAGE.replace("Render the page.", "<img src=x onerror=alert(1)>"))
        page = self.client().get("/issues/SK-0001").get_data(as_text=True)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)

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

    def test_index_with_nothing_in_progress_says_so(self):
        self.assertIn("No store ticket is in progress", self.client().get("/").get_data(as_text=True))


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
