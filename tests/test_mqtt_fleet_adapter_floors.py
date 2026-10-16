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

    def test_prepare_floor_change_payload(self):
        msg = fa.prepare_floor_change_payload("carter1", 8, "lift_1", "1F", (-16.214, -4.395, 0.0))
        self.assertEqual(msg["action"], "prepare_floor_change")
        self.assertEqual(
            (msg["robot_id"], msg["cmd_id"], msg["lift_name"], msg["floor_name"]),
            ("carter1", 8, "lift_1", "1F"),
        )
        self.assertEqual(msg["pose"], {"x": -16.214, "y": -4.395, "yaw": 0.0})

    def test_transform_pose_between_levels(self):
        x, y, yaw = fa.transform_pose_between_levels({}, B1, (-16.214, -4.395, 0.1))
        self.assertAlmostEqual(x, 1.468, delta=0.01)
        self.assertAlmostEqual(y, -2.289, delta=0.01)
        self.assertAlmostEqual(yaw, 0.1 + B1["rotation"], places=9)
        back = fa.transform_pose_between_levels(B1, {}, (x, y, yaw))
        for a, b in zip(back, (-16.214, -4.395, 0.1)):
            self.assertAlmostEqual(a, b, places=9)
        # yaw is wrapped to (-pi, pi]
        _, _, wrapped = fa.transform_pose_between_levels({}, B1, (0.0, 0.0, 3.0))
        self.assertAlmostEqual(wrapped, 3.0 + B1["rotation"] - 2 * math.pi, places=9)

    def test_lift_boarding_dispatches_prepare_floor_change_before_finishing(self):
        self._exercise_floor_trip([-14.0, -4.395, 0.0])

    def test_change_floor_uses_recorded_entry_pose(self):
        # Robot froze localization slightly off-centre and rotated; that pose is carried to B1.
        self._exercise_floor_trip(
            [-14.0, -4.395, 0.0], prepare_location={"x": -16.1, "y": -4.3, "yaw": 3.0},
            expected_initial=fa.transform_pose_between_levels({}, B1, (-16.1, -4.3, 3.0)))

    def test_change_floor_entry_pose_far_from_cabin_keeps_heading_only(self):
        _, _, yaw = fa.transform_pose_between_levels({}, B1, (-10.0, -4.395, 0.4))
        self._exercise_floor_trip(
            [-14.0, -4.395, 0.0], prepare_location={"x": -10.0, "y": -4.395, "yaw": 0.4},
            expected_initial=(1.468, -2.289, yaw))

    def test_change_floor_without_entry_pose_uses_rmf_cabin_pose(self):
        self._exercise_floor_trip([-14.0, -4.395, 0.0], drop_entry_pose=True,
                                  expected_initial=(1.468, -2.289, 2.15))

    def test_lift_boarding_at_cabin_still_requires_navigation_result(self):
        self._exercise_floor_trip([-16.214, -4.395, 0.0])

    def test_failed_prepare_does_not_finish_boarding(self):
        self._exercise_floor_trip([-14.0, -4.395, 0.0], prepare_status="failed")

    def test_canceled_prepare_does_not_finish_boarding(self):
        self._exercise_floor_trip([-14.0, -4.395, 0.0], prepare_status="canceled")

    def _exercise_floor_trip(self, start_position, prepare_status="completed", prepare_location=None,
                             expected_initial=None, drop_entry_pose=False):
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
            position=np.array(start_position, dtype=np.float64),
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

        # Neither telemetry nor an uncorrelated/incomplete result may start preparation.
        for reported_id in (nav_cmd_id, nav_cmd_id - 1, None):
            adapter._handle_heartbeat(
                "r1", "tinyRobot",
                {"x": -16.214, "y": -4.395, "yaw": 0.0, "battery": 90.0,
                 "status": "idle", "level_name": "1F", "current_cmd_id": reported_id},
            )
            self.assertEqual(len(published), 1)
            self.assertFalse(board_exec.done)
        for result in ({"cmd_id": nav_cmd_id - 1, "status": "completed"},
                       {"cmd_id": nav_cmd_id}):
            adapter._handle_command_result("r1", "tinyRobot", result)
            self.assertEqual(len(published), 1)
            self.assertFalse(board_exec.done)

        # Step 2: Robot finishes navigate into cabin via command_result -> FA dispatches prepare_floor_change and does NOT finish board_exec yet
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
        self.assertEqual(published[-1][1]["action"], "prepare_floor_change")
        self.assertEqual(published[-1][1]["lift_name"], "lift_1")
        prepare_cmd_id = published[-1][1]["cmd_id"]
        self.assertNotEqual(prepare_cmd_id, nav_cmd_id)
        self.assertFalse(robot.nav_suspended)

        # Delayed navigation results and heartbeats cannot acknowledge preparation.
        adapter._handle_command_result(
            "r1", "tinyRobot", {"cmd_id": nav_cmd_id, "status": "completed"})
        adapter._handle_heartbeat(
            "r1", "tinyRobot",
            {"x": -16.214, "y": -4.395, "yaw": 0.0, "status": "idle",
             "current_cmd_id": prepare_cmd_id},
        )
        self.assertEqual(len(published), 2)
        self.assertFalse(board_exec.done)
        adapter._handle_command_result(
            "r1", "tinyRobot", {"cmd_id": prepare_cmd_id})
        self.assertFalse(board_exec.done)
        self.assertFalse(robot.nav_suspended)

        # Step 2b: Robot confirms prepare_floor_change -> now board_exec finishes (allowing lift to close door & move)
        prepare_result = {"cmd_id": prepare_cmd_id, "status": prepare_status}
        if prepare_location is not None:
            prepare_result["final_location"] = prepare_location
        adapter._handle_command_result("r1", "tinyRobot", prepare_result)
        if prepare_status != "completed":
            self.assertFalse(board_exec.done)
            self.assertFalse(robot.nav_suspended)
            adapter._handle_heartbeat(
                "r1", "tinyRobot",
                {"x": -16.214, "y": -4.395, "yaw": 0.0, "status": "idle"},
            )
            self.assertFalse(board_exec.done)
            self.assertEqual(len(published), 2)
            return
        self.assertTrue(board_exec.done)
        self.assertTrue(robot.nav_suspended)
        entry = prepare_location or {"x": -16.214, "y": -4.395, "yaw": 0.0}
        self.assertEqual(robot.lift_entry_pose[0], "1F")
        self.assertEqual(list(robot.lift_entry_pose[1]), [entry["x"], entry["y"], entry["yaw"]])
        if drop_entry_pose:
            robot.lift_entry_pose = None

        # Step 3-5: While in lift with nav_suspended, drifted heartbeats must NOT mark robot off-graph or move its pose
        adapter._handle_heartbeat(
            "r1",
            "tinyRobot",
            {"robot_id": "r1", "x": 999.0, "y": 999.0, "yaw": 1.2, "battery": 88.0, "status": "idle", "level_name": "1F"},
        )
        self.assertFalse(robot.is_off_graph)
        self.assertAlmostEqual(float(robot.position[0]), entry["x"], places=3)

        # Step 6: Lift opens on B1 -> RMF triggers localize -> FA dispatches change_floor
        loc_exec = DummyExec()
        dest_b1 = types.SimpleNamespace(position=[1.468, -2.289, 2.15], map="B1")
        adapter._on_rmf_localize("tinyRobot", "r1", dest_b1, loc_exec)
        self.assertEqual(published[-1][1]["action"], "change_floor")
        initial = published[-1][1]["initial_pose"]
        if expected_initial is None:
            expected_initial = fa.transform_pose_between_levels(
                {}, B1, (entry["x"], entry["y"], entry["yaw"]))
        for key, value in zip(("x", "y", "yaw"), expected_initial):
            self.assertAlmostEqual(initial[key], value, delta=0.01)
        cf_cmd_id = published[-1][1]["cmd_id"]
        self.assertFalse(loc_exec.done)

        adapter._handle_heartbeat(
            "r1", "tinyRobot",
            {"x": 1.468, "y": -2.289, "yaw": 0.0, "status": "idle",
             "level_name": "B1", "current_cmd_id": cf_cmd_id},
        )
        adapter._handle_command_result(
            "r1", "tinyRobot", {"cmd_id": prepare_cmd_id, "status": "completed"})
        self.assertFalse(loc_exec.done)
        self.assertTrue(robot.nav_suspended)
        self.assertEqual(robot.map_name, "1F")

        # Step 7: Robot finishes relocalization and re-enables navigation -> loc_exec finishes
        adapter._handle_command_result(
            "r1",
            "tinyRobot",
            {"cmd_id": cf_cmd_id, "status": "completed"},
        )
        self.assertTrue(loc_exec.done)
        self.assertFalse(robot.nav_suspended)
        self.assertEqual(robot.map_name, "B1")
        self.assertIsNone(robot.lift_entry_pose)

        exit_exec = DummyExec()
        adapter._on_rmf_navigate(
            "tinyRobot", "r1",
            types.SimpleNamespace(
                position=[3.0, -2.289, 0.0], map="B1", dock=None, speed_limit=None),
            exit_exec,
        )
        self.assertEqual(published[-1][1]["action"], "navigate")
        self.assertEqual(published[-1][1]["target"]["level_name"], "B1")
        self.assertFalse(exit_exec.done)
        adapter._handle_command_result(
            "r1", "tinyRobot",
            {"cmd_id": published[-1][1]["cmd_id"], "status": "completed"},
        )
        self.assertTrue(exit_exec.done)


if __name__ == "__main__":
    unittest.main()
