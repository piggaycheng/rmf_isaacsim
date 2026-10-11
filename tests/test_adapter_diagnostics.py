#!/usr/bin/env python3
"""Fleet Adapter diagnostics: loaded-graph staleness, dispatch pre-check, waypoint fallback warnings."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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
from rmf_service import rmf_service

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
                            ("ACTIVE_MAP_FILE", map_file)):
            patcher = patch.object(state, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(state.update_fa_state, {})
        state.update_fa_state({})
        state.paused_fleets.clear()
        state.estopped_robots.clear()

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


class TestReloadErrors(unittest.IsolatedAsyncioTestCase):
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
                    patch.object(rmf_service, "restart_fleet_adapter", return_value=False):
                result = await map_controller.save_map(req)
            self.assertIn("重新載入失敗", result["adapter_restart_error"])
            with open(map_file) as f:
                self.assertEqual(json.load(f)["waypoints"][0]["name"], "wp_1")


if __name__ == "__main__":
    unittest.main()
