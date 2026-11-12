# lib/serve.py: plan serve's actions over a fake LaunchAgent and injected probes, so no test runs
# launchctl, binds a port or needs the venv's Flask.
import os
import tempfile
import unittest
from unittest import mock

from lib import cli, serve


class FakeAgent:
    def __init__(self, loaded=False, server=None):
        self.isLoaded = loaded
        self.server = server  # the FakeServer the agent runs, which answers while it runs
        self.calls = []
        self.plist = None

    def loaded(self):
        return self.isLoaded

    def pid(self):
        return 4242 if self.isLoaded and self.server.up else None

    def install(self, plist):
        self.calls.append("install")
        self.plist = plist

    def load(self):
        self.calls.append("load")
        self.isLoaded = True
        self.server.up = True

    def unload(self):
        self.calls.append("unload")
        self.isLoaded = False
        self.server.up = False

    def restart(self):
        self.calls.append("restart")
        self.server.up = True

    def port(self):
        return serve.portIn(self.plist["ProgramArguments"]) if self.plist else None


class FakeServer:
    def __init__(self, up=False):
        self.up = up

    def answers(self, port):
        return self.up


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name
        os.makedirs(os.path.join(self.root, ".venv", "bin"))
        open(serve.venvPython(self.root), "w").close()

    def tearDown(self):
        self.dir.cleanup()

    def serve(self, loaded=False, up=False, holder=None, root=None, port=None, agentPort=None):
        self.server = FakeServer(up)
        self.agent = FakeAgent(loaded, self.server)
        if agentPort:
            self.agent.plist = serve.agentPlist(self.root, agentPort)
        return serve.Serve(
            {"serverPort": 8765},
            agent=self.agent,
            answers=self.server.answers,
            portHolder=lambda port: holder,
            waitSeconds=0,
            root=root or self.root,
            port=port,
        )

    def test_start_installs_and_loads_the_agent_and_prints_the_url_up(self):
        out = self.serve().start()
        self.assertEqual(self.agent.calls, ["install", "load"])
        self.assertEqual(out, "http://127.0.0.1:8765/  up  pid 4242")

    def test_start_with_a_port_runs_the_server_on_it(self):
        out = self.serve(port=8799).start()
        self.assertEqual(self.agent.port(), 8799)
        self.assertEqual(out, "http://127.0.0.1:8799/  up  pid 4242")

    def test_start_without_a_port_moves_a_server_on_another_port_back_to_serverPort(self):
        out = self.serve(loaded=True, up=True, agentPort=8799).start()
        self.assertEqual(self.agent.calls, ["install", "unload", "load"])
        self.assertEqual(out, "http://127.0.0.1:8765/  up  pid 4242")

    def test_stop_and_status_act_on_the_port_the_agent_runs_the_server_on(self):
        self.assertEqual(self.serve(loaded=True, up=True, agentPort=8799).status(), "http://127.0.0.1:8799/  up  pid 4242")
        self.assertEqual(self.serve(loaded=True, up=True, agentPort=8799).stop(), "http://127.0.0.1:8799/  down")

    def test_restart_keeps_the_port_the_agent_runs_the_server_on(self):
        out = self.serve(loaded=True, up=True, agentPort=8799).restart()
        self.assertEqual(self.agent.calls, ["restart"])
        self.assertEqual(out, "http://127.0.0.1:8799/  up  pid 4242")

    def test_restart_with_another_port_moves_the_server_there(self):
        out = self.serve(loaded=True, up=True, agentPort=8799, port=8800).restart()
        self.assertEqual(self.agent.calls, ["install", "unload", "load"])
        self.assertEqual(out, "http://127.0.0.1:8800/  up  pid 4242")

    def test_start_on_a_running_server_changes_nothing(self):
        out = self.serve(loaded=True, up=True).start()
        self.assertEqual(self.agent.calls, [])
        self.assertEqual(out, "http://127.0.0.1:8765/  up  pid 4242")

    def test_start_with_the_agent_loaded_but_down_reloads_the_new_plist(self):
        self.serve(loaded=True).start()
        self.assertEqual(self.agent.calls, ["install", "unload", "load"])

    def test_start_refuses_a_port_in_use_naming_the_port_and_the_process(self):
        with self.assertRaisesRegex(RuntimeError, r"port 8765 is in use by nginx \(pid 42\)"):
            self.serve(holder="nginx (pid 42)").start()
        self.assertEqual(self.agent.calls, [])

    def test_start_says_where_the_log_is_when_the_server_never_answers(self):
        s = self.serve()
        self.agent.load = lambda: None  # the server crashes at once
        with self.assertRaisesRegex(RuntimeError, r"down: the server didn't answer; see .*skuld-serve.log"):
            s.start()

    def test_stop_unloads_the_agent_and_prints_down(self):
        out = self.serve(loaded=True, up=True).stop()
        self.assertEqual(self.agent.calls, ["unload"])
        self.assertEqual(out, "http://127.0.0.1:8765/  down")

    def test_stop_with_nothing_loaded_is_down(self):
        out = self.serve().stop()
        self.assertEqual(self.agent.calls, [])
        self.assertTrue(out.endswith("down"))

    def test_restart_kicks_a_loaded_agent(self):
        out = self.serve(loaded=True, up=True).restart()
        self.assertEqual(self.agent.calls, ["restart"])
        self.assertEqual(out, "http://127.0.0.1:8765/  up  pid 4242")

    def test_restart_without_the_agent_starts_it(self):
        self.serve().restart()
        self.assertEqual(self.agent.calls, ["install", "load"])

    def test_restart_of_an_unloaded_agent_keeps_the_port_its_plist_names(self):
        out = self.serve(agentPort=8799).restart()
        self.assertEqual(self.agent.port(), 8799)
        self.assertEqual(out, "http://127.0.0.1:8799/  up  pid 4242")

    def test_status_says_whether_it_answers(self):
        self.assertEqual(self.serve(loaded=True, up=True).status(), "http://127.0.0.1:8765/  up  pid 4242")
        self.assertEqual(self.serve().status(), "http://127.0.0.1:8765/  down")

    def test_status_names_the_pid_of_a_server_that_doesnt_answer(self):
        s = self.serve(loaded=True)
        self.agent.pid = lambda: 4242
        self.assertEqual(s.status(), "http://127.0.0.1:8765/  down  pid 4242")

    def test_control_refuses_an_unknown_action_with_usage(self):
        with self.assertRaisesRegex(RuntimeError, r"usage: plan serve start\|stop\|restart\|status"):
            serve.control({"serverPort": 8765}, "reload")

    def test_control_refuses_a_port_for_stop_and_status(self):
        for action in ("stop", "status"):
            with self.assertRaisesRegex(RuntimeError, f"plan serve {action} takes no --port"):
                serve.control({"serverPort": 8765}, action, 8799)


class VenvTests(unittest.TestCase):
    def test_without_uv_the_error_names_uv(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError, r"needs uv, which isn't on PATH.*uv sync"):
                serve.refuseMissingVenv(root, which=lambda name: None)

    def test_with_uv_but_no_venv_the_error_says_uv_sync(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError, r"venv is missing: run `uv sync` in"):
                serve.refuseMissingVenv(root, which=lambda name: "/opt/uv")

    def test_start_without_uv_refuses_before_touching_launchd(self):
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as root, mock.patch.object(serve.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "needs uv"):
                serve.Serve({"serverPort": 8765}, agent=agent, root=root).start()
        self.assertEqual(agent.calls, [])


class AgentPlistTests(unittest.TestCase):
    def test_runs_one_venv_python_process_on_the_server_module(self):
        plist = serve.agentPlist("/opt/skuld")
        self.assertEqual(plist["ProgramArguments"], ["/opt/skuld/.venv/bin/python", "-m", "lib.server"])
        self.assertEqual(plist["WorkingDirectory"], "/opt/skuld")

    def test_passes_the_port_to_the_server(self):
        plist = serve.agentPlist("/opt/skuld", 8799)
        self.assertEqual(plist["ProgramArguments"][-2:], ["--port", "8799"])
        self.assertEqual(serve.portIn(plist["ProgramArguments"]), 8799)

    def test_starts_at_login_and_restarts_after_it_stops(self):
        plist = serve.agentPlist("/opt/skuld")
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["ThrottleInterval"], serve.THROTTLE_SECONDS)

    def test_logs_to_the_log_file(self):
        plist = serve.agentPlist("/opt/skuld")
        self.assertEqual(plist["StandardOutPath"], serve.LOG_FILE)
        self.assertEqual(plist["StandardErrorPath"], serve.LOG_FILE)


class LaunchAgentTests(unittest.TestCase):
    PRINTED = "gui/501/dev.skuld.serve = {\n\tstate = running\n\tpid = 4242\n\tendpoints = {\n\t\tpid = 7\n\t}\n}\n"

    def agent_printing(self, stdout, returncode=0):
        result = mock.Mock(stdout=stdout, stderr="", returncode=returncode)
        patcher = mock.patch.object(serve.subprocess, "run", return_value=result)
        patcher.start()
        self.addCleanup(patcher.stop)
        return serve.LaunchAgent()

    def test_pid_is_the_jobs_own_pid_line(self):
        self.assertEqual(self.agent_printing(self.PRINTED).pid(), 4242)

    def test_pid_is_none_when_the_job_isnt_running(self):
        self.assertIsNone(self.agent_printing("gui/501/dev.skuld.serve = {\n\tstate = not running\n}\n").pid())

    def test_pid_is_none_when_the_job_isnt_loaded(self):
        self.assertIsNone(self.agent_printing("", returncode=113).pid())


class PortTests(unittest.TestCase):
    def test_the_server_listens_on_loopback_only(self):
        self.assertEqual(serve.HOST, "127.0.0.1")

    def test_refuses_a_port_that_isnt_a_port_number(self):
        for bad in (None, "8765", 0, 70000, True):
            with self.assertRaisesRegex(RuntimeError, "serverPort must be a port number"):
                serve.portFrom({"serverPort": bad})

    def test_port_holder_reads_lsof(self):
        result = mock.Mock(stdout="p4242\ncnginx\n")
        with mock.patch.object(serve.subprocess, "run", return_value=result):
            self.assertEqual(serve.portHolder(8765), "nginx (pid 4242)")

    def test_port_holder_is_none_when_nothing_listens(self):
        with mock.patch.object(serve.subprocess, "run", return_value=mock.Mock(stdout="")):
            self.assertIsNone(serve.portHolder(8765))


class ServedPortTests(unittest.TestCase):
    def agentOn(self, port):
        agent = FakeAgent()
        agent.plist = serve.agentPlist("/opt/skuld", port) if port else None
        return agent

    def test_links_follow_the_port_the_agent_runs_the_server_on(self):
        self.assertEqual(serve.withServedPort({"serverPort": 8765}, self.agentOn(8799)), {"serverPort": 8799})

    def test_without_an_agent_port_the_config_is_unchanged(self):
        self.assertEqual(serve.withServedPort({"serverPort": 8765}, self.agentOn(None)), {"serverPort": 8765})

    def test_launch_agent_port_is_none_without_a_plist(self):
        agent = serve.LaunchAgent()
        agent.plistFile = "/nonexistent/dev.skuld.serve.plist"
        self.assertIsNone(agent.port())


class CliTests(unittest.TestCase):
    def test_plan_serve_prints_the_actions_result(self):
        with mock.patch.object(cli.config_mod, "load", return_value={"serverPort": 8765}), \
                mock.patch.object(serve, "control", return_value="http://127.0.0.1:8765/  up  pid 4242") as control, \
                mock.patch("builtins.print") as printed:
            cli.main(["serve", "status"])
        control.assert_called_once_with({"serverPort": 8765}, "status", None)
        printed.assert_called_once_with("http://127.0.0.1:8765/  up  pid 4242")


if __name__ == "__main__":
    unittest.main()
