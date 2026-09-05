import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import vrm_battery as bridge


class MeasurementsTest(unittest.TestCase):
    def setUp(self):
        self.state = bridge.status_template("live")
        self.measurements = bridge.Measurements(self.state)

    def test_metrics_accept_zero_but_reject_invalid_values(self):
        self.measurements.set_metric("solar", 0)
        self.assertEqual(self.state["solar"]["value"], 0.0)
        self.assertEqual(self.state["solar"]["validity"], "fresh")
        self.measurements.set_metric("soc", 101)
        self.assertIsNone(self.state["soc"]["value"])
        self.assertEqual(self.state["soc"]["validity"], "missing")

    def test_home_requires_every_reported_phase(self):
        self.measurements.set_phase_count(3)
        self.measurements.set_phase(1, 120)
        self.measurements.set_phase(2, 230)
        self.assertIsNone(self.state["home"]["value"])
        self.measurements.set_phase(3, 340)
        self.assertEqual(self.state["home"]["value"], 690)

    def test_old_measurement_becomes_stale(self):
        self.measurements.set_metric("solar", 12)
        self.state["solar"]["confirmedAt"] = time.time() - bridge.FRESH_SECONDS - 1
        self.assertTrue(self.measurements.expire())
        self.assertEqual(self.state["solar"]["validity"], "stale")


class MqttParsingTest(unittest.TestCase):
    def test_preserves_confirmed_zero(self):
        self.assertEqual(bridge.payload_value(b'{"value":0}'), 0)

    def test_parses_topic_without_accepting_malformed_packet(self):
        payload = bridge.utf8("N/portal/system/0/Dc/Pv/Power") + b'{"value": 42}'
        self.assertEqual(bridge.parse_publish(payload)[0], "N/portal/system/0/Dc/Pv/Power")
        self.assertIsNone(bridge.parse_publish(b"\x00"))


class KeyringTest(unittest.TestCase):
    @patch("vrm_battery.subprocess.run")
    def test_token_is_passed_via_input_without_a_conflicting_stdin(self, run):
        run.return_value.returncode = 0
        bridge.save_secret("token-value")
        self.assertEqual(run.call_args.kwargs["input"], "token-value\n")
        self.assertNotIn("stdin", run.call_args.kwargs)


class TlsTest(unittest.TestCase):
    def test_victron_ca_is_bundled(self):
        self.assertTrue(bridge.VICTRON_CA.is_file())


if __name__ == "__main__":
    unittest.main()
