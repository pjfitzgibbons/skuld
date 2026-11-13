# The local page server (architecture §6.3, GUIDE §7): Flask on 127.0.0.1 at the machine config's
# serverPort, rendering every page from a Jinja template in templates/. Its routes only read, but
# one: POST /issues/<KEY>/open opens the ticket's file in VS Code. This is the one module that
# imports Flask and markdown-it-py: the LaunchAgent runs it from skuld's uv venv as
# `python -m lib.server`, and `plan serve` (lib/serve.py) controls that agent. Each request and
# each error is logged to stderr, which the agent sends to serve.LOG_FILE.
import errno
import logging
import os
import re
import socket
import subprocess
import sys

from flask import Flask, abort, jsonify, redirect, render_template, request
from markdown_it import MarkdownIt
from markupsafe import Markup
from mdit_py_plugins.tasklists import tasklists_plugin
from werkzeug.serving import make_server

from . import config as config_mod
from . import issuestore as issuestore_mod
from . import serve as serve_mod
from . import term as term_mod
from .issue import IssueNotFound

TEMPLATES = os.path.join(serve_mod.ROOT, "templates")
# The Host header must name loopback, so a DNS-rebinding page can't read the server as its own origin.
TRUSTED_HOSTS = [serve_mod.HOST, "localhost"]
# What reading the store can raise: no store set, a file unreadable or gone, a malformed field.
STORE_ERRORS = (RuntimeError, OSError, ValueError)
CODE_SECONDS = 10
# CommonMark with GitHub's tables, strikethrough and task lists. html off: raw HTML in a ticket is
# escaped as text, and markdown-it refuses javascript: and data: links, so nothing a ticket says
# becomes markup the renderer didn't write.
# A ticket's file name in the store, SK-0034-some-slug.md or SK-0034.md, and the key it starts with.
TICKET_FILE_RE = re.compile(r"^([A-Z][A-Z0-9]*-\d+)(?:-[^/]*)?\.md$")
INDEX_FILE = "INDEX.md"

MARKDOWN = (
    MarkdownIt("commonmark", {"html": False})
    .enable(["table", "strikethrough"])
    .use(tasklists_plugin)
)

log = logging.getLogger("skuld.server")


# Templates ending in .html autoescape, so issue data never renders as markup.
def createApp(cfg):
    app = Flask(__name__, template_folder=TEMPLATES)
    app.config["TRUSTED_HOSTS"] = TRUSTED_HOSTS

    @app.get("/health")
    def health():
        return jsonify(serve_mod.HEALTH)

    @app.get("/")
    def index():
        try:
            nextUp = indexHtml(cfg)
        except STORE_ERRORS as error:
            return render_template("index.html", nextUp=None, problem=str(error))
        return render_template("index.html", nextUp=nextUp, problem=None)

    @app.get("/issues/<key>")
    def issue(key):
        if not issuestore_mod.hasLocalPage(key):
            return notFound(f"{key} isn't a store ticket's key.")
        try:
            issue = storeFrom(cfg).issue(key)
        except IssueNotFound as error:
            return notFound(str(error))
        except STORE_ERRORS as error:
            return render_template("issue.html", ticket=None, problem=str(error)), 500
        return render_template("issue.html", ticket=ticketPage(issue), problem=None)

    # A link the integrated browser clicks never reaches VS Code, so the page posts here and the
    # server sends it through `code`. Only this server's own pages may post, so another site's page can't.
    @app.post("/issues/<key>/open")
    def openInEditor(key):
        if not isSameOrigin(request.headers.get("Origin"), request.host_url):
            abort(403)
        if not issuestore_mod.hasLocalPage(key):
            return notFound(f"{key} isn't a store ticket's key.")
        try:
            issue = storeFrom(cfg).issue(key)
            subprocess.run(openCommand(cfg, issue.file), check=True, capture_output=True, timeout=CODE_SECONDS)
        except IssueNotFound as error:
            return notFound(str(error))
        except (*STORE_ERRORS, subprocess.SubprocessError) as error:
            return render_template("issue.html", ticket=None, problem=f"VS Code didn't open it: {error}"), 500
        return redirect(f"/issues/{key}", code=303)

    return app


# A browser sends Origin on a POST; one from this server's own page names the host it was served on.
def isSameOrigin(origin, hostUrl):
    return origin is not None and origin.rstrip("/") == hostUrl.rstrip("/")


def codeCommand(cfg):
    return os.path.expanduser(cfg.get("codeCommand") or config_mod.defaults()["codeCommand"])


# The `code` run that opens path in a tab of the focused window, the one whose page was clicked:
# plan-window's /open link goes to that window. `code <path>` would open the file in whichever
# window's folders hold it.
def openCommand(cfg, path):
    return [codeCommand(cfg), "--open-url", term_mod.openFileLink(path)]


def notFound(problem):
    return render_template("not_found.html", problem=problem), 404


def storeFrom(cfg):
    return issuestore_mod.fileStoreFrom(cfg)


# The ticket as its page shows it: its front matter, its body and its log, the body and each log
# entry rendered from their Markdown.
def ticketPage(issue):
    return {
        "key": issue.key,
        "title": issue.summary,
        "fields": issue.fields,
        "path": issue.file,
        "body": bodyHtml(issue.description),
        "log": [logEntry(entry) for entry in issue.comments],
    }


def bodyHtml(markdown):
    return Markup(MARKDOWN.render(markdown))


# A log entry is one line, so its Markdown is rendered inline, with no <p> around it.
def logEntry(comment):
    return {"date": comment.date, "text": Markup(MARKDOWN.renderInline(comment.text))}


# The store's INDEX.md, Next up, rendered as the page: the store's scripts/index.py writes it, so
# the page lists what INDEX lists, in its order. Its # heading is the page's own heading.
def indexHtml(cfg):
    with open(os.path.join(storeFrom(cfg).dir, INDEX_FILE), encoding="utf8") as f:
        tokens = withoutTitle(MARKDOWN.parse(f.read()))
    for token in tokens:
        if token.children:
            token.children = linkedToPages(token.children)
    return Markup(MARKDOWN.renderer.render(tokens, MARKDOWN.options, {}))


def withoutTitle(tokens):
    startsWithTitle = bool(tokens) and tokens[0].type == "heading_open" and tokens[0].tag == "h1"
    return tokens[3:] if startsWithTitle else tokens


# An inline run's tokens with each ticket link sent to the ticket's page. A link to another file in
# the store, such as a milestone's, has no page, so it is dropped and its text kept.
def linkedToPages(children):
    kept, droppedLinks = [], []
    for token in children:
        if token.type == "link_open":
            keep = pointsAtAPage(token)
            droppedLinks.append(not keep)
        else:
            keep = token.type != "link_close" or not droppedLinks.pop()
        if keep:
            kept.append(token)
    return kept


# Whether link goes somewhere once a ticket file's link is pointed at its page.
def pointsAtAPage(link):
    href = link.attrGet("href") or ""
    ticket = TICKET_FILE_RE.match(href)
    if ticket and issuestore_mod.hasLocalPage(ticket.group(1)):
        link.attrSet("href", f"/issues/{ticket.group(1)}")
        return True
    return href.startswith(("http://", "https://"))


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
    port = serve_mod.portIn(sys.argv[1:]) or serve_mod.portFrom(cfg)
    cfg = {**cfg, "serverPort": port}  # so the pages link to the port this server is on
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
