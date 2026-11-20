# lib/interim_*.py: the interim suite's own helpers, proven offline (no Redmine, git or Keychain).
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from . import fileissues
from . import interim_config as configuration
from . import interim_e2e as suite
from . import interim_probes as probes
from . import interim_triggers as triggers
from . import interim_world as world
from .interim_world import CheckFailed, Fixture, PendingTrigger, Redmine


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

    def test_branch_names_are_the_lines_of_the_refname_format(self):
        self.assertEqual(probes.branchNames("feat/SK-41-a\nfeat/SK-41-b\n"), ["feat/SK-41-a", "feat/SK-41-b"])
        self.assertEqual(probes.branchNames(""), [])

    def test_worktrees_are_found_by_branch(self):
        self.assertEqual(probes.worktreesOf(self.PORCELAIN, ["feat/SK-41-end-to-end"], "none"), ["/wt/SK-41"])
        self.assertEqual(probes.worktreesOf(self.PORCELAIN, [], "none"), [])

    def test_a_detached_worktree_is_found_by_its_folder_name(self):
        detached = self.PORCELAIN + "\nworktree /wt/SK-42\nHEAD ccc\ndetached\n"
        self.assertEqual(probes.worktreesOf(detached, [], "SK-42"), ["/wt/SK-42"])
        self.assertEqual(probes.worktreesOf(detached, [], "SK-4"), [])

    def test_staged_equal_to_the_manifest_has_no_difference(self):
        self.assertEqual(probes.compareStaged(["a.py", "docs/x.md"], ["a.py", "docs/"]), ([], []))

    def test_a_manifest_file_not_staged_and_a_staged_file_not_listed_are_named(self):
        missing, unexpected = probes.compareStaged(["b.py"], ["a.py"])
        self.assertEqual((missing, unexpected), (["a.py"], ["b.py"]))

    def test_a_directory_entry_does_not_cover_a_sibling_with_the_same_prefix(self):
        self.assertEqual(probes.compareStaged(["docs-extra/x"], ["docs/"]), ([], ["docs-extra/x"]))

    def test_a_directory_entry_is_one_with_a_trailing_slash(self):
        self.assertTrue(probes.isDirectoryEntry("docs/"))
        self.assertFalse(probes.isDirectoryEntry("docs"))

    def test_a_path_is_listed_by_name_or_by_a_directory_entry_above_it(self):
        manifest = ["a.py", "docs/"]
        self.assertTrue(probes.isListed("a.py", manifest))
        self.assertTrue(probes.isListed("docs/x/y.md", manifest))
        self.assertFalse(probes.isListed("docs", manifest))
        self.assertFalse(probes.isListed("docs-extra/x", manifest))

    def test_porcelain_status_of_a_path(self):
        porcelain = " M a.py\n?? e2e-unlisted.txt\n"
        self.assertEqual(probes.porcelainStatusOf(porcelain, "e2e-unlisted.txt"), "??")
        self.assertEqual(probes.porcelainStatusOf(porcelain, "a.py"), " M")
        self.assertIsNone(probes.porcelainStatusOf(porcelain, "gone.txt"))


class RemoteIdentityTest(unittest.TestCase):
    def test_ssh_scp_and_https_forms_of_one_repo_are_equal(self):
        forms = [
            "git@github.com:Owner/skuld.git",
            "ssh://git@github.com/owner/skuld",
            "https://github.com/owner/skuld.git",
            "https://github.com/owner/skuld/",
        ]
        self.assertEqual({probes.remoteIdentity(url) for url in forms}, {"github.com/owner/skuld"})

    def test_another_repo_or_host_differs(self):
        self.assertNotEqual(probes.remoteIdentity("git@github.com:owner/skuld-fixture.git"), probes.remoteIdentity("git@github.com:owner/skuld.git"))
        self.assertNotEqual(probes.remoteIdentity("git@gitlab.com:owner/skuld.git"), probes.remoteIdentity("git@github.com:owner/skuld.git"))

    def test_a_local_path_is_its_real_path(self):
        folder = os.path.realpath(tempfile.mkdtemp(prefix="skuld-remote-"))
        self.assertEqual(probes.remoteIdentity(folder + "/"), folder)
        self.assertEqual(probes.remoteIdentity("file://" + folder), folder)


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def repoWithOrigin(parent, name, origin):
    path = os.path.join(parent, name)
    git("init", "-q", path, cwd=parent)
    if origin:
        git("remote", "add", "origin", origin, cwd=path)
    return path


class FixtureGuardTest(unittest.TestCase):
    """The guard against skuld's own repo, with a temp repo standing in for skuld's checkout."""

    SKULDS_ORIGIN = "git@github.com:owner/skuld.git"

    def setUp(self):
        self.parent = os.path.realpath(tempfile.mkdtemp(prefix="skuld-guard-"))
        skuld = repoWithOrigin(self.parent, "skuld", self.SKULDS_ORIGIN)
        git("commit", "-q", "--allow-empty", "-m", "first", cwd=skuld)
        self.skuld = skuld
        patcher = mock.patch.object(world, "REPO_ROOT", skuld)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_repo_with_another_origin_passes(self):
        Fixture(repoWithOrigin(self.parent, "fixture", "https://github.com/owner/skuld-fixture.git")).requireNotSkuldsRepo()

    def test_a_worktree_of_skulds_checkout_is_refused(self):
        worktree = os.path.join(self.parent, "skuld-wt")
        git("worktree", "add", "-q", "-b", "feat/SK-1-x", worktree, cwd=self.skuld)
        with self.assertRaisesRegex(CheckFailed, "is skuld's own repo"):
            Fixture(worktree).requireNotSkuldsRepo()

    def test_a_separate_clone_of_skulds_remote_is_refused_in_any_url_form(self):
        clone = repoWithOrigin(self.parent, "clone", "https://github.com/owner/skuld")
        with self.assertRaisesRegex(CheckFailed, "is a clone of skuld's repo"):
            Fixture(clone).requireNotSkuldsRepo()

    def test_a_repo_with_no_origin_is_refused(self):
        with self.assertRaisesRegex(CheckFailed, "has no origin"):
            Fixture(repoWithOrigin(self.parent, "bare-local", None)).requireNotSkuldsRepo()

    def test_a_missing_folder_is_refused(self):
        with self.assertRaisesRegex(CheckFailed, "does not exist"):
            Fixture(os.path.join(self.parent, "absent")).requireNotSkuldsRepo()


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

    def test_the_fetch_path_encodes_the_key(self):
        path = probes.fetchChangesetsPath("skuld", "a&b+c=d")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        self.assertEqual(query["key"], ["a&b+c=d"])
        self.assertEqual(query["id"], ["skuld"])

    def test_the_report_page_ends_with_the_pr_command(self):
        self.assertTrue(probes.endsWithPrCommand("Done.\nrun `pr SK-41`\n", "SK-41"))
        self.assertFalse(probes.endsWithPrCommand("run `pr SK-41` first, then wait", "SK-41"))

    def test_a_journal_mentions_text_held_in_any_detail_value(self):
        detail = {"property": "attr", "name": "checklist", "new_value": "Suite item ticked"}
        self.assertTrue(probes.journalMentions({"details": [detail]}, "Suite item"))
        self.assertFalse(probes.journalMentions({"details": [detail]}, "Other"))
        self.assertFalse(probes.journalMentions(None, "Suite item"))


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

    def test_the_body_stops_before_the_log(self):
        body = probes.parseTicket(TICKET)["body"]
        self.assertNotIn("## Log", body)
        self.assertNotIn("Filed.", body)
        self.assertTrue(body.endswith("- [ ] Second item"))

    def test_a_ticket_reads_the_same_in_skulds_file_store_parser(self):
        text = TICKET.replace("ordinal: 310", "ordinal: 310   # the work order")
        front, title, body, entries = fileissues._parse(text)
        ticket = probes.parseTicket(text)
        self.assertEqual((ticket["frontmatter"], ticket["title"], ticket["body"]), (front, title, body.strip()))
        self.assertEqual(ticket["frontmatter"]["ordinal"], "310")
        self.assertEqual(entries[0]["text"], "Filed.")

    def test_a_ticket_key_comes_from_the_file_name(self):
        self.assertEqual(probes.ticketKey("SK-0012-write-the-adr.md"), "SK-0012")
        self.assertIsNone(probes.ticketKey("README.md"))

    def test_a_file_without_frontmatter_is_refused(self):
        with self.assertRaises(RuntimeError):
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
            triggers.pr: ("SK-1", "/wt"),
            triggers.roundStatus: ({}, {}),
            triggers.reportPageText: ({}, {}),
            triggers.storedFindings: ({}, {}),
        }
        for trigger, args in calls.items():
            with self.assertRaisesRegex(PendingTrigger, r"SK-00(84|85|86|87): .* not defined"):
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

    def test_an_unreachable_instance_prints_the_reset_cause_and_runs_no_check(self):
        code, output = self.runWith('{"redmineUrl": "http://127.0.0.1:1", "taskNumber": 41}')
        self.assertEqual(code, 1)
        lines = [l for l in output.splitlines() if l.startswith("SKIP")]
        self.assertEqual(len(lines), 12)
        self.assertEqual(output.count("RESET  Redmine unreachable"), 1)
        self.assertTrue(all("not run" in l for l in lines))
        self.assertIn("0 of 12 checks passed", output)

    def test_each_check_names_its_owning_ticket(self):
        owners = {number: owner for number, _, owner, _ in suite.CHECKS}
        self.assertEqual(owners[3], "SK-0087, SK-0086")
        self.assertEqual(owners[4], "SK-0087, SK-0084")
        self.assertEqual(sorted(owners), list(range(1, 13)))

    def test_a_bad_config_fails_all_12_checks_with_that_cause(self):
        code, output = self.runWith("{")
        self.assertEqual(code, 1)
        self.assertEqual(output.count("not JSON"), 12)


# ---- temp repos standing in for the fixture repo and its origin

def commit(path, name, text="x\n"):
    with open(os.path.join(path, name), "a") as f:
        f.write(text)
    git("add", name, cwd=path)
    git("commit", "-q", "-m", f"add {name}", cwd=path)


class RepoCase(unittest.TestCase):
    """A bare local origin and a clone of it (the fixture), both in a temp folder."""

    def setUp(self):
        self.parent = os.path.realpath(tempfile.mkdtemp(prefix="skuld-fixture-"))
        self.origin = os.path.join(self.parent, "origin.git")
        git("init", "-q", "--bare", "-b", "main", self.origin, cwd=self.parent)
        self.path = os.path.join(self.parent, "fixture")
        git("clone", "-q", self.origin, self.path, cwd=self.parent)
        git("checkout", "-q", "-b", "main", cwd=self.path)
        commit(self.path, "a.txt")
        git("push", "-q", "-u", "origin", "main", cwd=self.path)
        git("remote", "set-head", "origin", "main", cwd=self.path)
        self.fixture = Fixture(self.path)

    def addWorktree(self, branch, folder):
        path = os.path.join(self.parent, folder)
        git("worktree", "add", "-q", "-b", branch, path, cwd=self.path)
        return path

    def detachWorktree(self, folder):
        """A worktree as a Done that left HEAD detached would leave it: no branch, the folder kept."""
        path = os.path.join(self.parent, folder)
        git("worktree", "add", "-q", "--detach", path, cwd=self.path)
        return path

    def originBranches(self):
        out = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"], cwd=self.origin, capture_output=True, text=True, check=True
        ).stdout
        return out.split()


class FixtureGitTest(RepoCase):
    def test_a_branch_with_a_linked_worktree_is_found_without_its_marker(self):
        self.addWorktree("feat/SK-1-x", "wt")
        self.assertEqual(self.fixture.branches(1), ["feat/SK-1-x"])
        self.assertEqual(self.fixture.worktrees(1), [os.path.join(self.parent, "wt")])

    def test_a_detached_worktree_in_the_issues_folder_is_found_and_removed(self):
        folder = self.detachWorktree("SK-1")
        self.assertEqual(self.fixture.worktrees(1), [folder])
        self.assertEqual(self.fixture.worktrees(1, "XX"), [])
        self.fixture.remove(1)
        self.assertFalse(os.path.exists(folder))

    def test_remove_deletes_the_issues_worktree_and_branch_only(self):
        mine = self.addWorktree("feat/SK-1-x", "wt-1")
        other = self.addWorktree("feat/SK-12-y", "wt-12")
        git("branch", "feat/SK-1-loose", cwd=self.path)
        self.fixture.remove(1)
        self.assertFalse(os.path.exists(mine))
        self.assertEqual(self.fixture.branches(1), [])
        self.assertEqual(self.fixture.branches(12), ["feat/SK-12-y"])
        self.assertTrue(os.path.isdir(other))

    def test_remove_deletes_the_remote_branch_and_closes_its_open_pr(self):
        git("push", "-q", "origin", "main:refs/heads/feat/SK-1-x", "main:refs/heads/feat/SK-12-y", cwd=self.path)
        with mock.patch.object(Fixture, "closeOpenPullRequests") as close:
            self.fixture.remove(1)
        self.assertEqual(sorted(self.originBranches()), ["feat/SK-12-y", "main"])
        close.assert_called_once_with("feat/SK-1-x")

    def test_closing_a_branchs_open_prs_closes_each_with_gh(self):
        calls = []
        with mock.patch.object(Fixture, "openPullRequests", return_value=[{"number": 5, "body": ""}]), \
                mock.patch.object(Fixture, "run", side_effect=lambda *command: calls.append(command)):
            self.fixture.closeOpenPullRequests("feat/SK-1-x")
        self.assertEqual(calls, [("gh", "pr", "close", "5")])

    def test_a_push_to_the_default_branch_reaches_the_bare_origin_only(self):
        head = self.fixture.head(self.path)
        self.fixture.pushCommitToDefaultBranch("fixes SK-41")
        log = subprocess.run(["git", "log", "-1", "--format=%s", "main"], cwd=self.origin, capture_output=True, text=True).stdout
        self.assertEqual(log.strip(), "fixes SK-41")
        self.assertEqual(self.fixture.head(self.path), head)

    def test_a_command_that_outlives_the_timeout_fails_naming_it(self):
        with self.assertRaisesRegex(CheckFailed, r"`sleep 5` .* did not finish in 0.2 seconds"):
            Fixture(self.path, timeout=0.2).run("sleep", "5")


# ---- a scriptable Redmine

class ScriptedRedmine(BaseHTTPRequestHandler):
    """Answers (method, path) from server.routes: a JSON value, a str (sent raw), or a callable of
    the query dict returning either. Every request is kept in server.requests."""

    def handle_any(self):
        parts = urllib.parse.urlsplit(self.path)
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.requests.append((self.command, parts.path, parts.query, dict(self.headers)))
        route = self.server.routes.get((self.command, parts.path))
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        body = route(urllib.parse.parse_qs(parts.query)) if callable(route) else route
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_PUT = handle_any

    def log_message(self, *args):
        pass


def startRedmine(test, routes):
    server = HTTPServer(("127.0.0.1", 0), ScriptedRedmine)
    server.routes, server.requests = routes, []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    server.url = f"http://127.0.0.1:{server.server_port}"
    return server


TASK = {"issue": {"id": 41, "subject": "End-to-end task", "status": {"name": "New"}, "journals": []}}


class SuiteCase(unittest.TestCase):
    """Checks run against a ScriptedRedmine, with the keychain and the unbuilt triggers faked."""

    def setUp(self):
        keychain = mock.patch("lib.interim_world.keychainSecret", return_value="bot-key")
        keychain.start()
        self.addCleanup(keychain.stop)

    def stub(self, target, name, **kw):
        patcher = mock.patch.object(target, name, **kw)
        patcher.start()
        self.addCleanup(patcher.stop)

    def worldFor(self, server, **settings):
        base = {**configuration.DEFAULTS, "redmineUrl": server.url, "taskNumber": 41, "signedInUserId": 7}
        return suite.World({**base, **settings})


class ResetTest(SuiteCase, RepoCase):
    def setUp(self):
        SuiteCase.setUp(self)
        RepoCase.setUp(self)
        self.stub(triggers, "checklistItems", return_value=[])
        self.routes = {
            ("GET", "/issues/41.json"): TASK,
            ("GET", "/issue_statuses.json"): {"issue_statuses": [{"id": 1, "name": "New"}]},
            ("PUT", "/issues/41.json"): "",
        }
        self.server = startRedmine(self, self.routes)

    def world(self):
        return self.worldFor(self.server, fixtureRepo=self.path)

    def test_a_reset_with_nothing_wrong_reports_no_problem(self):
        self.assertEqual(suite.reset(self.world()), [])

    def test_a_reply_that_is_not_json_is_a_problem_naming_its_type(self):
        self.routes[("PUT", "/issues/41.json")] = "<html>oops</html>"
        problems = suite.reset(self.world())
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].text.startswith("JSONDecodeError"))
        self.assertTrue(problems[0].blocksChecks)

    def test_a_reset_that_waits_on_unbuilt_work_does_not_block_the_checks(self):
        self.stub(triggers, "checklistItems", side_effect=PendingTrigger("SK-0085: checklist API not defined"))
        problems = suite.reset(self.world())
        self.assertEqual([(p.text, p.blocksChecks) for p in problems], [("SK-0085: checklist API not defined", False)])

    def test_a_failed_reset_runs_no_check_and_writes_nothing_more(self):
        self.routes[("PUT", "/issues/41.json")] = "<html>oops</html>"
        config = os.path.join(self.parent, "interim-e2e.json")
        with open(config, "w") as f:
            json.dump({"redmineUrl": self.server.url, "taskNumber": 41, "fixtureRepo": self.path}, f)
        out = io.StringIO()
        code = suite.run(config, out)
        self.assertEqual(code, 1)
        self.assertIn("RESET  JSONDecodeError", out.getvalue())
        self.assertEqual(out.getvalue().count("SKIP"), 12)
        writes = [r for r in self.server.requests if r[0] == "PUT"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.originBranches(), ["main"])

    def test_the_reset_removes_the_fixtures_leftovers_too(self):
        self.addWorktree("feat/SK-41-x", "wt")
        suite.reset(self.world())
        self.assertEqual(self.fixture.branches(41), [])


class ChecksTest(SuiteCase, RepoCase):
    def setUp(self):
        SuiteCase.setUp(self)
        RepoCase.setUp(self)
        self.routes = {("GET", "/issues/41.json"): TASK}
        self.server = startRedmine(self, self.routes)

    def world(self, **settings):
        return self.worldFor(self.server, fixtureRepo=self.path, **settings)

    # check 2
    def test_check_2_fails_naming_a_branch_the_refused_start_created(self):
        self.stub(triggers, "startRefusal", return_value="No project has the prefix XX")
        self.addWorktree("feat/XX-41-x", "wt")
        with self.assertRaisesRegex(CheckFailed, r"branch feat/XX-41-x exists.*worktree .*wt exists"):
            suite.mismatched_prefix_is_refused(self.world())

    def test_check_2_fails_for_a_detached_worktree_the_refused_start_created(self):
        self.stub(triggers, "startRefusal", return_value="No project has the prefix XX")
        self.detachWorktree("XX-41")
        with self.assertRaisesRegex(CheckFailed, r"worktree .*XX-41 exists"):
            suite.mismatched_prefix_is_refused(self.world())

    def test_check_2_passes_when_the_refusal_leaves_nothing(self):
        self.stub(triggers, "startRefusal", return_value="No project has the prefix XX")
        suite.mismatched_prefix_is_refused(self.world())

    # check 5
    def test_check_5_does_not_mistake_a_longer_worktree_path_for_the_removed_one(self):
        first = self.addWorktree("feat/SK-12-a", "SK-12-x")
        self.routes[("GET", "/issues/12.json")] = {"issue": {**TASK["issue"], "id": 12}}
        self.stub(Fixture, "mergePullRequestOf")

        def done(key):
            git("worktree", "remove", "--force", first, cwd=self.path)
            self.addWorktree("feat/SK-12-b", "SK-12-x-2")

        self.stub(triggers, "done", side_effect=done)
        suite.done_never_changes_status(self.world(taskNumber=12))

    def test_check_5_fails_when_done_detaches_the_worktree_and_deletes_the_branch(self):
        worktree = self.addWorktree("feat/SK-41-x", "SK-41")
        self.stub(Fixture, "mergePullRequestOf")

        def done(key):
            git("checkout", "-q", "--detach", cwd=worktree)
            git("branch", "-D", "feat/SK-41-x", cwd=self.path)

        self.stub(triggers, "done", side_effect=done)
        with self.assertRaisesRegex(CheckFailed, r"`git worktree list` still shows .*SK-41"):
            suite.done_never_changes_status(self.world())

    # check 6
    def test_check_6_reads_the_baseline_after_the_item_is_created(self):
        self.stubChecklist(tick={"id": 3, "user": {"id": 7}, "details": [{"name": "checklist", "new_value": "Suite item"}]})
        suite.ticked_done_box_is_journaled(self.world())

    def test_check_6_fails_when_the_newest_journal_is_an_unrelated_change(self):
        self.stubChecklist(tick={"id": 3, "user": {"id": 7}, "details": [{"name": "subject", "new_value": "Other"}]})
        with self.assertRaisesRegex(CheckFailed, "does not name the ticked item 'Suite item'"):
            suite.ticked_done_box_is_journaled(self.world())

    def stubChecklist(self, tick):
        """Redmine journals the item's creation as well as its tick, so a baseline read before the
        create is one journal short."""
        journals = [{"id": 1, "user": {"id": 7}, "details": []}]
        creation = {"id": 2, "user": {"id": 7}, "details": [{"name": "checklist", "new_value": "Suite item"}]}
        self.routes[("GET", "/issues/41.json")] = lambda query: {"issue": {**TASK["issue"], "journals": list(journals)}}
        self.stub(triggers, "createChecklistItem", side_effect=lambda number, text: journals.append(creation) or "item")
        self.stub(triggers, "tickChecklistItem", side_effect=lambda key, item: journals.append(tick))
        self.stub(triggers, "checklistItems", return_value=[{"text": "Suite item", "ticked": True}])

    # checks 3 and 4
    def stubRound(self, status):
        rows = {"n": 0}

        def ledger(query):
            rows["n"] += 1
            return {"review_rounds": [] if rows["n"] == 1 else [{"id": 1}]}

        self.routes[("GET", "/issues/41/review_findings.json")] = ledger
        self.stub(triggers, "storedRoundFiles", side_effect=lambda ledger, row: self.round_files())
        self.stub(triggers, "roundStatus", return_value=status)
        self.stub(triggers, "reportPageText", return_value="Round stored.\nrun `pr SK-41`")

    def round_files(self):
        folder = os.path.join(self.wt, ".tmp", "SK-41")
        return {n: suite.readBytes(os.path.join(folder, n)) for n in ("commit.txt", "pr-body.md")}

    def ship(self, key, worktree, manifest):
        git("add", *manifest, cwd=worktree)

    def pr(self, key, worktree):
        message = suite.readBytes(os.path.join(worktree, ".tmp", key, "commit.txt")).decode()
        git("commit", "-q", "-m", message, cwd=worktree)
        git("push", "-q", "-u", "origin", "HEAD", cwd=worktree)

    def prBody(self):
        return suite.readBytes(os.path.join(self.wt, ".tmp", "SK-41", "pr-body.md")).decode()

    def runCheck3(self, status="draft"):
        self.wt = self.addWorktree("feat/SK-41-x", "wt")
        self.stubRound(status)
        self.stub(triggers, "ship", side_effect=self.ship)
        world = self.world()
        suite.ship_uploads_the_round_as_a_draft(world)
        return world

    def test_check_3_passes_for_a_correct_ship(self):
        self.runCheck3()

    def test_check_3_fails_when_the_round_is_stored_as_sent(self):
        with self.assertRaisesRegex(CheckFailed, "status is 'sent', expected 'draft'"):
            self.runCheck3("sent")

    def test_check_3_fails_when_the_stored_bytes_differ(self):
        self.wt = self.addWorktree("feat/SK-41-x", "wt")
        self.stubRound("draft")
        self.stub(triggers, "storedRoundFiles", return_value={"commit.txt": b"changed", "pr-body.md": b"x"})
        self.stub(triggers, "ship", side_effect=self.ship)
        with self.assertRaisesRegex(CheckFailed, "commit.txt on the round is not the file's bytes"):
            suite.ship_uploads_the_round_as_a_draft(self.world())

    def test_check_3_fails_when_the_report_ends_with_another_line(self):
        self.wt = self.addWorktree("feat/SK-41-x", "wt")
        self.stubRound("draft")
        self.stub(triggers, "reportPageText", return_value="run `pr SK-41` or not")
        self.stub(triggers, "ship", side_effect=self.ship)
        with self.assertRaisesRegex(CheckFailed, "report page does not end with"):
            suite.ship_uploads_the_round_as_a_draft(self.world())

    def runCheck4(self, status, opened=True):
        world = self.runCheck3()
        self.stub(triggers, "roundStatus", return_value=status)
        self.stub(triggers, "pr", side_effect=self.pr)
        self.stub(Fixture, "openPullRequests", side_effect=lambda branch: [{"number": 1, "body": self.prBody()}] if opened else [])
        suite.pr_sends_the_round(world)

    def test_check_4_passes_when_pr_commits_pushes_opens_and_marks_the_round_sent(self):
        self.runCheck4("sent")

    def test_check_4_fails_naming_the_sent_probe_when_the_round_stays_a_draft(self):
        with self.assertRaisesRegex(CheckFailed, "status is 'draft', expected 'sent'"):
            self.runCheck4("draft")

    def test_check_4_fails_when_no_pr_is_open(self):
        with self.assertRaisesRegex(CheckFailed, "0 open PRs"):
            self.runCheck4("sent", opened=False)

    def test_check_4_has_no_round_to_send_when_check_3_never_prepared_one(self):
        self.addWorktree("feat/SK-41-x", "wt")
        with self.assertRaisesRegex(CheckFailed, "check 3 did not prepare the round"):
            suite.pr_sends_the_round(self.world())


class ImportChecksTest(SuiteCase):
    """Checks 9 to 11 against a store folder with active/, archive/ and reviews/."""

    def setUp(self):
        super().setUp()
        self.store = tempfile.mkdtemp(prefix="skuld-store-")
        self.stub(triggers, "checklistItems", return_value=[])
        self.routes = {
            ("GET", "/issues/41.json"): TASK,
            ("GET", "/users/current.json"): {"user": {"id": 99}},
        }
        self.server = startRedmine(self, self.routes)

    def ticket(self, folder, key, title="A ticket"):
        os.makedirs(os.path.join(self.store, folder), exist_ok=True)
        path = os.path.join(self.store, folder, f"{key}-slug.md")
        with open(path, "w") as f:
            f.write(f"---\nstatus: done\n---\n\n# {title}\n\nBody.\n\n## Log\n\n- 2026-11-17: Filed.\n")

    def world(self):
        return self.worldFor(self.server, issueStore=self.store, oldIdFieldId=5, fixtureRepo=None)

    def importedAs(self, ids):
        """/issues.json answers by the Old ID asked for: {old key: id or None}."""
        def lookup(query):
            key = query["cf_5"][0]
            found = ids.get(key)
            return {"total_count": 1 if found else 0, "issues": [{"id": found}] if found else []}
        self.routes[("GET", "/issues.json")] = lookup

    def test_checks_9_and_10_read_active_archive_and_the_root(self):
        self.ticket("", "SK-0001")
        self.ticket("active", "SK-0002")
        self.ticket("archive", "SK-0003")
        self.assertEqual(sorted(suite.storeTickets(self.world())), ["SK-0001", "SK-0002", "SK-0003"])

    def test_check_9_names_every_ticket_that_is_not_imported_once(self):
        self.ticket("active", "SK-0001")
        self.ticket("archive", "SK-0002")
        self.importedAs({})
        with self.assertRaisesRegex(CheckFailed, r"2 of 2 tickets: SK-0001: 0 issues.*SK-0002: 0 issues"):
            suite.every_ticket_is_imported_once(self.world())

    def test_check_10_reports_both_a_missing_import_and_a_wrong_field(self):
        self.ticket("active", "SK-0001")
        self.ticket("archive", "SK-0002", title="The title")
        self.importedAs({"SK-0002": 7})
        self.routes[("GET", "/issues/7.json")] = {
            "issue": {"id": 7, "subject": "Other", "description": "Body.\n", "status": {"name": "Closed"}, "journals": [], "relations": []}
        }
        with self.assertRaises(CheckFailed) as caught:
            suite.import_keeps_every_field(self.world())
        self.assertIn("SK-0001: 0 issues carry it as Old ID", str(caught.exception))
        self.assertIn("SK-0002: subject: file has 'The title'", str(caught.exception))

    def test_the_old_id_lookup_is_made_once_per_key_in_a_run(self):
        self.importedAs({"SK-0001": 7})
        world = self.world()
        suite.lookupByOldId(world, "SK-0001")
        suite.lookupByOldId(world, "SK-0001")
        self.assertEqual(len([r for r in self.server.requests if r[1] == "/issues.json"]), 1)

    def reviewFolder(self, key, files):
        for name, content in files.items():
            path = os.path.join(self.store, "reviews", key, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(content)

    def importRound(self, files, findings):
        self.importedAs({"SK-0001": 7})
        self.routes[("GET", "/issues/7/review_findings.json")] = {"review_rounds": [{"id": 1}]}
        self.stub(triggers, "storedRoundFiles", return_value=files)
        self.stub(triggers, "storedFindings", return_value=findings)

    def test_a_reply_in_replies_is_read_as_a_round_file(self):
        self.reviewFolder("SK-0001", {"commit.txt": b"c", "replies/1.md": b"r"})
        self.assertEqual(sorted(suite.storeRoundFiles(self.world())["SK-0001"]), ["commit.txt", "replies/1.md"])

    def test_check_11_fails_for_a_reply_the_import_dropped(self):
        self.reviewFolder("SK-0001", {"commit.txt": b"c", "replies/1.md": b"r"})
        self.importRound({"commit.txt": b"c"}, [])
        with self.assertRaisesRegex(CheckFailed, r"SK-0001: replies/1.md is on no imported round"):
            suite.every_round_is_imported(self.world())

    def test_check_11_fails_naming_the_findings_the_import_omitted(self):
        findings = [{"key": "lib/a.py:1:x"}]
        self.reviewFolder("SK-0001", {"commit.txt": b"c", "review.json": json.dumps({"findings": findings}).encode()})
        self.importRound({"commit.txt": b"c"}, [])
        with self.assertRaisesRegex(CheckFailed, r"SK-0001: findings differ: missing \[\{'key': 'lib/a.py:1:x'\}\]"):
            suite.every_round_is_imported(self.world())

    def test_check_11_passes_when_files_and_findings_match(self):
        findings = [{"key": "lib/a.py:1:x"}]
        self.reviewFolder("SK-0001", {"commit.txt": b"c", "review.json": json.dumps({"findings": findings}).encode()})
        self.importRound({"commit.txt": b"c"}, findings)
        suite.every_round_is_imported(self.world())


    def test_check_11_does_not_compare_findings_with_a_round_that_stored_no_files(self):
        findings = [{"key": "lib/a.py:1:x"}]
        self.reviewFolder("SK-0001", {"commit.txt": b"c", "review.json": json.dumps({"findings": findings}).encode()})
        self.importedAs({"SK-0001": 7})
        self.routes[("GET", "/issues/7/review_findings.json")] = {"review_rounds": [{"id": 1}, {"id": 2}]}
        self.stub(triggers, "storedRoundFiles", side_effect=lambda ledger, row: {} if row["id"] == 1 else {"commit.txt": b"c"})
        self.stub(triggers, "storedFindings", side_effect=lambda ledger, row: [] if row["id"] == 1 else findings)
        suite.every_round_is_imported(self.world())

    def test_check_10_records_a_malformed_ticket_and_still_examines_the_rest(self):
        self.ticket("active", "SK-0001")
        self.ticket("active", "SK-0002", title="The title")
        self.importedAs({"SK-0001": 7, "SK-0002": 8})
        issue = {"description": "Body.\n", "status": {"name": "Closed"}, "relations": []}
        self.routes[("GET", "/issues/7.json")] = {"issue": {"id": 7, "subject": "A ticket", **issue, "journals": [{"id": 1}]}}
        self.routes[("GET", "/issues/8.json")] = {"issue": {"id": 8, "subject": "Other", **issue, "journals": []}}
        with self.assertRaises(CheckFailed) as caught:
            suite.import_keeps_every_field(self.world())
        self.assertIn("SK-0001: KeyError", str(caught.exception))
        self.assertIn("SK-0002: subject: file has 'The title'", str(caught.exception))

    def test_check_9_names_a_key_held_in_both_the_root_and_archive(self):
        self.ticket("", "SK-0001")
        self.ticket("archive", "SK-0001")
        self.importedAs({"SK-0001": 7})
        with self.assertRaisesRegex(CheckFailed, r"SK-0001: in the store twice, at .*SK-0001-slug\.md and .*archive.*SK-0001-slug\.md"):
            suite.every_ticket_is_imported_once(self.world())


class KeychainTest(unittest.TestCase):
    def test_a_keychain_that_does_not_answer_fails_naming_the_service(self):
        timeout = subprocess.TimeoutExpired("security", world.TIMEOUT_SECONDS)
        with mock.patch("lib.interim_world.subprocess.run", side_effect=timeout):
            with self.assertRaisesRegex(CheckFailed, r"Keychain did not answer for service 'svc' in 15 seconds"):
                world.keychainSecret("svc")


class FixtureGuardOnceTest(SuiteCase):
    def test_the_fixture_guard_runs_once_per_run(self):
        self.stub(Fixture, "requireNotSkuldsRepo")
        run = self.worldFor(startRedmine(self, {}), fixtureRepo="/fixture")
        self.assertIs(run.fixture, run.fixture)
        self.assertEqual(run.fixture.path, "/fixture")
        Fixture.requireNotSkuldsRepo.assert_called_once_with()


class RedirectTest(unittest.TestCase):
    def test_a_redirect_is_not_followed_so_no_credential_reaches_its_target(self):
        target = startRedmine(self, {("GET", "/stolen"): {}})
        source = startRedmine(self, {})

        class Redirect(ScriptedRedmine):
            def handle_any(inner):
                inner.send_response(302)
                inner.send_header("Location", target.url + "/stolen")
                inner.end_headers()

            do_GET = handle_any

        source.RequestHandlerClass = Redirect
        with mock.patch("lib.interim_world.keychainSecret", return_value="bot-key"):
            with self.assertRaisesRegex(CheckFailed, "answered HTTP 302"):
                Redmine(source.url, "svc", "svc").get("/issues/1.json")
        self.assertEqual(target.requests, [])


# ---- the ADR is the spec

class AdrChecksTest(unittest.TestCase):
    ADR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "adr", "0005-redmine-is-the-tracker.md")

    def adrChecks(self):
        with open(self.ADR, encoding="utf8") as f:
            text = f.read()
        section = text[text.index('<a id="interim-end"></a>'):text.index('<a id="migration"></a>')]
        return [(int(n), title) for n, title in re.findall(r"(?m)^(\d+)\. \*\*(.+?)\*\*", section)]

    def test_the_suites_checks_are_the_adrs_checks_in_order(self):
        adr = self.adrChecks()
        self.assertEqual(len(adr), 12)
        self.assertEqual([(number, title) for number, title, _, _ in suite.CHECKS], adr)


if __name__ == "__main__":
    unittest.main()
