import React, { useRef, useEffect, useState, useMemo, useCallback } from 'react';
import { Canvas, useFrame, useThree, ThreeEvent } from '@react-three/fiber';
import { OrbitControls, Html, Edges } from '@react-three/drei';
import * as THREE from 'three';
import {
  MapLevel,
  MapLift,
  Robot,
  SlamMap,
  Waypoint,
  Lane,
  NavGraph,
  LiftRuntimeState,
  toBuildingCoords,
  toRobotCoords,
  normalizeTransform,
  resolveLevelElevations,
  waypointLevel,
  getWaypointGraphIndices,
  computeLiftYawFromLanes,
} from '@/types/rmf';
import {
  Layers,
  Eye,
  Sliders,
  ChevronDown,
  RotateCcw,
  Sparkles,
  Maximize2,
  Box,
} from 'lucide-react';

export interface Map3DCanvasProps {
  levels: MapLevel[];
  activeLevel?: string;
  floorSlamMaps: Record<string, SlamMap>;
  waypoints: Waypoint[];
  lanes: Lane[];
  robots: Robot[];
  lifts?: MapLift[];
  liftStates?: Record<string, LiftRuntimeState>;
  graphs?: NavGraph[];
  selectedWaypointIds?: string[];
  onSelectWaypoint?: (id: string | null) => void;
  onWaypointClickInMonitor?: (waypoint: Waypoint) => void;
  onFocusFloor?: (levelName: string) => void;
}

// -------------------------------------------------------------
// Helper: Draw single floor on offscreen canvas
// -------------------------------------------------------------
interface DrawFloorParams {
  canvas: HTMLCanvasElement;
  level: MapLevel;
  slamMap?: SlamMap;
  slamImage?: HTMLImageElement | null;
  waypoints: Waypoint[];
  lanes: Lane[];
  robots: Robot[];
  graphs?: NavGraph[];
  selectedWaypointIds?: string[];
  bounds: { minX: number; maxX: number; minY: number; maxY: number; W: number; H: number };
}

function renderFloorToCanvas({
  canvas,
  level,
  slamMap,
  slamImage,
  waypoints,
  lanes,
  robots,
  graphs,
  selectedWaypointIds = [],
  bounds,
}: DrawFloorParams) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const { minX, maxX, minY, maxY, W, H } = bounds;
  const canvasW = canvas.width;
  const canvasH = canvas.height;
  const scaleX = canvasW / W;
  const scaleY = canvasH / H;

  // 1. Clear & Dark Background Plate
  ctx.clearRect(0, 0, canvasW, canvasH);
  ctx.fillStyle = '#080d1a';
  ctx.fillRect(0, 0, canvasW, canvasH);

  // Border frame
  ctx.strokeStyle = '#1e293b';
  ctx.lineWidth = 4;
  ctx.strokeRect(2, 2, canvasW - 4, canvasH - 4);

  // 2. 1m & 5m Grid Lines
  ctx.save();
  const xStart = Math.ceil(minX);
  const xEnd = Math.floor(maxX);
  const yStart = Math.ceil(minY);
  const yEnd = Math.floor(maxY);

  for (let x = xStart; x <= xEnd; x++) {
    const isMajor = Math.abs(x % 5) === 0;
    ctx.strokeStyle = isMajor ? 'rgba(255, 255, 255, 0.08)' : 'rgba(255, 255, 255, 0.03)';
    ctx.lineWidth = isMajor ? 1.5 : 1;
    const px = (x - minX) * scaleX;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, canvasH);
    ctx.stroke();
  }

  for (let y = yStart; y <= yEnd; y++) {
    const isMajor = Math.abs(y % 5) === 0;
    ctx.strokeStyle = isMajor ? 'rgba(255, 255, 255, 0.08)' : 'rgba(255, 255, 255, 0.03)';
    ctx.lineWidth = isMajor ? 1.5 : 1;
    const py = (maxY - y) * scaleY;
    ctx.beginPath();
    ctx.moveTo(0, py);
    ctx.lineTo(canvasW, py);
    ctx.stroke();
  }
  ctx.restore();

  // 3. SLAM Background Map (if loaded)
  if (slamMap && slamImage && slamImage.complete && slamImage.naturalWidth > 0) {
    const slamLeft = (slamMap.origin[0] - minX) * scaleX;
    const slamTop = (maxY - (slamMap.origin[1] + slamMap.real_height_m)) * scaleY;
    const slamWidth = slamMap.real_width_m * scaleX;
    const slamHeight = slamMap.real_height_m * scaleY;

    ctx.save();
    ctx.globalAlpha = slamMap.opacity ?? 0.85;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(slamImage, slamLeft, slamTop, slamWidth, slamHeight);

    // Cyan dashed bounding box
    ctx.strokeStyle = '#0284c7';
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 6]);
    ctx.strokeRect(slamLeft, slamTop, slamWidth, slamHeight);
    ctx.restore();
  }

  // Helper coordinate converter for this floor
  const toScreen = (x: number, y: number) => ({
    x: (x - minX) * scaleX,
    y: (maxY - y) * scaleY,
  });

  // 4. Lanes (Edges)
  lanes.forEach((lane) => {
    const p1 = waypoints.find((w) => w.id === lane.start_id);
    const p2 = waypoints.find((w) => w.id === lane.end_id);
    if (!p1 || !p2) return;

    const s1 = toScreen(p1.x, p1.y);
    const s2 = toScreen(p2.x, p2.y);
    const laneGraphIdx = lane.graph_idx ?? 0;
    const graphDef = graphs?.find((g) => g.id === laneGraphIdx);
    const graphColor = graphDef ? graphDef.color : '#38bdf8';

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(s1.x, s1.y);
    ctx.lineTo(s2.x, s2.y);
    ctx.strokeStyle = graphColor;
    ctx.lineWidth = 3.5;
    ctx.setLineDash(lane.bidirectional ? [] : [6, 4]);
    ctx.stroke();

    // Direction arrow at midpoint
    const midX = (s1.x + s2.x) / 2;
    const midY = (s1.y + s2.y) / 2;
    const angle = Math.atan2(s2.y - s1.y, s2.x - s1.x);

    ctx.fillStyle = graphColor;
    ctx.beginPath();
    ctx.arc(midX, midY, 3.5, 0, Math.PI * 2);
    ctx.fill();

    ctx.save();
    ctx.translate(midX, midY);
    ctx.rotate(angle);
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(-8, -4);
    ctx.lineTo(-8, 4);
    ctx.closePath();
    ctx.fill();
    ctx.restore();

    ctx.restore();
  });

  // 5. Waypoints
  waypoints.forEach((wp) => {
    const pos = toScreen(wp.x, wp.y);
    const isSelected = selectedWaypointIds.includes(wp.id);
    const wpGraphIndices = getWaypointGraphIndices(wp, lanes);
    const primaryGraphIdx = wpGraphIndices[0] ?? (wp.graph_idx ?? 0);
    const graphDef = graphs?.find((g) => g.id === primaryGraphIdx);
    const graphColor = graphDef ? graphDef.color : '#38bdf8';

    ctx.save();

    // Outer glow if selected
    if (isSelected) {
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, 16, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(245, 158, 11, 0.35)';
      ctx.fill();
      ctx.strokeStyle = '#f59e0b';
      ctx.lineWidth = 2.5;
      ctx.stroke();
    }

    // Waypoint circle
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, 9, 0, Math.PI * 2);

    let badge = '';
    let accentBorderColor = '#ffffff';

    if (wp.type === 'charger') {
      badge = '⚡';
      accentBorderColor = '#f59e0b';
    } else if (wp.type === 'parking') {
      badge = 'P';
      accentBorderColor = '#c084fc';
    } else if (wp.type === 'workcell') {
      badge = 'W';
      accentBorderColor = '#34d399';
    } else if (wp.type === 'lift') {
      badge = 'L';
      accentBorderColor = '#818cf8';
    }

    ctx.fillStyle = graphColor;
    ctx.fill();
    ctx.strokeStyle = accentBorderColor;
    ctx.lineWidth = 1.8;
    ctx.stroke();

    // Badge symbol inside
    if (badge) {
      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 9px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(badge, pos.x, pos.y);
    }

    // In 3D mode, waypoint names are rendered via upright 3D billboard badges (WaypointNode3D)
    ctx.restore();
  });

  // 6. Robots (Rendered with identical 2D aesthetic directly on floor surface)
  robots.forEach((robot) => {
    const rPos = toScreen(robot.x, robot.y);

    ctx.save();

    // Planned path glowing line
    if (robot.path && robot.path.length > 0) {
      ctx.beginPath();
      ctx.moveTo(rPos.x, rPos.y);
      robot.path.forEach((pt) => {
        const screenPt = toScreen(pt.x, pt.y);
        ctx.lineTo(screenPt.x, screenPt.y);
      });
      ctx.strokeStyle = '#06b6d4';
      ctx.lineWidth = 3;
      ctx.shadowColor = '#06b6d4';
      ctx.shadowBlur = 8;
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    // AMR circular footprint + directional pointer
    ctx.translate(rPos.x, rPos.y);
    ctx.rotate(-robot.yaw); // Screen Y inversion

    const isMoving = robot.status === 'moving';
    const isEstopped = robot.status === 'emergency_stop' || !!robot.emergency_stop;
    const isOffGraph = Boolean(robot.is_off_graph || robot.status === 'off_graph');
    const statusColor = isEstopped ? '#ef4444' : isOffGraph ? '#f59e0b' : isMoving ? '#06b6d4' : '#10b981';
    const arrowColor = isEstopped ? '#f87171' : isOffGraph ? '#fbbf24' : isMoving ? '#22d3ee' : '#34d399';

    // Base dark circular pad for high contrast
    ctx.beginPath();
    ctx.arc(0, 0, 18, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(15, 23, 42, 0.75)';
    ctx.fill();

    // Pulsing aura
    ctx.beginPath();
    ctx.arc(0, 0, 18, 0, Math.PI * 2);
    ctx.fillStyle = isEstopped
      ? 'rgba(239, 68, 68, 0.35)'
      : isOffGraph
      ? 'rgba(245, 158, 11, 0.3)'
      : isMoving
      ? 'rgba(6, 182, 212, 0.3)'
      : 'rgba(16, 185, 129, 0.25)';
    ctx.fill();
    ctx.strokeStyle = statusColor;
    ctx.lineWidth = 2.2;
    ctx.stroke();

    // Directional Arrow Head
    ctx.beginPath();
    ctx.moveTo(15, 0);
    ctx.lineTo(-10, -10);
    ctx.lineTo(-6, 0);
    ctx.lineTo(-10, 10);
    ctx.closePath();
    ctx.strokeStyle = '#090d16';
    ctx.lineWidth = 2.5;
    ctx.stroke();
    ctx.fillStyle = arrowColor;
    ctx.fill();

    ctx.restore();

    // In 3D mode, AMR robot status and names are rendered via upright 3D billboard entities (RobotEntity3D)
  });
}

// -------------------------------------------------------------
// Component: WaypointNode3D (Floating Billboard Badge + 3D Anchor)
// -------------------------------------------------------------
interface WaypointNode3DProps {
  waypoint: Waypoint;
  bounds: { cx: number; cy: number };
  isSelected: boolean;
  graphColor: string;
  onSelect?: (id: string | null) => void;
  onClickInMonitor?: (waypoint: Waypoint) => void;
}

const WaypointNode3D: React.FC<WaypointNode3DProps> = ({
  waypoint,
  bounds,
  isSelected,
  graphColor,
  onSelect,
  onClickInMonitor,
}) => {
  const localX = waypoint.x - bounds.cx;
  const localY = waypoint.y - bounds.cy;

  let badgeIcon = null;
  if (waypoint.type === 'charger') badgeIcon = <span className="text-amber-400">⚡</span>;
  else if (waypoint.type === 'parking') badgeIcon = <span className="text-purple-400 font-bold">P</span>;
  else if (waypoint.type === 'workcell') badgeIcon = <span className="text-emerald-400 font-bold">W</span>;
  else if (waypoint.type === 'lift') badgeIcon = <span className="text-indigo-400">🛗</span>;

  return (
    <group position={[localX, localY, 0]}>
      {/* 3D Vertical Marker Stem */}
      <mesh position={[0, 0, 0.11]}>
        <cylinderGeometry args={[0.015, 0.015, 0.22, 8]} />
        <meshBasicMaterial color={isSelected ? '#f59e0b' : graphColor} transparent opacity={0.65} />
      </mesh>

      {/* 3D Anchor Node Dot */}
      <mesh position={[0, 0, 0.22]}>
        <sphereGeometry args={[0.07, 16, 16]} />
        <meshStandardMaterial
          color={isSelected ? '#fbbf24' : graphColor}
          emissive={isSelected ? '#f59e0b' : graphColor}
          emissiveIntensity={0.6}
        />
      </mesh>

      {/* Upright Floating Billboard Name Badge (same style as Elevator) */}
      <Html position={[0, 0, 0.44]} center distanceFactor={26}>
        <div
          onClick={(e) => {
            e.stopPropagation();
            onSelect?.(waypoint.id);
            onClickInMonitor?.(waypoint);
          }}
          className={`cursor-pointer select-none px-2 py-0.5 rounded-lg border text-[11px] font-semibold shadow-xl backdrop-blur flex items-center gap-1 whitespace-nowrap transition-all ${
            isSelected
              ? 'bg-amber-950/95 border-amber-400 text-amber-300 ring-2 ring-amber-400/50 scale-110 z-10'
              : 'bg-slate-950/85 border-slate-700/80 text-slate-200 hover:border-cyan-400 hover:text-cyan-300 hover:scale-105'
          }`}
          title={`站點: ${waypoint.name} (點擊快速派單)`}
        >
          {badgeIcon}
          <span>{waypoint.name}</span>
        </div>
      </Html>
    </group>
  );
};

// -------------------------------------------------------------
// Component: RobotBadge3D (Upright Floating Billboard Status Badge)
// -------------------------------------------------------------
interface RobotBadge3DProps {
  robot: Robot;
  bounds: { cx: number; cy: number };
  onClick?: () => void;
}

const RobotBadge3D: React.FC<RobotBadge3DProps> = ({ robot, bounds, onClick }) => {
  const localX = robot.x - bounds.cx;
  const localY = robot.y - bounds.cy;

  const isMoving = robot.status === 'moving';
  const isEstopped = robot.status === 'emergency_stop' || !!robot.emergency_stop;
  const isOffGraph = Boolean(robot.is_off_graph || robot.status === 'off_graph');
  const statusColor = isEstopped ? '#ef4444' : isOffGraph ? '#f59e0b' : isMoving ? '#06b6d4' : '#10b981';
  const statusLabel = isEstopped ? '急停' : isOffGraph ? '偏移' : isMoving ? '移動中' : '待命';

  return (
    <group position={[localX, localY, 0]}>
      {/* Upright Floating Billboard Status Badge hovering above 2D AMR icon */}
      <Html position={[0, 0, 0.95]} center distanceFactor={28}>
        <div
          onClick={(e) => {
            e.stopPropagation();
            onClick?.();
          }}
          className="cursor-pointer select-none px-2.5 py-1 rounded-xl bg-slate-950/90 border border-slate-700/80 hover:border-cyan-400 shadow-2xl backdrop-blur flex items-center gap-1.5 whitespace-nowrap transition-all hover:scale-105"
        >
          <span className="w-2 h-2 rounded-full animate-pulse" style={{ backgroundColor: statusColor }} />
          <span className="font-bold text-xs text-white">🤖 {robot.name}</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-900 text-cyan-300 font-mono font-bold border border-slate-800">
            ⚡ {Math.round(robot.battery)}%
          </span>
          <span
            className="text-[9px] px-1.5 py-0.5 rounded font-bold"
            style={{ backgroundColor: `${statusColor}25`, color: statusColor }}
          >
            {statusLabel}
          </span>
        </div>
      </Html>
    </group>
  );
};

// -------------------------------------------------------------
// Component: FloorLevelPlane (Single Floor Plane in 3D)
// -------------------------------------------------------------
interface FloorLevelPlaneProps {
  level: MapLevel;
  elevation: number;
  slamMap?: SlamMap;
  waypoints: Waypoint[];
  lanes: Lane[];
  robots: Robot[];
  graphs?: NavGraph[];
  selectedWaypointIds?: string[];
  floorOpacity: number;
  onSelectWaypoint?: (id: string | null) => void;
  onWaypointClickInMonitor?: (waypoint: Waypoint) => void;
  onFocusFloor?: (levelName: string) => void;
}

const FloorLevelPlane: React.FC<FloorLevelPlaneProps> = ({
  level,
  elevation,
  slamMap,
  waypoints,
  lanes,
  robots,
  graphs,
  selectedWaypointIds,
  floorOpacity,
  onSelectWaypoint,
  onWaypointClickInMonitor,
  onFocusFloor,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textureRef = useRef<THREE.CanvasTexture | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [, setTick] = useState(0);

  // Compute floor local bounding box
  const bounds = useMemo(() => {
    let minX = 0;
    let maxX = 0;
    let minY = 0;
    let maxY = 0;
    let hasData = false;

    if (slamMap) {
      minX = slamMap.origin[0];
      maxX = slamMap.origin[0] + slamMap.real_width_m;
      minY = slamMap.origin[1];
      maxY = slamMap.origin[1] + slamMap.real_height_m;
      hasData = true;
    }

    if (waypoints.length > 0) {
      const xs = waypoints.map((w) => w.x);
      const ys = waypoints.map((w) => w.y);
      const wpMinX = Math.min(...xs) - 3;
      const wpMaxX = Math.max(...xs) + 3;
      const wpMinY = Math.min(...ys) - 3;
      const wpMaxY = Math.max(...ys) + 3;

      if (!hasData) {
        minX = wpMinX;
        maxX = wpMaxX;
        minY = wpMinY;
        maxY = wpMaxY;
        hasData = true;
      } else {
        minX = Math.min(minX, wpMinX);
        maxX = Math.max(maxX, wpMaxX);
        minY = Math.min(minY, wpMinY);
        maxY = Math.max(maxY, wpMaxY);
      }
    }

    if (!hasData) {
      minX = -15;
      maxX = 15;
      minY = -15;
      maxY = 15;
    }

    // Safety margin
    minX -= 1;
    maxX += 1;
    minY -= 1;
    maxY += 1;

    const W = Math.max(10, maxX - minX);
    const H = Math.max(10, maxY - minY);
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;

    return { minX, maxX, minY, maxY, W, H, cx, cy };
  }, [slamMap, waypoints]);

  // Create canvas and texture once or on bounds change
  const { canvas, texture } = useMemo(() => {
    const cvs = document.createElement('canvas');
    const pxPerMeter = 32;
    cvs.width = Math.min(2048, Math.max(512, Math.round(bounds.W * pxPerMeter)));
    cvs.height = Math.min(2048, Math.max(512, Math.round(bounds.H * pxPerMeter)));

    const tex = new THREE.CanvasTexture(cvs);
    tex.minFilter = THREE.LinearFilter;
    tex.magFilter = THREE.LinearFilter;
    tex.generateMipmaps = false;

    canvasRef.current = cvs;
    textureRef.current = tex;
    return { canvas: cvs, texture: tex };
  }, [bounds.W, bounds.H]);

  // Preload SLAM map image
  useEffect(() => {
    if (!slamMap?.image_url) {
      imgRef.current = null;
      return;
    }
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.src = slamMap.image_url;
    img.onload = () => {
      imgRef.current = img;
      setTick((t) => t + 1);
    };
  }, [slamMap?.image_url]);

  // Re-render floor canvas whenever inputs update
  useEffect(() => {
    renderFloorToCanvas({
      canvas,
      level,
      slamMap,
      slamImage: imgRef.current,
      waypoints,
      lanes,
      robots,
      graphs,
      selectedWaypointIds,
      bounds,
    });
    texture.needsUpdate = true;
  }, [canvas, texture, level, slamMap, waypoints, lanes, robots, graphs, selectedWaypointIds, bounds]);

  // Dispose texture on unmount
  useEffect(() => {
    return () => {
      texture.dispose();
    };
  }, [texture]);

  // Building frame center & inverse rotation
  const bCenter = useMemo(
    () => toBuildingCoords(level.transform, bounds.cx, bounds.cy),
    [level.transform, bounds.cx, bounds.cy]
  );
  const t = useMemo(() => normalizeTransform(level.transform), [level.transform]);

  // Click on floor to dispatch or select waypoint
  const handleFloorClick = useCallback(
    (e: ThreeEvent<MouseEvent>) => {
      if (!e.uv) return;
      // Convert UV [0, 1] back to local floor coords
      // UV x=0 is minX, x=1 is maxX
      // UV y=0 is minY, y=1 is maxY
      const localX = bounds.minX + e.uv.x * bounds.W;
      const localY = bounds.minY + e.uv.y * bounds.H;

      let nearestWp: Waypoint | null = null;
      let minDist = 0.8; // 80cm click radius
      for (const wp of waypoints) {
        const d = Math.hypot(wp.x - localX, wp.y - localY);
        if (d < minDist) {
          minDist = d;
          nearestWp = wp;
        }
      }

      if (nearestWp) {
        e.stopPropagation();
        onSelectWaypoint?.(nearestWp.id);
        onWaypointClickInMonitor?.(nearestWp);
      }
    },
    [bounds, waypoints, onSelectWaypoint, onWaypointClickInMonitor]
  );

  return (
    <group
      position={[bCenter.x, bCenter.y, elevation]}
      rotation={[0, 0, -t.rotation]}
      scale={[1 / t.scale, 1 / t.scale, 1]}
    >
      <mesh onClick={handleFloorClick}>
        <planeGeometry args={[bounds.W, bounds.H]} />
        <meshBasicMaterial
          map={texture}
          transparent
          opacity={floorOpacity}
          depthWrite={false}
          side={THREE.DoubleSide}
        />
      </mesh>

      {/* Floating Floor Label Badge in Corner */}
      <Html position={[-bounds.W / 2 + 1.2, bounds.H / 2 - 1.2, 0.15]} center={false}>
        <div
          onClick={(e) => {
            e.stopPropagation();
            onFocusFloor?.(level.name);
          }}
          className="cursor-pointer select-none px-2.5 py-1 rounded-lg bg-slate-900/95 border border-cyan-500/50 text-cyan-300 text-xs font-bold shadow-xl backdrop-blur hover:bg-slate-800 hover:border-cyan-400 transition-all flex items-center gap-1.5 whitespace-nowrap"
          title={`點擊聚焦至 ${level.name} 樓層`}
        >
          <span className="text-cyan-400">🏢 {level.name}</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-950 text-cyan-300 border border-cyan-800/60 font-mono">
            {robots.length} 台車
          </span>
          <span className="text-[10px] text-slate-400 font-mono">
            {waypoints.length} 點
          </span>
        </div>
      </Html>

      {/* 3D Waypoints (Floating Billboard Name Badges + 3D Node Stem) */}
      {waypoints.map((wp) => {
        const isSelected = Boolean(selectedWaypointIds?.includes(wp.id));
        const wpGraphIndices = getWaypointGraphIndices(wp, lanes);
        const primaryGraphIdx = wpGraphIndices[0] ?? (wp.graph_idx ?? 0);
        const graphDef = graphs?.find((g) => g.id === primaryGraphIdx);
        const graphColor = graphDef ? graphDef.color : '#38bdf8';

        return (
          <WaypointNode3D
            key={wp.id}
            waypoint={wp}
            bounds={bounds}
            isSelected={isSelected}
            graphColor={graphColor}
            onSelect={onSelectWaypoint}
            onClickInMonitor={onWaypointClickInMonitor}
          />
        );
      })}

      {/* 2D AMR Robots with Upright Floating Billboard Status Badges */}
      {robots.map((robot) => (
        <RobotBadge3D
          key={robot.id}
          robot={robot}
          bounds={bounds}
        />
      ))}
    </group>
  );
};

// -------------------------------------------------------------
// Component: LiftStructure3D (Shaft + Moving Cabin + Dynamic Door)
// -------------------------------------------------------------
interface LiftStructure3DProps {
  lift: MapLift;
  elevations: Record<string, number>;
  levels: MapLevel[];
  liftState?: LiftRuntimeState;
  robots?: Robot[];
}

const LiftStructure3D: React.FC<LiftStructure3DProps> = ({
  lift,
  elevations,
  levels,
  liftState,
  robots = [],
}) => {
  const cabinRef = useRef<THREE.Group | null>(null);
  const leftDoorRef = useRef<THREE.Mesh | null>(null);
  const rightDoorRef = useRef<THREE.Mesh | null>(null);

  const width = lift.width || 2.2;
  const depth = lift.depth || 2.2;
  const cabinHeight = 2.1;

  // Shaft vertical span
  const allElevations = useMemo(() => {
    const vals = Object.values(elevations);
    return vals.length > 0 ? vals : [0];
  }, [elevations]);

  const minZ = Math.min(...allElevations) - 0.2;
  const maxZ = Math.max(...allElevations) + 2.5;
  const shaftHeight = Math.max(3.0, maxZ - minZ);
  const shaftCenterZ = (minZ + maxZ) / 2;

  // Initial cabin Z position
  const initialZ = useMemo(() => {
    const curFloor = liftState?.current_floor || levels[0]?.name || '1F';
    return elevations[curFloor] ?? 0;
  }, [liftState?.current_floor, levels, elevations]);

  // Cabin status colors
  const statusColor = useMemo(() => {
    if (!liftState) return '#818cf8';
    if (liftState.current_mode === 'offline' || liftState.current_mode === 'emergency') return '#ef4444';
    if (liftState.motion_state === 'up' || liftState.motion_state === 'down') return '#38bdf8';
    if (liftState.door_state === 'open') return '#10b981';
    return '#818cf8';
  }, [liftState]);

  // Check if any robot is riding inside this lift
  const passengerRobot = useMemo(() => {
    return robots.find((r) => {
      if (liftState?.session_id && liftState.session_id === r.name) return true;
      const dist = Math.hypot(r.x - lift.x, r.y - lift.y);
      return dist < 1.1;
    });
  }, [robots, lift.x, lift.y, liftState?.session_id]);

  // Animation frame loop: interpolate cabin Z and door slide
  useFrame((_, delta) => {
    if (!cabinRef.current) return;

    // 1. Calculate Target Z
    let targetZ = initialZ;
    if (liftState) {
      const curZ = elevations[liftState.current_floor] ?? 0;
      const destZ = elevations[liftState.destination_floor] ?? curZ;

      if (liftState.motion_state === 'up' || liftState.motion_state === 'down') {
        targetZ = destZ;
      } else {
        targetZ = curZ;
      }
    } else if (passengerRobot) {
      // Fallback: follow passenger robot's floor
      const robotFloor = passengerRobot.level_name || '1F';
      targetZ = elevations[robotFloor] ?? 0;
    }

    // Smooth Lerp Z
    const currentZ = cabinRef.current.position.z;
    cabinRef.current.position.z = THREE.MathUtils.lerp(currentZ, targetZ, delta * 2.2);

    // 2. Sliding Door Animation
    const isDoorOpen = liftState?.door_state === 'open';
    const doorOpenOffset = isDoorOpen ? width * 0.22 : 0.0;

    if (leftDoorRef.current) {
      const targetLeftX = -width * 0.23 - doorOpenOffset;
      leftDoorRef.current.position.x = THREE.MathUtils.lerp(
        leftDoorRef.current.position.x,
        targetLeftX,
        delta * 4.0
      );
    }

    if (rightDoorRef.current) {
      const targetRightX = width * 0.23 + doorOpenOffset;
      rightDoorRef.current.position.x = THREE.MathUtils.lerp(
        rightDoorRef.current.position.x,
        targetRightX,
        delta * 4.0
      );
    }
  });

  return (
    <group position={[lift.x, lift.y, 0]} rotation={[0, 0, lift.yaw || 0]}>
      {/* 1. 3D Shaft Translucent Column */}
      <mesh position={[0, 0, shaftCenterZ]}>
        <boxGeometry args={[width * 1.05, depth * 1.05, shaftHeight]} />
        <meshBasicMaterial
          color="#6366f1"
          transparent
          opacity={0.06}
          depthWrite={false}
          side={THREE.DoubleSide}
        />
        <Edges color="#818cf8" threshold={15} />
      </mesh>

      {/* 2. Floor Landing Door Frames at Each Floor Elevation */}
      {levels.map((lvl) => {
        const floorZ = elevations[lvl.name] ?? 0;
        return (
          <group key={lvl.name} position={[0, 0, floorZ]}>
            {/* Landing frame ring */}
            <mesh position={[0, 0, 0.05]}>
              <ringGeometry args={[Math.min(width, depth) * 0.45, Math.max(width, depth) * 0.65, 4]} />
              <meshBasicMaterial color="#38bdf8" transparent opacity={0.35} side={THREE.DoubleSide} />
            </mesh>
            {/* Landing entrance portal */}
            <mesh position={[0, -depth * 0.52, 1.0]}>
              <boxGeometry args={[width * 0.9, 0.08, 2.0]} />
              <meshBasicMaterial color="#818cf8" transparent opacity={0.25} />
              <Edges color="#c7d2fe" />
            </mesh>
          </group>
        );
      })}

      {/* 3. 3D Cabin (Elevator Body) */}
      <group ref={cabinRef} position={[0, 0, initialZ]}>
        {/* Cabin Base Plate */}
        <mesh position={[0, 0, 0.06]}>
          <boxGeometry args={[width * 0.94, depth * 0.94, 0.12]} />
          <meshStandardMaterial color="#1e1b4b" metalness={0.7} roughness={0.3} />
        </mesh>

        {/* Cabin Roof */}
        <mesh position={[0, 0, cabinHeight]}>
          <boxGeometry args={[width * 0.94, depth * 0.94, 0.1]} />
          <meshStandardMaterial color="#1e1b4b" metalness={0.7} roughness={0.3} />
        </mesh>

        {/* Cabin Glass Walls */}
        <mesh position={[0, 0, cabinHeight / 2]}>
          <boxGeometry args={[width * 0.92, depth * 0.92, cabinHeight]} />
          <meshStandardMaterial
            color="#312e81"
            transparent
            opacity={0.28}
            depthWrite={false}
            metalness={0.5}
            roughness={0.2}
          />
          <Edges color={statusColor} threshold={15} />
        </mesh>

        {/* Left Sliding Door */}
        <mesh ref={leftDoorRef} position={[-width * 0.23, -depth * 0.46, cabinHeight / 2]}>
          <boxGeometry args={[width * 0.44, 0.04, cabinHeight * 0.94]} />
          <meshStandardMaterial color="#4f46e5" transparent opacity={0.75} />
          <Edges color="#a5b4fc" />
        </mesh>

        {/* Right Sliding Door */}
        <mesh ref={rightDoorRef} position={[width * 0.23, -depth * 0.46, cabinHeight / 2]}>
          <boxGeometry args={[width * 0.44, 0.04, cabinHeight * 0.94]} />
          <meshStandardMaterial color="#4f46e5" transparent opacity={0.75} />
          <Edges color="#a5b4fc" />
        </mesh>

        {/* Passenger AMR Robot inside cabin if present */}
        {passengerRobot && (
          <mesh position={[0, 0, 0.25]} rotation={[0, 0, -passengerRobot.yaw]}>
            <cylinderGeometry args={[0.35, 0.35, 0.2, 16]} />
            <meshStandardMaterial color="#06b6d4" emissive="#0891b2" emissiveIntensity={0.5} />
          </mesh>
        )}

        {/* 4. 3D Floating Status Badge */}
        <Html position={[0, 0, cabinHeight + 0.6]} center distanceFactor={28}>
          <div className="select-none px-2.5 py-1.5 rounded-xl bg-slate-950/90 border border-indigo-500/60 text-slate-100 shadow-2xl backdrop-blur flex flex-col items-center gap-1 min-w-[130px]">
            <div className="flex items-center gap-1.5 text-xs font-bold">
              <span className="text-indigo-400">🛗 {lift.name}</span>
              <span
                className={`text-[10px] px-1 py-0.2 rounded font-mono ${
                  liftState?.motion_state === 'up'
                    ? 'bg-cyan-950 text-cyan-300 animate-pulse'
                    : liftState?.motion_state === 'down'
                    ? 'bg-amber-950 text-amber-300 animate-pulse'
                    : 'bg-slate-800 text-slate-400'
                }`}
              >
                {liftState?.motion_state === 'up'
                  ? '▲ 升降中'
                  : liftState?.motion_state === 'down'
                  ? '▼ 下降中'
                  : '⏸ 靜止'}
              </span>
            </div>

            <div className="flex items-center gap-1 text-[11px] font-mono text-slate-300">
              <span className="font-bold text-cyan-300">
                {liftState?.current_floor || levels[0]?.name || '1F'}
              </span>
              <span className="text-slate-500">➔</span>
              <span className="font-bold text-emerald-300">
                {liftState?.destination_floor || liftState?.current_floor || levels[0]?.name || '1F'}
              </span>
            </div>

            <div className="flex items-center gap-1 text-[9.5px]">
              <span
                className={`px-1.5 py-0.2 rounded-full ${
                  liftState?.door_state === 'open'
                    ? 'bg-emerald-950 text-emerald-300 border border-emerald-700/60'
                    : liftState?.door_state === 'moving'
                    ? 'bg-amber-950 text-amber-300 border border-amber-700/60'
                    : 'bg-slate-800 text-slate-400'
                }`}
              >
                門：{liftState?.door_state === 'open' ? '開門' : liftState?.door_state === 'moving' ? '動作中' : '關閉'}
              </span>
              {passengerRobot && (
                <span className="px-1.5 py-0.2 rounded-full bg-cyan-950 text-cyan-300 border border-cyan-700/60 font-semibold">
                  搭乘中
                </span>
              )}
            </div>
          </div>
        </Html>
      </group>
    </group>
  );
};

// -------------------------------------------------------------
// Component: CameraRig (Camera controller & floor focusing)
// -------------------------------------------------------------
interface CameraRigProps {
  elevations: Record<string, number>;
  activeLevel?: string;
  focusFloorSignal?: string | null;
}

const CameraRig: React.FC<CameraRigProps> = ({ elevations, activeLevel, focusFloorSignal }) => {
  const { camera } = useThree();
  const controlsRef = useRef<any>(null);

  // Focus camera target on selected floor
  const targetZ = useMemo(() => {
    const floor = focusFloorSignal || activeLevel;
    if (floor && typeof elevations[floor] === 'number') {
      return elevations[floor];
    }
    return 0;
  }, [focusFloorSignal, activeLevel, elevations]);

  useEffect(() => {
    if (controlsRef.current) {
      // Smoothly update target Z
      const currentTarget = controlsRef.current.target;
      currentTarget.z = targetZ;
      controlsRef.current.update();
    }
  }, [targetZ]);

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      enableDamping
      dampingFactor={0.08}
      maxPolarAngle={Math.PI * 0.48}
      minDistance={6}
      maxDistance={120}
    />
  );
};

// -------------------------------------------------------------
// Main Component: Map3DCanvas
// -------------------------------------------------------------
export const Map3DCanvas: React.FC<Map3DCanvasProps> = ({
  levels,
  activeLevel,
  floorSlamMaps,
  waypoints,
  lanes,
  robots,
  lifts = [],
  liftStates = {},
  graphs,
  selectedWaypointIds = [],
  onSelectWaypoint,
  onWaypointClickInMonitor,
  onFocusFloor,
}) => {
  // Visual adjustment parameters
  const [floorSpacing, setFloorSpacing] = useState<number>(12); // meters
  const [floorOpacity, setFloorOpacity] = useState<number>(0.92);
  const [focusSignal, setFocusSignal] = useState<string | null>(null);

  // Compute floor Z elevations dynamically based on logical floor order & spacing
  const elevations = useMemo(() => {
    return resolveLevelElevations(levels, floorSpacing);
  }, [levels, floorSpacing]);

  // Project robots to each floor's local frame
  const floorRobotsMap = useMemo(() => {
    const map: Record<string, Robot[]> = {};
    levels.forEach((lvl) => {
      const transform = lvl.transform;
      const rot = normalizeTransform(transform).rotation;

      map[lvl.name] = robots
        .filter((r) => {
          const rLvl = r.level_name || '1F';
          return (
            rLvl === lvl.name ||
            (rLvl === 'L1' && lvl.name === '1F') ||
            (rLvl === '1F' && lvl.name === 'L1')
          );
        })
        .map((r) => {
          const localPos = toRobotCoords(transform, r.x, r.y);
          return {
            ...r,
            x: localPos.x,
            y: localPos.y,
            yaw: r.yaw + rot,
            path: r.path
              ?.filter((pt) => !pt.level_name || pt.level_name === r.level_name || pt.level_name === lvl.name)
              .map((pt) => {
                const ptLocal = toRobotCoords(transform, pt.x, pt.y);
                return { ...pt, x: ptLocal.x, y: ptLocal.y };
              }),
          };
        });
    });
    return map;
  }, [levels, robots]);

  // Group waypoints and lanes by floor
  const floorWaypointsMap = useMemo(() => {
    const map: Record<string, Waypoint[]> = {};
    levels.forEach((lvl) => {
      map[lvl.name] = waypoints.filter((w) => waypointLevel(w, levels) === lvl.name);
    });
    return map;
  }, [levels, waypoints]);

  const floorLanesMap = useMemo(() => {
    const map: Record<string, Lane[]> = {};
    levels.forEach((lvl) => {
      const wpsOnFloor = new Set((floorWaypointsMap[lvl.name] || []).map((w) => w.id));
      map[lvl.name] = lanes.filter((l) => wpsOnFloor.has(l.start_id) && wpsOnFloor.has(l.end_id));
    });
    return map;
  }, [levels, lanes, floorWaypointsMap]);

  const handleFocusFloorInternal = (floorName: string) => {
    setFocusSignal(floorName);
    onFocusFloor?.(floorName);
  };

  // Only render 3D elevator shafts if at least one 'lift' waypoint references the lift
  const activeLifts = useMemo(() => {
    const liftWps = waypoints.filter((w) => w.type === 'lift' || w.lift);
    if (liftWps.length === 0) return [];

    const liftNamesInWaypoints = Array.from(
      new Set(
        liftWps.map((w) => w.lift || (w.name.startsWith('lift_') ? w.name.split('_').slice(0, 2).join('_') : 'lift_1'))
      )
    );

    return liftNamesInWaypoints.map((liftName) => {
      const existing = (lifts || []).find((l) => l.name === liftName);
      // Find the primary waypoint for this lift (prefer 1F or first available)
      const wpsForThisLift = liftWps.filter((w) => w.lift === liftName || w.name.startsWith(liftName));
      const primaryWp = wpsForThisLift.find((w) => waypointLevel(w, levels) === '1F') || wpsForThisLift[0];
      const lvl = levels.find((l) => l.name === waypointLevel(primaryWp, levels));
      const bCoord = toBuildingCoords(lvl?.transform, primaryWp.x, primaryWp.y);

      // Automatically align elevator doors with the connected corridor / entrance lane
      const laneYaw = computeLiftYawFromLanes(liftName, waypoints, lanes, levels);
      const yaw = laneYaw !== null ? laneYaw : (existing?.yaw ?? 0);

      return {
        name: liftName,
        x: existing?.x ?? bCoord.x,
        y: existing?.y ?? bCoord.y,
        yaw,
        width: existing?.width ?? 2.0,
        depth: existing?.depth ?? 2.0,
      };
    });
  }, [lifts, waypoints, lanes, levels]);

  return (
    <div className="relative w-full h-full bg-[#070b14] overflow-hidden select-none">
      {/* 3D WebGL Canvas */}
      <Canvas
        camera={{ position: [0, -34, 28], up: [0, 0, 1], fov: 45 }}
        gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
      >
        {/* Lights */}
        <ambientLight intensity={1.4} />
        <directionalLight position={[10, -20, 30]} intensity={1.2} />
        <directionalLight position={[-15, 20, 20]} intensity={0.6} />

        {/* Camera Rig & Orbit Controls */}
        <CameraRig
          elevations={elevations}
          activeLevel={activeLevel}
          focusFloorSignal={focusSignal}
        />

        {/* Floor Planes */}
        {levels.map((lvl) => (
          <FloorLevelPlane
            key={lvl.name}
            level={lvl}
            elevation={elevations[lvl.name] ?? 0}
            slamMap={floorSlamMaps[lvl.name]}
            waypoints={floorWaypointsMap[lvl.name] || []}
            lanes={floorLanesMap[lvl.name] || []}
            robots={floorRobotsMap[lvl.name] || []}
            graphs={graphs}
            selectedWaypointIds={selectedWaypointIds}
            floorOpacity={floorOpacity}
            onSelectWaypoint={onSelectWaypoint}
            onWaypointClickInMonitor={onWaypointClickInMonitor}
            onFocusFloor={handleFocusFloorInternal}
          />
        ))}

        {/* 3D Lifts (Shaft + Moving Cabin + Telemetry) */}
        {activeLifts.map((lift) => (
          <LiftStructure3D
            key={lift.name}
            lift={lift}
            elevations={elevations}
            levels={levels}
            liftState={liftStates[lift.name]}
            robots={robots}
          />
        ))}
      </Canvas>

      {/* Floating 3D HUD & Viewport Controls (Top-Right) */}
      <div className="absolute top-4 right-4 z-20 flex flex-col items-end gap-2 pointer-events-auto">
        {/* View Controls & Floor Spacing Box */}
        <div className="bg-[#0b101d]/90 backdrop-blur border border-slate-800 p-2.5 rounded-xl shadow-2xl flex flex-col gap-2 min-w-[210px]">
          <div className="flex items-center justify-between text-xs font-bold text-slate-200 border-b border-slate-800/80 pb-1.5">
            <span className="flex items-center gap-1.5 text-cyan-400">
              <Box className="w-3.5 h-3.5" />
              3D 多樓層監控
            </span>
            <span className="text-[10px] text-slate-500 font-mono">
              {levels.length} 層樓 · {robots.length} 車
            </span>
          </div>

          {/* Floor Spacing Slider */}
          <div className="flex flex-col gap-1 text-[11px]">
            <div className="flex justify-between text-slate-300">
              <span>樓層 Z 間距</span>
              <span className="font-mono text-cyan-400 font-bold">{floorSpacing} m</span>
            </div>
            <input
              type="range"
              min="5"
              max="30"
              step="1"
              value={floorSpacing}
              onChange={(e) => setFloorSpacing(Number(e.target.value))}
              className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
            />
          </div>

          {/* Floor Opacity Slider */}
          <div className="flex flex-col gap-1 text-[11px]">
            <div className="flex justify-between text-slate-300">
              <span>地圖透明度</span>
              <span className="font-mono text-cyan-400 font-bold">
                {Math.round(floorOpacity * 100)}%
              </span>
            </div>
            <input
              type="range"
              min="0.2"
              max="1.0"
              step="0.05"
              value={floorOpacity}
              onChange={(e) => setFloorOpacity(Number(e.target.value))}
              className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
            />
          </div>

          {/* Quick Floor Focus Buttons */}
          <div className="flex flex-wrap items-center gap-1 pt-1 border-t border-slate-800/80">
            <span className="text-[10px] text-slate-400 mr-1">聚焦：</span>
            {levels.map((l) => (
              <button
                key={l.name}
                onClick={() => handleFocusFloorInternal(l.name)}
                className={`px-2 py-0.5 rounded text-[10px] font-bold transition-all ${
                  (focusSignal || activeLevel) === l.name
                    ? 'bg-cyan-500 text-white shadow'
                    : 'bg-slate-800/80 text-slate-300 hover:bg-slate-700'
                }`}
              >
                {l.name}
              </button>
            ))}
          </div>
        </div>

        {/* Lift Telemetry Quick Pills if active lifts exist */}
        {activeLifts.length > 0 && (
          <div className="bg-[#0b101d]/90 backdrop-blur border border-slate-800 p-2 rounded-xl shadow-xl flex flex-col gap-1.5 min-w-[210px]">
            <div className="text-[11px] font-semibold text-indigo-300 flex items-center justify-between">
              <span>跨樓層電梯動態</span>
              <span className="text-[10px] font-mono text-indigo-400 bg-indigo-950/60 px-1 rounded">
                {activeLifts.length} 部
              </span>
            </div>
            <div className="flex flex-col gap-1">
              {activeLifts.map((l) => {
                const st = liftStates[l.name];
                return (
                  <div
                    key={l.name}
                    className="flex items-center justify-between text-[10px] bg-slate-900/60 p-1.5 rounded-lg border border-slate-800/60"
                  >
                    <span className="font-bold text-slate-200">🛗 {l.name}</span>
                    <span className="font-mono text-cyan-300 font-semibold">
                      {st?.current_floor || '1F'} ➔ {st?.destination_floor || st?.current_floor || '1F'}
                    </span>
                    <span
                      className={`px-1 py-0.2 rounded text-[9px] ${
                        st?.motion_state === 'up'
                          ? 'bg-cyan-950 text-cyan-300 font-bold'
                          : st?.motion_state === 'down'
                          ? 'bg-amber-950 text-amber-300 font-bold'
                          : 'bg-slate-800 text-slate-400'
                      }`}
                    >
                      {st?.motion_state === 'up' ? '▲' : st?.motion_state === 'down' ? '▼' : '靜止'}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Floating Bottom-Left Interaction Helper */}
      <div className="absolute bottom-4 left-4 z-20 pointer-events-none">
        <div className="bg-slate-950/80 backdrop-blur border border-slate-800/80 px-3 py-1.5 rounded-xl shadow-xl flex items-center gap-2 text-slate-400 text-xs">
          <span className="font-semibold text-cyan-400">💡 3D 操作提示</span>
          <span className="text-slate-600">|</span>
          <span>按住左鍵旋轉 · 右鍵平移 · 滾輪縮放 · 點擊站點可派單</span>
        </div>
      </div>
    </div>
  );
};
