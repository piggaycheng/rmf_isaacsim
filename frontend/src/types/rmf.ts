export type WaypointType = 'normal' | 'charger' | 'parking' | 'workcell';

export interface Waypoint {
  id: string;
  name: string;
  x: number; // in meters (or canvas coordinates)
  y: number; // in meters (or canvas coordinates)
  type: WaypointType;
  yaw?: number; // radians
}

export interface Lane {
  id: string;
  start_id: string;
  end_id: string;
  bidirectional: boolean;
  speed_limit?: number; // m/s
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
}

