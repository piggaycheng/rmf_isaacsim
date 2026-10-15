# Open-RMF MQTT Lift Adapter

Bridges Open-RMF lifts to elevators controlled over MQTT (e.g. the Isaac Sim Office elevator in
`isaacsim_fast-lio2-slam/scripts/mqtt_elevator.py`), so the upper system never talks ROS 2 to the field side.

```
fleet adapter ─/adapter_lift_requests─▶ lift_supervisor ─/lift_requests─▶ mqtt_lift_adapter ─MQTT─▶ lift
                                                         ◀─/lift_states──                   ◀─MQTT──
```

`backend/rmf_service.py` starts `lift_supervisor` and `mqtt_lift_adapter.py` together with the RMF core
(logs: `/tmp/lift_supervisor.log`, `/tmp/lift_adapter.log` in the container).

## MQTT topics (default prefix `rmf/lift`)

| Topic | Direction | Payload |
| --- | --- | --- |
| `rmf/lift/<lift>/request` | RMF → lift | `{"session_id", "request_type": "agv"\|"human"\|"end_session", "destination_floor", "door_state": "open"\|"closed", "request_time"}` |
| `rmf/lift/<lift>/state` (retained, ≥1 Hz) | lift → RMF | `{"lift_name", "available_floors", "current_floor", "destination_floor", "door_state": "closed"\|"moving"\|"open", "motion_state": "stopped"\|"up"\|"down", "current_mode": "human"\|"agv"\|"offline", "session_id"}` |

Lifts are discovered from their state topic. A state older than `--stale_after` (3 s) is reported as
`offline`, so RMF will not use it. Floor names must match the nav graph `levels` (e.g. `1F`, `B1`), and the
lift name must match the nav graph `lifts[].name`.

## Testing without Isaac Sim

```bash
python3 mock_lift.py --lift lift_1 --floors 1F,B1
python3 ../mqtt_fleet_adapter/mock_robot.py --level 1F ...   # supports change_floor
```
Then dispatch a `goto` to a waypoint on another level from the web UI or `POST /api/tasks/dispatch`.
