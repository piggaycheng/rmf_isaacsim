#!/usr/bin/env python3
"""Multi-floor nav graph generation (levels, level transforms, lifts)."""

import math
import os
import sys
import unittest

for p in (os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")), "/root/rmf_ws/backend"):
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import nav_graph_builder as builder

B1 = {"rotation": math.pi / 2, "scale": 1.0, "translation": [10.0, 0.0]}
LEVELS = [{"name": "1F"}, {"name": "B1", "transform": B1}]
LIFTS = [{"name": "lift_1", "x": 2.0, "y": 1.0}]


def wp(id_, x, y, level=None, lift=None, kind="normal"):
    item = {"id": id_, "name": id_, "x": x, "y": y, "type": kind}
    if level:
        item["level"] = level
    if lift:
        item["lift"] = lift
    return item


def lane(a, b, graph=0, bidirectional=True):
    return {"id": f"{a}-{b}", "start_id": a, "end_id": b, "graph_idx": graph,
            "bidirectional": bidirectional}


class TransformTest(unittest.TestCase):
    def test_round_trip(self):
        transform = builder.normalize_transform({**B1, "scale": 2.0}, "t")
        x, y = builder.to_robot(transform, 1.5, -2.0)
        self.assertEqual(tuple(round(v, 9) for v in builder.to_building(transform, x, y)), (1.5, -2.0))
        self.assertEqual(tuple(round(v, 9) for v in builder.to_robot(B1, 1.0, 0.0)), (10.0, 1.0))

    def test_invalid_transform(self):
        for raw in ({"scale": 0}, {"translation": [1]}, {"rotation": "x"}, {"rotation": float("nan")}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                builder.normalize_transform(raw, "t")


class BuildTest(unittest.TestCase):
    def test_single_floor_map_keeps_old_shape(self):
        built = builder.build("office", [wp("a", 0, 0), wp("b", 1, 0)], [lane("a", "b")])
        self.assertEqual([level["name"] for level in built["levels"]], ["1F"])
        graph = built["nav_graphs"][0]
        self.assertEqual(list(graph["levels"]), ["1F"])
        self.assertEqual(graph["levels"]["1F"]["lanes"], [[0, 1, {}], [1, 0, {}]])
        self.assertTrue(graph["levels"]["1F"]["vertices"][0][2]["is_charger"])
        self.assertEqual(graph["lifts"], {})
        self.assertEqual(built["graph_indices"], [0])
        self.assertEqual(builder.level_transforms({"waypoints": []}),
                         {"1F": {"rotation": 0.0, "scale": 1.0, "translation": [0.0, 0.0]}})

    def test_two_floors_with_lift(self):
        # B1 map coordinates of building (2, 1) are (10 - 1, 2) = (9, 2).
        waypoints = [
            wp("hall", 0, 0, kind="charger"), wp("lift_1F", 2.3, 1.0, "1F", "lift_1"),
            wp("lift_B1", 9.0, 2.2, "B1", "lift_1"), wp("b1_desk", 9.0, 5.0, "B1"),
        ]
        built = builder.build("office", waypoints,
                              [lane("hall", "lift_1F"), lane("lift_B1", "b1_desk", 1, False)],
                              [{"id": 0}, {"id": 1}], LEVELS, LIFTS)
        g0, g1 = built["nav_graphs"][0], built["nav_graphs"][1]
        self.assertEqual(g0["lifts"], {"lift_1": {"position": [2.0, 1.0, 0.0], "dims": [2.0, 2.0]}})
        f1, b1 = g0["levels"]["1F"]["vertices"], g0["levels"]["B1"]["vertices"]
        self.assertEqual(f1[1][:2], [2.0, 1.0])
        self.assertEqual(b1[0][:2], [2.0, 1.0])
        self.assertEqual((f1[1][2]["lift"], b1[0][2]["lift"]), ("lift_1", "lift_1"))
        desk = b1[1]
        self.assertAlmostEqual(desk[0], 5.0)
        self.assertAlmostEqual(desk[1], 1.0)
        self.assertEqual(g0["levels"]["B1"]["lanes"], [])
        self.assertEqual(g1["levels"]["B1"]["lanes"], [[0, 1, {}]])
        # Graph 1 only uses B1 vertices: one of them becomes its charger.
        self.assertTrue(g1["levels"]["B1"]["vertices"][0][2]["is_charger"])
        self.assertEqual(list(built["building"]["levels"]), ["1F", "B1"])
        self.assertEqual(built["building"]["levels"]["B1"]["transform"]["translation"], [10.0, 0.0])
        # Lift cabins and chargers are precise.
        self.assertEqual([v[2]["precise_arrival"] for v in f1], [True, True])
        self.assertEqual([v[2]["precise_arrival"] for v in b1], [True, False])

    def test_precise_arrival_flag(self):
        waypoints = [wp("a", 0, 0), wp("b", 1, 0), wp("c", 2, 0, kind="workcell")]
        waypoints[1]["precise_arrival"] = True
        built = builder.build("office", waypoints, [lane("a", "b"), lane("b", "c")])
        vertices = built["nav_graphs"][0]["levels"]["1F"]["vertices"]
        # "a" is promoted to the graph's charger but stays an ordinary (non-precise) waypoint.
        self.assertTrue(vertices[0][2]["is_charger"])
        self.assertEqual([v[2]["precise_arrival"] for v in vertices], [False, True, True])

    def test_rejections(self):
        cases = {
            "跨越樓層": ([wp("a", 0, 0), wp("b", 9, 2, "B1")], [lane("a", "b")], LEVELS, []),
            "未定義": ([wp("a", 0, 0, "2F")], [], LEVELS, []),
            "距電梯": ([wp("a", 5, 5, "1F", "lift_1")], [], LEVELS, LIFTS),
            "只能有一個": ([wp("a", 2, 1, "1F", "lift_1"), wp("b", 2, 1, "1F", "lift_1")], [], LEVELS, LIFTS),
            "不存在": ([wp("a", 0, 0)], [lane("a", "missing")], LEVELS, []),
            "樓層名稱重複": ([], [], [{"name": "1F"}, {"name": "1F"}], []),
            "無效": ([], [], [{"name": "../1F"}], []),
            "電梯 lift_2 未定義": ([wp("a", 2, 1, "1F", "lift_2")], [], LEVELS, LIFTS),
        }
        for message, (waypoints, lanes, levels, lifts) in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                builder.build("office", waypoints, lanes, None, levels, lifts)


if __name__ == "__main__":
    unittest.main()
