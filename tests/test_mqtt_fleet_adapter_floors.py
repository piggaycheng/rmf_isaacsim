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

    def test_suspend_navigation_payload(self):
        msg = fa.suspend_navigation_payload("carter1", 8, "lift_1", "1F", (-16.214, -4.395, 0.0))
        self.assertEqual(msg["action"], "suspend_navigation")
        self.assertEqual(
            (msg["robot_id"], msg["cmd_id"], msg["lift_name"], msg["floor_name"]),
            ("carter1", 8, "lift_1", "1F"),
        )
        self.assertEqual(msg["pose"], {"x": -16.214, "y": -4.395, "yaw": 0.0})

    def test_lift_boarding_dispatches_suspend_navigation_before_finishing(self):
        import json
        import threading
        import numpy as np

        adapter = fa.MqttFleetAdapter.__new__(fa.MqttFleetAdapter)
        adapter.lock = threading.RLock()
        adapter.topic_prefix = "rmf"
        adapter.fleet_name = "tinyRobot"
        adapter.configured_fleets = {"tinyRobot"}
        adapter.mock_rmf = True
        adapter.discovered_robots = {}
        adapter.fleet_waypoint_info = {
            "tinyRobot": {
                "levels": {
                    "1F": {
                        "transform": {},
                        "vertex_coords": [(-14.0, -4.395), (-16.214, -4.395)],
                        "lane_segments": [((-14.0, -4.395), (-16.214, -4.395))],
                        "waypoints": {"lift_1_1F_front": (-14.0, -4.395), "lift_1_1F": (-16.214, -4.395)},
                        "lift_waypoints": {"lift_1_1F": "lift_1"},
                        "lift_cabins": [("lift_1", -16.214, -4.395)],
                    },
                    "B1": {
                        "transform": B1,
                        "vertex_coords": [(-16.214, -4.395)],
                        "lane_segments": [],
                        "waypoints": {"lift_1_B1": (-16.214, -4.395)},
                        "lift_waypoints": {"lift_1_B1": "lift_1"},
                        "lift_cabins": [("lift_1", -16.214, -4.395)],
                    },
                }
            }
        }
        published = []

        class DummyMQTT:
            def publish(self, topic, payload, qos=0):
                published.append((topic, json.loads(payload), qos))

        adapter.mqtt_client = DummyMQTT()
        adapter._publish_task_status = lambda *args, **kwargs: None
        adapter._save_discovered_robots = lambda: None

        robot = fa.ManagedRobot(
            name="r1",
            map_name="1F",
            position=np.array([-14.0, -4.395, 0.0], dtype=np.float64),
            battery_soc=0.9,
            fleet_name="tinyRobot",
        )
        adapter.robots = {("tinyRobot", "r1"): robot}

        class DummyExec:
            def __init__(self):
                self.done = False
                self.identifier = "act"

            def finished(self):
                self.done = True

            def okay(self):
                return not self.done

        # Step 1: RMF dispatches navigate into lift cabin
        board_exec = DummyExec()
        dest_1f_lift = types.SimpleNamespace(
            position=[-16.214, -4.395, 0.0],
            map="1F",
            dock=None,
            speed_limit=None,
            name="lift_1_1F",
            inside_lift="lift_1",
        )
        adapter._on_rmf_navigate("tinyRobot", "r1", dest_1f_lift, board_exec)
        self.assertEqual(len(published), 1)
        self.assertEqual(published[-1][1]["action"], "navigate")
        nav_cmd_id = published[-1][1]["cmd_id"]
        self.assertEqual(robot.pending_suspend_lift, "lift_1")
        self.assertFalse(board_exec.done)

        # Step 2: Robot finishes navigate into cabin -> FA must dispatch suspend_navigation and NOT finish board_exec yet
        adapter._handle_command_result(
            "r1",
            "tinyRobot",
            {
                "cmd_id": nav_cmd_id,
                "status": "completed",
                "final_location": {"x": -16.214, "y": -4.395, "yaw": 0.0},
            },
        )
        self.assertFalse(board_exec.done)
        self.assertEqual(len(published), 2)
        self.assertEqual(published[-1][1]["action"], "suspend_navigation")
        self.assertEqual(published[-1][1]["lift_name"], "lift_1")
        suspend_cmd_id = published[-1][1]["cmd_id"]

        # Step 2b: Robot confirms suspend_navigation -> now board_exec finishes (allowing lift to close door & move)
        adapter._handle_command_result(
            "r1",
            "tinyRobot",
            {"cmd_id": suspend_cmd_id, "status": "completed"},
        )
        self.assertTrue(board_exec.done)
        self.assertTrue(robot.nav_suspended)

        # Step 3-5: While in lift with nav_suspended, drifted heartbeats must NOT mark robot off-graph or move its pose
        adapter._handle_heartbeat(
            "r1",
            "tinyRobot",
            {"robot_id": "r1", "x": 999.0, "y": 999.0, "yaw": 1.2, "battery": 88.0, "status": "idle", "level_name": "1F"},
        )
        self.assertFalse(robot.is_off_graph)
        self.assertAlmostEqual(float(robot.position[0]), -16.214, places=3)

        # Step 6: Lift opens on B1 -> RMF triggers localize -> FA dispatches change_floor
        loc_exec = DummyExec()
        dest_b1 = types.SimpleNamespace(position=[1.468, -2.289, 0.0], map="B1")
        adapter._on_rmf_localize("tinyRobot", "r1", dest_b1, loc_exec)
        self.assertEqual(published[-1][1]["action"], "change_floor")
        cf_cmd_id = published[-1][1]["cmd_id"]
        self.assertFalse(loc_exec.done)

        # Step 7: Robot finishes relocalization and re-enables navigation -> loc_exec finishes
        adapter._handle_command_result(
            "r1",
            "tinyRobot",
            {"cmd_id": cf_cmd_id, "status": "completed"},
        )
        self.assertTrue(loc_exec.done)
        self.assertFalse(robot.nav_suspended)
        self.assertEqual(robot.map_name, "B1")


if __name__ == "__main__":
    unittest.main()
