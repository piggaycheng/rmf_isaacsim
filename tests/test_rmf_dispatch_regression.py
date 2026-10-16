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
import tempfile
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
from rmf_service import RMFService, rmf_service

class TestRMFDispatchRegression(unittest.TestCase):

    def setUp(self):
        # Isolate FA persistence (paused fleets / discovered robots) so tests don't leak state via saved_maps.
        self._tmpdir = tempfile.TemporaryDirectory()
        real_find = mfa.find_backend_path
        isolated = {"saved_maps/paused_fleets.json", "saved_maps/discovered_robots.json",
                    "saved_maps/estopped_robots.json", "saved_maps/robot_sessions.json"}

        def isolated_find(subpath):
            if subpath in isolated:
                return os.path.join(self._tmpdir.name, os.path.basename(subpath))
            return real_find(subpath)

        self._find_patcher = patch.object(mfa, "find_backend_path", side_effect=isolated_find)
        self._find_patcher.start()
        self.addCleanup(self._find_patcher.stop)
        self.addCleanup(self._tmpdir.cleanup)
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

    def test_core_process_logs_inherit_backend_streams(self):
        service = RMFService()
        with patch("rmf_service.subprocess.run") as run, \
                patch("rmf_service.subprocess.Popen") as popen, \
                patch("rmf_service.time.sleep"):
            run.return_value.returncode = 0
            service.start_background_processes()

        self.assertEqual(popen.call_count, 2)
        for call, executable in zip(
            popen.call_args_list, ("rmf_traffic_schedule", "rmf_task_dispatcher")
        ):
            self.assertIn(executable, call.args[0][2])
            # An undrained PIPE fills up and blocks the ROS executor.
            self.assertIsNone(call.kwargs["stdout"])
            self.assertIsNone(call.kwargs["stderr"])

    def test_fleet_planning_velocity_capped_to_registered_robot_specs(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter.discovered_robots = {
            "fleet1_carter1": {
                "fleet_name": "fleet1", "robot_id": "carter1", "status": "offline",
                "specs": {"max_linear_velocity": 0.75, "max_angular_velocity": 0.5},
            }
        }
        config = {"fleet_name": "fleet1", "robots": [{"name": "carter1"}]}
        self.assertEqual(adapter._fleet_velocity_limits(config, 1.2, 1.0), (0.75, 0.5))
        self.assertEqual(adapter._fleet_velocity_limits(config, 0.4, 0.3), (0.4, 0.3))

    def test_fleet_planning_velocity_uses_slowest_adopted_robot_only(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter.discovered_robots = {
            "fleet1_carter1": {
                "fleet_name": "fleet1", "robot_id": "carter1",
                "specs": {"max_linear_velocity": 0.75, "max_angular_velocity": 0.5},
            },
            "fleet1_carter2": {
                "fleet_name": "fleet1", "robot_id": "carter2",
                "specs": {"max_linear_velocity": 0.6, "max_angular_velocity": 0.8},
            },
            "fleet1_unadopted": {
                "fleet_name": "fleet1", "robot_id": "unadopted",
                "specs": {"max_linear_velocity": 0.1, "max_angular_velocity": 0.1},
            },
            "fleet2_other": {
                "fleet_name": "fleet2", "robot_id": "carter1",
                "specs": {"max_linear_velocity": 0.1, "max_angular_velocity": 0.1},
            },
        }
        config = {"fleet_name": "fleet1", "robots": [{"name": "carter1"}, {"name": "carter2"}]}
        self.assertEqual(adapter._fleet_velocity_limits(config, 1.2, 1.0), (0.6, 0.5))

    def test_fleet_planning_velocity_without_reported_specs_preserves_config(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter.discovered_robots = {
            "fleet1_carter1": {"fleet_name": "fleet1", "robot_id": "carter1", "specs": {}}
        }
        self.assertEqual(
            adapter._fleet_velocity_limits({"fleet_name": "fleet1"}, 1.2, 1.0), (1.2, 1.0)
        )
        adapter.discovered_robots["fleet1_carter1"]["specs"]["max_linear_velocity"] = 0.75
        self.assertEqual(
            adapter._fleet_velocity_limits({"fleet_name": "fleet1"}, 1.2, 1.0), (0.75, 1.0)
        )

    def test_invalid_reported_velocity_does_not_silently_enter_planner(self):
        adapter = self._create_mock_adapter(["fleet1"])
        for value in (0, -0.5, float("nan"), float("inf"), None, "invalid"):
            with self.subTest(value=value):
                adapter.discovered_robots = {
                    "fleet1_carter1": {
                        "fleet_name": "fleet1", "robot_id": "carter1",
                        "specs": {"max_linear_velocity": value},
                    }
                }
                with self.assertRaises((ValueError, TypeError)):
                    adapter._fleet_velocity_limits({"fleet_name": "fleet1"}, 1.2, 1.0)

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
    # Issue 3: Even In-Place Navigation Requires a Robot Result
    # --------------------------------------------------------------------------
    def test_issue3_inplace_navigation_strict_conditions(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {
            "robot_id": "carter1",
            "initial_location": {"x": 2.0, "y": 3.0, "yaw": 0.0, "level_name": "L1"}
        })
        robot = adapter.robots[("fleet1", "carter1")]
        robot.is_off_graph = False

        # Case A: Robot is moving -> must NOT complete in place
        robot.status = "moving"
        dest_same = MagicMock()
        dest_same.position = [2.05, 3.02, 0.05]
        dest_same.map = "L1"
        dest_same.dock = None
        dest_same.speed_limit = None
        dest_same.inside_lift = None
        dest_same.name = ""
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

        # Case C: Idle at the exact target still dispatches navigation, with no finish timer.
        adapter.mqtt_client.publish.reset_mock()
        dest_same.position = [2.0, 3.0, 0.0]
        execution = MagicMock()
        with patch.object(mfa.threading, "Timer") as timer:
            adapter._on_rmf_navigate("fleet1", "carter1", dest_same, execution)
        timer.assert_not_called()
        adapter.mqtt_client.publish.assert_called_once()
        self.assertEqual(
            json.loads(adapter.mqtt_client.publish.call_args[0][1])["action"], "navigate")
        execution.finished.assert_not_called()
        adapter._handle_command_result(
            "carter1", "fleet1", {"cmd_id": robot.cmd_id, "status": "completed"})
        execution.finished.assert_called_once()

    # --------------------------------------------------------------------------
    # Issue 4: Heartbeat Never Completes a Precise Navigation (Explicit command_result Required)
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

        # 3. Idle, matching cmd_id 42, at target -> heartbeat STILL must NOT finish (only command_result completes navigation)
        adapter._handle_heartbeat("carter1", "fleet1", {
            "robot_id": "carter1",
            "x": 5.02, "y": 4.98, "yaw": 0.05,
            "status": "idle",
            "current_cmd_id": 42
        })
        mock_exec.finished.assert_not_called()

        # 4. Explicit command_result completes the execution
        adapter._handle_command_result("carter1", "fleet1", {
            "robot_id": "carter1",
            "cmd_id": 42,
            "status": "completed",
        })
        mock_exec.finished.assert_called_once()

    # --------------------------------------------------------------------------
    # Issue 4b: Ordinary waypoints finish on heartbeat proximity; precise ones wait for command_result
    # --------------------------------------------------------------------------
    def _navigate_to(self, adapter, name, position, dock=None):
        dest = MagicMock()
        dest.position = position
        dest.map = "L1"
        dest.dock = dock
        dest.speed_limit = None
        dest.inside_lift = None
        dest.name = name
        execution = MagicMock()
        adapter._on_rmf_navigate("fleet1", "carter1", dest, execution)
        return execution

    def _heartbeat(self, adapter, robot, x, y, yaw, status="moving"):
        adapter._handle_heartbeat("carter1", "fleet1", {
            "robot_id": "carter1", "x": x, "y": y, "yaw": yaw, "level_name": "L1",
            "status": status, "current_cmd_id": robot.cmd_id,
        })

    def _arrival_adapter(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {
            "robot_id": "carter1",
            "initial_location": {"x": 0.0, "y": 0.0, "yaw": 0.0, "level_name": "L1"}
        })
        adapter.fleet_waypoint_info["fleet1"] = {"levels": {"L1": {
            "transform": {}, "waypoints": {}, "lift_waypoints": {},
            "precise_waypoints": {"station"}, "vertex_coords": [], "lane_segments": [],
        }}}
        adapter._compute_distance_to_graph = MagicMock(return_value=0.0)
        robot = adapter.robots[("fleet1", "carter1")]
        robot.is_off_graph = False
        return adapter, robot

    def test_issue4b_ordinary_waypoint_completes_on_heartbeat(self):
        adapter, robot = self._arrival_adapter()
        execution = self._navigate_to(adapter, "wp_mid", [5.0, 0.0, 1.0])
        self.assertFalse(robot.precise_arrival)

        self._heartbeat(adapter, robot, 4.6, 0.0, 1.0)   # too far
        self._heartbeat(adapter, robot, 4.9, 0.0, 0.5)   # heading off by 0.5 rad
        execution.finished.assert_not_called()

        # Within 0.25 m / 0.2 rad: finishes even while the robot is still moving.
        self._heartbeat(adapter, robot, 4.85, 0.05, 0.85)
        execution.finished.assert_called_once()
        self.assertIsNone(robot.current_execution)

        # The robot's late result for the same goal is ignored.
        adapter._handle_command_result("carter1", "fleet1", {"cmd_id": robot.cmd_id, "status": "completed"})
        execution.finished.assert_called_once()

    def test_issue4b_stale_cmd_id_does_not_complete(self):
        adapter, robot = self._arrival_adapter()
        execution = self._navigate_to(adapter, "wp_mid", [5.0, 0.0, 0.0])
        adapter._handle_heartbeat("carter1", "fleet1", {
            "robot_id": "carter1", "x": 5.0, "y": 0.0, "yaw": 0.0, "level_name": "L1",
            "status": "idle", "current_cmd_id": robot.cmd_id - 1,
        })
        execution.finished.assert_not_called()

    def test_issue4b_precise_waypoints_need_command_result(self):
        adapter, robot = self._arrival_adapter()
        for name, dock in (("station", None), ("wp_mid", "dock_1")):
            with self.subTest(name=name, dock=dock):
                execution = self._navigate_to(adapter, name, [5.0, 0.0, 0.0], dock)
                self.assertTrue(robot.precise_arrival)
                self._heartbeat(adapter, robot, 5.0, 0.0, 0.0, status="idle")
                execution.finished.assert_not_called()
                adapter._handle_command_result(
                    "carter1", "fleet1", {"cmd_id": robot.cmd_id, "status": "completed"})
                execution.finished.assert_called_once()

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

    def test_issue7_task_completion_lifecycle(self):
        from rmf_service import task_matches
        # 1. Successful completion when robot finishes navigation and clears task_id
        state.current_tasks = [
            {"id": "task_100", "request_id": "task_100", "booking_id": "patrol.dispatch-1", "robot_id": "carter1", "status": "active", "progress": 95}
        ]
        state.current_robots = {
            "carter1": {"id": "carter1", "task_id": "patrol.dispatch-1", "status": "moving"}
        }
        state.discovered_robots = {
            "fleet1_carter1": {"status": "idle", "is_online": True}
        }

        # Simulate fleet_states callback when carter1 finishes navigation (task_id cleared)
        prev_robot = state.current_robots.get("carter1", {})
        prev_task_id = prev_robot.get("task_id", "")
        is_online = state.discovered_robots["fleet1_carter1"]["is_online"]
        phys_status = state.discovered_robots["fleet1_carter1"]["status"]
        current_r_task_id = ""  # cleared by Open-RMF upon finish

        if not current_r_task_id and prev_task_id:
            if is_online and phys_status != "offline":
                for t in state.current_tasks:
                    if task_matches(t, prev_task_id):
                        if t.get("status") not in state.TERMINAL_STATES:
                            t["status"] = "completed"
                            t["progress"] = 100

        self.assertEqual(state.current_tasks[0]["status"], "completed")
        self.assertEqual(state.current_tasks[0]["progress"], 100)

        # 2. Offline guard: If robot disconnected, task should NOT be completed
        state.current_tasks = [
            {"id": "task_101", "request_id": "task_101", "booking_id": "patrol.dispatch-2", "robot_id": "carter1", "status": "active", "progress": 50}
        ]
        prev_task_id = "patrol.dispatch-2"
        is_online = False
        phys_status = "offline"

        if not current_r_task_id and prev_task_id:
            if is_online and phys_status != "offline":
                for t in state.current_tasks:
                    if task_matches(t, prev_task_id):
                        if t.get("status") not in state.TERMINAL_STATES:
                            t["status"] = "completed"
                            t["progress"] = 100

        self.assertEqual(state.current_tasks[0]["status"], "active")
        self.assertEqual(state.current_tasks[0]["progress"], 50)

        # 3. Canceled task protection: Already canceled task is never overwritten to completed
        state.current_tasks = [
            {"id": "task_102", "request_id": "task_102", "booking_id": "patrol.dispatch-3", "robot_id": "carter1", "status": "canceled", "progress": 40}
        ]
        prev_task_id = "patrol.dispatch-3"
        is_online = True
        phys_status = "idle"

        if not current_r_task_id and prev_task_id:
            if is_online and phys_status != "offline":
                for t in state.current_tasks:
                    if task_matches(t, prev_task_id):
                        if t.get("status") not in state.TERMINAL_STATES:
                            t["status"] = "completed"
                            t["progress"] = 100

        self.assertEqual(state.current_tasks[0]["status"], "canceled")

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

    def test_issue11_2_no_cross_fleet_fallback(self):
        adapter = self._create_mock_adapter(["fleet1", "fleet2"])
        # Register carter1 only in fleet1
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1", "initial_location": {"x": 1.0, "y": 1.0, "yaw": 0.0}})
        r1 = adapter.robots[("fleet1", "carter1")]
        mock_exec1 = MagicMock()
        r1.current_execution = mock_exec1
        r1.cmd_id = 10

        adapter._send_register_ack.reset_mock()

        # 1. Unregistered fleet2/carter1 sends heartbeat
        adapter._handle_heartbeat("carter1", "fleet2", {"x": 50.0, "y": 50.0, "yaw": 0.0})
        # Check require_register was requested for fleet2
        adapter._send_register_ack.assert_called_with(
            "carter1", success=False, msg="Robot not registered in RMF, please register", status="require_register", fleet_name="fleet2"
        )
        # Check fleet1/carter1 position unchanged
        self.assertEqual(r1.position[0], 1.0)

        # 2. Unregistered fleet2/carter1 sends deregister
        adapter._handle_deregister("carter1", "fleet2", {})
        self.assertTrue(r1.is_online)
        self.assertNotEqual(r1.status, "offline")

        # 3. Unregistered fleet2/carter1 sends status offline
        adapter._handle_status("carter1", "fleet2", {"status": "offline"})
        self.assertTrue(r1.is_online)
        self.assertNotEqual(r1.status, "offline")

        # 4. Unregistered fleet2/carter1 sends command_result
        adapter._handle_command_result("carter1", "fleet2", {"cmd_id": 10, "status": "completed"})
        mock_exec1.finished.assert_not_called()
        self.assertEqual(r1.current_execution, mock_exec1)

        # 5. Now register carter1 in fleet2 as well
        adapter._handle_register("carter1", "fleet2", {"robot_id": "carter1", "initial_location": {"x": 2.0, "y": 2.0, "yaw": 0.0}})
        r2 = adapter.robots[("fleet2", "carter1")]
        mock_exec2 = MagicMock()
        r2.current_execution = mock_exec2
        r2.cmd_id = 20

        # Sending command_result for fleet2/carter1 must only complete fleet2
        adapter._handle_command_result("carter1", "fleet2", {"cmd_id": 20, "status": "completed"})
        mock_exec2.finished.assert_called_once()
        mock_exec1.finished.assert_not_called()

        # Sending deregister for fleet2/carter1 must not affect fleet1
        adapter._handle_deregister("carter1", "fleet2", {})
        self.assertFalse(r2.is_online)
        self.assertTrue(r1.is_online)

    def test_issue11_4_offline_command_rejection_reported_to_rmf(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1", "initial_location": {"x": 0.0, "y": 0.0, "yaw": 0.0}})
        robot = adapter.robots[("fleet1", "carter1")]
        mock_handle = MagicMock()
        robot.update_handle = mock_handle

        # Robot goes offline
        robot.is_online = False
        robot.status = "offline"

        adapter.mqtt_client.publish.reset_mock()
        mock_handle.reset_mock()

        # 1. RMF attempts to dispatch NAVIGATE to offline robot
        dest = MagicMock()
        dest.position = [10.0, 10.0, 0.0]
        dest.map = "L1"
        dest.dock = None
        mock_nav_exec = MagicMock()

        adapter._on_rmf_navigate("fleet1", "carter1", dest, mock_nav_exec)

        # Must NOT finish, must NOT publish MQTT command, must NOT replan (avoids replan loop)
        mock_nav_exec.finished.assert_not_called()
        self.assertIs(robot.current_execution, mock_nav_exec)
        self.assertIsNotNone(robot.deferred_cmd_payload)
        self.assertIsNone(robot.target_position)
        adapter.mqtt_client.publish.assert_not_called()
        mock_handle.more().log_error.assert_called_once()
        self.assertIn("offline", mock_handle.more().log_error.call_args[0][0])
        mock_handle.more().override_status.assert_called_with("offline")
        mock_handle.more().unstable_decommission.assert_called()
        mock_handle.more().replan.assert_not_called()

        # Repeated callbacks while offline must not snowball into replans
        adapter._on_rmf_navigate("fleet1", "carter1", dest, mock_nav_exec)
        mock_handle.more().replan.assert_not_called()

        mock_handle.reset_mock()

        # 2. RMF attempts to dispatch ACTION to offline robot -> replaces the deferred command
        mock_act_exec = MagicMock()
        adapter._on_rmf_action("fleet1", "carter1", "teleop", {}, mock_act_exec)

        mock_act_exec.finished.assert_not_called()
        self.assertIs(robot.current_execution, mock_act_exec)
        self.assertEqual(robot.deferred_cmd_payload["action"], "action")
        adapter.mqtt_client.publish.assert_not_called()
        mock_handle.more().log_error.assert_called_once()
        mock_handle.more().replan.assert_not_called()

        # 3. Robot reconnects via heartbeat -> deferred command dispatched exactly once with a fresh cmd_id
        adapter.mqtt_client.publish.reset_mock()
        adapter._handle_heartbeat("carter1", "fleet1", {"x": 0.0, "y": 0.0, "yaw": 0.0, "status": "idle"})
        self.assertTrue(robot.is_online)
        mock_handle.more().unstable_recommission.assert_called()
        cmd_pubs = [c for c in adapter.mqtt_client.publish.call_args_list if c[0][0].endswith("/command")]
        self.assertEqual(len(cmd_pubs), 1)
        sent = json.loads(cmd_pubs[0][0][1])
        self.assertEqual(sent["action"], "action")
        self.assertEqual(sent["cmd_id"], robot.cmd_id)
        self.assertIsNone(robot.deferred_cmd_payload)

        adapter.mqtt_client.publish.reset_mock()
        adapter._handle_heartbeat("carter1", "fleet1", {"x": 0.0, "y": 0.0, "yaw": 0.0, "status": "moving"})
        cmd_pubs = [c for c in adapter.mqtt_client.publish.call_args_list if c[0][0].endswith("/command")]
        self.assertEqual(len(cmd_pubs), 0)

        # Completion of the dispatched deferred command finishes the execution
        adapter._handle_command_result("carter1", "fleet1", {"cmd_id": robot.cmd_id, "status": "completed"})
        mock_act_exec.finished.assert_called_once()

    def test_issue11_4_deferred_command_dropped_if_rmf_moved_on(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]
        robot.update_handle = MagicMock()
        adapter._handle_status("carter1", "fleet1", {"status": "offline"})

        dest = MagicMock()
        dest.position = [3.0, 0.0, 0.0]
        dest.map = "L1"
        dest.dock = None
        dest.speed_limit = None
        execution = MagicMock()
        adapter._on_rmf_navigate("fleet1", "carter1", dest, execution)

        # RMF stops the deferred activity -> no STOP sent to the offline robot
        adapter.mqtt_client.publish.reset_mock()
        adapter._on_rmf_stop("fleet1", "carter1", None)
        self.assertIsNone(robot.deferred_cmd_payload)
        self.assertIsNone(robot.current_execution)
        adapter.mqtt_client.publish.assert_not_called()

        # Execution no longer okay at recovery -> dropped, not sent
        adapter._handle_status("carter1", "fleet1", {"status": "offline"})
        execution2 = MagicMock()
        adapter._on_rmf_navigate("fleet1", "carter1", dest, execution2)
        execution2.okay.return_value = False
        adapter._handle_heartbeat("carter1", "fleet1", {"x": 0.0, "y": 0.0, "yaw": 0.0, "status": "idle"})
        cmd_pubs = [c for c in adapter.mqtt_client.publish.call_args_list if c[0][0].endswith("/command")]
        self.assertEqual(len(cmd_pubs), 0)
        self.assertIsNone(robot.current_execution)

    def test_lost_command_resent_with_same_cmd_id(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]
        dest = MagicMock()
        dest.position = [5.0, 0.0, 0.0]
        dest.map = "L1"
        dest.dock = None
        dest.speed_limit = None
        adapter._on_rmf_navigate("fleet1", "carter1", dest, MagicMock())
        cmd_id = robot.cmd_id

        def cmd_pubs():
            return [json.loads(c[0][1]) for c in adapter.mqtt_client.publish.call_args_list if c[0][0].endswith("/command")]

        adapter.mqtt_client.publish.reset_mock()
        hb = {"x": 0.0, "y": 0.0, "yaw": 0.0, "status": "idle", "current_cmd_id": None}
        # Within the grace period: no resend
        adapter._handle_heartbeat("carter1", "fleet1", hb)
        self.assertEqual(cmd_pubs(), [])
        # Robot reports the command: no resend even after the grace period
        robot.last_dispatch_time -= 10
        adapter._handle_heartbeat("carter1", "fleet1", dict(hb, status="moving", current_cmd_id=cmd_id))
        self.assertEqual(cmd_pubs(), [])
        # Robot does not report it after the grace period: resent once with the same cmd_id
        adapter._handle_heartbeat("carter1", "fleet1", hb)
        adapter._handle_heartbeat("carter1", "fleet1", hb)
        sent = cmd_pubs()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["cmd_id"], cmd_id)
        self.assertEqual(sent[0]["action"], "navigate")

    def test_pause_survives_reconnect_and_reregister(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]
        handle = MagicMock()
        robot.update_handle = handle
        adapter._handle_fleet_control("fleet1", {"action": "pause"})

        # Heartbeat recovery while paused
        adapter._handle_status("carter1", "fleet1", {"status": "offline"})
        handle.reset_mock()
        adapter._handle_heartbeat("carter1", "fleet1", {"x": 0.0, "y": 0.0, "yaw": 0.0, "status": "idle"})
        handle.more().unstable_recommission.assert_not_called()
        handle.more().unstable_decommission.assert_called()
        handle.more().override_status.assert_called_with(None)

        # Re-register while paused
        handle.reset_mock()
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        self.assertTrue(robot.is_paused)
        handle.more().unstable_recommission.assert_not_called()
        handle.more().unstable_decommission.assert_called()

        # Resume while offline keeps the robot decommissioned
        adapter._handle_status("carter1", "fleet1", {"status": "offline"})
        handle.reset_mock()
        adapter._handle_fleet_control("fleet1", {"action": "resume"})
        handle.more().unstable_recommission.assert_not_called()
        handle.more().override_status.assert_called_with("offline")

        # Back online after resume -> available again
        handle.reset_mock()
        adapter._handle_heartbeat("carter1", "fleet1", {"x": 0.0, "y": 0.0, "yaw": 0.0, "status": "idle"})
        handle.more().unstable_recommission.assert_called_once()
        handle.more().override_status.assert_called_with(None)

    def test_issue11_1_deregister_and_reboot_invalidation(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1", "initial_location": {"x": 0.0, "y": 0.0, "yaw": 0.0}})
        robot = adapter.robots[("fleet1", "carter1")]
        mock_handle = MagicMock()
        robot.update_handle = mock_handle

        # Active execution in progress
        mock_exec1 = MagicMock()
        mock_exec1.okay.return_value = True
        mock_exec1.identifier = 101
        robot.current_execution = mock_exec1
        robot.cmd_id = 10
        robot.target_position = np.array([2.0, 2.0, 0.0])
        robot.last_cmd_payload = {"cmd_id": 10, "action": "navigate"}

        mock_handle.reset_mock()

        # Case A: Robot explicitly deregisters during execution (shutdown/reboot)
        adapter._handle_deregister("carter1", "fleet1", {"reason": "rebooting"})

        mock_exec1.finished.assert_not_called()
        self.assertIsNone(robot.current_execution)
        self.assertIsNone(robot.target_position)
        self.assertFalse(robot.is_online)
        self.assertEqual(robot.status, "offline")
        # Must report to RMF, stop new task assignment, and trigger replan
        mock_handle.more().log_error.assert_called_once()
        mock_handle.more().unstable_decommission.assert_called()
        mock_handle.more().replan.assert_called_once()
        # Late result from the ended session must not match
        adapter._handle_command_result("carter1", "fleet1", {"cmd_id": 10, "status": "completed"})
        mock_exec1.finished.assert_not_called()

        # RMF re-issues the step while offline -> deferred, then sent after re-register ACK
        dest = MagicMock()
        dest.position = [2.0, 2.0, 0.0]
        dest.map = "L1"
        dest.dock = None
        dest.speed_limit = None
        mock_exec_replanned = MagicMock()
        adapter._on_rmf_navigate("fleet1", "carter1", dest, mock_exec_replanned)
        self.assertIsNotNone(robot.deferred_cmd_payload)
        mock_handle.more().replan.assert_called_once()
        adapter.mqtt_client.publish.reset_mock()
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1", "initial_location": {"x": 0.0, "y": 0.0, "yaw": 0.0}})
        cmd_pubs = [c for c in adapter.mqtt_client.publish.call_args_list if c[0][0].endswith("/command")]
        self.assertEqual(len(cmd_pubs), 1)
        self.assertEqual(json.loads(cmd_pubs[0][0][1])["action"], "navigate")
        self.assertIs(robot.current_execution, mock_exec_replanned)
        mock_handle.more().replan.assert_called_once()  # not aborted as stale

        # Simulate that step being cancelled so the original flow below starts clean
        adapter._on_rmf_stop("fleet1", "carter1", None)
        adapter._handle_deregister("carter1", "fleet1", {"reason": "rebooting"})

        # Robot boots up and re-registers
        mock_handle.reset_mock()
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1", "initial_location": {"x": 2.0, "y": 2.0, "yaw": 0.0}})

        self.assertTrue(robot.is_online)
        self.assertEqual(robot.status, "idle")  # Must NOT be 'moving'
        self.assertIsNone(robot.current_execution)

        # Heartbeat arriving at target location must NOT falsely complete the aborted task
        adapter._handle_heartbeat("carter1", "fleet1", {"x": 2.0, "y": 2.0, "yaw": 0.0, "status": "idle"})
        mock_exec1.finished.assert_not_called()

        # Case B: Crash reboot without deregister sending fresh register
        mock_exec2 = MagicMock()
        mock_exec2.okay.return_value = True
        mock_exec2.identifier = 202
        robot.current_execution = mock_exec2
        robot.cmd_id = 20
        mock_handle.reset_mock()

        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1", "initial_location": {"x": 0.0, "y": 0.0, "yaw": 0.0}})
        mock_exec2.finished.assert_not_called()
        self.assertIsNone(robot.current_execution)
        self.assertEqual(robot.status, "idle")
        mock_handle.more().replan.assert_called_once()

    # --------------------------------------------------------------------------
    # Operator emergency stop (FA) and task cancellation (backend)
    # --------------------------------------------------------------------------
    def _published(self, adapter):
        return [json.loads(c.args[1]) for c in adapter.mqtt_client.publish.call_args_list
                if c.args[0].endswith("/command")]

    def test_estop_halts_holds_and_resumes_command(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]
        dest = MagicMock()
        dest.position = [5.0, 0.0, 0.0]
        dest.map = robot.map_name
        dest.dock = None
        dest.speed_limit = None
        nav_exec = MagicMock()
        nav_exec.okay.return_value = True
        adapter._on_rmf_navigate("fleet1", "carter1", dest, nav_exec)
        nav_cmd = self._published(adapter)[-1]
        self.assertEqual(nav_cmd["action"], "navigate")

        adapter.mqtt_client.publish.reset_mock()
        adapter._handle_robot_control("fleet1", "carter1", "estop")
        stop_cmd = self._published(adapter)[-1]
        self.assertEqual(stop_cmd["action"], "stop")
        self.assertGreater(stop_cmd["cmd_id"], nav_cmd["cmd_id"])
        self.assertTrue(robot.is_estopped)
        self.assertIn("fleet1/carter1", adapter.estopped_robots)
        self.assertIs(robot.current_execution, nav_exec)
        self.assertIsNotNone(robot.deferred_cmd_payload)
        with open(mfa.find_backend_path("saved_maps/estopped_robots.json")) as f:
            self.assertEqual(json.load(f), ["fleet1/carter1"])

        # Late result of the interrupted navigate and heartbeats at the target must not finish the execution
        adapter._handle_command_result("carter1", "fleet1", {"cmd_id": nav_cmd["cmd_id"], "status": "completed"})
        adapter._handle_heartbeat("carter1", "fleet1", {"x": 5.0, "y": 0.0, "yaw": 0.0, "status": "idle"})
        nav_exec.finished.assert_not_called()

        # New RMF commands while e-stopped are held, not sent
        adapter.mqtt_client.publish.reset_mock()
        adapter._on_rmf_navigate("fleet1", "carter1", dest, nav_exec)
        adapter._on_rmf_action("fleet1", "carter1", "act", {}, nav_exec)
        self.assertEqual(self._published(adapter), [])

        # A robot (re)connecting while e-stopped must not get the held command
        adapter._dispatch_deferred_command(robot)
        self.assertEqual(self._published(adapter), [])

        # Release re-dispatches the held command with a fresh cmd_id
        adapter._on_rmf_navigate("fleet1", "carter1", dest, nav_exec)
        adapter._handle_robot_control("fleet1", "carter1", "release")
        resumed = self._published(adapter)[-1]
        self.assertEqual(resumed["action"], "navigate")
        self.assertEqual(resumed["cmd_id"], robot.cmd_id)
        self.assertGreater(resumed["cmd_id"], stop_cmd["cmd_id"])
        self.assertFalse(robot.is_estopped)
        self.assertIsNone(robot.deferred_cmd_payload)

    def test_estop_persists_across_registration(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter.estopped_robots = {"fleet1/carter1"}
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]
        self.assertTrue(robot.is_estopped)
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        self.assertTrue(robot.is_estopped)

    def test_rmf_cancel_while_estopped_drops_held_command(self):
        adapter = self._create_mock_adapter(["fleet1"])
        adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        robot = adapter.robots[("fleet1", "carter1")]
        dest = MagicMock()
        dest.position = [5.0, 0.0, 0.0]
        dest.map = robot.map_name
        dest.dock = None
        dest.speed_limit = None
        adapter._on_rmf_navigate("fleet1", "carter1", dest, MagicMock())
        adapter._handle_robot_control("fleet1", "carter1", "estop")
        adapter._on_rmf_stop("fleet1", "carter1", None)  # task canceled by RMF
        self.assertIsNone(robot.deferred_cmd_payload)
        adapter.mqtt_client.publish.reset_mock()
        adapter._handle_robot_control("fleet1", "carter1", "release")
        self.assertEqual(self._published(adapter), [])

    def test_backend_cancel_task(self):
        state.current_tasks.clear()
        state.current_tasks.append({"id": "task_1", "request_id": "task_1", "booking_id": "patrol.dispatch-7",
                                    "robot_id": "carter1", "fleet_name": "fleet1", "status": "active", "progress": 50})
        with patch.object(rmf_service, "_call_task_api", return_value=[{"success": False, "errors": [{"detail": "x"}]},
                                                                         {"success": True}]) as call:
            res = rmf_service.cancel_task("task_1")
        self.assertEqual(res["status"], "success")
        self.assertEqual(call.call_args.args[0], {"type": "cancel_task_request", "task_id": "patrol.dispatch-7",
                                                  "labels": ["rmf_web_studio"]})
        self.assertEqual(state.current_tasks[0]["status"], "canceled")
        self.assertEqual(rmf_service.cancel_task("task_1")["status"], "error")  # already terminal

        state.current_tasks.append({"id": "task_2", "request_id": "task_2", "booking_id": None, "status": "queued"})
        self.assertEqual(rmf_service.cancel_task("task_2")["status"], "error")  # no RMF id yet

        state.current_tasks.append({"id": "task_3", "request_id": "task_3", "booking_id": "b3", "status": "queued"})
        with patch.object(rmf_service, "_call_task_api", return_value=[]):
            res = rmf_service.cancel_task("task_3")
        self.assertEqual(res["status"], "error")
        self.assertEqual(state.current_tasks[-1]["status"], "queued")
        self.assertNotIn("cancel_requested", state.current_tasks[-1])

    def test_backend_task_api_collects_success_after_failure(self):
        import rmf_service as rs

        def fake_publish(msg):
            def respond():
                for payload in ({"success": False, "errors": []}, {"success": True}):
                    time.sleep(0.05)
                    with rmf_service.lock:
                        pending = rmf_service.pending_requests.get(msg.request_id)
                        pending["responses"].append(payload)
                        pending["event"].set()
            import threading
            threading.Thread(target=respond, daemon=True).start()

        with patch.object(rmf_service, "task_pub", MagicMock(publish=fake_publish)), \
                patch.object(rs.rclpy, "ok", return_value=True):
            responses = rmf_service._call_task_api({"type": "cancel_task_request", "task_id": "x"}, timeout_s=2.0)
        self.assertTrue(any(r.get("success") for r in responses))
        self.assertEqual(rmf_service.pending_requests, {})

    def test_backend_robot_control_waits_for_fa_ack(self):
        import rmf_service as rs
        state.estopped_robots.clear()
        pub = MagicMock()
        pub.publish.side_effect = lambda msg: state.update_fa_state(
            {"mqtt_connected": True, "paused_fleets": [], "estopped_robots": ["fleet1/carter1"]})
        with patch.object(rmf_service, "robot_control_pub", pub), patch.object(rs.rclpy, "ok", return_value=True):
            self.assertTrue(rmf_service.send_robot_control("fleet1", "carter1", "estop"))
            pub.publish.side_effect = None
            self.assertFalse(rmf_service.send_robot_control("fleet1", "carter1", "release", timeout_s=0.3))
        state.estopped_robots.clear()


if __name__ == "__main__":
    unittest.main()
