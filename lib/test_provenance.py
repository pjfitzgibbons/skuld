# provenance-check against temp repos: a merge, a port, a rule and a parser change. Each runs the
# real check, its snapshot, temporary worktree and test processes included.
import argparse
import contextlib
import io
import os
import shutil
import signal
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from lib import provenance, provenance_runner
from lib.provenance_runner import TestProcesses, ownEnvironment, runTest, tapVerdict

KEY = "SK-0001"
BASE_FILES = {
    ".gitignore": ".tmp/\n",
    "lib/__init__.py": "",
    "lib/keys.py": """
        def keyOf(name):
            return name.split(".")[0]
    """,
    "lib/prices.py": """
        def price(amount):
            return amount

        def label(amount):
            return str(amount)
    """,
}
PRICE = """
    def price(amount):
        return amount * 2

    def label(amount):
        return str(amount)
"""
PRICE_TEST = """
    import unittest

    from lib import prices


    class Price(unittest.TestCase):
        def test_doubles(self):
            self.assertEqual(prices.price(2), 4)

        def test_proves_nothing(self):
            self.assertTrue(prices.price)
"""
PRICE_ROW = "| lib/prices.py::price | plain | test: lib/test_prices.py::Price.test_doubles |"


class Repo:
    def __init__(self, testcase, files=BASE_FILES):
        folder = tempfile.mkdtemp()
        testcase.addCleanup(shutil.rmtree, folder)
        self.root = Path(folder) / "repo"
        self.root.mkdir()
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.email", "t@t.com")
        self.git("config", "user.name", "t")
        self.write(files)
        self.commit("base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "--quiet", "-b", f"feat/{KEY}-x")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def write(self, files):
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text).lstrip("\n"))

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)

    def check(self, *rows, jobs=provenance.DEFAULT_JOBS):
        manifest = self.root / ".tmp" / KEY / "provenance.md"
        if rows:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text("\n".join(["| Function | Class | Proof |", "|---|---|---|", *rows]) + "\n")
        return provenance.check(str(self.root), "origin/main", manifest, jobs=jobs)


class CheckCase(unittest.TestCase):
    def assertRefused(self, report, *parts):
        self.assertTrue(any(all(part in problem for part in parts) for problem in report.problems),
                        f"no refusal names all of {parts} in {report.problems}")

    def assertPasses(self, report, lines):
        self.assertEqual(report.problems, [])
        self.assertEqual(len(report.passed), lines, report.passed)

    def priceChange(self):
        repo = Repo(self)
        repo.write({"lib/prices.py": PRICE, "lib/test_prices.py": PRICE_TEST})
        return repo


class Coverage(CheckCase):
    def test_a_change_with_no_row_is_refused_naming_its_file_and_line(self):
        report = self.priceChange().check()
        self.assertRefused(report, "lib/prices.py:2: lib/prices.py::price changed, and no row names it")
        self.assertRefused(report, "there is no manifest at")
        self.assertEqual(len(report.problems), 2, "tests and the unchanged label() need no row")

    def test_a_proven_change_passes_with_one_line_per_row(self):
        report = self.priceChange().check(PRICE_ROW)
        self.assertPasses(report, 1)
        self.assertEqual(report.passed, [
            "ok row 1 (lib/prices.py::price, plain): fails on revert: lib/test_prices.py::Price.test_doubles"])

    def test_committed_and_untracked_changes_are_both_checked(self):
        repo = self.priceChange()
        repo.commit("price")
        repo.write({"lib/extra.py": "def extra():\n    return 1\n"})
        self.assertRefused(repo.check(PRICE_ROW), "lib/extra.py:1: lib/extra.py::extra changed")

    def test_comments_docstrings_imports_prose_and_deletions_need_no_row(self):
        repo = Repo(self)
        repo.write({
            "lib/prices.py": '''
                import os


                def price(amount):
                    """What it costs."""
                    # Unchanged.
                    return amount
            ''',
            "notes.md": "Prose.\n",
        })
        self.assertPasses(repo.check(), 0)

    def test_a_file_in_another_language_is_one_unit_and_its_comments_need_no_row(self):
        repo = Repo(self, {**BASE_FILES, "bin/hello": "#!/bin/sh\necho hello\n"})
        repo.write({"bin/hello": "#!/bin/sh\n# Says hello.\necho hello\n"})
        self.assertPasses(repo.check(), 0)
        repo.write({"bin/hello": "#!/bin/sh\n# Says goodbye.\necho bye\n"})
        self.assertRefused(repo.check(), "bin/hello:3: bin/hello changed")


class Proof(CheckCase):
    def test_a_test_that_passes_on_revert_is_refused_naming_the_row_the_hunk_and_the_test(self):
        report = self.priceChange().check("| lib/prices.py::price | plain | test: lib/test_prices.py::Price.test_proves_nothing |")
        self.assertRefused(report, "row 1 (lib/prices.py::price, plain) at lib/prices.py:2",
                           "lib/test_prices.py::Price.test_proves_nothing still passes with the code reverted")
        self.assertEqual(report.passed, [])

    def test_a_test_that_does_not_exist_is_refused(self):
        for test in ("lib/test_prices.py::Price.test_absent", "lib/test_absent.py::Price.test_doubles"):
            with self.subTest(test=test):
                report = self.priceChange().check(f"| lib/prices.py::price | plain | test: {test} |")
                self.assertRefused(report, "row 1", "lib/prices.py:2", f"names the test {test}, which doesn't exist")

    def test_an_erroring_test_is_refused_naming_the_row(self):
        repo = self.priceChange()
        repo.write({"lib/test_broken.py": "import no_such_module\n"})
        report = repo.check("| lib/prices.py::price | plain | test: lib/test_broken.py::Price.test_doubles |")
        self.assertRefused(report, "row 1", "lib/prices.py:2", "can't run with the change in place", "no_such_module")

    def test_a_test_that_no_longer_loads_on_reverted_code_is_proof(self):
        repo = self.priceChange()
        # LIMIT exists only with the change, and the test file reads it as it loads.
        repo.write({
            "lib/limits.py": "LIMIT = 3\n\ndef limit():\n    return LIMIT\n",
            "lib/test_limits.py": """
                import unittest

                from lib import limits

                assert limits.LIMIT == 3


                class Limit(unittest.TestCase):
                    def test_limit(self):
                        self.assertEqual(limits.limit(), 3)
            """,
        })
        self.assertPasses(repo.check(
            PRICE_ROW, "| lib/limits.py::LIMIT | plain | test: lib/test_limits.py::Limit.test_limit |",
            "| lib/limits.py::limit | plain | test: lib/test_limits.py::Limit.test_limit |"), 3)

    def test_a_new_test_file_over_new_code_is_not_refused_for_failing_to_load(self):
        shapes = {
            "a constant read as the file loads": ("N = 2\n", "N", "TOTAL = new.N * 2"),
            "a new base class subclassed": ("class Base:\n    pass\n", "Base", "class Fake(new.Base):\n    pass"),
            "a class setup using new code": ("N = 2\n", "N", ""),
            "the module reading its own name": ("N = 2\nDOUBLE = N * 2\n", "N", ""),
        }
        for shape, (source, name, atLoad) in shapes.items():
            with self.subTest(shape=shape):
                repo = Repo(self)
                setUp = "    @classmethod\n    def setUpClass(cls):\n        cls.total = new.N * 2\n\n"
                repo.write({"lib/new.py": source, "lib/test_new.py":
                            f"import unittest\n\nfrom lib import new\n\n{atLoad}\n\nclass T(unittest.TestCase):\n"
                            + (setUp if shape.startswith("a class setup") else "")
                            + "    def test_it(self):\n        self.assertTrue(new)\n"})
                report = repo.check(f"| lib/new.py::{name} | plain | test: lib/test_new.py::T.test_it |")
                self.assertFalse([problem for problem in report.problems if problem.startswith("row 1")], report.problems)

    def test_a_test_skipped_on_reverted_code_is_no_proof(self):
        repo = Repo(self)
        repo.write({"lib/limits.py": "LIMIT = 3\n", "lib/test_limits.py": """
            import unittest

            from lib import limits


            class Limit(unittest.TestCase):
                def test_limit(self):
                    if limits.LIMIT is None:
                        self.skipTest("no limit")
        """})
        report = repo.check("| lib/limits.py::LIMIT | plain | test: lib/test_limits.py::Limit.test_limit |")
        self.assertRefused(report, "row 1", "lib/limits.py:1", "reached no verdict", "skipped")

    def test_a_test_that_crashes_on_reverted_code_is_no_proof(self):
        repo = Repo(self)
        repo.write({"lib/limits.py": "LIMIT = 3\n", "lib/test_limits.py": """
            import os
            import unittest

            from lib import limits


            class Limit(unittest.TestCase):
                def test_limit(self):
                    if limits.LIMIT is None:
                        os._exit(9)
        """})
        report = repo.check("| lib/limits.py::LIMIT | plain | test: lib/test_limits.py::Limit.test_limit |")
        self.assertRefused(report, "row 1", "crashed (exit 9)")

    def test_a_new_function_reverts_to_one_that_raises_so_a_test_importing_it_still_loads(self):
        repo = Repo(self)
        repo.write({
            "lib/new.py": "def triple(x):\n    return x * 3\n",
            "lib/test_new.py": """
                import unittest

                from lib.new import triple


                class Triple(unittest.TestCase):
                    def test_triple(self):
                        self.assertEqual(triple(2), 6)
            """,
        })
        self.assertPasses(repo.check("| lib/new.py::triple | plain | test: lib/test_new.py::Triple.test_triple |"), 1)

    def test_each_changed_function_needs_its_own_row_which_a_class_or_file_name_is_not(self):
        repo = self.priceChange()
        repo.write({"lib/prices.py": PRICE.replace("str(amount)", "repr(amount)")})
        together = repo.check(
            "| lib/prices.py::price, lib/prices.py::label | plain | test: lib/test_prices.py::Price.test_doubles |")
        self.assertRefused(together, "row 1", "lib/prices.py:2", "names 2 changed functions")
        self.assertEqual(together.passed, [])
        wholeFile = repo.check("| lib/prices.py | plain | test: lib/test_prices.py::Price.test_doubles |")
        self.assertRefused(wholeFile, "lib/prices.py::price changed, and no row names it")
        self.assertRefused(wholeFile, "lib/prices.py::label changed, and no row names it")

    def test_a_rows_constant_reverts_with_its_function(self):
        repo = Repo(self)
        repo.write({"lib/prices.py": "RATE = 2\n\ndef price(amount):\n    return amount * RATE\n\n"
                                     "def label(amount):\n    return str(amount)\n",
                    "lib/test_prices.py": PRICE_TEST})
        self.assertPasses(repo.check(
            "| lib/prices.py::price, lib/prices.py::RATE | plain | test: lib/test_prices.py::Price.test_doubles |"), 1)

    def test_a_new_script_reverts_to_an_empty_file(self):
        repo = Repo(self)
        repo.write({
            "bin/hello": "#!/bin/sh\necho hello\n",
            "bin/test_hello.py": """
                import subprocess
                import unittest


                class Hello(unittest.TestCase):
                    def test_says_hello(self):
                        self.assertEqual(subprocess.run(["sh", "hello"], capture_output=True, text=True,
                                                        cwd=__file__.rsplit("/", 1)[0]).stdout, "hello\\n")
            """,
        })
        self.assertPasses(repo.check("| bin/hello | plain | test: bin/test_hello.py::Hello.test_says_hello |"), 1)

    def test_a_revert_that_cannot_be_applied_is_refused_naming_the_row(self):
        repo = Repo(self, {**BASE_FILES, "lib/prices.py": "def price(amount:\n    return amount\n"})
        repo.write({"lib/prices.py": PRICE, "lib/test_prices.py": PRICE_TEST})
        report = repo.check(PRICE_ROW, "| lib/prices.py::label | plain | test: lib/test_prices.py::Price.test_doubles |")
        self.assertRefused(report, "row 1", "lib/prices.py:1", "can't be applied", "doesn't parse")

    def test_a_row_naming_unchanged_code_is_refused(self):
        report = self.priceChange().check(
            PRICE_ROW, "| lib/prices.py::label | plain | test: lib/test_prices.py::Price.test_doubles |")
        self.assertRefused(report, "row 2", "names lib/prices.py::label, which this change doesn't touch")

    def test_only_the_named_tests_run_each_once_with_the_change_in_place(self):
        repo = self.priceChange()
        ran = repo.root.parent / "ran"
        repo.write({"lib/test_prices.py": f"""
            import unittest

            from lib import prices


            class Price(unittest.TestCase):
                def note(self):
                    with open({str(ran)!r}, "a") as log:
                        log.write(self._testMethodName + "\\n")

                def test_doubles(self):
                    self.note()
                    self.assertEqual(prices.price(2), 4)

                def test_other(self):
                    self.note()
        """})
        self.assertPasses(repo.check(PRICE_ROW, PRICE_ROW), 2)
        self.assertEqual(ran.read_text().split(), ["test_doubles"] * 3, "one baseline run, then one revert per row")

    def test_the_worktree_its_index_and_its_worktree_list_stay_as_they_were(self):
        repo = self.priceChange()
        repo.git("add", "lib/prices.py")

        def state():
            files = {str(path): path.read_bytes() for path in repo.root.rglob("*.py")}
            return repo.git("status", "--porcelain"), repo.git("worktree", "list"), repo.git("rev-parse", "HEAD"), files

        repo.check(PRICE_ROW)
        before = state()
        repo.check(PRICE_ROW)
        self.assertEqual(state(), before)

    def test_inherited_git_variables_do_not_turn_the_check_on_the_worktree(self):
        repo = self.priceChange()
        repo.git("add", "lib/prices.py")
        repo.write({"notes.cfg": "uncommitted\n"})
        repo.check(PRICE_ROW)
        before = repo.git("status", "--porcelain", "--untracked-files=all")
        elsewhere = {"GIT_DIR": str(repo.root / ".git"), "GIT_WORK_TREE": str(repo.root),
                     "GIT_INDEX_FILE": str(repo.root / ".git" / "index")}
        with mock.patch.dict(os.environ, elsewhere):
            report = repo.check(PRICE_ROW)
        self.assertRefused(report, "notes.cfg changed")
        self.assertEqual(repo.git("status", "--porcelain", "--untracked-files=all"), before)
        self.assertTrue((repo.root / ".tmp" / KEY / "provenance.md").is_file())

    def test_an_ignored_file_added_by_force_needs_a_row(self):
        repo = Repo(self)
        repo.write({".tmp/forced.py": "def forced():\n    return 1\n"})
        repo.git("add", "--force", ".tmp/forced.py")
        self.assertRefused(repo.check(), ".tmp/forced.py::forced changed")

    def test_what_one_test_run_leaves_behind_is_no_proof_in_the_next(self):
        repo = self.priceChange()
        repo.write({"lib/test_leftover.py": """
            import os
            import unittest


            class Leftover(unittest.TestCase):
                def test_makes_a_folder(self):
                    os.mkdir("scratch-out")
        """})
        report = repo.check("| lib/prices.py::price | plain | test: lib/test_leftover.py::Leftover.test_makes_a_folder |")
        self.assertRefused(report, "row 1", "test_makes_a_folder still passes")

    def test_a_file_saved_while_the_check_runs_is_refused(self):
        repo = self.priceChange()
        late = str(repo.root / "lib" / "late.py")
        repo.write({"lib/test_late.py": f"""
            import unittest

            from lib import prices


            class Late(unittest.TestCase):
                def test_doubles_and_saves_a_file(self):
                    with open({late!r}, "w") as file:
                        file.write("def late():\\n    return 1\\n")
                    self.assertEqual(prices.price(2), 4)
        """})
        report = repo.check("| lib/prices.py::price | plain | test: lib/test_late.py::Late.test_doubles_and_saves_a_file |")
        self.assertEqual(report.problems, ["the worktree changed while the check ran; run it again"])

    def test_the_repos_checkout_hook_does_not_run_or_stop_the_check(self):
        repo = self.priceChange()
        hook = repo.root / ".git" / "hooks" / "post-checkout"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.assertPasses(repo.check(PRICE_ROW), 1)
        self.assertEqual(len(repo.git("worktree", "list").splitlines()), 1)

    def test_a_python_file_in_another_encoding_is_one_unit(self):
        repo = Repo(self)
        (repo.root / "lib" / "latin.py").write_bytes(b"# -*- coding: latin-1 -*-\nNAME = '\xe9'\n")
        repo.write({"lib/test_latin.py": """
            import unittest

            from lib import latin


            class Latin(unittest.TestCase):
                def test_name(self):
                    self.assertEqual(latin.NAME, chr(233))
        """})
        self.assertRefused(repo.check(), "lib/latin.py:1: lib/latin.py changed")
        self.assertPasses(repo.check("| lib/latin.py | plain | test: lib/test_latin.py::Latin.test_name |"), 1)

    def test_a_git_repository_a_test_leaves_behind_is_removed_too(self):
        repo = self.priceChange()
        repo.write({"lib/test_nested.py": """
            import subprocess
            import unittest


            class Nested(unittest.TestCase):
                def test_makes_a_repository(self):
                    subprocess.run(["mkdir", "scratch-repo"], check=True)
                    subprocess.run(["git", "init", "--quiet", "scratch-repo"], check=True)
        """})
        report = repo.check("| lib/prices.py::price | plain | test: lib/test_nested.py::Nested.test_makes_a_repository |")
        self.assertRefused(report, "row 1", "test_makes_a_repository still passes")

    def test_what_a_test_stages_or_branches_reaches_neither_the_next_run_nor_the_repository(self):
        repo = self.priceChange()
        repo.write({"lib/test_stages.py": """
            import subprocess
            import unittest


            class Stages(unittest.TestCase):
                def test_stages_a_file_and_makes_a_branch(self):
                    with open("scratch.cfg", "x") as file:
                        file.write("x")
                    subprocess.run(["git", "add", "scratch.cfg"], check=True)
                    subprocess.run(["git", "branch", "scratch-branch"], check=True)
        """})
        report = repo.check("| lib/prices.py::price | plain | test: lib/test_stages.py::Stages.test_stages_a_file_and_makes_a_branch |")
        self.assertRefused(report, "row 1", "test_stages_a_file_and_makes_a_branch still passes")
        self.assertNotIn("scratch-branch", repo.git("branch", "--list"))

    def test_if_listed_reads_this_repository_whatever_git_variables_are_set(self):
        repo, other = self.priceChange(), Repo(self)
        listed = {"provenanceCheckouts": [str(repo.root)]}
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other.root / ".git"), "GIT_WORK_TREE": str(other.root)}):
            self.assertTrue(provenance.isListed(str(repo.root), listed))

    def test_a_reordered_module_needs_a_row_its_test_can_prove(self):
        repo = Repo(self, {**BASE_FILES, "lib/order.py": "VALUE = 1\nRESULT = VALUE\nVALUE = 2\n"})
        repo.write({"lib/order.py": "VALUE = 1\nVALUE = 2\nRESULT = VALUE\n", "lib/test_order.py": """
            import unittest

            from lib import order


            class Order(unittest.TestCase):
                def test_result(self):
                    self.assertEqual(order.RESULT, 2)
        """})
        self.assertRefused(repo.check(), "lib/order.py changed, and no row names it")
        self.assertPasses(repo.check("| lib/order.py | plain | test: lib/test_order.py::Order.test_result |"), 1)

    def meetingRows(self):
        """Two rows whose tests each wait for the other to have started."""
        repo = self.priceChange()
        repo.write({"lib/prices.py": PRICE.replace("str(amount)", 'str(amount) + "!"')})
        folder = str(repo.root.parent)
        repo.write({"lib/test_meet.py": f"""
            import os
            import time
            import unittest

            from lib import prices


            class Meet(unittest.TestCase):
                def meet(self, mine, other):
                    open(os.path.join({folder!r}, mine), "w").close()
                    deadline = time.monotonic() + 3
                    while not os.path.exists(os.path.join({folder!r}, other)):
                        self.assertLess(time.monotonic(), deadline, "the other row never started")
                        time.sleep(0.02)

                def test_price(self):
                    self.meet("price", "label")
                    self.assertEqual(prices.price(2), 4)

                def test_label(self):
                    self.meet("label", "price")
                    self.assertEqual(prices.label(2), "2!")
        """})
        rows = [f"| lib/prices.py::{name} | plain | test: lib/test_meet.py::Meet.test_{name} |" for name in ("price", "label")]
        return repo, rows

    def test_rows_are_proven_at_once(self):
        repo, rows = self.meetingRows()
        self.assertPasses(repo.check(*rows, jobs=2), 2)

    def test_one_job_proves_them_one_at_a_time(self):
        repo, rows = self.meetingRows()
        self.assertRefused(repo.check(*rows, jobs=1), "the other row never started")

    def test_a_test_asked_for_by_many_rows_at_once_runs_once_for_the_baseline(self):
        asked = []

        def slow(test):
            asked.append(test)
            time.sleep(0.05)
            return test.upper()

        once = provenance.onceEach(slow)
        with ThreadPoolExecutor(8) as pool:
            self.assertEqual(list(pool.map(once, ["a", "b"] * 8)), ["A", "B"] * 8)
        self.assertEqual(sorted(asked), ["a", "b"])

    def test_what_a_shared_run_raises_reaches_every_asker(self):
        def broken(_test):
            raise ValueError("no")

        once = provenance.onceEach(broken)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "no"):
                once("a")

    def test_a_move_no_test_can_tell_apart_is_exempted_with_its_reason(self):
        repo = Repo(self, {**BASE_FILES, "lib/order.py": "A = 1\nB = 2\n"})
        repo.write({"lib/order.py": "B = 2\nA = 1\n"})
        self.assertRefused(repo.check(), "lib/order.py changed, and no row names it")
        report = repo.check("| lib/order.py | plain | exempt: two constants that read nothing |")
        self.assertPasses(report, 1)
        self.assertIn("moved, exempt: two constants that read nothing", report.passed[0])

    def test_an_exemption_spares_nothing_that_was_edited(self):
        report = self.priceChange().check("| lib/prices.py::price | plain | exempt: only moved |")
        self.assertRefused(report, "only statements that did nothing but move", "lib/prices.py::price changed")

    def test_an_exemption_names_only_what_the_change_touches(self):
        repo = Repo(self, {**BASE_FILES, "lib/order.py": "A = 1\nB = 2\n"})
        repo.write({"lib/order.py": "B = 2\nA = 1\n"})
        report = repo.check("| lib/order.py, lib/order.py::A | plain | exempt: constants |")
        self.assertRefused(report, "names lib/order.py::A, which this change doesn't touch")

    def test_a_run_that_stops_the_check_starts_no_further_row(self):
        started = []

        def judged(row, *_rest):
            started.append(row.number)
            if row.number == 1:
                raise KeyboardInterrupt
            time.sleep(0.2)
            return []

        rows = [provenance.Row(number, [], "plain", []) for number in range(1, 7)]
        checked = provenance.Checked("root", "commit", "tree")
        with mock.patch.object(provenance, "rowProblems", judged), self.assertRaises(KeyboardInterrupt):
            provenance.proveRows(provenance.Report(), rows, [], checked, "base", 1)
        self.assertLessEqual(len(started), 2, started)
        with self.assertRaisesRegex(provenance.ProvenanceError, "the check was stopped"):
            checked.run("lib/test_prices.py::Price.test_doubles")

    def test_a_run_that_dies_or_skips_or_expects_failure_gives_no_verdict(self):
        repo = Repo(self)
        repo.write({
            "lib/test_dies.py": """
                import atexit
                import os
                import unittest


                class Dies(unittest.TestCase):
                    def test_passes_then_dies(self):
                        atexit.register(os._exit, 9)
            """,
            "lib/test_skips.py": """
                import unittest

                raise unittest.SkipTest("not here")
            """,
            "lib/test_expects.py": """
                import unittest


                class Expects(unittest.TestCase):
                    @unittest.expectedFailure
                    def test_fails(self):
                        self.fail()
            """,
        })
        for test, why in (("lib/test_dies.py::Dies.test_passes_then_dies", "crashed (exit 9)"),
                          ("lib/test_skips.py::T", "skipped: not here"),
                          ("lib/test_expects.py::Expects.test_fails", "expectedFailure")):
            with self.subTest(test=test):
                verdict = runTest(str(repo.root), test)
                self.assertEqual(verdict.state, "error")
                self.assertIn(why, verdict.detail)

    def test_an_unknown_class_and_an_unreadable_item_are_refused(self):
        report = self.priceChange().check("| lib/prices.py::price | obvious | tset: x |")
        self.assertRefused(report, "row 1", "lib/prices.py:2", 'unknown class "obvious"')
        self.assertRefused(report, "manifest line 3", 'unreadable proof item "tset: x"')


class BorrowedProof(CheckCase):
    """A row proves only what it names, so one function's test can't stand for another's."""

    MODULE = """
        RATE = {rate}


        class Shop:
            def priced(self):
                return {priced}

            def labelled(self):
                return {labelled}


        def total():
            return {total}
    """
    TESTS = """
        import unittest

        from lib import shop


        class Shopping(unittest.TestCase):
            def test_priced(self):
                self.assertEqual(shop.Shop().priced(), 2)

            def test_labelled(self):
                self.assertEqual(shop.Shop().labelled(), "new")

            def test_rate(self):
                self.assertEqual(shop.RATE, 2)

            def test_total(self):
                self.assertEqual(shop.total(), 2)
    """

    def shop(self, rate=1, priced=1, labelled='"old"', total=1, base=True):
        files = {"lib/shop.py": self.MODULE.format(rate=1, priced=1, labelled='"old"', total=1)} if base else {}
        repo = Repo(self, {**BASE_FILES, **files})
        repo.write({"lib/shop.py": self.MODULE.format(rate=rate, priced=priced, labelled=labelled, total=total),
                    "lib/test_shop.py": self.TESTS})
        return repo

    @staticmethod
    def row(targets, test):
        return f"| {targets} | plain | test: lib/test_shop.py::Shopping.{test} |"

    def test_a_file_target_does_not_prove_a_constant_through_a_function(self):
        report = self.shop(rate=2, total=2).check(
            self.row("lib/shop.py", "test_total"), self.row("lib/shop.py::total", "test_total"))
        self.assertRefused(report, "row 1", "names lib/shop.py, which this change doesn't touch")
        self.assertRefused(report, "lib/shop.py::RATE changed, and no row names it")

    def test_a_row_naming_code_it_does_not_prove_is_refused(self):
        report = self.shop(priced=2, total=2).check(
            self.row("lib/shop.py::Shop.priced, lib/shop.py::Shop", "test_priced"))
        self.assertRefused(report, "row 1", "names lib/shop.py::Shop, which this change doesn't touch")
        self.assertEqual(report.passed, [])

    def test_a_new_class_named_beside_its_method_does_not_lend_it_another_methods_test(self):
        repo = self.shop(priced=2, labelled='"new"', total=2, base=False)
        report = repo.check(self.row("lib/shop.py::Shop.priced, lib/shop.py::Shop", "test_labelled"))
        self.assertRefused(report, "row 1", "still passes with only lib/shop.py::Shop.priced reverted")

    def test_a_name_a_row_carries_cannot_be_all_its_test_fails_on(self):
        report = self.shop(rate=2, total=2).check(self.row("lib/shop.py::total, lib/shop.py::RATE", "test_rate"))
        self.assertRefused(report, "row 1", "still passes with only lib/shop.py::total reverted")

    def test_a_class_declaration_reverts_alone_leaving_its_methods(self):
        repo = self.shop()
        source = (repo.root / "lib/shop.py").read_text()
        repo.write({"lib/shop.py": source.replace("class Shop:\n", "class Shop:\n    currency = 'usd'\n\n")})
        report = repo.check(self.row("lib/shop.py::Shop", "test_priced"))
        self.assertRefused(report, "row 1", "lib/shop.py:5", "test_priced")
        self.assertEqual(len(report.problems), 1, report.problems)

    def test_a_merge_row_does_not_answer_for_changes_no_resolver_wrote(self):
        repo = self.shop(total=2)
        report = repo.check("| lib/shop.py::total | merge-resolved | test: lib/test_shop.py::Shopping.test_total |")
        self.assertRefused(report, "lib/shop.py::total changed, and no row names it")
        self.assertRefused(report, "row 1", "which no merge's resolver wrote")


class ModuleStatements(CheckCase):
    TESTS = """
        import unittest

        from lib import registry


        class Registry(unittest.TestCase):
            def test_calls(self):
                self.assertEqual(registry.f(), 1)

            def test_registered(self):
                self.assertTrue(registry.f.registered)
    """

    def test_a_new_files_module_statements_are_reverted_and_nothing_else(self):
        repo = Repo(self)
        repo.write({"lib/registry.py": "def f():\n    return 1\n\n\nf.registered = True\n",
                    "lib/test_registry.py": self.TESTS})
        function = "| lib/registry.py::f | plain | test: lib/test_registry.py::Registry.test_calls |"
        statements = "| lib/registry.py | plain | test: lib/test_registry.py::Registry.{} |"
        self.assertRefused(repo.check(function, statements.format("test_calls")), "row 2", "still passes")
        self.assertPasses(repo.check(function, statements.format("test_registered")), 2)

    def test_a_script_of_statements_alone_can_be_proven(self):
        repo = Repo(self)
        repo.write({"bin/run.py": "import sys\n\nsys.exit(3)\n", "bin/test_run.py": """
            import subprocess
            import sys
            import unittest


            class Run(unittest.TestCase):
                def test_exits_3(self):
                    script = __file__.replace("test_run", "run")
                    self.assertEqual(subprocess.run([sys.executable, script]).returncode, 3)
        """})
        self.assertPasses(repo.check("| bin/run.py | plain | test: bin/test_run.py::Run.test_exits_3 |"), 1)


class MultilineText(CheckCase):
    def test_a_comment_like_line_inside_a_heredoc_needs_a_row(self):
        script = "#!/bin/sh\ncat <<EOF\n# {}\nEOF\n"
        repo = Repo(self, {**BASE_FILES, "bin/note": script.format("one")})
        repo.write({"bin/note": script.format("two")})
        self.assertRefused(repo.check(), "bin/note:3: bin/note changed")


class Rule(CheckCase):
    TESTS = """
        import unittest

        from lib import prices

        CASES = [(1, "gold", 2), (1, "none", 1), (0, "gold", 0)]
        FULL = [(1, "gold", 2), (1, "none", 1), (0, "gold", 0), (0, "none", 0)]


        class Other:
            CASES = FULL = [1, 2, 3, 4]


        class Price(unittest.TestCase):
            CASES = [1]

            def test_table(self):
                for amount, tier, expected in CASES:
                    self.assertEqual(prices.price(amount, tier), expected)

            def test_inside(self):
                self.assertEqual(prices.price(1, "none"), 1)
    """

    def ruleReport(self, proof):
        repo = Repo(self)
        repo.write({
            "lib/prices.py": """
                def price(amount, tier="none"):
                    return amount * 2 if tier == "gold" else amount

                def label(amount):
                    return str(amount)
            """,
            "lib/test_prices.py": self.TESTS,
        })
        return repo.check(f"| lib/prices.py::price | rule | test: lib/test_prices.py::Price.test_table; {proof} |")

    def test_a_table_smaller_than_the_product_is_refused_naming_both_counts(self):
        report = self.ruleReport("dims: amount(0, 1) x tier(gold, none); table: lib/test_prices.py::CASES")
        self.assertRefused(report, "row 1", "lib/prices.py:1", "has 3 rows", "product is 4")

    def test_a_table_with_a_row_per_combination_passes(self):
        self.assertPasses(self.ruleReport("dims: amount(0, 1) x tier(gold, none); table: lib/test_prices.py::FULL"), 1)

    def test_a_dotted_table_name_is_followed_through_its_classes(self):
        proof = "dims: amount(0, 1); table: lib/test_prices.py::{}"
        self.assertRefused(self.ruleReport(proof.format("Price.CASES")), "has 1 rows", "product is 2")
        self.assertRefused(self.ruleReport(proof.format("Nope.CASES")), "there is no table")

    def test_dimensions_that_do_not_read_whole_are_refused(self):
        for dims in ("closed(unset, date(2020))", "amount(0, 1) x tier", "amount(0, 1) tier(a)", "amount()"):
            with self.subTest(dims=dims):
                self.assertRefused(self.ruleReport(f"dims: {dims}; table: lib/test_prices.py::FULL"), "`dims:` lists each")

    def test_a_missing_table_or_dimensions_are_refused(self):
        self.assertRefused(self.ruleReport("dims: amount(0, 1); table: lib/test_prices.py::NONE"), "there is no table")
        self.assertRefused(self.ruleReport("table: lib/test_prices.py::CASES"), "needs a `dims:` item")
        self.assertRefused(self.ruleReport("dims: amount; table: lib/test_prices.py::CASES"), "`dims:` lists each")

    def test_each_exempt_path_needs_a_test_on_both_sides(self):
        proof = ("dims: amount(0, 1); table: lib/test_prices.py::CASES; exempt: log only, edits; "
                 "inside log only: lib/test_prices.py::Price.test_inside; "
                 "outside log only: lib/test_prices.py::Price.test_table; "
                 "inside edits: lib/test_prices.py::Price.test_absent")
        report = self.ruleReport(proof)
        self.assertRefused(report, 'exempt path "edits" has no `outside edits:` test')
        self.assertRefused(report, "test_absent, which doesn't exist")
        self.assertEqual(len(report.problems), 2)


class PortAndParser(CheckCase):
    def portReport(self, proof, kind="ported"):
        repo = Repo(self)
        repo.write({
            "lib/index.py": """
                def entry(name):
                    return name[: name.index("-")]
            """,
            "lib/test_index.py": """
                import unittest

                from lib import index, keys


                class Index(unittest.TestCase):
                    def test_entry(self):
                        self.assertEqual(index.entry("a-b"), "a")

                    def test_agrees(self):
                        self.assertEqual(index.entry("a.md"), keys.keyOf("a.md"))
            """,
        })
        return repo.check(f"| lib/index.py::entry | {kind} | test: lib/test_index.py::Index.test_entry; {proof} |")

    def test_a_port_needs_its_source_and_its_concepts(self):
        report = self.portReport("")
        self.assertRefused(report, "row 1", "lib/index.py:1", "needs a `source:` item")
        self.assertRefused(report, "each concept it handles")

    def test_a_ports_agreement_test_must_pass(self):
        report = self.portReport("source: store script; agree key from filename: lib/test_index.py::Index.test_agrees")
        self.assertRefused(report, "test_agrees fails with the change in place", "ValueError")

    def test_a_reused_implementation_must_exist(self):
        self.assertRefused(self.portReport("source: s; reuse key: lib/keys.py::absent", "moved"),
                           "reuses lib/keys.py::absent, which doesn't exist")
        self.assertPasses(self.portReport("source: s; reuse key: lib/keys.py::keyOf", "moved"), 1)

    def test_a_concept_found_nowhere_else_records_the_search(self):
        self.assertPasses(self.portReport("source: s; new key from filename: grep -rn keyOf lib"), 1)

    def test_a_parser_needs_its_spec_and_a_test_per_case(self):
        report = self.portReport("case four backticks: lib/test_index.py::Index.test_four", "parser")
        self.assertRefused(report, "row 1", "needs a `spec:` item")
        self.assertRefused(report, "test_four, which doesn't exist")
        self.assertPasses(self.portReport(
            "spec: https://spec.commonmark.org/#fenced-code-blocks; case dash: lib/test_index.py::Index.test_entry", "parser"), 1)


class Merge(CheckCase):
    """main and the branch each change one() and two(), and the merge conflicts."""

    MODULE = """
        def one():
            return "{one}"


        def spacer():
            return None


        def two():
            return "{two}"


        {tail}
    """
    TESTS = """
        import unittest

        from lib import merged


        class Merged(unittest.TestCase):
            def test_two(self):
                self.assertEqual(merged.two(), "both")

            def test_ours_only_fails(self):
                self.assertNotEqual(merged.two(), "branch")
    """

    def merged(self, one, two, mainTail=""):
        repo = Repo(self, {**BASE_FILES, "lib/merged.py": self.MODULE.format(one="base", two="base", tail="")})
        repo.write({"lib/merged.py": self.MODULE.format(one="branch", two="branch", tail="")})
        repo.commit("branch")
        repo.git("checkout", "--quiet", "main")
        repo.write({"lib/merged.py": self.MODULE.format(one="main", two="main", tail=mainTail)})
        repo.commit("main")
        repo.git("update-ref", "refs/remotes/origin/main", "HEAD")
        repo.git("checkout", "--quiet", f"feat/{KEY}-x")
        subprocess.run(["git", "merge", "--quiet", "main"], cwd=repo.root, capture_output=True)
        repo.write({"lib/merged.py": self.MODULE.format(one=one, two=two, tail=""), "lib/test_merged.py": self.TESTS})
        return repo

    ONE_ROW = "| lib/merged.py::one | plain | test: lib/test_merged.py::Merged.test_two |"

    def test_a_verbatim_pick_of_either_side_needs_no_merge_row(self):
        repo = self.merged(one="branch", two="main")
        repo.commit("merge")
        report = repo.check()
        self.assertFalse([problem for problem in report.problems if "beyond git's merge" in problem], report.problems)
        self.assertRefused(report, "lib/merged.py::one changed")

    def test_resolver_written_code_with_no_merge_row_is_refused_naming_its_line(self):
        repo = self.merged(one="main", two="both")
        repo.commit("merge")
        report = repo.check("| lib/merged.py::two | plain | test: lib/test_merged.py::Merged.test_two |")
        self.assertRefused(report, "lib/merged.py:10: merge ", "wrote lib/merged.py::two beyond git's merge",
                           "no merge-resolved row")

    def test_a_resolver_change_is_seen_when_only_one_parent_makes_the_file_one_unit(self):
        repo = self.merged(one="main", two="both", mainTail="A = 1; B = 1")
        repo.commit("merge")
        self.assertRefused(repo.check(), "wrote lib/merged.py beyond git's merge")
        report = repo.check("| lib/merged.py | merge-resolved | test: lib/test_merged.py::Merged.test_two |")
        self.assertFalse([problem for problem in report.problems if "merge" in problem], report.problems)

    def test_an_uncommitted_resolution_is_checked_too(self):
        report = self.merged(one="main", two="both").check()
        self.assertRefused(report, "wrote lib/merged.py::two beyond git's merge")

    def test_a_merge_row_must_fail_with_each_parents_version(self):
        repo = self.merged(one="main", two="both")
        repo.commit("merge")
        report = repo.check("| lib/merged.py::two | merge-resolved | test: lib/test_merged.py::Merged.test_two |")
        self.assertPasses(report, 1)
        weak = repo.check("| lib/merged.py::two | merge-resolved | test: lib/test_merged.py::Merged.test_ours_only_fails |")
        parents = repo.git("rev-parse", "--short=7", "HEAD^2").strip()
        self.assertRefused(weak, "row 1", "lib/merged.py:10", f"still passes with the code reverted to {parents}")
        self.assertEqual(len(weak.problems), 1, "it fails against the branch's version")

    def test_a_merge_row_on_code_no_resolver_wrote_is_refused(self):
        repo = self.merged(one="main", two="main")
        repo.commit("merge")
        report = repo.check("| lib/merged.py::two | merge-resolved | test: lib/test_merged.py::Merged.test_two |")
        self.assertRefused(report, "names lib/merged.py::two, which no merge's resolver wrote")


class Processes(unittest.TestCase):
    """Real tests that start a process or hang, each telling its pid through a file."""

    TESTS = """
        import os
        import subprocess
        import time
        import unittest


        def tell(pid):
            with open("pid.part", "w") as file:
                file.write(str(pid))
            os.rename("pid.part", "pid")


        class Left(unittest.TestCase):
            def test_leaves_a_process_behind(self):
                tell(subprocess.Popen(["sleep", "60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid)

            def test_hangs(self):
                tell(os.getpid())
                time.sleep(60)
    """

    def setUp(self):
        self.repo = Repo(self, {**BASE_FILES, "lib/test_left.py": self.TESTS})

    def toldPid(self):
        file = self.repo.root / "pid"
        self.waitFor(file.exists, "the test never told its pid")
        pid = int(file.read_text())
        self.addCleanup(self.killLeftover, pid)  # so a failing test doesn't wait out the sleep
        return pid

    def killLeftover(self, pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)

    def waitFor(self, done, failure):
        deadline = time.monotonic() + 20
        while not done():
            self.assertLess(time.monotonic(), deadline, failure)
            time.sleep(0.02)

    def assertDies(self, pid):
        def dead():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            return False

        self.waitFor(dead, f"process {pid} is still alive")

    def test_what_a_passing_test_leaves_running_is_killed(self):
        verdict = runTest(str(self.repo.root), "lib/test_left.py::Left.test_leaves_a_process_behind")
        self.assertEqual(verdict.state, "pass")
        self.assertDies(self.toldPid())

    def test_a_test_that_runs_out_of_time_is_killed(self):
        with mock.patch.object(provenance_runner, "TIMEOUT", 3):
            verdict = runTest(str(self.repo.root), "lib/test_left.py::Left.test_hangs")
        self.assertEqual(verdict, ("error", "ran past 3 seconds"))
        self.assertDies(self.toldPid())

    def test_stopping_kills_a_test_under_way_and_any_started_afterwards(self):
        processes, verdicts = TestProcesses(), []
        hanging = "lib/test_left.py::Left.test_hangs"
        run = threading.Thread(target=lambda: verdicts.append(runTest(str(self.repo.root), hanging, processes)))
        run.start()
        pid = self.toldPid()
        processes.stop()
        run.join(20)
        self.assertEqual([verdict.state for verdict in verdicts], ["error"])
        self.assertDies(pid)
        self.assertEqual(runTest(str(self.repo.root), hanging, processes).state, "error")


@unittest.skipUnless(shutil.which("node"), "node isn't installed")
class Node(CheckCase):
    def test_a_node_test_that_runs_out_of_time_takes_its_child_process_with_it(self):
        repo = Repo(self, {**BASE_FILES, "web/hang.test.js": """
            const test = require("node:test");
            const fs = require("node:fs");
            test("hangs", async () => {
                fs.writeFileSync("pid", String(process.pid));
                await new Promise((resolve) => setTimeout(resolve, 60000));
            });
        """})
        with mock.patch.object(provenance_runner, "TIMEOUT", 3):
            verdict = runTest(str(repo.root), "web/hang.test.js::hangs")
        self.assertEqual(verdict, ("error", "ran past 3 seconds"))
        child = int((repo.root / "pid").read_text())
        deadline = time.monotonic() + 20
        with self.assertRaises(ProcessLookupError):
            while time.monotonic() < deadline:
                os.kill(child, 0)
                time.sleep(0.02)

    def test_a_javascript_row_runs_its_node_test(self):
        repo = Repo(self, {**BASE_FILES, "web/double.js": "exports.double = (x) => x;\n"})
        repo.write({
            "web/double.js": "exports.double = (x) => x * 2;\n",
            "web/double.test.js": """
                const test = require("node:test");
                const assert = require("node:assert");
                const { double } = require("./double");
                test("doubles (a number) #1", () => assert.strictEqual(double(2), 4));
            """,
        })
        row = "| web/double.js | plain | test: web/double.test.js::{} |"
        self.assertPasses(repo.check(row.format("doubles (a number) #1")), 1)
        self.assertRefused(repo.check(row.format("no such test")), "doesn't exist")
        self.assertEqual(runTest(str(repo.root), "web/double.test.js::doubles (a number) #1").state, "pass")

    def test_a_test_that_cannot_start_is_no_verdict(self):
        repo = Repo(self, {**BASE_FILES, "web/a.test.js": ""})
        ownEnvironment()  # reads git's variable names while git can still be found
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            verdict = runTest(str(repo.root), "web/a.test.js::a")
        self.assertEqual(verdict.state, "error")
        self.assertIn("can't start", verdict.detail)

    def test_a_skipped_node_test_is_no_verdict_and_a_dead_file_is_unloaded(self):
        self.assertEqual(tapVerdict("ok 1 - a # SKIP\n", "a", 0).state, "error")
        self.assertEqual(tapVerdict("not ok 1 - web/a.test.js\n", "a", 1).state, "unloaded")

    def test_a_javascript_rule_row_passes_with_its_json_table(self):
        repo = Repo(self)
        repo.write({
            "web/price.js": "exports.price = (gold) => (gold ? 2 : 1);\n",
            "web/price.test.js": """
                const test = require("node:test");
                const assert = require("node:assert");
                const { price } = require("./price");
                test("table", () => {
                  for (const [gold, expected] of require("./cases.test.json")) assert.strictEqual(price(gold), expected);
                });
            """,
            "web/cases.test.json": "[[true, 2], [false, 1]]\n",
        })
        row = ("| web/price.js | rule | test: web/price.test.js::table; dims: gold({}); "
               "table: web/cases.test.json |")
        self.assertPasses(repo.check(row.format("yes, no")), 1)
        self.assertRefused(repo.check(row.format("yes, no, unset")), "has 2 rows", "product is 3")

    def test_a_new_es_module_with_a_new_test_file_is_proven(self):
        repo = Repo(self)
        repo.write({
            "web/new.mjs": "export const f = () => 1;\n",
            "web/new.test.mjs": """
                import test from "node:test";
                import assert from "node:assert";
                import { f } from "./new.mjs";
                test("it", () => assert.strictEqual(f(), 1));
            """,
        })
        self.assertPasses(repo.check("| web/new.mjs | plain | test: web/new.test.mjs::it |"), 1)


class CommandLine(CheckCase):
    def run_main(self, repo, *args):
        out, err = io.StringIO(), io.StringIO()
        before = os.getcwd()
        os.chdir(repo.root)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = provenance.main(list(args))
        finally:
            os.chdir(before)
        return code, out.getvalue(), err.getvalue()

    def test_the_branch_names_the_issue_whose_manifest_is_read(self):
        repo = self.priceChange()
        repo.check(PRICE_ROW)
        code, out, err = self.run_main(repo)
        self.assertEqual((code, err), (0, ""))
        header, row, total = out.splitlines()
        self.assertRegex(header, r"^provenance-check: 1 rows, 1 changed units against [0-9a-f]{7}$")
        self.assertRegex(row, r"^ok row 1 \(lib/prices\.py::price, plain\): .*  \[1 of 1, [0-9.]+s\]$")
        self.assertRegex(total, r"^provenance-check: 1 rows pass in [0-9]+s$")

    def test_each_line_is_told_as_it_is_found_not_when_the_check_ends(self):
        repo = self.priceChange()
        told = repo.root.parent / "told"
        repo.write({"lib/test_told.py": f"""
            import unittest

            from lib import prices


            class Told(unittest.TestCase):
                def test_doubles_after_the_earlier_lines_are_told(self):
                    with open({str(told)!r}) as log:
                        earlier = log.read()
                    assert "ok row 1" in earlier, earlier
                    self.assertEqual(prices.price(2), 4)
        """})

        def tell(line, refused):
            with open(told, "a") as log:
                log.write(f"{refused} {line}\n")

        manifest = repo.root / ".tmp" / KEY / "provenance.md"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("| Function | Class | Proof |\n|---|---|---|\n" + PRICE_ROW + "\n"
                            "| lib/prices.py::price | plain | test: lib/test_told.py::Told."
                            "test_doubles_after_the_earlier_lines_are_told |\n"
                            "| lib/prices.py::label | obvious | test: x |\n")
        report = provenance.check(str(repo.root), "origin/main", manifest, tell, jobs=1)
        self.assertEqual(len(report.passed), 2, report.problems)
        lines = told.read_text().splitlines()
        self.assertTrue(lines[-1].startswith("True row 3"), lines)
        self.assertEqual([line.split()[0] for line in lines], ["False", "False", "False", "True"])

    def test_a_refusal_exits_1_on_stderr(self):
        code, out, err = self.run_main(self.priceChange())
        self.assertEqual((code, out), (1, ""))
        self.assertIn("lib/prices.py:2", err)
        self.assertIn("refused, 2 problem(s)", err)

    def test_if_listed_skips_a_repo_the_config_does_not_list_before_reading_the_change(self):
        repo = self.priceChange()
        repo.git("update-ref", "-d", "refs/remotes/origin/main")
        with mock.patch("lib.config.CONFIG_FILE", str(repo.root.parent / "no-config.json")):
            code, out, _err = self.run_main(repo, "--if-listed")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^provenance-check: not checked; the machine config's provenanceCheckouts doesn't list .*/repo\n$")

    def test_if_listed_checks_a_listed_repo(self):
        repo = self.priceChange()
        config = repo.root.parent / "config.json"
        config.write_text('{"provenanceCheckouts": ["%s"]}' % repo.root)
        with mock.patch("lib.config.CONFIG_FILE", str(config)):
            self.assertEqual(self.run_main(repo, "--if-listed")[0], 1)

    def test_if_code_skips_a_change_with_no_python_or_javascript(self):
        repo = Repo(self)
        repo.write({"bin/hello": "#!/bin/sh\necho hello\n", "main.tf": "x = 1\n"})
        self.assertEqual(self.run_main(repo, "--if-code")[:2],
                         (0, "provenance-check: not checked; the change has no Python or JavaScript file\n"))
        self.assertEqual(self.run_main(repo)[0], 1, "without --if-code the same change needs rows")

    def test_if_code_checks_a_change_that_includes_python_or_javascript(self):
        for name, text in (("a.py", "A = 1\n"), ("a.mjs", "export const a = 1;\n"),
                           ("bin/tool", "#!/usr/bin/env python3\nA = 1\n")):
            with self.subTest(name=name):
                repo = Repo(self)
                repo.write({name: text, "main.tf": "x = 1\n"})
                code, _out, err = self.run_main(repo, "--if-code")
                self.assertEqual(code, 1)
                self.assertIn("main.tf changed", err)

    def test_jobs_sets_how_many_rows_are_proven_at_once(self):
        repo = self.priceChange()
        repo.check(PRICE_ROW)
        with mock.patch.object(provenance, "ThreadPoolExecutor", wraps=ThreadPoolExecutor) as pool:
            self.assertEqual(self.run_main(repo, "--jobs", "3")[0], 0)
        pool.assert_called_once_with(3)

    def test_by_default_at_least_one_job_runs_and_no_more_than_the_cores(self):
        self.assertGreaterEqual(provenance.DEFAULT_JOBS, 1)
        self.assertLessEqual(provenance.DEFAULT_JOBS, os.cpu_count())

    def test_a_hang_up_or_a_kill_stops_the_check_as_ctrl_c_does(self):
        signals = (signal.SIGHUP, signal.SIGTERM)
        for signum in signals:
            self.addCleanup(signal.signal, signum, signal.signal(signum, signal.SIG_DFL))
        provenance.stopOnHangUp()
        self.assertEqual([signal.getsignal(signum) for signum in signals], [signal.default_int_handler] * 2)

    def test_jobs_below_one_is_refused_by_name(self):
        for text in ("0", "-1", "many"):
            with self.subTest(text=text), self.assertRaisesRegex(argparse.ArgumentTypeError, "1 or more"):
                provenance.jobCount(text)
        self.assertEqual(provenance.jobCount("3"), 3)

    def test_a_folder_a_test_is_still_writing_to_does_not_stop_the_check(self):
        with mock.patch.object(tempfile, "TemporaryDirectory", wraps=tempfile.TemporaryDirectory) as folder:
            self.priceChange().check(PRICE_ROW)
        self.assertTrue(folder.call_args_list)
        self.assertTrue(all(call.kwargs.get("ignore_cleanup_errors") for call in folder.call_args_list if call.kwargs.get("prefix") == "provenance-"))

    def test_no_change_says_so(self):
        self.assertEqual(self.run_main(Repo(self))[:2], (0, "provenance-check: nothing needs a row\n"))

    def test_the_bin_script_exits_2_when_it_cannot_check(self):
        script = Path(__file__).resolve().parent.parent / "bin" / "provenance-check"
        repo = Repo(self)
        finished = subprocess.run([str(script), "--base", "no-such-ref"], cwd=repo.root, capture_output=True, text=True)
        self.assertEqual(finished.returncode, 2)
        self.assertIn("no merge base with no-such-ref; run git fetch origin", finished.stderr)


if __name__ == "__main__":
    unittest.main()
