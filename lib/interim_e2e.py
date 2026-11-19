# The interim's end-to-end suite: the 12 checks of docs/adr/0005-redmine-is-the-tracker.md §3.9
# (anchor interim-end), each an action and the probes that read the result. The suite is the gate
# that ends the interim: it fails until the Ruby core works, each check for its real reason, and
# turns green with no change here once the triggers in lib/interim_triggers.py exist.
# Run it with bin/test-interim-e2e.
import os
import sys

from . import interim_config as configuration
from . import interim_probes as probes
from . import interim_triggers as triggers
from .interim_world import CheckFailed, Fixture, Redmine, keychainSecret

TASK_SUBJECT = "End-to-end task"
WRONG_PREFIX = "XX"
UNLISTED_FILE = "e2e-unlisted.txt"


class World:
    """The run's settings, clients and the facts one check leaves for a later one."""

    def __init__(self, settings):
        self.settings = settings
        self.redmine = Redmine(settings["redmineUrl"], settings["botKeyService"], settings["sessionService"])
        self.state = {}

    def need(self, key):
        try:
            return configuration.require(self.settings, key)
        except configuration.NotConfigured as error:
            raise CheckFailed(str(error))

    @property
    def number(self):
        return self.need("taskNumber")

    @property
    def key(self):
        return f"SK-{self.number}"

    @property
    def fixture(self):
        fixture = Fixture(self.need("fixtureRepo"))
        fixture.requireNotSkuldsRepo()
        return fixture

    def task(self, include=None):
        """The end-to-end task, read as the bot user. Also proves the instance and the task exist."""
        self.redmine.requireReachable()
        params = {"include": include} if include else {}
        issue = self.redmine.get(f"/issues/{self.number}.json", **params)["issue"]
        if issue["subject"] != TASK_SUBJECT:
            raise CheckFailed(f"issue {self.key} has subject {issue['subject']!r}, not {TASK_SUBJECT!r}")
        return issue

    def statuses(self):
        return {s["name"]: s for s in self.redmine.get("/issue_statuses.json")["issue_statuses"]}

    def statusNamed(self, name):
        statuses = self.statuses()
        if name not in statuses:
            raise CheckFailed(f"Redmine has no status named {name!r}; it has {sorted(statuses)}")
        return statuses[name]

    def requireStatus(self, issue, name):
        actual = issue["status"]["name"]
        if actual != name:
            raise CheckFailed(f"{self.key} status is {actual!r}, expected {name!r}")

    def signedInUserId(self):
        return self.need("signedInUserId")

    def requirePersonsJournal(self, journal, what):
        if journal is None:
            raise CheckFailed(f"{self.key} has no journal for {what}")
        actual = probes.journalUserId(journal)
        if actual != self.signedInUserId():
            raise CheckFailed(f"journal for {what} is by user {actual}, expected the signed-in user {self.signedInUserId()}")

    def ledger(self, number=None):
        return self.redmine.get(f"/issues/{number or self.number}/review_findings.json")

    def onlyWorktree(self):
        worktrees = self.fixture.worktrees(self.number)
        if len(worktrees) != 1:
            raise CheckFailed(f"fixture shows {len(worktrees)} worktrees for {self.key}, expected 1 (Start, check 1)")
        return worktrees[0]


# ---- the reset (§3.9, "Each run first resets it")

def reset(world):
    """Restore the task to its starting state. Returns what could not be restored, each cause once."""
    problems = []
    for step in (resetTask, resetFixture):
        try:
            step(world)
        except CheckFailed as error:
            if str(error) not in problems:
                problems.append(str(error))
    return problems


def resetTask(world):
    task = world.task()
    openStatus = world.statusNamed(world.need("openStatus"))
    update = {"status_id": openStatus["id"]}
    if world.key not in (task.get("description") or ""):
        update["description"] = f"The end-to-end task for the interim suite: {world.key}"
    world.redmine.json("PUT", f"/issues/{world.number}.json", body={"issue": update})
    for item in triggers.checklistItems(world.number):
        triggers.deleteChecklistItem(world.number, item)


def resetFixture(world):
    world.fixture.remove(world.number)


# ---- the 12 checks. Each raises CheckFailed with what its probe saw.

def start_as_signed_in_user(world):
    world.task()
    triggers.start(world.key)
    issue = world.task("journals")
    world.requireStatus(issue, world.need("inProgressStatus"))
    world.requirePersonsJournal(probes.newestStatusJournal(issue["journals"]), "the status change")
    fixture = world.fixture
    branches = fixture.branches(world.number)
    if len(branches) != 1:
        raise CheckFailed(f"`git branch --list '*/SK-{world.number}-*'` lists {branches}, expected one branch")
    world.onlyWorktree()


def mismatched_prefix_is_refused(world):
    before = world.task("journals")
    refusal = triggers.startRefusal(f"{WRONG_PREFIX}-{world.number}")
    after = world.task("journals")
    if not refusal:
        raise CheckFailed("the plugin showed no refusal")
    if after["status"] != before["status"] or probes.journalCount(after) != probes.journalCount(before):
        raise CheckFailed(
            f"status/journals changed: {before['status']['name']}/{probes.journalCount(before)} "
            f"-> {after['status']['name']}/{probes.journalCount(after)}"
        )


def ship_stages_from_manifest(world):
    world.task()
    worktree = world.onlyWorktree()
    fixture = world.fixture
    world.state["ledgerBeforeShip"] = readLedgerIfAny(world)
    manifest = [writeChangedTrackedFile(fixture, worktree, world.key)]
    write(os.path.join(worktree, UNLISTED_FILE), f"not in the manifest of {world.key}\n")
    headBefore = fixture.head(worktree)
    triggers.ship(world.key, worktree, manifest)
    report = os.path.join(worktree, ".tmp", world.key, "report.html")
    problems = []
    if not os.path.exists(report):
        problems.append(f"{report} does not exist")
    missing, unexpected = probes.compareStaged(fixture.staged(worktree), manifest)
    if missing or unexpected:
        problems.append(f"staged files differ from the manifest: missing {missing}, unexpected {unexpected}")
    if probes.porcelainStatusOf(fixture.porcelain(worktree), UNLISTED_FILE) != "??":
        problems.append(f"{UNLISTED_FILE} is no longer untracked")
    if fixture.head(worktree) != headBefore:
        problems.append("HEAD moved: Ship committed")
    if problems:
        raise CheckFailed("; ".join(problems))


def round_is_recorded(world):
    world.task()
    ledger = world.ledger()
    before = world.state.get("ledgerBeforeShip")
    if before is None:
        raise CheckFailed("check 3 did not record the rounds before Ship, so there is nothing to compare")
    added = probes.roundsAdded(before, ledger)
    if added != 1:
        raise CheckFailed(f"{added} rounds were added by Ship, expected 1")
    latest = ledger["review_rounds"][-1]
    worktree = world.onlyWorktree()
    problems = roundFileMismatches(triggers.storedRoundFiles(ledger, latest), worktree, world.key)
    if triggers.reportFindings(worktree) != triggers.storedFindings(ledger, latest):
        problems.append("the round's findings differ from the report's")
    if problems:
        raise CheckFailed("; ".join(problems))


def done_never_changes_status(world):
    before = world.task()
    worktree = world.onlyWorktree()
    branch = world.fixture.branches(world.number)[0]
    world.fixture.mergePullRequestOf(branch)
    triggers.done(world.key)
    problems = []
    if worktree in world.fixture.worktrees(world.number) or worktree in world.fixture.worktreeList():
        problems.append(f"`git worktree list` still shows {worktree}")
    after = world.task()
    if after["status"] != before["status"]:
        problems.append(f"status went from {before['status']['name']!r} to {after['status']['name']!r}")
    if problems:
        raise CheckFailed("; ".join(problems))


def ticked_done_box_is_journaled(world):
    world.task()
    before = probes.journalCount(world.task("journals"))
    item = triggers.createChecklistItem(world.number, "Suite item")
    triggers.tickChecklistItem(world.key, item)
    issue = world.task("journals")
    problems = []
    if probes.journalCount(issue) != before + 1:
        problems.append(f"journals went from {before} to {probes.journalCount(issue)}, expected one more")
    newest = probes.newestJournal(issue["journals"])
    if probes.journalUserId(newest) != world.signedInUserId():
        problems.append(f"newest journal is by user {probes.journalUserId(newest)}, expected the signed-in user")
    if not any(i["text"] == "Suite item" and i["ticked"] for i in triggers.checklistItems(world.number)):
        problems.append("the checklist API does not read the item back as ticked")
    if problems:
        raise CheckFailed("; ".join(problems))


def key_renders_as_link(world):
    world.task()
    page = world.redmine.page(f"/issues/{world.number}")
    if not probes.linksToIssue(page, world.number):
        raise CheckFailed(
            f"the description holds links {probes.descriptionLinks(page)}, none to /issues/{world.number}"
        )


def fixes_closes_the_issue(world):
    world.task()
    world.fixture.pushCommitToDefaultBranch(f"fixes {world.key}")
    project = world.need("skuldProject")
    wsKey = keychainSecret(world.need("repositoryKeyService"))
    world.redmine.plain(probes.fetchChangesetsPath(project, wsKey))
    status = world.task()["status"]["name"]
    if not world.statusNamed(status)["is_closed"]:
        raise CheckFailed(f"{world.key} status is {status!r}, which is not a closed status")


def every_ticket_is_imported_once(world):
    world.task()
    tickets = storeTickets(world)
    problems = []
    for key in sorted(tickets):
        total = lookupByOldId(world, key)["total_count"]
        if total != 1:
            problems.append(f"{key}: {total} issues carry it as Old ID")
    if problems:
        raise CheckFailed(f"{len(problems)} of {len(tickets)} tickets: {probes.firstFailures(problems)}")


def import_keeps_every_field(world):
    world.task()
    tickets = storeTickets(world)
    botUserId = world.redmine.get("/users/current.json")["user"]["id"]
    problems = []
    for key in sorted(tickets):
        problems += [f"{key} {m}" for m in importDifferences(world, key, tickets[key], botUserId)]
    if problems:
        raise CheckFailed(f"{len(problems)} differences: {probes.firstFailures(problems)}")


def every_round_is_imported(world):
    world.task()
    problems = []
    for key, files in storeRoundFiles(world).items():
        issue = importedIssue(world, key)
        ledger = world.ledger(issue["id"])
        stored = [triggers.storedRoundFiles(ledger, row) for row in ledger.get("review_rounds", [])]
        for name, content in files.items():
            if not any(round_.get(name) == content for round_ in stored):
                problems.append(f"{key}: {name} is on no imported round")
    if problems:
        raise CheckFailed(probes.firstFailures(problems))


def bot_reaches_skuld_projects_only(world):
    user = world.redmine.get("/users/current.json", include="memberships")["user"]
    actual = probes.membershipProjectNames(user)
    allowed = sorted(world.need("skuldProjectNames"))
    if not actual or actual != allowed:
        raise CheckFailed(f"the bot user's memberships are {actual}, expected {allowed}")


# ---- helpers the checks share

def write(path, text):
    with open(path, "w", encoding="utf8") as f:
        f.write(text)


def writeChangedTrackedFile(fixture, worktree, key):
    """Change a tracked file of the worktree and return its path, the one the manifest lists."""
    tracked = fixture.git("ls-files", cwd=worktree).split()
    if not tracked:
        raise CheckFailed("the fixture worktree has no tracked file to change")
    with open(os.path.join(worktree, tracked[0]), "a", encoding="utf8") as f:
        f.write(f"\nchanged by the interim suite for {key}\n")
    return tracked[0]


def readLedgerIfAny(world):
    try:
        return world.ledger()
    except CheckFailed:
        return None


def roundFileMismatches(stored, worktree, key):
    problems = []
    for name in ("commit.txt", "pr-body.md"):
        with open(os.path.join(worktree, ".tmp", key, name), "rb") as f:
            if stored.get(name) != f.read():
                problems.append(f"{name} on the round is not the file's bytes")
    return problems


def storeFolder(world):
    store = world.settings["issueStore"]
    if not store:
        from .config import load
        store = load().get("issueStore")
    if store:
        store = os.path.expanduser(store)
    if not store or not os.path.isdir(store):
        raise CheckFailed("no ticket store: set issueStore in the suite's config or skuld's")
    return store


def storeTickets(world):
    """{old key: parsed ticket} for every SK-*.md file in the store."""
    folder = storeFolder(world)
    names = sorted(n for n in os.listdir(folder) if n.startswith("SK-") and n.endswith(".md"))
    if not names:
        raise CheckFailed(f"the store {folder} holds no ticket files")
    tickets = {}
    for name in names:
        with open(os.path.join(folder, name), encoding="utf8") as f:
            tickets[name.split("-")[0] + "-" + name.split("-")[1]] = probes.parseTicket(f.read())
    return tickets


def storeRoundFiles(world):
    """{old key: {file name: bytes}} for every reviews/<KEY>/ folder in the store."""
    reviews = os.path.join(storeFolder(world), "reviews")
    rounds = {}
    for key in sorted(os.listdir(reviews)) if os.path.isdir(reviews) else []:
        folder = os.path.join(reviews, key)
        rounds[key] = {n: readBytes(os.path.join(folder, n)) for n in sorted(os.listdir(folder))}
    return rounds


def readBytes(path):
    with open(path, "rb") as f:
        return f.read()


def lookupByOldId(world, oldKey):
    field = world.need("oldIdFieldId")
    return world.redmine.get(
        "/issues.json", project_id=world.need("skuldProject"), status_id="*", **{f"cf_{field}": oldKey}
    )


def importedIssue(world, oldKey):
    found = lookupByOldId(world, oldKey)
    if found["total_count"] != 1:
        raise CheckFailed(f"{oldKey}: {found['total_count']} issues carry it as Old ID, expected 1")
    return found["issues"][0]


def importDifferences(world, key, ticket, botUserId):
    issue = world.redmine.get(f"/issues/{importedIssue(world, key)['id']}.json", include="journals,relations")["issue"]
    blockers = [importedIssue(world, k)["id"] for k in probes.blockerKeys(ticket["frontmatter"])]
    return probes.importMismatches(
        ticket, issue, triggers.checklistItems(issue["id"]), blockers, world.settings["statusMap"], botUserId
    )


# ---- the gate

CHECKS = [
    (1, "Start, as the signed-in user", "SK-0086", start_as_signed_in_user),
    (2, "A mismatched prefix is refused", "SK-0086", mismatched_prefix_is_refused),
    (3, "Ship stages from the manifest", "SK-0087, SK-0086", ship_stages_from_manifest),
    (4, "The round is recorded", "SK-0084", round_is_recorded),
    (5, "Done never changes the status", "SK-0086", done_never_changes_status),
    (6, "A ticked Done box is journaled", "SK-0085", ticked_done_box_is_journaled),
    (7, "A key renders as a link", "SK-0081", key_renders_as_link),
    (8, "`fixes` closes the issue", "SK-0081", fixes_closes_the_issue),
    (9, "Every store ticket is imported once", "SK-0079", every_ticket_is_imported_once),
    (10, "The import keeps every field", "SK-0079", import_keeps_every_field),
    (11, "Every round is imported", "SK-0079", every_round_is_imported),
    (12, "The bot user reaches skuld's projects only", "SK-0080", bot_reaches_skuld_projects_only),
]


def causeOf(check, world, loadError):
    """None when the check passes, else what it saw."""
    if loadError:
        return loadError
    try:
        check(world)
    except CheckFailed as error:
        return str(error)
    except Exception as error:  # noqa: BLE001 -- a probe bug or an unexpected reply is a failure with its type named
        return f"{type(error).__name__}: {error}"
    return None


def run(configPath=configuration.CONFIG_FILE, out=sys.stdout):
    loadError, world = None, None
    try:
        world = World(configuration.load(configPath))
    except configuration.NotConfigured as error:
        loadError = str(error)
    problems = [] if loadError else reset(world)
    for problem in problems:
        print(f"RESET  {problem}", file=out)
    failed = 0
    for number, title, owner, check in CHECKS:
        cause = causeOf(check, world, loadError)
        failed += cause is not None
        print(f"{'PASS' if cause is None else 'FAIL'}  {number:>2}. {title} [{owner}]" + (f": {cause}" if cause else ""), file=out)
    print(f"{len(CHECKS) - failed} of {len(CHECKS)} checks passed", file=out)
    return 1 if failed or problems else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    return run(argv[0]) if argv else run()
