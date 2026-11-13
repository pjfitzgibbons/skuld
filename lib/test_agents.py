# lib/agents.py: the placing and colouring rules on made-up sessions, with no call to claude.
# reportIsFresh runs against a real temp git repo; a tick runs through a patched readSessions and
# real temp issues and pulses files.
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from lib import agents, cli, pulses

LISTED = ["SK-0041", "CIAM-12"]


def session(cwd, status="idle", **more):
    return {"cwd": cwd, "status": status, "name": "s", **more}


def never(worktree, key):
    return False


def always(worktree, key):
    return True


def place(*sessions, reportIsFresh=never, listed=LISTED):
    return agents.placeSessions(list(sessions), listed, reportIsFresh)


class PlacingTests(unittest.TestCase):
    def test_a_worktree_of_a_listed_issue_places_the_session_on_it(self):
        [agent] = place(session("/p/skuld.worktrees/SK-0041"))
        self.assertEqual((agent.key, agent.issue, agent.worktree), ("SK-0041", "SK-0041", "/p/skuld.worktrees/SK-0041"))

    def test_a_cwd_below_the_worktree_is_placed_on_the_same_issue(self):
        [agent] = place(session("/p/repo.worktrees/CIAM-12/lib/sub"))
        self.assertEqual((agent.key, agent.issue, agent.worktree), ("CIAM-12", "CIAM-12", "/p/repo.worktrees/CIAM-12"))

    def test_a_key_the_issues_file_doesnt_list_shows_on_dash(self):
        [agent] = place(session("/p/repo.worktrees/SK-0099"))
        self.assertEqual((agent.key, agent.issue), ("SK-0099", "-"))

    def test_a_cwd_outside_every_worktree_has_no_key_and_no_issue(self):
        [agent] = place(session("/p/skuld"))
        self.assertEqual((agent.key, agent.issue), ("", "-"))

    def test_a_missing_issues_file_still_lists_every_session_on_dash(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            agents, "readSessions", return_value=[session("/p/a.worktrees/SK-0041"), session("/p/b")]
        ):
            found = agents.currentAgents(os.path.join(directory, "issues"), waitingDir=directory)
        self.assertEqual([a.issue for a in found], ["-", "-"])

    def test_a_background_session_reports_its_state(self):
        [agent] = place({"cwd": "/p/a.worktrees/SK-0041", "kind": "background", "state": "blocked"})
        self.assertEqual(agent.state, "blocked")

    def test_most_urgent_first(self):
        found = place(session("/a", "idle"), session("/b", "busy"), session("/c", "waiting"))
        self.assertEqual([a.state for a in found], ["waiting", "busy", "idle"])


class MarkedWaitingTests(unittest.TestCase):
    def test_a_session_its_hook_marked_is_waiting_whatever_claude_says(self):
        [agent] = agents.placeSessions([session("/p/a.worktrees/SK-0041", "busy", sessionId="s1")], LISTED, never, {"s1"})
        self.assertEqual(agent.state, "waiting")

    def test_an_unmarked_session_keeps_claudes_state(self):
        [agent] = agents.placeSessions([session("/p/a.worktrees/SK-0041", "busy", sessionId="s1")], LISTED, never, {"s2"})
        self.assertEqual(agent.state, "busy")

    def test_a_fresh_report_stays_done_though_the_idle_prompt_marked_it(self):
        [agent] = agents.placeSessions([session("/p/a.worktrees/SK-0041", "idle", sessionId="s1")], LISTED, always, {"s1"})
        self.assertEqual(agent.state, "done")

    def test_the_watch_loop_wakes_when_a_marker_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            def markerWritten(seconds):
                with open(os.path.join(directory, "s1"), "w"):
                    pass

            with mock.patch.object(agents.time, "sleep", side_effect=markerWritten) as sleep:
                agents.sleepUntilMarkersChange(60, directory)
            self.assertEqual(sleep.call_count, 1)


class DoneTests(unittest.TestCase):
    def test_an_idle_claude_with_a_fresh_report_is_done_for_an_sk_key(self):
        asked = []

        def fresh(worktree, key):
            asked.append((worktree, key))
            return True

        [agent] = place(session("/p/skuld.worktrees/SK-0041/lib"), reportIsFresh=fresh)
        self.assertEqual(agent.state, "done")
        self.assertEqual(asked, [("/p/skuld.worktrees/SK-0041", "SK-0041")])

    def test_an_idle_claude_without_a_fresh_report_stays_idle(self):
        [agent] = place(session("/p/skuld.worktrees/SK-0041"))
        self.assertEqual(agent.state, "idle")

    def test_a_busy_or_waiting_claude_is_never_done_and_costs_no_lookup(self):
        def refuse(worktree, key):
            raise AssertionError("asked about a report for a Claude that isn't idle")

        found = place(session("/p/a.worktrees/SK-0041", "busy"), session("/p/a.worktrees/SK-0041", "waiting"), reportIsFresh=refuse)
        self.assertEqual(sorted(a.state for a in found), ["busy", "waiting"])

    def test_a_claude_outside_a_worktree_is_never_done(self):
        [agent] = place(session("/p/skuld"), reportIsFresh=always)
        self.assertEqual(agent.state, "idle")


class ReportIsFreshTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.worktree = self.dir.name
        for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", self.worktree, *args], check=True)
        self.lastCommit = int(subprocess.check_output(["git", "-C", self.worktree, "log", "-1", "--format=%ct"], text=True))

    def tearDown(self):
        self.dir.cleanup()

    def writeReport(self, mtime):
        path = agents.reportPath(self.worktree, "SK-0041")
        os.makedirs(os.path.dirname(path))
        open(path, "w").close()
        os.utime(path, (mtime, mtime))

    def test_a_report_newer_than_the_last_commit_is_fresh(self):
        self.writeReport(self.lastCommit + 60)
        self.assertTrue(agents.reportIsFresh(self.worktree, "SK-0041"))

    def test_a_report_older_than_the_last_commit_is_not(self):
        self.writeReport(self.lastCommit - 60)
        self.assertFalse(agents.reportIsFresh(self.worktree, "SK-0041"))

    def test_no_report_is_not_fresh(self):
        self.assertFalse(agents.reportIsFresh(self.worktree, "SK-0041"))

    def test_a_worktree_that_is_gone_is_not_fresh(self):
        self.assertFalse(agents.reportIsFresh(os.path.join(self.worktree, "gone"), "SK-0041"))


class ColouringTests(unittest.TestCase):
    def test_waiting_is_orange_done_is_green_and_an_issue_with_no_claude_is_off(self):
        found = place(session("/p/a.worktrees/SK-0041", "waiting"))
        self.assertEqual(agents.wantedPulses(found, LISTED), {"SK-0041": "orange", "CIAM-12": "off"})
        found = place(session("/p/a.worktrees/CIAM-12"), reportIsFresh=always)
        self.assertEqual(agents.wantedPulses(found, LISTED), {"SK-0041": "off", "CIAM-12": "green"})

    def test_a_busy_or_idle_claude_is_off(self):
        found = place(session("/p/a.worktrees/SK-0041", "busy"), session("/p/a.worktrees/CIAM-12"))
        self.assertEqual(agents.wantedPulses(found, LISTED), {"SK-0041": "off", "CIAM-12": "off"})

    def test_the_most_urgent_claude_on_an_issue_wins(self):
        found = place(
            session("/p/a.worktrees/SK-0041", "busy"),
            session("/p/a.worktrees/SK-0041", "waiting"),
            session("/p/a.worktrees/SK-0041", "idle"),
            reportIsFresh=always,
        )
        self.assertEqual(agents.wantedPulses(found, LISTED)["SK-0041"], "orange")

    def test_an_unplaced_claude_pulses_nothing(self):
        found = place(session("/p/a.worktrees/SK-0099", "waiting"))
        self.assertEqual(agents.wantedPulses(found, LISTED), {"SK-0041": "off", "CIAM-12": "off"})

    def test_no_listed_issue_wants_no_pulse(self):
        found = place(session("/p/a.worktrees/SK-0041", "waiting"), listed=[])
        self.assertEqual(agents.wantedPulses(found, []), {})


class ReviewColouringTests(unittest.TestCase):
    def test_a_waiting_review_turns_its_issue_orange_over_done(self):
        found = place(session("/p/a.worktrees/CIAM-12"), reportIsFresh=always)
        self.assertEqual(agents.wantedPulses(found, LISTED, frozenset({"CIAM-12"})), {"SK-0041": "off", "CIAM-12": "orange"})

    def test_a_review_for_an_unlisted_key_pulses_nothing(self):
        self.assertEqual(agents.wantedPulses([], LISTED, frozenset({"SK-0099"})), {"SK-0041": "off", "CIAM-12": "off"})

    def test_only_listed_issues_with_a_claude_have_their_prs_checked(self):
        found = place(session("/p/a.worktrees/SK-0041/lib"), session("/p/a.worktrees/SK-0099"), session("/p/x"))
        self.assertEqual(agents.listedWorktrees(found), {"SK-0041": "/p/a.worktrees/SK-0041"})

    def test_reviews_are_read_from_each_listed_worktree(self):
        reviewed = {"/p/a.worktrees/SK-0041": {"state": "OPEN", "headRefOid": "b",
                    "reviews": [{"author": {"login": "copilot-pull-request-reviewer"}, "commit": {"oid": "b"}}]}}
        sessions = [session("/p/a.worktrees/SK-0041"), session("/p/a.worktrees/CIAM-12")]
        with mock.patch.object(agents, "currentAgents", return_value=place(*sessions)):
            keys = agents.checkReviews(frozenset(), readPr=reviewed.get)
        self.assertEqual(keys, frozenset({"SK-0041"}))

    def test_a_failing_claude_keeps_the_reviews_last_read(self):
        with mock.patch.object(agents, "currentAgents", side_effect=RuntimeError("claude: not found")):
            self.assertEqual(agents.checkReviews(frozenset({"SK-0041"})), frozenset({"SK-0041"}))

    def test_a_tick_reads_the_last_finished_check_while_the_next_runs_on_its_own(self):
        snapshot = agents.ReviewSnapshot()
        self.assertEqual(snapshot.keys, frozenset())
        with mock.patch.object(agents, "checkReviews", return_value=frozenset({"SK-0041"})):
            snapshot.refresh()
        self.assertEqual(snapshot.keys, frozenset({"SK-0041"}))

    def test_the_log_names_each_review_that_started_waiting(self):
        self.assertEqual(agents.reviewLine({"SK-0041", "CIAM-12"}), "Copilot review waiting: CIAM-12, SK-0041")


class PulseOnChangeTests(unittest.TestCase):
    def test_only_a_changed_issue_is_sent(self):
        sent = {"SK-0041": "orange", "CIAM-12": "off"}
        self.assertEqual(agents.pulsesToSend(sent, {"SK-0041": "orange", "CIAM-12": "off"}), {})
        self.assertEqual(agents.pulsesToSend(sent, {"SK-0041": "off", "CIAM-12": "off"}), {"SK-0041": "off"})

    def test_nothing_sent_yet_sends_every_issue(self):
        self.assertEqual(agents.pulsesToSend({}, {"SK-0041": "orange", "CIAM-12": "off"}), {"SK-0041": "orange", "CIAM-12": "off"})

    def test_an_issue_that_left_the_issues_file_is_cleared_once(self):
        self.assertEqual(agents.pulsesToSend({"SK-0041": "green", "CIAM-12": "off"}, {"CIAM-12": "off"}), {"SK-0041": "off"})
        self.assertEqual(agents.pulsesToSend({"SK-0041": "off", "CIAM-12": "off"}, {"CIAM-12": "off"}), {})

    def test_a_sent_pulse_is_recorded(self):
        calls = []
        record, failures = agents.sendPulses({"CIAM-12": "off"}, {"SK-0041": "orange"}, lambda k, c: calls.append((k, c)))
        self.assertEqual((record, failures, calls), ({"CIAM-12": "off", "SK-0041": "orange"}, [], [("SK-0041", "orange")]))

    def test_a_failed_write_marks_only_that_key_unsent(self):
        def unwritable(key, color):
            raise PermissionError("[Errno 13] Permission denied: '/c/pulses.tmp'")

        record, failures = agents.sendPulses({"CIAM-12": "green"}, {"SK-0041": "orange"}, unwritable)
        self.assertEqual(record, {"CIAM-12": "green", "SK-0041": agents.UNSENT})
        self.assertEqual(failures, ["[Errno 13] Permission denied: '/c/pulses.tmp'"])

    def test_an_unsent_key_is_sent_again_whether_wanted_or_gone(self):
        self.assertEqual(agents.pulsesToSend({"SK-0041": agents.UNSENT}, {"SK-0041": "orange"}), {"SK-0041": "orange"})
        self.assertEqual(agents.pulsesToSend({"SK-0041": agents.UNSENT}, {}), {"SK-0041": "off"})

    def test_the_log_leaves_out_an_unsent_key(self):
        self.assertEqual(agents.pulsesSent({}, {"SK-0041": agents.UNSENT, "CIAM-12": "green"}), {"CIAM-12": "green"})

    def test_the_log_names_only_the_pulses_a_tick_sent(self):
        sent = agents.pulsesSent({"SK-0041": "off", "CIAM-12": "off"}, {"SK-0041": "orange", "CIAM-12": "off"})
        self.assertEqual(agents.pulseLine(sent), "pulsed SK-0041 orange")
        self.assertEqual(agents.pulsesSent({"SK-0041": "orange"}, {"SK-0041": "orange"}), {})


class TickTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.issuesFile = os.path.join(self.dir.name, "issues")
        self.pulsesFile = os.path.join(self.dir.name, "pulses")
        self.waitingDir = os.path.join(self.dir.name, "waiting")
        self.writeIssues("SK-0041\nCIAM-12\n")
        self.sessions = [session("/p/a.worktrees/SK-0041", "waiting")]

    def writeIssues(self, text):
        with open(self.issuesFile, "w") as f:
            f.write(text)

    def colours(self):
        return pulses.readPulses(self.pulsesFile)

    def tick(self, sent):
        with mock.patch.object(agents, "readSessions", return_value=self.sessions):
            return agents.tick(sent, self.issuesFile, self.pulsesFile, waitingDir=self.waitingDir)

    def test_a_tick_writes_a_colour_only_when_an_issue_changes(self):
        sent, _ = self.tick({})
        self.assertEqual(self.colours(), {"SK-0041": "orange"})
        self.assertEqual(sent, {"SK-0041": "orange", "CIAM-12": "off"})
        with mock.patch.object(agents.pulses, "setPulse") as setPulse:
            sent, _ = self.tick(sent)
        setPulse.assert_not_called()
        self.sessions = [session("/p/a.worktrees/SK-0041", "busy")]
        self.tick(sent)
        self.assertEqual(self.colours(), {})

    def test_a_key_that_leaves_the_issues_file_loses_its_colour(self):
        sent, _ = self.tick({})
        self.writeIssues("CIAM-12\n")
        self.tick(sent)
        self.assertEqual(self.colours(), {})

    def test_a_missing_issues_file_writes_no_colour(self):
        os.remove(self.issuesFile)
        sent, failures = self.tick({})
        self.assertEqual((sent, failures), ({}, []))
        self.assertFalse(os.path.exists(self.pulsesFile))

    def test_a_half_written_issues_file_never_stops_a_tick(self):
        self.writeIssues("SK-0041\nCIAM-1")
        _, failures = self.tick({})
        self.assertEqual(failures, [])
        self.assertEqual(self.colours(), {"SK-0041": "orange"})

    def test_an_unwritable_pulses_file_never_stops_a_tick(self):
        with mock.patch.object(agents.pulses, "setPulse", side_effect=PermissionError("denied")):
            sent, failures = self.tick({})
        self.assertEqual(sent, {"SK-0041": agents.UNSENT, "CIAM-12": agents.UNSENT})
        self.assertEqual(failures, ["denied", "denied"])

    # Review finding 2: a failed write used to empty the record, so the next tick rewrote every
    # colour, bringing back one cleared in the Issues view.
    def test_after_one_keys_write_fails_the_next_tick_rewrites_only_that_key(self):
        self.writeIssues("SK-0041\nCIAM-12\n")
        self.sessions = [session("/p/a.worktrees/SK-0041", "waiting"), session("/p/a.worktrees/CIAM-12", "waiting")]
        sent, _ = self.tick({})
        self.assertEqual(self.colours(), {"SK-0041": "orange", "CIAM-12": "orange"})
        pulses.setPulse("SK-0041", "off", self.pulsesFile)  # cleared by a click in the Issues view
        self.sessions = [session("/p/a.worktrees/SK-0041", "waiting"), session("/p/a.worktrees/CIAM-12", "busy")]

        def failForCiam(key, color, pulsesFile):
            if key == "CIAM-12":
                raise PermissionError("denied")

        with mock.patch.object(agents.pulses, "setPulse", side_effect=failForCiam):
            sent, failures = self.tick(sent)
        self.assertEqual(failures, ["denied"])
        with mock.patch.object(agents.pulses, "setPulse") as setPulse:
            self.tick(sent)
        self.assertEqual([call.args[:2] for call in setPulse.call_args_list], [("CIAM-12", "off")])

    def test_empty_output_from_claude_is_a_failure_not_an_empty_list(self):
        with mock.patch.object(agents, "run", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "gave no JSON"):
                agents.readSessions()

    def test_a_failing_claude_keeps_what_was_sent(self):
        with mock.patch.object(agents, "readSessions", side_effect=RuntimeError("claude: not found")):
            sent, failures = agents.tick({"SK-0041": "orange"}, self.issuesFile, self.pulsesFile)
        self.assertEqual((sent, failures), ({"SK-0041": "orange"}, ["claude: not found"]))


class ShowingTests(unittest.TestCase):
    def setUp(self):
        self.found = place(
            session("/p/a.worktrees/SK-0041", "waiting", waitingFor="input needed"),
            session("/p/a.worktrees/CIAM-12", "busy"),
            session("/p/skuld"),
        )

    def test_status_and_table_show_the_same_states_on_the_same_issues(self):
        self.assertEqual(agents.statusLine(self.found), "CIAM-12:● SK-0041:⏳ -:○")
        table = agents.tableLines(self.found)
        self.assertTrue(table[0].startswith("waiting: input needed  SK-0041"))
        self.assertTrue(table[1].startswith("busy                   CIAM-12"))
        self.assertTrue(table[2].startswith("idle                   -          skuld"))

    def test_no_session_says_so(self):
        self.assertEqual(agents.statusLine([]), "no Claude")

    def test_a_title_lookup_that_fails_leaves_it_blank(self):
        def titleOf(key):
            if key == "CIAM-12":
                raise RuntimeError("CIAM-12 is unknown on this Mac")
            return "Issues view colours"

        titled = agents.withTitles(self.found, titleOf)
        self.assertEqual([a.title for a in titled], ["Issues view colours", "", ""])


class PlanAgentsTests(unittest.TestCase):
    def run_plan(self, *args, cfg=None):
        found = place(session("/p/a.worktrees/SK-0041", "waiting"), session("/p/b.worktrees/CIAM-12"))
        out = io.StringIO()
        with mock.patch.object(cli.config_mod, "load", return_value=cfg or {}), mock.patch.object(
            cli.agents_mod, "currentAgents", return_value=found
        ), mock.patch.object(cli, "issueTitle", side_effect=lambda cfg, key: f"title of {key}"), redirect_stdout(out):
            cli.main(["agents", *args])
        return out.getvalue()

    def test_json_lists_each_sessions_key_state_and_title_and_no_desktop(self):
        rows = json.loads(self.run_plan("--json"))
        picked = [{k: row[k] for k in ("key", "issue", "state", "title")} for row in rows]
        self.assertEqual(
            picked,
            [
                {"key": "SK-0041", "issue": "SK-0041", "state": "waiting", "title": "title of SK-0041"},
                {"key": "CIAM-12", "issue": "CIAM-12", "state": "idle", "title": "title of CIAM-12"},
            ],
        )
        self.assertFalse(any("desktop" in row for row in rows))

    def test_list_is_the_default(self):
        self.assertEqual(self.run_plan(), self.run_plan("list"))
        self.assertIn("title of SK-0041", self.run_plan())

    def test_status_is_one_line(self):
        self.assertEqual(self.run_plan("status"), "CIAM-12:○ SK-0041:⏳\n")

    def test_an_unknown_subcommand_shows_the_usage(self):
        with self.assertRaisesRegex(RuntimeError, "usage: plan agents"):
            self.run_plan("bogus")

    def test_a_jira_key_gets_no_title_when_the_config_names_no_jira(self):
        title = agents.quietly(lambda key: cli.issueTitle({}, key), "CIAM-12")
        self.assertEqual(title, "")


if __name__ == "__main__":
    unittest.main()
