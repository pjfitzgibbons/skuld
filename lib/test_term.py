# M3: tmux's `-t <name>` (no leading "=") prefix-matches, so `has-session -t CIAM-1` also matches
# a session named CIAM-12. term.py must target every tmux call with the exact-match "=name" form.
# term.py talks to tmux through start.py's run() (subprocess), so these tests patch
# `lib.term.run` directly (term.py did `from .start import run`, binding its own name at import
# time), and assert the exact argv each tool would see, never spawning a real process. The issues
# file and plan workspace are real temp files, since their lock and write-temp-then-rename logic
# is what's under test.
#
# See lib/term.py's module comment and docs/architecture.md §7.1.
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock

from lib import term


class TermTests(unittest.TestCase):
    def test_hasSession_targets_tmux_with_exact_match_not_bare_name(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                return ""
            raise RuntimeError(f"unexpected {command} {' '.join(args)}")

        with mock.patch.object(term, "run", side_effect=fake_run):
            found = term.hasSession("CIAM-1")
        self.assertTrue(found)
        self.assertEqual(calls, [["tmux", "has-session", "-t", "=CIAM-1"]])

    def test_hasSession_exact_match_target_not_satisfied_by_same_prefix_session(self):
        # Models real tmux: with the exact "=" form, has-session -t =CIAM-1 fails when only
        # CIAM-12 exists, where the old bare "-t CIAM-1" form would have wrongly matched it.
        def fake_run(command, args, cwd):
            if command == "tmux" and args[0] == "has-session":
                target = args[args.index("-t") + 1]
                if target == "=CIAM-12":
                    return ""
                raise RuntimeError("can't find session")  # tmux's real behavior for an exact miss
            raise RuntimeError(f"unexpected {command} {' '.join(args)}")

        with mock.patch.object(term, "run", side_effect=fake_run):
            self.assertFalse(term.hasSession("CIAM-1"))
            self.assertTrue(term.hasSession("CIAM-12"))

    # Ship and Go used to be separate terminal windows -- a "ship" window with nvim+lazygit for a
    # standalone review step, and a "go" window that ran `pr`. The review step is removed (DECIDED,
    # before Report.html exists) and shipWindow() now does what goWindow() used to: run `pr <key>`
    # visibly in a tmux window named "ship", no nvim/lazygit anywhere in its argv.
    def test_shipWindow_targets_new_select_window_calls_exactly(self):
        calls = []

        def fake_run(command, args, cwd):
            calls.append([command, *args])
            if command == "tmux" and args[0] == "has-session":
                raise RuntimeError("no session")
            if command == "tmux" and args[0] == "new-window":
                return "@7\n"
            return ""

        with mock.patch.object(term, "run", side_effect=fake_run):
            term.shipWindow("CIAM-1", "/tmp/ciam-1", "/usr/local/bin/pr")
        newWindow = next(c for c in calls if c[1] == "new-window")
        selectWindow = next(c for c in calls if c[1] == "select-window")
        self.assertEqual(newWindow, ["tmux", "new-window", "-P", "-F", "#{window_id}", "-t", "=CIAM-1", "-n", "ship", "-c", "/tmp/ciam-1", "/usr/local/bin/pr", "CIAM-1"])
        self.assertEqual(selectWindow, ["tmux", "select-window", "-t", "@7"])
        self.assertFalse(any("nvim" in c or "lazygit" in c for c in calls))
        self.assertFalse(any(c[1] == "split-window" for c in calls))

# The issues file: one <KEY> per line, in the order issues were added, each once.
class IssuesFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.file = os.path.join(self.dir.name, "plan", "issues")

    def write(self, text):
        os.makedirs(os.path.dirname(self.file), exist_ok=True)
        with open(self.file, "w", encoding="utf8") as f:
            f.write(text)

    def read(self):
        with open(self.file, encoding="utf8") as f:
            return f.read()

    def test_added_issues_round_trip_in_the_order_added(self):
        term.addIssue("SK-0041", self.file)
        term.addIssue("CIAM-12", self.file)
        self.assertEqual(term.issueKeys(self.file), ["SK-0041", "CIAM-12"])
        self.assertEqual(self.read(), "SK-0041\nCIAM-12\n")

    def test_adding_a_listed_issue_again_keeps_one_line_in_its_place(self):
        term.addIssue("SK-0041", self.file)
        term.addIssue("CIAM-12", self.file)
        term.addIssue("SK-0041", self.file)
        self.assertEqual(term.issueKeys(self.file), ["SK-0041", "CIAM-12"])

    def test_removing_drops_only_that_issue(self):
        self.write("SK-0041\nCIAM-12\nSK-0045\n")
        term.removeIssue("CIAM-12", self.file)
        self.assertEqual(self.read(), "SK-0041\nSK-0045\n")

    def test_removing_an_unlisted_issue_writes_nothing(self):
        self.write("SK-0041\n\nnot a key\n")
        before = os.stat(self.file).st_mtime_ns
        term.removeIssue("CIAM-12", self.file)
        self.assertEqual(self.read(), "SK-0041\n\nnot a key\n")
        self.assertEqual(os.stat(self.file).st_mtime_ns, before)

    def test_a_missing_file_is_empty_and_removing_from_it_creates_nothing(self):
        self.assertEqual(term.issueKeys(self.file), [])
        term.removeIssue("SK-0041", self.file)
        self.assertFalse(os.path.exists(self.file))

    def test_a_half_written_or_hand_edited_file_keeps_its_keys_and_skips_the_rest(self):
        self.write("SK-0041\n\n  CIAM-12  \n3=SK-0045\ngarbage\nSK-0041\nCIAM-1")
        self.assertEqual(term.issueKeys(self.file), ["SK-0041", "CIAM-12", "CIAM-1"])

    def test_the_next_write_drops_the_lines_that_arent_keys(self):
        self.write("SK-0041\ngarbage\n")
        term.addIssue("CIAM-12", self.file)
        self.assertEqual(self.read(), "SK-0041\nCIAM-12\n")

    def test_writing_leaves_no_temp_file_behind(self):
        term.addIssue("SK-0041", self.file)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.file))), ["issues", "issues.lock"])


# Review finding 1: two writers that read the same file before either wrote it lost one write, and
# shared one temp name. Each read-modify-write now holds the file's lock, and each write has a temp
# file of its own.
class SharedFileWriteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.issuesFile = os.path.join(self.dir.name, "issues")
        self.planWorkspace = os.path.join(self.dir.name, "plan.code-workspace")

    # Runs write in a thread while this test holds path's lock, and returns whether it had written
    # before the lock was released and after.
    def writeWhileLocked(self, path, write, written):
        thread = threading.Thread(target=write)
        with term.locked(path):
            thread.start()
            thread.join(0.3)
            before = written()
        thread.join(5)
        return before, written()

    def test_adding_an_issue_waits_for_the_lock(self):
        written = lambda: term.issueKeys(self.issuesFile) == ["SK-0041"]
        self.assertEqual(self.writeWhileLocked(self.issuesFile, lambda: term.addIssue("SK-0041", self.issuesFile), written), (False, True))

    def test_removing_an_issue_waits_for_the_lock(self):
        term.addIssue("SK-0041", self.issuesFile)
        written = lambda: term.issueKeys(self.issuesFile) == []
        self.assertEqual(self.writeWhileLocked(self.issuesFile, lambda: term.removeIssue("SK-0041", self.issuesFile), written), (False, True))

    def test_adding_and_removing_a_folder_wait_for_the_lock(self):
        folders = lambda: term.readPlanWorkspace(self.planWorkspace)["folders"]
        add = lambda: term.addFolders(["/w/SK-0041"], self.planWorkspace, SETTINGS)
        self.assertEqual(self.writeWhileLocked(self.planWorkspace, add, lambda: folders() != []), (False, True))
        remove = lambda: term.removeFolder("/w/SK-0041", self.planWorkspace, SETTINGS)
        self.assertEqual(self.writeWhileLocked(self.planWorkspace, remove, lambda: folders() == []), (False, True))

    def test_writers_at_once_lose_no_issue(self):
        keys = [f"SK-{n:04d}" for n in range(1, 21)]
        threads = [threading.Thread(target=term.addIssue, args=(key, self.issuesFile)) for key in keys]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sorted(term.issueKeys(self.issuesFile)), keys)

    def test_writers_at_once_lose_no_folder(self):
        folders = [f"/w/SK-{n:04d}" for n in range(1, 21)]
        threads = [threading.Thread(target=term.addFolders, args=([folder], self.planWorkspace, SETTINGS)) for folder in folders]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sorted(f["path"] for f in term.readPlanWorkspace(self.planWorkspace)["folders"]), folders)

    # The writer's read must happen inside the lock: another writer's change, made while the lock is
    # held, is then in what the writer reads once it gets the lock.
    def test_a_writer_reads_the_file_after_it_gets_the_lock(self):
        thread = threading.Thread(target=term.addIssue, args=("SK-0041", self.issuesFile))
        with term.locked(self.issuesFile):
            thread.start()
            thread.join(0.3)
            term.writeLines(self.issuesFile, ["CIAM-12"])
        thread.join(5)
        self.assertEqual(term.issueKeys(self.issuesFile), ["CIAM-12", "SK-0041"])

    def test_a_new_file_is_readable_by_others_as_the_umask_allows(self):
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)
        term.writeAtomically(self.issuesFile, "SK-0041\n")
        self.assertEqual(os.stat(self.issuesFile).st_mode & 0o777, 0o644)

    def test_a_rewritten_file_keeps_its_mode(self):
        for mode in (0o600, 0o664):
            with self.subTest(mode=oct(mode)):
                term.writeAtomically(self.issuesFile, "SK-0041\n")
                os.chmod(self.issuesFile, mode)
                term.writeAtomically(self.issuesFile, "CIAM-12\n")
                self.assertEqual(os.stat(self.issuesFile).st_mode & 0o777, mode)

    def test_each_write_has_a_temp_file_of_its_own_beside_the_file(self):
        temps = []
        replace = os.replace

        def recordingReplace(source, target):
            temps.append(source)
            replace(source, target)

        with mock.patch.object(term.os, "replace", side_effect=recordingReplace):
            term.writeAtomically(self.issuesFile, "SK-0041\n")
            term.writeAtomically(self.issuesFile, "CIAM-12\n")
        self.assertEqual(len(set(temps)), 2)
        for temp in temps:
            self.assertEqual(os.path.dirname(temp), self.dir.name)
            self.assertNotEqual(temp, f"{self.issuesFile}.tmp")

    def test_a_failed_write_leaves_the_file_and_no_temp_file(self):
        term.addIssue("SK-0041", self.issuesFile)
        with mock.patch.object(term.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                term.addIssue("CIAM-12", self.issuesFile)
        self.assertEqual(sorted(os.listdir(self.dir.name)), ["issues", "issues.lock"])
        self.assertEqual(term.issueKeys(self.issuesFile), ["SK-0041"])


# The plan window: `code <plan workspace>`, then the open link, through run(), and nothing else.
class PlanWindowTests(unittest.TestCase):
    def test_opening_runs_code_on_the_plan_workspace(self):
        calls = []
        with mock.patch.object(term, "run", side_effect=lambda c, a, cwd: calls.append([c, *a])):
            term.openPlanWindow("/w/plan.code-workspace")
        self.assertEqual(calls, [["code", "/w/plan.code-workspace"]])

    def test_a_failed_code_stops_naming_the_command_to_run_by_hand(self):
        with mock.patch.object(term, "run", side_effect=RuntimeError("code /w/plan.code-workspace: No such file or directory")):
            with self.assertRaisesRegex(RuntimeError, r"(?s)opening the plan window failed: .*Open it by hand: code /w/plan\.code-workspace"):
                term.openPlanWindow("/w/plan.code-workspace")

    def test_revealing_opens_the_issues_link(self):
        calls = []
        with mock.patch.object(term, "run", side_effect=lambda c, a, cwd: calls.append([c, *a])):
            term.revealIssue("SK-0041")
        self.assertEqual(calls, [["code", "--open-url", "vscode://skuld.plan-window/open?key%3DSK-0041"]])

    def test_a_failed_reveal_prints_one_line_to_stderr_and_returns(self):
        err = io.StringIO()
        with mock.patch.object(term, "run", side_effect=RuntimeError("code: not found")), mock.patch("sys.stderr", err):
            term.revealIssue("SK-0041")
        self.assertEqual(
            err.getvalue(),
            "couldn't open SK-0041 in the plan window: code: not found; click it in the Issues view\n",
        )

    def test_the_open_link_is_encoded_as_the_browse_link_is(self):
        self.assertEqual(term.openLink("CIAM-12"), "vscode://skuld.plan-window/open?key%3DCIAM-12")
        self.assertTrue(term.browseLink("https://x/", "CIAM-12").endswith("%26key%3DCIAM-12"))


SETTINGS = {"plan.issuesFile": "/c/issues", "plan.pulsesFile": "/c/pulses", "plan.workspaceDir": "/c/ws"}


class PlanWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "ws", "plan.code-workspace")

    def write(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            f.write(text)

    def folders(self):
        return term.readPlanWorkspace(self.path)["folders"]

    def test_the_default_sits_in_the_workspace_folder(self):
        self.assertEqual(term.planWorkspacePath("/cache/ws"), "/cache/ws/plan.code-workspace")

    def test_folders_are_added_in_order_once_each(self):
        term.addFolders(["/w/SK-0041"], self.path, SETTINGS)
        term.addFolders(["/w/CIAM-12"], self.path, SETTINGS)
        term.addFolders(["/w/SK-0041"], self.path, SETTINGS)
        self.assertEqual(self.folders(), [{"path": "/w/SK-0041"}, {"path": "/w/CIAM-12"}])

    def test_several_folders_are_added_in_one_write_each_once(self):
        term.addFolders(["/w/SK-0041", "/issues"], self.path, SETTINGS)
        term.addFolders(["/w/CIAM-12", "/issues"], self.path, SETTINGS)
        self.assertEqual(self.folders(), [{"path": "/w/SK-0041"}, {"path": "/issues"}, {"path": "/w/CIAM-12"}])

    def test_removing_drops_only_that_folder(self):
        term.addFolders(["/w/SK-0041"], self.path, SETTINGS)
        term.addFolders(["/w/CIAM-12"], self.path, SETTINGS)
        term.removeFolder("/w/SK-0041", self.path, SETTINGS)
        self.assertEqual(self.folders(), [{"path": "/w/CIAM-12"}])

    def test_a_folder_named_through_a_symlink_is_the_same_folder(self):
        real = os.path.join(self.dir.name, "real")
        os.makedirs(real)
        link = os.path.join(self.dir.name, "link")
        os.symlink(real, link)
        term.addFolders([link], self.path, SETTINGS)
        term.addFolders([real], self.path, SETTINGS)
        self.assertEqual(self.folders(), [{"path": link}])
        term.removeFolder(real, self.path, SETTINGS)
        self.assertEqual(self.folders(), [])

    def test_removing_an_unlisted_folder_writes_nothing(self):
        term.removeFolder("/w/SK-0041", self.path, SETTINGS)
        self.assertFalse(os.path.exists(self.path))

    def test_existing_settings_are_kept(self):
        self.write('{"folders": [{"path": "/w/A-1"}], "settings": {"window.title": "plan"}}')
        term.addFolders(["/w/SK-0041"], self.path, SETTINGS)
        with open(self.path) as f:
            self.assertEqual(
                json.load(f),
                {"folders": [{"path": "/w/A-1"}, {"path": "/w/SK-0041"}], "settings": {"window.title": "plan", **SETTINGS}},
            )

    def test_every_write_sets_the_plan_settings_over_stale_ones(self):
        self.write('{"folders": [], "settings": {"plan.issuesFile": "/old/issues", "editor.tabSize": 2}}')
        term.addFolders(["/w/SK-0041"], self.path, SETTINGS)
        self.assertEqual(term.readPlanWorkspace(self.path)["settings"], {"plan.issuesFile": "/c/issues", "editor.tabSize": 2, "plan.pulsesFile": "/c/pulses", "plan.workspaceDir": "/c/ws"})

    def test_the_plan_settings_are_the_expanded_paths(self):
        settings = term.planSettings(None, "/p/pulses", None)
        self.assertEqual(
            settings,
            {
                "plan.command": term.PLAN_COMMAND,
                "workbench.browser.openLocalhostLinks": True,
                "plan.issuesFile": os.path.expanduser("~/.cache/plan/issues"),
                "plan.pulsesFile": "/p/pulses",
                "plan.workspaceDir": os.path.expanduser("~/.cache/plan/code-workspaces"),
            },
        )
        self.assertEqual(term.planSettings("/i", "/p", "/ws")["plan.workspaceDir"], "/ws")

    def test_plan_command_is_this_checkouts_executable_bin_plan(self):
        command = term.planSettings(None, None, None)["plan.command"]
        self.assertTrue(os.path.isabs(command))
        self.assertTrue(os.access(command, os.X_OK))
        self.assertEqual(command, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "bin", "plan")))

    def test_the_plan_workspace_sends_clicked_localhost_links_to_its_integrated_browser(self):
        self.assertIs(term.planSettings(None, None, None)["workbench.browser.openLocalhostLinks"], True)

    def test_the_pulses_file_setting_is_resolved_here(self):
        self.assertEqual(term.planSettings(None, "~/p/pulses", None)["plan.pulsesFile"], os.path.expanduser("~/p/pulses"))
        self.assertEqual(term.planSettings(None, None, None)["plan.pulsesFile"], os.path.expanduser("~/.cache/plan/pulses"))

    def test_the_first_folder_is_added_first_and_the_rest_follow(self):
        term.addFolders(["/w/SK-0041"], self.path, SETTINGS, first="/issues")
        term.addFolders(["/w/CIAM-12"], self.path, SETTINGS, first="/issues")
        self.assertEqual(self.folders(), [{"path": "/issues"}, {"path": "/w/SK-0041"}, {"path": "/w/CIAM-12"}])

    def test_a_first_folder_listed_further_down_moves_to_the_front(self):
        self.write('{"folders": [{"path": "/w/SK-0041"}, {"path": "/w/CIAM-12"}, {"path": "/issues"}]}')
        term.addFolders(["/w/SK-0042"], self.path, SETTINGS, first="/issues")
        self.assertEqual(self.folders(), [{"path": "/issues"}, {"path": "/w/SK-0041"}, {"path": "/w/CIAM-12"}, {"path": "/w/SK-0042"}])

    def test_a_relative_folder_is_relative_to_the_workspace_file_not_the_cwd(self):
        worktree = os.path.join(os.path.dirname(self.path), "wt", "SK-0041")
        self.write('{"folders": [{"path": "wt/SK-0041"}, {"path": "/issues"}]}')
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(elsewhere)
        term.addFolders([worktree], self.path, SETTINGS)
        self.assertEqual(self.folders(), [{"path": "wt/SK-0041"}, {"path": "/issues"}])
        term.removeFolder(worktree, self.path, SETTINGS)
        self.assertEqual(self.folders(), [{"path": "/issues"}])

    def test_removing_reads_the_plan_workspace_once(self):
        self.write('{"folders": [{"path": "/w/SK-0041"}]}')
        with mock.patch.object(term, "readPlanWorkspace", wraps=term.readPlanWorkspace) as read:
            term.removeFolder("/w/SK-0041", self.path, SETTINGS)
        read.assert_called_once()

    def test_a_key_needs_two_letters_or_digits_in_its_project(self):
        self.assertEqual(term.uniqueKeys(["C-1", "CI-1", "C1-2"]), ["CI-1", "C1-2"])

    def test_a_missing_file_is_an_empty_workspace(self):
        self.assertEqual(term.readPlanWorkspace(self.path), {"folders": []})

    def test_a_garbage_file_is_an_empty_workspace_the_next_write_repairs(self):
        for garbage in ("{not json", "[1, 2]", '{"folders": 3}'):
            with self.subTest(garbage=garbage):
                self.write(garbage)
                self.assertEqual(self.folders(), [])
                term.addFolders(["/w/SK-0041"], self.path, SETTINGS)
                self.assertEqual(self.folders(), [{"path": "/w/SK-0041"}])

    def test_writing_leaves_no_temp_file_behind(self):
        term.addFolders(["/w/SK-0041"], self.path, SETTINGS)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["plan.code-workspace", "plan.code-workspace.lock"])


class IssueWorkspaceFileTests(unittest.TestCase):
    def test_the_file_sits_in_the_workspace_folder_named_for_the_issue(self):
        self.assertEqual(term.workspaceFilePath("SK-0030", "/cache/ws"), "/cache/ws/SK-0030.code-workspace")

    def test_removing_takes_the_file_and_the_legacy_one_beside_the_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspaceDir = os.path.join(tmp, "ws")
            worktree = os.path.join(tmp, "repo.worktrees", "SK-0030")
            current = term.workspaceFilePath("SK-0030", workspaceDir)
            legacy = term.legacyWorkspaceFilePath(worktree, "SK-0030")
            for path in (current, legacy):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").close()
            self.assertEqual(term.removeWorkspaceFiles("SK-0030", worktree, workspaceDir), [])
            self.assertFalse(os.path.exists(current) or os.path.exists(legacy))
            self.assertEqual(term.removeWorkspaceFiles("SK-0030", worktree, workspaceDir), [])

    def test_a_file_that_wont_delete_is_a_warning_not_an_error(self):
        with mock.patch.object(term.os, "remove", side_effect=PermissionError(13, "Permission denied")):
            warnings = term.removeWorkspaceFiles("SK-0030", None, "/cache/ws")
        self.assertEqual(len(warnings), 1)
        self.assertRegex(warnings[0], r"^Warning: couldn't delete /cache/ws/SK-0030.code-workspace \(Permission denied\)")

    def test_writing_makes_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = term.workspaceFilePath("SK-0030", os.path.join(tmp, "new"))
            term.writeWorkspaceFile(path, {"folders": []})
            self.assertTrue(os.path.exists(path))

    def test_a_store_issue_names_its_worktree_prompt_and_ticket(self):
        workspace = term.issueWorkspace("SK-0030", "/w/SK-0030", "Work on SK-0030.", "/issues/SK-0030-x.md")
        settings = workspace["settings"]
        self.assertEqual(settings["plan.key"], "SK-0030")
        self.assertEqual(settings["plan.worktree"], "/w/SK-0030")
        self.assertEqual(settings["plan.prompt"], "Work on SK-0030.")
        self.assertEqual(settings["plan.ticket"], "/issues/SK-0030-x.md")

    def test_the_record_holds_only_plan_settings_and_no_folders(self):
        workspace = term.issueWorkspace("SK-0030", "/w/SK-0030", "p", "/issues/SK-0030-x.md")
        self.assertEqual(list(workspace), ["settings"])
        self.assertTrue(all(name.startswith("plan.") for name in workspace["settings"]))

    def test_a_jira_issue_has_no_ticket_setting(self):
        workspace = term.issueWorkspace("CIAM-1", "/w/CIAM-1", "Work on Jira CIAM-1")
        self.assertNotIn("plan.ticket", workspace["settings"])

    def test_a_jira_issue_has_its_browse_url_as_plan_ticketUrl(self):
        url = "https://jira.example/browse/CIAM-1"
        settings = term.issueWorkspace("CIAM-1", "/w/CIAM-1", "p", None, url)["settings"]
        self.assertEqual(settings["plan.ticketUrl"], url)

    def test_a_store_issue_has_no_plan_ticketUrl(self):
        settings = term.issueWorkspace("SK-0030", "/w", "p", "/issues/SK-0030-x.md", "https://x/")["settings"]
        self.assertNotIn("plan.ticketUrl", settings)

    def test_the_written_file_reads_back_as_the_same_workspace(self):
        workspace = term.issueWorkspace("CIAM-1", "/w/CIAM-1", "p")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "CIAM-1.code-workspace")
            term.writeWorkspaceFile(path, workspace)
            with open(path, encoding="utf8") as f:
                self.assertEqual(json.load(f), workspace)
            self.assertEqual(os.listdir(tmp), ["CIAM-1.code-workspace"])



# showUrl()'s two `code` calls, built by browseCommands() and browseLink(), which are pure; run is
# patched so no `code` ever runs.
# The query plan-window's handler reads: VS Code percent-decodes a link's query once first.
def handlerQuery(link):
    return urllib.parse.unquote(urllib.parse.urlsplit(link).query)


class BrowseTests(unittest.TestCase):
    def test_the_link_names_plan_windows_browse_handler_and_the_key(self):
        self.assertEqual(
            term.browseLink("https://example.com/", "SK-0031"),
            "vscode://skuld.plan-window/browse?url%3Dhttps%253A%252F%252Fexample.com%252F%26key%3DSK-0031",
        )

    def test_with_no_key_the_link_carries_only_the_url(self):
        self.assertEqual(handlerQuery(term.browseLink("http://127.0.0.1/")), "url=http%3A%2F%2F127.0.0.1%2F")

    # The handler's side of this round trip is plan.test.js's "a page's own query, and a key inside
    # it, stay in the url", which reads this same query.
    def test_a_query_and_a_key_inside_the_url_stay_inside_it_after_vs_codes_decode(self):
        link = term.browseLink("https://example.com/?a=1&key=SK-10&b=2", "SK-9")
        self.assertEqual(handlerQuery(link), "url=https%3A%2F%2Fexample.com%2F%3Fa%3D1%26key%3DSK-10%26b%3D2&key=SK-9")

    # L1: quotes, spaces, & and ; stay inside the url parameter, and the link has no shell
    # metacharacter left in it; it is passed as one argv item, never through a shell.
    def test_a_url_with_quotes_and_spaces_is_percent_encoded_into_one_argument(self):
        url = """https://example.com/a b?q='x'&r="y";$(rm -rf ~)#frag"""
        commands = term.browseCommands(url, "SK-0031", "/ws/SK-0031.code-workspace")
        self.assertEqual(commands[0], ("code", ["/ws/SK-0031.code-workspace"]))
        command, args = commands[1]
        self.assertEqual((command, args[0], len(args)), ("code", "--open-url", 2))
        link = args[1]
        self.assertRegex(link, r"^vscode://skuld\.plan-window/browse\?[A-Za-z0-9%._~-]+$")
        query = urllib.parse.parse_qs(handlerQuery(link))
        self.assertEqual(query, {"url": [url], "key": ["SK-0031"]})

    def test_with_no_workspace_file_only_the_link_is_sent(self):
        self.assertEqual(term.browseCommands("https://x/", None, None), [("code", ["--open-url", term.browseLink("https://x/")])])

    def test_showUrl_prints_then_focuses_the_window_then_sends_the_link(self):
        calls = []
        with mock.patch.object(term, "run", side_effect=lambda c, a, cwd: calls.append((c, a))), mock.patch("builtins.print") as printed:
            term.showUrl("https://x/", "SK-0031", "/ws/SK-0031.code-workspace")
        printed.assert_called_once_with("https://x/")
        self.assertEqual(calls, term.browseCommands("https://x/", "SK-0031", "/ws/SK-0031.code-workspace"))

    # L3: a missing `code` or a failed call leaves the printed URL and raises nothing.
    def test_a_failing_call_still_prints_the_url(self):
        for failure in (RuntimeError("code: not found"), OSError(2, "No such file")):
            with self.subTest(failure=failure), mock.patch.object(term, "run", side_effect=failure), mock.patch(
                "builtins.print"
            ) as printed:
                term.showUrl("https://x/", "SK-0031", "/ws/SK-0031.code-workspace")
            printed.assert_called_once_with("https://x/")

    def test_a_url_that_isnt_http_is_printed_and_never_sent(self):
        with mock.patch.object(term, "run") as run, mock.patch("builtins.print") as printed:
            term.showUrl("/issues/SK-0031-x.md", "SK-0031")
        printed.assert_called_once_with("/issues/SK-0031-x.md")
        run.assert_not_called()



if __name__ == "__main__":
    unittest.main()
