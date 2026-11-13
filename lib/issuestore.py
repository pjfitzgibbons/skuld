# The IssueStore port (docs/architecture.md §6.1): which adapter answers for a key. Every adapter
# has the same surface:
#
#   list(project)               Next up's rows for one project: {id, subject, status, category,
#                               issueType, project, parent}, plus `after` from the ticket store
#   issue(key) -> Issue         the issue in full, in one fetch (lib/issue.py)
#   statusTransitionId(key, c)  refuses when the issue can't move to category c (lib/issue.py's
#                               IN_PROGRESS, Start's one move)
#   transition(key, c) -> Issue moves it, reads it back, and refuses if the move didn't take
#   addComment(key, text)       a plain-text comment, as the tracker keeps one
#   closeNote(key)              what Done prints for the human's close
#
# Callers hold a store and the Issue it returned; none of them reads a tracker's own shapes.
from . import config as config_mod
from . import filestore as filestore_mod
from . import serve as serve_mod
from .issue import KEY_RE, projectOf
from .jira import Jira

canonicalKey = filestore_mod.canonicalKey


# Every project this Mac works, in Next up's order: each Jira project the config's `jira` entry
# names, in its order, then the ticket store's.
def projects(cfg):
    return [*config_mod.jiraProjects(cfg), filestore_mod.PROJECT]


# The adapter for key's project. Any project projects() doesn't name is refused before a command
# changes anything.
def storeFor(cfg, key):
    refuseUnknownProject(cfg, key)
    return storeForProject(cfg, projectOf(key))


# The adapter for one of projects(): the ticket store for its prefix, Jira for the rest.
def storeForProject(cfg, project):
    if isFileStoreProject(project):
        return fileStoreFrom(cfg)
    return jiraFrom(cfg)


def isFileStoreProject(project):
    return project == filestore_mod.PROJECT


# Whether key's issue has its page on the local server (lib/server.py's /issues/<KEY>): a ticket
# store key, and only a well-formed one, so nothing else a URL carries reaches a file glob.
def hasLocalPage(key):
    return bool(KEY_RE.match(key)) and isFileStoreProject(projectOf(key))


# The ticket store, with the local server's URL for its tickets' pages.
def fileStoreFrom(cfg):
    pages = serve_mod.serverUrl(serve_mod.portFrom(cfg))
    return filestore_mod.FileStore({"dir": config_mod.issueStoreFrom(cfg), "pages": pages})


def jiraFrom(cfg):
    return Jira(config_mod.jiraSettingsFrom(cfg))


def refuseUnknownProject(cfg, key):
    project = projectOf(key)
    if project not in projects(cfg):
        raise RuntimeError(
            f'{key} is unknown on this Mac: {config_mod.CONFIG_FILE} has no "jira" entry naming {project}'
        )
