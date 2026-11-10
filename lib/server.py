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

from flask import Flask, jsonify, render_template
from werkzeug.serving import make_server

from . import config as config_mod
from . import fileissues as fileissues_mod
from . import serve as serve_mod

TEMPLATES = os.path.join(serve_mod.ROOT, "templates")
IN_PROGRESS = "indeterminate"  # fileissues' status category for in-progress
# The Host header must name loopback, so a DNS-rebinding page can't read the server as its own origin.
TRUSTED_HOSTS = [serve_mod.HOST, "localhost"]
# What reading the store can raise: no store set, a file unreadable or gone, a malformed field.
STORE_ERRORS = (RuntimeError, OSError, ValueError)

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

    return app


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
