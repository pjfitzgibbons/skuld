# What the interim suite touches outside itself: Redmine's REST API, the Keychain, the fixture
# repo's git and gh. Every call that can fail raises CheckFailed with what it saw.
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from . import interim_probes as probes

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT_SECONDS = 15


class CheckFailed(Exception):
    pass


class PendingTrigger(CheckFailed):
    """A step whose trigger or API belongs to a ticket that has not built it yet."""


# ---- Keychain

def keychainSecret(service):
    """The generic password stored under `service`, read now and never kept (§3.3)."""
    done = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise CheckFailed(f"Keychain has no item for service {service!r}")
    return done.stdout.strip()


# ---- Redmine

class Redmine:
    def __init__(self, baseUrl, botKeyService, sessionService):
        self.baseUrl = baseUrl.rstrip("/")
        self.botKeyService = botKeyService
        self.sessionService = sessionService

    def get(self, path, **params):
        return self.json("GET", path, params=params)

    def json(self, method, path, params=None, body=None):
        headers = {"X-Redmine-API-Key": self.botKey(), "Content-Type": "application/json"}
        data = json.dumps(body).encode() if body is not None else None
        raw = self.send(method, self.url(path, params), headers, data)
        return json.loads(raw) if raw.strip() else {}

    def page(self, path):
        """An HTML page as Peter's signed-in user: his session cookie from the Keychain."""
        headers = {"Cookie": f"_redmine_session={keychainSecret(self.sessionService)}"}
        return self.send("GET", self.url(path), headers, None).decode("utf8", "replace")

    def plain(self, path):
        """A request that carries no user's credentials, such as /sys/fetch_changesets."""
        return self.send("GET", self.url(path), {}, None).decode("utf8", "replace")

    def botKey(self):
        try:
            return keychainSecret(self.botKeyService)
        except CheckFailed:
            self.requireReachable()
            raise

    def requireReachable(self):
        self.send("GET", self.url("/"), {}, None)

    def url(self, path, params=None):
        query = "?" + urllib.parse.urlencode(params) if params else ""
        return self.baseUrl + path + query

    def send(self, method, url, headers, data):
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            error.close()
            raise CheckFailed(f"{method} {urlWithoutQuery(url)} answered HTTP {error.code}")
        except (urllib.error.URLError, OSError) as error:
            raise CheckFailed(f"Redmine unreachable at {self.baseUrl}: {error}")


def urlWithoutQuery(url):
    return url.split("?", 1)[0]


# ---- git and gh in the fixture repo

class Fixture:
    def __init__(self, path):
        self.path = path

    def run(self, *command, cwd=None):
        done = subprocess.run(command, cwd=cwd or self.path, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise CheckFailed(f"`{' '.join(command)}` in {cwd or self.path}: {done.stderr.strip()}")
        return done.stdout

    def git(self, *args, cwd=None):
        return self.run("git", *args, cwd=cwd)

    def requireNotSkuldsRepo(self):
        if not os.path.isdir(self.path):
            raise CheckFailed(f"fixture repo {self.path} does not exist")
        if self.commonDir(self.path) == self.commonDir(REPO_ROOT):
            raise CheckFailed(f"fixture repo {self.path} is skuld's own repo; the suite never writes there")

    def commonDir(self, path):
        out = self.git("rev-parse", "--git-common-dir", cwd=path).strip()
        return os.path.realpath(os.path.join(path, out))

    def branches(self, number):
        return probes.branchNames(self.git("branch", "--list", f"*/SK-{number}-*"))

    def worktrees(self, number):
        porcelain = self.git("worktree", "list", "--porcelain")
        return probes.worktreesOnBranches(porcelain, self.branches(number))

    def worktreeList(self):
        return self.git("worktree", "list")

    def remove(self, number):
        """Remove the worktrees, then the branches, left for SK-<number>."""
        for path in self.worktrees(number):
            self.git("worktree", "remove", "--force", path)
        for branch in self.branches(number):
            self.git("branch", "-D", branch)

    def staged(self, worktree):
        return self.git("diff", "--cached", "--name-only", cwd=worktree).split()

    def porcelain(self, worktree):
        return self.git("status", "--porcelain", cwd=worktree)

    def head(self, worktree):
        return self.git("rev-parse", "HEAD", cwd=worktree).strip()

    def pushCommitToDefaultBranch(self, message):
        """Push an empty commit with `message` on top of the remote's default branch, without
        touching any checkout."""
        default = self.git("symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip().split("/", 1)[1]
        self.git("fetch", "origin", default)
        tip = f"origin/{default}"
        tree = self.git("rev-parse", f"{tip}^{{tree}}").strip()
        commit = self.git("commit-tree", tree, "-p", tip, "-m", message).strip()
        self.git("push", "origin", f"{commit}:refs/heads/{default}")

    def mergePullRequestOf(self, branch):
        found = json.loads(self.run("gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"))
        if not found:
            raise CheckFailed(f"fixture has no open PR for branch {branch}")
        self.run("gh", "pr", "merge", str(found[0]["number"]), "--squash")
