"""Build Open-RMF building and nav graph YAML from the editor's JSON map.

Multi-floor model (all fields optional, so single-floor maps keep working):
- ``levels``: ``[{"name": "1F", "transform": {"rotation": rad, "scale": 1,
  "translation": [x, y]}}]``. Each level's waypoints are stored in that floor's robot
  (SLAM) map coordinates. ``transform`` maps the shared Open-RMF building frame into the
  floor map (robot = T + R(rotation) * scale * rmf, the fleet adapter convention); the
  first level is normally the identity, i.e. the building frame is the 1F map frame.
- ``lifts``: ``[{"name": "lift_1", "x", "y", "yaw", "width", "depth"}]``, cabin centre
  in the building frame.
- waypoint ``level`` (default: first level) and ``lift`` (the waypoint is inside that
  lift; one per lift and level). Lift waypoints are snapped onto the lift centre so
  Open-RMF's 0.1 m vertical alignment check holds despite map registration error.
Lanes connect waypoints of one level; Open-RMF adds the lift travel between levels.
"""

import math
import re
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_LEVEL = "1F"
LIFT_SNAP_TOLERANCE_M = 1.0
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
DEFAULT_GRAPHS = [{"id": 0, "name": "Graph 0 (預設車隊)", "color": "#38bdf8"}]


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必須是數值")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} 必須是數值") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} 必須是有限數值")
    return number


def normalize_transform(raw: Optional[Dict[str, Any]], label: str) -> Dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{label} transform 格式錯誤")
    translation = raw.get("translation", [0.0, 0.0])
    if not isinstance(translation, (list, tuple)) or len(translation) != 2:
        raise ValueError(f"{label} transform.translation 必須是 [x, y]")
    scale = _number(raw.get("scale", 1.0), f"{label} transform.scale")
    if scale <= 0.0:
        raise ValueError(f"{label} transform.scale 必須大於 0")
    return {
        "rotation": _number(raw.get("rotation", 0.0), f"{label} transform.rotation"),
        "scale": scale,
        "translation": [_number(v, f"{label} transform.translation") for v in translation],
    }


def parse_floor_order(name: str) -> Optional[float]:
    if not isinstance(name, str):
        return None
    s = name.strip()
    m = re.match(r'^B(\d+)$', s, re.IGNORECASE)
    if m:
        return -float(m.group(1))
    m = re.match(r'^(\d+)F$', s, re.IGNORECASE) or re.match(r'^[LF](\d+)$', s, re.IGNORECASE)
    if m:
        return float(m.group(1)) - 1.0
    if re.match(r'^G(F)?$', s, re.IGNORECASE):
        return 0.0
    return None


def normalize_levels(levels: Optional[List[Dict[str, Any]]],
                     waypoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validated levels; maps without levels get one identity level."""
    if not levels:
        used = [w.get("level") for w in waypoints if w.get("level")]
        levels = [{"name": used[0] if used else DEFAULT_LEVEL}]
    result, names = [], set()
    for level in levels:
        name = level.get("name") if isinstance(level, dict) else None
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise ValueError(f"樓層名稱 {name!r} 無效，僅能使用英數字、_、-（例如 1F、B1）")
        if name in names:
            raise ValueError(f"樓層名稱重複：{name}")
        names.add(name)
        lvl_dict = {**level, "name": name,
                    "transform": normalize_transform(level.get("transform"), f"樓層 {name}")}
        elevation = level.get("elevation") if isinstance(level, dict) else None
        if elevation is None:
            elevation = parse_floor_order(name)
        if elevation is not None:
            try:
                lvl_dict["elevation"] = float(elevation)
            except (ValueError, TypeError):
                pass
        result.append(lvl_dict)
    return result


def normalize_lifts(lifts: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    result, names = [], set()
    for lift in lifts or []:
        name = lift.get("name") if isinstance(lift, dict) else None
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise ValueError(f"電梯名稱 {name!r} 無效，僅能使用英數字、_、-")
        if name in names:
            raise ValueError(f"電梯名稱重複：{name}")
        names.add(name)
        item = {**lift, "name": name}
        for key, default in (("x", None), ("y", None), ("yaw", 0.0), ("width", 2.0), ("depth", 2.0)):
            value = lift.get(key, default)
            if value is None:
                raise ValueError(f"電梯 {name} 缺少 {key}")
            item[key] = _number(value, f"電梯 {name} {key}")
        if item["width"] <= 0.0 or item["depth"] <= 0.0:
            raise ValueError(f"電梯 {name} 的 width/depth 必須大於 0")
        result.append(item)
    return result


def to_robot(transform: Dict[str, Any], x: float, y: float) -> Tuple[float, float]:
    """Building (RMF) frame -> floor map frame."""
    c, s = math.cos(transform["rotation"]), math.sin(transform["rotation"])
    k = transform["scale"]
    tx, ty = transform["translation"]
    return tx + k * (c * x - s * y), ty + k * (s * x + c * y)


def to_building(transform: Dict[str, Any], x: float, y: float) -> Tuple[float, float]:
    """Floor map frame -> building (RMF) frame."""
    c, s = math.cos(transform["rotation"]), math.sin(transform["rotation"])
    k = transform["scale"]
    dx, dy = x - transform["translation"][0], y - transform["translation"][1]
    return (c * dx + s * dy) / k, (-s * dx + c * dy) / k


def waypoint_level(waypoint: Dict[str, Any], levels: List[Dict[str, Any]]) -> str:
    return waypoint.get("level") or levels[0]["name"]


def _waypoint_options(waypoint: Dict[str, Any], name: str) -> Dict[str, Any]:
    kind = waypoint.get("type")
    return {
        "is_charger": kind == "charger",
        "is_parking_spot": kind == "parking",
        "is_holding_point": kind == "parking",
        "is_workcell": kind == "workcell",
        "is_lift_cabin": kind in ("lift", "elevator"),
        # The fleet adapter waits for the robot's own arrival result here instead of finishing on
        # heartbeat proximity; lift cabins, chargers and workcells always need it.
        "precise_arrival": bool(waypoint.get("precise_arrival"))
        or kind in ("charger", "workcell", "lift", "elevator"),
        "name": name,
    }


def build(name: str, waypoints: List[Dict[str, Any]], lanes: List[Dict[str, Any]],
          graphs: Optional[List[Dict[str, Any]]] = None,
          levels: Optional[List[Dict[str, Any]]] = None,
          lifts: Optional[List[Dict[str, Any]]] = None,
          drawing: str = "",
          drawings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Validate the editor map and return {levels, lifts, graphs, building, nav_graphs}.

    nav_graphs maps graph index -> nav graph dict. Raises ValueError with a user message.
    """
    graphs = graphs or DEFAULT_GRAPHS
    levels = normalize_levels(levels, waypoints)
    lifts = normalize_lifts(lifts)
    level_by_name = {level["name"]: level for level in levels}
    lift_by_name = {lift["name"]: lift for lift in lifts}

    # (level, index within that level) per waypoint id, in editor order.
    slots: Dict[Any, Tuple[str, int]] = {}
    vertices: Dict[str, List[list]] = {level["name"]: [] for level in levels}
    lift_slots = set()
    for position, waypoint in enumerate(waypoints):
        wp_name = waypoint.get("name", f"wp_{position}")
        level = waypoint_level(waypoint, levels)
        if level not in level_by_name:
            raise ValueError(f"站點 {wp_name} 的樓層 {level} 未定義")
        transform = level_by_name[level]["transform"]
        x, y = to_building(transform, _number(waypoint.get("x", 0.0), f"站點 {wp_name} x"),
                           _number(waypoint.get("y", 0.0), f"站點 {wp_name} y"))
        options = _waypoint_options(waypoint, wp_name)
        lift_name = waypoint.get("lift")
        if waypoint.get("type") in ("lift", "elevator") and not lift_name:
            lift_name = "lift_1"
            waypoint["lift"] = lift_name

        if lift_name:
            lift = lift_by_name.get(lift_name)
            if lift is None:
                if not lifts:
                    lift = {
                        "name": lift_name,
                        "x": x,
                        "y": y,
                        "yaw": 0.0,
                        "width": 2.0,
                        "depth": 2.0,
                    }
                    lifts.append(lift)
                    lift_by_name[lift_name] = lift
                else:
                    raise ValueError(f"站點 {wp_name} 的電梯 {lift_name} 未定義")
            if (lift_name, level) in lift_slots:
                raise ValueError(f"電梯 {lift_name} 在 {level} 只能有一個電梯內站點")
            lift_slots.add((lift_name, level))
            offset = math.hypot(x - lift["x"], y - lift["y"])
            if offset > LIFT_SNAP_TOLERANCE_M:
                raise ValueError(
                    f"站點 {wp_name} 距電梯 {lift_name} 中心 {offset:.2f} m（上限 "
                    f"{LIFT_SNAP_TOLERANCE_M} m），請檢查站點位置或樓層 transform")
            x, y = lift["x"], lift["y"]
            options["lift"] = lift_name
            options["precise_arrival"] = True
        if waypoint.get("id") is not None:
            slots[waypoint.get("id")] = (level, len(vertices[level]))
        vertices[level].append([x, y, options])

    resolved = []
    for lane in lanes:
        start, end = slots.get(lane.get("start_id")), slots.get(lane.get("end_id"))
        if start is None or end is None:
            raise ValueError(f"路線 {lane.get('id', '')} 的起點或終點站點不存在")
        if start[0] != end[0]:
            raise ValueError(
                f"路線 {lane.get('id', '')} 跨越樓層 {start[0]}→{end[0]}；"
                "跨樓層請在各層設定同一電梯的電梯內站點")
        resolved.append((lane, start[0], start[1], end[1]))

    # Open-RMF needs a charger: keep the old rule of promoting the first vertex.
    flat = [vertex for level in levels for vertex in vertices[level["name"]]]
    if flat and not any(vertex[2]["is_charger"] for vertex in flat):
        flat[0][2]["is_charger"] = True
        flat[0][2]["is_parking_spot"] = True

    lifts_yaml = {
        lift["name"]: {"position": [lift["x"], lift["y"], lift["yaw"]],
                       "dims": [lift["width"], lift["depth"]]}
        for lift in lifts
    }
    graph_indices = sorted({int(lane.get("graph_idx", 0)) for lane in lanes}
                           | {int(graph.get("id", 0)) for graph in graphs} | {0})
    nav_graphs = {}
    for g_idx in graph_indices:
        # ``transform`` is ignored by RMF's parse_graph; the MQTT fleet adapter reads it to map
        # robot (per-floor SLAM) coordinates onto this graph and into the fleet config.
        graph_levels = {level["name"]: {"lanes": [], "transform": dict(level["transform"]), "vertices": [
            [v[0], v[1], dict(v[2])] for v in vertices[level["name"]]
        ]} for level in levels}
        used = set()
        for lane, level, start, end in resolved:
            if int(lane.get("graph_idx", 0)) != g_idx:
                continue
            used.update(((level, start), (level, end)))
            speed_limit = float(lane.get("speed_limit", 0.0))
            params = {"speed_limit": speed_limit} if speed_limit > 0.0 else {}
            graph_levels[level]["lanes"].append([start, end, dict(params)])
            if bool(lane.get("bidirectional", True)):
                graph_levels[level]["lanes"].append([end, start, dict(params)])
        if used and not any(graph_levels[lv]["vertices"][i][2]["is_charger"] for lv, i in used):
            level, index = min(used, key=lambda item: ([l["name"] for l in levels].index(item[0]), item[1]))
            graph_levels[level]["vertices"][index][2]["is_charger"] = True
            graph_levels[level]["vertices"][index][2]["is_parking_spot"] = True
        nav_graphs[g_idx] = {
            "building_name": name,
            "levels": graph_levels,
            "doors": {},
            "lifts": lifts_yaml,
        }

    drawings = drawings or {}
    building = {
        "name": name,
        "levels": {
            level["name"]: {
                "drawing": {
                    "filename": (
                        drawings.get(level["name"])
                        or level.get("drawing")
                        or (drawing if index == 0 else "")
                    )
                },
                "elevation": float(level.get("elevation", 0.0) if level.get("elevation") is not None else 0.0),
                "transform": level["transform"],
                "vertices": [
                    [v[0], v[1], 0.0, v[2]["name"],
                     {key: value for key, value in v[2].items() if key != "name"}]
                    for v in vertices[level["name"]]
                ],
                "lanes": [
                    [start, end, {
                        "is_bidirectional": bool(lane.get("bidirectional", True)),
                        "graph_idx": int(lane.get("graph_idx", 0)),
                        "speed_limit": float(lane.get("speed_limit", 0.0)),
                    }]
                    for lane, lane_level, start, end in resolved if lane_level == level["name"]
                ],
            }
            for index, level in enumerate(levels)
        },
        "lifts": lifts_yaml,
    }
    return {"levels": levels, "lifts": lifts, "graphs": graphs, "graph_indices": graph_indices,
            "building": building, "nav_graphs": nav_graphs}


def level_transforms(map_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Fleet config ``transforms`` for the levels of a saved editor map."""
    levels = normalize_levels(map_data.get("levels"), map_data.get("waypoints", []))
    return {level["name"]: level["transform"] for level in levels}
