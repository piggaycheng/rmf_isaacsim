#!/usr/bin/env python3
"""MQTT lift adapter translation and the mock lift (no ROS 2 or broker needed)."""

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../workspace/mqtt_lift_adapter")))
paho = types.ModuleType("paho")
paho.mqtt = types.ModuleType("paho.mqtt")
paho.mqtt.client = types.ModuleType("paho.mqtt.client")
with patch.dict(sys.modules, {"paho": paho, "paho.mqtt": paho.mqtt, "paho.mqtt.client": paho.mqtt.client}):
    import mock_lift
    import mqtt_lift_adapter as adapter


class TranslationTest(unittest.TestCase):
    def test_request_to_json(self):
        msg = SimpleNamespace(session_id="fleet1/carter1", request_type=1, destination_floor="B1",
                              door_state=2, request_time=SimpleNamespace(sec=3, nanosec=500000000))
        self.assertEqual(adapter.request_to_json(msg), {
            "session_id": "fleet1/carter1", "request_type": "agv", "destination_floor": "B1",
            "door_state": "open", "request_time": 3.5})
        msg.request_type, msg.door_state = 0, 0
        self.assertEqual(adapter.request_to_json(msg)["request_type"], "end_session")
        self.assertEqual(adapter.request_to_json(msg)["door_state"], "closed")

    def test_fill_state(self):
        data = {"lift_name": "lift_1", "available_floors": ["1F", "B1"], "current_floor": "B1",
                "destination_floor": "B1", "door_state": "open", "motion_state": "stopped",
                "current_mode": "agv", "session_id": "s"}
        msg = adapter.fill_state(SimpleNamespace(), data, "stamp")
        self.assertEqual((msg.lift_name, msg.available_floors, msg.door_state, msg.motion_state,
                          msg.current_mode, msg.available_modes, msg.lift_time),
                         ("lift_1", ["1F", "B1"], 2, 0, 2, [1, 2], "stamp"))
        stale = adapter.fill_state(SimpleNamespace(), dict(data, motion_state="sideways"), "t", offline=True)
        self.assertEqual((stale.current_mode, stale.motion_state), (4, 3))


class MockLiftTest(unittest.TestCase):
    def test_trip_and_sessions(self):
        now = [0.0]
        lift = mock_lift.MockLift("lift_1", ["1F", "B1"], clock=lambda: now[0])

        def run(seconds):
            for _ in range(int(seconds / 0.5)):
                now[0] += 0.5
                lift.update()

        lift.request({"session_id": "a", "request_type": "agv", "destination_floor": "1F", "door_state": "open"})
        run(2)
        self.assertEqual(lift.state()["door_state"], "open")
        lift.request({"session_id": "b", "request_type": "agv", "destination_floor": "B1"})
        lift.request({"session_id": "a", "request_type": "agv", "destination_floor": "B1", "door_state": "open"})
        run(1)
        self.assertEqual(lift.state()["door_state"], "moving")
        run(2)
        self.assertEqual(lift.state()["motion_state"], "down")
        run(6)
        run(2)
        state = lift.state()
        self.assertEqual((state["current_floor"], state["door_state"], state["session_id"]), ("B1", "open", "a"))
        lift.request({"session_id": "a", "request_type": "end_session"})
        self.assertEqual(lift.state()["current_mode"], "human")
        with self.assertRaises(ValueError):
            lift.request({"session_id": "a", "request_type": "agv", "destination_floor": "9F"})


if __name__ == "__main__":
    unittest.main()
