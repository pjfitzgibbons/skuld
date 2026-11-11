# `plan serve start|stop|restart|status [--port N]`: controls the local page server's LaunchAgent through
# launchctl (architecture §6.3, GUIDE §7). It renders nothing itself; every page is the server's
# (lib/server.py). Stdlib only: Flask lives in skuld's uv venv, which only the server runs from.
import json
import os
import plistlib
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from . import config as config_mod

LABEL = "dev.skuld.serve"
HOST = "127.0.0.1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_FILE = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
LOG_FILE = os.path.expanduser("~/Library/Logs/skuld-serve.log")
# launchd waits this long before starting the server again after it stops.
THROTTLE_SECONDS = 10
# What /health answers, so status can tell this server from anything else on the port.
HEALTH = {"app": "skuld", "status": "ok"}
ACTIONS = ("start", "stop", "restart", "status")
# The actions that take --port: the others act on the server where it runs.
PORT_ACTIONS = ("start", "restart")
PORT_FLAG = "--port"
USAGE = f"plan serve {'|'.join(ACTIONS)} [{PORT_FLAG} N]"
# The job's own pid line in `launchctl print`, one tab in; deeper lines belong to nested sections.
PID_LINE_RE = re.compile(r"^\tpid = (\d+)$", re.MULTILINE)


def serverUrl(port):
    return f"http://{HOST}:{port}/"


def portFrom(cfg):
    port = cfg.get("serverPort")
    if not isPort(port):
        raise RuntimeError(f'serverPort must be a port number, not {port!r}: fix it in {config_mod.CONFIG_FILE}')
    return port


def isPort(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536


# The port a --port argument names.
def portArg(text):
    port = int(text) if text and text.isdigit() else None
    if not isPort(port):
        raise RuntimeError(f"{PORT_FLAG} must be a port number, not {text!r}")
    return port


# The --port value in a list of arguments, or None when they name none.
def portIn(args):
    if PORT_FLAG not in args:
        return None
    at = args.index(PORT_FLAG)
    return portArg(args[at + 1] if at + 1 < len(args) else None)


# cfg with serverPort set to the port the agent runs the server on, when it names one: Start's and
# plan browse's links go where the server is, after `plan serve start --port N`.
def withServedPort(cfg, agent=None):
    port = (agent or LaunchAgent()).port()
    return {**cfg, "serverPort": port} if port else cfg


def venvPython(root=ROOT):
    return os.path.join(root, ".venv", "bin", "python")


# The agent runs the venv's python directly, not `uv run`, so it is one process. The port it runs the
# server on is in its arguments, which is where stop, status and the page links find it.
def agentPlist(root=ROOT, port=None):
    return {
        "Label": LABEL,
        "ProgramArguments": [venvPython(root), "-m", "lib.server", *portArgs(port)],
        "WorkingDirectory": root,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": LOG_FILE,
        "StandardErrorPath": LOG_FILE,
    }


# The server needs skuld's venv, which `uv sync` makes; without uv there is no way to make it.
def refuseMissingVenv(root=ROOT, which=None):
    if os.path.exists(venvPython(root)):
        return
    if (which or shutil.which)("uv") is None:
        raise RuntimeError(
            f"plan serve needs uv, which isn't on PATH: install it (brew install uv), then run `uv sync` in {root}"
        )
    raise RuntimeError(f"the server's venv is missing: run `uv sync` in {root}")


def portArgs(port):
    return [PORT_FLAG, str(port)] if port else []


def portInUseMessage(port, holder):
    return (
        f"port {port} is in use by {holder or 'another process'}; stop it, pass {PORT_FLAG} N, "
        f"or set serverPort in {config_mod.CONFIG_FILE}"
    )


# "<command> (pid <n>)" for whatever listens on port, or None when nothing does or lsof can't say.
def portHolder(port):
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"], capture_output=True, text=True
        ).stdout
    except OSError:
        return None
    fields = {line[0]: line[1:] for line in out.splitlines() if line}
    if "p" not in fields:
        return None
    return f"{fields.get('c', '?')} (pid {fields['p']})"


# True when this server answers /health on port.
def answers(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f"{serverUrl(port)}health", timeout=timeout) as response:
            return json.loads(response.read()).get("app") == HEALTH["app"]
    except (OSError, ValueError, AttributeError):
        return False


class LaunchAgent:
    """The server's job in the user's launchd domain, through launchctl."""

    def __init__(self, label=LABEL, plistFile=AGENT_FILE):
        self.label = label
        self.plistFile = plistFile
        self.domain = f"gui/{os.getuid()}"

    def loaded(self):
        return self._print().returncode == 0

    # The server's pid as launchd reports it, or None when the job isn't loaded or isn't running.
    def pid(self):
        found = PID_LINE_RE.search(self._print().stdout)
        return int(found.group(1)) if found else None

    def _print(self):
        return self._launchctl("print", f"{self.domain}/{self.label}", check=False)

    # The port the installed plist runs the server on, or None when there is no plist or it names none.
    def port(self):
        try:
            with open(self.plistFile, "rb") as f:
                plist = plistlib.load(f)
        except (OSError, plistlib.InvalidFileException):
            return None
        try:
            return portIn(plist.get("ProgramArguments", []))
        except RuntimeError:
            return None

    def install(self, plist):
        os.makedirs(os.path.dirname(self.plistFile), exist_ok=True)
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(self.plistFile, "wb") as f:
            plistlib.dump(plist, f)

    def load(self):
        self._launchctl("bootstrap", self.domain, self.plistFile)

    def unload(self):
        self._launchctl("bootout", f"{self.domain}/{self.label}")

    def restart(self):
        self._launchctl("kickstart", "-k", f"{self.domain}/{self.label}")

    def _launchctl(self, *args, check=True):
        result = subprocess.run(["launchctl", *args], capture_output=True, text=True)
        if check and result.returncode != 0:
            message = (result.stderr or result.stdout).strip() or f"exited with code {result.returncode}"
            raise RuntimeError(f"launchctl {' '.join(args)}: {message}")
        return result


class Serve:
    """plan serve's four actions over the LaunchAgent; the probes are injectable for tests."""

    # start runs the server on port, else serverPort; the other actions act on it where the agent
    # runs it, and a restart given a port moves it there.
    def __init__(self, cfg, agent=None, answers=answers, portHolder=portHolder, waitSeconds=5.0, root=ROOT, port=None):
        self.agent = agent or LaunchAgent()
        self.requestedPort = port
        self.startPort = port or portFrom(cfg)
        self.port = port or self.agent.port() or self.startPort
        self.answers = answers
        self.portHolder = portHolder
        self.waitSeconds = waitSeconds
        self.root = root

    def start(self):
        self.port = self.startPort
        refuseMissingVenv(self.root)
        if self.agent.loaded() and self.agent.port() in (self.port, None) and self.answers(self.port):
            return self.status()
        self.refusePortInUse()
        self.agent.install(agentPlist(self.root, self.port))
        if self.agent.loaded():
            self.agent.unload()  # launchd keeps the loaded plist; reload it to run this checkout
        self.agent.load()
        return self.statusAfterWaitingFor(up=True)

    def stop(self):
        if self.agent.loaded():
            self.agent.unload()
        return self.statusAfterWaitingFor(up=False)

    def restart(self):
        if not self.agent.loaded() or self.movesPort():
            return self.start()
        refuseMissingVenv(self.root)
        self.agent.restart()
        return self.statusAfterWaitingFor(up=True)

    def movesPort(self):
        return self.requestedPort is not None and self.requestedPort != self.agent.port()

    # The URL, up or down, and the agent's pid while launchd runs it, even one that doesn't answer.
    def status(self):
        line = f"{serverUrl(self.port)}  {'up' if self.answers(self.port) else 'down'}"
        pid = self.agent.pid()
        return f"{line}  pid {pid}" if pid else line

    def refusePortInUse(self):
        holder = self.portHolder(self.port)
        if holder:
            raise RuntimeError(portInUseMessage(self.port, holder))

    def statusAfterWaitingFor(self, up):
        deadline = time.monotonic() + self.waitSeconds
        while self.answers(self.port) != up and time.monotonic() < deadline:
            time.sleep(0.1)
        if up and not self.answers(self.port):
            raise RuntimeError(f"{serverUrl(self.port)}  down: the server didn't answer; see {LOG_FILE}")
        return self.status()


def control(cfg, action, port=None, serve=None):
    if action not in ACTIONS:
        raise RuntimeError(f"usage: {USAGE}")
    if port is not None and action not in PORT_ACTIONS:
        raise RuntimeError(f"plan serve {action} takes no {PORT_FLAG}: it acts on the server where it runs")
    serve = serve or Serve(cfg, port=port)
    return getattr(serve, action)()
