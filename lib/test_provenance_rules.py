# Each decision provenance-check makes, as a table with a row for every combination of its inputs,
# and the outside formats it reads, case by case. lib/test_provenance.py runs the whole check.
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lib import provenance
from lib.provenance import Change, Row
from lib.provenance_runner import TIMEOUT, Verdict, resultVerdict, runTest, tapVerdict
from lib.provenance_units import (Unrevertable, changedUnits, isCode, isPython, needsNoRow, reverted, units)
from lib.test_provenance import Repo

DIFF = Change("a.py", "f", 1, "function")
MERGED = Change("a.py", "f", 1, "function", "abc1234", ("p1", "p2"))


def row(kind, *proof, targets=(("a.py", "f"),)):
    return Row(1, list(targets), kind, list(proof))


class Table(unittest.TestCase):
    def check_table(self, cases, answer):
        for *inputs, expected in cases:
            with self.subTest(inputs=inputs):
                self.assertEqual(answer(*inputs), expected)


class NeedsNoRow(Table):
    # folder x file name
    CASES = [
        ("lib/", "keys.py", False), ("lib/", "notes.md", True), ("lib/", "test_keys.py", True), ("lib/", "uv.lock", True),
        ("e2e/", "keys.py", True), ("e2e/", "notes.md", True), ("e2e/", "test_keys.py", True), ("e2e/", "uv.lock", True),
        (".vscode/", "keys.py", True), (".vscode/", "notes.md", True), (".vscode/", "test_keys.py", True),
        (".vscode/", "uv.lock", True),
    ]

    def test_tests_prose_and_inert_files_need_no_row(self):
        self.check_table(self.CASES, lambda folder, name: needsNoRow(folder + name))

    def test_each_inert_file_needs_no_row(self):
        for name in ("Gemfile.lock", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", ".terraform.lock.hcl",
                     "go.sum", ".gitignore", ".dockerignore", ".vscodeignore", "notes.txt", "a.PNG", "a.jpeg", "a.jpg",
                     "a.gif", "a.svg", "a.webp", "a.ico"):
            self.assertTrue(needsNoRow(f"lib/{name}"), name)

    def test_configuration_templates_and_scripts_still_need_a_row(self):
        for name in ("store.yml", "pyproject.toml", "package.json", "settings.json", "base.html", "pr", "run.sh",
                     "lock.py", "ignore.py", "gitignore", "vscode/plan.js"):
            self.assertFalse(needsNoRow(f"bin/{name}"), name)

    def test_each_name_a_test_file_takes(self):
        for name in ("conftest.py", "keys_test.py", "store_sandbox.py", "plan.test.js", "plan.spec.mjs"):
            self.assertTrue(needsNoRow(f"lib/{name}"), name)
        for name in ("keys.test.json", "protest.py", "test-plan-window-e2e"):
            self.assertFalse(needsNoRow(f"bin/{name}"), name)


class IsPython(Table):
    # suffix x first line
    CASES = [
        (".py", "#!/usr/bin/env python3", True), (".py", "#!/bin/sh", True), (".py", "import os", True),
        ("", "#!/usr/bin/env python3", True), ("", "#!/bin/sh", False), ("", "import os", False),
    ]

    def test_a_python_file_is_named_or_run_as_one(self):
        self.check_table(self.CASES, lambda suffix, first: isPython(f"bin/tool{suffix}", f"{first}\n"))


class CodeLines(Table):
    # line x the language's comment mark
    CASES = [
        ("", "#", False), ("", None, False),
        ("# note", "#", False), ("# note", None, True),
        ("#!/bin/sh", "#", True), ("#!/bin/sh", None, True),
        ("echo", "#", True), ("echo", None, True),
    ]

    def test_blank_and_comment_lines_are_no_code(self):
        self.check_table(self.CASES, isCode)


class IncludesCode(Table):
    # the changed file's language
    CASES = [("a.py", "", True), ("a.mjs", "", True), ("a.jsx", "", True), ("bin/tool", "#!/usr/bin/env python3\n", True),
             ("bin/tool", "#!/usr/bin/env node\n", True), ("bin/tool", "#!/bin/sh\n", False), ("a.ts", "", False)]

    def test_python_and_javascript_are_code_the_check_can_run(self):
        self.check_table(self.CASES, provenance.isCode)


class IsJavaScript(Table):
    # suffix x shebang
    CASES = [(".jsx", "#!/usr/bin/env node", True), (".jsx", "#!/bin/sh", True),
             ("", "#!/usr/bin/env node", True), ("", "#!/bin/sh", False)]

    def test_a_javascript_file_is_named_or_run_as_one(self):
        self.check_table(self.CASES, lambda suffix, shebang: provenance.isJavaScript(f"bin/tool{suffix}", f"{shebang}\n"))


class MissingParts(Table):
    # class x the proof items given: how many things are missing
    EVERY = [("test", "", "t"), ("source", "", "s"), ("new", "c", "grep"), ("dims", "", "a(1)"),
             ("table", "", "t::T"), ("spec", "", "s"), ("case", "c", "t")]
    CASES = [
        ("merge-resolved", "none", 1), ("merge-resolved", "every", 0),
        ("moved", "none", 3), ("moved", "every", 0),
        ("ported", "none", 3), ("ported", "every", 0),
        ("rule", "none", 3), ("rule", "every", 0),
        ("parser", "none", 3), ("parser", "every", 0),
        ("plain", "none", 1), ("plain", "every", 0),
    ]

    def test_each_class_needs_its_proof(self):
        self.check_table(self.CASES, lambda kind, given: len(
            provenance.missingParts(row(kind, *(self.EVERY if given == "every" else [])))))


class ExemptionSides(Table):
    # inside test x outside test
    CASES = [
        (True, True, []), (True, False, ["outside"]), (False, True, ["inside"]), (False, False, ["inside", "outside"]),
    ]

    def test_an_exempt_path_needs_both_sides(self):
        def missing(inside, outside):
            sides = [("inside", "log", "t")] * inside + [("outside", "log", "t")] * outside
            found = provenance.unpairedExemptions(row("rule", ("exempt", "", "log"), *sides))
            return [side for side in ("inside", "outside") if any(f"`{side} log:`" in each for each in found)]

        self.check_table(self.CASES, missing)


class Verdicts(Table):
    # the verdict on reverted code: is it proof?
    CASES = [("fail", True), ("unloaded", True), ("pass", False), ("missing", False), ("error", False)]

    def test_only_a_failure_or_a_test_that_stops_loading_is_proof(self):
        self.check_table(self.CASES, lambda state: provenance.unproven("t", Verdict(state), "reverted") is None)

    # a verdict with the change in place that is no pass, and what the refusal says
    BROKEN = [("missing", "doesn't exist"), ("fail", "fails with"), ("unloaded", "can't run"), ("error", "can't run")]

    def test_a_test_that_does_not_pass_with_the_change_is_named_with_why(self):
        for state, says in self.BROKEN:
            with self.subTest(state=state):
                self.assertIn(says, provenance.brokenTest("lib/t.py::T", Verdict(state, "why")))


class Coverage(Table):
    # the change x the row's class: does the row cover it, and does it answer for it?
    CASES = [
        (DIFF, "plain", (True, True)), (DIFF, "merge-resolved", (False, False)),
        (MERGED, "plain", (False, False)), (MERGED, "merge-resolved", (True, True)),
    ]

    def test_a_resolvers_code_needs_a_merge_row(self):
        def answer(change, kind):
            rows = [row(kind)]
            return bool(rows[0].covered([change])), not provenance.uncovered(rows, [change])

        self.check_table(self.CASES, answer)

    def test_a_merge_row_answers_the_diff_change_of_what_its_resolver_wrote(self):
        self.assertEqual(provenance.uncovered([row("merge-resolved")], [DIFF, MERGED]), [])
        self.assertEqual(len(provenance.uncovered([row("plain")], [DIFF, MERGED])), 1)

    def test_a_name_is_covered_only_exactly(self):
        self.assertEqual(row("plain", targets=[("a.py", "")]).covered([DIFF]), [])

    # the row's class x whether the change covers what it names
    NAMED = [
        ("plain", True, []), ("plain", False, ["doesn't touch"]),
        ("merge-resolved", True, []), ("merge-resolved", False, ["resolver wrote"]),
    ]

    def test_a_row_naming_what_it_cannot_prove_is_told_why(self):
        def reasons(kind, covered):
            found = provenance.unprovable(row(kind), [DIFF] if covered else [])
            return [why for why in ("doesn't touch", "resolver wrote") if any(why in each for each in found)]

        self.check_table(self.NAMED, reasons)


class OneFunction(Table):
    # changed functions the row covers
    CASES = [(0, False), (1, False), (2, True)]

    def test_a_row_names_one_changed_function(self):
        functions = [Change("a.py", name, 1, "function") for name in "fg"]
        self.check_table(self.CASES, lambda count: bool(provenance.sharedRow(functions[:count])))


class Reversions(Table):
    # a function among them x other names beside it: how many reverts, and what the last reverts
    CASES = [
        (False, False, (1, 1)), (False, True, (1, 2)),
        (True, False, (1, 1)), (True, True, (2, 1)),
    ]

    def test_a_function_with_other_names_is_also_reverted_alone(self):
        def reverts(hasFunction, hasOthers):
            kind = "function" if hasFunction else "name"
            covered = [Change("a.py", "f", 1, kind)] + ([Change("a.py", "N", 2, "name")] if hasOthers else [])
            found = provenance.reversions(row("plain", targets=[each.target for each in covered]), covered)
            return len(found), len(found[-1][0])

        self.check_table(self.CASES, reverts)


class Dimensions(Table):
    # one `dims:` value, and the rows its table needs (0: unreadable)
    CASES = [
        ("a(1, 2)", 2), ("a(1, 2) x b(3, 4, 5)", 6), ("a given(1) × b(2, 3)", 2), ("a(1, 1)", 1),
        ("--flag(0, 1) x origin/HEAD(set)", 2), ("a(1, f(2))", 0), ("a(1) x b", 0), ("a(1) b(2)", 0), ("a()", 0), ("a", 0),
    ]

    def test_the_product_of_the_value_counts(self):
        self.check_table(self.CASES, lambda text: provenance.combinations([text]))

    # class x dims
    SHORT = [
        ("rule", None, 0), ("rule", "a", 1), ("rule", "a(1, 2)", 1),
        ("plain", None, 0), ("plain", "a", 0), ("plain", "a(1, 2)", 0),
    ]

    def test_only_a_rule_row_has_its_table_counted(self):
        with TemporaryDirectory() as tree:
            Path(tree, "t.py").write_text("CASES = [1]\n")

            def problems(kind, dims):
                proof = [("table", "", "t.py::CASES")] + ([("dims", "", dims)] if dims else [])
                return len(provenance.shortTables(row(kind, *proof), tree))

            self.check_table(self.SHORT, problems)

    def test_a_table_is_a_literal_counted_by_its_distinct_rows(self):
        source = ("LIST = [1, 1, 2]\nTUPLE = (1, 2)\nSET = {1}\nMAP = {1: 2, 3: 4}\nBUILT = list(range(9))\n"
                  "class A:\n    CASES = [1]\n")
        with TemporaryDirectory() as tree:
            Path(tree, "t.py").write_text(source)
            Path(tree, "broken.py").write_text("CASES = [\n")
            found = {name: provenance.tableRows(tree, f"t.py::{name}")
                     for name in ("LIST", "TUPLE", "SET", "MAP", "BUILT", "A.CASES", "CASES", "B.CASES")}
            self.assertEqual(found, {"LIST": 2, "TUPLE": 2, "SET": 1, "MAP": 2, "BUILT": None,
                                     "A.CASES": 1, "CASES": None, "B.CASES": None})
            self.assertIsNone(provenance.tableRows(tree, "broken.py::CASES"))


class DefaultBase(Table):
    CASES = [(True, "origin/HEAD"), (False, "origin/main")]  # origin/HEAD is set

    def test_origins_default_branch(self):
        def base(hasHead):
            with mock.patch.object(provenance, "runGit", return_value=mock.Mock(returncode=0 if hasHead else 1)):
                return provenance.defaultBase(".")

        self.check_table(self.CASES, base)


class UnittestResults(Table):
    # skipped x outcome
    CASES = [
        (False, "passed", "pass"), (False, "failed", "fail"), (False, "setup failed", "unloaded"), (False, "nothing ran", "error"),
        (True, "passed", "error"), (True, "failed", "error"), (True, "setup failed", "error"), (True, "nothing ran", "error"),
    ]

    def test_a_result_says_pass_fail_unloaded_or_no_verdict(self):
        def state(skipped, outcome):
            raised = [("test", "Traceback\nValueError: no")]
            result = mock.Mock(skipped=[("test", "why")] if skipped else [], errors=[],
                               failures=raised if "failed" in outcome else [],
                               testsRun=0 if outcome in ("setup failed", "nothing ran") else 1)
            result.wasSuccessful.return_value = "failed" not in outcome
            return resultVerdict(result).state

        self.check_table(self.CASES, state)


class TestKinds(Table):
    # the test's file
    CASES = [("t.py::T", "unloaded"), ("t.js::t", "missing"), ("t.sh::t", "error"), ("gone.py::T", "missing"),
             ("t.py", "missing")]

    def test_what_can_run(self):
        with TemporaryDirectory() as tree:
            Path(tree, "t.py").write_text("import no_such_module\n")
            Path(tree, "t.js").write_text("")
            Path(tree, "t.sh").write_text("")
            self.check_table(self.CASES, lambda test: runTest(tree, test).state)

    def test_the_time_limit(self):
        self.assertEqual(TIMEOUT, 300)


class Tap(unittest.TestCase):
    """node --test's TAP: https://testanything.org/tap-version-14-specification.html"""

    def state(self, line, name="a", exitCode=1):
        return tapVerdict(f"TAP version 13\n{line}\n", name, exitCode).state

    def test_ok_and_not_ok(self):
        self.assertEqual(self.state("ok 1 - a", exitCode=0), "pass")
        self.assertEqual(self.state("not ok 1 - a"), "fail")

    def test_a_skip_or_todo_directive_is_no_verdict(self):
        self.assertEqual(self.state("ok 1 - a # SKIP", exitCode=0), "error")
        self.assertEqual(self.state("not ok 1 - a # TODO later"), "error")

    def test_an_escaped_hash_and_backslash_belong_to_the_description(self):
        self.assertEqual(self.state("ok 1 - a \\# 1 \\\\ b", name="a # 1 \\ b", exitCode=0), "pass")

    def test_an_indented_subtest_point_is_read(self):
        self.assertEqual(self.state("# Subtest: s\n    not ok 1 - a"), "fail")

    def test_another_description_is_not_the_test(self):
        self.assertEqual(self.state("ok 1 - ab", exitCode=0), "missing")
        self.assertEqual(self.state("not ok 1 - file.js"), "unloaded")


class MultilineText(unittest.TestCase):
    """A comment-like line inside each language's multi-line text is part of the file's code."""

    def changes(self, path, text):
        return bool(changedUnits(path, text.format("one"), text.format("two")))

    def test_a_plain_comment_is_no_change(self):
        self.assertFalse(self.changes("bin/a", "# {}\necho\n"))

    def test_a_shell_here_document(self):
        # POSIX shell command language, 2.7.4 Here-Document
        for operator in ("<<", "<<-"):
            self.assertTrue(self.changes("a.sh", f"cat {operator}EOF\n# {{}}\nEOF\n"), operator)

    def test_a_yaml_block_scalar(self):
        # YAML 1.2.2, 8.1 Block Scalar Styles
        for header in ("|", ">", "|-", ">+", "|2-"):
            self.assertTrue(self.changes("a.yml", f"k: {header}\n  # {{}}\n"), header)

    def test_a_toml_multi_line_string(self):
        # TOML 1.0.0, String
        for quotes in ('"""', "'''"):
            self.assertTrue(self.changes("a.toml", f"k = {quotes}\n# {{}}\n{quotes}\n"), quotes)

    def test_a_javascript_template_literal(self):
        # ECMAScript, Template Literals
        self.assertTrue(self.changes("a.js", "const k = `\n// {}\n`;\n"))
        self.assertFalse(self.changes("a.js", "const k = `x`;\n// {}\n"))


class GitPathList(unittest.TestCase):
    """git diff --name-only -z: https://git-scm.com/docs/git-diff#Documentation/git-diff.txt--z"""

    def changed(self, repo):
        repo.commit("change")
        return provenance.changedPaths(str(repo.root), "origin/main", "HEAD")

    def test_names_with_spaces_newlines_and_other_letters_arrive_as_written(self):
        repo = Repo(self)
        repo.write({"lib/a b.py": "A = 1\n", "lib/\u00e9.py": "A = 1\n", "lib/two\nlines.py": "A = 1\n"})
        self.assertEqual(self.changed(repo), ["lib/a b.py", "lib/two\nlines.py", "lib/\u00e9.py"])

    def test_a_rename_lists_both_names(self):
        repo = Repo(self)
        repo.git("mv", "lib/keys.py", "lib/moved.py")
        self.assertEqual(self.changed(repo), ["lib/keys.py", "lib/moved.py"])


class GitMergedTree(unittest.TestCase):
    """git merge-tree --write-tree: https://git-scm.com/docs/git-merge-tree#OUTPUT"""

    def parents(self, repo, ours, theirs):
        sides = []
        for name, change in (("ours", ours), ("theirs", theirs)):
            repo.git("checkout", "--quiet", "-B", name, "origin/main")
            change(repo)
            repo.commit(name)
            sides.append(repo.git("rev-parse", "HEAD").strip())
        return sides

    def merged(self, repo, side, parents):
        tree = provenance.mergedByGit(str(repo.root), side, parents)
        return provenance.textAt(str(repo.root), tree, "lib/keys.py")

    def test_a_content_conflict_takes_the_side_asked_for(self):
        repo = Repo(self)
        parents = self.parents(repo, lambda r: r.write({"lib/keys.py": "OURS = 1\n"}),
                               lambda r: r.write({"lib/keys.py": "THEIRS = 1\n"}))
        self.assertEqual(self.merged(repo, "ours", parents), "OURS = 1\n")
        self.assertEqual(self.merged(repo, "theirs", parents), "THEIRS = 1\n")

    def test_a_file_deleted_on_one_side_still_gives_a_tree(self):
        repo = Repo(self)
        parents = self.parents(repo, lambda r: r.git("rm", "--quiet", "lib/keys.py"),
                               lambda r: r.write({"lib/keys.py": "THEIRS = 1\n"}))
        self.assertEqual(self.merged(repo, "ours", parents), "THEIRS = 1\n")

    def test_a_merge_git_cannot_run_is_an_error(self):
        repo = Repo(self)
        with self.assertRaisesRegex(provenance.ProvenanceError, "merge-tree failed"):
            provenance.mergedByGit(str(repo.root), "ours", ["HEAD", "no-such-ref"])


class Reverting(Table):
    SOURCES = {"function": ("f", "def f():\n    return 2\n", "def f():\n    return 1\n"),
               "name": ("N", "N = 2\n", "N = 1\n"),
               "class": ("C", "class C:\n    a = 2\n", "class C:\n    a = 1\n"),
               "rest": ("", "print(2)\n", "print(1)\n")}
    # unit kind x whether the reference has it, and what the reverted file says
    CASES = [
        ("function", True, "return 1"), ("function", False, "raise NotImplementedError"),
        ("name", True, "N = 1"), ("name", False, "N = None"),
        ("class", True, "a = 1"), ("class", False, "C = None"),
        ("rest", True, "print(1)"), ("rest", False, ""),
    ]

    def test_each_kind_goes_back_to_the_reference_or_to_a_stand_in(self):
        for kind, had, expected in self.CASES:
            with self.subTest(kind=kind, had=had):
                name, new, old = self.SOURCES[kind]
                self.assertEqual(units("a.py", new)[name].kind, kind)
                result = reverted("a.py", new, old if had else None, [name])
                self.assertIn(expected, result)
                self.assertNotIn("2", result)

    def test_a_file_that_is_not_split_reverts_whole(self):
        self.assertEqual(reverted("a.sh", "new\n", "old\n", [""]), "old\n")
        self.assertEqual(reverted("a.sh", "new\n", None, [""]), "")
        with self.assertRaisesRegex(Unrevertable, "isn't Python"):
            reverted("a.sh", "new\n", "old\n", ["f"])

    def test_what_cannot_be_reverted_says_why(self):
        for current, reference, targets, why in (
                ("N = 1\n", "N = 0\n", ["gone"], "has no gone"),
                ("N = 1\n", "N = (\n", ["N"], "in the reference doesn't parse"),
                ("N = (\n", "N = 0\n", ["N"], "in the worktree doesn't parse")):
            with self.subTest(why=why), self.assertRaisesRegex(Unrevertable, why):
                reverted("a.py", current, reference, targets)


if __name__ == "__main__":
    unittest.main()
