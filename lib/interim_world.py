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
COMMAND_TIMEOUT_SECONDS = 120
PROJECT_PREFIX = "SK"


class CheckFailed(Exception):
    pass


class PendingTrigger(CheckFailed):
    """A step whose trigger or API belongs to a ticket that has not built it yet."""


# ---- Keychain

def keychainSecret(service):
    """The generic password stored under `service`, read now and never kept (§3.3)."""
    try:
        done = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, check=False, timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise CheckFailed(f"Keychain did not answer for service {service!r} in {TIMEOUT_SECONDS} seconds")
    if done.returncode != 0:
        raise CheckFailed(f"Keychain has no item for service {service!r}")
    return done.stdout.strip()


# ---- Redmine

class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the API key or session cookie to its target, so none is followed: the
    3xx answer surfaces as an HTTPError."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_RefuseRedirects)


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
            with OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
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
    def __init__(self, path, timeout=COMMAND_TIMEOUT_SECONDS):
        self.path = path
        self.timeout = timeout

    def run(self, *command, cwd=None):
        where = cwd or self.path
        try:
            done = subprocess.run(
                command, cwd=where, capture_output=True, text=True, check=False, timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            raise CheckFailed(f"`{' '.join(command)}` in {where} did not finish in {self.timeout} seconds")
        if done.returncode != 0:
            raise CheckFailed(f"`{' '.join(command)}` in {where}: {done.stderr.strip()}")
        return done.stdout

    def git(self, *args, cwd=None):
        return self.run("git", *args, cwd=cwd)

    def requireNotSkuldsRepo(self):
        """Refuse a fixture that is skuld's checkout, a worktree of it, or any clone whose origin is
        skuld's remote: the suite pushes to and merges into the fixture's origin."""
        if not os.path.isdir(self.path):
            raise CheckFailed(f"fixture repo {self.path} does not exist")
        if self.commonDir(self.path) == self.commonDir(REPO_ROOT):
            raise CheckFailed(f"fixture repo {self.path} is skuld's own repo; the suite never writes there")
        origin = self.originUrl(self.path)
        if not origin:
            raise CheckFailed(f"fixture repo {self.path} has no origin; the suite pushes to it")
        if self.isSkuldsRemote(origin):
            raise CheckFailed(f"fixture repo {self.path} is a clone of skuld's repo ({origin}); the suite never writes there")

    def isSkuldsRemote(self, url):
        skuldsOrigin = self.originUrl(REPO_ROOT)
        return bool(skuldsOrigin) and probes.remoteIdentity(url) == probes.remoteIdentity(skuldsOrigin)

    def originUrl(self, path):
        """The origin remote's URL, or "" when the repo has none."""
        return self.git("config", "--default", "", "--get", "remote.origin.url", cwd=path).strip()

    def commonDir(self, path):
        out = self.git("rev-parse", "--git-common-dir", cwd=path).strip()
        return os.path.realpath(os.path.join(path, out))

    def branches(self, number, prefix=PROJECT_PREFIX):
        """The local branches named `*/<prefix>-<number>-*`."""
        listing = self.git("branch", "--list", "--format=%(refname:short)", f"*/{prefix}-{number}-*")
        return probes.branchNames(listing)

    def worktrees(self, number, prefix=PROJECT_PREFIX):
        """The worktrees on the issue's branches or in its `<prefix>-<number>` folder."""
        porcelain = self.git("worktree", "list", "--porcelain")
        return probes.worktreesOf(porcelain, self.branches(number, prefix), f"{prefix}-{number}")

    def remoteBranches(self, number):
        """The branches on origin named `*/SK-<number>-*`."""
        listing = self.git("ls-remote", "--heads", "origin", f"*/{PROJECT_PREFIX}-{number}-*")
        return [line.split("\t")[1].removeprefix("refs/heads/") for line in listing.splitlines() if "\t" in line]

    def remove(self, number):
        """Remove what a run leaves for SK-<number>: its worktrees, its local and remote branches, and
        the open PRs on the remote ones."""
        for path in self.worktrees(number):
            self.git("worktree", "remove", "--force", path)
        for branch in self.branches(number):
            self.git("branch", "-D", branch)
        for branch in self.remoteBranches(number):
            self.closeOpenPullRequests(branch)
            self.git("push", "origin", "--delete", branch)

    def staged(self, worktree):
        return self.git("diff", "--cached", "--name-only", cwd=worktree).split()

    def porcelain(self, worktree):
        return self.git("status", "--porcelain", cwd=worktree)

    def head(self, worktree):
        return self.git("rev-parse", "HEAD", cwd=worktree).strip()

    def headMessage(self, worktree):
        return self.git("log", "-1", "--format=%B", cwd=worktree).strip()

    def committedFiles(self, worktree):
        return self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD", cwd=worktree).split()

    def remoteHead(self, branch):
        """The commit origin holds for `branch`, or "" when it has none."""
        listing = self.git("ls-remote", "origin", f"refs/heads/{branch}")
        return listing.split("\t")[0] if listing.strip() else ""

    def pushCommitToDefaultBranch(self, message):
        """Push an empty commit with `message` on top of the remote's default branch, without
        touching any checkout."""
        default = self.git("symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip().split("/", 1)[1]
        self.git("fetch", "origin", default)
        tip = f"origin/{default}"
        tree = self.git("rev-parse", f"{tip}^{{tree}}").strip()
        commit = self.git("commit-tree", tree, "-p", tip, "-m", message).strip()
        self.git("push", "origin", f"{commit}:refs/heads/{default}")

    def openPullRequests(self, branch):
        """[{"number", "body"}] of the open PRs whose head is `branch`."""
        listing = self.run("gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number,body")
        return json.loads(listing)

    def closeOpenPullRequests(self, branch):
        for pullRequest in self.openPullRequests(branch):
            self.run("gh", "pr", "close", str(pullRequest["number"]))

    def mergePullRequestOf(self, branch):
        found = self.openPullRequests(branch)
        if not found:
            raise CheckFailed(f"fixture has no open PR for branch {branch}")
        self.run("gh", "pr", "merge", str(found[0]["number"]), "--squash")
