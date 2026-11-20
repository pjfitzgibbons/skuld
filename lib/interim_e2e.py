# The interim's end-to-end suite: the 12 checks of docs/adr/0005-redmine-is-the-tracker.md §3.9
# (anchor interim-end), each an action and the probes that read the result. The suite is the gate
# that ends the interim: it fails until the Ruby core works, each check for its real reason, and
# turns green with no change here once the triggers in lib/interim_triggers.py exist.
# Run it with bin/test-interim-e2e.
import os
import sys
from collections import namedtuple

from . import interim_config as configuration
from . import interim_probes as probes
from . import interim_triggers as triggers
from .config import load as loadSkuldsConfig
from .interim_world import CheckFailed, Fixture, PendingTrigger, Redmine, keychainSecret

TASK_SUBJECT = "End-to-end task"
WRONG_PREFIX = "XX"
UNLISTED_FILE = "e2e-unlisted.txt"
STORE_TICKET_FOLDERS = ("", "active", "archive")


class Unconfigured(CheckFailed):
    """A setting the suite needs is not set: the check that needs it names it."""


class World:
    """The run's settings, clients and the facts one check leaves for a later one."""

    def __init__(self, settings):
        self.settings = settings
        self.redmine = Redmine(settings["redmineUrl"], settings["botKeyService"], settings["sessionService"])
        self.state = {}
        self.guardedFixture = None

    def need(self, key):
        try:
            return configuration.require(self.settings, key)
        except configuration.NotConfigured as error:
            raise Unconfigured(str(error))

    @property
    def number(self):
        return self.need("taskNumber")

    @property
    def key(self):
        return f"SK-{self.number}"

    @property
    def fixture(self):
        """The fixture repo, guarded once per run."""
        if self.guardedFixture is None:
            fixture = Fixture(self.need("fixtureRepo"))
            fixture.requireNotSkuldsRepo()
            self.guardedFixture = fixture
        return self.guardedFixture

    def task(self, include=None):
        """The end-to-end task, read as the bot user. Also proves the instance and the task exist."""
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




def describeFailure(error):
    """What a failure says: a CheckFailed's own text, any other error with its type named."""
    if isinstance(error, CheckFailed):
        return str(error)
    return f"{type(error).__name__}: {error}"


def raiseIfAny(problems):
    if problems:
        raise CheckFailed("; ".join(problems))


# ---- the reset (§3.9, "Each run first resets it")

ResetProblem = namedtuple("ResetProblem", "text blocksChecks")


def reset(world):
    """Restore the task to its starting state. Returns what could not be restored, each cause once.
    A step that waits on unbuilt work (a PendingTrigger) or on a setting not yet made leaves nothing
    in an unknown state, since nothing was written through it; any other failure blocks the checks."""
    problems = {}
    for step in (resetTask, resetFixture):
        try:
            step(world)
        except (PendingTrigger, Unconfigured) as error:
            problems.setdefault(str(error), ResetProblem(str(error), False))
        except Exception as error:  # noqa: BLE001 -- a bad reply or probe bug is a reset problem with its type named
            problems.setdefault(describeFailure(error), ResetProblem(describeFailure(error), True))
    return list(problems.values())


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
    branches = world.fixture.branches(world.number)
    if len(branches) != 1:
        raise CheckFailed(f"`git branch --list '*/SK-{world.number}-*'` lists {branches}, expected one branch")
    world.onlyWorktree()


def mismatched_prefix_is_refused(world):
    before = world.task("journals")
    refusal = triggers.startRefusal(f"{WRONG_PREFIX}-{world.number}")
    after = world.task("journals")
    problems = []
    if not refusal:
        problems.append("the plugin showed no refusal")
    problems += unchangedProblems(before, after)
    problems += leftoverProblems(world.fixture, world.number, WRONG_PREFIX)
    raiseIfAny(problems)


def unchangedProblems(before, after):
    if after["status"] == before["status"] and probes.journalCount(after) == probes.journalCount(before):
        return []
    return [
        f"status/journals changed: {before['status']['name']}/{probes.journalCount(before)} "
        f"-> {after['status']['name']}/{probes.journalCount(after)}"
    ]


def leftoverProblems(fixture, number, prefix):
    """The branches and worktrees the fixture holds for `<prefix>-<number>`, which a refusal leaves none of."""
    problems = [f"branch {b} exists" for b in fixture.branches(number, prefix)]
    problems += [f"worktree {w} exists" for w in fixture.worktrees(number, prefix)]
    return problems


def ship_uploads_the_round_as_a_draft(world):
    world.task()
    worktree = world.onlyWorktree()
    fixture = world.fixture
    sent = prepareRound(world, fixture, worktree)
    triggers.ship(world.key, worktree, sent["manifest"])
    sent["staged"] = fixture.staged(worktree)
    problems = headProblems(fixture, worktree, sent["head"])
    problems += stagedProblems(fixture, worktree, sent["manifest"])
    problems += storedRoundProblems(world, sent, world.ledger())
    raiseIfAny(problems)


def prepareRound(world, fixture, worktree):
    """Leave the worktree as the author's step does: a changed file, the manifest listing it, and
    commit.txt and pr-body.md with known bytes. The copies kept in world.state are what checks 3 and
    4 compare against."""
    sent = {
        "manifest": [writeChangedTrackedFile(fixture, worktree, world.key)],
        "files": writeRoundFiles(worktree, world.key),
        "head": fixture.head(worktree),
        "ledgerBefore": readLedgerIfAny(world) or {},
    }
    write(os.path.join(worktree, UNLISTED_FILE), f"not in the manifest of {world.key}\n")
    world.state["round"] = sent
    return sent


def headProblems(fixture, worktree, headBefore):
    return ["HEAD moved: Ship committed"] if fixture.head(worktree) != headBefore else []


def stagedProblems(fixture, worktree, manifest):
    problems = []
    missing, unexpected = probes.compareStaged(fixture.staged(worktree), manifest)
    if missing or unexpected:
        problems.append(f"staged files differ from the manifest: missing {missing}, unexpected {unexpected}")
    if probes.porcelainStatusOf(fixture.porcelain(worktree), UNLISTED_FILE) != "??":
        problems.append(f"{UNLISTED_FILE} is no longer untracked")
    return problems


def storedRoundProblems(world, sent, ledger):
    added = probes.roundsAdded(sent["ledgerBefore"], ledger)
    if added != 1:
        return [f"{added} rounds were added by Ship, expected 1"]
    latest = ledger["review_rounds"][-1]
    problems = roundFileMismatches(triggers.storedRoundFiles(ledger, latest), sent["files"])
    status = triggers.roundStatus(ledger, latest)
    if status != "draft":
        problems.append(f"the round's status is {status!r}, expected 'draft'")
    if not probes.endsWithPrCommand(triggers.reportPageText(ledger, latest), world.key):
        problems.append(f"the round's report page does not end with \"run `pr {world.key}`\"")
    return problems


def pr_sends_the_round(world):
    world.task()
    sent = roundFromCheck3(world)
    worktree = world.onlyWorktree()
    fixture = world.fixture
    branch = fixture.branches(world.number)[0]
    triggers.pr(world.key, worktree)
    problems = committedProblems(fixture, worktree, sent)
    problems += pushedProblems(fixture, worktree, branch)
    problems += pullRequestProblems(fixture, branch, sent)
    problems += sentProblems(world, sent)
    raiseIfAny(problems)


def roundFromCheck3(world):
    sent = world.state.get("round")
    if sent is None:
        raise CheckFailed("check 3 did not prepare the round, so check 4 has no round to send")
    return sent


def committedProblems(fixture, worktree, sent):
    problems = []
    if fixture.head(worktree) == sent["head"]:
        problems.append("HEAD did not move: `pr` committed nothing")
    if fixture.headMessage(worktree) != sent["files"]["commit.txt"].decode("utf8").strip():
        problems.append("the commit message is not the round's commit.txt")
    if sorted(fixture.committedFiles(worktree)) != sorted(sent["staged"]):
        problems.append(f"the commit holds {fixture.committedFiles(worktree)}, not the staged {sent['staged']}")
    return problems


def pushedProblems(fixture, worktree, branch):
    if fixture.remoteHead(branch) != fixture.head(worktree):
        return [f"origin's {branch} is not the new HEAD"]
    return []


def pullRequestProblems(fixture, branch, sent):
    pullRequests = fixture.openPullRequests(branch)
    if len(pullRequests) != 1:
        return [f"{len(pullRequests)} open PRs on {branch}, expected 1"]
    if pullRequests[0]["body"].strip() != sent["files"]["pr-body.md"].decode("utf8").strip():
        return ["the PR body is not the round's pr-body.md"]
    return []


def sentProblems(world, sent):
    ledger = world.ledger()
    added = probes.roundsAdded(sent["ledgerBefore"], ledger)
    if added != 1:
        return [f"{added} rounds are new since before Ship, expected only check 3's"]
    status = triggers.roundStatus(ledger, ledger["review_rounds"][-1])
    return [] if status == "sent" else [f"the round's status is {status!r}, expected 'sent'"]


def done_never_changes_status(world):
    before = world.task()
    worktree = world.onlyWorktree()
    fixture = world.fixture
    fixture.mergePullRequestOf(fixture.branches(world.number)[0])
    triggers.done(world.key)
    problems = []
    if worktree in fixture.worktrees(world.number):
        problems.append(f"`git worktree list` still shows {worktree}")
    after = world.task()
    if after["status"] != before["status"]:
        problems.append(f"status went from {before['status']['name']!r} to {after['status']['name']!r}")
    raiseIfAny(problems)


def ticked_done_box_is_journaled(world):
    world.task()
    item = triggers.createChecklistItem(world.number, CHECKLIST_ITEM)
    baseline = probes.journalCount(world.task("journals"))
    triggers.tickChecklistItem(world.key, item)
    issue = world.task("journals")
    newest = probes.newestJournal(issue["journals"])
    problems = []
    if probes.journalCount(issue) != baseline + 1:
        problems.append(f"journals went from {baseline} to {probes.journalCount(issue)}, expected one more")
    if probes.journalUserId(newest) != world.signedInUserId():
        problems.append(f"newest journal is by user {probes.journalUserId(newest)}, expected the signed-in user")
    if not probes.journalMentions(newest, CHECKLIST_ITEM):
        problems.append(f"the newest journal does not name the ticked item {CHECKLIST_ITEM!r}")
    if not any(i["text"] == CHECKLIST_ITEM and i["ticked"] for i in triggers.checklistItems(world.number)):
        problems.append("the checklist API does not read the item back as ticked")
    raiseIfAny(problems)


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
    problems = duplicateTicketProblems(storeFolder(world))
    problems += collectProblems(tickets, lambda key, ticket: importCountProblems(world, key))
    if problems:
        raise CheckFailed(f"{len(problems)} of {len(tickets)} tickets: {probes.firstFailures(problems)}")


def importCountProblems(world, key):
    total = lookupByOldId(world, key)["total_count"]
    return [f"{total} issues carry it as Old ID"] if total != 1 else []


def import_keeps_every_field(world):
    world.task()
    tickets = storeTickets(world)
    botUserId = world.redmine.get("/users/current.json")["user"]["id"]
    problems = collectProblems(tickets, lambda key, ticket: importDifferences(world, key, ticket, botUserId))
    if problems:
        raise CheckFailed(f"{len(problems)} differences: {probes.firstFailures(problems)}")


def every_round_is_imported(world):
    world.task()
    problems = collectProblems(storeRoundFiles(world), lambda key, files: roundImportProblems(world, key, files))
    if problems:
        raise CheckFailed(probes.firstFailures(problems))


def roundImportProblems(world, key, storeFiles):
    """What differs between a store round folder and the imported issue's rounds: its files and its findings."""
    files, findings = probes.splitRound(storeFiles)
    ledger = world.ledger(importedIssue(world, key)["id"])
    rows = [(row, triggers.storedRoundFiles(ledger, row)) for row in ledger.get("review_rounds", [])]
    problems = [
        f"{name} is on no imported round"
        for name, content in files.items()
        if not any(stored.get(name) == content for _, stored in rows)
    ]
    if findings is not None and not problems:
        problems += findingsProblems(ledger, findings, roundHoldingFiles(rows, files))
    return problems


def roundHoldingFiles(rows, files):
    """The imported round whose stored files all equal the store round's, or None."""
    for row, stored in rows:
        if holdsOnly(stored, files):
            return row
    return None


def holdsOnly(stored, files):
    """True when the round stored some files and each equals the store round's. An empty round
    matches no store round."""
    return bool(stored) and all(files.get(name) == content for name, content in stored.items())


def findingsProblems(ledger, findings, row):
    if row is None:
        return ["no imported round holds its files, so its findings cannot be compared"]
    imported = triggers.storedFindings(ledger, row)
    if imported == findings:
        return []
    missing = [f for f in findings if f not in imported]
    return [f"findings differ: missing {missing}, store has {len(findings)} and the round {len(imported)}"]


def bot_reaches_skuld_projects_only(world):
    user = world.redmine.get("/users/current.json", include="memberships")["user"]
    actual = probes.membershipProjectNames(user)
    allowed = sorted(world.need("skuldProjectNames"))
    if not actual or actual != allowed:
        raise CheckFailed(f"the bot user's memberships are {actual}, expected {allowed}")


# ---- helpers the checks share

CHECKLIST_ITEM = "Suite item"
ROUND_FILES = ("commit.txt", "pr-body.md")


def collectProblems(items, examine):
    """Run `examine(key, item)` on every item, each key's problems named by key. Any failure for
    one key is that key's problem, with its type named when it is not a CheckFailed, so the rest are
    still examined."""
    problems = []
    for key in sorted(items):
        try:
            problems += [f"{key}: {message}" for message in examine(key, items[key])]
        except Exception as error:  # noqa: BLE001 -- a malformed reply is that key's problem
            problems.append(f"{key}: {describeFailure(error)}")
    return problems


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


def writeRoundFiles(worktree, key):
    """Write commit.txt and pr-body.md into the worktree's .tmp/<key>/ and return {name: bytes}."""
    folder = os.path.join(worktree, ".tmp", key)
    os.makedirs(folder, exist_ok=True)
    files = {
        "commit.txt": f"Change made by the interim suite for {key}\n".encode(),
        "pr-body.md": f"The round the interim suite made for {key}.\n".encode(),
    }
    for name, content in files.items():
        with open(os.path.join(folder, name), "wb") as f:
            f.write(content)
    return files


def readLedgerIfAny(world):
    try:
        return world.ledger()
    except CheckFailed:
        return None


def roundFileMismatches(stored, kept):
    return [f"{name} on the round is not the file's bytes" for name in ROUND_FILES if stored.get(name) != kept[name]]


def storeFolder(world):
    folder = os.path.expanduser(configuredStore(world) or "")
    if not folder or not os.path.isdir(folder):
        raise CheckFailed("no ticket store: set issueStore in the suite's config or skuld's")
    return folder


def configuredStore(world):
    return world.settings["issueStore"] or skuldsStore()


def skuldsStore():
    return loadSkuldsConfig().get("issueStore")


def storeTickets(world):
    """{old key: parsed ticket} for every ticket file in the store, open and closed."""
    paths = storeTicketPaths(storeFolder(world))
    if not paths:
        raise CheckFailed(f"the store {storeFolder(world)} holds no ticket files")
    return {key: parseTicketFile(path) for key, path in sorted(paths.items())}


def storeTicketPaths(folder):
    """{key: path} for the ticket files in the store's root, active/ and archive/. A key in more
    than one place keeps its first path; duplicateTicketProblems names the rest."""
    return {key: found[0] for key, found in ticketPathsByKey(folder).items()}


def ticketPathsByKey(folder):
    """{key: [every path holding it]}, the root first, then active/, then archive/."""
    paths = {}
    for sub in STORE_TICKET_FOLDERS:
        where = os.path.join(folder, sub)
        for name in sorted(os.listdir(where)) if os.path.isdir(where) else []:
            key = probes.ticketKey(name)
            if key:
                paths.setdefault(key, []).append(os.path.join(where, name))
    return paths


def duplicateTicketProblems(folder):
    """One problem for each key the store holds in more than one file, naming every path."""
    return [
        f"{key}: in the store twice, at {' and '.join(found)}"
        for key, found in sorted(ticketPathsByKey(folder).items())
        if len(found) > 1
    ]


def parseTicketFile(path):
    return probes.parseTicket(readBytes(path).decode("utf8"))


def storeRoundFiles(world):
    """{old key: {path under the folder: bytes}} for every reviews/<KEY>/ folder in the store."""
    reviews = os.path.join(storeFolder(world), "reviews")
    keys = sorted(os.listdir(reviews)) if os.path.isdir(reviews) else []
    return {key: roundFilesIn(os.path.join(reviews, key)) for key in keys if os.path.isdir(os.path.join(reviews, key))}


def roundFilesIn(folder):
    """Every file under `folder`, replies/ included, keyed by its relative path."""
    files = {}
    for where, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(where, name)
            files[os.path.relpath(path, folder)] = readBytes(path)
    return files


def readBytes(path):
    with open(path, "rb") as f:
        return f.read()


def lookupByOldId(world, oldKey):
    """The issues carrying `oldKey` as Old ID, asked once per run."""
    lookups = world.state.setdefault("oldIdLookups", {})
    if oldKey not in lookups:
        field = world.need("oldIdFieldId")
        lookups[oldKey] = world.redmine.get(
            "/issues.json", project_id=world.need("skuldProject"), status_id="*", **{f"cf_{field}": oldKey}
        )
    return lookups[oldKey]


def importedIssue(world, oldKey):
    found = lookupByOldId(world, oldKey)
    if found["total_count"] != 1:
        raise CheckFailed(f"{found['total_count']} issues carry it as Old ID, expected 1")
    return found["issues"][0]


def importDifferences(world, key, ticket, botUserId):
    issue = world.redmine.get(f"/issues/{importedIssue(world, key)['id']}.json", include="journals,relations")["issue"]
    blockers = [importedIssue(world, k)["id"] for k in probes.blockerKeys(ticket["frontmatter"])]
    return probes.importMismatches(
        ticket, issue, triggers.checklistItems(issue["id"]), blockers, world.settings["statusMap"], botUserId
    )


# ---- the gate

CHECKS = [
    (1, "Start, from the issue's `vscode://` link", "SK-0086", start_as_signed_in_user),
    (2, "A mismatched prefix is refused", "SK-0086", mismatched_prefix_is_refused),
    (3, "Ship uploads the round as a draft", "SK-0087, SK-0086", ship_uploads_the_round_as_a_draft),
    (4, "`pr` sends the round and marks it sent", "SK-0087, SK-0084", pr_sends_the_round),
    (5, "Done never changes the status", "SK-0086", done_never_changes_status),
    (6, "A ticked Done box is journaled", "SK-0085", ticked_done_box_is_journaled),
    (7, "A key renders as a link", "SK-0081", key_renders_as_link),
    (8, "`fixes` closes the issue", "SK-0081", fixes_closes_the_issue),
    (9, "Every store ticket is imported once", "SK-0079", every_ticket_is_imported_once),
    (10, "The import keeps every field", "SK-0079", import_keeps_every_field),
    (11, "Every round is imported", "SK-0079", every_round_is_imported),
    (12, "The bot user reaches skuld's projects only", "SK-0080", bot_reaches_skuld_projects_only),
]

NOT_RUN = "not run: the reset left the task in an unknown state"


def causeOf(check, world, loadError):
    """None when the check passes, else what it saw."""
    if loadError:
        return loadError
    try:
        check(world)
    except Exception as error:  # noqa: BLE001 -- a probe bug or an unexpected reply is a failure with its type named
        return describeFailure(error)
    return None


def run(configPath=configuration.CONFIG_FILE, out=sys.stdout):
    loadError, world = None, None
    try:
        world = World(configuration.load(configPath))
    except configuration.NotConfigured as error:
        loadError = str(error)
    problems = [] if loadError else reset(world)
    for problem in problems:
        print(f"RESET  {problem.text}", file=out)
    resetBlocked = any(problem.blocksChecks for problem in problems)
    failed = 0
    for number, title, owner, check in CHECKS:
        cause = NOT_RUN if resetBlocked else causeOf(check, world, loadError)
        failed += cause is not None
        label = "PASS" if cause is None else "SKIP" if cause == NOT_RUN else "FAIL"
        print(f"{label}  {number:>2}. {title} [{owner}]" + (f": {cause}" if cause else ""), file=out)
    print(f"{len(CHECKS) - failed} of {len(CHECKS)} checks passed", file=out)
    return 1 if failed or problems else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    return run(argv[0]) if argv else run()
