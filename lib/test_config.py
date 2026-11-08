import json
import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
