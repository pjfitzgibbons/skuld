# The local page server (architecture §6.3, GUIDE §7): Flask on 127.0.0.1 at the machine config's
# serverPort, rendering every page from a Jinja template in templates/. Its routes only read. This
# is the one module that imports Flask: the LaunchAgent runs it from skuld's uv venv as
# `python -m lib.server`, and `plan serve` (lib/serve.py) controls that agent. Each request and
# each error is logged to stderr, which the agent sends to serve.LOG_FILE.
import errno
import logging
import os
import socket
import sys
from dataclasses import dataclass

from flask import Flask, jsonify, redirect, render_template, request
from werkzeug.serving import make_server

from . import config as config_mod
from . import fileissues as fileissues_mod
from . import reviews as reviews_mod
from . import rounds as rounds_mod
from . import serve as serve_mod
from . import start as start_mod
from .jira import KEY_RE, Jira

TEMPLATES = os.path.join(serve_mod.ROOT, "templates")
IN_PROGRESS = "indeterminate"  # fileissues' status category for in-progress
# The Host header must name loopback, so a DNS-rebinding page can't read the server as its own origin.
TRUSTED_HOSTS = [serve_mod.HOST, "localhost"]
# What reading the store can raise: no store set, a file unreadable or gone, a malformed field.
STORE_ERRORS = (RuntimeError, OSError, ValueError)

log = logging.getLogger("skuld.server")


# Templates ending in .html autoescape, so issue data never renders as markup. issues finds what
# the round pages need about an issue; the tests pass one that reads a temp dir.
def createApp(cfg, issues=None):
    issues = issues or IssueLocator(cfg)
    app = Flask(__name__, template_folder=TEMPLATES)
    app.config["TRUSTED_HOSTS"] = TRUSTED_HOSTS
    app.add_template_filter(listed)

    @app.get("/health")
    def health():
        return jsonify(serve_mod.HEALTH)

    @app.get("/")
    def index():
        try:
            issues = issuesInProgress(cfg)
        except STORE_ERRORS as error:
            return render_template("index.html", issues=[], problem=str(error))
        return render_template("index.html", issues=issues, problem=None)

    @app.get("/issues/<key>/<any(report, review):page>")
    def latestRoundPage(key, page):
        return roundPage(issues, cfg, key, None, page)

    @app.get("/issues/<key>/rounds/<number>/<any(report, review):page>")
    def numberedRoundPage(key, number, page):
        return roundPage(issues, cfg, key, number, page)

    @app.errorhandler(404)
    def notFoundPage(error):
        return notFound(cfg, "No page is served at this address.")

    return app


# The page, a 302 to the nearest one (the browser caches a 301, and the latest round changes), or
# the 404 page. A store or config that can't be read shows on the 404 page, never as a 500.
def roundPage(issues, cfg, key, number, page):
    try:
        target = resolveRoundPage(issues, key, number, page)
        if isinstance(target, Redirect):
            return redirect(target.url, code=302)
        if isinstance(target, NotFound):
            return notFound(cfg, target.reason)
        return renderRound(issues, target, page)
    except STORE_ERRORS as error:
        return notFound(cfg, str(error))


@dataclass
class Redirect:
    url: str


@dataclass
class NotFound:
    reason: str


@dataclass
class Show:
    key: str
    number: int
    worktree: str
    roundsFolder: str
    rounds: list


# Where /issues/<key>[/rounds/<number>]/<page> goes. The key and round are matched against their
# patterns before either builds a path, so a URL can't name a folder outside the issue's rounds.
def resolveRoundPage(issues, key, number, page):
    if not KEY_RE.match(key):
        return NotFound(f'"{key}" isn\'t an issue key.')
    if number is not None and not rounds_mod.isRoundNumber(number):
        return NotFound(f'"{number}" isn\'t a round number.')
    canonical = fileissues_mod.canonicalKey(key)
    if canonical != key:
        return Redirect(rounds_mod.pagePath(canonical, number, page))
    if not issues.isKnown(key):
        return NotFound(f"{key} isn't an issue this Mac knows: no store ticket, and no Jira project in the config.")
    worktree = issues.worktree(key)
    folder = rounds_mod.roundsFolder(worktree, key) if worktree else None
    numbers = rounds_mod.roundNumbers(folder) if folder else []
    if not numbers:
        return Redirect(issues.issueUrl(key))
    if number is None or int(number) not in numbers:
        return Redirect(rounds_mod.pagePath(key, numbers[-1], page))
    return Show(key, int(number), worktree, folder, numbers)


def renderRound(issues, show, page):
    found = rounds_mod.readRound(os.path.join(show.roundsFolder, str(show.number)), show.number)
    return render_template(
        f"{page}.html",
        key=show.key,
        round=found,
        rounds=show.rounds,
        page=page,
        issueUrl=issues.issueUrl(show.key),
        prUrl=issues.prUrl(show.worktree),
    )


# The 404 page: what was asked for, why it isn't here, and the issues that are.
def notFound(cfg, reason):
    try:
        existing, problem = issuesInProgress(cfg), None
    except STORE_ERRORS as error:
        existing, problem = [], str(error)
    page = render_template("notfound.html", asked=request.path, reason=reason, issues=existing, problem=problem)
    return page, 404


# A list as "a, b"; anything else as itself. review.json's passes and verdicts may be either.
def listed(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return value


# What the round pages need about an issue, from the machine config: whether this Mac knows it, its
# page, its worktree and its pull request.
class IssueLocator:
    def __init__(self, cfg):
        self.cfg = cfg

    def isKnown(self, key):
        if not fileissues_mod.isFileBacked(key):
            return fileissues_mod.projectOf(key) in config_mod.jiraProjects(self.cfg)
        try:
            self.store().issue(key)
        except RuntimeError:
            return False
        return True

    # A store ticket's server page (SK-0034), or a Jira issue's browse URL.
    def issueUrl(self, key):
        if fileissues_mod.isFileBacked(key):
            return f"/issues/{key}"
        return Jira(config_mod.jiraSettingsFrom(self.cfg)).browseUrl(key)

    def worktree(self, key):
        return start_mod.issueWorktree(self.cfg, key)

    def prUrl(self, worktree):
        return reviews_mod.prUrl(worktree)

    def store(self):
        return fileissues_mod.FileIssues({"dir": config_mod.issueStoreFrom(self.cfg)})


# The store's tickets in progress, in work order. Jira's are left out: the server makes no Jira call.
def issuesInProgress(cfg):
    store = fileissues_mod.FileIssues({"dir": config_mod.issueStoreFrom(cfg)})
    return [row for row in store.list() if row["statusCategory"] == IN_PROGRESS]


# Bound here rather than by werkzeug, which answers a port in use with its own message and exit.
def listeningSocket(port):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((serve_mod.HOST, port))
        listener.listen(socket.SOMAXCONN)
    except OSError:
        listener.close()
        raise
    return listener


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    cfg = config_mod.load()
    port = serve_mod.portFrom(cfg)
    try:
        listener = listeningSocket(port)
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        log.error(serve_mod.portInUseMessage(port, serve_mod.portHolder(port)))
        return 1
    with listener:
        server = make_server(serve_mod.HOST, port, createApp(cfg), threaded=True, fd=listener.fileno())
    log.info("serving %s", serve_mod.serverUrl(port))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
