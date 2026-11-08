# plan's settings: ~/.config/jira-plan/config.json, merged over defaults. No vscode settings API
# here, so this is the one place every key the terminal tool reads is named and documented.
#
# Ported from lib/config.js. Faithful, behavior-preserving port: no change in logic.
import json
import os

from .jira import BASE_URL, DEFAULT_JQL, DEFAULT_PROJECT
from .start import DEFAULT_TYPE_MAP
from .done import DISPOSABLE

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".config", "jira-plan", "config.json")

# This repo's own root (lib/'s parent) -- where SK-<n> issues live (issues/) and the repo
# checkouts.SK falls back to, so Start/Open/Ship/Done find it regardless of where `plan` runs
# from. See issuesDir/checkouts below.
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
#   ghosttyApp     Path to the Ghostty.app bundle term.py launches new windows from. Default:
#                  "/Applications/Ghostty.app".
#   aerospaceConfig Path to the Aerospace config whose "# Workspaces: ..." comment names every
#                  workspace an Aerospace hotkey already targets; term.py's freeWorkspace() picks
#                  the first workspace this doesn't name and that has no windows. Default:
#                  "~/.aerospace.toml".
#   slotsFile      Path to the "<workspace>=<KEY>" file term.py writes so SketchyBar's
#                  workspaces.sh plugin can label a workspace with the issue it holds. Default:
#                  "~/.cache/plan/workspaces".
#   browserApp     The browser openUrl() opens the issue's URL in -- a name `open` resolves (as
#                  plan-open does), not necessarily a path. If a browserBundleId window is already
#                  on the target workspace, the URL opens as a new tab in it (`open -a`); otherwise
#                  a new window opens (`open -na ... --args --new-window`) and is moved there.
#                  Default: "Brave Browser" (user decision 2026-11-05: Brave, launched explicitly
#                  this way, never a bare `open <url>` and never a terminal's own link-click).
#   browserBundleId The launched browser's bundle id, used both to find an existing window on the
#                  target workspace to reuse as a tab, and for the before/after window-id diff that
#                  finds a newly-opened window. Default: "com.brave.Browser" (Brave's bundle id).
#   issuesDir      Where SK-<n> issue files live (lib/fileissues.py's FileIssues), per
#                  issues/README.md's format. Default: this repo's own issues/ folder, derived
#                  from lib/config.py's own location -- never a path a user has to set, since
#                  SK issues are this tool's own backlog, not a configurable tracker.
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
        "ghosttyApp": "/Applications/Ghostty.app",
        "aerospaceConfig": "~/.aerospace.toml",
        "slotsFile": "~/.cache/plan/workspaces",
        "browserApp": "Brave Browser",
        "browserBundleId": "com.brave.Browser",
        "issuesDir": os.path.join(_REPO_ROOT, "issues"),
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
    return {**defaults(), **parsed}
