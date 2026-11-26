# provenance-check: the author's manifest, .tmp/<KEY>/provenance.md, checked against the change
# (docs/architecture.md §7.1; the format is in skills/jira-issues/SKILL.md).
#
#     provenance-check [KEY] [--base REF] [--manifest PATH] [--if-code]
#
# KEY defaults to the branch's, REF to origin's default branch. --if-code, which `pr` passes, checks
# only a change that includes a Python or JavaScript file. It says what it is checking, prints each
# row's result as the row finishes and each refusal as it is found, then the count and the time. It
# exits 0 with one line per row, 1 with every refusal, each naming its row, file:line and reason,
# and 2 when it can't check at all.
# It changes nothing in the worktree: the change is read from a snapshot commit, and each revert
# runs in a temporary worktree.
import argparse
import ast
import contextlib
import functools
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import reviews
from .cli import branchKey
from .fileissues import linesOutsideFences
from .provenance_runner import NODE_SUFFIXES, ownEnvironment, runTest
from .provenance_units import OWN_ROW, Unrevertable, assignedNames, changedUnits, isPython, needsNoRow, reverted, units

# The proof each class needs besides a `test:` that fails on revert.
REQUIRED = {
    "merge-resolved": (),
    "moved": ("source",),
    "ported": ("source",),
    "rule": ("dims", "table"),
    "parser": ("spec", "case"),
    "plain": (),
}
MERGE = "merge-resolved"
# The items whose value names a test; each must pass with the change in place.
TEST_ITEMS = ("test", "agree", "inside", "outside", "case")
ITEMS = (*TEST_ITEMS, "source", "reuse", "new", "dims", "table", "exempt", "spec")
# What a moved or ported row says of each concept it handles: reused, agreed with, or found nowhere else.
CONCEPT_ITEMS = ("reuse", "agree", "new")
ITEM = re.compile(r"^([a-z]+)(?:\s+([^:]+?))?\s*:\s*(.+)$")
# Paths come out as written, and none of the repo's hooks run: a post-checkout hook has no business in
# the temporary worktree, and one that fails there would stop the check.
GIT_SETTINGS = ("-c", "core.quotePath=false", "-c", "core.hooksPath=/dev/null")
# The suffixes and the shebang of JavaScript, which `pr` checks a change for.
JAVASCRIPT_SUFFIXES = (*NODE_SUFFIXES, ".jsx")
OBJECT_NAME = re.compile(r"[0-9a-f]{40,64}")
DIMENSION = r"[^()\s][^()]*\([^()]*\)"  # name(value, value)
DIMENSIONS = re.compile(rf"\s*{DIMENSION}(?:\s*[x×*]\s*{DIMENSION})*\s*")


class ProvenanceError(Exception):
    pass


@dataclass(frozen=True)
class Change:
    """A unit that needs a row: changed against the base, or, with merge set, written by that
    merge's resolver."""

    path: str
    unit: str
    line: int
    kind: str
    merge: str = ""
    parents: tuple = ()

    @property
    def target(self):
        return self.path, self.unit

    @property
    def name(self):
        return named(self.target)

    @property
    def place(self):
        return f"{self.path}:{self.line}"


def named(target):
    path, unit = target
    return f"{path}::{unit}" if unit else path


@dataclass
class Row:
    number: int
    targets: list  # [(path, unit)]
    kind: str
    proof: list  # [(item, label, value)]

    def values(self, item):
        return [value for word, _label, value in self.proof if word == item]

    def tests(self):
        return list(dict.fromkeys(value for word, _label, value in self.proof if word in TEST_ITEMS))

    def covered(self, changes):
        """The changes this row proves, each named exactly: a merge row's are those a resolver wrote."""
        return [change for change in changes
                if change.target in self.targets and bool(change.merge) == (self.kind == MERGE)]

    def title(self, covered):
        names = ", ".join(named(target) for target in self.targets)
        places = ", ".join(dict.fromkeys(change.place for change in covered))
        return f"row {self.number} ({names}, {self.kind})" + (f" at {places}" if places else "")


# ---- git ----


def runGit(root, *args, env=None):
    return subprocess.run(["git", *GIT_SETTINGS, *args], cwd=root, capture_output=True, env=env or ownEnvironment())


def git(root, *args, env=None):
    answer = runGit(root, *args, env=env)
    if answer.returncode != 0:
        raise ProvenanceError(f"git {args[0]} failed: {decoded(answer.stderr).strip()}")
    return decoded(answer.stdout)


def decoded(raw):
    return raw.decode("utf-8", "surrogateescape")


def textAt(root, commit, path):
    """The file's text at a commit or tree, or None when it isn't there."""
    answer = runGit(root, "cat-file", "blob", f"{commit}:{path}")
    return decoded(answer.stdout) if answer.returncode == 0 else None


def mergeBase(root, baseRef, head):
    answer = runGit(root, "merge-base", baseRef, head)
    if answer.returncode != 0:
        raise ProvenanceError(f"there is no merge base with {baseRef}; run git fetch origin "
                              f"{decoded(answer.stderr).strip()}".rstrip())
    return decoded(answer.stdout).strip()


def changedPaths(root, old, new):
    return [path for path in git(root, "diff", "--name-only", "--no-renames", "-z", old, new).split("\0") if path]


def snapshot(root):
    """A commit of the worktree as it is, uncommitted and untracked files included, made with a
    throwaway index so the worktree and its index stay as they are. A merge in progress keeps both
    parents, so its resolution is checked too."""
    with tempfile.TemporaryDirectory() as folder:
        index = os.path.join(folder, "index")
        # A copy of the index, so a file added by force is in the snapshot as it will be in the
        # commit; copy2 keeps its time, which git reads to tell a changed file from an unchanged one.
        shutil.copy2(os.path.join(root, git(root, "rev-parse", "--git-path", "index").strip()), index)
        env = ownEnvironment(GIT_INDEX_FILE=index)
        git(root, "add", "-A", env=env)
        tree = git(root, "write-tree", env=env).strip()
    merging = runGit(root, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode == 0
    parents = ["-p", "HEAD"] + (["-p", "MERGE_HEAD"] if merging else [])
    return git(root, "commit-tree", tree, *parents, "-m", "provenance-check snapshot").strip()


@contextlib.contextmanager
def temporaryWorktree(root, commit):
    with tempfile.TemporaryDirectory(prefix="provenance-") as folder:
        tree = os.path.join(folder, "tree")
        git(root, "worktree", "add", "--quiet", "--detach", tree, commit)
        try:
            yield tree
        finally:
            git(root, "worktree", "remove", "--force", tree)


# ---- what needs a row ----


def diffChanges(root, base, head):
    found = []
    for path in changedPaths(root, base, head):
        new = textAt(root, head, path)
        if new is not None and not needsNoRow(path):
            changed = changedUnits(path, textAt(root, base, path), new)
            found += [Change(path, unit, line, kind) for unit, (line, kind) in changed.items()]
    return found


def mergeChanges(root, base, head):
    merges = git(root, "rev-list", "--merges", "--reverse", f"{base}..{head}").split()
    return [change for merge in merges for change in resolverChanges(root, merge, head)]


def resolverChanges(root, merge, head):
    """The units a merge's resolver wrote beyond git's merge, and head still holds. A unit is theirs
    when it differs from git's merge taking either side of every conflict, and from both parents: so
    git's own merge and a verbatim pick of one side need no row."""
    parents = tuple(git(root, "rev-list", "--parents", "-n", "1", merge).split()[1:])
    if len(parents) != 2:
        raise ProvenanceError(f"merge {merge[:7]} has {len(parents)} parents; only a two-parent merge can be checked")
    references = [mergedByGit(root, side, parents) for side in ("ours", "theirs")] + list(parents)
    paths = set.intersection(*(set(changedPaths(root, reference, merge)) for reference in references))
    found = []
    for path in sorted(paths):
        written, now = textAt(root, merge, path), textAt(root, head, path)
        if written is None or now is None or needsNoRow(path):
            continue
        theirOwn = set.intersection(*(set(changedUnits(path, textAt(root, ref, path), written)) for ref in references))
        first, second = (changedUnits(path, textAt(root, parent, path), now) for parent in parents)
        held = sorted(theirOwn & first.keys() & second.keys())
        found += [Change(path, unit, *first[unit], merge, parents) for unit in held]
    return found


def mergedByGit(root, side, parents):
    """The tree git's merge of the parents gives, every conflict resolved to one side. Exit 1 is a
    conflict that side can't settle (a file deleted on one side), which still gives a tree. git
    exits 1 for a ref it can't read too, so only a tree on the first line counts."""
    answer = runGit(root, "merge-tree", "--write-tree", "-X", side, *parents)
    tree = decoded(answer.stdout).split("\n", 1)[0]
    if answer.returncode not in (0, 1) or not OBJECT_NAME.fullmatch(tree):
        raise ProvenanceError(f"git merge-tree failed: {decoded(answer.stderr).strip()}")
    return tree


def uncovered(rows, changes):
    """A changed unit needs a row that proves it; what a resolver wrote needs a merge row."""
    proven = {change.target for row in rows for change in row.covered(changes)}
    merged = {change.target for row in rows if row.kind == MERGE for change in row.covered(changes)}
    return [unnamed(change) for change in changes if change.target not in (merged if change.merge else proven)]


def unnamed(change):
    if change.merge:
        return (f"{change.place}: merge {change.merge[:7]} wrote {change.name} beyond git's merge, "
                f"and no {MERGE} row names it")
    return f"{change.place}: {change.name} changed, and no row names it"


# ---- the manifest ----


def readManifest(text):
    """([Row], [problem]) of the manifest's table, `| Function | Class | Proof |`."""
    rows, problems = [], []
    for offset, line in linesOutsideFences(text):
        if not line.startswith("|"):
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if isHeaderOrRule(cells):
            continue
        where = f"manifest line {text.count(chr(10), 0, offset) + 1}"
        if len(cells) != 3:
            problems.append(f"{where}: a row is `| Function | Class | Proof |`")
            continue
        proof, unread = readProof(cells[2])
        rows.append(Row(len(rows) + 1, readTargets(cells[0]), cells[1].strip("`"), proof))
        problems += [f'{where}: unreadable proof item "{item}"' for item in unread]
    return rows, problems


def isHeaderOrRule(cells):
    isHeader = len(cells) > 1 and cells[1].lower() == "class"
    return isHeader or all(re.fullmatch(r":?-+:?", cell) for cell in cells)


def readTargets(cell):
    names = [name.strip() for name in cell.replace("`", "").split(",") if name.strip()]
    return [(path, unit) for path, _, unit in (name.partition("::") for name in names)]


def readProof(cell):
    proof, unread = [], []
    for item in filter(None, (part.strip() for part in cell.replace("`", "").split(";"))):
        found = ITEM.match(item)
        if found and found[1] in ITEMS:
            proof.append((found[1], found[2] or "", found[3].strip()))
        else:
            unread.append(item)
    return proof, unread


# ---- one row's proof ----


def missingParts(row):
    found = [f"a {row.kind} row needs a `{item}:` item" for item in ("test", *REQUIRED[row.kind]) if not row.values(item)]
    if row.kind in ("moved", "ported") and not any(row.values(item) for item in CONCEPT_ITEMS):
        forms = ", ".join(f"`{item} <concept>:`" for item in CONCEPT_ITEMS)
        found.append(f"a {row.kind} row needs each concept it handles, as one of {forms}")
    return found + unpairedExemptions(row)


def unpairedExemptions(row):
    """Each input path an `exempt:` item lists needs a test on each side of the exemption."""
    paths = [path.strip() for value in row.values("exempt") for path in value.split(",") if path.strip()]
    sides = {(word, label) for word, label, _value in row.proof}
    return [f'exempt path "{path}" has no `{side} {path}:` test'
            for path in paths for side in ("inside", "outside") if (side, path) not in sides]


def sharedRow(covered):
    """One row per changed function: reverting two together would let one test stand for both."""
    own = list(dict.fromkeys(change.name for change in covered if change.kind in OWN_ROW))
    return [f"names {len(own)} changed functions, and each needs its own row: {', '.join(own)}"] if len(own) > 1 else []


def unprovable(row, covered):
    """The units a row names that it can't prove."""
    proven = {change.target for change in covered}
    why = "which no merge's resolver wrote" if row.kind == MERGE else "which this change doesn't touch"
    return [f"names {named(target)}, {why}" for target in row.targets if target not in proven]


def missingReuses(row, tree):
    return [f"reuses {value}, which doesn't exist" for value in row.values("reuse") if not exists(tree, value)]


def exists(tree, target):
    path, _, unit = target.partition("::")
    file = Path(tree, path)
    return file.is_file() and (not unit or unit in units(path, file.read_text(errors="surrogateescape")))


def shortTables(row, tree):
    if row.kind != "rule" or not row.values("dims"):
        return []
    wanted = combinations(row.values("dims"))
    if not wanted:
        return ["`dims:` lists each dimension's values, as `status(open, done) x closed(unset, date)`"]
    found = []
    for table in row.values("table"):
        count = tableRows(tree, table)
        if count is None:
            found.append(f"there is no table {table}: a list, tuple, set or dict written out under that name")
        elif count < wanted:
            found.append(f"the table {table} has {count} rows, and its dimensions' product is {wanted}")
    return found


def combinations(dimensions):
    """The product of the dimensions' value counts, 0 when any of the text can't be read."""
    if not all(DIMENSIONS.fullmatch(text) for text in dimensions):
        return 0
    groups = [group for text in dimensions for group in re.findall(r"\(([^()]*)\)", text)]
    return math.prod(len({value.strip() for value in group.split(",") if value.strip()}) for group in groups)


def tableRows(tree, table):
    """The distinct rows of the literal a Python file assigns to the dotted name, or None."""
    path, _, dotted = table.partition("::")
    try:
        scope = ast.parse(Path(tree, path).read_text(errors="surrogateescape"))
    except (OSError, SyntaxError, ValueError):
        return None
    *owners, name = dotted.split(".")
    for owner in owners:
        scope = next((node for node in scope.body if getattr(node, "name", None) == owner), None)
        if scope is None:
            return None
    return next((distinctElements(node.value) for node in scope.body if name in assignedNames(node)), None)


def distinctElements(value):
    if isinstance(value, ast.Dict):
        return len({ast.dump(key) for key in value.keys if key})
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return len({ast.dump(element) for element in value.elts})
    return None


def brokenTest(test, verdict):
    if verdict.state == "missing":
        return f"names the test {test}, which doesn't exist"
    if verdict.state == "fail":
        return f"test {test} fails with the change in place: {verdict.detail}"
    return f"test {test} can't run with the change in place: {verdict.detail}"


def unproven(test, verdict, reverted):
    """Why a test run on reverted code proves nothing, or None. A failure is proof, and so is a test
    that no longer loads, since it loaded with the change in place."""
    if verdict.state in ("fail", "unloaded"):
        return None
    if verdict.state == "pass":
        return f"test {test} still passes with {reverted}"
    return f"test {test} reached no verdict with {reverted}: {verdict.detail or verdict.state}"


def restore(tree):
    git(tree, "checkout", "--quiet", "--force", "HEAD", "--", ".")
    git(tree, "clean", "--quiet", "--force", "-d", "-x")


def runClean(tree, test):
    """One test's Verdict; what it left behind is removed, so no later run can see it."""
    try:
        return runTest(tree, test)
    finally:
        restore(tree)


def revertedTexts(tree, targets, reference):
    """{path: reverted text}, all worked out before any file is written."""
    unitsByPath = {}
    for path, unit in targets:
        unitsByPath.setdefault(path, []).append(unit)
    current = {path: textAt(tree, "HEAD", path) for path in unitsByPath}
    texts = {path: reverted(path, current[path], textAt(tree, reference, path), names) for path, names in unitsByPath.items()}
    if texts == current:
        raise Unrevertable("reverting changes nothing")
    return texts


def runReverted(tree, texts, test):
    for path, text in texts.items():
        file = Path(tree, path)
        if file.is_symlink():  # never write through a link, which can point outside the tree
            file.unlink()
        file.write_bytes(text.encode("utf-8", "surrogateescape"))
    return runClean(tree, test)


def reversions(row, covered):
    """[(units, what to call them)] to revert in turn: all the row names, then its function alone,
    so that another name the row carries can't be all its test fails on."""
    own = [change.target for change in covered if change.kind in OWN_ROW]
    together = (row.targets, "the code reverted")
    alone = [([own[0]], f"only {named(own[0])} reverted")] if own and len(row.targets) > 1 else []
    return [together, *alone]


def revertProblems(row, covered, tree, base, proofs):
    parents = dict.fromkeys(parent for change in covered for parent in change.parents)
    found = []
    for reference in parents if row.kind == MERGE else [base]:
        for targets, what in reversions(row, covered):
            try:
                texts = revertedTexts(tree, targets, reference)
            except Unrevertable as error:
                found.append(f"the revert to {reference[:7]} can't be applied: {error}")
                continue
            reasons = [unproven(test, runReverted(tree, texts, test), f"{what} to {reference[:7]}") for test in proofs]
            found += [reason for reason in reasons if reason]
    return found


def rowProblems(row, covered, tree, base, baseline):
    """Why the row is refused. baseline gives a test's Verdict with the change in place."""
    if row.kind not in REQUIRED:
        return [f'unknown class "{row.kind}": one of {", ".join(REQUIRED)}']
    found = missingParts(row) + missingReuses(row, tree) + shortTables(row, tree)
    broken = [test for test in row.tests() if baseline(test).state != "pass"]
    found += [brokenTest(test, baseline(test)) for test in broken]
    unrevertable = unprovable(row, covered) + sharedRow(covered)
    if unrevertable:
        return found + unrevertable
    proofs = [test for test in row.values("test") if test not in broken]
    return found + (revertProblems(row, covered, tree, base, proofs) if proofs else [])


# ---- the check ----


def untold(line, refused):
    """For a caller that reads the Report instead of watching the check run."""


@dataclass
class Report:
    """What the check found, told line by line as it is found: tell(line, whether it refuses)."""

    tell: Callable = untold
    passed: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    def passes(self, line, progress):
        self.passed.append(line)
        self.tell(f"{line}  {progress}", False)

    def refuses(self, problems):
        for problem in problems:
            self.problems.append(problem)
            self.tell(problem, True)


def check(root, baseRef, manifest, tell=untold):
    report = Report(tell)
    head = snapshot(root)
    base = mergeBase(root, baseRef, head)
    changes = diffChanges(root, base, head) + mergeChanges(root, base, head)
    hasManifest = manifest.is_file()
    rows, problems = readManifest(manifest.read_text(encoding="utf-8")) if hasManifest else ([], [])
    if changes and not hasManifest:
        problems.append(f"there is no manifest at {manifest}")
    report.refuses(problems + uncovered(rows, changes))
    if rows:
        tell(f"provenance-check: {len(rows)} rows, {len(changes)} changed units against {base[:7]}", False)
        with temporaryWorktree(root, head) as tree:
            proveRows(report, rows, changes, tree, base)
        report.refuses(changedSince(root, head))
    return report


def proveRows(report, rows, changes, tree, base):
    baseline = functools.cache(lambda test: runClean(tree, test))
    for row in rows:
        started = time.monotonic()
        covered = row.covered(changes)
        reasons = rowProblems(row, covered, tree, base, baseline)
        report.refuses([f"{row.title(covered)}: {reason}" for reason in reasons])
        if not reasons:
            progress = f"[{row.number} of {len(rows)}, {time.monotonic() - started:.1f}s]"
            report.passes(f"ok {row.title([])}: fails on revert: {', '.join(row.values('test'))}", progress)


def changedSince(root, checked):
    """A refusal when the worktree no longer holds what was checked: `pr` commits what is there when
    the check ends, which can be minutes after its snapshot."""
    if treeOf(root, snapshot(root)) == treeOf(root, checked):
        return []
    return ["the worktree changed while the check ran; run it again"]


def treeOf(root, commit):
    return git(root, "rev-parse", f"{commit}^{{tree}}").strip()


def includesCode(root, baseRef):
    """Whether the change includes a Python or JavaScript file."""
    head = snapshot(root)
    base = mergeBase(root, baseRef, head)
    return any(isCode(path, textAt(root, head, path) or textAt(root, base, path) or "")
               for path in changedPaths(root, base, head))


def isCode(path, text):
    return isPython(path, text) or isJavaScript(path, text)


def isJavaScript(path, text):
    shebang = text.split("\n", 1)[0]
    return path.endswith(JAVASCRIPT_SUFFIXES) or (shebang.startswith("#!") and "node" in shebang)


def defaultBase(root):
    hasHead = runGit(root, "rev-parse", "--verify", "--quiet", "origin/HEAD").returncode == 0
    return "origin/HEAD" if hasHead else "origin/main"


def manifestPath(root, key):
    review = reviews.review()
    return Path(review.provenance_path(review.rounds_folder(Path(root), key)))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="provenance-check", description="Check .tmp/<KEY>/provenance.md against the change.")
    parser.add_argument("key", nargs="?", help="the issue; the branch's by default")
    parser.add_argument("--base", help="the ref the change is against; origin's default branch by default")
    parser.add_argument("--manifest", type=Path, help="the manifest; .tmp/<KEY>/provenance.md by default")
    parser.add_argument("--if-code", action="store_true", help="check only a change with a Python or JavaScript file")
    args = parser.parse_args(argv)
    root = git(os.getcwd(), "rev-parse", "--show-toplevel").strip()
    base = args.base or defaultBase(root)
    if args.if_code and not includesCode(root, base):
        print("provenance-check: not checked; the change has no Python or JavaScript file")
        return 0
    key = args.key or branchKey(git(root, "branch", "--show-current").strip())
    if not (args.manifest or key):
        parser.error("the branch names no issue; give KEY or --manifest")
    manifest = args.manifest.resolve() if args.manifest else manifestPath(root, key)
    started = time.monotonic()
    report = check(root, base, manifest, printLine)
    took = f"in {time.monotonic() - started:.0f}s"
    if report.problems:
        printLine(f"provenance-check: refused, {len(report.problems)} problem(s) {took}", True)
        return 1
    passed = f"{len(report.passed)} rows pass {took}" if report.passed else "nothing needs a row"
    printLine(f"provenance-check: {passed}", False)
    return 0


def printLine(line, refused):
    # Flushed, so a line shows as it is found when the output goes through a pipe too.
    print(line, file=sys.stderr if refused else sys.stdout, flush=True)
