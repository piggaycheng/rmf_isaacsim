#!/usr/bin/env python3
"""Multi-floor helpers of the MQTT fleet adapter (no ROS 2 or broker needed)."""

import math
import os
import sys
import types
import unittest
from unittest.mock import patch

import importlib.util

# The adapter module needs ROS 2 message types; run inside the RMF container
# (docker cp this file to /root/rmf_ws/tests/ and run it with python3).
for sub in ("../workspace/mqtt_fleet_adapter", "../src/custom_ws/mqtt_fleet_adapter"):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), sub)))
HAS_ROS = importlib.util.find_spec("rclpy") is not None
paho = types.ModuleType("paho")
paho.mqtt = types.ModuleType("paho.mqtt")
paho.mqtt.client = types.ModuleType("paho.mqtt.client")
if HAS_ROS:
    with patch.dict(sys.modules, {"paho": paho, "paho.mqtt": paho.mqtt, "paho.mqtt.client": paho.mqtt.client}):
        import mqtt_fleet_adapter as fa

B1 = {"rotation": 2.1500870527688436, "scale": 1.0, "translation": [-11.08572135, 8.87415396]}


@unittest.skipUnless(HAS_ROS, "rclpy not available (run inside the RMF container)")
class FloorTransformTest(unittest.TestCase):
    def test_identity_without_transform(self):
        self.assertEqual(fa.rmf_to_robot(None, 1.5, -2.0), (1.5, -2.0))
        self.assertEqual(fa.robot_to_rmf({}, 1.5, -2.0), (1.5, -2.0))

    def test_round_trip(self):
        for t in (B1, {"rotation": -0.3, "scale": 2.0, "translation": [4.0, -1.0]}):
            x, y = fa.rmf_to_robot(t, -16.214, -4.395)
            bx, by = fa.robot_to_rmf(t, x, y)
            self.assertAlmostEqual(bx, -16.214, places=9)
            self.assertAlmostEqual(by, -4.395, places=9)

    def test_office_lift_maps_to_b1_cabin(self):
        x, y = fa.rmf_to_robot(B1, -16.214, -4.395)
        self.assertAlmostEqual(x, 1.468, delta=0.01)
        self.assertAlmostEqual(y, -2.289, delta=0.01)

    def test_change_floor_payload(self):
        msg = fa.change_floor_payload("carter1", 7, "B1", (1.0, 2.0, math.pi / 2))
        self.assertEqual(msg["action"], "change_floor")
        self.assertEqual((msg["robot_id"], msg["cmd_id"], msg["floor_name"]), ("carter1", 7, "B1"))
        self.assertEqual(msg["initial_pose"], {"x": 1.0, "y": 2.0, "yaw": math.pi / 2})
        self.assertNotIn("z", msg["initial_pose"])


if __name__ == "__main__":
    unittest.main()
