# plan's settings: ~/.config/skuld/config.json, merged over defaults. No vscode settings API
# here, so this is the one place every key the terminal tool reads is named and documented.
#
# Ported from lib/config.js. Faithful, behavior-preserving port: no change in logic.
import json
import os
import sys

from .start import DEFAULT_TYPE_MAP
from .done import DISPOSABLE

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".config", "skuld", "config.json")

# This repo's own root (lib/'s parent) -- checkouts.SK's default, so Start/Open/Ship/Done find it
# regardless of where `plan` runs from. See checkouts below.

# Keys a config may no longer set, and where their setting went. A config that still sets one is
# refused, so a stale config fails loudly instead of silently losing the setting.
RETIRED_KEYS = {
    "issuesDir": "which is now issueStore; rename the key",
    "jql": 'which is now per project: move it into its project\'s entry under "jira"',
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every key plan reads, and its default. Keys not listed here are never read by any module;
# add the key here first if a module needs a new one.
#
#   jira           { <Jira project key>: { "repos": [<remote>, ...], "jql": <JQL> } }, e.g.
#                  { "CIAM": { "repos": ["git@github.com:cryoport/cryo-gitops-github.git"] } }.
#                  "jql" is the project's set in Next up; without it, lib/jira.py's nextUpJql()
#                  for the project ("project in (CIAM) AND assignee = currentUser() AND
#                  statusCategory != Done ORDER BY Rank").
#                  Jira is on only when this names a project: with none, no command reads the
#                  netrc file or calls Jira, Next up is the store's tickets alone, and a Jira key
#                  is refused (jiraProjects()). Default: None.
#   checkouts      { <project key>: <repo path, or a LIST of them> }, e.g.
#                  { "CIAM": ["~/programming/cryo-gitops-github",
#                  "~/programming/CryoportSystems/Cryoport-Authentication-Service"] } -- several
#                  repos can serve one project. A bare string is still accepted, as a
#                  one-item list. These are the only repos Start/Open/Ship/Done use for the
#                  project (start.py's mainCheckout(), docs/architecture.md §7.0): existing work
#                  for the key in one of them wins; for new work the repo `plan` runs from is
#                  used only when it is listed here, otherwise one listed repo is used outright,
#                  several ask (fzf) for a pick, and none listed is a refusal naming the project
#                  and checkouts. Default: {} for every project but SK, which defaults to this
#                  repo's own root (SK issues are this repo's own backlog, lib/filestore.py) -- a
#                  config that sets its OWN `checkouts` dict without a "SK" entry loses this
#                  default (the merge below is shallow, same caveat as typeMap's); add one back by
#                  hand if that happens:
#                  "SK": "~/programming/jira-plan".
#   projectMarkers { <project key>: <short marker string shown in the picker/list>, e.g.
#                  { "CIAM": "C" } }. Cosmetic only; falls back to the project key itself.
#                  Default: {}.
#   disposable     Branch name patterns (globs, as done.py's DISPOSABLE already defines them)
#                  that Done may delete after a merge without asking. Default: done.py's
#                  DISPOSABLE list.
#   pr             Path to the `pr` script Ship invokes. Default: "~/.local/bin/pr".
#   typeMap        { <issue type name>: <branch prefix> }, e.g. { "story": "feat",
#                  "bug": "fix" }, matched without regard to case. An issue type absent from the
#                  map gets "chore" (start.py's prefixFor). Default: start.py's DEFAULT_TYPE_MAP. load() merges
#                  this key SHALLOWLY (a config typeMap entirely replaces the default, not just
#                  the issue types it names) -- a config overriding one issue type only recognizes
#                  branches made since, under that narrower map. issueBranch() (N9) compensates by
#                  always accepting DEFAULT_TYPE_MAP's prefixes too, in addition to whatever the
#                  configured typeMap adds, so a branch made before the override is still adopted.
#   netrcFile      Path to the netrc file holding the "machine <baseUrl's host>" entry, if not
#                  the default ~/.netrc. Default: None (jira.py falls back to ~/.netrc itself).
#   baseUrl        The Jira site's base URL, e.g. "https://cryoport.atlassian.net". Required
#                  once `jira` names a project (jiraSettingsFrom()). Default: None.
#   slotsFile      Path to the "<Desktop number>=<KEY>" file the placer writes so SlotBar
#                  can mark a Desktop with the issue it holds.
#                  Default: "~/.cache/plan/workspaces".
#   windowsFile    Path to the "<KEY>=<window id>" file that says which VS Code window is each
#                  issue's. Default: "~/.cache/plan/windows".
#   workspaceDir   Folder for each issue window's throwaway <KEY>.code-workspace file, outside
#                  every repo; Done deletes the issue's. Default: "~/.cache/plan/code-workspaces".
#   spacesHelper   Path to the skuld-spaces helper (macos/spaces.swift). Default:
#                  macos/build/skuld-spaces in this repo, which the placer builds with swiftc when
#                  it's missing or older than its source.
#   reservedDesktops Desktop numbers Start never takes for an issue. Default: [1].
#   issueStore     The issue store's checkout: the folder of SK-<nnnn> ticket files
#                  (lib/filestore.py's FileStore), its own git repo, e.g.
#                  "~/programming/skuld-project/issues". `~` is expanded. Default: None -- the
#                  store lives outside skuld, so a machine that works tickets sets it; a command
#                  that needs the store without it refuses, naming this key (issueStoreFrom()).
#   claudeCommand  The `claude` CLI `plan agents` asks for its sessions (`claude agents --json`),
#                  as a path, since the LaunchAgent's PATH doesn't hold it.
#                  Default: "~/.local/bin/claude".
#   serverPort     The local page server's port on 127.0.0.1 (lib/server.py, `plan serve`).
#                  Default: 8765.
def defaults():
    return {
        "jira": None,
        "checkouts": {"SK": [_REPO_ROOT]},
        "projectMarkers": {},
        "disposable": DISPOSABLE,
        "pr": "~/.local/bin/pr",
        "typeMap": DEFAULT_TYPE_MAP,
        "netrcFile": None,
        "baseUrl": None,
        "slotsFile": "~/.cache/plan/workspaces",
        "windowsFile": "~/.cache/plan/windows",
        "workspaceDir": "~/.cache/plan/code-workspaces",
        "spacesHelper": None,
        "reservedDesktops": [1],
        "issueStore": None,
        "claudeCommand": "~/.local/bin/claude",
        "serverPort": 8765,
    }


# Reads CONFIG_FILE (or file, for tests), merged shallowly over defaults(); a missing file is
# the defaults alone, not an error. A malformed file is an error: a typo'd config should fail
# loudly, not silently fall back.
def load(file=None):
    if file is None:
        file = CONFIG_FILE
    try:
        with open(file, "r", encoding="utf8") as f:
            text = f.read()
    except FileNotFoundError:
        return defaults()
    except OSError as error:
        raise RuntimeError(f"can't read {file}: {error}") from error
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{file} isn't valid JSON: {error}") from error
    refuseRetiredKeys(parsed, file)
    warnIgnoredKeys(parsed, file)
    return withExpandedStore({**defaults(), **parsed})


def refuseRetiredKeys(parsed, file):
    for key, where in RETIRED_KEYS.items():
        if key in parsed:
            raise RuntimeError(f"{file} sets {key}, {where}")


# Keys a config may still set from an earlier front end; they're read by nothing now, so a config
# that sets one still loads, with a warning.
IGNORED_KEYS = ("ghosttyApp", "aerospaceConfig", "windowPlacer", "browserApp", "browserBundleId", "project")


def warnIgnoredKeys(parsed, file):
    for key in IGNORED_KEYS:
        if key in parsed:
            print(f"{file} sets {key}, which is no longer read; remove it", file=sys.stderr)


def withExpandedStore(cfg):
    store = cfg.get("issueStore")
    if store:
        cfg["issueStore"] = os.path.expanduser(store)
    return cfg


# The issue store's path, for a command that needs it; refuses when the config names none.
def issueStoreFrom(cfg):
    store = cfg.get("issueStore")
    if not store:
        raise RuntimeError(f"no issue store set: add \"issueStore\": \"<path>\" to {CONFIG_FILE}")
    return store


# The Jira projects this Mac works, in the config's order; empty when Jira is off.
def jiraProjects(cfg):
    return list(cfg.get("jira") or {})


# What Jira() needs, for a Mac whose config names Jira projects; refuses when it names no site.
def jiraSettingsFrom(cfg):
    if not cfg.get("baseUrl"):
        raise RuntimeError(f'the "jira" entry needs a site: add "baseUrl": "https://<site>.atlassian.net" to {CONFIG_FILE}')
    return {
        "baseUrl": cfg["baseUrl"],
        "jql": {project: projectJql(cfg, project) for project in jiraProjects(cfg)},
        "netrcFile": cfg.get("netrcFile"),
    }


# A Jira project's own Next up query, its entry's "jql"; None when it sets none.
def projectJql(cfg, project):
    return (cfg["jira"][project] or {}).get("jql")
