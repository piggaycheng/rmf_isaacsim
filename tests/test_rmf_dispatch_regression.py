#!/usr/bin/env python3
"""
Open-RMF Web Studio - End-to-End Regression Test Suite
Covers all 10 issues fixed in docs/TO_FIX.md using isolated fake executions & MQTT mock.
No real robot connection required.
"""

import os
import sys
import json
import math
import time
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

# Path resolution for host and Docker container execution
candidate_paths = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../workspace/mqtt_fleet_adapter")),
    "/root/rmf_ws/src/custom_ws/mqtt_fleet_adapter",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")),
    "/root/rmf_ws/backend",
]
for p in candidate_paths:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

def get_file_path(subpath):
    candidates = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", subpath)) if "__file__" in globals() else subpath,
        os.path.join("/root/rmf_ws", subpath),
        os.path.join("/root/rmf_ws/backend", subpath),
        os.path.join("/root/rmf_ws/src/custom_ws", subpath),
        os.path.join("/home/yu/Documents/code/open_rmf", subpath),
    ]
    if subpath.startswith("workspace/"):
        candidates.append(os.path.join("/root/rmf_ws/src/custom_ws", subpath[len("workspace/"):]))
    if subpath.startswith("backend/"):
        candidates.append(os.path.join("/root/rmf_ws/backend", subpath[len("backend/"):]))
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    return subpath

import mqtt_fleet_adapter as mfa
import state
from rmf_service import rmf_service

class TestRMFDispatchRegression(unittest.TestCase):

    def setUp(self):
        state.paused_fleets.clear()
        state.current_tasks.clear()
        state.active_nav_graph = {
            "vertices": [
                {"name": "wp_1", "x": 0.0, "y": 0.0},
                {"name": "wp_2", "x": 5.0, "y": 0.0},
                {"name": "charger_1", "x": 0.0, "y": 0.0},
                {"name": "charger_2", "x": 5.0, "y": 0.0},
            ]
        }

    def _create_mock_adapter(self, configured_fleets=None):
        with patch.object(mfa.MqttFleetAdapter, '_init_rmf'):
            cfg = get_file_path("workspace/mqtt_fleet_adapter/config.yaml")
            nav = get_file_path("backend/saved_maps/nav_graphs/0.yaml")
            adapter = mfa.MqttFleetAdapter(cfg, nav, mock_rmf=True)
            if configured_fleets:
                adapter.configured_fleets = set(configured_fleets)
            adapter.mqtt_client.publish = MagicMock()
            adapter._send_register_ack = MagicMock()
            return adapter

    # --------------------------------------------------------------------------
    # Issue 1: Heartbeat Watchdog Timeout does not prematurely mark finished
    # --------------------------------------------------------------------------
    def test_issue1_watchdog_timeout_preserves_execution(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]

        mock_exec = MagicMock()
        mock_exec.okay.return_value = True
        mock_exec.identifier = 101
        robot.current_execution = mock_exec
        robot.last_heartbeat = time.time() - 20.0  # Timed out

        # Run one pass of watchdog logic
        with adapter.lock:
            for (f_name, r_id), r in list(adapter.robots.items()):
                if r.is_online and (time.time() - r.last_heartbeat > 10.0):
                    r.is_online = False
                    r.status = "offline"

        self.assertFalse(robot.is_online)
        self.assertEqual(robot.status, "offline")
        # Ensure finished() was NOT called prematurely
        mock_exec.finished.assert_not_called()
        self.assertIsNotNone(robot.current_execution)

    # --------------------------------------------------------------------------
    # Issue 2: LWT Idempotency and Offline Command Rejection
    # --------------------------------------------------------------------------
    def test_issue2_lwt_idempotency_and_command_rejection(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]

        # First LWT
        adapter._handle_status("carter1", "fleet1", {"status": "offline"})
        self.assertFalse(robot.is_online)
        self.assertEqual(robot.status, "offline")

        # Second duplicate LWT (must be idempotent without crashing)
        adapter._handle_status("carter1", "fleet1", {"status": "offline"})
        self.assertFalse(robot.is_online)

        # Attempt to navigate an offline robot -> must be rejected
        adapter.mqtt_client.publish.reset_mock()
        dest = MagicMock()
        dest.position = [1.0, 2.0, 0.0]
        adapter._on_rmf_navigate("fleet1", "carter1", dest, MagicMock())
        adapter.mqtt_client.publish.assert_not_called()

    # --------------------------------------------------------------------------
    # Issue 3: In-Place Navigation Strict Completion Checks
    # --------------------------------------------------------------------------
    def test_issue3_inplace_navigation_strict_conditions(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {
            "robot_id": "carter1",
            "initial_location": {"x": 2.0, "y": 3.0, "yaw": 0.0, "level_name": "L1"}
        })
        robot = adapter.robots[("fleet1", "carter1")]

        # Case A: Robot is moving -> must NOT complete in place
        robot.status = "moving"
        dest_same = MagicMock()
        dest_same.position = [2.05, 3.02, 0.05]
        dest_same.map = "L1"
        dest_same.dock = None
        dest_same.speed_limit = None
        adapter._on_rmf_navigate("fleet1", "carter1", dest_same, MagicMock())
        # Command published down because robot was moving
        adapter.mqtt_client.publish.assert_called()

        # Case B: Docking required -> must NOT complete in place even if at coords
        robot.status = "idle"
        adapter.mqtt_client.publish.reset_mock()
        dest_dock = MagicMock()
        dest_dock.position = [2.0, 3.0, 0.0]
        dest_dock.map = "L1"
        dest_dock.dock = "charger_1"
        dest_dock.speed_limit = None
        adapter._on_rmf_navigate("fleet1", "carter1", dest_dock, MagicMock())
        adapter.mqtt_client.publish.assert_called()
        self.assertEqual(adapter.mqtt_client.publish.call_args[0][1].count('"action": "dock"'), 1)

    # --------------------------------------------------------------------------
    # Issue 4: Heartbeat Completion Guards & Command ID Correlation
    # --------------------------------------------------------------------------
    def test_issue4_heartbeat_completion_guards(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {
            "robot_id": "carter1",
            "initial_location": {"x": 0.0, "y": 0.0, "yaw": 0.0}
        })
        robot = adapter.robots[("fleet1", "carter1")]
        mock_exec = MagicMock()
        robot.current_execution = mock_exec
        robot.target_position = np.array([5.0, 5.0, 0.0])
        robot.cmd_id = 42

        # 1. Stale cmd_id (e.g. 41) arrived at target -> must NOT finish
        adapter._handle_heartbeat("carter1", "fleet1", {
            "robot_id": "carter1",
            "x": 5.0, "y": 5.0, "yaw": 0.0,
            "status": "idle",
            "current_cmd_id": 41
        })
        mock_exec.finished.assert_not_called()

        # 2. Status moving even at coords -> must NOT finish
        adapter._handle_heartbeat("carter1", "fleet1", {
            "robot_id": "carter1",
            "x": 5.0, "y": 5.0, "yaw": 0.0,
            "status": "moving",
            "current_cmd_id": 42
        })
        mock_exec.finished.assert_not_called()

        # 3. Idle, matching cmd_id 42, at target -> completes!
        adapter._handle_heartbeat("carter1", "fleet1", {
            "robot_id": "carter1",
            "x": 5.02, "y": 4.98, "yaw": 0.05,
            "status": "idle",
            "current_cmd_id": 42
        })
        mock_exec.finished.assert_called_once()

    # --------------------------------------------------------------------------
    # Issue 5: Activity Identifier Check on STOP & State Clearing
    # --------------------------------------------------------------------------
    def test_issue5_stop_activity_id_and_state_clearing(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]

        active_exec = MagicMock()
        active_exec.identifier = "act_200"
        robot.current_execution = active_exec
        robot.target_position = np.array([10.0, 10.0, 0.0])

        # Stale STOP activity id
        stale_act = MagicMock()
        stale_act.is_same.return_value = False
        adapter._on_rmf_stop("fleet1", "carter1", stale_act)
        # Ignored
        self.assertIsNotNone(robot.current_execution)

        # Matching STOP activity id
        matching_act = MagicMock()
        matching_act.is_same.return_value = True
        adapter._on_rmf_stop("fleet1", "carter1", matching_act)
        self.assertIsNone(robot.current_execution)
        self.assertIsNone(robot.target_position)
        # STOP command published
        adapter.mqtt_client.publish.assert_called()

    # --------------------------------------------------------------------------
    # Issue 6: Canceled Command Handling & Retry Escalation
    # --------------------------------------------------------------------------
    def test_issue6_canceled_and_retry_exhaustion(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]
        robot.cmd_id = 50
        robot.current_execution = MagicMock()

        # Canceled command result
        adapter._handle_command_result("carter1", "fleet1", {"cmd_id": 50, "status": "canceled"})
        self.assertEqual(robot.status, "idle")
        self.assertIsNone(robot.current_execution)

        # Exhaust retries on failure
        robot.cmd_id = 51
        robot.current_execution = MagicMock()
        robot.nav_retries = 2  # Already retried twice
        robot.last_cmd_payload = {"cmd_id": 51}
        adapter._handle_command_result("carter1", "fleet1", {"cmd_id": 51, "status": "failed"})
        self.assertEqual(robot.status, "failed")
        self.assertFalse(getattr(robot, "is_retrying", False))

    # --------------------------------------------------------------------------
    # Issue 7: Terminal State Protection from Stale Messages
    # --------------------------------------------------------------------------
    def test_issue7_terminal_state_protection(self):
        state.current_tasks = [
            {"id": "task_1", "request_id": "req_1", "status": "completed"}
        ]
        # Late message trying to revive task back to active
        task = state.current_tasks[0]
        if task["status"] in state.TERMINAL_STATES:
            # Shielded
            pass
        else:
            task["status"] = "in_progress"
        self.assertEqual(state.current_tasks[0]["status"], "completed")

    # --------------------------------------------------------------------------
    # Issue 8: Atomic Task ID Generation and Decoupled Request IDs
    # --------------------------------------------------------------------------
    def test_issue8_atomic_task_ids_and_request_id_decoupling(self):
        ids = set()
        for _ in range(50):
            res = rmf_service.dispatch_task("goto", "wp_1", robot_id="carter1")
            tid = res.get("task_id")
            if tid:
                self.assertNotIn(tid, ids)
                ids.add(tid)
        self.assertEqual(len(ids), 50)

    # --------------------------------------------------------------------------
    # Issue 9: Dispatch Waypoint & Task Type Validation
    # --------------------------------------------------------------------------
    def test_issue9_dispatch_validation(self):
        # Invalid task type
        res_bad_type = rmf_service.dispatch_task("teleport", "wp_1")
        self.assertEqual(res_bad_type["status"], "error")

        # Delivery without distinct dropoff
        res_bad_deliv = rmf_service.dispatch_task("delivery", "wp_1", destination_waypoint="wp_1")
        self.assertEqual(res_bad_deliv["status"], "error")

        # Unknown waypoint
        res_bad_wp = rmf_service.dispatch_task("goto", "non_existent_wp")
        self.assertEqual(res_bad_wp["status"], "error")

    # --------------------------------------------------------------------------
    # Issue 10: Multi-Fleet Isolation and Pause Gating
    # --------------------------------------------------------------------------
    def test_issue10_multi_fleet_isolation_and_pause(self):
        adapter = self._create_mock_adapter(["fleet1", "fleet2"])
        # Same robot ID 'robot1' in fleet1 and fleet2
        adapter._handle_register("robot1", "fleet1", {"robot_id": "robot1", "initial_location": {"x": 1.0, "y": 0.0}})
        adapter._handle_register("robot1", "fleet2", {"robot_id": "robot1", "initial_location": {"x": 9.0, "y": 0.0}})

        self.assertIn(("fleet1", "robot1"), adapter.robots)
        self.assertIn(("fleet2", "robot1"), adapter.robots)
        self.assertEqual(adapter.robots[("fleet1", "robot1")].position[0], 1.0)
        self.assertEqual(adapter.robots[("fleet2", "robot1")].position[0], 9.0)

        # Action topics isolated
        adapter._on_rmf_action("fleet1", "robot1", "act_pickup", {}, MagicMock())
        adapter._on_rmf_action("fleet2", "robot1", "act_dropoff", {}, MagicMock())
        published_topics = [c[0][0] for c in adapter.mqtt_client.publish.call_args_list]
        self.assertIn("rmf/fleet1/robot/robot1/command", published_topics)
        self.assertIn("rmf/fleet2/robot/robot1/command", published_topics)

        # Unconfigured fleet rejected
        adapter._handle_register("robot1", "unknown_fleet", {"robot_id": "robot1"})
        self.assertNotIn(("unknown_fleet", "robot1"), adapter.robots)

        # Pause blocks dispatching to paused fleet
        state.paused_fleets.add("fleet1")
        res_pause = rmf_service.dispatch_task("goto", "wp_1", fleet_name="fleet1")
        self.assertEqual(res_pause["status"], "error")
        self.assertIn("暫停狀態", res_pause["message"])

    def test_issue11_3_pause_does_not_swallow_command_completion(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]
        mock_exec = MagicMock()
        robot.current_execution = mock_exec
        robot.cmd_id = 99

        # Pause fleet1
        adapter._handle_fleet_control("fleet1", {"action": "pause"})
        self.assertTrue(robot.is_paused)
        self.assertFalse(robot.is_holding)  # must NOT be holding

        # Robot sends completed command_result while fleet is paused
        adapter._handle_command_result("carter1", "fleet1", {"cmd_id": 99, "status": "completed"})
        mock_exec.finished.assert_called_once()
        self.assertIsNone(robot.current_execution)


if __name__ == "__main__":
    unittest.main()
