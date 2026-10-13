#!/usr/bin/env python3
"""
Unit tests for dispatching a multi-step task (navigate / gimbal) to Open-RMF as a compose task.
The RMF publisher is mocked; no RMF core is needed.
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

for p in (os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")), "/root/rmf_ws/backend"):
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import state
import rmf_service as rs
from rmf_service import rmf_service

WAYPOINTS = [
    {"id": "a", "name": "wp_1", "x": 0.0, "y": 0.0},
    {"id": "b", "name": "wp_2", "x": 5.0, "y": 0.0},
]


class TestComposeTask(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        map_file = os.path.join(tmp.name, "active_nav_graph.json")
        with open(map_file, "w", encoding="utf-8") as f:
            json.dump({"waypoints": WAYPOINTS, "lanes": []}, f)
        for patcher in (
            patch.object(state, "ACTIVE_MAP_FILE", map_file),
            patch.object(state, "loaded_fleet_waypoints", return_value={"fleet1": {"wp_1", "wp_2"}}),
            patch.object(state, "is_robot_off_graph", return_value=(False, 0.0)),
            patch.object(state, "is_robot_estopped", return_value=False),
            patch.object(rs.rclpy, "ok", return_value=True),
            patch.object(rmf_service, "task_pub", MagicMock()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        state.paused_fleets.clear()
        state.current_tasks.clear()
        state.current_robots["carter1"] = {"id": "carter1", "fleet": "fleet1"}
        self.addCleanup(state.current_robots.pop, "carter1", None)

    def published(self):
        msg = rmf_service.task_pub.publish.call_args.args[0]
        return msg.request_id, json.loads(msg.json_msg)

    def activities(self, payload):
        return [p["activity"]["description"]["activities"][0] for p in payload["request"]["description"]["phases"]]

    def test_navigate_gimbal_navigate_becomes_compose_phases(self):
        steps = [
            {"type": "navigate", "place": "wp_2", "yaw": 1.57},
            {"type": "gimbal_move", "pan_deg": 30, "tilt_deg": -10},
            {"type": "navigate", "x": 0.1, "y": -0.2},
        ]
        res = rmf_service.dispatch_compose_task(steps, robot_id="carter1", task_id="C1")
        self.assertEqual(res["status"], "success", res)

        req_id, payload = self.published()
        self.assertEqual(req_id, "C1")
        self.assertEqual(payload["type"], "robot_task_request")
        self.assertEqual((payload["robot"], payload["fleet"]), ("carter1", "fleet1"))
        self.assertEqual(payload["request"]["category"], "compose")
        acts = self.activities(payload)
        self.assertEqual(acts[0], {"category": "go_to_place", "description": {"waypoint": "wp_2", "orientation": 1.57}})
        self.assertEqual(acts[1]["category"], "perform_action")
        self.assertEqual(acts[1]["description"]["category"], "gimbal_move")
        self.assertEqual(acts[1]["description"]["description"], {"pan_deg": 30.0, "tilt_deg": -10.0})
        # x/y close to a graph waypoint is mapped to it so RMF can plan the route.
        self.assertEqual(acts[2], {"category": "go_to_place", "description": {"waypoint": "wp_1"}})

        task = state.current_tasks[0]
        self.assertEqual((task["id"], task["type"], task["total_steps"]), ("C1", "compose", 3))

    def test_without_robot_rmf_bids(self):
        res = rmf_service.dispatch_compose_task([{"type": "gimbal_rotate", "pan_deg": 15}], robot_id="any")
        self.assertEqual(res["status"], "success", res)
        _, payload = self.published()
        self.assertEqual(payload["type"], "dispatch_task_request")

    def test_rejects_coordinates_off_the_graph(self):
        res = rmf_service.dispatch_compose_task([{"type": "navigate", "x": 2.5, "y": 0.0}], robot_id="carter1")
        self.assertEqual(res["status"], "error")
        self.assertIn("不在路網站點上", res["message"])
        rmf_service.task_pub.publish.assert_not_called()

    def test_rejects_unknown_place_and_type(self):
        for steps in ([{"type": "navigate", "place": "nowhere"}], [{"type": "dance"}], [], [{"type": "gimbal_move", "pan_deg": "x"}]):
            res = rmf_service.dispatch_compose_task(steps, robot_id="carter1")
            self.assertEqual(res["status"], "error", steps)
        rmf_service.task_pub.publish.assert_not_called()
        self.assertEqual(state.current_tasks, [])

    def test_rejects_paused_fleet(self):
        state.paused_fleets.add("fleet1")
        self.addCleanup(state.paused_fleets.clear)
        res = rmf_service.dispatch_compose_task([{"type": "navigate", "place": "wp_1"}], robot_id="carter1")
        self.assertEqual(res["status"], "error")
        self.assertIn("暫停", res["message"])

    def test_compose_task_initializes_current_step_and_coords(self):
        steps = [
            {"type": "navigate", "place": "wp_2"},
            {"type": "gimbal_move", "pan_deg": 30},
            {"type": "navigate", "place": "wp_1"},
        ]
        res = rmf_service.dispatch_compose_task(steps, robot_id="carter1", task_id="step_test_1")
        self.assertEqual(res["status"], "success")
        task = state.current_tasks[0]
        self.assertEqual(task["current_step"], 0)
        self.assertEqual(task["total_steps"], 3)
        self.assertIn("步驟 1/3", task["detail"])
        self.assertEqual(task["steps"][0]["x"], 5.0)
        self.assertEqual(task["steps"][2]["x"], 0.0)

    def test_proceed_task_advances_current_step(self):
        state.current_tasks.append({
            "id": "direct_1",
            "type": "multi_step",
            "robot_id": "carter1",
            "status": "waiting",
            "total_steps": 3,
            "current_step": 0,
            "detail": "步驟 1/3 完成，等待放行...",
        })
        with patch.object(rmf_service, "task_cmd_pub", MagicMock()):
            res = rmf_service.proceed_task("carter1")
        self.assertEqual(res["status"], "success")
        task = state.current_tasks[0]
        self.assertEqual(task["current_step"], 1)
        self.assertEqual(task["status"], "active")
        self.assertIn("步驟 2/3", task["detail"])


if __name__ == "__main__":
    unittest.main()

