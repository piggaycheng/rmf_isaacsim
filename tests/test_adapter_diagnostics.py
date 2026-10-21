#!/usr/bin/env python3
"""Fleet Adapter diagnostics: loaded-graph staleness, dispatch pre-check, waypoint fallback warnings."""

import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

for p in (
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../workspace/mqtt_fleet_adapter")),
    "/root/rmf_ws/src/custom_ws/mqtt_fleet_adapter",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")),
    "/root/rmf_ws/backend",
):
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import mqtt_fleet_adapter as mfa
import state
from rmf_service import AdapterReloadBlocked, RMFService, rmf_service

GRAPH_YAML = "levels:\n  L1:\n    vertices:\n    - [0, 0, {name: wp_1}]\n    - [1, 0, {name: wp_2}]\n"


class BackendFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        graphs = os.path.join(self.tmp.name, "nav_graphs")
        os.makedirs(graphs)
        self.graph_file = os.path.join(graphs, "0.yaml")
        with open(self.graph_file, "w") as f:
            f.write(GRAPH_YAML)
        adapters_file = os.path.join(self.tmp.name, "fleet_adapters.json")
        with open(adapters_file, "w") as f:
            json.dump([{"id": "a1", "fleet_name": "fleet1", "graph_idx": 0}], f)
        map_file = os.path.join(self.tmp.name, "active_nav_graph.json")
        with open(map_file, "w") as f:
            json.dump({"waypoints": [{"id": "wp_1", "name": "wp_1"}, {"id": "wp_2", "name": "wp_2"},
                                     {"id": "wp_3", "name": "wp_3"}]}, f)
        for attr, value in (("NAV_GRAPHS_DIR", graphs), ("FLEET_ADAPTERS_FILE", adapters_file),
                            ("OFFLINE_RELOAD_FILE", os.path.join(self.tmp.name, "offline_reload_pending.json")),
                            ("ACTIVE_MAP_FILE", map_file)):
            patcher = patch.object(state, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(state.update_fa_state, {})
        state.update_fa_state({})
        state.paused_fleets.clear()
        state.estopped_robots.clear()
        tasks_patch = patch.object(state, "current_tasks", [])
        robots_patch = patch.object(state, "current_robots", {})
        tasks_patch.start()
        robots_patch.start()
        self.addCleanup(tasks_patch.stop)
        self.addCleanup(robots_patch.stop)

    def report_loaded(self, waypoints=("wp_1", "wp_2"), graph_idx=0, sha=None, warnings=()):
        state.update_fa_state({
            "fleets": {"fleet1": {
                "graph_idx": graph_idx,
                "graph_sha": sha or mfa.nav_graph_fingerprint(self.graph_file),
                "waypoints": list(waypoints),
            }},
            "waypoint_warnings": [{"key": k, "message": m} for k, m in warnings],
        })

    def diag(self):
        return state.adapter_diagnostics(state.load_fleet_adapters_data()[0])


class TestAdapterDiagnostics(BackendFixture):
    def test_unknown_without_fa_state(self):
        self.assertEqual(self.diag()["status"], "unknown")
        state.update_fa_state({"mqtt_connected": True})  # old FA without graph report
        self.assertEqual(self.diag()["status"], "unknown")

    def test_ok_when_loaded_graph_matches_file(self):
        self.report_loaded()
        d = self.diag()
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["loaded_waypoints"], 2)

    def test_stale_when_graph_file_changed_after_load(self):
        self.report_loaded()
        with open(self.graph_file, "a") as f:
            f.write("    - [2, 0, {name: wp_3}]\n")
        d = self.diag()
        self.assertEqual(d["status"], "stale")
        self.assertIn("舊版路網", d["issues"][0])

    def test_stale_when_graph_idx_differs_or_fleet_missing(self):
        self.report_loaded(graph_idx=1)
        self.assertEqual(self.diag()["status"], "stale")
        state.update_fa_state({"fleets": {}})
        self.assertEqual(self.diag()["status"], "stale")

    def test_waypoint_warnings_filtered_by_fleet(self):
        self.report_loaded(warnings=[("fleet1_carter1", "w1"), ("fleet10_x", "other")])
        self.assertEqual(self.diag()["warnings"], ["w1"])

    def test_expired_report_is_unknown(self):
        self.report_loaded()
        with patch.object(state, "fa_state_time", 1):
            self.assertEqual(self.diag()["status"], "unknown")
            self.assertIsNone(state.loaded_fleet_waypoints())

    def test_missing_graph_file_or_fingerprint_is_unknown(self):
        self.report_loaded()
        os.remove(self.graph_file)
        self.assertEqual(self.diag()["status"], "unknown")
        with open(self.graph_file, "w") as f:
            f.write(GRAPH_YAML)
        state.fa_state["fleets"]["fleet1"]["graph_sha"] = None
        self.assertEqual(self.diag()["status"], "unknown")


class TestRobotSpecsSync(BackendFixture):
    def setUp(self):
        super().setUp()
        self.adapter = {
            "id": "a1", "fleet_name": "fleet1",
            "linear_velocity": 1.2, "angular_velocity": 1.0, "robot_radius": 0.35,
            "robots": [{"name": "carter1"}],
        }
        self.carter = {
            "fleet_name": "fleet1", "robot_id": "carter1", "status": "offline",
            "specs": {"max_linear_velocity": 0.75, "max_angular_velocity": 0.5,
                      "footprint_radius": 0.65},
        }
        state.save_fleet_adapters_data([self.adapter])

    def test_unadopted_cached_test_robot_cannot_overwrite_fleet_limits(self):
        test_robot = {
            "fleet_name": "fleet1", "robot_id": "e2e_bot",
            "specs": {"max_linear_velocity": 1.2, "max_angular_velocity": 1.0,
                      "footprint_radius": 0.35},
        }
        for records in ([self.carter, test_robot], [test_robot, self.carter]):
            with self.subTest(order=[r["robot_id"] for r in records]):
                state.save_fleet_adapters_data([self.adapter])
                state.sync_robot_specs_to_adapters({r["robot_id"]: r for r in records})
                saved = state.load_fleet_adapters_data()[0]
                self.assertEqual((saved["linear_velocity"], saved["angular_velocity"]), (0.75, 0.5))
                self.assertEqual(saved["robot_radius"], 0.65)

    def test_multiple_adopted_robots_use_slowest_speed_and_largest_radius(self):
        self.adapter["robots"].append({"name": "carter2"})
        state.save_fleet_adapters_data([self.adapter])
        other = {
            "fleet_name": "fleet1", "robot_id": "carter2",
            "specs": {"max_linear_velocity": 0.6, "max_angular_velocity": 0.8,
                      "footprint_radius": 0.8},
        }
        state.sync_robot_specs_to_adapters({"carter1": self.carter, "carter2": other})
        saved = state.load_fleet_adapters_data()[0]
        self.assertEqual((saved["linear_velocity"], saved["angular_velocity"]), (0.6, 0.5))
        self.assertEqual(saved["robot_radius"], 0.8)

    def test_adoption_changes_apply_even_when_reported_specs_are_unchanged(self):
        state.save_fleet_adapters_data([dict(self.adapter, robots=[])])
        state.sync_robot_specs_to_adapters({"carter1": self.carter})
        self.assertEqual(state.load_fleet_adapters_data()[0]["linear_velocity"], 1.2)
        state.save_fleet_adapters_data([self.adapter])
        state.sync_robot_specs_to_adapters({"carter1": self.carter})
        self.assertEqual(state.load_fleet_adapters_data()[0]["linear_velocity"], 0.75)
        with patch.object(state, "save_fleet_adapters_data") as save:
            state.sync_robot_specs_to_adapters({"carter1": self.carter})
        save.assert_not_called()

    def test_other_fleets_and_missing_specs_do_not_change_limits(self):
        other = dict(self.carter, fleet_name="fleet2")
        state.sync_robot_specs_to_adapters({"other": other, "carter": dict(self.carter, specs={})})
        self.assertEqual(state.load_fleet_adapters_data()[0], self.adapter)

    def test_lower_configured_speeds_are_preserved(self):
        self.adapter.update(linear_velocity=0.4, angular_velocity=0.3)
        state.save_fleet_adapters_data([self.adapter])
        state.sync_robot_specs_to_adapters({"carter": self.carter})
        saved = state.load_fleet_adapters_data()[0]
        self.assertEqual((saved["linear_velocity"], saved["angular_velocity"]), (0.4, 0.3))

    def test_invalid_reported_limits_are_logged_without_persisting(self):
        for raw in (0, -1, float("nan"), float("inf"), "invalid"):
            with self.subTest(raw=raw):
                robot = dict(self.carter, specs={"max_linear_velocity": raw})
                with patch("builtins.print") as log, patch.object(state, "save_fleet_adapters_data") as save:
                    state.sync_robot_specs_to_adapters({"carter": robot})
                log.assert_called_once()
                save.assert_not_called()


class TestDispatchPrecheck(BackendFixture):
    def test_rejects_place_missing_from_loaded_graph(self):
        self.report_loaded()
        res = rmf_service.dispatch_task("goto", "wp_3")
        self.assertEqual(res["status"], "error")
        self.assertIn("'wp_3'", res["message"])
        self.assertIn("已載入路網", res["message"])
        self.assertNotIn("重新載入", res["message"])

    def test_hints_restart_when_stale(self):
        self.report_loaded(sha="outdated")
        res = rmf_service.dispatch_task("goto", "wp_3", robot_id="carter1", fleet_name="fleet1")
        self.assertEqual(res["status"], "error")
        self.assertIn("重新載入", res["message"])

    def test_loaded_place_passes_precheck(self):
        self.report_loaded()
        with patch.object(rmf_service, "task_pub", None):
            res = rmf_service.dispatch_task("goto", "wp_2")
        # Passes the graph check and stops at the (absent) publisher instead.
        self.assertIn("Publisher", res["message"])

    def test_no_precheck_without_report(self):
        with patch.object(rmf_service, "task_pub", None):
            res = rmf_service.dispatch_task("goto", "wp_3")
        self.assertIn("Publisher", res["message"])

    def test_directed_fleet_does_not_use_other_fleet_graph(self):
        self.report_loaded()
        msg = rmf_service._unknown_places_for_adapter(["wp_1"], "fleet2")
        self.assertIn("尚未被", msg)

    def test_all_places_must_be_in_same_fleet(self):
        self.report_loaded(waypoints=["wp_1"])
        state.fa_state["fleets"]["fleet2"] = {"waypoints": ["wp_2"]}
        msg = rmf_service._unknown_places_for_adapter(["wp_1", "wp_2"], None)
        self.assertIn("同一個", msg)
        state.fa_state["fleets"]["fleet2"]["waypoints"].append("wp_1")
        self.assertIsNone(rmf_service._unknown_places_for_adapter(["wp_1", "wp_2"], None))

    def test_empty_live_fleets_rejects_task(self):
        state.update_fa_state({"fleets": {}})
        self.assertIsNotNone(rmf_service._unknown_places_for_adapter(["wp_1"], None))


class TestAdapterFallbackWarnings(unittest.TestCase):
    def setUp(self):
        with patch.object(mfa.MqttFleetAdapter, "_init_rmf"):
            cfg = os.path.join(os.path.dirname(mfa.__file__), "config.yaml")
            self.fa = mfa.MqttFleetAdapter(cfg, cfg, mock_rmf=True)
        self.fa.fa_state_pub = MagicMock()
        self.fa.robot_sessions = {}
        self.fa.mock_rmf = False
        self.fa.fleet_handles = {"fleet1": MagicMock()}
        self.fa.fleet_waypoint_info = {
            "fleet1": {"all": ["wp_1", "wp_2"], "chargers": [], "parkings": [], "graph_idx": 0, "graph_sha": "abc"}
        }
        patcher = patch.object(mfa, "find_backend_path", return_value="/nonexistent/fleet_adapters.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def published(self):
        return json.loads(self.fa.fa_state_pub.publish.call_args[0][0].data)

    def test_fallback_is_reported_and_cleared(self):
        rejected = []
        charger = self.fa._resolve_charger_waypoint("carter1", "fleet1", {"default_charger": "charger_1"}, rejected)
        self.assertEqual((charger, rejected), ("wp_1", ["charger_1"]))
        self.fa._record_waypoint_fallbacks("carter1", "fleet1", rejected, charger, [], "wp_2")
        st = self.published()
        self.assertEqual(st["fleets"]["fleet1"], {"graph_idx": 0, "graph_sha": "abc", "waypoints": ["wp_1", "wp_2"]})
        self.assertEqual(len(st["waypoint_warnings"]), 1)
        self.assertEqual(st["waypoint_warnings"][0]["key"], "fleet1_carter1")
        self.assertIn("charger_1", st["waypoint_warnings"][0]["message"])

        self.fa._record_waypoint_fallbacks("carter1", "fleet1", [], "wp_2", [], "wp_2")
        self.assertEqual(self.published()["waypoint_warnings"], [])

    def test_valid_charger_not_rejected(self):
        rejected = []
        self.assertEqual(
            self.fa._resolve_charger_waypoint("carter1", "fleet1", {"default_charger": "wp_2"}, rejected), "wp_2"
        )
        self.assertEqual(rejected, [])

    def test_failed_fleet_registration_not_reported_as_loaded(self):
        self.fa.fleet_handles = {}
        self.fa._publish_fa_state()
        self.assertEqual(self.published()["fleets"], {})

    def test_stop_confirmation_requires_real_heartbeat_and_no_execution(self):
        robot = mfa.ManagedRobot("carter1", "L1", [0, 0, 0], 1.0, fleet_name="fleet1")
        self.fa.robots[("fleet1", "carter1")] = robot
        self.fa._publish_fa_state()
        self.assertEqual(self.published()["robots"]["fleet1/carter1"]["last_heartbeat"], 0)
        robot.heartbeat_received = True
        robot.current_execution = object()
        self.fa._publish_fa_state()
        reported = self.published()["robots"]["fleet1/carter1"]
        self.assertEqual(reported["last_heartbeat"], robot.last_heartbeat)
        self.assertTrue(reported["busy"])

    def test_reregister_does_not_reuse_previous_heartbeat_as_stop_confirmation(self):
        self.fa.mock_rmf = True
        self.fa.configured_fleets = {"fleet1"}
        robot = mfa.ManagedRobot("carter1", "L1", [0, 0, 0], 1.0, fleet_name="fleet1")
        robot.heartbeat_received = True
        self.fa.robots[("fleet1", "carter1")] = robot
        with patch.object(self.fa, "_save_discovered_robots"), \
                patch.object(self.fa, "_send_register_ack"), \
                patch.object(self.fa, "_sync_rmf_availability"):
            self.fa._handle_register("carter1", "fleet1", {"robot_id": "carter1"})
        self.assertFalse(robot.heartbeat_received)
        self.fa._handle_heartbeat("carter1", "fleet1", {"status": "idle"})
        self.assertTrue(robot.heartbeat_received)


class TestSafeReload(BackendFixture):
    def setUp(self):
        super().setUp()
        self.service = RMFService()
        self.now = time.time()
        self.robot = {
            "online": True, "status": "idle", "busy": False,
            "last_heartbeat": self.now,
        }
        state.update_fa_state({
            "mqtt_connected": True, "started_at": self.now - 20,
            "robots": {"fleet1/carter1": self.robot},
        })

    def task(self, **overrides):
        result = {
            "id": "old", "status": "queued", "fleet_name": "fleet1", "robot_id": "carter1",
            "submitted_at": self.now - 10,
        }
        result.update(overrides)
        state.current_tasks.append(result)
        return result

    def test_all_nonterminal_tasks_in_other_fleets_block_before_writing(self):
        for status in ("queued", "active", "waiting", "canceling"):
            with self.subTest(status=status):
                state.current_tasks.clear()
                self.task(status=status, fleet_name="fleet2")
                with patch.object(self.service, "_restart_fleet_adapter_process") as restart:
                    persist = MagicMock()
                    with self.assertRaisesRegex(AdapterReloadBlocked, "fleet2"):
                        self.service.restart_fleet_adapter(persist)
                persist.assert_not_called()
                restart.assert_not_called()
                self.assertFalse(self.service.adapter_reloading)

    def test_canceled_task_still_requires_robot_stop_confirmation(self):
        self.task(status="canceled")
        for change in (
            {"busy": True}, {"status": "moving"}, {"online": False},
            {"last_heartbeat": self.now - 10},
        ):
            with self.subTest(change=change):
                original = dict(self.robot)
                self.robot.update(change)
                with self.assertRaises(AdapterReloadBlocked):
                    self.service.restart_fleet_adapter()
                self.robot.update(original)

    def test_external_task_and_stale_adapter_block_reload(self):
        state.current_robots["carter2"] = {"task_id": "external"}
        with self.assertRaises(AdapterReloadBlocked):
            self.service.restart_fleet_adapter()
        state.current_robots.clear()
        with patch.object(state, "fa_state_time", self.now - 10):
            with self.assertRaises(AdapterReloadBlocked):
                self.service.restart_fleet_adapter()

    def test_configured_robot_missing_from_report_blocks_reload(self):
        state.save_fleet_adapters_data([{
            "id": "a1", "fleet_name": "fleet2", "robots": [{"name": "carter2"}],
        }])
        state.current_robots["carter2"] = {"id": "carter2", "fleet": "fleet2"}
        with self.assertRaisesRegex(AdapterReloadBlocked, "部分車輛"):
            self.service.restart_fleet_adapter()

    def test_explicit_offline_reload_allows_disconnected_and_unregistered_robots(self):
        state.save_fleet_adapters_data([{
            "id": "a1", "fleet_name": "fleet1", "graph_idx": 0,
            "robots": [{"name": "carter1"}, {"name": "carter2"}],
        }])
        self.robot.update(online=False, status="offline", last_heartbeat=self.now - 60)
        persist = MagicMock()
        with self.assertRaisesRegex(AdapterReloadBlocked, "離線套用"):
            self.service.restart_fleet_adapter(persist)
        persist.assert_not_called()
        with patch.object(self.service, "_restart_fleet_adapter_process", return_value=True) as restart:
            self.assertTrue(self.service.restart_fleet_adapter(persist, allow_offline=True))
        persist.assert_called_once()
        restart.assert_called_once_with(offline_robots={"fleet1/carter1", "fleet1/carter2"})
        self.assertEqual(self.service.adapter_reload_waiting_robots, {"fleet1/carter1", "fleet1/carter2"})
        restored = RMFService()
        self.assertEqual(restored.adapter_reload_waiting_robots, self.service.adapter_reload_waiting_robots)
        self.assertIn("重新連線", restored.dispatch_task("goto", "wp_1")["message"])
        self.assertFalse(self.robot["online"])

    def test_offline_reload_does_not_bypass_task_or_online_stop_guards(self):
        for blocker in ("local_task", "external_task", "moving", "busy", "stale_heartbeat", "stale_adapter"):
            with self.subTest(blocker=blocker):
                state.current_tasks.clear()
                state.current_robots.clear()
                self.robot.update(online=True, status="idle", busy=False, last_heartbeat=self.now)
                state.fa_state_time = time.time()
                if blocker == "local_task":
                    self.task()
                    self.robot.update(online=False, status="offline")
                elif blocker == "external_task":
                    self.robot.update(online=False, status="offline")
                    state.current_robots["carter1"] = {"task_id": "external"}
                elif blocker == "moving":
                    self.robot["status"] = "moving"
                elif blocker == "busy":
                    self.robot["busy"] = True
                elif blocker == "stale_heartbeat":
                    self.robot["last_heartbeat"] = self.now - 60
                else:
                    state.fa_state_time = self.now - 60
                persist = MagicMock()
                with patch.object(self.service, "_restart_fleet_adapter_process") as restart:
                    with self.assertRaises(AdapterReloadBlocked):
                        self.service.restart_fleet_adapter(persist, allow_offline=True)
                persist.assert_not_called()
                restart.assert_not_called()

    def test_failed_normal_reload_preserves_persisted_reconnect_fence(self):
        self.robot.update(online=False, status="offline")
        with patch.object(self.service, "_restart_fleet_adapter_process", return_value=True):
            self.service.restart_fleet_adapter(allow_offline=True)
        state.fa_state["robots"] = {}
        with patch.object(self.service, "_restart_fleet_adapter_process", return_value=False):
            self.assertFalse(self.service.restart_fleet_adapter())
        self.assertTrue(self.service.adapter_reload_failed)
        restored = RMFService()
        self.assertEqual(restored.adapter_reload_waiting_robots, {"fleet1/carter1"})
        self.assertIn("重新連線", restored.dispatch_task("goto", "wp_1")["message"])

    def test_offline_reload_blocks_all_submissions_until_new_adapter_idle_heartbeat(self):
        self.robot.update(online=False, status="offline")
        with patch.object(self.service, "_restart_fleet_adapter_process", return_value=True):
            self.service.restart_fleet_adapter(allow_offline=True)
        self.robot.update(online=True, status="idle")
        # The previous adapter's idle report must not release the reconnect fence.
        for change in (
            {},
            {"started_at": time.time(), "last_heartbeat": self.now - 30},
            {"last_heartbeat": time.time(), "status": "moving"},
            {"status": "idle", "busy": True},
            {"busy": False, "mqtt_connected": False},
            {"mqtt_connected": True, "stale_report": True},
        ):
            with self.subTest(change=change):
                change = dict(change)
                for key in ("started_at", "mqtt_connected"):
                    if key in change:
                        state.fa_state[key] = change.pop(key)
                state.fa_state_time = self.now - 60 if change.pop("stale_report", False) else time.time()
                self.robot.update(change)
                results = [
                    self.service.dispatch_task("goto", "wp_1"),
                    self.service.dispatch_compose_task([]),
                    self.service.dispatch_multi_step_task("carter1", []),
                    self.service.proceed_task("carter1"),
                ]
                self.assertTrue(all(r["status"] == "error" and "重新連線" in r["message"] for r in results))
                self.assertEqual(state.current_tasks, [])
        state.fa_state_time = time.time()
        self.robot.update(online=True, status="idle", busy=False, last_heartbeat=time.time())
        with patch("rmf_service.append_adapter_log") as log:
            result = self.service.dispatch_task("goto", "wp_1")
        self.assertIn("Publisher", result["message"])
        self.assertEqual(self.service.adapter_reload_waiting_robots, set())
        self.assertEqual(RMFService().adapter_reload_waiting_robots, set())
        log.assert_called_once()

    def test_submission_and_concurrent_reload_rejected_during_persist_and_restart(self):
        def assert_blocked():
            results = [
                self.service.dispatch_task("goto", "wp_1"),
                self.service.dispatch_compose_task([]),
                self.service.dispatch_multi_step_task("carter1", []),
                self.service.proceed_task("carter1"),
            ]
            self.assertTrue(all(r["status"] == "error" and "重新載入" in r["message"] for r in results))
            with self.assertRaises(AdapterReloadBlocked):
                self.service.restart_fleet_adapter()
            self.assertEqual(state.current_tasks, [])
            return True

        with patch.object(self.service, "_restart_fleet_adapter_process", side_effect=assert_blocked):
            self.assertTrue(self.service.restart_fleet_adapter(assert_blocked))
        self.assertFalse(self.service.adapter_reloading)

    def test_failure_and_persistence_exception_release_reservation(self):
        with patch.object(self.service, "_restart_fleet_adapter_process", return_value=False):
            self.assertFalse(self.service.restart_fleet_adapter())
        self.assertFalse(self.service.adapter_reloading)
        self.assertEqual(self.service.dispatch_task("goto", "wp_1")["status"], "error")
        with patch.object(self.service, "_restart_fleet_adapter_process", return_value=True):
            self.assertTrue(self.service.restart_fleet_adapter())
        self.assertFalse(self.service.adapter_reload_failed)
        with self.assertRaisesRegex(OSError, "disk"):
            self.service.restart_fleet_adapter(MagicMock(side_effect=OSError("disk")))
        self.assertFalse(self.service.adapter_reloading)
        self.assertFalse(self.service.adapter_reload_failed)

    def test_owned_process_restart_waits_for_fresh_loaded_graph(self):
        state.save_fleet_adapters_data([{
            "id": "a1", "fleet_name": "fleet1", "graph_idx": 0, "robots": [{"name": "carter1"}],
        }])
        self.report_loaded()
        state.fa_state.update(mqtt_connected=True, started_at=self.now - 20)
        old_process = MagicMock()
        old_process.poll.return_value = None
        new_process = MagicMock()
        new_process.poll.return_value = None
        self.service.adapter_process = old_process

        reports = []

        def ready(_):
            self.report_loaded()
            state.fa_state.update(mqtt_connected=True, started_at=time.time())
            reports.append(True)
            if len(reports) >= 2:
                state.fa_state["robots"] = {"fleet1/carter1": dict(
                    self.robot, last_heartbeat=time.time() if len(reports) >= 3 else 0,
                )}

        with patch("rmf_service.os.path.exists", return_value=True), \
                patch("rmf_service.subprocess.Popen", return_value=new_process), \
                patch("rmf_service.open", unittest.mock.mock_open()), \
                patch("rmf_service.time.sleep", side_effect=ready):
            self.assertTrue(self.service._restart_fleet_adapter_process())
        old_process.terminate.assert_called_once()
        old_process.wait.assert_called_once_with(timeout=5)
        self.assertEqual(len(reports), 3)

    def test_process_running_without_new_graph_ack_is_not_success(self):
        process = MagicMock()
        process.poll.return_value = None
        self.service.adapter_process = process
        with patch("rmf_service.os.path.exists", return_value=True), \
                patch("rmf_service.subprocess.Popen", return_value=process), \
                patch("rmf_service.open", unittest.mock.mock_open()), \
                patch("rmf_service.time.monotonic", side_effect=[0, 0, 16]), \
                patch("rmf_service.time.sleep"):
            self.assertFalse(self.service._restart_fleet_adapter_process())

    def test_offline_process_restart_requires_graph_ack_but_not_offline_heartbeat(self):
        state.save_fleet_adapters_data([{
            "id": "a1", "fleet_name": "fleet1", "graph_idx": 0, "robots": [{"name": "carter1"}],
        }])
        process = MagicMock()
        process.poll.return_value = None
        self.service.adapter_process = process

        def ready(_):
            self.report_loaded()
            state.fa_state.update(mqtt_connected=True, started_at=time.time(), robots={})

        with patch("rmf_service.os.path.exists", return_value=True), \
                patch("rmf_service.subprocess.Popen", return_value=process), \
                patch("rmf_service.open", unittest.mock.mock_open()), \
                patch("rmf_service.time.monotonic", side_effect=[0, 0, 1]), \
                patch("rmf_service.time.sleep", side_effect=ready):
            self.assertTrue(self.service._restart_fleet_adapter_process(offline_robots={"fleet1/carter1"}))

        for report in (
            {"mqtt_connected": True, "started_at": self.now - 60, "robots": {}},
            {"mqtt_connected": True, "started_at": time.time(), "robots": {
                "fleet1/carter1": dict(self.robot, status="moving", busy=True),
            }},
            {"mqtt_connected": False, "started_at": time.time(), "robots": {}},
        ):
            with self.subTest(report=report):
                self.report_loaded()
                state.fa_state.update(report)
                with patch("rmf_service.os.path.exists", return_value=True), \
                        patch("rmf_service.subprocess.Popen", return_value=process), \
                        patch("rmf_service.open", unittest.mock.mock_open()), \
                        patch("rmf_service.time.time", return_value=self.now), \
                        patch("rmf_service.time.monotonic", side_effect=[0, 0, 16]), \
                        patch("rmf_service.time.sleep"):
                    self.assertFalse(self.service._restart_fleet_adapter_process(offline_robots={"fleet1/carter1"}))

    def test_old_tasks_interrupted_only_after_post_restart_idle_heartbeat(self):
        old = self.task(submitted_at=self.now - 30, progress=90, status="active")
        new = self.task(id="new")
        terminal = self.task(id="done", status="completed", submitted_at=self.now - 30)
        for change in (
            {"busy": True}, {"status": "moving"}, {"online": False},
            {"last_heartbeat": self.now - 10}, {"last_heartbeat": self.now - 25},
        ):
            with self.subTest(change=change):
                original = dict(self.robot)
                self.robot.update(change)
                self.service.reconcile_interrupted_tasks()
                self.assertEqual(old["status"], "active")
                self.robot.update(original)
        with patch.object(state, "fa_state_time", self.now - 10):
            self.service.reconcile_interrupted_tasks()
        self.assertEqual(old["status"], "active")
        with patch("rmf_service.append_adapter_log") as log:
            self.service.reconcile_interrupted_tasks()
            self.service.reconcile_interrupted_tasks()
        self.assertEqual(old["status"], "failed")
        self.assertEqual(old["interruption_reason"], "adapter_reloaded")
        self.assertEqual(old["progress"], 90)
        self.assertIn("重新派遣", old["detail"])
        self.assertEqual(new["status"], "queued")
        self.assertEqual(terminal["status"], "completed")
        log.assert_called_once()

    def test_legacy_generated_ids_can_be_reconciled_but_unknown_times_are_not_guessed(self):
        old = self.task(id=f"task_{int((self.now - 30) * 1000)}_1_abcd")
        old.pop("submitted_at")
        unknown = self.task(id="custom")
        unknown.pop("submitted_at")
        self.service.reconcile_interrupted_tasks()
        self.assertEqual(old["status"], "failed")
        self.assertEqual(unknown["status"], "queued")

    def test_cancel_of_interrupted_task_reports_pending_stop_instead_of_calling_rmf(self):
        old = self.task(submitted_at=self.now - 30)
        self.robot.update(status="moving", busy=True)
        with patch.object(self.service, "_call_task_api") as api:
            result = self.service.cancel_task(old["id"])
        api.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.assertIn("尚未確認車輛停止", result["message"])
        self.assertEqual(old["status"], "queued")
        with patch.object(state, "fa_state_time", self.now - 10):
            self.assertTrue(self.service._task_predates_adapter(old))
        self.robot.update(status="idle", busy=False)
        self.service.reconcile_interrupted_tasks()
        self.assertEqual(old["status"], "failed")
        self.assertNotIn("interruption_pending", old)

    def test_interrupted_tasks_cannot_be_completed_by_late_ros_messages(self):
        old = self.task(submitted_at=self.now - 30, progress=90, status="active")
        self.service.reconcile_interrupted_tasks()
        node = MagicMock()
        with patch("rmf_service.rclpy.ok", return_value=True), \
                patch("rmf_service.Node", return_value=node), \
                patch("rmf_service.threading.Thread"):
            self.service.init_ros2_node()
        callbacks = {c.args[1]: c.args[2] for c in node.create_subscription.call_args_list}
        api_response = MagicMock(request_id="old", json_msg=json.dumps({
            "success": True, "state": {"booking": {"id": "old"}, "status": "completed"},
        }))
        callbacks["/task_api_responses"](api_response)
        summary = MagicMock(task_id="old", state=2)
        summary.task_profile.task_id = "old"
        callbacks["/task_summaries"](summary)
        callbacks["/rmf/task_status"](mfa.String(data=json.dumps({
            "task_id": "old", "robot_id": "carter1", "fleet_name": "fleet1", "status": "succeeded",
        })))
        self.assertEqual(old["status"], "failed")
        self.assertEqual(old["progress"], 90)
        self.assertIn("重載而中斷", old["detail"])

    def test_old_active_task_not_inferred_completed_while_stop_unconfirmed(self):
        old = self.task(submitted_at=self.now - 30, progress=90, status="active")
        self.robot.update(status="moving", busy=True)
        state.current_robots["carter1"] = {"task_id": "old"}
        robot = MagicMock(name="carter1", task_id="", battery_percent=100)
        robot.name = "carter1"
        robot.mode.mode = 0
        robot.location.x = robot.location.y = robot.location.yaw = 0.0
        robot.location.level_name = "L1"
        robot.path = []
        with patch.object(state, "is_robot_offline", return_value=False), \
                patch.object(state, "is_robot_off_graph", return_value=(False, 0.0)):
            self.service.fleet_callback(SimpleNamespace(name="fleet1", robots=[robot]))
        self.assertEqual(old["status"], "active")
        self.assertEqual(old["progress"], 90)


class TestReloadErrors(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tasks_patch = patch.object(state, "current_tasks", [])
        self.robots_patch = patch.object(state, "current_robots", {})
        self.tasks_patch.start()
        self.robots_patch.start()
        self.addCleanup(self.tasks_patch.stop)
        self.addCleanup(self.robots_patch.stop)

    async def test_restart_endpoint_does_not_claim_success_on_failure(self):
        from controller import adapter_controller

        adapter = {"id": "a1", "fleet_name": "fleet1", "status": "offline"}
        with patch.object(adapter_controller, "load_fleet_adapters_data", return_value=[adapter]), \
                patch.object(adapter_controller, "append_adapter_log"), \
                patch.object(adapter_controller, "save_fleet_adapters_data") as save, \
                patch.object(rmf_service, "restart_fleet_adapter", return_value=False):
            result = await adapter_controller.fleet_adapter_action("a1", {"action": "restart"})
        self.assertEqual(result["status"], "error")
        self.assertEqual(adapter["status"], "offline")
        save.assert_not_called()

    async def test_map_save_reports_reload_failure_without_losing_saved_map(self):
        from controller import map_controller

        with tempfile.TemporaryDirectory() as directory:
            graph_dir = os.path.join(directory, "nav_graphs")
            os.makedirs(graph_dir)
            map_file = os.path.join(directory, "map.json")
            req = state.MapSaveRequest(
                waypoints=[{"id": "wp_1", "name": "wp_1", "x": 0, "y": 0}],
                lanes=[], restart_adapter=True,
            )
            with patch.object(map_controller, "ACTIVE_MAP_FILE", map_file), \
                    patch.object(map_controller, "ACTIVE_YAML_FILE", os.path.join(directory, "map.yaml")), \
                    patch.object(map_controller, "NAV_GRAPHS_DIR", graph_dir), \
                    patch.object(map_controller, "broadcast", new_callable=AsyncMock), \
                    patch.object(state, "load_fleet_adapters_data", return_value=[]), \
                    patch.object(rmf_service, "restart_fleet_adapter", side_effect=lambda persist: (persist(), False)[1]):
                result = await map_controller.save_map(req)
            self.assertIn("重新載入失敗", result["adapter_restart_error"])
            self.assertEqual(result["status"], "error")
            with open(map_file) as f:
                self.assertEqual(json.load(f)["waypoints"][0]["name"], "wp_1")

    async def test_map_offline_apply_forwards_explicit_opt_in_and_reports_reconnect_requirement(self):
        from controller import map_controller

        request = state.MapSaveRequest(
            waypoints=[{"id": "wp_1", "name": "wp_1", "x": 0, "y": 0}],
            lanes=[], levels=[{"name": "L1"}], lifts=[],
            restart_adapter=True, allow_offline_reload=True,
        )
        with patch.object(map_controller, "reload_fleet_adapter", new_callable=AsyncMock) as reload_adapter, \
                patch.object(map_controller, "broadcast", new_callable=AsyncMock), \
                patch.object(state, "load_fleet_adapters_data", return_value=[]):
            result = await map_controller.save_map(request)
        self.assertEqual(result["status"], "success")
        self.assertIn("重新連線", result["message"])
        self.assertIn("不會自動續跑", result["message"])
        self.assertTrue(reload_adapter.await_args.kwargs["allow_offline"])
        request.restart_adapter = False
        with self.assertRaises(HTTPException) as error:
            await map_controller.save_map(request)
        self.assertEqual(error.exception.status_code, 400)

    async def test_reload_helper_forwards_offline_opt_in(self):
        from controller import adapter_controller

        persist = MagicMock()
        with patch.object(rmf_service, "restart_fleet_adapter", return_value=True) as restart:
            await adapter_controller.reload_fleet_adapter(persist, allow_offline=True)
        restart.assert_called_once_with(persist, allow_offline=True)

    async def test_busy_map_apply_does_not_overwrite_json_or_yaml(self):
        from controller import map_controller

        state.current_tasks.append({"id": "running", "fleet_name": "fleet2", "status": "waiting"})
        with tempfile.TemporaryDirectory() as directory:
            map_file = os.path.join(directory, "map.json")
            yaml_file = os.path.join(directory, "map.yaml")
            graph_dir = os.path.join(directory, "nav_graphs")
            os.makedirs(graph_dir)
            with open(map_file, "w") as f:
                f.write('{"name":"previous"}')
            with open(yaml_file, "w") as f:
                f.write("previous")
            request = state.MapSaveRequest(
                waypoints=[{"id": "wp_1", "name": "wp_1", "x": 0, "y": 0}], lanes=[], restart_adapter=True,
            )
            with patch.object(map_controller, "ACTIVE_MAP_FILE", map_file), \
                    patch.object(map_controller, "ACTIVE_YAML_FILE", yaml_file), \
                    patch.object(map_controller, "NAV_GRAPHS_DIR", graph_dir), \
                    patch.object(rmf_service, "_restart_fleet_adapter_process") as restart:
                with self.assertRaises(HTTPException) as error:
                    await map_controller.save_map(request)
            self.assertEqual(error.exception.status_code, 409)
            restart.assert_not_called()
            with open(map_file) as f:
                self.assertEqual(f.read(), '{"name":"previous"}')
            with open(yaml_file) as f:
                self.assertEqual(f.read(), "previous")
            self.assertEqual(os.listdir(graph_dir), [])

    async def test_all_configuration_endpoints_use_shared_guard_before_save(self):
        from controller import adapter_controller, discovery_controller

        state.current_tasks.append({"id": "running", "fleet_name": "fleet2", "status": "active"})
        adapter = {"id": "a1", "fleet_name": "fleet1", "robots": []}
        operations = [
            lambda: adapter_controller.save_or_update_fleet_adapter(dict(adapter)),
            lambda: adapter_controller.delete_fleet_adapter("a1"),
            lambda: adapter_controller.set_fleet_adapter_graph("a1", {"graph_idx": 1}),
            lambda: discovery_controller.adopt_discovered_robot(discovery_controller.AdoptRequest(
                fleet_name="fleet1", robot_id="carter1", action="adopt_to_existing",
            )),
            discovery_controller.batch_adopt_discovered_robots,
        ]
        for operation in operations:
            with self.subTest(operation=operation), \
                    patch.object(adapter_controller, "load_fleet_adapters_data", return_value=[dict(adapter, robots=[])]), \
                    patch.object(discovery_controller, "load_fleet_adapters_data", return_value=[dict(adapter, robots=[])]), \
                    patch.object(discovery_controller, "get_discovered_robots_list", return_value=[
                        {"fleet_name": "fleet1", "robot_id": "carter1", "is_adopted": False},
                    ]), \
                    patch.object(adapter_controller, "save_fleet_adapters_data") as save_adapter, \
                    patch.object(discovery_controller, "save_fleet_adapters_data") as save_discovery, \
                    patch.object(rmf_service, "_restart_fleet_adapter_process") as restart:
                with self.assertRaises(HTTPException) as error:
                    await operation()
                self.assertEqual(error.exception.status_code, 409)
                save_adapter.assert_not_called()
                save_discovery.assert_not_called()
                restart.assert_not_called()
        with patch.object(adapter_controller, "load_fleet_adapters_data", return_value=[adapter]), \
                patch.object(adapter_controller, "save_fleet_adapters_data") as save:
            result = await adapter_controller.fleet_adapter_action("a1", {"action": "restart"})
        self.assertEqual(result["status"], "error")
        self.assertIn("fleet2", result["message"])
        save.assert_not_called()


class TestClearOfflineDiscoveredRobots(BackendFixture, unittest.IsolatedAsyncioTestCase):
    async def test_clear_offline_purges_only_unadopted_offline_robots(self):
        from controller import discovery_controller
        state.save_fleet_adapters_data([{
            "id": "a1", "fleet_name": "fleet1",
            "robots": [{"name": "carter1"}],
        }, {
            "id": "a2", "fleet_name": "fleet2",
            "robots": [{"name": "carter2"}],
        }])
        state.discovered_robots.clear()
        state.discovered_robots.update({
            "fleet1_carter1": {"fleet_name": "fleet1", "robot_id": "carter1", "status": "idle", "last_seen": time.time()},
            "fleet2_carter2": {"fleet_name": "fleet2", "robot_id": "carter2", "status": "offline", "last_seen": 100.0},
            "fleet1_e2e_bot": {"fleet_name": "fleet1", "robot_id": "e2e_bot", "status": "offline", "last_seen": 100.0},
            "floor_test_1": {"fleet_name": "floor_test", "robot_id": "floor_test_1", "status": "offline", "last_seen": 100.0},
            "live_unadopted": {"fleet_name": "live_fleet", "robot_id": "live_bot", "status": "idle", "last_seen": time.time()},
        })

        with patch("controller.discovery_controller.broadcast"), \
             patch("rmf_service.rmf_service.clear_offline_unadopted_robots"):
            res = await discovery_controller.clear_offline_discovered_robots()

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["cleared_count"], 2)
        self.assertIn("fleet1_e2e_bot", res["cleared_keys"])
        self.assertIn("floor_test_1", res["cleared_keys"])

        # Remaining in state.discovered_robots
        remaining = set(state.discovered_robots.keys())
        self.assertEqual(remaining, {"fleet1_carter1", "fleet2_carter2", "live_unadopted"})

    async def test_delete_single_prevents_adopted_robot_deletion(self):
        from controller import discovery_controller
        state.save_fleet_adapters_data([{
            "id": "a1", "fleet_name": "fleet1",
            "robots": [{"name": "carter1"}],
        }])
        state.discovered_robots.clear()
        state.discovered_robots.update({
            "fleet1_carter1": {"fleet_name": "fleet1", "robot_id": "carter1", "status": "offline"},
            "test_ghost": {"fleet_name": "test", "robot_id": "ghost_bot", "status": "offline"},
        })

        with patch("controller.discovery_controller.broadcast"), \
             patch("rmf_service.rmf_service.forget_discovered_robot"):
            err_res = await discovery_controller.delete_single_discovered_robot("fleet1", "carter1")
            self.assertEqual(err_res["status"], "error")
            self.assertIn("fleet1_carter1", state.discovered_robots)

            ok_res = await discovery_controller.delete_single_discovered_robot("test", "ghost_bot")
            self.assertEqual(ok_res["status"], "ok")
            self.assertNotIn("test_ghost", state.discovered_robots)


if __name__ == "__main__":
    unittest.main()
