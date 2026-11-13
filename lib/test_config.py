import io
import json
import os
import tempfile
import unittest
from unittest import mock

from . import config


def writeConfig(settings):
    folder = tempfile.mkdtemp(prefix="skuld-config-")
    path = os.path.join(folder, "config.json")
    with open(path, "w", encoding="utf8") as f:
        json.dump(settings, f)
    return path


class ConfigTest(unittest.TestCase):
    def test_the_config_file_is_skulds(self):
        self.assertEqual(config.CONFIG_FILE, os.path.expanduser("~/.config/skuld/config.json"))

    def test_a_missing_file_is_the_defaults(self):
        missing = os.path.join(tempfile.mkdtemp(prefix="skuld-config-"), "config.json")
        self.assertEqual(config.load(missing), config.defaults())

    def test_a_config_with_ghosttyApp_loads_with_a_warning(self):
        path = writeConfig({"ghosttyApp": "/Applications/Ghostty.app"})
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            cfg = config.load(path)
        self.assertIn("ghosttyApp, which is no longer read", stderr.getvalue())
        self.assertEqual(cfg["issueStore"], None)

    def test_a_config_with_browserApp_or_browserBundleId_loads_with_a_warning(self):
        path = writeConfig({"browserApp": "Brave Browser", "browserBundleId": "com.brave.Browser"})
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            cfg = config.load(path)
        self.assertIn("browserApp, which is no longer read", stderr.getvalue())
        self.assertIn("browserBundleId, which is no longer read", stderr.getvalue())
        self.assertNotIn("browserApp", config.defaults())
        self.assertEqual(cfg["issueStore"], None)

    def test_a_malformed_file_fails_loudly(self):
        path = writeConfig({})
        with open(path, "w", encoding="utf8") as f:
            f.write("{not json")
        with self.assertRaisesRegex(RuntimeError, "isn't valid JSON"):
            config.load(path)

    def test_issueStore_expands_the_home_folder(self):
        cfg = config.load(writeConfig({"issueStore": "~/programming/issues"}))
        self.assertEqual(cfg["issueStore"], os.path.expanduser("~/programming/issues"))

    def test_the_retired_issuesDir_key_is_refused_naming_issueStore(self):
        with self.assertRaisesRegex(RuntimeError, "issuesDir, which is now issueStore"):
            config.load(writeConfig({"issuesDir": "/somewhere"}))

    def test_issueStoreFrom_names_the_key_when_none_is_set(self):
        with self.assertRaisesRegex(RuntimeError, 'no issue store set: add "issueStore"'):
            config.issueStoreFrom(config.defaults())

    def test_issueStoreFrom_returns_the_configured_store(self):
        self.assertEqual(config.issueStoreFrom({"issueStore": "/store"}), "/store")


    def test_the_defaults_name_no_jira_site_project_or_jql(self):
        defaults = config.defaults()
        self.assertEqual(config.jiraProjects(defaults), [])
        self.assertIsNone(defaults["baseUrl"])
        self.assertNotIn("jql", defaults)
        self.assertNotIn("project", defaults)

    def test_jiraProjects_are_the_jira_entrys_keys_in_order(self):
        cfg = config.load(writeConfig({"jira": {"CIAM": {"repos": []}, "OPS": {"repos": []}}}))
        self.assertEqual(config.jiraProjects(cfg), ["CIAM", "OPS"])

    def test_jiraSettingsFrom_refuses_a_jira_entry_with_no_site(self):
        with self.assertRaisesRegex(RuntimeError, 'the "jira" entry needs a site: add "baseUrl"'):
            config.jiraSettingsFrom({**config.defaults(), "jira": {"CIAM": {}}})

    def test_jiraSettingsFrom_names_the_configured_projects(self):
        cfg = {**config.defaults(), "jira": {"CIAM": {}, "OPS": {}}, "baseUrl": "https://jira.example"}
        self.assertEqual(
            config.jiraSettingsFrom(cfg),
            {
                "baseUrl": "https://jira.example",
                "jql": {"CIAM": None, "OPS": None},
                "netrcFile": None,
            },
        )

    def test_jiraSettingsFrom_carries_each_projects_own_jql(self):
        cfg = {**config.defaults(), "jira": {"CIAM": {"jql": "key = CIAM-1"}, "OPS": None}, "baseUrl": "https://jira.example"}
        self.assertEqual(config.jiraSettingsFrom(cfg)["jql"], {"CIAM": "key = CIAM-1", "OPS": None})

    def test_a_config_with_a_top_level_jql_is_refused_naming_where_it_went(self):
        with self.assertRaisesRegex(RuntimeError, 'sets jql, which is now per project: move it into its project\'s entry under "jira"'):
            config.load(writeConfig({"jql": "key = CIAM-1"}))

    def test_a_config_with_the_retired_project_key_loads_with_a_warning(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            config.load(writeConfig({"project": "CIAM"}))
        self.assertIn("project, which is no longer read", stderr.getvalue())

if __name__ == "__main__":
    unittest.main()
