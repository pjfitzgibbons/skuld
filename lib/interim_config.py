# The interim end-to-end suite's settings (docs/adr/0005-redmine-is-the-tracker.md §3.9). The file
# holds names and numbers only: the bot's key, the repository's WS key and the signed-in session
# live in the Keychain and are read on each use (§3.3), so a service name here is not a secret.
import json
import os

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".config", "skuld", "interim-e2e.json")

# Every key the suite reads, and its default. None means "not configured": the check that needs it
# fails and names the key, so the run lists the full gate instead of stopping at the first gap.
#
#   redmineUrl            The instance (§3.1).
#   skuldProject          The skuld project's identifier in Redmine.
#   skuldProjectNames     The project names the bot user's memberships may show (check 12).
#   taskNumber            Number n of the end-to-end task, issue SK-<n> (SK-0080 creates it).
#   fixtureRepo           Path of a clone of the fixture GitHub repo. Never skuld's own repo.
#   signedInUserId        Redmine id of Peter's user, whose journal entries mark a person's action.
#   openStatus            Name of the status the reset restores.
#   inProgressStatus      Name of the status Start sets.
#   oldIdFieldId          Id of the `Old ID` custom field, for `cf_<id>` filters (checks 9, 10).
#   statusMap             Store status -> Redmine status name (SK-0079's mapping, check 10).
#   issueStore            The ticket store checkout (checks 9 to 11); falls back to skuld's config.
#   botKeyService         Keychain service holding the bot user's API key.
#   repositoryKeyService  Keychain service holding the fixture repository's WS key (check 8).
#   sessionService        Keychain service holding Peter's Redmine session cookie (check 7).
DEFAULTS = {
    "redmineUrl": "http://127.0.0.1:3010",
    "skuldProject": "skuld",
    "skuldProjectNames": ["skuld"],
    "taskNumber": None,
    "fixtureRepo": None,
    "signedInUserId": None,
    "openStatus": "New",
    "inProgressStatus": "In Progress",
    "oldIdFieldId": None,
    "statusMap": {"open": "New", "in-progress": "In Progress", "done": "Closed", "wontfix": "Rejected"},
    "issueStore": None,
    "botKeyService": "skuld-redmine-bot-key",
    "repositoryKeyService": "skuld-redmine-repository-key",
    "sessionService": "skuld-redmine-session",
}

PATH_KEYS = ["fixtureRepo", "issueStore"]


class NotConfigured(Exception):
    pass


def load(path=CONFIG_FILE):
    """The defaults, with the file's keys merged over them. A missing file is the defaults."""
    settings = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf8") as f:
            settings.update(readSettings(f.read(), path))
    return expandPaths(settings)


def readSettings(text, path):
    try:
        given = json.loads(text)
    except ValueError as error:
        raise NotConfigured(f"{path} is not JSON: {error}")
    unknown = sorted(set(given) - set(DEFAULTS))
    if unknown:
        raise NotConfigured(f"{path} sets {', '.join(unknown)}, which the suite does not read")
    return given


def expandPaths(settings):
    for key in PATH_KEYS:
        if settings[key]:
            settings[key] = os.path.expanduser(settings[key])
    return settings


def require(settings, key):
    """The setting's value, or a NotConfigured that names the key and the file."""
    value = settings[key]
    if value in (None, "", []):
        raise NotConfigured(f"{key} is not set: add it to {CONFIG_FILE}")
    return value
