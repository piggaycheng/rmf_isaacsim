import React, { useState, useEffect, useRef } from 'react';
import { MapCanvas, EditorTool } from '@/components/MapCanvas';
import { DispatchModal } from '@/components/DispatchModal';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Waypoint, Lane, Robot, Task, WaypointType, SlamMap, PlantSite, NavGraph, GRAPH_PALETTE, isWaypointInGraph, getWaypointGraphIndices } from '@/types/rmf';
import { ImportMapModal } from '@/components/ImportMapModal';
import { WorldMap } from '@/components/WorldMap';
import { IsaacSimStreamModal } from '@/components/IsaacSimStreamModal';
import { PlantCameraList } from '@/components/PlantCameraList';
import { FleetAdapterManager } from '@/components/FleetAdapterManager';
import { loadPlantSites, savePlantSites } from '@/data/plantSites';
import {
  MousePointer,
  MapPin,
  GitCommit,
  BatteryCharging,
  SquareParking,
  Boxes,
  Send,
  Download,
  Trash2,
  Play,
  RotateCcw,
  Layers,
  Radio,
  Wifi,
  WifiOff,
  Cpu,
  FileUp,
  Map as MapIcon,
  X,
  BoxSelect,
  Save,
  Check,
  Globe,
  Video,
  Building2,
  Plus,
  Eye,
  EyeOff,
  Pencil,
} from 'lucide-react';

const DEFAULT_GRAPHS: NavGraph[] = [
  { id: 0, name: 'Graph 0 (預設車隊)', color: '#38bdf8' },
];

// Initial Demo Map Data (Office like)
const INITIAL_WAYPOINTS: Waypoint[] = [
  { id: 'wp_1', name: 'pantry', x: 2.0, y: 5.0, type: 'workcell' },
  { id: 'wp_2', name: 'lounge', x: 8.0, y: 5.0, type: 'normal' },
  { id: 'wp_3', name: 'junction_1', x: 5.0, y: 5.0, type: 'normal' },
  { id: 'wp_4', name: 'junction_2', x: 5.0, y: 1.0, type: 'normal' },
  { id: 'wp_5', name: 'coe', x: 10.0, y: 1.0, type: 'normal' },
  { id: 'wp_6', name: 'charger_1', x: 2.0, y: 1.0, type: 'charger' },
  { id: 'wp_7', name: 'parking_1', x: 8.0, y: -2.0, type: 'parking' },
];

const INITIAL_LANES: Lane[] = [
  { id: 'lane_1', start_id: 'wp_1', end_id: 'wp_3', bidirectional: true },
  { id: 'lane_2', start_id: 'wp_3', end_id: 'wp_2', bidirectional: true },
  { id: 'lane_3', start_id: 'wp_3', end_id: 'wp_4', bidirectional: true },
  { id: 'lane_4', start_id: 'wp_4', end_id: 'wp_6', bidirectional: true },
  { id: 'lane_5', start_id: 'wp_4', end_id: 'wp_5', bidirectional: true },
  { id: 'lane_6', start_id: 'wp_5', end_id: 'wp_7', bidirectional: true },
];

const INITIAL_ROBOTS: Robot[] = [];

export type AppMode = 'world' | 'monitor' | 'edit' | 'adapters';

export function App() {
  // 1. Mode state (Persisted in localStorage so F5 keeps current mode)
  const [mode, setMode] = useState<AppMode>(() => {
    try {
      const cached = localStorage.getItem('rmf_active_mode') as AppMode;
      if (cached === 'world' || cached === 'monitor' || cached === 'edit' || cached === 'adapters') {
        return cached;
      }
    } catch (e) {}
    return 'world';
  });
  const [tool, setTool] = useState<EditorTool>('select');

  // Plant Sites & Isaac Sim Modal State
  const [plantSites, setPlantSites] = useState<PlantSite[]>(loadPlantSites);
  const [selectedPlant, setSelectedPlant] = useState<PlantSite | null>(null);
  const [isaacModalOpen, setIsIsaacModalOpen] = useState<boolean>(false);
  const [isaacModalPlant, setIsIsaacModalPlant] = useState<PlantSite | null>(null);

  // 2. Navigation Graph state (Lazy initialized from localStorage for 0ms restoration)
  const [waypoints, setWaypoints] = useState<Waypoint[]>(() => {
    try {
      const cached = localStorage.getItem('rmf_active_nav_graph');
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed.waypoints && Array.isArray(parsed.waypoints) && parsed.waypoints.length > 0) {
          return parsed.waypoints;
        }
      }
    } catch (e) {}
    return INITIAL_WAYPOINTS;
  });

  const [lanes, setLanes] = useState<Lane[]>(() => {
    try {
      const cached = localStorage.getItem('rmf_active_nav_graph');
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed.lanes && Array.isArray(parsed.lanes)) {
          return parsed.lanes;
        }
      }
    } catch (e) {}
    return INITIAL_LANES;
  });

  // Navigation Graphs state
  const [graphs, setGraphs] = useState<NavGraph[]>(() => {
    try {
      const cached = localStorage.getItem('rmf_active_nav_graph');
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed.graphs && Array.isArray(parsed.graphs) && parsed.graphs.length > 0) {
          return parsed.graphs;
        }
      }
    } catch (e) {}
    return DEFAULT_GRAPHS;
  });
  const [activeGraphIdx, setActiveGraphIdx] = useState<number>(0);
  const [focusActiveGraph, setFocusActiveGraph] = useState<boolean>(true);
  const [editingGraphId, setEditingGraphId] = useState<number | null>(null);
  const [editingGraphName, setEditingGraphName] = useState<string>('');

  const [robots, setRobots] = useState<Robot[]>(INITIAL_ROBOTS);
  const [tasks, setTasks] = useState<Task[]>([]);

  const [selectedWaypointIds, setSelectedWaypointIds] = useState<string[]>([]);
  const [selectedLaneIds, setSelectedLaneIds] = useState<string[]>([]);
  const [dispatchModalOpen, setDispatchModalOpen] = useState<boolean>(false);
  const [dispatchPresetWp, setDispatchPresetWp] = useState<Waypoint | null>(null);

  // SLAM Map state (Lazy initialized from localStorage for 0ms restoration)
  const [slamMap, setSlamMap] = useState<SlamMap | null>(() => {
    try {
      const cached = localStorage.getItem('rmf_active_slam_map');
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed && parsed.image_url) {
          return parsed;
        }
      }
    } catch (e) {}
    return null;
  });
  const [importModalOpen, setImportModalOpen] = useState<boolean>(false);

  // Backend / Simulation state
  const [isMockSimulation, setIsMockSimulation] = useState<boolean>(true);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [saveToast, setSaveToast] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const isInitialMount = useRef(true);

  // Update mode and persist to localStorage
  const handleSetMode = (newMode: AppMode) => {
    setMode(newMode);
    try {
      localStorage.setItem('rmf_active_mode', newMode);
    } catch (e) {}
  };

  // 1. Fetch initial SLAM map from backend if exists
  useEffect(() => {
    fetch('/api/map/slam')
      .then((res) => res.json())
      .then((data) => {
        if (data && data.image_url) {
          setSlamMap((prev) => {
            const merged = { ...data, opacity: prev?.opacity ?? data.opacity ?? 0.85 };
            try {
              localStorage.setItem('rmf_active_slam_map', JSON.stringify(merged));
            } catch (e) {}
            return merged;
          });
        }
      })
      .catch(() => {});
  }, []);

  // 1.1 Persist SLAM map & opacity changes to localStorage
  useEffect(() => {
    try {
      if (slamMap) {
        localStorage.setItem('rmf_active_slam_map', JSON.stringify(slamMap));
      } else {
        localStorage.removeItem('rmf_active_slam_map');
      }
    } catch (e) {}
  }, [slamMap]);

  // 2. Fetch initial Saved Navigation Graph from backend on startup
  useEffect(() => {
    fetch('/api/map')
      .then((res) => res.json())
      .then((data) => {
        if (data && Array.isArray(data.waypoints) && data.waypoints.length > 0) {
          const cached = localStorage.getItem('rmf_active_nav_graph');
          if (!cached) {
            setWaypoints(data.waypoints);
            if (Array.isArray(data.lanes)) setLanes(data.lanes);
            if (Array.isArray(data.graphs) && data.graphs.length > 0) setGraphs(data.graphs);
          }
        }
      })
      .catch(() => {});
  }, []);

  // 3. Auto-Save Effect: Automatically syncs to localStorage (0ms) and backend (debounced 800ms) on any change
  useEffect(() => {
    const payload = {
      name: 'office_map',
      waypoints,
      lanes,
      graphs,
    };

    // Immediate local save
    try {
      localStorage.setItem('rmf_active_nav_graph', JSON.stringify(payload));
    } catch (e) {}

    // Skip network POST on initial component load
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }

    // Debounced server auto-save
    const timer = setTimeout(async () => {
      try {
        await fetch('/api/map', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        setSaveToast('已自動存檔');
        setTimeout(() => setSaveToast(null), 2000);
      } catch (err) {
        // Fallback: local storage is already updated
      }
    }, 800);

    return () => clearTimeout(timer);
  }, [waypoints, lanes, graphs]);

  // WebSocket Connection to Backend
  useEffect(() => {
    const connectWs = () => {
      try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host || 'localhost:8000';
        const ws = new WebSocket(`${protocol}//${host}/ws/fleet`);
        wsRef.current = ws;

        ws.onopen = () => {
          setIsConnected(true);
          setIsMockSimulation(false);
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === 'fleet_states' && data.robots) {
              setRobots(data.robots);
            }
            if (data.type === 'tasks' && data.tasks) {
              setTasks(data.tasks);
            }
            if (data.type === 'slam_map') {
              if (data.map) {
                setSlamMap((prev) => ({
                  ...data.map,
                  opacity: prev?.opacity ?? data.map.opacity ?? 0.85,
                }));
              } else {
                setSlamMap(null);
                try {
                  localStorage.removeItem('rmf_active_slam_map');
                } catch (e) {}
              }
            }
          } catch (e) {
            console.error('Failed to parse WS msg', e);
          }
        };

        ws.onclose = () => {
          setIsConnected(false);
        };
      } catch (err) {
        setIsConnected(false);
      }
    };

    connectWs();
    const interval = setInterval(() => {
      if (!wsRef.current || wsRef.current.readyState === WebSocket.CLOSED) {
        connectWs();
      }
    }, 5000);

    return () => {
      clearInterval(interval);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  // Mock Simulation Loop (Runs when ROS 2 is not active or mock is enabled)
  useEffect(() => {
    if (!isMockSimulation) return;

    const timer = setInterval(() => {
      setRobots((prevRobots) =>
        prevRobots.map((robot) => {
          if (robot.status === 'moving') {
            // Move gently towards a target or patrol
            const targetX = robot.id === 'tinyRobot1' ? 8.0 : 2.0;
            const targetY = robot.id === 'tinyRobot1' ? 5.0 : 1.0;
            const dx = targetX - robot.x;
            const dy = targetY - robot.y;
            const dist = Math.hypot(dx, dy);

            if (dist < 0.1) {
              return {
                ...robot,
                status: 'idle',
                current_task: '已到達目的地',
                battery: Math.max(10, robot.battery - 0.2),
              };
            }

            const step = 0.05;
            const newX = robot.x + (dx / dist) * step;
            const newY = robot.y + (dy / dist) * step;
            const yaw = Math.atan2(dy, dx);

            return {
              ...robot,
              x: newX,
              y: newY,
              yaw,
              battery: Math.max(10, robot.battery - 0.05),
            };
          }
          return robot;
        })
      );

      // Advance task progress
      setTasks((prevTasks) =>
        prevTasks.map((t) => {
          if (t.status === 'active') {
            const nextProgress = t.progress + 2;
            if (nextProgress >= 100) {
              return { ...t, progress: 100, status: 'completed' };
            }
            return { ...t, progress: nextProgress };
          }
          return t;
        })
      );
    }, 100);

    return () => clearInterval(timer);
  }, [isMockSimulation]);

  // Editor Actions
  const handleAddWaypoint = (x: number, y: number, type: WaypointType) => {
    const id = `wp_${Date.now()}`;
    const name = `${type === 'normal' ? 'wp' : type}_${waypoints.length + 1}`;
    const newWp: Waypoint = {
      id,
      name,
      x,
      y,
      type,
      graph_idx: activeGraphIdx,
    };
    setWaypoints((prev) => [...prev, newWp]);
    setSelectedWaypointIds([id]);
    setSelectedLaneIds([]);
  };

  const handleMoveWaypoint = (id: string, x: number, y: number) => {
    setWaypoints((prev) =>
      prev.map((w) => (w.id === id ? { ...w, x, y } : w))
    );
  };

  const handleAddLane = (startId: string, endId: string) => {
    const exists = lanes.some(
      (l) =>
        (l.start_id === startId && l.end_id === endId) ||
        (l.start_id === endId && l.end_id === startId)
    );
    if (exists) return;

    const newLane: Lane = {
      id: `lane_${Date.now()}`,
      start_id: startId,
      end_id: endId,
      bidirectional: true,
      graph_idx: activeGraphIdx,
    };
    setLanes((prev) => [...prev, newLane]);
  };

  // Select active graph tab & purge any selected lanes/waypoints not in active graph (Option A)
  const handleSelectGraphTab = (id: number) => {
    setActiveGraphIdx(id);
    setSelectedLaneIds((prev) =>
      prev.filter((laneId) => {
        const lane = lanes.find((l) => l.id === laneId);
        return (lane?.graph_idx ?? 0) === id;
      })
    );
    setSelectedWaypointIds((prev) =>
      prev.filter((wpId) => {
        const wp = waypoints.find((w) => w.id === wpId);
        return wp ? isWaypointInGraph(wp, id, lanes) : false;
      })
    );
  };

  // Graph Management Actions
  const handleAddGraph = () => {
    const existingIds = graphs.map((g) => g.id);
    const nextId = existingIds.length > 0 ? Math.max(...existingIds) + 1 : 0;
    const nextColor = GRAPH_PALETTE[nextId % GRAPH_PALETTE.length];
    const newGraph: NavGraph = {
      id: nextId,
      name: `Graph ${nextId}`,
      color: nextColor,
    };
    setGraphs((prev) => [...prev, newGraph]);
    handleSelectGraphTab(nextId);
  };

  const handleDeleteGraph = (graphId: number) => {
    if (graphs.length <= 1) return;
    const fallbackGraph = graphs.find((g) => g.id !== graphId) || graphs[0];
    // Reassign any lanes in this graph to the fallback graph
    setLanes((prev) =>
      prev.map((l) => (l.graph_idx === graphId ? { ...l, graph_idx: fallbackGraph.id } : l))
    );
    setGraphs((prev) => prev.filter((g) => g.id !== graphId));
    if (activeGraphIdx === graphId) {
      handleSelectGraphTab(fallbackGraph.id);
    }
  };

  const handleSaveGraphName = (graphId: number, name: string) => {
    const trimmed = name.trim();
    if (!trimmed) {
      setEditingGraphId(null);
      return;
    }
    setGraphs((prev) =>
      prev.map((g) => (g.id === graphId ? { ...g, name: trimmed } : g))
    );
    setEditingGraphId(null);
  };

  const handleSelectWaypoint = (id: string | null) => {
    if (id === null) {
      setSelectedWaypointIds([]);
      setSelectedLaneIds([]);
    } else {
      setSelectedWaypointIds([id]);
      setSelectedLaneIds([]);
    }
  };

  const handleSelectMultiple = (wpIds: string[], laneIds: string[]) => {
    setSelectedWaypointIds(wpIds);
    setSelectedLaneIds(laneIds);
  };

  const handleDeleteSelected = () => {
    if (selectedWaypointIds.length === 0 && selectedLaneIds.length === 0) return;

    // Delete selected waypoints
    setWaypoints((prev) => prev.filter((w) => !selectedWaypointIds.includes(w.id)));

    // Delete selected lanes OR lanes connected to deleted waypoints
    setLanes((prev) =>
      prev.filter(
        (l) =>
          !selectedLaneIds.includes(l.id) &&
          !selectedWaypointIds.includes(l.start_id) &&
          !selectedWaypointIds.includes(l.end_id)
      )
    );

    setSelectedWaypointIds([]);
    setSelectedLaneIds([]);
  };

  // Keyboard Shortcut: Delete / Backspace to batch delete selected items
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const activeEl = document.activeElement;
      const isInput =
        activeEl?.tagName === 'INPUT' ||
        activeEl?.tagName === 'TEXTAREA' ||
        activeEl?.tagName === 'SELECT';
      if (isInput) return;

      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedWaypointIds.length > 0 || selectedLaneIds.length > 0) {
          handleDeleteSelected();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedWaypointIds, selectedLaneIds]);

  // Dispatch Action
  const handleDispatchTask = (taskReq: {
    type: 'patrol' | 'delivery' | 'goto';
    target_waypoint: string;
    destination_waypoint?: string;
    robot_id?: string;
  }) => {
    const newTask: Task = {
      id: `task_${Date.now().toString().slice(-4)}`,
      type: taskReq.type,
      target_waypoint: taskReq.target_waypoint,
      destination_waypoint: taskReq.destination_waypoint,
      robot_id: taskReq.robot_id || 'tinyRobot1',
      status: 'active',
      progress: 5,
      created_at: new Date().toLocaleTimeString(),
    };

    setTasks((prev) => [newTask, ...prev]);

    // Update robot state in mock simulation
    const assignedRobotId = taskReq.robot_id || 'tinyRobot1';
    setRobots((prev) =>
      prev.map((r) =>
        r.id === assignedRobotId
          ? {
              ...r,
              status: 'moving',
              current_task: `執行 ${taskReq.type} 前往 ${taskReq.target_waypoint}`,
            }
          : r
      )
    );

    // If connected to real backend, send over WebSocket / REST
    if (isConnected && wsRef.current) {
      wsRef.current.send(
        JSON.stringify({
          action: 'dispatch_task',
          payload: taskReq,
        })
      );
    }
  };

  // Save Map to Backend & LocalStorage
  const handleSaveMap = async () => {
    setIsSaving(true);
    const payload = {
      name: 'office_map',
      waypoints,
      lanes,
      graphs,
    };
    try {
      // 1. Immediately cache in localStorage
      localStorage.setItem('rmf_active_nav_graph', JSON.stringify(payload));

      // 2. Persist to Backend server disk (JSON + Open-RMF standard building.yaml)
      const res = await fetch('/api/map', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok) {
        setSaveToast(`已儲存 (${waypoints.length} 點, ${lanes.length} 線, ${graphs.length} 路網)`);
      } else {
        setSaveToast('儲存失敗');
      }
    } catch (e) {
      setSaveToast('已儲存至瀏覽器快取 (伺服器離線)');
    } finally {
      setIsSaving(false);
      setTimeout(() => setSaveToast(null), 3000);
    }
  };

  // Export JSON Map
  const handleExportJson = () => {
    const data = {
      name: 'rmf_navigation_graph',
      waypoints,
      lanes,
      graphs,
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'rmf_navigation_graph.json';
    a.click();
  };

  // Export Building YAML (Open-RMF Building Map standard)
  const handleExportBuildingYaml = () => {
    window.open('/api/map/download/yaml', '_blank');
  };

  // Export Nav Graph YAML for current active graph (nav_graphs/{activeGraphIdx}.yaml)
  const handleExportActiveNavYaml = () => {
    window.open(`/api/map/download/nav_graph/${activeGraphIdx}`, '_blank');
  };

  const selectedWaypoint =
    selectedWaypointIds.length === 1
      ? waypoints.find((w) => w.id === selectedWaypointIds[0]) || null
      : null;

  const selectedLane =
    selectedLaneIds.length === 1
      ? lanes.find((l) => l.id === selectedLaneIds[0]) || null
      : null;

  return (
    <div className="flex flex-col h-screen w-screen bg-[#070a13] text-slate-100 overflow-hidden font-sans">
      {/* 1. Top Navbar */}
      <header className="h-14 border-b border-slate-800 bg-[#0d1322]/90 backdrop-blur px-4 flex items-center justify-between z-20">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-primary to-cyan-400 flex items-center justify-center font-bold text-white shadow-lg shadow-primary/20">
            <Cpu className="w-5 h-5" />
          </div>
          <div>
            <h1 className="font-bold text-sm tracking-wide flex items-center space-x-2">
              <span>RMF Web Studio</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/20 text-primary border border-primary/30">
                Jazzy
              </span>
            </h1>
            <p className="text-[11px] text-slate-400">Open-RMF 路網編輯與即時車隊監控系統</p>
          </div>
        </div>

        {/* Mode Selector */}
        <div className="flex items-center bg-slate-900 border border-slate-800 p-1 rounded-lg select-none">
          {/* 1. World Map Tab (on the far left) */}
          <button
            onClick={() => handleSetMode('world')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
              mode === 'world'
                ? 'bg-primary text-primary-foreground shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Globe className="w-3.5 h-3.5" />
            <span>🌐 全球廠區 (World Map)</span>
          </button>

          {/* 2. Monitor Mode (swapped to left of Edit) */}
          <button
            onClick={() => handleSetMode('monitor')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
              mode === 'monitor'
                ? 'bg-primary text-primary-foreground shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Radio className="w-3.5 h-3.5" />
            <span>📡 即時監控 (Monitor Mode)</span>
          </button>

          {/* 3. Edit Mode (swapped to right of Monitor) */}
          <button
            onClick={() => handleSetMode('edit')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
              mode === 'edit'
                ? 'bg-primary text-primary-foreground shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <GitCommit className="w-3.5 h-3.5" />
            <span>✏️ 編輯路網 (Edit Mode)</span>
          </button>

          {/* 4. Fleet Adapter Management Tab */}
          <button
            onClick={() => handleSetMode('adapters')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
              mode === 'adapters'
                ? 'bg-primary text-primary-foreground shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
            title="管理與設定 Open-RMF 車隊適配器 (Fleet Adapters)"
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>🤖 車隊適配器 (Adapters)</span>
          </button>
        </div>

        {/* Right Info & Actions */}
        <div className="flex items-center space-x-3">
          {/* Status Indicator */}
          <div className="flex items-center space-x-1.5 text-xs px-2.5 py-1 rounded-full border border-slate-800 bg-slate-900/60">
            {isConnected ? (
              <>
                <Wifi className="w-3.5 h-3.5 text-emerald-400" />
                <span className="text-emerald-400 font-medium">RMF Core 連線中</span>
              </>
            ) : isMockSimulation ? (
              <>
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                <span className="text-cyan-400 font-medium">模擬展示模式 (Mock)</span>
              </>
            ) : (
              <>
                <WifiOff className="w-3.5 h-3.5 text-rose-400" />
                <span className="text-rose-400 font-medium">未連線</span>
              </>
            )}
          </div>
        </div>
      </header>

      {/* 2. Main Workspace */}
      {mode === 'world' ? (
        <WorldMap
          plants={plantSites}
          selectedPlant={selectedPlant}
          onSelectPlant={(plant) => setSelectedPlant(plant)}
          onOpenIsaacSim={(plant) => {
            setIsIsaacModalPlant(plant);
            setIsIsaacModalOpen(true);
          }}
          onNavigateToMonitor={(plant) => {
            setSelectedPlant(plant);
            handleSetMode('monitor');
          }}
          onNavigateToEdit={(plant) => {
            setSelectedPlant(plant);
            handleSetMode('edit');
          }}
          onAddPlant={(newPlant) => {
            const updated = [...plantSites, newPlant];
            setPlantSites(updated);
            savePlantSites(updated);
            setSelectedPlant(newPlant);
          }}
        />
      ) : mode === 'adapters' ? (
        <FleetAdapterManager
          graphs={graphs}
          robots={robots}
          waypoints={waypoints}
          lanes={lanes}
          onOpenIsaacSim={() => {
            if (selectedPlant) {
              setIsIsaacModalPlant(selectedPlant);
              setIsIsaacModalOpen(true);
            }
          }}
        />
      ) : (
        <div className="flex-1 flex overflow-hidden relative">
        {/* Left Toolbar (Edit Mode Only) */}
        {mode === 'edit' && (
          <aside className="w-14 border-r border-slate-800 bg-[#0d1322] flex flex-col items-center py-3 space-y-2 z-10 select-none">
            <button
              onClick={() => setTool('select')}
              title="選取工具（單選點位、拖曳移動、空白處拖曳矩形框選）"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'select' ? 'bg-primary text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <MousePointer className="w-5 h-5" />
            </button>
            <button
              onClick={() => setTool('waypoint')}
              title="新增一般導航點 (Waypoint)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'waypoint' ? 'bg-primary text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <MapPin className="w-5 h-5" />
            </button>
            <button
              onClick={() => setTool('lane')}
              title="連接兩點建立路徑 (Lane)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'lane' ? 'bg-primary text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <GitCommit className="w-5 h-5" />
            </button>
            <div className="w-8 h-px bg-slate-800 my-1" />
            <button
              onClick={() => setTool('charger')}
              title="新增充電樁點位 (Charger)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'charger' ? 'bg-amber-500 text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <BatteryCharging className="w-5 h-5" />
            </button>
            <button
              onClick={() => setTool('parking')}
              title="新增停車/等候點 (Parking)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'parking' ? 'bg-purple-500 text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <SquareParking className="w-5 h-5" />
            </button>
            <button
              onClick={() => setTool('workcell')}
              title="新增工作/取放料點 (Workcell)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'workcell' ? 'bg-emerald-500 text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <Boxes className="w-5 h-5" />
            </button>
          </aside>
        )}

        {/* Center: Interactive Map Canvas */}
        <main className="flex-1 h-full relative flex flex-col">
          {/* Top Actions Toolbar (Edit Mode Only - Above Graph Management Bar) */}
          {mode === 'edit' && (
            <div className="h-10 bg-[#0d1222] border-b border-slate-800 px-3 flex items-center justify-between z-10 flex-shrink-0">
              {/* Left: Save Feedback Toast */}
              <div className="flex items-center space-x-2 min-w-0">
                {saveToast && (
                  <span className="text-xs px-2.5 py-1 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-800 flex items-center transition-all shadow-sm">
                    <Check className="w-3.5 h-3.5 mr-1 text-emerald-400" />
                    {saveToast}
                  </span>
                )}
              </div>

              {/* Right: Action Buttons */}
              <div className="flex items-center space-x-2 overflow-x-auto no-scrollbar flex-shrink-0 ml-2">
                <Button
                  size="sm"
                  variant={slamMap ? 'secondary' : 'outline'}
                  onClick={() => setImportModalOpen(true)}
                  className="text-xs h-7 px-2.5 flex-shrink-0"
                  title="上傳並顯示 ROS SLAM 雷達地圖 (.pgm + map.yaml)"
                >
                  <FileUp className="w-3.5 h-3.5 mr-1 text-primary" />
                  {slamMap ? '已載入 SLAM 地圖' : '載入 SLAM 地圖'}
                </Button>

                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setWaypoints(INITIAL_WAYPOINTS);
                    setLanes(INITIAL_LANES);
                    setRobots(INITIAL_ROBOTS);
                  }}
                  className="text-xs h-7 px-2.5 flex-shrink-0"
                  title="重設為範例路網"
                >
                  <RotateCcw className="w-3.5 h-3.5 mr-1" />
                  範例路網
                </Button>

                <Button
                  size="sm"
                  variant="secondary"
                  onClick={handleExportBuildingYaml}
                  className="text-xs h-7 px-2.5 flex-shrink-0"
                  title="下載 Open-RMF 建築總圖 (building.yaml)"
                >
                  <Download className="w-3.5 h-3.5 mr-1" />
                  building.yaml
                </Button>

                <Button
                  size="sm"
                  variant="secondary"
                  onClick={handleExportActiveNavYaml}
                  className="text-xs h-7 px-2.5 flex-shrink-0"
                  title={`下載 Open-RMF 車隊專用導航路網 (nav_graphs/${activeGraphIdx}.yaml)`}
                >
                  <Download className="w-3.5 h-3.5 mr-1" />
                  Graph {activeGraphIdx}.yaml
                </Button>

                <Button
                  size="sm"
                  className="bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-sm shadow-emerald-950/50 text-xs h-7 px-2.5 flex-shrink-0"
                  onClick={handleSaveMap}
                  disabled={isSaving}
                  title="儲存當前點位與路線至伺服器硬碟 (JSON + Open-RMF YAML)"
                >
                  <Save className="w-3.5 h-3.5 mr-1" />
                  {isSaving ? '儲存中...' : '儲存路網'}
                </Button>
              </div>
            </div>
          )}

          {/* Top Graph Management Bar (Edit Mode Only) */}
          {mode === 'edit' && (
            <div className="h-10 bg-[#0b101d] border-b border-slate-800 px-3 flex items-center justify-between z-10 select-none flex-shrink-0">
              {/* Left: Graphs tabs - flex-1 min-w-0 enables overflow-x-auto, wheel scrolling supported */}
              <div
                className="flex-1 min-w-0 flex items-center space-x-1.5 overflow-x-auto py-1 no-scrollbar"
                onWheel={(e) => {
                  if (e.deltaY !== 0) {
                    e.currentTarget.scrollLeft += e.deltaY;
                  }
                }}
              >
                <span className="text-[11px] font-semibold text-slate-400 flex items-center mr-1 flex-shrink-0">
                  <Layers className="w-3.5 h-3.5 mr-1 text-primary" />
                  路網分組:
                </span>
                {graphs.map((g) => {
                  const laneCount = lanes.filter((l) => (l.graph_idx ?? 0) === g.id).length;
                  const isActive = activeGraphIdx === g.id;
                  return (
                    <div
                      key={g.id}
                      onClick={() => handleSelectGraphTab(g.id)}
                      className={`group flex items-center space-x-1.5 px-2.5 py-1 rounded-md text-xs cursor-pointer transition-all border flex-shrink-0 ${
                        isActive
                          ? 'bg-slate-800 border-slate-600 text-white font-medium shadow-sm'
                          : 'bg-slate-900/60 border-slate-800 text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                      }`}
                    >
                      <span
                        className="w-2.5 h-2.5 rounded-full inline-block shadow-sm flex-shrink-0"
                        style={{ backgroundColor: g.color }}
                      />
                      {editingGraphId === g.id ? (
                        <input
                          autoFocus
                          type="text"
                          value={editingGraphName}
                          onChange={(e) => setEditingGraphName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleSaveGraphName(g.id, editingGraphName);
                            if (e.key === 'Escape') setEditingGraphId(null);
                          }}
                          onBlur={() => handleSaveGraphName(g.id, editingGraphName)}
                          className="h-5 px-1 py-0 bg-slate-950 border border-primary text-xs text-white rounded w-28 focus:outline-none"
                          onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        <span className="truncate max-w-[130px]">{g.name}</span>
                      )}
                      <span
                        className={`text-[10px] px-1 rounded ${
                          isActive ? 'bg-slate-700 text-slate-200' : 'bg-slate-800/80 text-slate-400'
                        }`}
                      >
                        {laneCount}線
                      </span>

                      {/* Edit name icon button */}
                      {editingGraphId !== g.id && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditingGraphId(g.id);
                            setEditingGraphName(g.name);
                          }}
                          className="opacity-0 group-hover:opacity-100 hover:text-primary transition-opacity p-0.5"
                          title="重新命名此路網"
                        >
                          <Pencil className="w-3 h-3" />
                        </button>
                      )}

                      {/* Delete icon button (only if > 1 graph) */}
                      {graphs.length > 1 && editingGraphId !== g.id && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            if (window.confirm(`確定要刪除「${g.name}」嗎？該路網中的路線將會轉移至其他路網。`)) {
                              handleDeleteGraph(g.id);
                            }
                          }}
                          className="opacity-0 group-hover:opacity-100 hover:text-rose-400 transition-opacity p-0.5"
                          title="刪除此路網"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                  );
                })}

                {/* + Add Graph button */}
                <button
                  onClick={handleAddGraph}
                  className="flex items-center space-x-1 px-2 py-1 rounded-md text-xs text-slate-400 hover:text-primary hover:bg-slate-800 border border-dashed border-slate-700 transition-all flex-shrink-0"
                  title="新增一組全新的 Navigation Graph"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>新增路網</span>
                </button>
              </div>

              {/* Right: Graph display filter */}
              <div className="flex items-center space-x-2 flex-shrink-0 ml-3">
                <span className="text-[11px] text-slate-400 hidden xl:flex items-center">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1.5 inline-block" />
                  已鎖定範圍：僅選取當前路網 (Graph {activeGraphIdx})
                </span>
                <button
                  onClick={() => setFocusActiveGraph(!focusActiveGraph)}
                  className={`flex items-center space-x-1 text-xs px-2.5 py-1 rounded border transition-colors ${
                    focusActiveGraph
                      ? 'bg-amber-500/15 border-amber-500/40 text-amber-300'
                      : 'bg-slate-900 border-slate-800 text-slate-400 hover:text-slate-200'
                  }`}
                  title={focusActiveGraph ? '當前模式：僅高亮當前路網，其他路網淡化半透明顯示（點擊切換為顯示全部原色）' : '當前模式：顯示所有路網原色（點擊切換為聚焦當前路網）'}
                >
                  {focusActiveGraph ? (
                    <>
                      <EyeOff className="w-3.5 h-3.5 text-amber-400" />
                      <span>聚焦當前 (淡化其他)</span>
                    </>
                  ) : (
                    <>
                      <Eye className="w-3.5 h-3.5 text-slate-400" />
                      <span>顯示全部路網原色</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          )}

          <div className="flex-1 h-full relative">
            <MapCanvas
              mode={mode}
              tool={tool}
              waypoints={waypoints}
              lanes={lanes}
              robots={robots}
              slamMap={slamMap}
              graphs={graphs}
              activeGraphIdx={activeGraphIdx}
              focusActiveGraph={focusActiveGraph}
              selectedWaypointIds={selectedWaypointIds}
              selectedLaneIds={selectedLaneIds}
              onSelectWaypoint={handleSelectWaypoint}
              onSelectMultiple={handleSelectMultiple}
              onAddWaypoint={handleAddWaypoint}
              onMoveWaypoint={handleMoveWaypoint}
              onAddLane={handleAddLane}
              onWaypointClickInMonitor={(wp) => {
                setDispatchPresetWp(wp);
                setDispatchModalOpen(true);
              }}
            />
          </div>

          {/* Monitor Mode: Floating Quick Dispatch Prompt */}
          {mode === 'monitor' && (
            <div className="absolute top-4 left-4 bg-slate-900/90 backdrop-blur border border-slate-800 p-3 rounded-xl shadow-xl flex items-center space-x-3 pointer-events-auto">
              <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center text-primary">
                <Play className="w-4 h-4 fill-primary" />
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-200">地圖互動派單</p>
                <p className="text-[11px] text-slate-400">點擊地圖上的任意站點即可快速派遣任務</p>
              </div>
              <Button
                size="sm"
                onClick={() => {
                  setDispatchPresetWp(waypoints[0] || null);
                  setDispatchModalOpen(true);
                }}
              >
                <Send className="w-3.5 h-3.5 mr-1" />
                立即派單
              </Button>
            </div>
          )}
        </main>

        {/* Right Sidebar: Contextual Info & Inspector */}
        <aside className="w-80 border-l border-slate-800 bg-[#0d1322] flex flex-col p-4 space-y-4 overflow-y-auto z-10">
          {mode === 'edit' ? (
            /* Edit Mode: Waypoint / Lane Inspector */
            <div className="space-y-4">
              <div className="border-b border-slate-800 pb-2.5 space-y-1.5">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-bold text-slate-200 flex items-center space-x-1.5">
                    <Layers className="w-4 h-4 text-primary" />
                    <span>路網屬性檢視器</span>
                  </h2>
                  <Badge variant="outline" className="text-[10px] text-cyan-400 border-cyan-800/60 bg-cyan-950/40 font-medium">
                    共 {graphs.length} 層路網
                  </Badge>
                </div>
                <div className="flex items-center justify-between text-[11px] text-slate-400">
                  <span className="flex items-center space-x-1.5 truncate">
                    <span>當前層：</span>
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-full shrink-0 shadow-sm"
                      style={{
                        backgroundColor:
                          GRAPH_PALETTE[activeGraphIdx % GRAPH_PALETTE.length],
                      }}
                    />
                    <strong className="text-slate-200 truncate">
                      Graph {activeGraphIdx} ({graphs.find((g) => g.id === activeGraphIdx)?.name || '未命名'})
                    </strong>
                  </span>
                  <span className="text-[10px] text-slate-500 shrink-0 ml-1">
                    {waypoints.length} 站點 · {lanes.length} 路線
                  </span>
                </div>
              </div>

              {/* SLAM Map Info Card (if loaded) */}
              {slamMap && (
                <div className="bg-slate-900/80 border border-cyan-800/40 p-3 rounded-xl space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-cyan-400 flex items-center">
                      <MapIcon className="w-3.5 h-3.5 mr-1" />
                      SLAM 雷達底圖
                    </span>
                    <button
                      onClick={() => {
                        setSlamMap(null);
                        try {
                          localStorage.removeItem('rmf_active_slam_map');
                        } catch (e) {}
                        fetch('/api/map/slam', { method: 'DELETE' }).catch(() => {});
                      }}
                      className="text-slate-400 hover:text-rose-400 p-0.5 rounded transition-colors"
                      title="卸載底圖"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <div className="text-[11px] text-slate-300 space-y-0.5">
                    <p className="truncate text-slate-400">檔案: {slamMap.name}</p>
                    <p>真實尺寸: {slamMap.real_width_m.toFixed(1)}m × {slamMap.real_height_m.toFixed(1)}m</p>
                    <p>解析度: {slamMap.resolution} m/px</p>
                    <p>原點: [{slamMap.origin[0]}, {slamMap.origin[1]}]</p>
                  </div>
                  <div className="pt-1">
                    <div className="flex justify-between text-[10px] text-slate-400 mb-1">
                      <span>底圖不透明度</span>
                      <span>{Math.round((slamMap.opacity ?? 0.85) * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0.1"
                      max="1.0"
                      step="0.05"
                      value={slamMap.opacity ?? 0.85}
                      onChange={(e) =>
                        setSlamMap({ ...slamMap, opacity: parseFloat(e.target.value) })
                      }
                      className="w-full h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer"
                    />
                  </div>
                </div>
              )}

              {/* Multi-selection Batch Inspector */}
              {selectedWaypointIds.length > 1 || (selectedWaypointIds.length > 0 && selectedLaneIds.length > 0) || selectedLaneIds.length > 1 ? (
                <div className="space-y-3 bg-slate-900/60 border border-amber-900/40 p-3.5 rounded-xl">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-amber-400 flex items-center">
                      <BoxSelect className="w-3.5 h-3.5 mr-1" />
                      批次選取項目
                    </span>
                    <Badge variant="warning" className="text-[10px]">
                      {selectedWaypointIds.length} 站點 / {selectedLaneIds.length} 路線
                    </Badge>
                  </div>

                  <p className="text-[11px] text-slate-400">已透過矩形框選或 Shift 選取以下多個項目：</p>
                  
                  {selectedWaypointIds.length > 0 && (
                    <div className="flex flex-wrap gap-1 max-h-32 overflow-y-auto p-1.5 bg-slate-950/60 rounded-lg border border-slate-800">
                      {waypoints
                        .filter((w) => selectedWaypointIds.includes(w.id))
                        .map((w) => (
                          <span
                            key={w.id}
                            className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-200 border border-slate-700 truncate max-w-[120px]"
                          >
                            {w.name}
                          </span>
                        ))}
                    </div>
                  )}

                  {selectedLaneIds.length > 0 && (
                    <div className="pt-2 border-t border-slate-800 space-y-1.5">
                      <label className="text-[11px] text-slate-300 block font-medium">
                        批次轉移選取的 {selectedLaneIds.length} 條路線至路網：
                      </label>
                      <div className="flex space-x-1.5">
                        <select
                          id="batch-graph-target"
                          defaultValue={activeGraphIdx}
                          className="flex-1 bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs text-slate-200"
                        >
                          {graphs.map((g) => (
                            <option key={g.id} value={g.id}>
                              {g.name} (Graph {g.id})
                            </option>
                          ))}
                        </select>
                        <Button
                          size="sm"
                          variant="secondary"
                          className="h-7 text-xs"
                          onClick={() => {
                            const sel = (document.getElementById('batch-graph-target') as HTMLSelectElement)?.value;
                            const targetIdx = parseInt(sel, 10);
                            if (!isNaN(targetIdx)) {
                              setLanes((prev) =>
                                prev.map((l) => (selectedLaneIds.includes(l.id) ? { ...l, graph_idx: targetIdx } : l))
                              );
                            }
                          }}
                        >
                          套用
                        </Button>
                      </div>
                    </div>
                  )}

                  <div className="pt-2 space-y-1.5">
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={handleDeleteSelected}
                      className="w-full h-8 text-xs font-semibold"
                    >
                      <Trash2 className="w-3.5 h-3.5 mr-1" />
                      批次刪除選取的 {selectedWaypointIds.length + selectedLaneIds.length} 個項目 (Del)
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setSelectedWaypointIds([]);
                        setSelectedLaneIds([]);
                      }}
                      className="w-full h-7 text-xs text-slate-400"
                    >
                      取消選取
                    </Button>
                  </div>
                </div>
              ) : selectedLane ? (
                /* Single Lane Inspector */
                <div className="space-y-3 bg-slate-900/60 border border-slate-800 p-3.5 rounded-xl">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-sky-400 flex items-center">
                      <GitCommit className="w-3.5 h-3.5 mr-1" />
                      路線屬性 (Lane)
                    </span>
                    <Badge variant="secondary" className="text-[10px]">
                      {selectedLane.bidirectional ? '雙向通行' : '單向通行'}
                    </Badge>
                  </div>

                  <div className="text-xs text-slate-300 space-y-1 bg-slate-950 p-2 rounded-md border border-slate-800">
                    <p className="text-[11px] text-slate-400">連接點：</p>
                    <p className="font-mono text-[11px]">
                      {waypoints.find((w) => w.id === selectedLane.start_id)?.name || selectedLane.start_id}
                      {' ⇄ '}
                      {waypoints.find((w) => w.id === selectedLane.end_id)?.name || selectedLane.end_id}
                    </p>
                  </div>

                  {/* Graph Selection */}
                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">所屬路網 (Nav Graph)</label>
                    <div className="flex items-center space-x-2">
                      <span
                        className="w-3 h-3 rounded-full flex-shrink-0"
                        style={{
                          backgroundColor:
                            graphs.find((g) => g.id === (selectedLane.graph_idx ?? 0))?.color || '#38bdf8',
                        }}
                      />
                      <select
                        value={selectedLane.graph_idx ?? 0}
                        onChange={(e) => {
                          const newGraphIdx = parseInt(e.target.value, 10);
                          setLanes((prev) =>
                            prev.map((l) => (l.id === selectedLane.id ? { ...l, graph_idx: newGraphIdx } : l))
                          );
                        }}
                        className="flex-1 bg-slate-950 border border-slate-800 rounded-md px-2.5 py-1.5 text-xs text-slate-200"
                      >
                        {graphs.map((g) => (
                          <option key={g.id} value={g.id}>
                            {g.name} (Graph {g.id})
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">通行方向</label>
                    <select
                      value={selectedLane.bidirectional ? 'bi' : 'uni'}
                      onChange={(e) => {
                        const isBi = e.target.value === 'bi';
                        setLanes((prev) =>
                          prev.map((l) => (l.id === selectedLane.id ? { ...l, bidirectional: isBi } : l))
                        );
                      }}
                      className="w-full bg-slate-950 border border-slate-800 rounded-md px-2.5 py-1.5 text-xs text-slate-200"
                    >
                      <option value="bi">雙向通行 (Bidirectional)</option>
                      <option value="uni">單向通行 (Unidirectional)</option>
                    </select>
                  </div>

                  <div className="pt-2">
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={handleDeleteSelected}
                      className="w-full h-8 text-xs"
                    >
                      <Trash2 className="w-3.5 h-3.5 mr-1" />
                      刪除此條路線 (Del)
                    </Button>
                  </div>
                </div>
              ) : selectedWaypoint ? (
                /* Single Waypoint Inspector */
                <div className="space-y-3 bg-slate-900/60 border border-slate-800 p-3.5 rounded-xl">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-primary">{selectedWaypoint.name}</span>
                    <Badge variant="secondary" className="capitalize text-[10px]">
                      {selectedWaypoint.type}
                    </Badge>
                  </div>

                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">站點名稱</label>
                    <Input
                      value={selectedWaypoint.name}
                      onChange={(e) => {
                        const val = e.target.value;
                        setWaypoints((prev) =>
                          prev.map((w) => (w.id === selectedWaypoint.id ? { ...w, name: val } : w))
                        );
                      }}
                      className="h-8 text-xs bg-slate-950 border-slate-800"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[11px] text-slate-400 block mb-1">X 座標 (m)</label>
                      <Input
                        type="number"
                        step="0.1"
                        value={selectedWaypoint.x}
                        onChange={(e) =>
                          handleMoveWaypoint(selectedWaypoint.id, parseFloat(e.target.value) || 0, selectedWaypoint.y)
                        }
                        className="h-8 text-xs bg-slate-950 border-slate-800"
                      />
                    </div>
                    <div>
                      <label className="text-[11px] text-slate-400 block mb-1">Y 座標 (m)</label>
                      <Input
                        type="number"
                        step="0.1"
                        value={selectedWaypoint.y}
                        onChange={(e) =>
                          handleMoveWaypoint(selectedWaypoint.id, selectedWaypoint.x, parseFloat(e.target.value) || 0)
                        }
                        className="h-8 text-xs bg-slate-950 border-slate-800"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">站點類型</label>
                    <select
                      value={selectedWaypoint.type}
                      onChange={(e) => {
                        const newType = e.target.value as WaypointType;
                        setWaypoints((prev) =>
                          prev.map((w) => (w.id === selectedWaypoint.id ? { ...w, type: newType } : w))
                        );
                      }}
                      className="w-full bg-slate-950 border border-slate-800 rounded-md px-2.5 py-1.5 text-xs text-slate-200"
                    >
                      <option value="normal">一般導航點 (Normal)</option>
                      <option value="charger">⚡ 充電樁 (Charger)</option>
                      <option value="parking">🅿️ 停車等候點 (Parking)</option>
                      <option value="workcell">📦 物料取放站 (Workcell)</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">所屬路網 (Nav Graph)</label>
                    <div className="flex items-center gap-2">
                      <div
                        className="w-3 h-3 rounded-full shrink-0 shadow-sm"
                        style={{
                          backgroundColor:
                            GRAPH_PALETTE[(selectedWaypoint.graph_idx ?? 0) % GRAPH_PALETTE.length],
                        }}
                      />
                      <select
                        value={selectedWaypoint.graph_idx ?? 0}
                        onChange={(e) => {
                          const newGraphIdx = parseInt(e.target.value, 10);
                          setWaypoints((prev) =>
                            prev.map((w) =>
                              w.id === selectedWaypoint.id ? { ...w, graph_idx: newGraphIdx } : w
                            )
                          );
                        }}
                        className="flex-1 bg-slate-950 border border-slate-800 rounded-md px-2.5 py-1.5 text-xs text-slate-200"
                      >
                        {graphs.map((g) => (
                          <option key={g.id} value={g.id}>
                            {g.name} (Graph {g.id})
                          </option>
                        ))}
                      </select>
                    </div>
                    {(() => {
                      const connectedGraphs = getWaypointGraphIndices(selectedWaypoint, lanes);
                      if (connectedGraphs.length > 1) {
                        return (
                          <p className="text-[10px] text-amber-400/90 mt-1.5 leading-tight">
                            🔗 此站點連接至多組路網路線：{connectedGraphs.map((idx) => `Graph ${idx}`).join(', ')}
                          </p>
                        );
                      }
                      return null;
                    })()}
                  </div>

                  <div className="pt-2">
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={handleDeleteSelected}
                      className="w-full h-8 text-xs"
                    >
                      <Trash2 className="w-3.5 h-3.5 mr-1" />
                      刪除選取站點 (Del)
                    </Button>
                  </div>
                </div>
              ) : (
                /* Empty Selection: Show Nav Graphs Layers Overview */
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-300 flex items-center space-x-1.5">
                      <Layers className="w-3.5 h-3.5 text-cyan-400" />
                      <span>路網圖層總覽 ({graphs.length} 層)</span>
                    </span>
                    <button
                      onClick={handleAddGraph}
                      className="text-[11px] text-cyan-400 hover:text-cyan-300 flex items-center space-x-0.5 bg-cyan-950/40 hover:bg-cyan-900/50 px-2 py-0.5 rounded border border-cyan-800/40 transition-colors"
                      title="新增一組全新 Nav Graph"
                    >
                      <Plus className="w-3 h-3" />
                      <span>新增圖層</span>
                    </button>
                  </div>

                  <div className="space-y-2">
                    {graphs.map((g) => {
                      const isActive = g.id === activeGraphIdx;
                      const gLanes = lanes.filter((l) => (l.graph_idx ?? 0) === g.id);
                      const gWaypoints = waypoints.filter((w) => isWaypointInGraph(w, g.id, lanes));
                      const gColor = GRAPH_PALETTE[g.id % GRAPH_PALETTE.length];

                      return (
                        <div
                          key={g.id}
                          onClick={() => handleSelectGraphTab(g.id)}
                          className={`p-2.5 rounded-xl border transition-all cursor-pointer ${
                            isActive
                              ? 'bg-slate-900 border-cyan-500/50 shadow-sm shadow-cyan-950/50 ring-1 ring-cyan-500/30'
                              : 'bg-slate-950/60 border-slate-800 hover:border-slate-700 hover:bg-slate-900/40'
                          }`}
                        >
                          <div className="flex items-center justify-between mb-1.5">
                            <div className="flex items-center space-x-2">
                              <span
                                className="w-2.5 h-2.5 rounded-full shrink-0 shadow-sm"
                                style={{ backgroundColor: gColor }}
                              />
                              <span className={`text-xs font-semibold ${isActive ? 'text-slate-100' : 'text-slate-300'}`}>
                                {g.name}
                              </span>
                              <span className="text-[10px] text-slate-500 font-mono">
                                (Graph {g.id})
                              </span>
                            </div>
                            {isActive ? (
                              <Badge className="text-[9px] px-1.5 py-0 h-4 bg-cyan-500/20 text-cyan-300 border-cyan-500/30">
                                當前編輯中
                              </Badge>
                            ) : (
                              <span className="text-[10px] text-slate-500 hover:text-slate-300">
                                點擊切換 ➔
                              </span>
                            )}
                          </div>

                          <div className="grid grid-cols-2 gap-1.5 text-[11px] text-slate-400 bg-slate-900/40 rounded-lg p-1.5 border border-slate-800/60">
                            <div>
                              <span className="text-slate-500 text-[10px] block">專屬路線</span>
                              <span className="font-semibold text-slate-200">{gLanes.length}</span> 條
                            </div>
                            <div>
                              <span className="text-slate-500 text-[10px] block">關聯站點</span>
                              <span className="font-semibold text-slate-200">{gWaypoints.length}</span> 個
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div className="p-3 text-center text-[11px] text-slate-500 border border-dashed border-slate-800 rounded-xl space-y-1">
                    <p className="font-medium text-slate-400">💡 操作提示</p>
                    <p>點選畫布上的單一站點或路線可編輯屬性；拖曳可框選批次操作。</p>
                    <p className="text-[10px] text-slate-600">快捷鍵：選取後按 Delete 鍵可直接刪除</p>
                  </div>
                </div>
              )}
            </div>
          ) : (
            /* Monitor Mode: Fleet & Task Status */
            <div className="space-y-4">
              {/* Plant Cameras (MQTT Live On-Demand) */}
              {selectedPlant && selectedPlant.cameraTopic && (
                <PlantCameraList plant={selectedPlant} defaultExpanded={false} />
              )}

              {/* Fleet Summary */}
              <div>
                <div className="flex items-center justify-between border-b border-slate-800 pb-2 mb-3">
                  <h2 className="text-sm font-bold text-slate-200 flex items-center space-x-1.5">
                    <Radio className="w-4 h-4 text-emerald-400" />
                    <span>車隊即時狀態 ({robots.length})</span>
                  </h2>
                </div>

                <div className="space-y-2.5">
                  {robots.map((robot) => (
                    <Card key={robot.id} className="bg-slate-900/60 border-slate-800 shadow-none">
                      <CardContent className="p-3">
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="font-semibold text-xs text-slate-200">{robot.name}</span>
                          <Badge
                            variant={
                              robot.status === 'moving'
                                ? 'success'
                                : robot.status === 'charging'
                                ? 'warning'
                                : 'secondary'
                            }
                            className="text-[10px] uppercase font-mono"
                          >
                            {robot.status === 'moving' ? 'MOVING' : robot.status === 'charging' ? 'CHARGING' : 'IDLE'}
                          </Badge>
                        </div>
                        <div className="flex items-center justify-between text-[11px] text-slate-400 mb-2">
                          <span>車隊: {robot.fleet}</span>
                          <span className={robot.battery < 30 ? 'text-rose-400 font-bold' : 'text-emerald-400'}>
                            ⚡ {Math.round(robot.battery)}%
                          </span>
                        </div>
                        <div className="text-[10px] bg-slate-950 px-2 py-1 rounded text-slate-400 truncate">
                          {robot.current_task && robot.current_task !== 'nothing' && robot.status === 'moving'
                            ? robot.current_task
                            : '在線待命中'}
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </div>

              {/* Tasks Queue */}
              <div className="pt-2">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2 mb-3">
                  <h2 className="text-sm font-bold text-slate-200 flex items-center space-x-1.5">
                    <Send className="w-4 h-4 text-primary" />
                    <span>調度任務佇列 ({tasks.length})</span>
                  </h2>
                </div>

                <div className="space-y-2">
                  {tasks.map((task) => (
                    <div
                      key={task.id}
                      className="bg-slate-900/40 border border-slate-800/80 p-2.5 rounded-lg text-xs space-y-1.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-slate-300 capitalize">
                          {task.type}: {task.target_waypoint}
                        </span>
                        <Badge
                          variant={task.status === 'active' ? 'success' : 'outline'}
                          className="text-[10px]"
                        >
                          {task.status}
                        </Badge>
                      </div>
                      {/* Progress bar */}
                      <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                        <div
                          className="bg-primary h-full transition-all duration-300"
                          style={{ width: `${task.progress}%` }}
                        />
                      </div>
                      <div className="flex justify-between text-[10px] text-slate-500">
                        <span>車輛: {task.robot_id || '自動'}</span>
                        <span>{task.progress}%</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </aside>
      </div>
      )}

      {/* Task Dispatch Modal */}
      <DispatchModal
        open={dispatchModalOpen}
        onOpenChange={setDispatchModalOpen}
        selectedWaypoint={dispatchPresetWp}
        waypoints={waypoints}
        robots={robots}
        onDispatchTask={handleDispatchTask}
      />

      {/* Import SLAM Map Modal */}
      <ImportMapModal
        open={importModalOpen}
        onOpenChange={setImportModalOpen}
        onMapLoaded={(newMap) => {
          setSlamMap(newMap);
        }}
      />

      {/* Isaac Sim WebRTC Digital Twin Modal */}
      <IsaacSimStreamModal
        open={isaacModalOpen}
        onOpenChange={setIsIsaacModalOpen}
        plant={isaacModalPlant || selectedPlant}
        onNavigateToMonitor={(plant) => {
          setSelectedPlant(plant);
          handleSetMode('monitor');
        }}
        onNavigateToEdit={(plant) => {
          setSelectedPlant(plant);
          handleSetMode('edit');
        }}
      />
    </div>
  );
}

export default App;
