# The local page server (architecture §6.3, GUIDE §7): Flask on 127.0.0.1 at the machine config's
# serverPort, rendering every page from a Jinja template in templates/. Its routes only read. This
# is the one module that imports Flask: the LaunchAgent runs it from skuld's uv venv as
# `python -m lib.server`, and `plan serve` (lib/serve.py) controls that agent. Each request and
# each error is logged to stderr, which the agent sends to serve.LOG_FILE.
import errno
import logging
import os
import re
import socket
import sys

from flask import Flask, jsonify, render_template
from werkzeug.serving import make_server

from . import config as config_mod
from . import issuestore as issuestore_mod
from . import serve as serve_mod
from .issue import IN_PROGRESS, IssueNotFound

TEMPLATES = os.path.join(serve_mod.ROOT, "templates")
# The Host header must name loopback, so a DNS-rebinding page can't read the server as its own origin.
TRUSTED_HOSTS = [serve_mod.HOST, "localhost"]
# What reading the store can raise: no store set, a file unreadable or gone, a malformed field.
STORE_ERRORS = (RuntimeError, OSError, ValueError)
SECTION_RE = re.compile(r"^## (.+?)\s*$")
SUBHEADING_RE = re.compile(r"^#{3,6} (.+?)\s*$")
ITEM_RE = re.compile(r"^[-*] (.*)$")
CHECKBOX_RE = re.compile(r"^\[([ xX])\] (.*)$")

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
            issues = issuesInProgress(cfg)
        except STORE_ERRORS as error:
            return render_template("index.html", issues=[], problem=str(error))
        return render_template("index.html", issues=issues, problem=None)

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

    return app


def notFound(problem):
    return render_template("not_found.html", problem=problem), 404


def storeFrom(cfg):
    return issuestore_mod.fileStoreFrom(cfg)


# The ticket as its page shows it: its front matter, its body split at its ## headings into titled
# sections, each made of blocks, and its log. Only the block structure is read; every block's text
# is shown as written, and autoescaped, so nothing in a ticket becomes markup.
def ticketPage(issue):
    return {
        "key": issue.key,
        "title": issue.summary,
        "fields": issue.fields,
        "path": issue.file,
        "sections": bodySections(issue.description),
        "log": issue.comments,
    }


# [{heading, blocks}] for a ticket body: the text before the first ## heading under heading None,
# then one section per ## heading. A section with a heading and no text is kept.
def bodySections(body):
    sections = [{"heading": None, "lines": []}]
    for line in body.split("\n"):
        heading = SECTION_RE.match(line)
        if heading:
            sections.append({"heading": heading.group(1), "lines": []})
        else:
            sections[-1]["lines"].append(line)
    return [section for section in map(sectionBlocks, sections) if saysSomething(section)]


def sectionBlocks(section):
    return {"heading": section["heading"], "blocks": textBlocks(section["lines"])}


def saysSomething(section):
    return bool(section["heading"] or section["blocks"])


# A section's lines as blocks, read one blank-line-separated chunk at a time.
def textBlocks(lines):
    blocks = []
    for chunk in chunks(lines):
        blocks.extend(chunkBlocks(chunk))
    return blocks


# The runs of non-blank lines between blank ones.
def chunks(lines):
    found, chunk = [], []
    for line in lines:
        if line.strip():
            chunk.append(line)
        elif chunk:
            found.append(chunk)
            chunk = []
    if chunk:
        found.append(chunk)
    return found


def chunkBlocks(chunk):
    blocks = []
    for line in chunk:
        addLine(blocks, line)
    return blocks


# Adds line to the chunk's blocks: a ### line is a subheading, a "- " line an item of the list it
# starts or continues, an indented line under an item more of that item, and any other line starts
# a paragraph or, after one, joins it.
def addLine(blocks, line):
    last = blocks[-1] if blocks else {"kind": None}
    subheading = SUBHEADING_RE.match(line)
    item = ITEM_RE.match(line)
    if subheading:
        blocks.append({"kind": "subheading", "text": subheading.group(1)})
    elif item:
        listToExtend(blocks)["items"].append(listItem(item.group(1)))
    elif last["kind"] == "list" and line[:1].isspace():
        last["items"][-1]["text"] += " " + line.strip()
    elif last["kind"] == "paragraph":
        last["text"] += " " + line.strip()
    else:
        blocks.append({"kind": "paragraph", "text": line.strip()})


# The list the next item belongs to: the last block when it is a list, else a new one.
def listToExtend(blocks):
    if not blocks or blocks[-1]["kind"] != "list":
        blocks.append({"kind": "list", "items": []})
    return blocks[-1]


# A list item, with its [ ] or [x] checkbox read off as checked False or True.
def listItem(text):
    box = CHECKBOX_RE.match(text)
    if not box:
        return {"checked": None, "text": text}
    return {"checked": box.group(1).lower() == "x", "text": box.group(2)}


# The store's tickets in progress, in work order. Other trackers' issues are left out: the server
# reads only the ticket store.
def issuesInProgress(cfg):
    return [row for row in storeFrom(cfg).list() if row["category"] == IN_PROGRESS]


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
