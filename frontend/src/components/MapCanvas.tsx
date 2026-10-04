import React, { useRef, useEffect, useState, useCallback } from 'react';
import { Waypoint, Lane, Robot, WaypointType, SlamMap } from '@/types/rmf';

export type EditorTool = 'select' | 'waypoint' | 'lane' | 'charger' | 'parking' | 'workcell';

interface MapCanvasProps {
  mode: 'edit' | 'monitor';
  tool: EditorTool;
  waypoints: Waypoint[];
  lanes: Lane[];
  robots: Robot[];
  slamMap?: SlamMap | null;
  selectedWaypointIds: string[];
  selectedLaneIds: string[];
  onSelectWaypoint: (id: string | null) => void;
  onSelectMultiple: (wpIds: string[], laneIds: string[]) => void;
  onAddWaypoint: (x: number, y: number, type: WaypointType) => void;
  onMoveWaypoint: (id: string, x: number, y: number) => void;
  onAddLane: (startId: string, endId: string) => void;
  onWaypointClickInMonitor?: (waypoint: Waypoint) => void;
}

// Distance from point (px, py) to line segment (x1, y1)-(x2, y2)
function distToSegment(px: number, py: number, x1: number, y1: number, x2: number, y2: number) {
  const l2 = (x2 - x1) ** 2 + (y2 - y1) ** 2;
  if (l2 === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * (x2 - x1)), py - (y1 + t * (y2 - y1)));
}

// Configuration for waypoint placement tools (label, colors, etc.)
const PLACEMENT_TOOL_CONFIG: Record<
  string,
  { label: string; color: string; border: string; bg: string; dotColor: string }
> = {
  waypoint: {
    label: '一般導航點',
    color: 'text-cyan-400',
    border: 'border-cyan-400',
    bg: 'bg-cyan-500/20',
    dotColor: '#38bdf8',
  },
  charger: {
    label: '充電樁點位',
    color: 'text-amber-400',
    border: 'border-amber-400',
    bg: 'bg-amber-500/20',
    dotColor: '#f59e0b',
  },
  parking: {
    label: '停車等候點',
    color: 'text-purple-400',
    border: 'border-purple-400',
    bg: 'bg-purple-500/20',
    dotColor: '#a855f7',
  },
  workcell: {
    label: '工作站點',
    color: 'text-emerald-400',
    border: 'border-emerald-400',
    bg: 'bg-emerald-500/20',
    dotColor: '#10b981',
  },
};

export const MapCanvas: React.FC<MapCanvasProps> = ({
  mode,
  tool,
  waypoints,
  lanes,
  robots,
  slamMap,
  selectedWaypointIds,
  selectedLaneIds,
  onSelectWaypoint,
  onSelectMultiple,
  onAddWaypoint,
  onMoveWaypoint,
  onAddLane,
  onWaypointClickInMonitor,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Viewport transforms (pan and zoom)
  const [offset, setOffset] = useState<{ x: number; y: number }>(() => {
    if (typeof window !== 'undefined') {
      return {
        x: Math.round(window.innerWidth / 2),
        y: Math.round(window.innerHeight / 2),
      };
    }
    return { x: 500, y: 400 };
  });
  const [scale, setScale] = useState<number>(30); // 30 pixels per meter
  const [isPanning, setIsPanning] = useState<boolean>(false);
  const [panStart, setPanStart] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  // Center (0,0) coordinate to the middle of the canvas
  const centerOrigin = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    const width = parent?.clientWidth || canvas.clientWidth || window.innerWidth;
    const height = parent?.clientHeight || canvas.clientHeight || window.innerHeight;
    if (width > 0 && height > 0) {
      setOffset({
        x: Math.round(width / 2),
        y: Math.round(height / 2),
      });
    }
  }, []);

  // Auto-center (0,0) coordinate whenever entering the page or switching mode
  useEffect(() => {
    centerOrigin();

    // In case layout/sidebar takes a tick or CSS transitions
    const timer = setTimeout(centerOrigin, 50);

    const parent = canvasRef.current?.parentElement;
    let observer: ResizeObserver | null = null;
    let hasCentered = false;

    if (parent && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const { width, height } = entry.contentRect;
          if (width > 0 && height > 0 && !hasCentered) {
            hasCentered = true;
            setOffset({
              x: Math.round(width / 2),
              y: Math.round(height / 2),
            });
          }
        }
      });
      observer.observe(parent);
    }

    return () => {
      clearTimeout(timer);
      observer?.disconnect();
    };
  }, [mode, centerOrigin]);

  // Mouse hover position and world coordinates
  const [hoverPos, setHoverPos] = useState<{
    screenX: number;
    screenY: number;
    worldX: number;
    worldY: number;
  } | null>(null);

  // Lane creation state
  const [laneStartId, setLaneStartId] = useState<string | null>(null);

  // Dragging waypoint
  const [draggingWaypointId, setDraggingWaypointId] = useState<string | null>(null);

  // Marquee Box Selection state
  const [marqueeBox, setMarqueeBox] = useState<{
    startX: number;
    startY: number;
    currentX: number;
    currentY: number;
  } | null>(null);

  // SLAM Map image loader
  const [mapImageElement, setMapImageElement] = useState<HTMLImageElement | null>(null);

  useEffect(() => {
    if (!slamMap?.image_url) {
      setMapImageElement(null);
      return;
    }
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.src = slamMap.image_url;
    img.onload = () => setMapImageElement(img);
  }, [slamMap?.image_url]);

  // Screen to World coordinates (pixels to meters)
  const screenToWorld = useCallback(
    (screenX: number, screenY: number) => {
      return {
        x: (screenX - offset.x) / scale,
        y: -(screenY - offset.y) / scale, // invert Y for standard Cartesian robotics frame
      };
    },
    [offset, scale]
  );

  // World to Screen coordinates (meters to pixels)
  const worldToScreen = useCallback(
    (worldX: number, worldY: number) => {
      return {
        x: worldX * scale + offset.x,
        y: -worldY * scale + offset.y,
      };
    },
    [offset, scale]
  );

  // Redraw canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Handle high DPI
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.parentElement?.clientWidth || 800;
    const height = canvas.parentElement?.clientHeight || 600;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    // Clear background
    ctx.fillStyle = '#090d16'; // Deep space dark background
    ctx.fillRect(0, 0, width, height);

    // 1. Draw Grid lines (1 meter grid)
    ctx.save();
    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    const gridSize = scale; // 1 meter = scale pixels
    const startX = offset.x % gridSize;
    const startY = offset.y % gridSize;

    for (let x = startX; x < width; x += gridSize) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
    for (let y = startY; y < height; y += gridSize) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    // Draw Coordinate Origin (0,0)
    ctx.strokeStyle = '#334155';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(offset.x - 20, offset.y);
    ctx.lineTo(offset.x + 20, offset.y);
    ctx.moveTo(offset.x, offset.y - 20);
    ctx.lineTo(offset.x, offset.y + 20);
    ctx.stroke();
    ctx.fillStyle = '#64748b';
    ctx.font = '10px monospace';
    ctx.fillText('(0,0)', offset.x + 4, offset.y + 14);
    ctx.restore();

    // 1.5 Draw SLAM Background Map (if loaded)
    if (slamMap && mapImageElement) {
      const screenLeft = slamMap.origin[0] * scale + offset.x;
      const screenTop = -(slamMap.origin[1] + slamMap.real_height_m) * scale + offset.y;
      const screenWidth = slamMap.real_width_m * scale;
      const screenHeight = slamMap.real_height_m * scale;

      ctx.save();
      ctx.globalAlpha = slamMap.opacity ?? 0.85;
      ctx.imageSmoothingEnabled = false; // preserve crisp laser/occupancy grid lines
      ctx.drawImage(mapImageElement, screenLeft, screenTop, screenWidth, screenHeight);

      // Draw map bounding box outline in dashed cyan
      ctx.strokeStyle = '#0284c7';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(screenLeft, screenTop, screenWidth, screenHeight);
      ctx.restore();
    }

    // 2. Draw Lanes (Edges)
    lanes.forEach((lane) => {
      const p1 = waypoints.find((w) => w.id === lane.start_id);
      const p2 = waypoints.find((w) => w.id === lane.end_id);
      if (!p1 || !p2) return;

      const s1 = worldToScreen(p1.x, p1.y);
      const s2 = worldToScreen(p2.x, p2.y);
      const isSelected = selectedLaneIds.includes(lane.id);

      ctx.save();
      ctx.beginPath();
      ctx.moveTo(s1.x, s1.y);
      ctx.lineTo(s2.x, s2.y);

      // Selected lanes are highlighted in gold/amber with outer glow
      if (isSelected) {
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 5;
        ctx.shadowColor = '#f59e0b';
        ctx.shadowBlur = 8;
      } else {
        ctx.strokeStyle = lane.bidirectional ? '#38bdf8' : '#0ea5e9';
        ctx.lineWidth = 3;
      }

      ctx.setLineDash(lane.bidirectional ? [] : [6, 4]);
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Draw direction arrow for lanes
      const midX = (s1.x + s2.x) / 2;
      const midY = (s1.y + s2.y) / 2;
      const angle = Math.atan2(s2.y - s1.y, s2.x - s1.x);

      ctx.fillStyle = isSelected ? '#f59e0b' : '#38bdf8';
      ctx.beginPath();
      ctx.arc(midX, midY, isSelected ? 4 : 3, 0, Math.PI * 2);
      ctx.fill();

      // Draw arrow
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

    // 3. Draw Waypoints
    waypoints.forEach((wp) => {
      const pos = worldToScreen(wp.x, wp.y);
      const isSelected = selectedWaypointIds.includes(wp.id);
      const isConnecting = wp.id === laneStartId;

      ctx.save();

      // Outer glow if selected or connecting
      if (isSelected || isConnecting) {
        ctx.beginPath();
        ctx.arc(pos.x, pos.y, 17, 0, Math.PI * 2);
        ctx.fillStyle = isConnecting ? 'rgba(234, 179, 8, 0.3)' : 'rgba(245, 158, 11, 0.35)';
        ctx.fill();
        ctx.strokeStyle = isConnecting ? '#eab308' : '#f59e0b';
        ctx.lineWidth = 2.5;
        ctx.stroke();
      }

      // Waypoint circle color based on type
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, 10, 0, Math.PI * 2);
      let fillColor = '#3b82f6'; // normal: blue
      let badge = '';

      if (wp.type === 'charger') {
        fillColor = '#f59e0b'; // amber
        badge = '⚡';
      } else if (wp.type === 'parking') {
        fillColor = '#8b5cf6'; // purple
        badge = 'P';
      } else if (wp.type === 'workcell') {
        fillColor = '#10b981'; // emerald
        badge = 'W';
      }

      ctx.fillStyle = fillColor;
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Waypoint Label / Badge inside
      if (badge) {
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 9px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(badge, pos.x, pos.y);
      }

      // Waypoint Name below
      ctx.fillStyle = isSelected ? '#fbbf24' : '#e2e8f0';
      ctx.font = isSelected ? 'bold 11px sans-serif' : '11px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(wp.name, pos.x, pos.y + 20);

      ctx.restore();
    });

    // 4. In Monitor Mode: Draw Robots and Paths
    if (mode === 'monitor') {
      robots.forEach((robot) => {
        const rPos = worldToScreen(robot.x, robot.y);

        ctx.save();

        // Planned path glowing line
        if (robot.path && robot.path.length > 0) {
          ctx.beginPath();
          ctx.moveTo(rPos.x, rPos.y);
          robot.path.forEach((pt) => {
            const screenPt = worldToScreen(pt.x, pt.y);
            ctx.lineTo(screenPt.x, screenPt.y);
          });
          ctx.strokeStyle = '#06b6d4'; // Cyan
          ctx.lineWidth = 3;
          ctx.shadowColor = '#06b6d4';
          ctx.shadowBlur = 10;
          ctx.stroke();
          ctx.shadowBlur = 0;
        }

        // Robot Body (AMR circular footprint + directional pointer)
        ctx.translate(rPos.x, rPos.y);
        ctx.rotate(-robot.yaw); // Invert yaw due to screen Y inversion

        // Pulsing circle around robot
        ctx.beginPath();
        ctx.arc(0, 0, 18, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(16, 185, 129, 0.2)'; // Emerald glow
        ctx.fill();
        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 2;
        ctx.stroke();

        // Directional Triangle / Head
        ctx.beginPath();
        ctx.moveTo(14, 0);
        ctx.lineTo(-10, -9);
        ctx.lineTo(-6, 0);
        ctx.lineTo(-10, 9);
        ctx.closePath();
        ctx.fillStyle = '#34d399';
        ctx.fill();

        ctx.restore();

        // Robot Badge (Name + Battery Pill)
        ctx.save();
        ctx.translate(rPos.x, rPos.y);
        ctx.font = 'bold 10px sans-serif';
        ctx.textAlign = 'center';

        // Background pill
        const nameText = `${robot.name} (${Math.round(robot.battery)}%)`;
        const textWidth = ctx.measureText(nameText).width;
        ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
        ctx.strokeStyle = robot.battery > 30 ? '#10b981' : '#f43f5e';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(-textWidth / 2 - 6, -34, textWidth + 12, 16, 4);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = '#f8fafc';
        ctx.fillText(nameText, 0, -22);

        ctx.restore();
      });
    }

    // 5. Draw Marquee Selection Box (if dragging box selection)
    if (marqueeBox) {
      const bx = Math.min(marqueeBox.startX, marqueeBox.currentX);
      const by = Math.min(marqueeBox.startY, marqueeBox.currentY);
      const bw = Math.abs(marqueeBox.currentX - marqueeBox.startX);
      const bh = Math.abs(marqueeBox.currentY - marqueeBox.startY);

      ctx.save();
      ctx.fillStyle = 'rgba(56, 189, 248, 0.15)'; // translucent blue/cyan fill
      ctx.fillRect(bx, by, bw, bh);

      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(bx, by, bw, bh);
      ctx.restore();
    }
  }, [
    offset,
    scale,
    waypoints,
    lanes,
    robots,
    slamMap,
    mapImageElement,
    selectedWaypointIds,
    selectedLaneIds,
    laneStartId,
    mode,
    marqueeBox,
    worldToScreen,
  ]);

  // Mouse Handlers
  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const clientX = e.clientX - rect.left;
    const clientY = e.clientY - rect.top;

    // Right click (2) or middle click (1) or Alt/Space = Pan
    if (e.button === 2 || e.button === 1 || e.altKey) {
      setIsPanning(true);
      setPanStart({ x: clientX - offset.x, y: clientY - offset.y });
      return;
    }

    if (e.button === 0) {
      // 1. Check if clicked a waypoint
      const clickedWp = waypoints.find((wp) => {
        const screenPos = worldToScreen(wp.x, wp.y);
        const dist = Math.hypot(screenPos.x - clientX, screenPos.y - clientY);
        return dist <= 14;
      });

      if (mode === 'monitor') {
        if (clickedWp) {
          onSelectWaypoint(clickedWp.id);
          if (onWaypointClickInMonitor) {
            onWaypointClickInMonitor(clickedWp);
          }
        } else {
          // Pan map in monitor mode
          setIsPanning(true);
          setPanStart({ x: clientX - offset.x, y: clientY - offset.y });
        }
        return;
      }

      // 2. Edit Mode: Tool actions
      if (tool === 'lane') {
        if (clickedWp) {
          if (!laneStartId) {
            setLaneStartId(clickedWp.id);
          } else {
            if (laneStartId !== clickedWp.id) {
              onAddLane(laneStartId, clickedWp.id);
            }
            setLaneStartId(null);
          }
        } else {
          setLaneStartId(null);
        }
        return;
      }

      if (['waypoint', 'charger', 'parking', 'workcell'].includes(tool)) {
        const worldCoords = screenToWorld(clientX, clientY);
        const wpType = tool === 'waypoint' ? 'normal' : (tool as WaypointType);
        onAddWaypoint(parseFloat(worldCoords.x.toFixed(2)), parseFloat(worldCoords.y.toFixed(2)), wpType);
        return;
      }

      // 3. Selection tool ('select')

      if (tool === 'select') {
        if (clickedWp) {
          if (e.shiftKey) {
            // Toggle selection with Shift
            const isAlreadySelected = selectedWaypointIds.includes(clickedWp.id);
            const nextWpIds = isAlreadySelected
              ? selectedWaypointIds.filter((id) => id !== clickedWp.id)
              : [...selectedWaypointIds, clickedWp.id];
            onSelectMultiple(nextWpIds, selectedLaneIds);
          } else {
            onSelectWaypoint(clickedWp.id);
            setDraggingWaypointId(clickedWp.id);
          }
          return;
        }

        // Check if clicked a lane
        const clickedLane = lanes.find((l) => {
          const p1 = waypoints.find((w) => w.id === l.start_id);
          const p2 = waypoints.find((w) => w.id === l.end_id);
          if (!p1 || !p2) return false;
          const s1 = worldToScreen(p1.x, p1.y);
          const s2 = worldToScreen(p2.x, p2.y);
          return distToSegment(clientX, clientY, s1.x, s1.y, s2.x, s2.y) <= 8;
        });

        if (clickedLane) {
          if (e.shiftKey) {
            const nextLaneIds = selectedLaneIds.includes(clickedLane.id)
              ? selectedLaneIds.filter((id) => id !== clickedLane.id)
              : [...selectedLaneIds, clickedLane.id];
            onSelectMultiple(selectedWaypointIds, nextLaneIds);
          } else {
            onSelectMultiple([], [clickedLane.id]);
          }
          return;
        }

        // Clicked empty space: start marquee selection box!
        setMarqueeBox({
          startX: clientX,
          startY: clientY,
          currentX: clientX,
          currentY: clientY,
        });
      }
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const clientX = e.clientX - rect.left;
    const clientY = e.clientY - rect.top;

    const world = screenToWorld(clientX, clientY);
    setHoverPos({
      screenX: clientX,
      screenY: clientY,
      worldX: parseFloat(world.x.toFixed(2)),
      worldY: parseFloat(world.y.toFixed(2)),
    });

    if (isPanning) {
      setOffset({
        x: clientX - panStart.x,
        y: clientY - panStart.y,
      });
      return;
    }

    if (marqueeBox) {
      setMarqueeBox((prev) => (prev ? { ...prev, currentX: clientX, currentY: clientY } : null));
      return;
    }

    if (draggingWaypointId && mode === 'edit') {
      onMoveWaypoint(draggingWaypointId, parseFloat(world.x.toFixed(2)), parseFloat(world.y.toFixed(2)));
    }
  };

  const handleMouseLeave = () => {
    setHoverPos(null);
    if (isPanning) {
      setIsPanning(false);
    }
    if (draggingWaypointId) {
      setDraggingWaypointId(null);
    }
    if (marqueeBox) {
      setMarqueeBox(null);
    }
  };

  const handleMouseUp = () => {
    if (isPanning) {
      setIsPanning(false);
    }
    if (draggingWaypointId) {
      setDraggingWaypointId(null);
    }

    // Finalize Marquee Selection Box
    if (marqueeBox) {
      const minX = Math.min(marqueeBox.startX, marqueeBox.currentX);
      const maxX = Math.max(marqueeBox.startX, marqueeBox.currentX);
      const minY = Math.min(marqueeBox.startY, marqueeBox.currentY);
      const maxY = Math.max(marqueeBox.startY, marqueeBox.currentY);

      const boxWidth = maxX - minX;
      const boxHeight = maxY - minY;

      if (boxWidth > 5 || boxHeight > 5) {
        // Find all waypoints inside the box
        const foundWpIds: string[] = [];
        waypoints.forEach((wp) => {
          const screenPos = worldToScreen(wp.x, wp.y);
          if (screenPos.x >= minX && screenPos.x <= maxX && screenPos.y >= minY && screenPos.y <= maxY) {
            foundWpIds.push(wp.id);
          }
        });

        // Find all lanes inside the box or connecting selected waypoints
        const foundLaneIds: string[] = [];
        lanes.forEach((l) => {
          if (foundWpIds.includes(l.start_id) && foundWpIds.includes(l.end_id)) {
            foundLaneIds.push(l.id);
          }
        });

        onSelectMultiple(foundWpIds, foundLaneIds);
      } else {
        // Click without drag on empty space: deselect all
        onSelectMultiple([], []);
      }

      setMarqueeBox(null);
    }
  };

  // Zoom Handler
  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.85;
    const newScale = Math.min(Math.max(scale * zoomFactor, 5), 150);

    // Zoom towards mouse pointer
    const newOffsetX = mouseX - (mouseX - offset.x) * (newScale / scale);
    const newOffsetY = mouseY - (mouseY - offset.y) * (newScale / scale);

    setScale(newScale);
    setOffset({ x: newOffsetX, y: newOffsetY });
  };

  const placementConfig = mode === 'edit' ? PLACEMENT_TOOL_CONFIG[tool] : null;

  return (
    <div className="relative w-full h-full overflow-hidden select-none bg-[#090d16]">
      <canvas
        ref={canvasRef}
        className="w-full h-full cursor-crosshair"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        onWheel={handleWheel}
        onContextMenu={(e) => e.preventDefault()}
      />

      {/* Real-time Cursor Coordinates Tooltip & Ghost Preview when placing waypoints */}
      {placementConfig && hoverPos && !isPanning && !draggingWaypointId && (
        <>
          {/* Subtle crosshair guide lines */}
          <div
            className="pointer-events-none absolute left-0 right-0 h-px border-t border-dashed border-cyan-400/25 z-20"
            style={{ top: hoverPos.screenY }}
          />
          <div
            className="pointer-events-none absolute top-0 bottom-0 w-px border-l border-dashed border-cyan-400/25 z-20"
            style={{ left: hoverPos.screenX }}
          />

          {/* Ghost waypoint circle */}
          <div
            className={`pointer-events-none absolute w-6 h-6 rounded-full border-2 border-dashed ${placementConfig.border} ${placementConfig.bg} -translate-x-1/2 -translate-y-1/2 flex items-center justify-center z-20 shadow-lg`}
            style={{
              left: hoverPos.screenX,
              top: hoverPos.screenY,
              boxShadow: `0 0 12px ${placementConfig.dotColor}66`,
            }}
          >
            <div
              className="w-1.5 h-1.5 rounded-full"
              style={{ backgroundColor: placementConfig.dotColor }}
            />
          </div>

          {/* Floating coordinates badge */}
          <div
            className="pointer-events-none absolute z-30 flex items-center space-x-2 px-2.5 py-1 rounded-md bg-slate-950/95 border shadow-2xl backdrop-blur text-xs font-mono select-none"
            style={{
              left:
                hoverPos.screenX > (canvasRef.current?.clientWidth || 800) - 230
                  ? hoverPos.screenX - 225
                  : hoverPos.screenX + 16,
              top: hoverPos.screenY < 45 ? hoverPos.screenY + 18 : hoverPos.screenY - 34,
              borderColor: `${placementConfig.dotColor}88`,
            }}
          >
            <span className={`text-[10px] font-sans font-bold ${placementConfig.color} flex items-center`}>
              <span
                className="w-1.5 h-1.5 rounded-full mr-1.5 animate-pulse"
                style={{ backgroundColor: placementConfig.dotColor }}
              />
              {placementConfig.label}
            </span>
            <span className="text-slate-600">|</span>
            <span className="text-slate-300">
              X: <strong className="text-white">{hoverPos.worldX >= 0 ? `+${hoverPos.worldX.toFixed(2)}` : hoverPos.worldX.toFixed(2)}</strong>m
            </span>
            <span className="text-slate-300">
              Y: <strong className="text-white">{hoverPos.worldY >= 0 ? `+${hoverPos.worldY.toFixed(2)}` : hoverPos.worldY.toFixed(2)}</strong>m
            </span>
          </div>
        </>
      )}

      {/* Zoom / Pan & Cursor Coordinate indicator overlay */}
      <div className="absolute bottom-4 left-4 bg-slate-900/80 backdrop-blur border border-slate-700/50 px-3 py-1.5 rounded-lg text-xs text-slate-300 flex items-center space-x-3 pointer-events-auto z-10 select-none">
        <span>比例尺: 1m = {Math.round(scale)}px</span>
        {hoverPos && (
          <>
            <span className="text-slate-600">•</span>
            <span className="font-mono text-cyan-300 flex items-center">
              <span className="text-slate-400 mr-1.5">游標坐標:</span>
              X: <strong className="text-white ml-0.5 mr-2">{hoverPos.worldX >= 0 ? `+${hoverPos.worldX.toFixed(2)}` : hoverPos.worldX.toFixed(2)}m</strong>
              Y: <strong className="text-white ml-0.5">{hoverPos.worldY >= 0 ? `+${hoverPos.worldY.toFixed(2)}` : hoverPos.worldY.toFixed(2)}m</strong>
            </span>
          </>
        )}
        <span className="text-slate-600">•</span>
        <span>滑鼠拖曳框選 • 右鍵平移 • 滾輪縮放</span>
        <span className="text-slate-600">•</span>
        <button
          onClick={centerOrigin}
          className="text-cyan-400 hover:text-cyan-300 hover:underline font-medium transition-colors cursor-pointer"
          title="將視圖 (0,0) 原點置中"
        >
          原點置中
        </button>
      </div>
    </div>
  );
};
