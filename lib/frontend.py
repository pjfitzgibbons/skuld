# The front-end port (architecture §6.1): where Start and Open bring an issue up. Each adapter has
# check(command, key), which refuses before anything changes when its front end can't be used, and
# show(issue), the last step of Start and Open. The machine config's frontEnd picks the adapter, so
# no caller branches on the choice.
from dataclasses import dataclass

from . import config as config_mod
from . import term as term_mod
from .herdr import Herdr


# What a front end needs to show an issue. ticket is a store issue's ticket file, None for a Jira
# issue; planWorkspace is the plan workspace file, already listing the worktree.
@dataclass(frozen=True)
class IssueToShow:
    key: str
    worktree: str
    prompt: str
    ticket: str | None
    planWorkspace: str


# The plan window (ADR 0004). plan-window itself opens the issue's ticket and Claude, from the
# issue's workspace file.
class VSCode:
    def check(self, command, key):
        term_mod.checkExtensionLink(command, key)

    def show(self, issue):
        term_mod.openPlanWindow(issue.planWorkspace)
        term_mod.revealIssue(issue.key)


FRONT_ENDS = {"vscode": VSCode, "herdr": Herdr}


def frontEndFor(cfg):
    name = cfg.get("frontEnd", "vscode")
    # A list, not the dict, so a JSON list or object here is refused rather than a TypeError.
    if name not in list(FRONT_ENDS):
        choices = " or ".join(f'"{choice}"' for choice in FRONT_ENDS)
        raise RuntimeError(f'"frontEnd" in {config_mod.CONFIG_FILE} must be {choices}, not {name!r}')
    return FRONT_ENDS[name]()
