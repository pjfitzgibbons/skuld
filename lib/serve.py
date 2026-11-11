# `plan serve start|stop|restart|status`: controls the local page server's LaunchAgent through
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
# The job's own pid line in `launchctl print`, one tab in; deeper lines belong to nested sections.
PID_LINE_RE = re.compile(r"^\tpid = (\d+)$", re.MULTILINE)


def serverUrl(port):
    return f"http://{HOST}:{port}/"


def portFrom(cfg):
    port = cfg.get("serverPort")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise RuntimeError(f'serverPort must be a port number, not {port!r}: fix it in {config_mod.CONFIG_FILE}')
    return port


def venvPython(root=ROOT):
    return os.path.join(root, ".venv", "bin", "python")


# launchd's PATH lacks Homebrew, so the agent's PATH leads with the directory of the gh found here,
# which the round pages ask for each issue's PR, as bin/install-agents does for its agent.
def agentPath(which=None):
    gh = (which or shutil.which)("gh")
    return ":".join(([os.path.dirname(gh)] if gh else []) + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"])


# The agent runs the venv's python directly, not `uv run`, so it is one process.
def agentPlist(root=ROOT, which=None):
    return {
        "Label": LABEL,
        "ProgramArguments": [venvPython(root), "-m", "lib.server"],
        "WorkingDirectory": root,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PATH": agentPath(which)},
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


def portInUseMessage(port, holder):
    return f"port {port} is in use by {holder or 'another process'}; stop it, or set serverPort in {config_mod.CONFIG_FILE}"


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

    def __init__(self, cfg, agent=None, answers=answers, portHolder=portHolder, waitSeconds=5.0, root=ROOT):
        self.port = portFrom(cfg)
        self.agent = agent or LaunchAgent()
        self.answers = answers
        self.portHolder = portHolder
        self.waitSeconds = waitSeconds
        self.root = root

    def start(self):
        refuseMissingVenv(self.root)
        if self.agent.loaded() and self.answers(self.port):
            return self.status()
        self.refusePortInUse()
        self.agent.install(agentPlist(self.root))
        if self.agent.loaded():
            self.agent.unload()  # launchd keeps the loaded plist; reload it to run this checkout
        self.agent.load()
        return self.statusAfterWaitingFor(up=True)

    def stop(self):
        if self.agent.loaded():
            self.agent.unload()
        return self.statusAfterWaitingFor(up=False)

    def restart(self):
        if not self.agent.loaded():
            return self.start()
        refuseMissingVenv(self.root)
        self.agent.restart()
        return self.statusAfterWaitingFor(up=True)

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


def control(cfg, action, serve=None):
    if action not in ACTIONS:
        raise RuntimeError(f"usage: plan serve {'|'.join(ACTIONS)}")
    serve = serve or Serve(cfg)
    return getattr(serve, action)()
