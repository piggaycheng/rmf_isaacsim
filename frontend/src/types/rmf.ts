export type WaypointType = 'normal' | 'charger' | 'parking' | 'workcell';

export interface Waypoint {
  id: string;
  name: string;
  x: number; // in meters (or canvas coordinates)
  y: number; // in meters (or canvas coordinates)
  type: WaypointType;
  yaw?: number; // radians
  graph_idx?: number; // default: 0
}

export function getWaypointGraphIndices(wp: Waypoint, lanes: Lane[]): number[] {
  const connectedGraphs = new Set<number>();
  for (const l of lanes) {
    if (l.start_id === wp.id || l.end_id === wp.id) {
      connectedGraphs.add(l.graph_idx ?? 0);
    }
  }
  if (connectedGraphs.size > 0) {
    return Array.from(connectedGraphs);
  }
  return [wp.graph_idx ?? 0];
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
  status: 'idle' | 'moving' | 'charging' | 'error';
  current_task?: string;
  path?: { x: number; y: number }[];
}

export type FleetAdapterType = 'easy_full_control' | 'read_and_request';
export type FleetAdapterStatus = 'online' | 'standby' | 'warning' | 'offline';

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
  robots: string[]; // robot IDs
  latency_ms?: number;
  updated_at?: string;
  logs?: string[];
}

export interface Task {
  id: string;
  type: 'patrol' | 'delivery' | 'goto';
  target_waypoint: string;
  destination_waypoint?: string;
  robot_id?: string;
  status: 'queued' | 'active' | 'completed' | 'failed';
  progress: number; // 0 - 100
  created_at: string;
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
  rtsp_path: string;
  enable_topic: string;
  width: number;
  height: number;
  fps: number;
  enabled: boolean;
}

export interface PlantCameraPayload {
  online: boolean;
  rtsp_port: number;
  cameras: CameraInfo[];
}
