export type WaypointType = 'normal' | 'charger' | 'parking' | 'workcell' | 'lift';

export interface Waypoint {
  id: string;
  name: string;
  x: number; // in meters (or canvas coordinates)
  y: number; // in meters (or canvas coordinates)
  type: WaypointType;
  yaw?: number; // radians
  graph_idx?: number; // default: 0
  level?: string; // floor name (e.g. 1F, B1); coordinates are in that floor's map. Default: first level
  lift?: string; // waypoint is inside this lift (one per lift and level)
}

export interface LevelTransform {
  rotation: number; // in radians
  scale: number;
  translation: [number, number]; // [tx, ty] in meters
}

/** Building floor; transform maps the Open-RMF building frame into this floor's map. */
export interface MapLevel {
  name: string;
  transform?: LevelTransform;
  elevation?: number;
  drawing?: string;
}

export function normalizeTransform(raw?: Partial<LevelTransform>): LevelTransform {
  return {
    rotation: typeof raw?.rotation === 'number' && !isNaN(raw.rotation) ? raw.rotation : 0,
    scale: typeof raw?.scale === 'number' && raw.scale > 0 ? raw.scale : 1.0,
    translation: Array.isArray(raw?.translation) && raw.translation.length === 2
      ? [Number(raw.translation[0]) || 0, Number(raw.translation[1]) || 0]
      : [0, 0],
  };
}

/**
 * Floor Map / Canvas Coordinate -> Actual Building Frame Coordinate (實際座標)
 * Inverse of: robot = T + R(rot) * scale * building
 */
export function toBuildingCoords(
  transform: LevelTransform | undefined,
  x: number,
  y: number
): { x: number; y: number } {
  const t = normalizeTransform(transform);
  const c = Math.cos(t.rotation);
  const s = Math.sin(t.rotation);
  const dx = x - t.translation[0];
  const dy = y - t.translation[1];
  return {
    x: (c * dx + s * dy) / t.scale,
    y: (-s * dx + c * dy) / t.scale,
  };
}

/**
 * Actual Building Frame Coordinate (實際座標) -> Floor Map / Canvas Coordinate
 * Formula: robot = T + R(rot) * scale * building
 */
export function toRobotCoords(
  transform: LevelTransform | undefined,
  bldgX: number,
  bldgY: number
): { x: number; y: number } {
  const t = normalizeTransform(transform);
  const c = Math.cos(t.rotation);
  const s = Math.sin(t.rotation);
  return {
    x: t.translation[0] + t.scale * (c * bldgX - s * bldgY),
    y: t.translation[1] + t.scale * (s * bldgX + c * bldgY),
  };
}

/** Lift cabin (centre in the building frame = first level's map frame). */
export interface MapLift {
  name: string;
  x: number;
  y: number;
  yaw?: number;
  width?: number;
  depth?: number;
}

export const DEFAULT_LEVEL = '1F';

export function waypointLevel(wp: Waypoint, levels: MapLevel[]): string {
  return wp.level || levels[0]?.name || DEFAULT_LEVEL;
}

export function getWaypointGraphIndices(wp: Waypoint, lanes: Lane[]): number[] {
  const connectedGraphs = new Set<number>();
  if (wp.graph_idx !== undefined) {
    connectedGraphs.add(wp.graph_idx);
  }
  for (const l of lanes) {
    if (l.start_id === wp.id || l.end_id === wp.id) {
      connectedGraphs.add(l.graph_idx ?? 0);
    }
  }
  if (connectedGraphs.size > 0) {
    return Array.from(connectedGraphs);
  }
  return [0];
}

export function isWaypointInGraph(wp: Waypoint, graphIdx: number, lanes: Lane[]): boolean {
  return getWaypointGraphIndices(wp, lanes).includes(graphIdx);
}

export interface NavGraph {
  id: number;
  name: string;
  color: string;
}

export const GRAPH_PALETTE = [
  '#38bdf8', // Cyan / Light Blue (Graph 0)
  '#f59e0b', // Amber / Orange (Graph 1)
  '#10b981', // Emerald / Green (Graph 2)
  '#a855f7', // Purple (Graph 3)
  '#f43f5e', // Rose / Red (Graph 4)
  '#6366f1', // Indigo (Graph 5)
  '#eab308', // Yellow (Graph 6)
  '#06b6d4', // Darker Cyan (Graph 7)
];

export interface Lane {
  id: string;
  start_id: string;
  end_id: string;
  bidirectional: boolean;
  speed_limit?: number; // m/s
  graph_idx?: number; // 0, 1, 2... default: 0
}

export interface Robot {
  id: string;
  name: string;
  fleet: string;
  x: number;
  y: number;
  yaw: number;
  battery: number; // 0 - 100
  status: 'idle' | 'moving' | 'charging' | 'paused' | 'offline' | 'emergency_stop' | 'error' | 'off_graph' | 'waiting';
  current_task?: string;
  task_id?: string;
  emergency_stop?: boolean;
  online?: boolean;
  is_off_graph?: boolean;
  off_graph_distance?: number;
  path?: { x: number; y: number; level_name?: string }[];
  level_name?: string;
}

export type FleetAdapterType = 'easy_full_control' | 'read_and_request';
export type FleetAdapterStatus = 'online' | 'standby' | 'warning' | 'offline';

export interface AdapterRobotConfig {
  name: string;
  charger_waypoint?: string;
  parking_waypoint?: string;
}

export function normalizeRobotConfig(
  r: string | AdapterRobotConfig,
  defaultParking = 'parking_1',
  defaultCharger = 'charger_1'
): AdapterRobotConfig {
  if (typeof r === 'string') {
    return {
      name: r,
      parking_waypoint: defaultParking,
      charger_waypoint: defaultCharger,
    };
  }
  return {
    name: r.name || '',
    parking_waypoint: r.parking_waypoint || defaultParking,
    charger_waypoint: r.charger_waypoint || defaultCharger,
  };
}

export interface FleetAdapter {
  id: string;
  name: string;
  fleet_name: string;
  adapter_type: FleetAdapterType;
  status: FleetAdapterStatus;
  graph_idx: number;
  ros2_domain_id: number;
  linear_velocity: number; // m/s
  angular_velocity: number; // rad/s
  robot_radius: number; // meters
  recharge_threshold: number; // %
  recharge_target: number; // %
  default_charger?: string;
  default_parking?: string;
  robots: (string | AdapterRobotConfig)[]; // robot IDs or dedicated configs
  latency_ms?: number;
  updated_at?: string;
  logs?: string[];
  diagnostics?: FleetAdapterDiagnostics;
}

/** Runtime comparison between the configured adapter and what the running Fleet Adapter loaded. */
export interface FleetAdapterDiagnostics {
  status: 'ok' | 'stale' | 'unknown';
  issues: string[];
  warnings: string[];
  loaded_graph_idx?: number | null;
  loaded_waypoints?: number;
  fa_state_fresh?: boolean;
}

export interface DiscoveredRobot {
  robot_id: string;
  fleet_name: string;
  x: number;
  y: number;
  yaw: number;
  battery: number;
  status: string;
  connection_status: 'online' | 'offline';
  is_adopted: boolean;
  adopted_info?: {
    adapter_id: string;
    adapter_name: string;
    fleet_name: string;
  } | null;
  last_seen: number;
  specs?: {
    footprint_radius?: number;
    max_linear_velocity?: number;
    max_angular_velocity?: number;
  };
  initial_location?: {
    x?: number;
    y?: number;
    yaw?: number;
    level_name?: string;
    waypoint_name?: string;
  };
  default_parking?: string;
  default_charger?: string;
  is_off_graph?: boolean;
  off_graph_distance?: number;
}

export type MultiStepType = 'navigate' | 'gimbal_move' | 'gimbal_rotate';

export interface MultiStepItem {
  id: string;
  type: MultiStepType;
  // navigate:
  place?: string;
  x?: number;
  y?: number;
  yaw?: number;
  // gimbal_move / gimbal_rotate:
  pan_deg?: number;
  tilt_deg?: number;
  duration_s?: number;
}

export interface MultiStepTaskRequestData {
  mode: 'compose' | 'direct';
  robot_id?: string;
  fleet_name?: string;
  auto_proceed?: boolean;
  step_delay?: number;
  task_id?: string;
  steps: {
    type: MultiStepType;
    place?: string;
    x?: number;
    y?: number;
    yaw?: number;
    pan_deg?: number;
    tilt_deg?: number;
    duration_s?: number;
  }[];
}

export interface Task {
  id: string;
  request_id?: string;
  booking_id?: string;
  type: 'patrol' | 'delivery' | 'goto' | 'compose' | 'multi_step' | string;
  target_waypoint: string;
  destination_waypoint?: string;
  robot_id?: string;
  fleet_name?: string;
  status: 'queued' | 'active' | 'completed' | 'failed' | 'canceled' | 'waiting' | 'canceling' | string;
  progress: number; // 0 - 100
  created_at: string;
  error?: string;
  detail?: string;
  cmd_id?: number;
  current_step?: number;
  total_steps?: number;
  auto_proceed?: boolean;
  step_delay?: number;
  steps?: any[];
}


export interface SlamMap {
  name: string;
  image_url: string;
  resolution: number; // meters per pixel (e.g. 0.05)
  origin: [number, number, number]; // [x, y, yaw]
  width: number; // in pixels
  height: number; // in pixels
  real_width_m: number; // in meters
  real_height_m: number; // in meters
  opacity?: number; // 0.0 - 1.0
  level_name?: string;
}

export interface PlantSite {
  id: string;
  name: string;
  code: string;
  locationName: string;
  country: string;
  coordinates: [number, number]; // [lng, lat]
  status: 'online' | 'warning' | 'offline';
  robotCount: number;
  activeTasks: number;
  areaM2: number;
  description: string;
  isaacSimWebRTCUrl: string; // e.g. "ws://localhost:8080/webrtc" or "http://localhost:8011"
  mqttBrokerUrl?: string; // e.g. "ws://localhost:8083/mqtt"
  cameraTopic?: string; // e.g. "slam/cameras"
}

export interface CameraInfo {
  name: string;
  display_name?: string;
  rtsp_path: string;
  enable_topic: string;
  width: number;
  height: number;
  fps: number;
  enabled: boolean;
  gimbal_topic?: string;
}

export interface PlantCameraPayload {
  online: boolean;
  rtsp_port: number;
  cameras: CameraInfo[];
}

export interface GimbalCommandPayload {
  cmd_id: number | string;
  action: 'move' | 'rotate' | 'cancel';
  pan_deg?: number;
  tilt_deg?: number;
}

export interface GimbalStatePayload {
  robot_id: string;
  pan_deg: number;
  tilt_deg: number;
  busy: boolean;
  current_cmd_id?: number | string | null;
  timestamp: number;
}

export interface GimbalResultPayload {
  robot_id: string;
  cmd_id: number | string;
  status: 'succeeded' | 'aborted' | 'canceled' | 'rejected';
  message: string;
  pan_deg?: number;
  tilt_deg?: number;
  timestamp: number;
}

export interface GimbalStatusPayload {
  robot_id: string;
  online: boolean;
}
