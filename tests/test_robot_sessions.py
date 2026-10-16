#!/usr/bin/env python3
"""Session identity and reboot cleanup without a live broker or ROS executor."""

import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

for path in (
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../workspace/mqtt_fleet_adapter")),
    "/root/rmf_ws/src/custom_ws/mqtt_fleet_adapter",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")),
    "/root/rmf_ws/backend",
):
    sys.path.insert(0, path)

import mqtt_fleet_adapter as fa
import mock_robot
import state
from rmf_service import RMFService


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        finder = patch.object(fa, "find_backend_path", side_effect=lambda p: os.path.join(self.tmp.name, p))
        finder.start()
        self.addCleanup(finder.stop)
        self.config = os.path.join(self.tmp.name, "config.yaml")
        with open(self.config, "w") as f:
            f.write("rmf_fleet:\n  name: fleet1\n  responsive_wait: true\n")
        client = patch.object(fa.mqtt, "Client")
        client.start()
        self.addCleanup(client.stop)
        self.adapter = self.make_adapter()

    def make_adapter(self):
        adapter = fa.MqttFleetAdapter(self.config, self.config, mock_rmf=True)
        adapter.mqtt_client = MagicMock()
        adapter.fleet_waypoint_info = {
            "fleet1": {
                "all": ["home", "other"], "chargers": ["home"], "parkings": [],
                "levels": {
                    "1F": {
                        "transform": {}, "vertex_coords": [(0.0, 0.0), (10.0, 0.0)],
                        "lane_segments": [((0.0, 0.0), (10.0, 0.0))],
                    }
                },
            }
        }
        return adapter

    def register(self, session_id="boot-a", **extra):
        payload = {
            "session_id": session_id,
            "initial_location": {"x": 0.0, "y": 0.0, "yaw": 0.0, "level_name": "1F"},
            **extra,
        }
        with patch.object(fa.threading, "Thread") as worker:
            self.adapter._handle_register("r1", "fleet1", payload)
        return self.adapter.robots.get(("fleet1", "r1")), worker

    def heartbeat(self, session_id="boot-a", **extra):
        self.adapter._handle_heartbeat("r1", "fleet1", {
            "session_id": session_id, "x": 0.0, "y": 0.0, "yaw": 0.0,
            "level_name": "1F", "battery": 95.0, "status": "idle",
            "current_cmd_id": None, "localization_ready": True, **extra,
        })

    def ready(self):
        robot, _ = self.register()
        self.heartbeat()
        self.adapter._reset_session(robot, robot.session_id, False, time.time())
        self.assertFalse(robot.session_reset_pending)
        self.adapter.mqtt_client.reset_mock()
        return robot

    def ack(self):
        return json.loads(self.adapter.mqtt_client.publish.call_args.args[1])

    def test_first_registration_requires_fresh_ready_localization(self):
        robot, worker = self.register()
        self.assertTrue(robot.session_reset_pending)
        worker.return_value.start.assert_called_once()
        self.assertFalse(robot.session_ready.is_set())
        self.heartbeat(localization_ready=False)
        self.assertFalse(robot.session_ready.is_set())
        self.heartbeat(level_name="unknown")
        self.assertFalse(robot.session_ready.is_set())
        self.heartbeat(x=100.0, y=100.0)
        self.assertFalse(robot.session_ready.is_set())
        self.heartbeat(status="moving", current_cmd_id=7)
        self.assertFalse(robot.session_ready.is_set())
        self.heartbeat()
        self.assertTrue(robot.session_ready.is_set())  # (0, 0) is a valid position.

    def test_nonfinite_registration_and_heartbeat_rejected(self):
        self.register(initial_location={"x": float("nan"), "y": 0.0, "yaw": 0.0})
        self.assertEqual(self.adapter.robots, {})
        self.assertEqual(self.ack()["status"], "error")
        robot, _ = self.register()
        self.heartbeat()
        self.assertTrue(robot.session_ready.is_set())
        self.heartbeat(x=float("inf"))
        self.assertFalse(robot.session_ready.is_set())
        self.assertEqual(robot.position[0], 0.0)

    def test_available_session_restores_all_native_commission_flags(self):
        robot = self.ready()
        robot.update_handle = MagicMock()
        more = robot.update_handle.more.return_value
        commission = SimpleNamespace(
            accept_dispatched_tasks=False, accept_direct_tasks=False, perform_idle_behavior=False,
        )
        more.commission.return_value = commission
        self.adapter.mock_rmf = False
        self.adapter._sync_rmf_availability(robot)
        self.assertTrue(commission.accept_dispatched_tasks)
        self.assertTrue(commission.accept_direct_tasks)
        self.assertTrue(commission.perform_idle_behavior)
        more.unstable_recommission.assert_not_called()
        robot.is_online = False
        self.adapter._sync_rmf_availability(robot)
        self.assertFalse(commission.accept_direct_tasks)
        self.assertTrue(commission.perform_idle_behavior)
        robot.is_online = True
        robot.is_estopped = True
        self.adapter._sync_rmf_availability(robot)
        self.assertFalse(commission.perform_idle_behavior)

    def test_canceled_native_execution_cannot_send_a_late_command(self):
        self.ready()
        destination = SimpleNamespace(position=[10.0, 0.0, 0.0], map="1F")
        execution = MagicMock()
        execution.okay.return_value = False
        self.adapter._on_rmf_navigate("fleet1", "r1", destination, execution)
        self.adapter._on_rmf_localize("fleet1", "r1", destination, execution)
        self.adapter._on_rmf_action("fleet1", "r1", "gimbal_move", {}, execution)
        self.adapter.mqtt_client.publish.assert_not_called()

    def test_legacy_wait_initialization_handles_async_native_registration(self):
        robot = self.ready()
        robot.session_id = None
        robot.update_handle = MagicMock()
        more = MagicMock()
        robot.update_handle.more.side_effect = [None, more]
        with patch.object(fa.time, "sleep") as sleep:
            self.adapter._enable_legacy_responsive_wait(robot)
        sleep.assert_called_once_with(0.05)
        more.enable_responsive_wait.assert_called_once_with(True)

    def test_stale_localization_cannot_release_session_gate(self):
        robot, _ = self.register()
        self.heartbeat()
        robot.last_heartbeat = time.time() - 11.0
        self.adapter._reset_session(robot, "boot-a", False, time.time())
        self.assertTrue(robot.session_reset_pending)
        self.assertIn("Localization became unavailable", robot.session_reset_error)

    def test_duplicate_registration_preserves_execution_and_command(self):
        robot = self.ready()
        execution = MagicMock()
        robot.current_execution = execution
        robot.last_cmd_payload = {"action": "navigate", "cmd_id": robot.cmd_id}
        before = robot.cmd_id
        _, worker = self.register()
        worker.assert_not_called()
        self.assertIs(robot.current_execution, execution)
        self.assertEqual(robot.cmd_id, before)
        self.assertIsNotNone(robot.last_cmd_payload)
        execution.finished.assert_not_called()

    def test_same_session_network_reconnect_preserves_task(self):
        robot = self.ready()
        execution = MagicMock()
        robot.current_execution = execution
        self.adapter._handle_status("r1", "fleet1", {"session_id": "boot-a", "status": "offline"})
        self.assertFalse(robot.is_online)
        self.heartbeat(current_cmd_id=robot.cmd_id, status="moving")
        self.assertTrue(robot.is_online)
        self.assertIs(robot.current_execution, execution)
        self.assertFalse(robot.session_reset_pending)

    def test_new_session_discards_deferred_commands_and_timers_without_finishing(self):
        robot = self.ready()
        execution, timer = MagicMock(), MagicMock()
        robot.current_execution = execution
        robot.deferred_cmd_payload = {"action": "navigate"}
        robot.last_cmd_payload = {"action": "navigate"}
        robot.active_task = {"task_id": "old-task", "cmd_id": 5, "proceed_timer": timer}
        robot.task_refs[5] = {"task_id": "old-task"}
        robot.nav_suspended = True
        before = robot.cmd_id
        self.register("boot-b")
        self.assertIsNone(robot.current_execution)
        self.assertIsNone(robot.deferred_cmd_payload)
        self.assertIsNone(robot.last_cmd_payload)
        self.assertIsNone(robot.active_task)
        self.assertFalse(robot.nav_suspended)
        self.assertEqual(robot.task_refs, {})
        self.assertGreater(robot.cmd_id, before)
        self.assertTrue(robot.session_reset_pending)
        timer.cancel.assert_called_once()
        execution.finished.assert_not_called()
        with open(self.adapter.sessions_path) as f:
            self.assertEqual(json.load(f)["fleet1/r1"]["retired_ids"], ["boot-a"])

    def test_new_session_fences_all_rmf_command_callbacks(self):
        robot, _ = self.register()
        self.adapter.mqtt_client.reset_mock()
        destination = SimpleNamespace(position=[10.0, 0.0, 0.0], map="1F")
        execution = MagicMock()
        self.adapter._on_rmf_navigate("fleet1", "r1", destination, execution)
        self.adapter._on_rmf_localize("fleet1", "r1", destination, execution)
        self.adapter._on_rmf_action("fleet1", "r1", "gimbal_move", {}, execution)
        self.adapter._on_rmf_stop("fleet1", "r1", None)
        self.adapter._dispatch_deferred_command(robot)
        self.assertEqual(self.adapter.mqtt_client.publish.call_count, 0)
        self.assertEqual(self.adapter.dispatch_multi_step_task(
            "fleet1", "r1", [{"type": "gimbal_move", "pan_deg": 0}]
        )["status"], "error")

    def test_late_previous_session_messages_cannot_mutate_robot(self):
        robot = self.ready()
        self.register("boot-b")
        execution = MagicMock()
        robot.current_execution = execution
        self.heartbeat("boot-a", x=10.0)
        self.adapter._handle_status("r1", "fleet1", {"session_id": "boot-a", "status": "offline"})
        self.adapter._handle_deregister("r1", "fleet1", {"session_id": "boot-a"})
        self.adapter._handle_command_result("r1", "fleet1", {
            "session_id": "boot-a", "cmd_id": robot.cmd_id, "status": "completed",
        })
        self.adapter._handle_task_state("r1", "fleet1", {
            "session_id": "boot-a", "cmd_id": robot.cmd_id, "status": "succeeded",
        })
        self.assertTrue(robot.is_online)
        self.assertEqual(robot.position[0], 0.0)
        execution.finished.assert_not_called()
        self.register("boot-a")
        self.assertEqual(robot.session_id, "boot-b")
        self.assertEqual(self.ack()["status"], "error")

    def test_session_history_survives_adapter_restart(self):
        self.ready()
        self.register("boot-b")
        self.adapter = self.make_adapter()
        robot, worker = self.register("boot-b")
        self.assertEqual(robot.session_id, "boot-b")
        self.assertTrue(worker.call_args.kwargs["args"][2])  # Resume interrupted cleanup.
        self.register("boot-a")
        self.assertEqual(self.ack()["status"], "error")

    def test_session_file_write_failure_does_not_accept_new_boot(self):
        robot = self.ready()
        with patch.object(self.adapter, "_save_robot_sessions", side_effect=OSError("read only")):
            _, worker = self.register("boot-b")
        self.assertEqual(self.ack()["status"], "error")
        self.assertEqual(robot.session_id, "boot-a")
        self.assertEqual(self.adapter.robot_sessions["fleet1/r1"]["session_id"], "boot-a")
        worker.assert_not_called()

    def test_session_aware_vehicle_cannot_downgrade_to_legacy_registration(self):
        robot = self.ready()
        self.register(None)
        self.assertEqual(self.ack()["status"], "error")
        self.assertEqual(robot.session_id, "boot-a")
        self.heartbeat(None, x=10.0)
        self.assertEqual(robot.position[0], 0.0)

    def test_cleanup_cancels_queues_and_active_task_before_restoring_wait(self):
        robot, _ = self.register()
        self.heartbeat()
        robot.update_handle = MagicMock()
        more = robot.update_handle.more.return_value
        more.current_task_id.return_value = "old-task"
        self.adapter.mock_rmf = False
        with patch.object(self.adapter, "_call_session_api", return_value={}) as call:
            self.adapter._reset_session(robot, "boot-a", True, 100.0)
        self.assertEqual([c.args[0]["type"] for c in call.call_args_list], [
            "robot_commission_request", "kill_task_request", "robot_commission_request",
        ])
        request = call.call_args_list[0].args[0]
        self.assertEqual(request["pending_direct_tasks_policy"], "cancel")
        self.assertEqual(request["pending_dispatch_tasks_policy"], "cancel")
        self.assertFalse(request["commission"]["idle_behavior"])
        more.enable_responsive_wait.assert_called_once_with(True)
        self.assertFalse(robot.session_reset_pending)
        self.assertEqual(robot.session_cleanup_at, 100.0)
        with open(self.adapter.sessions_path) as f:
            self.assertEqual(json.load(f)["fleet1/r1"]["cleanup_at"], 100.0)

    def test_existing_wait_is_canceled_by_commission_not_killed_as_user_task(self):
        robot, _ = self.register()
        self.heartbeat()
        robot.update_handle = MagicMock()
        robot.update_handle.more().current_task_id.return_value = "wait.r1.fleet1-0"
        self.adapter.mock_rmf = False
        with patch.object(self.adapter, "_call_session_api", return_value={}) as call:
            self.adapter._reset_session(robot, "boot-a", False, 100.0)
        self.assertEqual(len(call.call_args_list), 2)
        self.assertFalse(robot.session_reset_pending)

    def test_cleanup_failure_stays_fenced_and_registration_can_retry(self):
        robot, _ = self.register()
        robot.update_handle = MagicMock()
        self.adapter.mock_rmf = False
        with patch.object(self.adapter, "_call_session_api", side_effect=RuntimeError("no acknowledgement")):
            self.adapter._reset_session(robot, "boot-a", True, 100.0)
        self.assertTrue(robot.session_reset_pending)
        self.assertIn("no acknowledgement", robot.session_reset_error)
        robot.update_handle.more().enable_responsive_wait.assert_not_called()
        self.adapter.mock_rmf = True
        _, worker = self.register()
        worker.return_value.start.assert_called_once()

    def test_commands_are_fenced_by_session_id(self):
        robot = self.ready()
        self.adapter._publish_command(robot, {"action": "navigate", "cmd_id": 7})
        self.assertEqual(self.ack()["session_id"], "boot-a")

    def test_malformed_persistence_fails_explicitly(self):
        os.makedirs(os.path.dirname(self.adapter.sessions_path), exist_ok=True)
        with open(self.adapter.sessions_path, "w") as f:
            json.dump({"fleet1/r1": {"session_id": 1}}, f)
        with self.assertRaises(ValueError):
            self.make_adapter()

    def test_mock_robot_session_is_stable_on_reconnect_and_new_on_restart(self):
        robot = mock_robot.MockRobot()
        other = mock_robot.MockRobot()
        self.assertNotEqual(robot.session_id, other.session_id)
        robot.client = MagicMock()
        robot.send_registration()
        sent = json.loads(robot.client.publish.call_args.args[1])
        self.assertEqual(sent["session_id"], robot.session_id)
        robot.is_registered = True
        with patch.object(robot, "send_registration") as registration:
            robot.on_connect(robot.client, None, {}, 0)
            registration.assert_not_called()
        robot.handle_command({"session_id": other.session_id, "action": "navigate", "target": {"x": 10}})
        self.assertEqual(robot.status, "idle")
        robot.handle_command({"session_id": robot.session_id, "action": "navigate", "target": {"x": 10}})
        self.assertEqual(robot.status, "moving")


class BackendSessionTest(unittest.TestCase):
    def test_only_old_nonterminal_tasks_of_rebooted_robot_are_canceled(self):
        tasks = [
            {"id": "old", "fleet_name": "f1", "robot_id": "r1", "submitted_at": 10, "status": "active"},
            {"id": "queued", "fleet_name": "f1", "robot_id": "r1", "submitted_at": 10, "status": "queued"},
            {"id": "done", "fleet_name": "f1", "robot_id": "r1", "submitted_at": 10, "status": "completed"},
            {"id": "new", "fleet_name": "f1", "robot_id": "r1", "submitted_at": 30, "status": "active"},
            {"id": "other", "fleet_name": "f2", "robot_id": "r1", "submitted_at": 10, "status": "active"},
        ]
        with patch.object(state, "current_tasks", tasks), \
                patch.object(state, "fa_state", {"robots": {"f1/r1": {"session_cleanup_at": 20}}}), \
                patch.object(state, "is_fa_state_fresh", return_value=True):
            RMFService.reconcile_vehicle_sessions()
        self.assertEqual([t["status"] for t in tasks], ["canceled", "canceled", "completed", "active", "active"])
        self.assertEqual(tasks[0]["interruption_reason"], "vehicle_session_changed")


if __name__ == "__main__":
    unittest.main()
