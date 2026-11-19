# lib/interim_*.py: the interim suite's own helpers, proven offline (no Redmine, git or Keychain).
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from . import interim_config as configuration
from . import interim_e2e as suite
from . import interim_probes as probes
from . import interim_triggers as triggers
from .interim_world import CheckFailed, PendingTrigger, Redmine


def journal(id, user, *names):
    return {"id": id, "user": {"id": user}, "details": [{"name": n} for n in names]}


class ConfigTest(unittest.TestCase):
    def writeFile(self, text):
        path = os.path.join(tempfile.mkdtemp(prefix="skuld-interim-"), "interim-e2e.json")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_the_file_is_skulds_and_a_missing_file_is_the_defaults(self):
        self.assertEqual(configuration.CONFIG_FILE, os.path.expanduser("~/.config/skuld/interim-e2e.json"))
        self.assertEqual(configuration.load("/nonexistent/interim-e2e.json"), configuration.DEFAULTS)

    def test_the_file_merges_over_the_defaults_and_expands_paths(self):
        settings = configuration.load(self.writeFile('{"taskNumber": 41, "fixtureRepo": "~/fixture"}'))
        self.assertEqual(settings["taskNumber"], 41)
        self.assertEqual(settings["fixtureRepo"], os.path.expanduser("~/fixture"))
        self.assertEqual(settings["redmineUrl"], "http://127.0.0.1:3010")

    def test_an_unknown_key_or_bad_json_is_refused(self):
        with self.assertRaisesRegex(configuration.NotConfigured, "apiKey"):
            configuration.load(self.writeFile('{"apiKey": "x"}'))
        with self.assertRaisesRegex(configuration.NotConfigured, "not JSON"):
            configuration.load(self.writeFile("{"))

    def test_the_defaults_name_keychain_services_and_no_secret(self):
        for key, value in configuration.DEFAULTS.items():
            if key.endswith("Service"):
                self.assertTrue(value.startswith("skuld-"), key)

    def test_require_names_the_missing_key(self):
        with self.assertRaisesRegex(configuration.NotConfigured, "taskNumber is not set"):
            configuration.require(configuration.DEFAULTS, "taskNumber")


class JournalTest(unittest.TestCase):
    def test_the_newest_status_journal_skips_later_journals_that_do_not_change_status(self):
        journals = [journal(1, 5, "status_id"), journal(3, 7, "status_id"), journal(4, 9, "assigned_to_id")]
        self.assertEqual(probes.newestStatusJournal(journals)["id"], 3)

    def test_no_status_journal_is_none(self):
        self.assertIsNone(probes.newestStatusJournal([journal(1, 5, "subject")]))
        self.assertIsNone(probes.newestStatusJournal([]))

    def test_the_newest_journal_is_the_highest_id(self):
        self.assertEqual(probes.newestJournal([journal(2, 1), journal(9, 2), journal(4, 3)])["id"], 9)
        self.assertIsNone(probes.newestJournal([]))

    def test_user_and_count(self):
        self.assertEqual(probes.journalUserId(journal(1, 5)), 5)
        self.assertIsNone(probes.journalUserId(None))
        self.assertEqual(probes.journalCount({"journals": [journal(1, 1), journal(2, 1)]}), 2)
        self.assertEqual(probes.journalCount({}), 0)


class GitStateTest(unittest.TestCase):
    PORCELAIN = (
        "worktree /repo\nHEAD aaa\nbranch refs/heads/main\n\n"
        "worktree /wt/SK-41\nHEAD bbb\nbranch refs/heads/feat/SK-41-end-to-end\n"
    )

    def test_branch_names_strip_the_current_marker(self):
        self.assertEqual(probes.branchNames("  feat/SK-41-a\n* feat/SK-41-b\n"), ["feat/SK-41-a", "feat/SK-41-b"])
        self.assertEqual(probes.branchNames(""), [])

    def test_worktrees_are_found_by_branch(self):
        self.assertEqual(probes.worktreesOnBranches(self.PORCELAIN, ["feat/SK-41-end-to-end"]), ["/wt/SK-41"])
        self.assertEqual(probes.worktreesOnBranches(self.PORCELAIN, []), [])

    def test_staged_equal_to_the_manifest_has_no_difference(self):
        self.assertEqual(probes.compareStaged(["a.py", "docs/x.md"], ["a.py", "docs/"]), ([], []))

    def test_a_manifest_file_not_staged_and_a_staged_file_not_listed_are_named(self):
        missing, unexpected = probes.compareStaged(["b.py"], ["a.py"])
        self.assertEqual((missing, unexpected), (["a.py"], ["b.py"]))

    def test_a_directory_entry_does_not_cover_a_sibling_with_the_same_prefix(self):
        self.assertEqual(probes.compareStaged(["docs-extra/x"], ["docs/"]), ([], ["docs-extra/x"]))

    def test_porcelain_status_of_a_path(self):
        porcelain = " M a.py\n?? e2e-unlisted.txt\n"
        self.assertEqual(probes.porcelainStatusOf(porcelain, "e2e-unlisted.txt"), "??")
        self.assertEqual(probes.porcelainStatusOf(porcelain, "a.py"), " M")
        self.assertIsNone(probes.porcelainStatusOf(porcelain, "gone.txt"))


class PageTest(unittest.TestCase):
    def test_a_link_in_the_description_is_found(self):
        html = '<a href="/nav">x</a><div class="issue description"><br/><p>See <a href="/issues/41">SK-41</a></p></div>'
        self.assertEqual(probes.descriptionLinks(html), ["/issues/41"])
        self.assertTrue(probes.linksToIssue(html, 41))

    def test_a_link_outside_the_description_does_not_count(self):
        html = '<div class="description"><p>SK-41</p></div><a href="/issues/41">top</a>'
        self.assertFalse(probes.linksToIssue(html, 41))

    def test_an_unlinked_key_is_not_a_link(self):
        self.assertFalse(probes.linksToIssue('<div class="description">SK-41</div>', 41))

    def test_memberships_are_the_sorted_project_names(self):
        user = {"memberships": [{"project": {"name": "skuld-fixture"}}, {"project": {"name": "skuld"}}]}
        self.assertEqual(probes.membershipProjectNames(user), ["skuld", "skuld-fixture"])
        self.assertEqual(probes.membershipProjectNames({}), [])

    def test_rounds_added_and_the_fetch_path(self):
        self.assertEqual(probes.roundsAdded({"review_rounds": [1]}, {"review_rounds": [1, 2]}), 1)
        self.assertEqual(probes.fetchChangesetsPath("skuld", "k"), "/sys/fetch_changesets?id=skuld&key=k")


TICKET = """---
status: done
milestone: ticket-store
ordinal: 310
blocked_by: SK-0003, SK-0004
---

# Write the ADR

The body.

## Done

- [x] First item
      wrapped on a second line.
- [ ] Second item

## Log

- 2026-11-17: Filed.
"""


class TicketTest(unittest.TestCase):
    def test_a_ticket_parses_into_title_body_fields_and_done_items(self):
        ticket = probes.parseTicket(TICKET)
        self.assertEqual(ticket["title"], "Write the ADR")
        self.assertTrue(ticket["body"].startswith("The body."))
        self.assertEqual(ticket["frontmatter"]["ordinal"], "310")
        self.assertEqual(
            ticket["done"],
            [{"text": "First item wrapped on a second line.", "ticked": True}, {"text": "Second item", "ticked": False}],
        )
        self.assertEqual(probes.blockerKeys(ticket["frontmatter"]), ["SK-0003", "SK-0004"])

    def test_a_file_without_frontmatter_is_refused(self):
        with self.assertRaises(ValueError):
            probes.parseTicket("# Title\n")

    def issue(self, ticket, **changes):
        issue = {
            "id": 7,
            "subject": ticket["title"],
            "description": ticket["body"],
            "status": {"name": "Closed"},
            "fixed_version": {"name": "ticket-store"},
            "custom_fields": [{"name": "Ordinal", "value": "310"}],
            "relations": [{"issue_id": 3, "issue_to_id": 7, "relation_type": "blocks"}],
            "journals": [{"id": 1, "user": {"id": 99}}],
        }
        issue.update(changes)
        return issue

    def mismatches(self, ticket, issue, checklist=None, blockers=(3,)):
        checklist = ticket["done"] if checklist is None else checklist
        return probes.importMismatches(ticket, issue, checklist, list(blockers), {"done": "Closed"}, 99)

    def test_an_import_that_keeps_every_field_has_no_mismatch(self):
        ticket = probes.parseTicket(TICKET)
        self.assertEqual(self.mismatches(ticket, self.issue(ticket)), [])

    def test_each_lost_field_is_named(self):
        ticket = probes.parseTicket(TICKET)
        broken = self.issue(
            ticket,
            subject="Other",
            status={"name": "New"},
            custom_fields=[],
            relations=[],
            journals=[{"id": 1, "user": {"id": 5}}],
        )
        names = [m.split(":")[0] for m in self.mismatches(ticket, broken, checklist=[])]
        self.assertEqual(names, ["subject", "status", "Ordinal", "blocks", "checklist", "journal users other than the bot"])

    def test_first_failures_counts_the_rest(self):
        self.assertEqual(probes.firstFailures(["a", "b"], limit=3), "a; b")
        self.assertEqual(probes.firstFailures(["a", "b", "c", "d"], limit=2), "a; b; and 2 more")


class PendingTriggerTest(unittest.TestCase):
    def test_each_trigger_fails_with_its_owner_until_it_is_defined(self):
        calls = {
            triggers.start: ("SK-1",),
            triggers.startRefusal: ("XX-1",),
            triggers.ship: ("SK-1", "/wt", []),
            triggers.done: ("SK-1",),
            triggers.tickChecklistItem: ("SK-1", None),
            triggers.checklistItems: (1,),
            triggers.createChecklistItem: (1, "t"),
            triggers.deleteChecklistItem: (1, None),
            triggers.storedRoundFiles: ({}, {}),
            triggers.reportFindings: ("/wt",),
            triggers.storedFindings: ({}, {}),
        }
        for trigger, args in calls.items():
            with self.assertRaisesRegex(PendingTrigger, r"SK-00(84|85|86): .* not defined"):
                trigger(*args)

    def test_the_plugin_trigger_message_is_the_one_the_spec_names(self):
        with self.assertRaisesRegex(PendingTrigger, "SK-0086: plugin trigger not defined"):
            triggers.start("SK-1")


class FakeRedmine(BaseHTTPRequestHandler):
    users = {"user": {"id": 99, "memberships": [{"project": {"name": "skuld"}}]}}  # noqa: RUF012 (tests swap it)

    def do_GET(self):
        if self.path.startswith("/users/current.json"):
            self.reply(200, self.users)
        else:
            self.reply(404, {})

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class RedmineClientTest(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), FakeRedmine)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        patch = mock.patch("lib.interim_world.keychainSecret", return_value="bot-key")
        patch.start()
        self.addCleanup(patch.stop)

    def settings(self, **changes):
        return {**configuration.DEFAULTS, "redmineUrl": self.url, **changes}

    def test_a_read_returns_the_json(self):
        redmine = Redmine(self.url, "svc", "svc")
        self.assertEqual(redmine.get("/users/current.json")["user"]["id"], 99)

    def test_an_http_error_names_the_path_and_status(self):
        with self.assertRaisesRegex(CheckFailed, r"GET .*/issues/1.json answered HTTP 404"):
            Redmine(self.url, "svc", "svc").get("/issues/1.json")

    def test_check_12_passes_when_the_memberships_are_skulds_only(self):
        suite.bot_reaches_skuld_projects_only(suite.World(self.settings()))

    def test_check_12_fails_naming_the_extra_project(self):
        FakeRedmine.users = {"user": {"memberships": [{"project": {"name": "skuld"}}, {"project": {"name": "other"}}]}}
        self.addCleanup(setattr, FakeRedmine, "users", {"user": {"id": 99, "memberships": [{"project": {"name": "skuld"}}]}})
        with self.assertRaisesRegex(CheckFailed, r"\['other', 'skuld'\]"):
            suite.bot_reaches_skuld_projects_only(suite.World(self.settings()))


class RunTest(unittest.TestCase):
    def runWith(self, text):
        folder = tempfile.mkdtemp(prefix="skuld-interim-")
        path = os.path.join(folder, "interim-e2e.json")
        with open(path, "w") as f:
            f.write(text)
        out = io.StringIO()
        with mock.patch("lib.interim_world.keychainSecret", side_effect=CheckFailed("no key")):
            code = suite.run(path, out)
        return code, out.getvalue()

    def test_an_unreachable_instance_lists_all_12_checks_failing_with_the_cause(self):
        code, output = self.runWith('{"redmineUrl": "http://127.0.0.1:1"}')
        self.assertEqual(code, 1)
        lines = [l for l in output.splitlines() if l.startswith("FAIL")]
        self.assertEqual(len(lines), 12)
        self.assertEqual(output.count("RESET  Redmine unreachable"), 1)
        self.assertTrue(all("Redmine unreachable" in l for l in lines))
        self.assertIn("0 of 12 checks passed", output)

    def test_each_check_names_its_owning_ticket(self):
        owners = {number: owner for number, _, owner, _ in suite.CHECKS}
        self.assertEqual(owners[3], "SK-0087, SK-0086")
        self.assertEqual(sorted(owners), list(range(1, 13)))

    def test_a_bad_config_fails_all_12_checks_with_that_cause(self):
        code, output = self.runWith("{")
        self.assertEqual(code, 1)
        self.assertEqual(output.count("not JSON"), 12)


if __name__ == "__main__":
    unittest.main()
