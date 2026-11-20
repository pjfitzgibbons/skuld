# The steps the interim suite cannot take yet, each one named function. A trigger is the plugin's
# (SK-0086), and the checklist and round APIs are SK-0085's and SK-0084's. None is defined, so each
# fails with its owner and what is missing, and the probe beside it is written in full. When the
# owner defines the step, the body here becomes one call and no check changes. No plugin API is
# invented here.
from .interim_world import PendingTrigger


def pending(owner, what):
    raise PendingTrigger(f"{owner}: {what} not defined")


# ---- the plugin's triggers (SK-0086)

def start(key):
    """Peter's signed-in user starts `key` from the plugin."""
    pending("SK-0086", "plugin trigger")


def startRefusal(key):
    """Peter's user starts `key`, a key whose prefix matches no project. Returns the refusal the
    plugin showed."""
    pending("SK-0086", "plugin trigger")


def ship(key, worktree, manifest):
    """Peter's user ships `key` from `worktree`, whose manifest lists `manifest`."""
    pending("SK-0086", "plugin trigger")


def pr(key, worktree):
    """Peter's user runs `pr <key>` in `worktree`: it commits what Ship staged, pushes, opens the PR
    and marks the round sent. Today's `pr` commits, pushes and opens the PR but sends no round."""
    pending("SK-0087", "`pr` sending the round")


def done(key):
    """Peter's user runs Done on `key`."""
    pending("SK-0086", "plugin trigger")


def tickChecklistItem(key, item):
    """Peter's user ticks `item` on `key`'s checklist."""
    pending("SK-0086", "plugin trigger")


# ---- the checklist's CRUD API (SK-0085)

def checklistItems(number):
    pending("SK-0085", "checklist API")


def createChecklistItem(number, text):
    pending("SK-0085", "checklist API")


def deleteChecklistItem(number, item):
    pending("SK-0085", "checklist API")


# ---- the round store (SK-0084)

def storedRoundFiles(ledger, roundRow):
    """{file name: bytes} of the files stored on a round: its commit message and PR body."""
    pending("SK-0084", "round-file storage")


def roundStatus(ledger, roundRow):
    """The round's status, "draft" or "sent"."""
    pending("SK-0084", "round status")


def reportPageText(ledger, roundRow):
    """The visible text of the round's report page, which tells a person to run `pr <KEY>`."""
    pending("SK-0087", "round report page")


def storedFindings(ledger, roundRow):
    """The findings the ledger holds for `roundRow`."""
    pending("SK-0084", "round-to-findings mapping")
