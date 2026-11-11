# lib/server.py: the routes through Flask's test client, with no running server. Flask is in skuld's
# uv venv only, so under a bare python3 these tests skip: run `uv run python -m unittest discover -s
# lib -t .` to include them.
import errno
import importlib.util
import json
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

    def test_index_with_a_malformed_ticket_shows_the_store_error(self):
        self.write("SK-0001-a.md", ticket("in-progress", "Serve pages").replace("ordinal: 1", "ordinal: soon"))
        response = self.client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("The ticket store can't be read", response.get_data(as_text=True))

    def test_index_with_an_unreadable_ticket_shows_the_store_error(self):
        with mock.patch.object(server.fileissues_mod.FileIssues, "list", side_effect=PermissionError("denied")):
            response = self.client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("denied", response.get_data(as_text=True))

    def test_answers_loopback_host_names(self):
        for host in ("127.0.0.1:8765", "localhost:8765"):
            self.assertEqual(self.client().get("/health", headers={"Host": host}).status_code, 200)

    def test_refuses_any_other_host_name(self):
        response = self.client().get("/", headers={"Host": "rebind.example:8765"})
        self.assertEqual(response.status_code, 400)

    def test_index_with_nothing_in_progress_says_so(self):
        self.assertIn("No store ticket is in progress", self.client().get("/").get_data(as_text=True))


# What the round pages ask about an issue, answered from a temp dir standing in for its worktree.
class FakeIssues:
    def __init__(self, worktree, known=("SK-0001", "CIAM-5"), pr=None):
        self.root, self.known, self.pr, self.calls = worktree, known, pr, []

    def isKnown(self, key):
        self.calls.append(("isKnown", key))
        return key in self.known

    def issueUrl(self, key):
        return f"/issues/{key}" if key.startswith("SK-") else f"https://jira.example/browse/{key}"

    def worktree(self, key):
        self.calls.append(("worktree", key))
        return self.root

    def prUrl(self, worktree):
        return self.pr


REVIEW = {
    "findings": [{"file": "lib/server.py", "line": 12, "summary": "A <b>bold</b> claim", "disposition": "fixed"}],
    "dispositions": [{"path": "lib/server.py", "blob": "0123456789abcdef", "passes": ["bugs", "clean-code"], "verdict": "OK"}],
}


@unittest.skipUnless(HAS_FLASK, "Flask is in skuld's uv venv; run under `uv run`")
class RoundPageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = os.path.join(self.dir.name, "store")
        self.worktree = os.path.join(self.dir.name, "worktree")
        os.makedirs(self.store)
        self.issues = FakeIssues(self.worktree)

    def round(self, number, key="SK-0001", report="Round report", review=REVIEW):
        path = os.path.join(self.worktree, ".tmp", key, "rounds", str(number))
        os.makedirs(path)
        if report is not None:
            with open(os.path.join(path, "report.md"), "w", encoding="utf8") as f:
                f.write(report)
        if review is not None:
            with open(os.path.join(path, "review.json"), "w", encoding="utf8") as f:
                json.dump(review, f)

    def get(self, path):
        app = server.createApp({"issueStore": self.store}, issues=self.issues)
        return app.test_client().get(path)

    def page(self, path):
        response = self.get(path)
        self.assertEqual(response.status_code, 200, path)
        return response.get_data(as_text=True)

    def assertRedirects(self, path, to):
        response = self.get(path)
        self.assertEqual(response.status_code, 302, path)
        self.assertEqual(response.headers["Location"], to)

    def test_a_rounds_report_shows_its_report(self):
        self.round(1, report="What changed <and why>")
        page = self.page("/issues/SK-0001/rounds/1/report")
        self.assertIn("SK-0001, round 1: report", page)
        self.assertIn("What changed &lt;and why&gt;", page)

    def test_a_rounds_review_shows_its_findings_and_dispositions(self):
        self.round(1)
        page = self.page("/issues/SK-0001/rounds/1/review")
        self.assertIn("A &lt;b&gt;bold&lt;/b&gt; claim", page)
        self.assertIn("fixed", page)
        self.assertIn("0123456789ab<", page)
        self.assertIn("bugs, clean-code", page)

    def test_each_round_shows_its_own_data(self):
        self.round(0, report="the author's change")
        self.round(1, report="the first review")
        self.assertIn("the author&#39;s change", self.page("/issues/SK-0001/rounds/0/report"))
        self.assertIn("the first review", self.page("/issues/SK-0001/rounds/1/report"))

    def test_round_0_alone_is_the_latest(self):
        self.round(0)
        self.assertRedirects("/issues/SK-0001/report", "/issues/SK-0001/rounds/0/report")

    def test_a_round_with_a_leading_zero_is_404(self):
        self.round(0)
        self.assertEqual(self.get("/issues/SK-0001/rounds/00/report").status_code, 404)

    def test_the_issues_pages_redirect_to_the_latest_rounds(self):
        self.round(1)
        self.round(2)
        self.assertRedirects("/issues/SK-0001/report", "/issues/SK-0001/rounds/2/report")
        self.assertRedirects("/issues/SK-0001/review", "/issues/SK-0001/rounds/2/review")

    def test_a_missing_round_redirects_to_the_latest_rounds_page(self):
        self.round(1)
        self.round(3)
        self.assertRedirects("/issues/SK-0001/rounds/99/report", "/issues/SK-0001/rounds/3/report")
        self.assertRedirects("/issues/SK-0001/rounds/2/review", "/issues/SK-0001/rounds/3/review")

    def test_an_issue_with_no_rounds_redirects_to_the_issues_page(self):
        self.assertRedirects("/issues/SK-0001/report", "/issues/SK-0001")
        self.assertRedirects("/issues/CIAM-5/rounds/1/review", "https://jira.example/browse/CIAM-5")

    def test_an_issue_with_no_worktree_redirects_to_the_issues_page(self):
        self.issues.root = None
        self.assertRedirects("/issues/SK-0001/report", "/issues/SK-0001")

    def test_an_unpadded_store_key_redirects_to_its_canonical_key(self):
        self.assertRedirects("/issues/SK-1/rounds/2/report", "/issues/SK-0001/rounds/2/report")

    def test_an_unknown_key_gets_the_404_page_linking_the_index(self):
        with open(os.path.join(self.store, "SK-0002-a.md"), "w", encoding="utf8") as f:
            f.write(ticket("in-progress", "Something in progress"))
        response = self.get("/issues/CIAM-9/report")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 404)
        self.assertIn("/issues/CIAM-9/report", page)
        self.assertIn("CIAM-9 isn&#39;t an issue this Mac knows", page)
        self.assertIn('href="/issues/SK-0002/report"', page)
        self.assertIn('href="/"', page)

    def test_any_other_address_gets_the_404_page(self):
        response = self.get("/issues/SK-0001/rounds/1/diff")
        self.assertEqual(response.status_code, 404)
        self.assertIn("No page is served at this address", response.get_data(as_text=True))

    def test_a_traversal_in_the_key_or_round_is_404_and_reads_nothing(self):
        self.round(1)
        for path in (
            "/issues/../report",
            "/issues/%2E%2E/report",
            "/issues/SK-0001/rounds/../report",
            "/issues/SK-0001/rounds/%2E%2E/review",
            "/issues/SK-0001/rounds/..%2F..%2Fetc/report",
            "/issues/..%2F..%2Fetc/rounds/1/report",
        ):
            with self.subTest(path=path):
                self.issues.calls.clear()
                self.assertEqual(self.get(path).status_code, 404)
                self.assertEqual(self.issues.calls, [])

    def test_an_empty_round_names_what_is_missing(self):
        self.round(1, report=None, review=None)
        report = self.page("/issues/SK-0001/rounds/1/report")
        review = self.page("/issues/SK-0001/rounds/1/review")
        self.assertIn("report.md is missing from this round.", report)
        self.assertIn("review.json is missing from this round.", review)
        self.assertIn("No findings in this round.", review)

    def test_a_malformed_review_names_the_problem(self):
        self.round(1, review={"findings": "none", "dispositions": [{"path": "a", "blob": 7, "verdict": ["one", "two"]}]})
        page = self.page("/issues/SK-0001/rounds/1/review")
        self.assertIn("isn&#39;t a list", page)
        self.assertIn("one, two", page)

    def test_both_headers_link_the_issue_and_the_pr(self):
        self.round(1)
        self.issues.pr = "https://github.com/o/r/pull/9"
        for page in ("report", "review"):
            with self.subTest(page=page):
                shown = self.page(f"/issues/SK-0001/rounds/1/{page}")
                self.assertIn('href="/issues/SK-0001"', shown)
                self.assertIn('href="https://github.com/o/r/pull/9"', shown)

    def test_both_headers_say_when_there_is_no_pr(self):
        self.round(1, key="CIAM-5")
        for page in ("report", "review"):
            with self.subTest(page=page):
                shown = self.page(f"/issues/CIAM-5/rounds/1/{page}")
                self.assertIn('href="https://jira.example/browse/CIAM-5"', shown)
                self.assertIn("No pull request yet", shown)

    def test_the_header_links_the_other_page_and_the_other_rounds(self):
        self.round(1)
        self.round(2)
        page = self.page("/issues/SK-0001/rounds/2/report")
        self.assertIn('href="/issues/SK-0001/rounds/2/review"', page)
        self.assertIn('href="/issues/SK-0001/rounds/1/report"', page)

    def test_a_store_that_cant_be_read_is_a_404_page_not_a_500(self):
        def broken(key):
            raise ValueError("ordinal: soon isn't a number")

        self.issues.isKnown = broken
        response = self.get("/issues/SK-0001/report")
        self.assertEqual(response.status_code, 404)
        self.assertIn("ordinal: soon", response.get_data(as_text=True))


@unittest.skipUnless(HAS_FLASK, "Flask is in skuld's uv venv; run under `uv run`")
class IssueLocatorTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cfg = {"issueStore": self.dir.name, "jira": {"CIAM": {}}, "baseUrl": "https://jira.example"}
        self.issues = server.IssueLocator(self.cfg)

    def test_a_store_ticket_is_known_when_its_file_exists(self):
        with open(os.path.join(self.dir.name, "SK-0001-a.md"), "w", encoding="utf8") as f:
            f.write(ticket("open", "A"))
        self.assertTrue(self.issues.isKnown("SK-0001"))
        self.assertFalse(self.issues.isKnown("SK-0002"))

    def test_a_jira_key_is_known_when_the_config_names_its_project(self):
        self.assertTrue(self.issues.isKnown("CIAM-5"))
        self.assertFalse(self.issues.isKnown("OTHER-5"))

    def test_issue_urls(self):
        self.assertEqual(self.issues.issueUrl("SK-0001"), "/issues/SK-0001")
        self.assertEqual(self.issues.issueUrl("CIAM-5"), "https://jira.example/browse/CIAM-5")

    def test_the_worktree_and_pr_come_from_start_and_reviews(self):
        with mock.patch.object(server.start_mod, "issueWorktree", return_value="/w") as worktree, \
                mock.patch.object(server.reviews_mod, "prUrl", return_value="https://pr") as prUrl:
            self.assertEqual(self.issues.worktree("CIAM-5"), "/w")
            self.assertEqual(self.issues.prUrl("/w"), "https://pr")
        worktree.assert_called_once_with(self.cfg, "CIAM-5")
        prUrl.assert_called_once_with("/w")


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
