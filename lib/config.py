# plan's settings: ~/.config/skuld/config.json, merged over defaults. No vscode settings API
# here, so this is the one place every key the terminal tool reads is named and documented.
#
# Ported from lib/config.js. Faithful, behavior-preserving port: no change in logic.
import json
import os
import sys

from .jira import BASE_URL, DEFAULT_JQL, DEFAULT_PROJECT
from .start import DEFAULT_TYPE_MAP
from .done import DISPOSABLE

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".config", "skuld", "config.json")

# This repo's own root (lib/'s parent) -- the repo checkouts.SK falls back to, so
# Start/Open/Ship/Done find it regardless of where `plan` runs from. See checkouts below.

# The key the issue store's path used to have. A config that still sets it is refused, naming
# issueStore, so a stale config fails loudly instead of silently losing the store.
RETIRED_STORE_KEY = "issuesDir"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every key plan reads, and its default. Keys not listed here are never read by any module;
# add the key here first if a module needs a new one.
#
#   jql            JQL plan's picker and `plan list` run. Default: jira.py's DEFAULT_JQL
#                  ("project = CIAM AND assignee = currentUser() AND statusCategory != Done
#                  ORDER BY Rank").
#   checkouts      { <Jira project key>: <repo path, or a LIST of them> }, e.g.
#                  { "CIAM": ["~/programming/cryo-gitops-github",
#                  "~/programming/CryoportSystems/Cryoport-Authentication-Service"] } -- several
#                  repos can serve one Jira project (user decision 2026-11-05). A bare string is
#                  still accepted, as a one-item list. DECIDED: the repo Start/Open/Ship/Done use is
#                  primarily the one `plan` is run from (its main checkout, found from cwd) --
#                  `checkouts` is now only a FALLBACK, consulted when an issue's work isn't already
#                  found in some other listed repo (start.py's mainCheckout()) and cwd isn't inside
#                  a git repo at all. One listed repo is used outright; more than one asks (fzf) for
#                  a pick; none listed is a refusal naming both ways to fix it. Default: {} for
#                  every project but SK, which defaults to this repo's own root (SK issues are
#                  this repo's own backlog, lib/fileissues.py) -- a config that sets its OWN
#                  `checkouts` dict without a "SK" entry loses this default (the merge below is
#                  shallow, same caveat as typeMap's); add one back by hand if that happens:
#                  "SK": "~/programming/jira-plan".
#   projectMarkers { <Jira project key>: <short marker string shown in the picker/list>, e.g.
#                  { "CIAM": "C" } }. Cosmetic only; falls back to the project key itself.
#                  Default: {}.
#   disposable     Branch name patterns (globs, as done.py's DISPOSABLE already defines them)
#                  that Done may delete after a merge without asking. Default: done.py's
#                  DISPOSABLE list.
#   pr             Path to the `pr` script Ship invokes. Default: "~/.local/bin/pr".
#   typeMap        { <Jira issue type name>: <branch prefix> }, e.g. { "Story": "feat",
#                  "Bug": "fix" }. An issue type absent from the map gets "chore"
#                  (start.py's prefixFor). Default: start.py's DEFAULT_TYPE_MAP. load() merges
#                  this key SHALLOWLY (a config typeMap entirely replaces the default, not just
#                  the issue types it names) -- a config overriding one issue type only recognizes
#                  branches made since, under that narrower map. issueBranch() (N9) compensates by
#                  always accepting DEFAULT_TYPE_MAP's prefixes too, in addition to whatever the
#                  configured typeMap adds, so a branch made before the override is still adopted.
#   netrcFile      Path to the netrc file holding the "machine cryoport.atlassian.net" entry,
#                  if not the default ~/.netrc. Default: None (jira.py falls back to
#                  ~/.netrc itself).
#   baseUrl        The Jira site's base URL. Default: jira.py's BASE_URL
#                  ("https://cryoport.atlassian.net").
#   project        The default Jira project key, used where none is otherwise known (e.g.
#                  resolving a bare key typed without one). Default: jira.py's DEFAULT_PROJECT
#                  ("CIAM").
#   slotsFile      Path to the "<Desktop number>=<KEY>" file the placer writes so SketchyBar's
#                  desktops.sh plugin can label a Desktop with the issue it holds.
#                  Default: "~/.cache/plan/workspaces".
#   windowsFile    Path to the "<KEY>=<window id>" file that says which VS Code window is each
#                  issue's. Default: "~/.cache/plan/windows".
#   workspaceDir   Folder for each issue window's throwaway <KEY>.code-workspace file, outside
#                  every repo; Done deletes the issue's. Default: "~/.cache/plan/code-workspaces".
#   spacesHelper   Path to the skuld-spaces helper (macos/spaces.swift). Default:
#                  macos/build/skuld-spaces in this repo, which the placer builds with swiftc when
#                  it's missing or older than its source.
#   reservedDesktops Desktop numbers Start never takes for an issue. Default: [1].
#   browserApp     The browser SpacesPlacer.showUrl() opens a Jira issue's URL in -- a name `open`
#                  resolves, not necessarily a path. On the issue's Desktop, the URL opens as a new
#                  tab in a browserBundleId window already there (`open -a`), otherwise in a new
#                  window (`open -na ... --args --new-window`). Default: "Brave Browser" (user
#                  decision 2026-11-05: never a bare `open <url>`, never a terminal's link-click).
#   browserBundleId The browser's bundle id, used to find its windows on the issue's Desktop.
#                  Default: "com.brave.Browser".
#   issueStore     The issue store's checkout: the folder of SK-<nnnn> ticket files
#                  (lib/fileissues.py's FileIssues), its own git repo, e.g.
#                  "~/programming/skuld-project/issues". `~` is expanded. Default: None -- the
#                  store lives outside skuld, so a machine that works tickets sets it; a command
#                  that needs the store without it refuses, naming this key (issueStoreFrom()).
def defaults():
    return {
        "jql": DEFAULT_JQL,
        "checkouts": {"SK": [_REPO_ROOT]},
        "projectMarkers": {},
        "disposable": DISPOSABLE,
        "pr": "~/.local/bin/pr",
        "typeMap": DEFAULT_TYPE_MAP,
        "netrcFile": None,
        "baseUrl": BASE_URL,
        "project": DEFAULT_PROJECT,
        "slotsFile": "~/.cache/plan/workspaces",
        "windowsFile": "~/.cache/plan/windows",
        "workspaceDir": "~/.cache/plan/code-workspaces",
        "spacesHelper": None,
        "reservedDesktops": [1],
        "browserApp": "Brave Browser",
        "browserBundleId": "com.brave.Browser",
        "issueStore": None,
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
    if RETIRED_STORE_KEY in parsed:
        raise RuntimeError(f"{file} sets {RETIRED_STORE_KEY}, which is now issueStore; rename the key")


# Keys a config may still set from an earlier front end; they're read by nothing now, so a config
# that sets one still loads, with a warning.
IGNORED_KEYS = ("ghosttyApp", "aerospaceConfig", "windowPlacer")


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
