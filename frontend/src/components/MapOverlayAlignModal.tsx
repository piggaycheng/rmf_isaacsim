import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import {
  MapLevel,
  LevelTransform,
  normalizeTransform,
  toBuildingCoords,
  toRobotCoords,
  Waypoint,
  MapLift,
  SlamMap,
} from '@/types/rmf';
import {
  Layers,
  RotateCw,
  RotateCcw,
  Move,
  Sparkles,
  Sliders,
  Check,
  Eye,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Crosshair,
  Building2,
  AlertCircle,
  Undo2,
} from 'lucide-react';

interface MapOverlayAlignModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  levels: MapLevel[];
  currentLevelName: string;
  floorSlamMaps: Record<string, SlamMap>;
  waypoints: Waypoint[];
  lifts: MapLift[];
  onSaveLevelTransform: (levelName: string, transform: LevelTransform) => void;
}

export const MapOverlayAlignModal: React.FC<MapOverlayAlignModalProps> = ({
  open,
  onOpenChange,
  levels,
  currentLevelName,
  floorSlamMaps,
  waypoints,
  lifts,
  onSaveLevelTransform,
}) => {
  // Base Level (Reference / Locked) & Target Level (Being aligned)
  const [baseLevelName, setBaseLevelName] = useState<string>(() => {
    return levels[0]?.name || '1F';
  });

  const [targetLevelName, setTargetLevelName] = useState<string>(() => {
    const candidate = levels.find((l) => l.name !== (levels[0]?.name || '1F'));
    return candidate ? candidate.name : currentLevelName;
  });

  // Current alignment transform parameters
  const [tx, setTx] = useState<number>(0);
  const [ty, setTy] = useState<number>(0);
  const [rotDeg, setRotDeg] = useState<number>(0);
  const [scaleTrans, setScaleTrans] = useState<number>(1.0);

  // Initial transform backup for Revert
  const [initialTransform, setInitialTransform] = useState<LevelTransform>({
    translation: [0, 0],
    rotation: 0,
    scale: 1.0,
  });

  // Opacity & Blend Mode
  const [baseOpacity, setBaseOpacity] = useState<number>(0.65);
  const [targetOpacity, setTargetOpacity] = useState<number>(0.75);
  const [blendMode, setBlendMode] = useState<GlobalCompositeOperation>('screen');

  // Canvas Viewport Pan & Zoom
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [zoom, setZoom] = useState<number>(30); // pixels per meter

  // Drag interaction state
  const isDraggingRef = useRef<boolean>(false);
  const dragModeRef = useRef<'pan_viewport' | 'translate_target' | 'rotate_target' | null>(null);
  const lastMousePosRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const initialRotateCenterRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });

  // Map Images
  const [baseImgElement, setBaseImgElement] = useState<HTMLImageElement | null>(null);
  const [targetImgElement, setTargetImgElement] = useState<HTMLImageElement | null>(null);

  const baseSlamMap = floorSlamMaps[baseLevelName];
  const targetSlamMap = floorSlamMaps[targetLevelName];

  // Load Base Map Image
  useEffect(() => {
    if (baseSlamMap?.image_url) {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.src = baseSlamMap.image_url;
      img.onload = () => setBaseImgElement(img);
    } else {
      setBaseImgElement(null);
    }
  }, [baseSlamMap?.image_url]);

  // Load Target Map Image
  useEffect(() => {
    if (targetSlamMap?.image_url) {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.src = targetSlamMap.image_url;
      img.onload = () => setTargetImgElement(img);
    } else {
      setTargetImgElement(null);
    }
  }, [targetSlamMap?.image_url]);

  // Initialize or update transform values when modal opens or target level changes
  useEffect(() => {
    if (open) {
      const targetLvl = levels.find((l) => l.name === targetLevelName);
      if (targetLvl) {
        const norm = normalizeTransform(targetLvl.transform);
        setTx(norm.translation[0]);
        setTy(norm.translation[1]);
        setRotDeg(parseFloat(((norm.rotation * 180) / Math.PI).toFixed(2)));
        setScaleTrans(norm.scale);
        setInitialTransform(norm);
      }
    }
  }, [open, targetLevelName, levels]);

  // Center viewport when opening
  useEffect(() => {
    if (open && canvasRef.current) {
      const rect = canvasRef.current.getBoundingClientRect();
      setPan({ x: Math.round(rect.width / 2), y: Math.round(rect.height / 2) });
    }
  }, [open]);

  const rotRad = useMemo(() => (rotDeg * Math.PI) / 180, [rotDeg]);

  const currentTransform: LevelTransform = useMemo(
    () => ({
      translation: [tx, ty],
      rotation: rotRad,
      scale: scaleTrans,
    }),
    [tx, ty, rotRad, scaleTrans]
  );

  // Helper: World (Building Frame meters) -> Screen pixels
  const worldToScreen = useCallback(
    (wx: number, wy: number) => {
      return {
        x: wx * zoom + pan.x,
        y: -wy * zoom + pan.y,
      };
    },
    [zoom, pan]
  );

  // Helper: Screen pixels -> World (Building Frame meters)
  const screenToWorld = useCallback(
    (sx: number, sy: number) => {
      return {
        x: (sx - pan.x) / zoom,
        y: -(sy - pan.y) / zoom,
      };
    },
    [zoom, pan]
  );

  // Reset to identity (0, 0, 0°)
  const handleReset = () => {
    setTx(0);
    setTy(0);
    setRotDeg(0);
    setScaleTrans(1.0);
  };

  // Revert to initial saved transform
  const handleRevert = () => {
    setTx(initialTransform.translation[0]);
    setTy(initialTransform.translation[1]);
    setRotDeg(parseFloat(((initialTransform.rotation * 180) / Math.PI).toFixed(2)));
    setScaleTrans(initialTransform.scale);
  };

  // Step adjustments
  const adjustTx = (delta: number) => {
    setTx((prev) => parseFloat((prev + delta).toFixed(3)));
  };

  const adjustTy = (delta: number) => {
    setTy((prev) => parseFloat((prev + delta).toFixed(3)));
  };

  const adjustRot = (deltaDeg: number) => {
    setRotDeg((prev) => {
      let next = parseFloat((prev + deltaDeg).toFixed(2));
      while (next > 180) next -= 360;
      while (next < -180) next += 360;
      return next;
    });
  };

  // Auto-snap elevators: aligns elevator waypoints between base and target floor
  const handleSnapElevators = () => {
    const baseLifts = waypoints.filter(
      (w) => (w.level || levels[0]?.name) === baseLevelName && (w.type === 'lift' || w.lift)
    );
    const targetLifts = waypoints.filter(
      (w) => (w.level || levels[0]?.name) === targetLevelName && (w.type === 'lift' || w.lift)
    );

    if (baseLifts.length === 0 || targetLifts.length === 0) {
      alert(`請確保在 [${baseLevelName}] 與 [${targetLevelName}] 均有建立電梯站點，即可一鍵對齊。`);
      return;
    }

    // Try matching by lift name, or pick first
    let bWp = baseLifts[0];
    let tWp = targetLifts[0];
    for (const b of baseLifts) {
      const match = targetLifts.find((t) => t.lift && t.lift === b.lift);
      if (match) {
        bWp = b;
        tWp = match;
        break;
      }
    }

    // Formula: robot = T + R(rot) * scale * building
    // => T_x = robot_x - scale * (c * bldgX - s * bldgY)
    // => T_y = robot_y - scale * (s * bldgX + c * bldgY)
    const c = Math.cos(rotRad);
    const s = Math.sin(rotRad);
    const sc = scaleTrans > 0 ? scaleTrans : 1.0;
    const newTx = tWp.x - sc * (c * bWp.x - s * bWp.y);
    const newTy = tWp.y - sc * (s * bWp.x + c * bWp.y);

    setTx(parseFloat(newTx.toFixed(4)));
    setTy(parseFloat(newTy.toFixed(4)));
  };

  // Save changes
  const handleSave = () => {
    onSaveLevelTransform(targetLevelName, currentTransform);
    onOpenChange(false);
  };

  // ---------------- Canvas Rendering Loop ----------------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;

    // Clear background
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#0a0f1d';
    ctx.fillRect(0, 0, width, height);

    // 1. Grid lines (1 meter grid)
    ctx.save();
    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    const gridSize = zoom;
    const startX = pan.x % gridSize;
    const startY = pan.y % gridSize;
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

    // Origin (0,0) marker
    ctx.strokeStyle = '#334155';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(pan.x - 15, pan.y);
    ctx.lineTo(pan.x + 15, pan.y);
    ctx.moveTo(pan.x, pan.y - 15);
    ctx.lineTo(pan.x, pan.y + 15);
    ctx.stroke();
    ctx.fillStyle = '#64748b';
    ctx.font = '10px monospace';
    ctx.fillText('(0,0)', pan.x + 4, pan.y + 14);
    ctx.restore();

    // 2. Render Base Map (Locked Reference)
    if (baseSlamMap && baseImgElement) {
      const screenLeft = baseSlamMap.origin[0] * zoom + pan.x;
      const screenTop = -(baseSlamMap.origin[1] + baseSlamMap.real_height_m) * zoom + pan.y;
      const screenWidth = baseSlamMap.real_width_m * zoom;
      const screenHeight = baseSlamMap.real_height_m * zoom;

      ctx.save();
      ctx.globalAlpha = baseOpacity;
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(baseImgElement, screenLeft, screenTop, screenWidth, screenHeight);

      // Green dashed border to designate Base Floor
      ctx.strokeStyle = '#10b981';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(screenLeft, screenTop, screenWidth, screenHeight);

      // Label
      ctx.fillStyle = '#34d399';
      ctx.font = 'bold 10px monospace';
      ctx.fillText(`基準層 [${baseLevelName}]`, screenLeft + 6, screenTop + 14);
      ctx.restore();
    }

    // 3. Render Target Map (Interactive Overlay)
    if (targetSlamMap && targetImgElement) {
      const localCenterX = targetSlamMap.origin[0] + targetSlamMap.real_width_m / 2;
      const localCenterY = targetSlamMap.origin[1] + targetSlamMap.real_height_m / 2;
      const bCenter = toBuildingCoords(currentTransform, localCenterX, localCenterY);
      const sCenter = worldToScreen(bCenter.x, bCenter.y);

      const drawW = (targetSlamMap.real_width_m / scaleTrans) * zoom;
      const drawH = (targetSlamMap.real_height_m / scaleTrans) * zoom;

      ctx.save();
      ctx.globalAlpha = targetOpacity;
      if (blendMode !== 'source-over') {
        ctx.globalCompositeOperation = blendMode;
      }

      ctx.translate(sCenter.x, sCenter.y);
      ctx.rotate(rotRad);

      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(targetImgElement, -drawW / 2, -drawH / 2, drawW, drawH);

      // Cyan dashed border for target map
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = '#06b6d4';
      ctx.lineWidth = 1.8;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(-drawW / 2, -drawH / 2, drawW, drawH);

      // Center crosshair of target map
      ctx.strokeStyle = '#22d3ee';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(-12, 0);
      ctx.lineTo(12, 0);
      ctx.moveTo(0, -12);
      ctx.lineTo(0, 12);
      ctx.stroke();

      // Top Rotation Handle & Guide Line
      ctx.beginPath();
      ctx.moveTo(0, -drawH / 2);
      ctx.lineTo(0, -drawH / 2 - 28);
      ctx.strokeStyle = '#06b6d4';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(0, -drawH / 2 - 28, 7, 0, Math.PI * 2);
      ctx.fillStyle = '#0891b2';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Label
      ctx.fillStyle = '#67e8f9';
      ctx.font = 'bold 10px monospace';
      ctx.textAlign = 'center';
      ctx.fillText(`調整層 [${targetLevelName}]`, 0, -drawH / 2 + 16);

      ctx.restore();
    }

    // 4. Render Waypoints on both floors (especially elevator waypoints)
    const baseWps = waypoints.filter((w) => (w.level || levels[0]?.name) === baseLevelName);
    baseWps.forEach((w) => {
      const s = worldToScreen(w.x, w.y);
      ctx.save();
      ctx.beginPath();
      ctx.arc(s.x, s.y, w.type === 'lift' ? 8 : 4, 0, Math.PI * 2);
      ctx.fillStyle = w.type === 'lift' ? '#10b981' : 'rgba(52, 211, 153, 0.4)';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      if (w.type === 'lift') {
        ctx.fillStyle = '#a7f3d0';
        ctx.font = 'bold 9px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(`🛗 ${w.name}`, s.x, s.y - 12);
      }
      ctx.restore();
    });

    const targetWps = waypoints.filter((w) => (w.level || levels[0]?.name) === targetLevelName);
    targetWps.forEach((w) => {
      const b = toBuildingCoords(currentTransform, w.x, w.y);
      const s = worldToScreen(b.x, b.y);
      ctx.save();
      ctx.beginPath();
      ctx.arc(s.x, s.y, w.type === 'lift' ? 8 : 4, 0, Math.PI * 2);
      ctx.fillStyle = w.type === 'lift' ? '#06b6d4' : 'rgba(34, 211, 238, 0.4)';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      if (w.type === 'lift') {
        ctx.fillStyle = '#bae6fd';
        ctx.font = 'bold 9px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(`🛗 ${w.name}`, s.x, s.y + 18);
      }
      ctx.restore();
    });
  }, [
    open,
    zoom,
    pan,
    baseLevelName,
    targetLevelName,
    baseSlamMap,
    targetSlamMap,
    baseImgElement,
    targetImgElement,
    baseOpacity,
    targetOpacity,
    blendMode,
    currentTransform,
    rotRad,
    scaleTrans,
    waypoints,
    levels,
    worldToScreen,
  ]);

  // Handle Resize of canvas to its container
  useEffect(() => {
    const handleResize = () => {
      if (canvasRef.current && canvasRef.current.parentElement) {
        const p = canvasRef.current.parentElement;
        canvasRef.current.width = p.clientWidth;
        canvasRef.current.height = p.clientHeight;
      }
    };
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [open]);

  // Mouse / Wheel Event Handlers
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.87;
    setZoom((prev) => Math.min(150, Math.max(5, prev * zoomFactor)));
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    isDraggingRef.current = true;
    lastMousePosRef.current = { x: mouseX, y: mouseY };

    // Check hit testing with Target Map
    if (targetSlamMap) {
      const localCenterX = targetSlamMap.origin[0] + targetSlamMap.real_width_m / 2;
      const localCenterY = targetSlamMap.origin[1] + targetSlamMap.real_height_m / 2;
      const bCenter = toBuildingCoords(currentTransform, localCenterX, localCenterY);
      const sCenter = worldToScreen(bCenter.x, bCenter.y);
      initialRotateCenterRef.current = sCenter;

      const drawH = (targetSlamMap.real_height_m / scaleTrans) * zoom;
      const drawW = (targetSlamMap.real_width_m / scaleTrans) * zoom;

      // Rotation handle screen position: (0, -drawH / 2 - 28) rotated by rotRad around sCenter
      const handleOffsetDist = drawH / 2 + 28;
      const handleScreenX = sCenter.x + Math.sin(rotRad) * handleOffsetDist;
      const handleScreenY = sCenter.y - Math.cos(rotRad) * handleOffsetDist;

      const distToHandle = Math.hypot(mouseX - handleScreenX, mouseY - handleScreenY);
      if (distToHandle <= 16) {
        dragModeRef.current = 'rotate_target';
        return;
      }

      // Check if mouse is inside target map
      const dx = mouseX - sCenter.x;
      const dy = mouseY - sCenter.y;
      const unrotX = Math.cos(rotRad) * dx + Math.sin(rotRad) * dy;
      const unrotY = -Math.sin(rotRad) * dx + Math.cos(rotRad) * dy;

      if (Math.abs(unrotX) <= drawW / 2 && Math.abs(unrotY) <= drawH / 2) {
        dragModeRef.current = 'translate_target';
        return;
      }
    }

    // Default: Pan Viewport
    dragModeRef.current = 'pan_viewport';
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDraggingRef.current || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const dx = mouseX - lastMousePosRef.current.x;
    const dy = mouseY - lastMousePosRef.current.y;
    lastMousePosRef.current = { x: mouseX, y: mouseY };

    const mode = dragModeRef.current;

    if (mode === 'pan_viewport') {
      setPan((prev) => ({ x: prev.x + dx, y: prev.y + dy }));
    } else if (mode === 'translate_target') {
      // Screen delta to building frame delta
      const dXb = dx / zoom;
      const dYb = -dy / zoom;

      // Formula: robot = T + R(rot) * scale * building
      // => delta_T = -scale * R(rot) * delta_building
      const c = Math.cos(rotRad);
      const s = Math.sin(rotRad);
      const sc = scaleTrans > 0 ? scaleTrans : 1.0;
      const deltaTx = -sc * (c * dXb - s * dYb);
      const deltaTy = -sc * (s * dXb + c * dYb);

      setTx((prev) => parseFloat((prev + deltaTx).toFixed(4)));
      setTy((prev) => parseFloat((prev + deltaTy).toFixed(4)));
    } else if (mode === 'rotate_target' && targetSlamMap) {
      const sCenter = initialRotateCenterRef.current;
      const angle = Math.atan2(mouseX - sCenter.x, -(mouseY - sCenter.y));
      const deg = (angle * 180) / Math.PI;

      // Keep building center fixed while rotating
      const localCenterX = targetSlamMap.origin[0] + targetSlamMap.real_width_m / 2;
      const localCenterY = targetSlamMap.origin[1] + targetSlamMap.real_height_m / 2;
      const bCenter = toBuildingCoords(currentTransform, localCenterX, localCenterY);

      const newRotRad = angle;
      const c = Math.cos(newRotRad);
      const s = Math.sin(newRotRad);
      const sc = scaleTrans > 0 ? scaleTrans : 1.0;

      const newTx = localCenterX - sc * (c * bCenter.x - s * bCenter.y);
      const newTy = localCenterY - sc * (s * bCenter.x + c * bCenter.y);

      setTx(parseFloat(newTx.toFixed(4)));
      setTy(parseFloat(newTy.toFixed(4)));
      setRotDeg(parseFloat(deg.toFixed(2)));
    }
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
    dragModeRef.current = null;
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      className="max-w-7xl w-[95vw] h-[90vh] p-0 bg-[#0a0f1d] border-slate-800 flex flex-col"
    >
      <div className="flex flex-col h-full w-full overflow-hidden text-slate-200">
        {/* Top Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-800 bg-[#0d1424] shrink-0">
          <div className="flex items-center space-x-2.5">
            <div className="p-1.5 rounded-lg bg-cyan-950/80 border border-cyan-800 text-cyan-400">
              <Layers className="w-5 h-5" />
            </div>
            <div>
              <DialogTitle className="text-base font-bold text-white flex items-center gap-2">
                雙樓層地圖疊圖旋轉對齊 (Map Overlay Aligner)
              </DialogTitle>
              <DialogDescription className="text-xs text-slate-400">
                拖曳平移與旋轉調整層地圖，使電梯井或樑柱重合。完成後直接儲存樓層 Transform 與 YAML 設定。
              </DialogDescription>
            </div>
          </div>

          <div className="flex items-center space-x-3 text-xs">
            {/* Quick Floor Selection */}
            <div className="flex items-center space-x-1.5 bg-slate-900 border border-slate-800 px-2.5 py-1 rounded-lg">
              <span className="text-emerald-400 font-medium">基準層 (鎖定):</span>
              <select
                value={baseLevelName}
                onChange={(e) => setBaseLevelName(e.target.value)}
                className="bg-slate-950 border border-slate-800 text-slate-200 rounded px-2 py-0.5 text-xs font-mono"
              >
                {levels.map((lvl) => (
                  <option key={lvl.name} value={lvl.name}>
                    {lvl.name} {floorSlamMaps[lvl.name] ? '✓' : '(未載地圖)'}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex items-center space-x-1.5 bg-slate-900 border border-slate-800 px-2.5 py-1 rounded-lg">
              <span className="text-cyan-400 font-medium">調整層 (移動中):</span>
              <select
                value={targetLevelName}
                onChange={(e) => setTargetLevelName(e.target.value)}
                className="bg-slate-950 border border-slate-800 text-slate-200 rounded px-2 py-0.5 text-xs font-mono"
              >
                {levels
                  .filter((l) => l.name !== baseLevelName)
                  .map((lvl) => (
                    <option key={lvl.name} value={lvl.name}>
                      {lvl.name} {floorSlamMaps[lvl.name] ? '✓' : '(未載地圖)'}
                    </option>
                  ))}
              </select>
            </div>
          </div>
        </div>

        {/* Main Body: Canvas (Left) + Precision Alignment Sidebar (Right) */}
        <div className="flex-1 flex overflow-hidden">
          {/* Canvas Interactive Workspace */}
          <div className="flex-1 relative bg-[#070b14] overflow-hidden select-none">
            <canvas
              ref={canvasRef}
              onWheel={handleWheel}
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onMouseLeave={handleMouseUp}
              className="w-full h-full cursor-crosshair block"
            />

            {/* Floating Overlay Visual Controls */}
            <div className="absolute top-3 left-3 flex items-center space-x-2 bg-slate-950/80 backdrop-blur-md border border-slate-800 px-3 py-1.5 rounded-lg shadow-lg text-xs z-10">
              <span className="text-slate-400 font-medium">混合濾鏡:</span>
              <select
                value={blendMode}
                onChange={(e) => setBlendMode(e.target.value as GlobalCompositeOperation)}
                className="bg-slate-900 border border-slate-700 text-slate-200 rounded px-2 py-0.5 text-xs font-mono"
              >
                <option value="screen">高亮對比 (Screen)</option>
                <option value="difference">差值邊緣 (Difference)</option>
                <option value="source-over">一般半透明 (Normal)</option>
              </select>

              <div className="h-3 w-px bg-slate-800 mx-1" />

              <span className="text-emerald-400">基準透明度:</span>
              <input
                type="range"
                min="0.1"
                max="1.0"
                step="0.05"
                value={baseOpacity}
                onChange={(e) => setBaseOpacity(parseFloat(e.target.value))}
                className="w-16 accent-emerald-500 cursor-pointer"
              />

              <span className="text-cyan-400 ml-1">調整層透明度:</span>
              <input
                type="range"
                min="0.1"
                max="1.0"
                step="0.05"
                value={targetOpacity}
                onChange={(e) => setTargetOpacity(parseFloat(e.target.value))}
                className="w-16 accent-cyan-500 cursor-pointer"
              />
            </div>

            {/* Canvas Zoom Controls (Bottom-Left) */}
            <div className="absolute bottom-3 left-3 flex items-center space-x-1.5 bg-slate-950/80 backdrop-blur-md border border-slate-800 px-2.5 py-1.5 rounded-lg shadow-lg text-xs z-10">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setZoom((z) => Math.min(150, z * 1.2))}
                className="h-6 w-6 p-0 text-slate-300 hover:text-white"
                title="放大"
              >
                <ZoomIn className="w-3.5 h-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setZoom((z) => Math.max(5, z * 0.8))}
                className="h-6 w-6 p-0 text-slate-300 hover:text-white"
                title="縮小"
              >
                <ZoomOut className="w-3.5 h-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  if (canvasRef.current) {
                    setPan({
                      x: Math.round(canvasRef.current.width / 2),
                      y: Math.round(canvasRef.current.height / 2),
                    });
                    setZoom(30);
                  }
                }}
                className="h-6 px-1.5 text-[10px] text-slate-400 hover:text-white"
                title="重設視圖置中"
              >
                <Maximize2 className="w-3 h-3 mr-1" />
                置中
              </Button>
              <span className="text-slate-500 font-mono text-[10px] ml-1">
                比例: {zoom.toFixed(0)} px/m
              </span>
            </div>

            {/* Instruction tooltip (Bottom-Right) */}
            <div className="absolute bottom-3 right-3 bg-slate-950/80 backdrop-blur-md border border-slate-800 px-3 py-1.5 rounded-lg text-[11px] text-slate-400 pointer-events-none shadow-lg">
              💡 <span className="text-cyan-300">拖曳青框</span> 平移 |{' '}
              <span className="text-cyan-300">頂端圓點</span> 旋轉 |{' '}
              <span className="text-slate-300">背景拖曳</span> 移動視野 |{' '}
              <span className="text-slate-300">滾輪</span> 縮放
            </div>
          </div>

          {/* Right: Precision Adjustment Control Panel */}
          <div className="w-84 w-[340px] border-l border-slate-800 bg-[#0d1322] flex flex-col p-4 space-y-4 overflow-y-auto shrink-0">
            {/* Quick Elevator Snap Tool */}
            <div className="p-3 rounded-lg bg-indigo-950/40 border border-indigo-800/60 space-y-2">
              <div className="flex items-center justify-between text-indigo-300">
                <span className="font-semibold text-xs flex items-center">
                  <Sparkles className="w-3.5 h-3.5 mr-1 text-indigo-400" />
                  特徵一鍵重合
                </span>
                <span className="text-[10px] text-indigo-400/80">電梯點基準</span>
              </div>
              <p className="text-[11px] text-slate-400 leading-tight">
                以兩樓層標記的電梯點作為物理重疊錨點，自動計算平移量 $(T_x, T_y)$。
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={handleSnapElevators}
                className="w-full h-7 text-xs bg-indigo-900/40 hover:bg-indigo-900/70 border-indigo-700 text-indigo-200"
              >
                <Sparkles className="w-3 h-3 mr-1 text-indigo-400" />
                一鍵電梯位置對齊
              </Button>
            </div>

            {/* Step Controls: Tx */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-slate-300">
                  平移 X 偏移量 (Tx)
                </label>
                <span className="text-xs font-mono text-cyan-400">{tx.toFixed(3)} m</span>
              </div>
              <Input
                type="number"
                step="0.05"
                value={tx}
                onChange={(e) => setTx(parseFloat(e.target.value) || 0)}
                className="h-7 text-xs bg-slate-950 border-slate-800 font-mono text-slate-200"
              />
              <div className="grid grid-cols-6 gap-1 pt-0.5">
                <Button variant="outline" size="sm" onClick={() => adjustTx(-1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">-1m</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTx(-0.1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">-0.1</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTx(-0.01)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">-1cm</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTx(+0.01)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">+1cm</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTx(+0.1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">+0.1</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTx(+1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">+1m</Button>
              </div>
            </div>

            {/* Step Controls: Ty */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-slate-300">
                  平移 Y 偏移量 (Ty)
                </label>
                <span className="text-xs font-mono text-cyan-400">{ty.toFixed(3)} m</span>
              </div>
              <Input
                type="number"
                step="0.05"
                value={ty}
                onChange={(e) => setTy(parseFloat(e.target.value) || 0)}
                className="h-7 text-xs bg-slate-950 border-slate-800 font-mono text-slate-200"
              />
              <div className="grid grid-cols-6 gap-1 pt-0.5">
                <Button variant="outline" size="sm" onClick={() => adjustTy(-1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">-1m</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTy(-0.1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">-0.1</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTy(-0.01)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">-1cm</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTy(+0.01)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">+1cm</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTy(+0.1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">+0.1</Button>
                <Button variant="outline" size="sm" onClick={() => adjustTy(+1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">+1m</Button>
              </div>
            </div>

            {/* Step Controls: Rotation θ */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-slate-300 flex items-center">
                  <RotateCw className="w-3.5 h-3.5 mr-1 text-cyan-400" />
                  旋轉角 (Rotation θ)
                </label>
                <span className="text-xs font-mono text-cyan-400">{rotDeg.toFixed(2)}°</span>
              </div>

              <div className="flex items-center space-x-2">
                <input
                  type="range"
                  min="-180"
                  max="180"
                  step="0.1"
                  value={rotDeg}
                  onChange={(e) => setRotDeg(parseFloat(e.target.value))}
                  className="flex-1 accent-cyan-500 cursor-pointer"
                />
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setRotDeg(0)}
                  className="h-6 px-1.5 text-[10px] text-slate-400 hover:text-white"
                  title="歸零"
                >
                  0°
                </Button>
              </div>

              <div className="grid grid-cols-6 gap-1 pt-0.5">
                <Button variant="outline" size="sm" onClick={() => adjustRot(-5)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">-5°</Button>
                <Button variant="outline" size="sm" onClick={() => adjustRot(-1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">-1°</Button>
                <Button variant="outline" size="sm" onClick={() => adjustRot(-0.1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">-0.1°</Button>
                <Button variant="outline" size="sm" onClick={() => adjustRot(+0.1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">+0.1°</Button>
                <Button variant="outline" size="sm" onClick={() => adjustRot(+1)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">+1°</Button>
                <Button variant="outline" size="sm" onClick={() => adjustRot(+5)} className="h-6 text-[10px] px-0 bg-slate-900 border-slate-800">+5°</Button>
              </div>

              <div className="text-[10px] text-slate-500 font-mono flex justify-between pt-1">
                <span>弧度 (rad):</span>
                <span>{rotRad.toFixed(4)} rad</span>
              </div>
            </div>

            {/* Transform Summary & YAML Preview */}
            <div className="p-2.5 rounded bg-slate-950 border border-slate-800 text-[11px] font-mono space-y-1 text-slate-400">
              <div className="text-[10px] text-slate-500 font-sans font-semibold">YAML Transform 配置預覽：</div>
              <div className="text-slate-300">
                translation: [{tx.toFixed(3)}, {ty.toFixed(3)}]
              </div>
              <div className="text-slate-300">
                rotation: {rotRad.toFixed(4)} <span className="text-slate-500">({rotDeg.toFixed(1)}°)</span>
              </div>
              <div className="text-slate-300">
                scale: {scaleTrans.toFixed(3)}
              </div>
            </div>

            <div className="flex-1" />

            {/* Action Buttons */}
            <div className="space-y-2 pt-2 border-t border-slate-800">
              <div className="flex items-center space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleReset}
                  className="flex-1 h-8 text-xs bg-slate-900 hover:bg-slate-800 border-slate-800"
                >
                  <RotateCcw className="w-3 h-3 mr-1" />
                  歸零 (Reset)
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRevert}
                  className="flex-1 h-8 text-xs bg-slate-900 hover:bg-slate-800 border-slate-800"
                >
                  <Undo2 className="w-3 h-3 mr-1" />
                  復原設定
                </Button>
              </div>

              <Button
                size="sm"
                onClick={handleSave}
                className="w-full h-8 text-xs bg-cyan-600 hover:bg-cyan-500 text-white font-medium shadow-md shadow-cyan-950/50"
              >
                <Check className="w-3.5 h-3.5 mr-1" />
                儲存對齊參數 (更新 Transform / YAML)
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Dialog>
  );
};
