# The IssueStore port's composition: storeFor() picks the adapter for a key's project, and refuses
# a project this Mac's config doesn't name.
import unittest

from lib import config as config_mod
from lib import issuestore
from lib.filestore import FileStore
from lib.jira import Jira

JIRA_ON = {**config_mod.defaults(), "jira": {"CIAM": {"repos": []}}, "baseUrl": "https://jira.example", "issueStore": "/issues"}


class StoreForTests(unittest.TestCase):
    def test_a_store_key_gets_the_ticket_store_with_the_servers_pages(self):
        store = issuestore.storeFor(JIRA_ON, "SK-0001")
        self.assertIsInstance(store, FileStore)
        self.assertEqual(store._page("SK-0001"), f"{serverUrlFor(JIRA_ON)}issues/SK-0001")

    def test_a_configured_projects_key_gets_jira(self):
        self.assertIsInstance(issuestore.storeFor(JIRA_ON, "CIAM-1"), Jira)

    def test_a_key_in_another_project_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'OPS-1 is unknown on this Mac: .*has no "jira" entry naming OPS'):
            issuestore.storeFor(JIRA_ON, "OPS-1")

    def test_the_projects_are_the_jira_entrys_in_order_then_the_stores(self):
        cfg = {**JIRA_ON, "jira": {"OPS": {}, "CIAM": {}}}
        self.assertEqual(issuestore.projects(cfg), ["OPS", "CIAM", "SK"])

    def test_with_no_jira_entry_the_store_is_the_only_project(self):
        self.assertEqual(issuestore.projects({**config_mod.defaults(), "issueStore": "/issues"}), ["SK"])


class HasLocalPageTests(unittest.TestCase):
    def test_a_store_key_has_its_page_on_the_local_server(self):
        self.assertTrue(issuestore.hasLocalPage("SK-0001"))

    def test_a_jira_key_does_not(self):
        self.assertFalse(issuestore.hasLocalPage("CIAM-1"))

    def test_a_malformed_key_does_not(self):
        self.assertFalse(issuestore.hasLocalPage("SK-*"))


def serverUrlFor(cfg):
    return issuestore.serve_mod.serverUrl(issuestore.serve_mod.portFrom(cfg))


if __name__ == "__main__":
    unittest.main()
