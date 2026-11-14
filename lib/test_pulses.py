# lib/pulses.py: `plan pulse` and the pulses file, through real temp issues and pulses files. No
# process is run and no viewer is needed.
import os
import tempfile
import threading
import unittest
from unittest import mock

from lib import cli, pulses, term


class PulseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.issuesFile = os.path.join(self.dir.name, "issues")
        self.pulsesFile = os.path.join(self.dir.name, "pulses")
        with open(self.issuesFile, "w") as f:
            f.write("SK-0045\nCIAM-12\n")

    def pulse(self, key, color):
        pulses.pulse(key, color, self.issuesFile, self.pulsesFile)

    def read(self):
        with open(self.pulsesFile) as f:
            return f.read()

    def test_a_pulse_line_takes_the_same_keys_as_the_issues_file(self):
        self.assertFalse(pulses.PULSE_LINE.match("C-1=orange"))
        self.assertTrue(pulses.PULSE_LINE.match("C1-1=orange"))

    def test_a_pulse_writes_the_keys_colour(self):
        self.pulse("CIAM-12", "orange")
        self.assertEqual(self.read(), "CIAM-12=orange\n")

    def test_a_second_colour_replaces_the_keys_line(self):
        self.pulse("SK-0045", "green")
        self.pulse("CIAM-12", "orange")
        self.pulse("SK-0045", "orange")
        self.assertEqual(pulses.readPulses(self.pulsesFile), {"SK-0045": "orange", "CIAM-12": "orange"})

    def test_off_removes_the_keys_line(self):
        self.pulse("SK-0045", "green")
        self.pulse("CIAM-12", "orange")
        self.pulse("SK-0045", "off")
        self.assertEqual(self.read(), "CIAM-12=orange\n")

    def test_off_with_no_file_writes_nothing(self):
        self.pulse("SK-0045", "off")
        self.assertFalse(os.path.exists(self.pulsesFile))

    def test_refuses_an_unknown_colour_without_writing(self):
        with self.assertRaisesRegex(RuntimeError, "orange, green or off"):
            self.pulse("SK-0045", "blue")
        self.assertFalse(os.path.exists(self.pulsesFile))

    def test_refuses_a_key_the_issues_file_doesnt_list(self):
        with self.assertRaisesRegex(RuntimeError, "SK-0099 is not in the issues file; Start or Open it first"):
            self.pulse("SK-0099", "orange")
        self.assertFalse(os.path.exists(self.pulsesFile))

    def test_a_missing_issues_file_lists_no_key(self):
        os.remove(self.issuesFile)
        with self.assertRaisesRegex(RuntimeError, "not in the issues file"):
            self.pulse("SK-0045", "orange")

    def test_a_missing_pulses_file_has_no_colours(self):
        self.assertEqual(pulses.readPulses(self.pulsesFile), {})

    def test_invalid_lines_are_ignored_and_dropped_on_the_next_write(self):
        with open(self.pulsesFile, "w") as f:
            f.write("SK-0045=green\nCIAM-12=blue\n4=SK-0045\nCIAM-1=ora")
        self.assertEqual(pulses.readPulses(self.pulsesFile), {"SK-0045": "green"})
        self.pulse("CIAM-12", "orange")
        self.assertEqual(self.read(), "SK-0045=green\nCIAM-12=orange\n")

    def test_writing_leaves_no_temp_file_behind(self):
        self.pulse("SK-0045", "green")
        self.assertEqual(sorted(os.listdir(self.dir.name)), ["issues", "pulses", "pulses.lock"])

    # Review finding 1: setPulse holds the pulses file's lock for its whole read-modify-write.
    def test_a_pulse_waits_for_the_lock(self):
        thread = threading.Thread(target=self.pulse, args=("SK-0045", "green"))
        with pulses.term.locked(self.pulsesFile):
            thread.start()
            thread.join(0.3)
            self.assertEqual(pulses.readPulses(self.pulsesFile), {})
        thread.join(5)
        self.assertEqual(pulses.readPulses(self.pulsesFile), {"SK-0045": "green"})

    def test_a_pulse_reads_the_file_after_it_gets_the_lock(self):
        thread = threading.Thread(target=self.pulse, args=("SK-0045", "green"))
        with pulses.term.locked(self.pulsesFile):
            thread.start()
            thread.join(0.3)
            pulses.writePulses({"CIAM-12": "orange"}, self.pulsesFile)
        thread.join(5)
        self.assertEqual(pulses.readPulses(self.pulsesFile), {"CIAM-12": "orange", "SK-0045": "green"})

    def test_pulses_at_once_lose_no_colour(self):
        keys = [f"SK-{n:04d}" for n in range(1, 21)]
        threads = [threading.Thread(target=pulses.setPulse, args=(key, "orange", self.pulsesFile)) for key in keys]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(pulses.readPulses(self.pulsesFile), {key: "orange" for key in keys})

    def test_the_default_file_is_in_plans_cache(self):
        self.assertEqual(term.DEFAULT_PULSES_FILE, "~/.cache/plan/pulses")


class PlanPulseTests(unittest.TestCase):
    def test_plan_pulse_passes_the_canonical_key_colour_and_both_files(self):
        cfg = {"issuesFile": "/c/issues", "pulsesFile": "/c/pulses"}
        with mock.patch.object(cli.config_mod, "load", return_value=cfg), mock.patch.object(
            cli.pulses_mod, "pulse"
        ) as pulse:
            cli.main(["pulse", "SK-45", "green"])
        pulse.assert_called_once_with("SK-0045", "green", "/c/issues", "/c/pulses")

    def test_plan_pulse_needs_a_key(self):
        with mock.patch.object(cli.config_mod, "load", return_value={}), mock.patch.object(
            cli.pulses_mod, "pulse"
        ) as pulse:
            with self.assertRaisesRegex(RuntimeError, "Pulse needs"):
                cli.main(["pulse"])
        pulse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
