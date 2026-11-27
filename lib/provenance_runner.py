# Runs one named test and says what happened: "pass"; "fail", an exception inside a test that ran
# included; "unloaded", its file or its class's setup didn't load (a node test file that dies says
# no more than that); "missing"; or "error" when there is no verdict: skipped, crashed or out of
# time. A test is `path::Class.method` (Python unittest) or `path::name` (node --test). Run as a
# script, this file is the Python side of it.
import collections
import contextlib
import functools
import importlib
import os
import re
import signal
import subprocess
import sys
import threading
import unittest

Verdict = collections.namedtuple("Verdict", "state detail")
Verdict.__new__.__defaults__ = ("",)
MARK = "@@provenance"
TIMEOUT = 300
NODE_SUFFIXES = (".js", ".mjs", ".cjs")


class TestProcesses:
    """The tests under way. Each is a process group of its own, so that everything it starts goes
    when its run ends or the check stops: nothing left over can hold a port or a file."""

    def __init__(self):
        self.stopped = False
        self.leaders = set()
        self.lock = threading.Lock()

    @contextlib.contextmanager
    def group(self, leader):
        with self.lock:
            self.leaders.add(leader)
            if self.stopped:
                killGroup(leader)
        try:
            yield
        finally:
            with self.lock:
                self.leaders.discard(leader)
                killGroup(leader)

    def stop(self):
        """Kills the tests under way, and any started from now on."""
        with self.lock:
            self.stopped = True
            for leader in self.leaders:
                killGroup(leader)


def killGroup(leader):
    # macOS answers PermissionError, not ProcessLookupError, for a group whose processes have all exited.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(leader, signal.SIGKILL)


def runTest(root, test, processes=None):
    """The Verdict of one test, run from root, in a group that processes can stop."""
    path, _, name = test.partition("::")
    if not name or not os.path.isfile(os.path.join(root, path)):
        return Verdict("missing")
    if not path.endswith((".py", *NODE_SUFFIXES)):
        return Verdict("error", "only Python unittest and node --test tests can run")
    try:
        verdict = pythonVerdict if path.endswith(".py") else nodeVerdict
        return verdict(root, path, name, processes or TestProcesses())
    except OSError as error:
        return Verdict("error", f"the test can't start: {error}")


@functools.cache
def repositoryVariables():
    """The variables that point git at a repository, such as GIT_DIR and GIT_WORK_TREE."""
    return frozenset(subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True,
                                    check=True).stdout.split())


def ownEnvironment(**added):
    """The environment without those variables: git obeys them over the folder it runs in, so an
    inherited one would turn a command meant for a temporary checkout on the real repository."""
    return {**{name: value for name, value in os.environ.items() if name not in repositoryVariables()}, **added}


def run(root, command, processes):
    """The finished process, or None when it ran out of time. No bytecode is written: a cached .pyc
    could stand in for a file that was just reverted."""
    environment = ownEnvironment(PYTHONDONTWRITEBYTECODE="1")
    # A node --test that runs this check must not pass its own test context on.
    environment.pop("NODE_TEST_CONTEXT", None)
    with subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, errors="replace", start_new_session=True) as process:
        with processes.group(process.pid):  # killed on the way out, before Popen waits for the process
            try:
                output, errors = process.communicate(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                return None
    return subprocess.CompletedProcess(command, process.returncode, output, errors)


def pythonVerdict(root, path, name, processes):
    finished = run(root, [sys.executable, os.path.abspath(__file__), path, name], processes)
    if finished is None:
        return Verdict("error", f"ran past {TIMEOUT} seconds")
    said = [line for line in finished.stdout.split("\n") if line.startswith(MARK)]
    # The script exits 0 once it has said its verdict, so any other exit is a crash after it too.
    if not said or finished.returncode != 0:
        return Verdict("error", f"the test run crashed (exit {finished.returncode})")
    _mark, state, detail = (said[-1].split(" ", 2) + [""])[:3]
    return Verdict(state, detail)


def nodeVerdict(root, path, name, processes):
    command = ["node", "--test", "--test-reporter=tap", f"--test-name-pattern=^{re.escape(name)}$", path]
    finished = run(root, command, processes)
    if finished is None:
        return Verdict("error", f"ran past {TIMEOUT} seconds")
    return tapVerdict(finished.stdout, name, finished.returncode)


def tapVerdict(output, name, exitCode):
    written = name.replace("\\", "\\\\").replace("#", "\\#")
    result = re.compile(rf"^\s*(not )?ok \d+ - {re.escape(written)}( # (SKIP|TODO).*)?$", re.M)
    found = result.findall(output)
    if not found:
        return Verdict("missing") if exitCode == 0 else Verdict("unloaded", "the test file didn't load")
    if any(skipped for _failed, skipped, _directive in found):
        return Verdict("error", "the test was skipped")
    if any(failed for failed, _skipped, _directive in found):
        return Verdict("fail")
    if exitCode != 0:
        return Verdict("error", f"the test passed, and node then exited {exitCode}")
    return Verdict("pass")


# ---- the script: one unittest test, in its own process ----


def moduleOf(path):
    """(the folder to import from, the dotted module name) of a test file, its packages included."""
    folder, name = os.path.split(os.path.abspath(path))
    module = os.path.splitext(name)[0]
    while os.path.exists(os.path.join(folder, "__init__.py")):
        folder, package = os.path.split(folder)
        module = f"{package}.{module}"
    return folder, module


def unittestVerdict(path, name):
    folder, moduleName = moduleOf(path)
    sys.path[0:1] = [folder, os.getcwd()]
    try:
        module = importlib.import_module(moduleName)
    except unittest.SkipTest as skip:
        return Verdict("error", f"the test was skipped: {skip}")
    except BaseException as error:  # a module can raise anything as it loads
        return Verdict("unloaded", f"{path} didn't load: {type(error).__name__}: {error}")
    if not exists(module, name):
        return Verdict("missing")
    suite = unittest.defaultTestLoader.loadTestsFromName(name, module)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=0).run(suite)
    return resultVerdict(result)


def resultVerdict(result):
    if result.skipped:
        return Verdict("error", "the test was skipped")
    if result.expectedFailures or result.unexpectedSuccesses:
        return Verdict("error", "the test is an expectedFailure, whose result is the reverse of what it asserts")
    failed = result.failures + result.errors
    why = failed[0][1].strip().split("\n")[-1] if failed else "unexpected success"
    if result.testsRun == 0:  # a setUpClass or setUpModule that raised, or nothing to run
        return Verdict("unloaded", why) if failed else Verdict("error", "the test didn't run")
    return Verdict("pass") if result.wasSuccessful() else Verdict("fail", why)


def exists(module, name):
    found = module
    for part in name.split("."):
        if not hasattr(found, part):
            return False
        found = getattr(found, part)
    return True


if __name__ == "__main__":
    verdict = unittestVerdict(*sys.argv[1:3])
    print(f"\n{MARK} {verdict.state} {' '.join(verdict.detail.split())}", flush=True)
