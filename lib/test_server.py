# lib/server.py: the routes through Flask's test client, with no running server. Flask is in skuld's
# uv venv only, so under a bare python3 these tests skip: run `uv run python -m unittest discover -s
# lib -t .` to include them.
import errno
import importlib.util
import os
import socket
import tempfile
import unittest
from unittest import mock

HAS_FLASK = importlib.util.find_spec("flask") is not None
if HAS_FLASK:
    from lib import server


def ticket(status, title):
    return f"---\nstatus: {status}\ntype: task\nordinal: 1\n---\n\n# {title}\n\n## Log\n"


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
        return server.createApp(cfg if cfg is not None else {"issueStore": self.store}).test_client()

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
