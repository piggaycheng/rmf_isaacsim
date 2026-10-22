#!/usr/bin/env python3
"""
Unit tests for the multi-step task flow of the MQTT Fleet Adapter (see the vehicle-side
docs/multi_step_task.md): the robot halts at a release gate after each step and only the
adapter's 'proceed' starts the next one. Uses a mocked MQTT client; no robot or RMF needed.
"""

import os
import sys
import json
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

candidate_paths = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../workspace/mqtt_fleet_adapter")),
    "/root/rmf_ws/src/custom_ws/mqtt_fleet_adapter",
]
for p in candidate_paths:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)


def get_file_path(subpath):
    candidates = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", subpath)),
        os.path.join("/root/rmf_ws", subpath),
    ]
    if subpath.startswith("workspace/"):
        candidates.append(os.path.join("/root/rmf_ws/src/custom_ws", subpath[len("workspace/"):]))
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    return subpath


import mqtt_fleet_adapter as mfa

GIMBAL = {"type": "gimbal_move", "pan_deg": 30.0, "tilt_deg": 0.0}
NAV = {"type": "navigate", "x": 1.0, "y": 0.0, "yaw": 0.0}


class TestMultiStepAdapter(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        real_find = mfa.find_backend_path
        isolated = {"saved_maps/paused_fleets.json", "saved_maps/discovered_robots.json",
                    "saved_maps/estopped_robots.json", "saved_maps/robot_sessions.json",
                    "saved_maps/adapter_reload.json"}

        def isolated_find(subpath):
            if subpath in isolated:
                return os.path.join(self._tmpdir.name, os.path.basename(subpath))
            return real_find(subpath)

        patcher = patch.object(mfa, "find_backend_path", side_effect=isolated_find)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmpdir.cleanup)

        with patch.object(mfa.MqttFleetAdapter, "_init_rmf"):
            self.adapter = mfa.MqttFleetAdapter(
                get_file_path("workspace/mqtt_fleet_adapter/config.yaml"),
                get_file_path("backend/saved_maps/nav_graphs/0.yaml"),
                mock_rmf=True,
            )
        self.adapter.configured_fleets = {"fleet1"}
        self.adapter.mqtt_client.publish = MagicMock()
        self.adapter._send_register_ack = MagicMock()
        self.adapter._publish_task_status = MagicMock()
        self.adapter.paused_fleets.clear()
        self.adapter.estopped_robots.clear()
        self.adapter._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        self.robot = self.adapter.robots[("fleet1", "carter1")]
        self.robot.is_off_graph = False
        self.robot.update_handle = MagicMock()

    # ---- helpers -----------------------------------------------------------
    def call(self, fn, *args, **kwargs):
        """Run fn in a thread; a deadlock on the (non-reentrant) adapter lock fails the test."""
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("v", fn(*args, **kwargs)), daemon=True)
        t.start()
        t.join(3.0)
        self.assertFalse(t.is_alive(), f"{getattr(fn, '__name__', fn)} deadlocked")
        return result.get("v")

    def commands(self):
        return [json.loads(c.args[1]) for c in self.adapter.mqtt_client.publish.call_args_list
                if c.args[0].endswith("/command")]

    def actions(self):
        return [c["action"] for c in self.commands()]

    def statuses(self):
        return [c.args[3] for c in self.adapter._publish_task_status.call_args_list]

    def state(self, cmd_id, status, step=0, steps=2, message=""):
        self.call(self.adapter._handle_task_state, "carter1", "fleet1",
                  {"cmd_id": cmd_id, "status": status, "step": step, "steps": steps, "message": message})

    def execution(self):
        ex = MagicMock()
        ex.okay.return_value = True
        return ex

    def task_command(self, **payload):
        msg = MagicMock()
        msg.data = json.dumps(dict({"fleet_name": "fleet1", "robot_id": "carter1"}, **payload))
        self.call(self.adapter._on_ros2_task_command, msg)

    # ---- gating ------------------------------------------------------------
    def test_backend_task_waits_for_manual_proceed_by_default(self):
        self.task_command(action="task", steps=[GIMBAL, NAV], task_id="T1")
        cmd = self.commands()[-1]
        self.assertEqual(cmd["action"], "task")
        self.assertNotIn("auto_proceed", cmd)
        self.assertFalse(self.robot.active_task["auto_proceed"])
        self.assertIsNone(self.robot.current_execution)  # direct tasks are not RMF executions

        self.state(cmd["cmd_id"], "running", 0)
        self.state(cmd["cmd_id"], "waiting", 0)
        self.assertNotIn("proceed", self.actions())

        self.task_command(action="proceed", cmd_id=cmd["cmd_id"])
        proceed = self.commands()[-1]
        self.assertEqual((proceed["action"], proceed["cmd_id"]), ("proceed", cmd["cmd_id"]))
        # A second proceed for the same gate is not sent (robot is no longer waiting).
        self.task_command(action="proceed", cmd_id=cmd["cmd_id"])
        self.assertEqual(self.actions().count("proceed"), 1)

    def test_auto_proceed_without_delay_does_not_deadlock(self):
        res = self.call(self.adapter.dispatch_multi_step_task, "fleet1", "carter1", [GIMBAL, GIMBAL],
                        auto_proceed=True)
        self.state(res["cmd_id"], "waiting", 0)
        self.assertEqual(self.actions()[-1], "proceed")

    def test_auto_proceed_timer_sends_after_delay(self):
        res = self.call(self.adapter.dispatch_multi_step_task, "fleet1", "carter1", [GIMBAL, GIMBAL],
                        auto_proceed=True, step_delay=0.05)
        self.state(res["cmd_id"], "waiting", 0)
        timer = self.robot.active_task["proceed_timer"]
        timer.join(2.0)
        self.assertEqual(self.actions()[-1], "proceed")

    def test_proceed_held_while_paused_then_sent_on_resume(self):
        res = self.call(self.adapter.dispatch_multi_step_task, "fleet1", "carter1", [GIMBAL, GIMBAL])
        self.call(self.adapter._handle_fleet_control, "fleet1", {"action": "pause"})
        self.state(res["cmd_id"], "waiting", 0)
        self.task_command(action="proceed", cmd_id=res["cmd_id"])
        self.assertNotIn("proceed", self.actions())
        self.call(self.adapter._handle_fleet_control, "fleet1", {"action": "resume"})
        self.assertEqual(self.actions()[-1], "proceed")

    def test_proceed_with_wrong_cmd_id_is_ignored(self):
        res = self.call(self.adapter.dispatch_multi_step_task, "fleet1", "carter1", [GIMBAL, GIMBAL])
        self.state(res["cmd_id"], "waiting", 0)
        self.task_command(action="proceed", cmd_id=res["cmd_id"] + 99)
        self.assertNotIn("proceed", self.actions())

    # ---- RMF actions -------------------------------------------------------
    def test_rmf_gimbal_action_dispatches_and_finishes_execution(self):
        ex = self.execution()
        self.call(self.adapter._on_rmf_action, "fleet1", "carter1", "gimbal_move",
                  json.dumps({"pan_deg": 45, "tilt_deg": -10}), ex)
        cmd = self.commands()[-1]
        self.assertEqual(cmd["action"], "gimbal_move")
        self.assertEqual((cmd["pan_deg"], cmd["tilt_deg"]), (45.0, -10.0))
        self.assertNotIn("steps", cmd)
        self.assertIsNone(self.robot.active_task)
        self.assertIs(self.robot.current_execution, ex)

        # The robot also publishes task_state for the command; only command_result finishes it.
        self.state(cmd["cmd_id"], "succeeded", 0, steps=1)
        ex.finished.assert_not_called()
        self.call(self.adapter._handle_command_result, "carter1", "fleet1", {"cmd_id": cmd["cmd_id"], "status": "completed"})
        ex.finished.assert_called_once()
        self.assertIsNone(self.robot.current_execution)

    def test_rmf_gimbal_action_with_invalid_angle_fails_cleanly(self):
        ex = self.execution()
        self.call(self.adapter._on_rmf_action, "fleet1", "carter1", "gimbal_rotate", {"pan_deg": 270}, ex)
        self.assertEqual(self.commands(), [])
        self.robot.update_handle.more().override_status.assert_called_with("failed")

    def test_rmf_gimbal_action_deferred_while_offline(self):
        self.robot.is_online = False
        ex = self.execution()
        self.call(self.adapter._on_rmf_action, "fleet1", "carter1", "gimbal_move", {"pan_deg": 5}, ex)
        self.assertEqual(self.commands(), [])
        self.robot.is_online = True
        self.call(self.adapter._dispatch_deferred_command, self.robot)
        cmd = self.commands()[-1]
        self.assertEqual((cmd["action"], cmd["pan_deg"], cmd["cmd_id"]), ("gimbal_move", 5.0, self.robot.cmd_id))

    def test_rmf_action_with_navigate_step_is_rejected(self):
        ex = self.execution()
        self.call(self.adapter._on_rmf_action, "fleet1", "carter1", "task",
                  json.dumps({"steps": [GIMBAL, NAV]}), ex)
        self.assertNotIn("task", self.actions())
        self.robot.update_handle.more().override_status.assert_called_with("failed")
        ex.finished.assert_not_called()

    def test_rmf_action_with_invalid_description_fails_cleanly(self):
        ex = self.execution()
        self.call(self.adapter._on_rmf_action, "fleet1", "carter1", "gimbal_move", "{not json", ex)
        self.assertEqual(self.commands(), [])
        self.robot.update_handle.more().override_status.assert_called_with("failed")

    def test_rmf_action_failure_is_escalated(self):
        ex = self.execution()
        self.call(self.adapter._on_rmf_action, "fleet1", "carter1", "task", {"steps": [GIMBAL]}, ex)
        cmd_id = self.commands()[-1]["cmd_id"]
        self.state(cmd_id, "aborted", 0, steps=1, message="gimbal timeout")
        ex.finished.assert_not_called()
        self.robot.update_handle.more().override_status.assert_called_with("failed")
        self.assertIn("aborted", self.statuses())

    def test_rmf_gimbal_action_failure_is_escalated_without_retry(self):
        ex = self.execution()
        self.call(self.adapter._on_rmf_action, "fleet1", "carter1", "gimbal_rotate", {"pan_deg": 10}, ex)
        cmd_id = self.commands()[-1]["cmd_id"]
        sent = len(self.commands())
        with patch.object(mfa.threading, "Timer") as timer:
            self.call(self.adapter._handle_command_result, "carter1", "fleet1",
                      {"cmd_id": cmd_id, "status": "failed", "message": "gimbal timeout"})
        timer.assert_not_called()
        self.assertEqual(len(self.commands()), sent)
        self.assertEqual(self.robot.nav_retries, 0)
        ex.finished.assert_not_called()
        self.assertEqual(self.robot.status, "failed")  # RMF escalation itself is skipped in mock mode
        self.assertIsNone(self.robot.last_cmd_payload)

    def test_rmf_navigate_failure_is_still_retried(self):
        dest = MagicMock()
        dest.position = [3.0, 0.0, 0.0]
        dest.map = self.robot.map_name
        dest.dock = None
        dest.speed_limit = None
        self.call(self.adapter._on_rmf_navigate, "fleet1", "carter1", dest, self.execution())
        cmd_id = self.commands()[-1]["cmd_id"]
        with patch.object(mfa.threading, "Timer") as timer:
            self.call(self.adapter._handle_command_result, "carter1", "fleet1", {"cmd_id": cmd_id, "status": "failed"})
        timer.assert_called_once()
        self.assertEqual(self.robot.nav_retries, 1)

    # ---- ownership / superseding ------------------------------------------
    def test_rmf_navigate_supersedes_direct_task_without_losing_execution(self):
        res = self.call(self.adapter.dispatch_multi_step_task, "fleet1", "carter1", [GIMBAL, GIMBAL], task_id="T2")
        dest = MagicMock()
        dest.position = [3.0, 0.0, 0.0]
        dest.map = self.robot.map_name
        dest.dock = None
        dest.speed_limit = None
        ex = self.execution()
        self.call(self.adapter._on_rmf_navigate, "fleet1", "carter1", dest, ex)
        self.assertIsNone(self.robot.active_task)

        # The superseded task's cancel report must not touch RMF's new execution.
        self.call(self.adapter._handle_command_result, "carter1", "fleet1", {"cmd_id": res["cmd_id"], "status": "canceled"})
        self.state(res["cmd_id"], "canceled", 0)
        self.assertIs(self.robot.current_execution, ex)
        last = self.adapter._publish_task_status.call_args_list[-1].args
        self.assertEqual((last[3], last[7]), ("canceled", "T2"))

    def test_direct_task_rejected_while_rmf_execution_active(self):
        self.robot.current_execution = self.execution()
        self.task_command(action="task", steps=[GIMBAL], task_id="T3")
        self.assertNotIn("task", self.actions())
        last = self.adapter._publish_task_status.call_args_list[-1].args
        self.assertEqual((last[3], last[7]), ("rejected", "T3"))

    def test_command_result_and_task_state_finish_once(self):
        ex = self.execution()
        self.call(self.adapter._on_rmf_action, "fleet1", "carter1", "task", {"steps": [GIMBAL]}, ex)
        cmd_id = self.commands()[-1]["cmd_id"]
        self.call(self.adapter._handle_command_result, "carter1", "fleet1", {"cmd_id": cmd_id, "status": "completed"})
        self.state(cmd_id, "succeeded", 0, steps=1)
        ex.finished.assert_called_once()
        self.assertEqual(self.statuses().count("succeeded"), 1)

    # ---- cancel ------------------------------------------------------------
    def test_backend_stop_waits_for_robot_confirmation(self):
        res = self.call(self.adapter.dispatch_multi_step_task, "fleet1", "carter1", [GIMBAL, GIMBAL], task_id="T4")
        self.task_command(action="stop", task_id="T4")
        self.assertEqual(self.actions()[-1], "stop")
        self.assertEqual(self.statuses()[-1], "canceling")
        self.state(res["cmd_id"], "canceled", 0)
        self.assertEqual(self.statuses()[-1], "canceled")

    def test_backend_stop_for_inactive_task_does_not_stop_robot(self):
        self.task_command(action="stop", task_id="UNKNOWN")
        self.assertNotIn("stop", self.actions())
        self.assertEqual(self.statuses()[-1], "canceled")

    # ---- e-stop ------------------------------------------------------------
    def test_estop_resumes_remaining_steps_of_rmf_task(self):
        ex = self.execution()
        steps = [GIMBAL, {"type": "gimbal_rotate", "pan_deg": 10.0, "tilt_deg": 0.0},
                 {"type": "gimbal_move", "pan_deg": 0.0, "tilt_deg": 0.0}]
        self.call(self.adapter._on_rmf_action, "fleet1", "carter1", "task", {"steps": steps}, ex)
        cmd_id = self.commands()[-1]["cmd_id"]
        self.robot.active_task["auto_proceed"] = False  # keep it at the gate for the test
        self.state(cmd_id, "waiting", 0, steps=3)

        self.call(self.adapter._handle_robot_control, "fleet1", "carter1", "estop")
        self.assertEqual(self.actions()[-1], "stop")
        self.state(cmd_id, "canceled", 0, steps=3)
        self.assertEqual(self.statuses()[-1], "held")
        self.assertIs(self.robot.current_execution, ex)
        ex.finished.assert_not_called()

        self.call(self.adapter._handle_robot_control, "fleet1", "carter1", "release")
        resumed = self.commands()[-1]
        self.assertEqual(resumed["action"], "task")
        self.assertEqual(resumed["steps"], steps[1:])
        self.assertNotIn("all_steps", resumed)
        self.assertEqual(self.robot.active_task["step_offset"], 1)
        self.assertIs(self.robot.active_task["execution"], ex)

        # Step numbers reported upward are absolute (offset + robot's relative step).
        self.state(resumed["cmd_id"], "running", 0, steps=2)
        last = self.adapter._publish_task_status.call_args_list[-1].args
        self.assertEqual((last[4], last[5]), (1, 3))

        self.state(resumed["cmd_id"], "succeeded", 1, steps=2)
        ex.finished.assert_called_once()


if __name__ == "__main__":
    unittest.main()
