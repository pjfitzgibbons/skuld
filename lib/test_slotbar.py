# lib/slotbar.py: `plan pulse` maps a key to its Desktop through a real temp slots file, and calls
# slotbar-pulse through a patched run(), so no process is spawned and no SlotBar is needed.
import os
import tempfile
import unittest
from unittest import mock

from lib import cli, slotbar


class PulseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.slotsFile = os.path.join(self.dir.name, "workspaces")
        with open(self.slotsFile, "w") as f:
            f.write("3=SK-0045\n4=CIAM-12\n")
        self.calls = []

    def tearDown(self):
        self.dir.cleanup()

    def pulse(self, key, color, fail=None):
        def fake_run(command, args, cwd):
            self.calls.append([command, *args])
            if fail:
                raise RuntimeError(fail)
            return ""

        with mock.patch.object(slotbar, "run", side_effect=fake_run):
            return slotbar.pulse(key, color, self.slotsFile, "/opt/slotbar-pulse")

    def test_pulses_the_keys_desktop(self):
        self.assertEqual(self.pulse("CIAM-12", "red"), "4")
        self.assertEqual(self.calls, [["/opt/slotbar-pulse", "4", "red"]])

    def test_accepts_green_and_off(self):
        self.pulse("SK-0045", "green")
        self.pulse("SK-0045", "off")
        self.assertEqual(self.calls, [["/opt/slotbar-pulse", "3", "green"], ["/opt/slotbar-pulse", "3", "off"]])

    def test_refuses_an_unknown_colour_without_calling_slotbar(self):
        with self.assertRaisesRegex(RuntimeError, "red, green or off"):
            self.pulse("SK-0045", "blue")
        self.assertEqual(self.calls, [])

    def test_refuses_a_key_with_no_desktop(self):
        with self.assertRaisesRegex(RuntimeError, "SK-0099 has no Desktop"):
            self.pulse("SK-0099", "red")
        self.assertEqual(self.calls, [])

    def test_passes_on_slotbar_not_running(self):
        with self.assertRaisesRegex(RuntimeError, "SlotBar isn't running"):
            self.pulse("SK-0045", "red", fail="slotbar-pulse 3 red: slotbar-pulse: SlotBar isn't running")

    def test_pulses_a_desktop_by_number_without_the_slots_file(self):
        with mock.patch.object(slotbar, "run", side_effect=lambda command, args, cwd: self.calls.append([command, *args])):
            slotbar.pulseDesktop(7, "off", "/opt/slotbar-pulse")
        self.assertEqual(self.calls, [["/opt/slotbar-pulse", "7", "off"]])

    def test_default_command_is_inside_the_installed_bundle(self):
        self.assertTrue(slotbar.DEFAULT_PULSE_COMMAND.endswith("SlotBar.app/Contents/MacOS/slotbar-pulse"))


class PlanPulseTests(unittest.TestCase):
    def test_plan_pulse_passes_the_canonical_key_colour_and_slots_file(self):
        cfg = {"slotsFile": "/tmp/slots"}
        with mock.patch.object(cli.config_mod, "load", return_value=cfg), mock.patch.object(
            cli.slotbar_mod, "pulse"
        ) as pulse:
            cli.main(["pulse", "SK-45", "green"])
        pulse.assert_called_once_with("SK-0045", "green", "/tmp/slots")

    def test_plan_pulse_needs_a_key(self):
        with mock.patch.object(cli.config_mod, "load", return_value={}), mock.patch.object(
            cli.slotbar_mod, "pulse"
        ) as pulse:
            with self.assertRaisesRegex(RuntimeError, "Pulse needs"):
                cli.main(["pulse"])
        pulse.assert_not_called()



class RunningIdTests(unittest.TestCase):
    def test_the_running_slotbars_pid(self):
        with mock.patch.object(slotbar, "run", return_value="96905\n"):
            self.assertEqual(slotbar.runningId(), "96905")

    def test_no_slotbar_running_is_blank(self):
        with mock.patch.object(slotbar, "run", side_effect=RuntimeError("pgrep -x SlotBar: exited with code 1")):
            self.assertEqual(slotbar.runningId(), "")


if __name__ == "__main__":
    unittest.main()
