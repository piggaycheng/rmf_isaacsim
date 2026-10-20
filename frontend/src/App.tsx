import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { MapCanvas, EditorTool } from '@/components/MapCanvas';
import { Map3DCanvas } from '@/components/Map3DCanvas';
import { DispatchModal } from '@/components/DispatchModal';
import { OFF_GRAPH_THRESHOLD_M } from '@/lib/constants';
import { MultiStepTaskModal } from '@/components/MultiStepTaskModal';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Waypoint, Lane, Robot, Task, FleetAdapter, WaypointType, SlamMap, PlantSite, NavGraph, GRAPH_PALETTE, isWaypointInGraph, getWaypointGraphIndices, MultiStepTaskRequestData, MapLevel, MapLift, DEFAULT_LEVEL, waypointLevel, LevelTransform, toBuildingCoords, toRobotCoords, normalizeTransform, isPreciseArrival, DispatchTaskRequest, LiftRuntimeState, computeLiftYawFromLanes, parseFloorOrder } from '@/types/rmf';
import { ImportMapModal } from '@/components/ImportMapModal';
import { MapOverlayAlignModal } from '@/components/MapOverlayAlignModal';
import { LevelModal } from '@/components/LevelModal';
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
  Box,
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
  Video,
  Building2,
  Plus,
  Eye,
  EyeOff,
  Pencil,
  OctagonAlert,
  AlertTriangle,
  Ban,
  ChevronDown,
  ChevronUp,
  ListOrdered,
  Workflow,
  Clock,
  Image as ImageIcon,
  CheckCircle2,
  ArrowUpDown,
  Sliders,
  Settings2,
  Sparkles,
  Crosshair,
  Undo2,
} from 'lucide-react';

const DEFAULT_GRAPHS: NavGraph[] = [
  { id: 0, name: 'Graph 0 (預設車隊)', color: '#38bdf8' },
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

  // Monitor View Mode ('2d' | '3d')
  const [monitorViewMode, setMonitorViewMode] = useState<'2d' | '3d'>(() => {
    try {
      const cached = localStorage.getItem('rmf_monitor_view_mode');
      if (cached === '2d' || cached === '3d') return cached;
    } catch (e) {}
    return '2d';
  });

  const handleSetMonitorViewMode = (nextMode: '2d' | '3d') => {
    setMonitorViewMode(nextMode);
    try {
      localStorage.setItem('rmf_monitor_view_mode', nextMode);
    } catch (e) {}
  };

  // Lift runtime telemetry states
  const [liftStates, setLiftStates] = useState<Record<string, LiftRuntimeState>>({});

  // Plant Sites & Isaac Sim Modal State
  const [plantSites, setPlantSites] = useState<PlantSite[]>(loadPlantSites);
  const [selectedPlant, setSelectedPlant] = useState<PlantSite | null>(() => {
    try {
      const cachedId = localStorage.getItem('rmf_selected_plant_id');
      const sites = loadPlantSites();
      if (cachedId) {
        const found = sites.find((s) => s.id === cachedId);
        if (found) return found;
      }
      return sites[0] || null;
    } catch (e) {}
    return null;
  });
  const [isaacModalOpen, setIsIsaacModalOpen] = useState<boolean>(false);
  const [isaacModalPlant, setIsIsaacModalPlant] = useState<PlantSite | null>(null);

  // Collapsible sidebar sections in Monitor Mode (Persisted in localStorage)
  const [isFleetExpanded, setIsFleetExpanded] = useState<boolean>(() => {
    try {
      const cached = localStorage.getItem('rmf_fleet_expanded');
      if (cached !== null) return cached === 'true';
    } catch (e) {}
    return true;
  });
  const [isTasksExpanded, setIsTasksExpanded] = useState<boolean>(() => {
    try {
      const cached = localStorage.getItem('rmf_tasks_expanded');
      if (cached !== null) return cached === 'true';
    } catch (e) {}
    return true;
  });

  const toggleFleetExpanded = () => {
    setIsFleetExpanded((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('rmf_fleet_expanded', String(next));
      } catch (e) {}
      return next;
    });
  };

  const toggleTasksExpanded = () => {
    setIsTasksExpanded((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('rmf_tasks_expanded', String(next));
      } catch (e) {}
      return next;
    });
  };

  // 2. Navigation Graph state (Lazy initialized from localStorage for 0ms restoration)
  const isLegacySampleGraph = (wps: Waypoint[]) =>
    wps.length === 7 &&
    wps[0]?.id === 'wp_1' &&
    wps[0]?.name === 'pantry' &&
    wps[1]?.id === 'wp_2' &&
    wps[1]?.name === 'lounge';

  const [waypoints, setWaypoints] = useState<Waypoint[]>(() => {
    try {
      const cached = localStorage.getItem('rmf_active_nav_graph');
      if (cached) {
        const parsed = JSON.parse(cached);
        if (Array.isArray(parsed.waypoints) && !isLegacySampleGraph(parsed.waypoints)) {
          return parsed.waypoints;
        }
      }
    } catch (e) {}
    return [];
  });

  const [lanes, setLanes] = useState<Lane[]>(() => {
    try {
      const cached = localStorage.getItem('rmf_active_nav_graph');
      if (cached) {
        const parsed = JSON.parse(cached);
        if (
          Array.isArray(parsed.lanes) &&
          !(Array.isArray(parsed.waypoints) && isLegacySampleGraph(parsed.waypoints))
        ) {
          return parsed.lanes;
        }
      }
    } catch (e) {}
    return [];
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
  // Floors and lifts (multi-level maps); kept from the saved map and sent back on save.
  const [levels, setLevels] = useState<MapLevel[]>(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem('rmf_active_nav_graph') || '{}');
      if (Array.isArray(parsed.levels) && parsed.levels.length > 0) return parsed.levels;
    } catch (e) {}
    return [{ name: DEFAULT_LEVEL }];
  });
  const [lifts, setLifts] = useState<MapLift[]>(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem('rmf_active_nav_graph') || '{}');
      if (Array.isArray(parsed.lifts)) return parsed.lifts;
    } catch (e) {}
    return [];
  });
  const [activeLevel, setActiveLevel] = useState<string>(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem('rmf_active_nav_graph') || '{}');
      if (Array.isArray(parsed.levels) && parsed.levels.length > 0) return parsed.levels[0].name;
    } catch (e) {}
    return DEFAULT_LEVEL;
  });
  const currentLevel = levels.some((l) => l.name === activeLevel)
    ? activeLevel
    : levels[0]?.name || DEFAULT_LEVEL;

  const levelWaypoints = useMemo(
    () => (levels.length > 1 ? waypoints.filter((w) => waypointLevel(w, levels) === currentLevel) : waypoints),
    [waypoints, levels, currentLevel]
  );
  const levelLanes = useMemo(() => {
    if (levels.length <= 1) return lanes;
    const ids = new Set(levelWaypoints.map((w) => w.id));
    return lanes.filter((l) => ids.has(l.start_id) && ids.has(l.end_id));
  }, [lanes, levels, levelWaypoints]);

  // Only consider lifts active if at least one 'lift' waypoint references them
  const activeLifts = useMemo(() => {
    return lifts
      .filter((l) =>
        waypoints.some(
          (w) => (w.type === 'lift' || w.lift) && (w.lift === l.name || w.name.startsWith(l.name))
        )
      )
      .map((l) => {
        const laneYaw = computeLiftYawFromLanes(l.name, waypoints, lanes, levels);
        return {
          ...l,
          yaw: laneYaw !== null ? laneYaw : (l.yaw ?? 0),
        };
      });
  }, [lifts, waypoints, lanes, levels]);

  const [overlayAlignModalOpen, setOverlayAlignModalOpen] = useState<boolean>(false);
  const [levelModalOpen, setLevelModalOpen] = useState<boolean>(false);
  const [levelModalMode, setLevelModalMode] = useState<'add' | 'edit'>('add');
  const [editingLevel, setEditingLevel] = useState<MapLevel | null>(null);

  const handleOpenAddLevelModal = () => {
    setLevelModalMode('add');
    setEditingLevel(null);
    setLevelModalOpen(true);
  };

  const handleOpenEditLevelModal = (lvl: MapLevel) => {
    setLevelModalMode('edit');
    setEditingLevel(lvl);
    setLevelModalOpen(true);
  };

  const handleSaveLevelDefinition = (data: { name: string; elevation: number }) => {
    if (levelModalMode === 'add') {
      const newLvl: MapLevel = {
        name: data.name,
        elevation: data.elevation,
        transform: { rotation: 0, scale: 1, translation: [0, 0] },
      };
      setLevels((prev) => [...prev, newLvl]);
      setActiveLevel(data.name);
      setSelectedWaypointIds([]);
      setSelectedLaneIds([]);
      setSaveToast(`已新增樓層 [${data.name}]（高度層級: ${data.elevation}），可開始繪製該樓層路網或載入專屬底圖`);
      setTimeout(() => setSaveToast(null), 3500);
    } else if (levelModalMode === 'edit' && editingLevel) {
      const oldName = editingLevel.name;
      const newName = data.name;
      setLevels((prev) =>
        prev.map((l) =>
          l.name === oldName
            ? { ...l, name: newName, elevation: data.elevation }
            : l
        )
      );
      if (oldName !== newName) {
        if (activeLevel === oldName) setActiveLevel(newName);
        setWaypoints((prev) =>
          prev.map((w) => (waypointLevel(w, levels) === oldName ? { ...w, level: newName } : w))
        );
        setFloorSlamMaps((prev) => {
          if (!prev[oldName]) return prev;
          const updated = { ...prev, [newName]: prev[oldName] };
          delete updated[oldName];
          return updated;
        });
      }
      setSaveToast(`樓層 [${newName}] 屬性已更新（高度層級: ${data.elevation}）！`);
      setTimeout(() => setSaveToast(null), 3000);
    }
  };

  const handleSaveLevelTransform = (levelName: string, transform: LevelTransform) => {
    setLevels((prev) =>
      prev.map((l) => (l.name === levelName ? { ...l, transform } : l))
    );
    setSaveToast(`樓層 [${levelName}] 座標變換參數已更新！`);
    setTimeout(() => setSaveToast(null), 3000);
  };

  const [activeGraphIdx, setActiveGraphIdx] = useState<number>(0);
  const [focusActiveGraph, setFocusActiveGraph] = useState<boolean>(true);
  const [editingGraphId, setEditingGraphId] = useState<number | null>(null);
  const [editingGraphName, setEditingGraphName] = useState<string>('');

  const [robots, setRobots] = useState<Robot[]>(INITIAL_ROBOTS);
  const [tasks, setTasks] = useState<Task[]>([]);

  // Filter robots visible on current floor. RMF reports poses in the building frame while the
  // canvas draws each floor in its own map frame, so project them through the level transform.
  const visibleRobots = useMemo(() => {
    if (!currentLevel) return robots;
    const transformOf = (name?: string) => levels.find((l) => l.name === name)?.transform;
    const levelTransform = transformOf(currentLevel);
    const rotation = normalizeTransform(levelTransform).rotation;
    return robots
      .filter((r) => {
        const rLevel = r.level_name || DEFAULT_LEVEL;
        return (
          rLevel === currentLevel ||
          (rLevel === 'L1' && currentLevel === '1F') ||
          (rLevel === '1F' && currentLevel === 'L1')
        );
      })
      .map((r) => {
        const pos = toRobotCoords(levelTransform, r.x, r.y);
        return {
          ...r,
          x: pos.x,
          y: pos.y,
          yaw: r.yaw + rotation,
          path: r.path
            ?.filter((pt) => !pt.level_name || pt.level_name === r.level_name || pt.level_name === currentLevel)
            .map((pt) => ({ ...pt, ...toRobotCoords(transformOf(pt.level_name) ?? levelTransform, pt.x, pt.y) })),
        };
      });
  }, [robots, currentLevel, levels]);

  const [selectedWaypointIds, setSelectedWaypointIds] = useState<string[]>([]);
  const [selectedLaneIds, setSelectedLaneIds] = useState<string[]>([]);

  // Switch editor tool & clear selection when moving away from select tool
  const handleSelectTool = (nextTool: EditorTool) => {
    setTool(nextTool);
    if (nextTool !== 'select') {
      setSelectedWaypointIds([]);
      setSelectedLaneIds([]);
    }
  };

  const [dispatchModalOpen, setDispatchModalOpen] = useState<boolean>(false);
  const [dispatchPresetWp, setDispatchPresetWp] = useState<Waypoint | null>(null);
  const [multiStepModalOpen, setMultiStepModalOpen] = useState<boolean>(false);
  const [proceedPendingRobotId, setProceedPendingRobotId] = useState<string | null>(null);

  // Multi-floor SLAM Map state (dictionary level_name -> SlamMap)
  const [floorSlamMaps, setFloorSlamMaps] = useState<Record<string, SlamMap>>(() => {
    try {
      const cached = localStorage.getItem('rmf_floor_slam_maps');
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed && typeof parsed === 'object') return parsed;
      }
      const legacy = localStorage.getItem('rmf_active_slam_map');
      if (legacy) {
        const parsed = JSON.parse(legacy);
        if (parsed && parsed.image_url) {
          return { [parsed.level_name || DEFAULT_LEVEL]: parsed };
        }
      }
    } catch (e) {}
    return {};
  });

  const slamMap = useMemo(() => {
    return floorSlamMaps[currentLevel] || null;
  }, [floorSlamMaps, currentLevel]);

  const setSlamMap = useCallback((updater: SlamMap | null | ((prev: SlamMap | null) => SlamMap | null)) => {
    setFloorSlamMaps((prev) => {
      const current = prev[currentLevel] || null;
      const nextMap = typeof updater === 'function' ? updater(current) : updater;
      const next = { ...prev };
      if (nextMap) {
        next[currentLevel] = nextMap;
      } else {
        delete next[currentLevel];
      }
      try {
        localStorage.setItem('rmf_floor_slam_maps', JSON.stringify(next));
        if (nextMap) localStorage.setItem('rmf_active_slam_map', JSON.stringify(nextMap));
        else localStorage.removeItem('rmf_active_slam_map');
      } catch (e) {}
      return next;
    });
  }, [currentLevel]);
  const [importModalOpen, setImportModalOpen] = useState<boolean>(false);

  // Backend / Simulation state
  const [isMockSimulation, setIsMockSimulation] = useState<boolean>(true);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [isApplyingMap, setIsApplyingMap] = useState<boolean>(false);
  // Fleets whose running Fleet Adapter has not loaded the latest saved nav graph
  const [staleAdapterFleets, setStaleAdapterFleets] = useState<string[]>([]);
  const [saveToast, setSaveToast] = useState<string | null>(null);
  const [cancelingTaskId, setCancelingTaskId] = useState<string | null>(null);
  const [actionPendingRobotId, setActionPendingRobotId] = useState<string | null>(null);
  const [isGlobalActionPending, setIsGlobalActionPending] = useState<boolean>(false);
  const wsRef = useRef<WebSocket | null>(null);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState<boolean>(false);
  const isMapLoaded = useRef(false);

  // Update mode and persist to localStorage
  const handleSetMode = (newMode: AppMode) => {
    setMode(newMode);
    setSelectedWaypointIds([]);
    setSelectedLaneIds([]);
    try {
      localStorage.setItem('rmf_active_mode', newMode);
    } catch (e) {}
  };

  const handleSelectPlant = (plant: PlantSite | null) => {
    setSelectedPlant(plant);
    try {
      if (plant) {
        localStorage.setItem('rmf_selected_plant_id', plant.id);
      } else {
        localStorage.removeItem('rmf_selected_plant_id');
      }
    } catch (e) {}
  };

  // 1. Fetch initial SLAM maps from backend if exists
  useEffect(() => {
    fetch('/api/map/slam')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) {
          if (data.maps && typeof data.maps === 'object') {
            setFloorSlamMaps(data.maps);
            try {
              localStorage.setItem('rmf_floor_slam_maps', JSON.stringify(data.maps));
            } catch (e) {}
          } else if (data.image_url) {
            const lvl = data.level_name || currentLevel || DEFAULT_LEVEL;
            setFloorSlamMaps((prev) => {
              const merged = { ...data, opacity: prev[lvl]?.opacity ?? data.opacity ?? 0.85 };
              const next = { ...prev, [lvl]: merged };
              try {
                localStorage.setItem('rmf_floor_slam_maps', JSON.stringify(next));
                localStorage.setItem('rmf_active_slam_map', JSON.stringify(merged));
              } catch (e) {}
              return next;
            });
          }
        }
      })
      .catch(() => {});
  }, [currentLevel]);

  // 2. Fetch initial Saved Navigation Graph from backend on startup
  useEffect(() => {
    fetch('/api/map')
      .then((res) => res.json())
      .then((data) => {
        if (data && Array.isArray(data.waypoints)) {
          setWaypoints(data.waypoints);
          if (Array.isArray(data.lanes)) setLanes(data.lanes);
          if (Array.isArray(data.graphs) && data.graphs.length > 0) setGraphs(data.graphs);
          if (Array.isArray(data.levels) && data.levels.length > 0) setLevels(data.levels);
          if (Array.isArray(data.lifts)) setLifts(data.lifts);
          try {
            localStorage.setItem('rmf_active_nav_graph', JSON.stringify(data));
          } catch (e) {}
        }
      })
      .catch(() => {})
      .finally(() => {
        setTimeout(() => {
          isMapLoaded.current = true;
          setHasUnsavedChanges(false);
        }, 100);
      });
  }, []);

  // 3. Track unsaved map edits (auto-save removed; saving is triggered manually via '儲存路網' or '套用至車隊')
  useEffect(() => {
    if (!isMapLoaded.current) return;
    setHasUnsavedChanges(true);
  }, [waypoints, lanes, graphs, levels, lifts]);

  // Prompt before leaving if there are unsaved changes
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedChanges]);

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
            if (data.type === 'dispatch_response') {
              if (data.status === 'error') {
                setSaveToast(`任務派發失敗: ${data.message || '調度器拒絕'}`);
              } else if (data.status === 'success') {
                setSaveToast(data.message || `任務 [${data.task_id}] 已成功提交`);
              }
            }
            if (data.type === 'proceed_response') {
              if (data.status === 'error') {
                setSaveToast(`放行失敗: ${data.message || '指令被拒絕'}`);
              } else {
                setSaveToast(data.message || `車輛 ${data.robot_id} 放行指令已送出`);
              }
            }
            if (data.type === 'lift_states' && Array.isArray(data.lifts)) {
              setLiftStates((prev) => {
                const next = { ...prev };
                data.lifts.forEach((l: LiftRuntimeState) => {
                  if (l.lift_name) {
                    next[l.lift_name] = l;
                  }
                });
                return next;
              });
            }
            if (data.type === 'slam_map') {
              if (data.maps && typeof data.maps === 'object') {
                setFloorSlamMaps(data.maps);
                try {
                  localStorage.setItem('rmf_floor_slam_maps', JSON.stringify(data.maps));
                } catch (e) {}
              } else if (data.level_name) {
                setFloorSlamMaps((prev) => {
                  const next = { ...prev };
                  if (data.map) {
                    next[data.level_name] = {
                      ...data.map,
                      opacity: prev[data.level_name]?.opacity ?? data.map.opacity ?? 0.85,
                    };
                  } else {
                    delete next[data.level_name];
                  }
                  try {
                    localStorage.setItem('rmf_floor_slam_maps', JSON.stringify(next));
                  } catch (e) {}
                  return next;
                });
              } else if (data.map) {
                setFloorSlamMaps((prev) => {
                  const lvl = data.map.level_name || currentLevel;
                  const next = {
                    ...prev,
                    [lvl]: {
                      ...data.map,
                      opacity: prev[lvl]?.opacity ?? data.map.opacity ?? 0.85,
                    },
                  };
                  try {
                    localStorage.setItem('rmf_floor_slam_maps', JSON.stringify(next));
                  } catch (e) {}
                  return next;
                });
              } else {
                setFloorSlamMaps({});
                try {
                  localStorage.removeItem('rmf_floor_slam_maps');
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

    // Fetch initial lifts state
    fetch('/api/lifts')
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) {
          const map: Record<string, LiftRuntimeState> = {};
          data.forEach((l: LiftRuntimeState) => {
            if (l.lift_name) map[l.lift_name] = l;
          });
          setLiftStates(map);
        }
      })
      .catch(() => {});

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
          if (robot.status === 'moving' && !robot.emergency_stop) {
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
    let name: string;
    let liftName: string | undefined;

    if (type === 'lift') {
      let liftNum = 1;
      while (waypoints.some((w) => w.lift === `lift_${liftNum}` && waypointLevel(w, levels) === currentLevel)) {
        liftNum++;
      }
      liftName = `lift_${liftNum}`;
      name = `${liftName}_${currentLevel}`;
      let suffix = 1;
      while (waypoints.some((w) => w.name === name)) {
        name = `${liftName}_${currentLevel}_${suffix++}`;
      }
    } else {
      const prefix = `${type === 'normal' ? 'wp' : type}_`;
      const usedNumbers = waypoints
        .map((w) => (w.name.startsWith(prefix) ? Number(w.name.slice(prefix.length)) : NaN))
        .filter((n) => Number.isInteger(n));
      name = `${prefix}${Math.max(0, ...usedNumbers) + 1}`;
    }

    const newWp: Waypoint = {
      id,
      name,
      x,
      y,
      type,
      graph_idx: activeGraphIdx,
      ...(levels.length > 0 ? { level: currentLevel } : {}),
      ...(liftName ? { lift: liftName } : {}),
    };
    setWaypoints((prev) => [...prev, newWp]);
    setSelectedWaypointIds([]);
    setSelectedLaneIds([]);

    if (type === 'lift' && liftName) {
      const currentLevelObj = levels.find((l) => l.name === currentLevel);
      const bCoord = toBuildingCoords(currentLevelObj?.transform, x, y);
      setLifts((prev) => {
        if (prev.some((l) => l.name === liftName)) {
          return prev.map((l) => (l.name === liftName ? { ...l, x: bCoord.x, y: bCoord.y } : l));
        }
        return [...prev, { name: liftName, x: bCoord.x, y: bCoord.y, yaw: 0, width: 2.0, depth: 2.0 }];
      });
    }
  };

  const handleMoveWaypoint = (id: string, x: number, y: number) => {
    setWaypoints((prev) =>
      prev.map((w) => {
        if (w.id === id) {
          if (w.type === 'lift' && w.lift) {
            const currentLevelObj = levels.find((l) => l.name === waypointLevel(w, levels));
            const bCoord = toBuildingCoords(currentLevelObj?.transform, x, y);
            setLifts((prevLifts) =>
              prevLifts.map((l) => (l.name === w.lift ? { ...l, x: bCoord.x, y: bCoord.y } : l))
            );
          }
          return { ...w, x, y };
        }
        return w;
      })
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
    setSelectedWaypointIds([]);
    setSelectedLaneIds([]);
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

  // Floor / Level Management Actions
  const handleAddLevel = (name: string, elevation?: number) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(trimmed)) {
      setSaveToast('樓層名稱格式錯誤，僅能使用英數字、_、-（例如 2F、B1）');
      setTimeout(() => setSaveToast(null), 3500);
      return;
    }
    if (levels.some((l) => l.name.toLowerCase() === trimmed.toLowerCase())) {
      setSaveToast(`樓層 ${trimmed} 已存在！`);
      setTimeout(() => setSaveToast(null), 3000);
      return;
    }
    const finalElevation =
      typeof elevation === 'number' && Number.isFinite(elevation)
        ? elevation
        : parseFloorOrder(trimmed) ?? levels.length;

    const newLvl: MapLevel = {
      name: trimmed,
      elevation: finalElevation,
      transform: { rotation: 0, scale: 1, translation: [0, 0] },
    };
    setLevels((prev) => [...prev, newLvl]);
    setActiveLevel(trimmed);
    setSelectedWaypointIds([]);
    setSelectedLaneIds([]);
    setSaveToast(`已新增樓層 [${trimmed}]（高度層級: ${finalElevation}），可開始繪製該樓層路網或載入專屬底圖`);
    setTimeout(() => setSaveToast(null), 3500);
  };

  const handlePromptAddLevel = () => {
    handleOpenAddLevelModal();
  };

  const handleDeleteLevel = (name: string) => {
    if (levels.length <= 1) {
      setSaveToast('至少必須保留一個樓層！');
      setTimeout(() => setSaveToast(null), 2500);
      return;
    }
    const countWp = waypoints.filter((w) => waypointLevel(w, levels) === name).length;
    const confirmMsg = countWp > 0
      ? `確定要刪除樓層 [${name}] 嗎？該樓層的 ${countWp} 個站點與其路線也將一併移除。`
      : `確定要刪除樓層 [${name}] 嗎？`;
    if (!window.confirm(confirmMsg)) return;

    const remaining = levels.filter((l) => l.name !== name);
    setLevels(remaining);

    // Remove waypoints and lanes associated with this level
    const removedWpIds = new Set(waypoints.filter((w) => waypointLevel(w, levels) === name).map((w) => w.id));
    setWaypoints((prev) => prev.filter((w) => !removedWpIds.has(w.id)));
    setLanes((prev) => prev.filter((l) => !removedWpIds.has(l.start_id) && !removedWpIds.has(l.end_id)));

    // Remove floor slam map from state and local storage
    setFloorSlamMaps((prev) => {
      const next = { ...prev };
      delete next[name];
      try {
        localStorage.setItem('rmf_floor_slam_maps', JSON.stringify(next));
      } catch (e) {}
      return next;
    });
    fetch(`/api/map/slam?level_name=${encodeURIComponent(name)}`, { method: 'DELETE' }).catch(() => {});

    if (activeLevel === name) {
      setActiveLevel(remaining[0]?.name || DEFAULT_LEVEL);
    }
    setSelectedWaypointIds([]);
    setSelectedLaneIds([]);
    setSaveToast(`已刪除樓層 [${name}]`);
    setTimeout(() => setSaveToast(null), 3000);
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
    const remainingWps = waypoints.filter((w) => !selectedWaypointIds.includes(w.id));
    setWaypoints(remainingWps);

    // If any lift no longer has any lift waypoint, remove it from lifts state
    setLifts((prev) =>
      prev.filter((l) =>
        remainingWps.some(
          (w) => (w.type === 'lift' || w.lift) && (w.lift === l.name || w.name.startsWith(l.name))
        )
      )
    );

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
  const handleDispatchTask = (taskReq: DispatchTaskRequest) => {
    // If connected to real backend, send over WebSocket and rely on backend authoritative state
    if (isConnected && wsRef.current) {
      wsRef.current.send(
        JSON.stringify({
          action: 'dispatch_task',
          payload: taskReq,
        })
      );
    } else {
      // Fallback only for standalone offline preview
      const newTask: Task = {
        id: `task_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
        type: taskReq.type,
        target_waypoint: taskReq.target_waypoint,
        destination_waypoint: taskReq.destination_waypoint,
        robot_id: taskReq.robot_id || 'tinyRobot1',
        status: 'active',
        progress: 5,
        created_at: new Date().toLocaleTimeString(),
        rounds: taskReq.type === 'patrol' ? (taskReq.rounds ?? 1) : undefined,
      };

      setTasks((prev) => [newTask, ...prev]);

      const assignedRobotId = taskReq.robot_id || 'tinyRobot1';
      setRobots((prev) =>
        prev.map((r) =>
          r.id === assignedRobotId
            ? {
                ...r,
                status: 'moving',
                current_task: `執行 ${taskReq.type} 前往 ${taskReq.target_waypoint}${
                  taskReq.type === 'patrol' ? ` (${taskReq.rounds ?? 1} 輪)` : ''
                }`,
              }
            : r
        )
      );
    }
  };

  // Cancel Task Action
  const handleCancelTask = async (taskId: string) => {
    setCancelingTaskId(taskId);
    try {
      const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status !== 'error') {
        setSaveToast(data.message || `任務 [${taskId}] 已成功取消`);
        setTasks((prev) =>
          prev.map((t) => (t.id === taskId ? { ...t, status: 'canceled' } : t))
        );
      } else {
        setSaveToast(`取消任務失敗：${data.message || res.statusText || '後端拒絕'}`);
      }
    } catch (err) {
      if (isMockSimulation) {
        setTasks((prev) =>
          prev.map((t) => (t.id === taskId ? { ...t, status: 'canceled' } : t))
        );
        setSaveToast(`任務 [${taskId}] 已取消 (Mock 模式)`);
      } else {
        setSaveToast('無法連線至後端取消任務');
      }
    } finally {
      setCancelingTaskId(null);
      setTimeout(() => setSaveToast(null), 4000);
    }
  };

  // Dispatch Multi-Step Task Action
  const handleDispatchMultiStepTask = async (
    payload: MultiStepTaskRequestData
  ): Promise<{ success: boolean; message?: string }> => {
    try {
      const endpoint = payload.mode === 'compose' ? '/api/tasks/compose' : '/api/tasks/multi-step';
      const bodyPayload =
        payload.mode === 'compose'
          ? {
              steps: payload.steps,
              robot_id: payload.robot_id,
              fleet_name: payload.fleet_name,
              task_id: payload.task_id,
            }
          : {
              robot_id: payload.robot_id,
              fleet_name: payload.fleet_name,
              steps: payload.steps,
              auto_proceed: payload.auto_proceed ?? false,
              step_delay: payload.step_delay ?? 0.0,
              task_id: payload.task_id,
            };

      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(bodyPayload),
      });

      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status !== 'error') {
        const msg = data.message || `多步任務 [${data.task_id}] 已成功下派！`;
        setSaveToast(msg);
        return { success: true, message: msg };
      } else {
        const errMsg = data.detail || data.message || res.statusText || '調度器拒絕下派多步任務';
        setSaveToast(`多步任務派送失敗：${errMsg}`);
        return { success: false, message: errMsg };
      }
    } catch (err: any) {
      const errMsg = err?.message || '無法連線至後端服務';
      setSaveToast(`多步任務派送錯誤：${errMsg}`);
      return { success: false, message: errMsg };
    } finally {
      setTimeout(() => setSaveToast(null), 5000);
    }
  };

  // Proceed Release Gate Action
  const handleProceedTask = async (robotId: string, cmdId?: number) => {
    setProceedPendingRobotId(robotId);
    try {
      const res = await fetch(`/api/robots/${encodeURIComponent(robotId)}/proceed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cmd_id: cmdId }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status !== 'error') {
        setSaveToast(data.message || `已成功放行車輛 '${robotId}' 執行下一步！`);
      } else {
        setSaveToast(`放行失敗：${data.detail || data.message || res.statusText}`);
      }
    } catch (err: any) {
      setSaveToast(`放行指令發送失敗：${err?.message || '連線錯誤'}`);
    } finally {
      setProceedPendingRobotId(null);
      setTimeout(() => setSaveToast(null), 4000);
    }
  };

  // Robot Emergency Stop Action
  const handleRobotEmergencyStop = async (
    robotId: string,
    fleetName: string | undefined,
    action: 'estop' | 'release',
    cancelTask: boolean = false
  ) => {
    setActionPendingRobotId(robotId);
    const endpoint =
      action === 'estop'
        ? `/api/robots/${encodeURIComponent(robotId)}/emergency-stop`
        : `/api/robots/${encodeURIComponent(robotId)}/emergency-stop/release`;

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fleet_name: fleetName,
          cancel_task: cancelTask,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status !== 'error') {
        setSaveToast(
          data.message ||
            (action === 'estop'
              ? `車輛 [${robotId}] 已緊急停車`
              : `車輛 [${robotId}] 已解除緊急停車`)
        );
        setRobots((prev) =>
          prev.map((r) =>
            r.id === robotId
              ? {
                  ...r,
                  emergency_stop: action === 'estop',
                  status: action === 'estop' ? 'emergency_stop' : 'idle',
                }
              : r
          )
        );
        if (cancelTask) {
          setTasks((prev) =>
            prev.map((t) => (t.robot_id === robotId ? { ...t, status: 'canceled' } : t))
          );
        }
      } else {
        setSaveToast(`操作失敗：${data.message || res.statusText || '未知錯誤'}`);
      }
    } catch (err) {
      if (isMockSimulation) {
        setRobots((prev) =>
          prev.map((r) =>
            r.id === robotId
              ? {
                  ...r,
                  emergency_stop: action === 'estop',
                  status: action === 'estop' ? 'emergency_stop' : 'idle',
                }
              : r
          )
        );
        setSaveToast(
          action === 'estop'
            ? `車輛 [${robotId}] 已緊急停車 (Mock 模式)`
            : `車輛 [${robotId}] 已解除緊急停車 (Mock 模式)`
        );
      } else {
        setSaveToast('無法連線至伺服器發送急停控制');
      }
    } finally {
      setActionPendingRobotId(null);
      setTimeout(() => setSaveToast(null), 4000);
    }
  };

  // Fleet-wide Emergency Stop Action
  const handleGlobalEmergencyStop = async (action: 'estop' | 'release', cancelTask: boolean = false) => {
    setIsGlobalActionPending(true);
    const endpoint =
      action === 'estop' ? '/api/emergency-stop' : '/api/emergency-stop/release';

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cancel_task: cancelTask,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status !== 'error') {
        setSaveToast(
          action === 'estop'
            ? '全場車輛已發送緊急停車指令'
            : '全場車輛已解除緊急停車'
        );
        setRobots((prev) =>
          prev.map((r) => ({
            ...r,
            emergency_stop: action === 'estop',
            status: action === 'estop' ? 'emergency_stop' : 'idle',
          }))
        );
        if (cancelTask) {
          setTasks((prev) =>
            prev.map((t) =>
              t.status === 'active' || t.status === 'queued'
                ? { ...t, status: 'canceled' }
                : t
            )
          );
        }
      } else {
        setSaveToast(`全場急停操作失敗：${data.message || res.statusText || '未知錯誤'}`);
      }
    } catch (err) {
      if (isMockSimulation) {
        setRobots((prev) =>
          prev.map((r) => ({
            ...r,
            emergency_stop: action === 'estop',
            status: action === 'estop' ? 'emergency_stop' : 'idle',
          }))
        );
        setSaveToast(
          action === 'estop'
            ? '全場車輛已緊急停車 (Mock 模式)'
            : '全場車輛已解除急停 (Mock 模式)'
        );
      } else {
        setSaveToast('無法連線至伺服器發送全場急停');
      }
    } finally {
      setIsGlobalActionPending(false);
      setTimeout(() => setSaveToast(null), 4000);
    }
  };

  // Computed emergency stop states across fleet
  const estoppedCount = robots.filter(
    (r) => r.emergency_stop || r.status === 'emergency_stop'
  ).length;
  const hasEstoppedRobots = estoppedCount > 0;

  // Poll Fleet Adapter diagnostics so an outdated loaded nav graph is visible without reading logs
  useEffect(() => {
    if (!isConnected) return;
    const poll = async () => {
      try {
        const res = await fetch('/api/fleet-adapters');
        if (!res.ok) return;
        const adapters: FleetAdapter[] = await res.json();
        setStaleAdapterFleets(
          adapters.filter((a) => a.diagnostics?.status === 'stale').map((a) => a.fleet_name)
        );
      } catch (e) {
        console.warn('Failed to refresh Fleet Adapter diagnostics:', e);
      }
    };
    poll();
    const timer = setInterval(poll, 5000);
    return () => clearInterval(timer);
  }, [isConnected]);

  // Save Map to Backend & LocalStorage (Pure save without restarting Fleet Adapters)
  const handleSaveMap = async () => {
    setIsSaving(true);
    // Only persist lifts that currently have at least one lift waypoint
    const liftWaypoints = waypoints.filter((w) => w.type === 'lift' || w.lift);
    const liftNamesWithWps = new Set(
      liftWaypoints.map((w) => w.lift || (w.name.startsWith('lift_') ? w.name.split('_').slice(0, 2).join('_') : 'lift_1'))
    );
    const finalLifts: MapLift[] = [];
    liftNamesWithWps.forEach((liftName) => {
      const existing = lifts.find((l) => l.name === liftName);
      const wp = liftWaypoints.find((w) => w.lift === liftName || w.name.startsWith(liftName));
      if (wp) {
        const lvl = levels.find((l) => l.name === waypointLevel(wp, levels));
        const bPos = toBuildingCoords(lvl?.transform, wp.x, wp.y);
        const laneYaw = computeLiftYawFromLanes(liftName, waypoints, lanes, levels);
        finalLifts.push({
          name: liftName,
          x: existing?.x ?? bPos.x,
          y: existing?.y ?? bPos.y,
          yaw: laneYaw !== null ? laneYaw : (existing?.yaw ?? 0),
          width: existing?.width ?? 2.0,
          depth: existing?.depth ?? 2.0,
        });
      }
    });

    const payload = {
      name: 'office_map',
      waypoints,
      lanes,
      graphs,
      ...(levels.length > 0 ? { levels, lifts: finalLifts } : {}),
      restart_adapter: false,
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
        setStaleAdapterFleets(data.adapter_restart_required || []);
        setSaveToast(data.message || '路網已儲存');
        setHasUnsavedChanges(false);
      } else {
        setSaveToast(`儲存失敗：${data.detail || res.status}`);
      }
    } catch (e) {
      setSaveToast('已儲存至瀏覽器快取 (伺服器離線)');
    } finally {
      setIsSaving(false);
      setTimeout(() => setSaveToast(null), 3000);
    }
  };

  // Apply Map to Fleet Adapters (Save & Reload all Fleet Adapters)
  const handleApplyMapToFleets = async (allowOffline = false) => {
    if (allowOffline && !window.confirm(
      '離線不代表車輛已停止。請確認 Isaac Sim 已關閉，或所有離線實體車已確實停止，而非僅網路中斷。\n\n離線套用仍會阻擋未結束任務及尚未停止的在線車輛；不會自動續跑舊任務。離線車輛重新連線並回報閒置前，暫停派遣。\n\n確定要離線套用路網？'
    )) return;
    setIsApplyingMap(true);
    // Only persist lifts that currently have at least one lift waypoint
    const liftWaypoints = waypoints.filter((w) => w.type === 'lift' || w.lift);
    const liftNamesWithWps = new Set(
      liftWaypoints.map((w) => w.lift || (w.name.startsWith('lift_') ? w.name.split('_').slice(0, 2).join('_') : 'lift_1'))
    );
    const finalLifts: MapLift[] = [];
    liftNamesWithWps.forEach((liftName) => {
      const existing = lifts.find((l) => l.name === liftName);
      const wp = liftWaypoints.find((w) => w.lift === liftName || w.name.startsWith(liftName));
      if (wp) {
        const lvl = levels.find((l) => l.name === waypointLevel(wp, levels));
        const bPos = toBuildingCoords(lvl?.transform, wp.x, wp.y);
        const laneYaw = computeLiftYawFromLanes(liftName, waypoints, lanes, levels);
        finalLifts.push({
          name: liftName,
          x: existing?.x ?? bPos.x,
          y: existing?.y ?? bPos.y,
          yaw: laneYaw !== null ? laneYaw : (existing?.yaw ?? 0),
          width: existing?.width ?? 2.0,
          depth: existing?.depth ?? 2.0,
        });
      }
    });

    const payload = {
      name: 'office_map',
      waypoints,
      lanes,
      graphs,
      ...(levels.length > 0 ? { levels, lifts: finalLifts } : {}),
      restart_adapter: true,
      allow_offline_reload: allowOffline,
    };
    try {
      localStorage.setItem('rmf_active_nav_graph', JSON.stringify(payload));

      const res = await fetch('/api/map', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok && data.status !== 'error') {
        setStaleAdapterFleets(data.adapter_restart_required || []);
        setSaveToast(data.message || '已套用路網至各車隊');
        setHasUnsavedChanges(false);
      } else {
        setSaveToast(`套用失敗：${data.detail || data.message || res.status}`);
      }
    } catch (e) {
      setSaveToast('套用失敗 (伺服器離線)');
    } finally {
      setIsApplyingMap(false);
      setTimeout(() => setSaveToast(null), allowOffline ? 8000 : 3000);
    }
  };

  // Discard unsaved changes and reload map from backend
  const handleDiscardChanges = async () => {
    if (!window.confirm('確定要放棄所有未儲存的路網變更，還原為上次儲存的狀態？')) return;
    try {
      const res = await fetch('/api/map');
      if (res.ok) {
        const data = await res.json();
        if (data && Array.isArray(data.waypoints)) {
          isMapLoaded.current = false;
          setWaypoints(data.waypoints);
          setLanes(Array.isArray(data.lanes) ? data.lanes : []);
          if (Array.isArray(data.graphs) && data.graphs.length > 0) setGraphs(data.graphs);
          if (Array.isArray(data.levels) && data.levels.length > 0) setLevels(data.levels);
          if (Array.isArray(data.lifts)) setLifts(data.lifts);
          setSelectedWaypointIds([]);
          setSelectedLaneIds([]);
          setHasUnsavedChanges(false);
          setSaveToast('已還原為伺服器已儲存路網');
          setTimeout(() => setSaveToast(null), 3000);
          setTimeout(() => {
            isMapLoaded.current = true;
          }, 100);
        }
      }
    } catch (e) {
      setSaveToast('還原失敗 (伺服器離線)');
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
      ...(levels.length > 0 ? { levels, lifts } : {}),
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
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
              mode === 'world'
                ? 'bg-primary text-primary-foreground shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            全球廠區 (World Map)
          </button>

          {/* 2. Monitor Mode (swapped to left of Edit) */}
          <button
            onClick={() => handleSetMode('monitor')}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
              mode === 'monitor'
                ? 'bg-primary text-primary-foreground shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            即時監控 (Monitor Mode)
          </button>

          {/* 3. Edit Mode (swapped to right of Monitor) */}
          <button
            onClick={() => handleSetMode('edit')}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
              mode === 'edit'
                ? 'bg-primary text-primary-foreground shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            編輯路網 (Edit Mode)
          </button>

          {/* 4. Fleet Adapter Management Tab */}
          <button
            onClick={() => handleSetMode('adapters')}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
              mode === 'adapters'
                ? 'bg-primary text-primary-foreground shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
            title="管理與設定 Open-RMF 車隊適配器 (Fleet Adapters)"
          >
            車隊適配器 (Adapters)
          </button>
        </div>

        {/* Right Info & Actions */}
        <div className="flex items-center space-x-3">
          {saveToast && (
            <div
              className={`text-xs px-3 py-1 rounded-full flex items-center shadow-md border animate-in fade-in transition-all ${
                saveToast.includes('失敗') || saveToast.includes('拒絕') || saveToast.includes('錯誤')
                  ? 'bg-rose-950/90 text-rose-300 border-rose-800'
                  : saveToast.includes('急停')
                  ? 'bg-amber-950/90 text-amber-300 border-amber-800'
                  : 'bg-emerald-950/90 text-emerald-300 border-emerald-800'
              }`}
            >
              {saveToast.includes('失敗') || saveToast.includes('拒絕') || saveToast.includes('錯誤') ? (
                <AlertTriangle className="w-3.5 h-3.5 mr-1.5 text-rose-400 shrink-0" />
              ) : saveToast.includes('急停') ? (
                <OctagonAlert className="w-3.5 h-3.5 mr-1.5 text-amber-400 shrink-0" />
              ) : (
                <Check className="w-3.5 h-3.5 mr-1.5 text-emerald-400 shrink-0" />
              )}
              <span className="truncate max-w-xs">{saveToast}</span>
            </div>
          )}
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
          onSelectPlant={(plant) => handleSelectPlant(plant)}
          onOpenIsaacSim={(plant) => {
            setIsIsaacModalPlant(plant);
            setIsIsaacModalOpen(true);
          }}
          onNavigateToMonitor={(plant) => {
            handleSelectPlant(plant);
            handleSetMode('monitor');
          }}
          onNavigateToEdit={(plant) => {
            handleSelectPlant(plant);
            handleSetMode('edit');
          }}
          onAddPlant={(newPlant) => {
            const updated = [...plantSites, newPlant];
            setPlantSites(updated);
            savePlantSites(updated);
            handleSelectPlant(newPlant);
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
              onClick={() => handleSelectTool('select')}
              title="選取工具（單選點位、拖曳移動、空白處拖曳矩形框選）"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'select' ? 'bg-primary text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <MousePointer className="w-5 h-5" />
            </button>
            <button
              onClick={() => handleSelectTool('waypoint')}
              title="新增一般導航點 (Waypoint)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'waypoint' ? 'bg-primary text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <MapPin className="w-5 h-5" />
            </button>
            <button
              onClick={() => handleSelectTool('lane')}
              title="連接兩點建立路徑 (Lane)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'lane' ? 'bg-primary text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <GitCommit className="w-5 h-5" />
            </button>
            <div className="w-8 h-px bg-slate-800 my-1" />
            <button
              onClick={() => handleSelectTool('charger')}
              title="新增充電樁點位 (Charger)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'charger' ? 'bg-amber-500 text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <BatteryCharging className="w-5 h-5" />
            </button>
            <button
              onClick={() => handleSelectTool('parking')}
              title="新增停車/等候點 (Parking)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'parking' ? 'bg-purple-500 text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <SquareParking className="w-5 h-5" />
            </button>
            <button
              onClick={() => handleSelectTool('workcell')}
              title="新增工作/取放料點 (Workcell)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'workcell' ? 'bg-emerald-500 text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <Boxes className="w-5 h-5" />
            </button>
            <button
              onClick={() => handleSelectTool('lift')}
              title="新增電梯點位 (Lift / Elevator)"
              className={`p-2.5 rounded-lg transition-colors ${
                tool === 'lift' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              <ArrowUpDown className="w-5 h-5" />
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

                {hasUnsavedChanges && (
                  <span
                    className="flex items-center text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/40 rounded px-2 h-7 flex-shrink-0"
                    title="當前有尚未儲存至伺服器的路網修改，請記得點擊「儲存路網」"
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 mr-1.5 animate-pulse" />
                    有未儲存變更
                  </span>
                )}

                {hasUnsavedChanges && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-slate-400 hover:text-rose-300 hover:bg-rose-950/30 text-xs h-7 px-2 flex-shrink-0"
                    onClick={handleDiscardChanges}
                    disabled={isSaving || isApplyingMap}
                    title="放棄當前未儲存的變更，還原為伺服器已儲存的路網"
                  >
                    <Undo2 className="w-3.5 h-3.5 mr-1" />
                    放棄變更
                  </Button>
                )}

                {staleAdapterFleets.length > 0 && (
                  <span
                    className="flex items-center text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/40 rounded px-2 h-7 flex-shrink-0"
                    title={`Fleet Adapter 仍使用舊路網，派到新站點的任務會失敗。按「套用至車隊」將最新路網套用至各車隊 Adapter。`}
                  >
                    <AlertTriangle className="w-3.5 h-3.5 mr-1" />
                    {staleAdapterFleets.join(', ')} 尚未套用新路網
                  </span>
                )}

                <Button
                  size="sm"
                  className={`font-medium shadow-sm text-xs h-7 px-2.5 flex-shrink-0 transition-all ${
                    hasUnsavedChanges
                      ? 'bg-amber-600 hover:bg-amber-500 text-white shadow-amber-950/50 ring-1 ring-amber-400/50'
                      : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-950/50'
                  }`}
                  onClick={handleSaveMap}
                  disabled={isSaving || isApplyingMap}
                  title="單純儲存當前點位與路線至伺服器硬碟 (JSON + Open-RMF YAML)，不重新載入車隊 Adapter"
                >
                  <Save className="w-3.5 h-3.5 mr-1" />
                  {isSaving ? '儲存中...' : hasUnsavedChanges ? '儲存路網 (未儲存)' : '儲存路網'}
                </Button>

                <Button
                  size="sm"
                  className="bg-cyan-600 hover:bg-cyan-500 text-white font-medium shadow-sm shadow-cyan-950/50 text-xs h-7 px-2.5 flex-shrink-0"
                  onClick={() => handleApplyMapToFleets()}
                  disabled={isApplyingMap || isSaving}
                  title="將當前路網套用至各車隊並重新載入 Fleet Adapter"
                >
                  <RotateCcw className={`w-3.5 h-3.5 mr-1 ${isApplyingMap ? 'animate-spin' : ''}`} />
                  {isApplyingMap ? '套用中...' : '套用至車隊'}
                </Button>

                <Button
                  size="sm"
                  variant="outline"
                  className="text-amber-300 border-amber-700 hover:bg-amber-950 text-xs h-7 px-2.5 flex-shrink-0"
                  onClick={() => handleApplyMapToFleets(true)}
                  disabled={isApplyingMap || isSaving}
                  title="模擬器已關閉或已確認離線車輛停止時使用；重新連線並回報閒置前暫停派遣"
                >
                  離線套用
                </Button>
              </div>
            </div>
          )}

          {/* Floor / Level Management Toolbar (Edit Mode Only) */}
          {mode === 'edit' && (
            <div className="h-10 bg-[#090e1a] border-b border-slate-800 px-3 flex items-center justify-between z-10 flex-shrink-0 select-none">
              <div className="flex items-center space-x-2 overflow-x-auto no-scrollbar py-1">
                <span className="text-[11px] font-semibold text-slate-400 flex items-center mr-1 shrink-0">
                  <Building2 className="w-3.5 h-3.5 mr-1 text-cyan-400" />
                  樓層管理:
                </span>
                <div className="flex items-center space-x-1.5">
                  {levels.map((lvl) => {
                    const isActive = lvl.name === currentLevel;
                    const hasMap = Boolean(floorSlamMaps[lvl.name]);
                    const wpCount = waypoints.filter((w) => waypointLevel(w, levels) === lvl.name).length;
                    const elev =
                      typeof lvl.elevation === 'number' && Number.isFinite(lvl.elevation)
                        ? lvl.elevation
                        : parseFloorOrder(lvl.name);
                    return (
                      <div
                        key={lvl.name}
                        className={`group flex items-center rounded-lg border text-xs transition-all ${
                          isActive
                            ? 'bg-primary/20 border-primary text-white font-semibold shadow-sm'
                            : 'bg-slate-900 border-slate-800 text-slate-300 hover:border-slate-700'
                        }`}
                      >
                        <button
                          onClick={() => {
                            setActiveLevel(lvl.name);
                            setSelectedWaypointIds([]);
                            setSelectedLaneIds([]);
                          }}
                          className="flex items-center space-x-1.5 px-2 py-1"
                          title={`切換編輯樓層：${lvl.name}（${wpCount} 站點，${hasMap ? '已載入底圖' : '尚未載入底圖'}，高度階數: ${elev ?? '未指定'}）`}
                        >
                          <span>{lvl.name}</span>
                          {elev !== null && (
                            <span
                              className={`text-[9px] px-1 py-0.2 rounded font-mono font-medium ${
                                elev >= 0
                                  ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-800/40'
                                  : 'bg-indigo-950/80 text-indigo-300 border border-indigo-800/40'
                              }`}
                              title={`樓層高度層級 (Elevation): ${elev} (${(elev * 24).toFixed(0)}m)`}
                            >
                              {elev >= 0 ? `+${elev}` : elev}
                            </span>
                          )}
                          {hasMap ? (
                            <span title="此樓層已有雷達底圖">
                              <ImageIcon className="w-3 h-3 text-cyan-400" />
                            </span>
                          ) : (
                            <span className="w-1.5 h-1.5 rounded-full bg-slate-600" title="尚未載入底圖" />
                          )}
                          <span className="text-[10px] text-slate-500 font-mono">
                            ({wpCount}點)
                          </span>
                        </button>

                        {/* Edit floor elevation / properties */}
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleOpenEditLevelModal(lvl);
                          }}
                          className="px-1 text-slate-500 hover:text-cyan-300 opacity-60 group-hover:opacity-100 transition-opacity"
                          title={`編輯 ${lvl.name} 樓層名稱與 3D 高度層級`}
                        >
                          <Settings2 className="w-3 h-3" />
                        </button>

                        {levels.length > 1 && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteLevel(lvl.name);
                            }}
                            className="pr-1.5 pl-0.5 text-slate-500 hover:text-rose-400 transition-colors"
                            title={`刪除 ${lvl.name} 樓層`}
                          >
                            <X className="w-3 h-3" />
                          </button>
                        )}
                      </div>
                    );
                  })}
                  <button
                    onClick={handleOpenAddLevelModal}
                    className="flex items-center space-x-1 px-2.5 py-1 rounded-lg border border-dashed border-slate-700 text-xs text-slate-400 hover:text-cyan-300 hover:border-cyan-500 transition-all shrink-0"
                    title="新增樓層 (自訂名稱與 3D 高度層級)"
                  >
                    <Plus className="w-3 h-3" />
                    <span>新增樓層</span>
                  </button>
                </div>
              </div>

              <div className="flex items-center space-x-2 shrink-0 text-xs">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setOverlayAlignModalOpen(true)}
                  className="h-6 text-[11px] px-2 text-cyan-300 border-cyan-800/60 bg-cyan-950/40 hover:bg-cyan-900/60"
                  title="開啟雙樓層 PGM 疊圖旋轉平移對齊工具 (讓電梯井與柱位特徵重合)"
                >
                  <Layers className="w-3 h-3 mr-1" />
                  雙圖疊圖校準
                </Button>
                {floorSlamMaps[currentLevel] ? (
                  <span className="text-emerald-400 text-[11px] flex items-center bg-emerald-950/40 border border-emerald-800/40 px-2 py-0.5 rounded">
                    <CheckCircle2 className="w-3 h-3 mr-1" />
                    {currentLevel} 底圖已載入
                  </span>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setImportModalOpen(true)}
                    className="h-6 text-[11px] px-2 text-cyan-400 border-cyan-800/60 bg-cyan-950/40 hover:bg-cyan-900/60"
                  >
                    <FileUp className="w-3 h-3 mr-1" />
                    載入 {currentLevel} 底圖
                  </Button>
                )}
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
            {mode === 'edit' && activeLifts.length > 0 && (
              <div className="absolute top-2 left-2 z-20 flex items-center space-x-1.5 bg-[#0b101d]/90 border border-indigo-900/60 rounded-md px-2.5 py-1 text-[11px] text-indigo-300 shadow-md">
                <ArrowUpDown className="w-3.5 h-3.5 text-indigo-400" />
                <span className="font-semibold text-slate-300">電梯:</span>
                <span className="font-mono text-indigo-200">{activeLifts.map((l) => l.name).join(', ')}</span>
              </div>
            )}
            {mode === 'monitor' && monitorViewMode === '3d' ? (
              <Map3DCanvas
                levels={levels}
                activeLevel={currentLevel}
                floorSlamMaps={floorSlamMaps}
                waypoints={waypoints}
                lanes={lanes}
                robots={robots}
                lifts={activeLifts}
                liftStates={liftStates}
                graphs={graphs}
                selectedWaypointIds={selectedWaypointIds}
                onSelectWaypoint={handleSelectWaypoint}
                onWaypointClickInMonitor={(wp) => {
                  setDispatchPresetWp(wp);
                  setDispatchModalOpen(true);
                }}
                onFocusFloor={(lvl) => {
                  setActiveLevel(lvl);
                }}
              />
            ) : (
              <MapCanvas
                mode={mode}
                tool={tool}
                waypoints={levelWaypoints}
                lanes={levelLanes}
                robots={visibleRobots}
                slamMap={slamMap}
                graphs={graphs}
                activeGraphIdx={activeGraphIdx}
                focusActiveGraph={focusActiveGraph}
                selectedWaypointIds={selectedWaypointIds}
                selectedLaneIds={selectedLaneIds}
                lifts={activeLifts}
                levelTransform={levels.find((l) => l.name === currentLevel)?.transform}
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
            )}
          </div>

          {/* Monitor Mode: Floating Floor Switcher & Quick Dispatch Toolbar */}
          {mode === 'monitor' && (
            <div className="absolute top-4 left-4 flex flex-wrap items-center gap-2.5 pointer-events-auto z-20">
              {/* 2D / 3D Mode Toggle */}
              <div className="bg-[#0b101d]/90 backdrop-blur border border-slate-800 p-1 rounded-xl shadow-xl flex items-center space-x-1">
                <button
                  onClick={() => handleSetMonitorViewMode('2d')}
                  className={`flex items-center space-x-1.5 px-3 py-1 rounded-lg text-xs font-semibold transition-all ${
                    monitorViewMode === '2d'
                      ? 'bg-cyan-600 text-white shadow-md'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/80'
                  }`}
                  title="切換為 2D 單樓層平面監控"
                >
                  <Layers className="w-3.5 h-3.5" />
                  <span>2D 平面</span>
                </button>
                <button
                  onClick={() => handleSetMonitorViewMode('3d')}
                  className={`flex items-center space-x-1.5 px-3 py-1 rounded-lg text-xs font-semibold transition-all ${
                    monitorViewMode === '3d'
                      ? 'bg-indigo-600 text-white shadow-md'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/80'
                  }`}
                  title="切換為 3D 多樓層立體同屏監控（含電梯動態）"
                >
                  <Box className="w-3.5 h-3.5" />
                  <span>3D 立體</span>
                </button>
              </div>

              {/* Floor Switcher Tabs */}
              <div className="bg-[#0b101d]/90 backdrop-blur border border-slate-800 p-1 px-2 rounded-xl shadow-xl flex items-center space-x-1.5">
                <div className="flex items-center space-x-1 text-slate-300 px-1.5 py-0.5 text-xs font-semibold">
                  <Building2 className="w-3.5 h-3.5 text-cyan-400" />
                  <span>樓層</span>
                </div>
                <div className="h-4 w-px bg-slate-800 mx-0.5" />
                {levels.map((l) => {
                  const isActive = l.name === currentLevel;
                  const robotsOnFloor = robots.filter(
                    (r) => (r.level_name || DEFAULT_LEVEL) === l.name || (l.name === '1F' && r.level_name === 'L1')
                  ).length;
                  const hasFloorMap = Boolean(floorSlamMaps[l.name]);
                  return (
                    <button
                      key={l.name}
                      onClick={() => {
                        setActiveLevel(l.name);
                        setSelectedWaypointIds([]);
                        setSelectedLaneIds([]);
                      }}
                      className={`flex items-center space-x-1.5 px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                        isActive
                          ? 'bg-primary text-white shadow'
                          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/80'
                      }`}
                      title={`切換至 ${l.name} 樓層即時畫面（${robotsOnFloor} 台車輛在線，${hasFloorMap ? '已載入雷達底圖' : '無底圖'}）`}
                    >
                      <span>{l.name}</span>
                      <span
                        className={`text-[10px] px-1.5 py-0.2 rounded-full font-mono ${
                          isActive
                            ? 'bg-white/20 text-white font-bold'
                            : robotsOnFloor > 0
                            ? 'bg-cyan-950 text-cyan-300 border border-cyan-800/60 font-semibold'
                            : 'bg-slate-800 text-slate-500'
                        }`}
                      >
                        {robotsOnFloor}台
                      </span>
                    </button>
                  );
                })}
              </div>

              {/* Quick Dispatch Card */}
              <div className="bg-slate-900/90 backdrop-blur border border-slate-800 p-1.5 px-3 rounded-xl shadow-xl flex items-center space-x-2.5">
                <div className="w-6 h-6 rounded-full bg-primary/20 flex items-center justify-center text-primary shrink-0">
                  <Play className="w-3.5 h-3.5 fill-primary" />
                </div>
                <div className="hidden sm:block">
                  <p className="text-xs font-semibold text-slate-200">地圖互動派單</p>
                </div>
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  onClick={() => {
                    setDispatchPresetWp(levelWaypoints[0] || waypoints[0] || null);
                    setDispatchModalOpen(true);
                  }}
                >
                  <Send className="w-3 h-3 mr-1" />
                  立即派單
                </Button>
              </div>
            </div>
          )}

          {/* Map Top-Right: Camera Stream PiP Mount Target */}
          <div id="map-camera-pip-target" className="absolute top-4 right-4 z-30 pointer-events-auto" />
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

              {/* SLAM Map Info Card for current floor */}
              {slamMap ? (
                <div className="bg-slate-900/80 border border-cyan-800/40 p-3 rounded-xl space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-cyan-400 flex items-center">
                      <MapIcon className="w-3.5 h-3.5 mr-1" />
                      SLAM 底圖 ({currentLevel})
                    </span>
                    <button
                      onClick={() => {
                        setFloorSlamMaps((prev) => {
                          const next = { ...prev };
                          delete next[currentLevel];
                          try {
                            localStorage.setItem('rmf_floor_slam_maps', JSON.stringify(next));
                            localStorage.removeItem('rmf_active_slam_map');
                          } catch (e) {}
                          return next;
                        });
                        fetch(`/api/map/slam?level_name=${encodeURIComponent(currentLevel)}`, { method: 'DELETE' }).catch(() => {});
                        setSaveToast(`已卸載樓層 [${currentLevel}] 的底圖`);
                        setTimeout(() => setSaveToast(null), 3000);
                      }}
                      className="text-slate-400 hover:text-rose-400 p-0.5 rounded transition-colors"
                      title="卸載此樓層底圖"
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
              ) : (
                <div className="bg-slate-900/40 border border-dashed border-slate-800 p-3 rounded-xl flex items-center justify-between">
                  <div>
                    <p className="text-xs font-medium text-slate-300">樓層 [{currentLevel}] 尚未載入底圖</p>
                    <p className="text-[10px] text-slate-500">可上傳該樓層專屬雷達底圖</p>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setImportModalOpen(true)}
                    className="h-7 text-xs text-cyan-400 border-cyan-800/60 bg-cyan-950/40 hover:bg-cyan-900/60 shrink-0"
                  >
                    <FileUp className="w-3 h-3 mr-1" />
                    載入底圖
                  </Button>
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
                  {levels.length > 0 && (
                    <div className="text-[11px] text-slate-400">
                      樓層 {waypointLevel(selectedWaypoint, levels)}
                      {selectedWaypoint.lift ? ` · 電梯 ${selectedWaypoint.lift}` : ''}
                    </div>
                  )}

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
                          prev.map((w) => {
                            if (w.id === selectedWaypoint.id) {
                              const updated: Waypoint = { ...w, type: newType };
                              if (newType === 'lift' && !updated.lift) {
                                updated.lift = 'lift_1';
                              }
                              return updated;
                            }
                            return w;
                          })
                        );
                        if (newType === 'lift') {
                          setLifts((prev) => {
                            const lName = selectedWaypoint.lift || 'lift_1';
                            if (prev.some((l) => l.name === lName)) return prev;
                            return [
                              ...prev,
                              {
                                name: lName,
                                x: selectedWaypoint.x,
                                y: selectedWaypoint.y,
                                yaw: 0,
                                width: 2.0,
                                depth: 2.0,
                              },
                            ];
                          });
                        }
                      }}
                      className="w-full bg-slate-950 border border-slate-800 rounded-md px-2.5 py-1.5 text-xs text-slate-200"
                    >
                      <option value="normal">一般導航點 (Normal)</option>
                      <option value="charger">⚡ 充電樁 (Charger)</option>
                      <option value="parking">🅿️ 停車等候點 (Parking)</option>
                      <option value="workcell">📦 物料取放站 (Workcell)</option>
                      <option value="lift">🛗 電梯點位 (Lift)</option>
                    </select>
                  </div>

                  <label
                    className="flex items-start gap-2 text-[11px] text-slate-300"
                    title="勾選後須等車端導航回報完成才算到點；未勾選的一般點位在距離 0.25 m、角度 0.2 rad 內即完成，車子直接往下一點"
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={isPreciseArrival(selectedWaypoint)}
                      disabled={['lift', 'charger', 'workcell'].includes(selectedWaypoint.type)}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setWaypoints((prev) =>
                          prev.map((w) => (w.id === selectedWaypoint.id ? { ...w, precise_arrival: checked } : w))
                        );
                      }}
                    />
                    <span>
                      精準到點（等車端導航回報完成）
                      <span className="block text-[10px] text-slate-500">
                        電梯、充電樁、取放站固定開啟；一般點位在 0.25 m / 0.2 rad 內即完成
                      </span>
                    </span>
                  </label>

                  {/* Lift Dedicated Inspector Section */}
                  {selectedWaypoint.type === 'lift' && (
                    <div className="p-2.5 rounded-lg border border-indigo-800/60 bg-indigo-950/30 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold text-indigo-300 flex items-center">
                          <ArrowUpDown className="w-3.5 h-3.5 mr-1 text-indigo-400" />
                          電梯設定 (Elevator)
                        </span>
                        <span className="text-[10px] text-indigo-400 font-mono">Open-RMF Lift</span>
                      </div>

                      <div>
                        <label className="text-[10px] text-slate-400 block mb-1">所屬電梯代號 (Lift Name)</label>
                        <Input
                          value={selectedWaypoint.lift || 'lift_1'}
                          onChange={(e) => {
                            const newLiftName = e.target.value.trim();
                            setWaypoints((prev) =>
                              prev.map((w) =>
                                w.id === selectedWaypoint.id ? { ...w, lift: newLiftName } : w
                              )
                            );
                            if (newLiftName) {
                              setLifts((prev) => {
                                if (prev.some((l) => l.name === newLiftName)) return prev;
                                return [
                                  ...prev,
                                  {
                                    name: newLiftName,
                                    x: selectedWaypoint.x,
                                    y: selectedWaypoint.y,
                                    yaw: 0,
                                    width: 2.0,
                                    depth: 2.0,
                                  },
                                ];
                              });
                            }
                          }}
                          placeholder="例如：lift_1"
                          className="h-7 text-xs bg-slate-950 border-slate-800"
                        />
                        {(() => {
                          const currentWpLevel = waypointLevel(selectedWaypoint, levels);
                          const liftName = selectedWaypoint.lift || 'lift_1';
                          const conflictWp = waypoints.find(
                            (w) =>
                              w.id !== selectedWaypoint.id &&
                              w.lift === liftName &&
                              waypointLevel(w, levels) === currentWpLevel
                          );
                          if (conflictWp) {
                            return (
                              <p className="text-[10px] text-rose-400 mt-1.5 leading-tight flex items-start">
                                <AlertTriangle className="w-3.5 h-3.5 mr-1 shrink-0 text-rose-400 mt-0.5" />
                                <span>
                                  樓層 [{currentWpLevel}] 已有站點 <code>{conflictWp.name}</code> 使用此電梯。每座電梯在同一樓層只能有 1 個站點。
                                </span>
                              </p>
                            );
                          }
                          return null;
                        })()}
                      </div>

                      {/* Building Actual Physical Coordinates (Building Frame) */}
                      {(() => {
                        const currentWpLevel = waypointLevel(selectedWaypoint, levels);
                        const currentLvlObj = levels.find((l) => l.name === currentWpLevel);
                        const bldgCoord = toBuildingCoords(currentLvlObj?.transform, selectedWaypoint.x, selectedWaypoint.y);

                        return (
                          <div className="p-2.5 rounded bg-slate-900/90 border border-slate-800 space-y-2">
                            <div className="flex items-center justify-between text-[11px]">
                              <span className="font-semibold text-cyan-300 flex items-center">
                                <Building2 className="w-3.5 h-3.5 mr-1 text-cyan-400" />
                                建築實際物理座標 (Building Frame)
                              </span>
                              <span className="text-[10px] text-slate-500 font-mono">Open-RMF 基準</span>
                            </div>

                            <div className="grid grid-cols-2 gap-2 text-xs">
                              <div>
                                <label className="text-[10px] text-slate-400 block mb-0.5">實際 X (m)</label>
                                <Input
                                  type="number"
                                  step="0.05"
                                  value={parseFloat(bldgCoord.x.toFixed(3))}
                                  onChange={(e) => {
                                    const newActualX = parseFloat(e.target.value) || 0;
                                    const newCanvas = toRobotCoords(currentLvlObj?.transform, newActualX, bldgCoord.y);
                                    handleMoveWaypoint(
                                      selectedWaypoint.id,
                                      parseFloat(newCanvas.x.toFixed(2)),
                                      parseFloat(newCanvas.y.toFixed(2))
                                    );
                                  }}
                                  className="h-7 text-xs bg-slate-950 border-slate-800 font-mono text-cyan-300"
                                />
                              </div>
                              <div>
                                <label className="text-[10px] text-slate-400 block mb-0.5">實際 Y (m)</label>
                                <Input
                                  type="number"
                                  step="0.05"
                                  value={parseFloat(bldgCoord.y.toFixed(3))}
                                  onChange={(e) => {
                                    const newActualY = parseFloat(e.target.value) || 0;
                                    const newCanvas = toRobotCoords(currentLvlObj?.transform, bldgCoord.x, newActualY);
                                    handleMoveWaypoint(
                                      selectedWaypoint.id,
                                      parseFloat(newCanvas.x.toFixed(2)),
                                      parseFloat(newCanvas.y.toFixed(2))
                                    );
                                  }}
                                  className="h-7 text-xs bg-slate-950 border-slate-800 font-mono text-cyan-300"
                                />
                              </div>
                            </div>
                            <p className="text-[10px] text-slate-500 leading-tight">
                              💡 修改實際座標會經由 [{currentWpLevel}] Transform 換算並更新當前 Canvas 座標 ({selectedWaypoint.x.toFixed(2)}, {selectedWaypoint.y.toFixed(2)})。
                            </p>

                            {/* Multi-floor elevator station alignment */}
                            {levels.length > 1 && (
                              <div className="pt-2 border-t border-slate-800/80">
                                <div className="flex items-center justify-between mb-1.5">
                                  <label className="text-[10px] text-slate-400 font-medium">
                                    跨樓層電梯對齊 (Vertical Alignment)
                                  </label>
                                  <button
                                    type="button"
                                    onClick={() => setOverlayAlignModalOpen(true)}
                                    className="text-[10px] text-cyan-400 hover:text-cyan-300 flex items-center"
                                    title="開啟雙樓層 PGM 疊圖旋轉平移對齊工具"
                                  >
                                    <Layers className="w-2.5 h-2.5 mr-0.5" />
                                    雙圖疊圖校準
                                  </button>
                                </div>

                                <div className="space-y-1.5">
                                  {levels.map((lvl) => {
                                    const liftName = selectedWaypoint.lift || 'lift_1';
                                    const stationOnFloor = waypoints.find(
                                      (w) => w.lift === liftName && waypointLevel(w, levels) === lvl.name
                                    );
                                    const isCurrentFloor = lvl.name === currentWpLevel;
                                    const targetCanvas = toRobotCoords(lvl.transform, bldgCoord.x, bldgCoord.y);

                                    return (
                                      <div
                                        key={lvl.name}
                                        className="p-1.5 rounded bg-slate-950/80 border border-slate-800 text-[11px]"
                                      >
                                        <div className="flex items-center justify-between">
                                          <span className="font-mono text-slate-300">
                                            {lvl.name}
                                            {isCurrentFloor && <span className="text-[10px] text-slate-500 ml-1">(當前)</span>}
                                          </span>

                                          {stationOnFloor ? (
                                            (() => {
                                              const stBldg = toBuildingCoords(lvl.transform, stationOnFloor.x, stationOnFloor.y);
                                              const offset = Math.hypot(stBldg.x - bldgCoord.x, stBldg.y - bldgCoord.y);
                                              const isAligned = offset <= 0.05;

                                              return isAligned ? (
                                                <span className="text-[10px] text-emerald-400 flex items-center">
                                                  <CheckCircle2 className="w-3 h-3 mr-1" />
                                                  已對齊 ({stationOnFloor.name})
                                                </span>
                                              ) : (
                                                <div className="flex items-center space-x-1">
                                                  <span className="text-[10px] text-amber-400" title={`實際座標偏差: ${offset.toFixed(2)}m`}>
                                                    偏差 {offset.toFixed(2)}m
                                                  </span>
                                                  <button
                                                    type="button"
                                                    onClick={() => {
                                                      handleMoveWaypoint(
                                                        stationOnFloor.id,
                                                        parseFloat(targetCanvas.x.toFixed(2)),
                                                        parseFloat(targetCanvas.y.toFixed(2))
                                                      );
                                                      setSaveToast(`已校正 [${lvl.name}] 電梯站點座標至實際座標換算位置 (${targetCanvas.x.toFixed(2)}, ${targetCanvas.y.toFixed(2)})`);
                                                      setTimeout(() => setSaveToast(null), 3000);
                                                    }}
                                                    className="text-[9px] bg-amber-950 text-amber-300 border border-amber-800/60 px-1 py-0.2 rounded hover:bg-amber-900"
                                                    title="以實際座標重新校正此站點在 Canvas 上的位置"
                                                  >
                                                    校正
                                                  </button>
                                                </div>
                                              );
                                            })()
                                          ) : (
                                            <button
                                              type="button"
                                              onClick={() => {
                                                const targetName = `${liftName}_${lvl.name}`;
                                                const newWp: Waypoint = {
                                                  id: `wp_${Date.now()}`,
                                                  name: targetName,
                                                  x: parseFloat(targetCanvas.x.toFixed(2)),
                                                  y: parseFloat(targetCanvas.y.toFixed(2)),
                                                  type: 'lift',
                                                  level: lvl.name,
                                                  lift: liftName,
                                                  graph_idx: selectedWaypoint.graph_idx ?? 0,
                                                };
                                                setWaypoints((prev) => [...prev, newWp]);
                                                setSaveToast(`已由實際座標換算在 ${lvl.name} 建立電梯點 [${targetName}] (Canvas: ${targetCanvas.x.toFixed(2)}, ${targetCanvas.y.toFixed(2)})`);
                                                setTimeout(() => setSaveToast(null), 3500);
                                              }}
                                              className="text-[10px] text-indigo-400 hover:text-indigo-300 bg-indigo-950/60 hover:bg-indigo-900/60 border border-indigo-800 px-1.5 py-0.5 rounded transition-all"
                                              title={`從實際座標 (${bldgCoord.x.toFixed(2)}, ${bldgCoord.y.toFixed(2)}) 經 ${lvl.name} 變換換算至 Canvas 座標 (${targetCanvas.x.toFixed(2)}, ${targetCanvas.y.toFixed(2)})`}
                                            >
                                              + 換算建立站點
                                            </button>
                                          )}
                                        </div>

                                        {/* Coordinate Breakdown */}
                                        <div className="text-[10px] text-slate-500 font-mono mt-0.5 flex justify-between">
                                          <span>Canvas: ({stationOnFloor ? `${stationOnFloor.x.toFixed(2)}, ${stationOnFloor.y.toFixed(2)}` : `${targetCanvas.x.toFixed(2)}, ${targetCanvas.y.toFixed(2)}`})</span>
                                          <span>實際: ({bldgCoord.x.toFixed(2)}, ${bldgCoord.y.toFixed(2)})</span>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  )}

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
                <PlantCameraList
                  plant={selectedPlant}
                  compact={true}
                  defaultExpanded={false}
                />
              )}

              {/* Fleet Summary (Collapsible Card matching PlantCameraList) */}
              <div className="bg-[#0d1424] border border-slate-800/80 hover:border-slate-700/80 rounded-xl overflow-hidden shadow-lg transition-all">
                {/* Header / Bar */}
                <div
                  onClick={toggleFleetExpanded}
                  className="flex items-center justify-between px-3 py-2 bg-[#0f182c] border-b border-slate-800/80 cursor-pointer select-none"
                  title={isFleetExpanded ? '收合車隊狀態' : '展開車隊狀態'}
                >
                  <div className="flex items-center space-x-2.5 min-w-0 flex-1 mr-2">
                    <div className="w-6 h-6 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center shrink-0">
                      <Radio className="w-3.5 h-3.5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-xs font-bold text-slate-100 truncate">
                          車隊即時狀態
                        </span>
                        <Badge
                          variant="outline"
                          className={`text-[10px] px-1.5 py-0 font-mono shrink-0 ${
                            hasEstoppedRobots
                              ? 'border-rose-600/60 text-rose-400 bg-rose-950/40 animate-pulse'
                              : 'border-emerald-600/40 text-emerald-400'
                          }`}
                        >
                          {robots.length} 台
                        </Badge>
                        {hasEstoppedRobots && (
                          <Badge variant="destructive" className="text-[9px] px-1.5 py-0 h-4">
                            {estoppedCount} 台急停
                          </Badge>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center space-x-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                    {robots.length > 0 && (
                      hasEstoppedRobots ? (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={isGlobalActionPending}
                          className="h-6 text-[10px] px-2 text-emerald-400 border-emerald-800 bg-emerald-950/40 hover:bg-emerald-900/60"
                          onClick={() => handleGlobalEmergencyStop('release')}
                          title="解除所有已急停的車輛"
                        >
                          <RotateCcw className="w-3 h-3 mr-1" />
                          解除全場
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="destructive"
                          disabled={isGlobalActionPending}
                          className="h-6 text-[10px] px-2 bg-rose-600/90 hover:bg-rose-600 text-white font-medium shadow-sm"
                          onClick={() => handleGlobalEmergencyStop('estop')}
                          title="對所有車輛發送緊急停車"
                        >
                          <OctagonAlert className="w-3 h-3 mr-1" />
                          全場急停
                        </Button>
                      )
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleFleetExpanded();
                      }}
                      className="text-slate-400 hover:text-white p-1 rounded-md hover:bg-slate-800 transition-colors"
                      title={isFleetExpanded ? '收合車隊狀態' : '展開車隊狀態'}
                    >
                      {isFleetExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {/* Fleet Content */}
                {isFleetExpanded && (
                  <div className="p-2.5">
                    {robots.length === 0 ? (
                      <div className="text-center py-4 px-2 border border-dashed border-slate-800/80 rounded-lg">
                        <Radio className="w-4 h-4 mx-auto text-slate-500 animate-pulse mb-1" />
                        <p className="text-xs text-slate-400 font-medium">目前尚無在線車輛</p>
                        <p className="text-[10px] text-slate-500 mt-0.5">車輛連線或啟動模擬後將自動列出</p>
                      </div>
                    ) : (
                      <div className="space-y-2.5">
                        {robots.map((robot) => {
                          const isEstopped = robot.emergency_stop || robot.status === 'emergency_stop';
                          const isOffGraph = Boolean(robot.is_off_graph || robot.status === 'off_graph');
                          const isWaiting = robot.status === 'waiting' || Boolean(robot.current_task && robot.current_task.includes('等待放行'));
                          const hasActiveTask = Boolean(
                            robot.task_id ||
                            (robot.current_task && robot.current_task !== 'nothing' && (robot.status === 'moving' || isWaiting))
                          );
                          return (
                            <Card
                              key={robot.id}
                              className={`bg-slate-900/60 border-slate-800 shadow-none transition-colors ${
                                isEstopped
                                  ? 'border-rose-800/80 bg-rose-950/20'
                                  : isOffGraph
                                  ? 'border-amber-500/60 bg-amber-950/20'
                                  : isWaiting
                                  ? 'border-amber-400/60 bg-amber-950/20'
                                  : ''
                              }`}
                            >
                              <CardContent className="p-3 space-y-2">
                                <div className="flex items-center justify-between">
                                  <span className="font-semibold text-xs text-slate-200 flex items-center space-x-1.5 min-w-0 mr-2">
                                    {isEstopped && <OctagonAlert className="w-3.5 h-3.5 text-rose-400 shrink-0" />}
                                    {!isEstopped && isOffGraph && <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />}
                                    {!isEstopped && !isOffGraph && isWaiting && <Clock className="w-3.5 h-3.5 text-amber-400 shrink-0 animate-pulse" />}
                                    <span className="truncate">{robot.name}</span>
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setActiveLevel(robot.level_name || DEFAULT_LEVEL);
                                      }}
                                      title={`點擊切換地圖至該車輛所在樓層 (${robot.level_name || DEFAULT_LEVEL})`}
                                      className={`text-[9px] px-1.5 py-0.2 rounded font-mono transition-colors shrink-0 ${
                                        (robot.level_name || DEFAULT_LEVEL) === currentLevel
                                          ? 'bg-cyan-950/80 text-cyan-300 border border-cyan-800/80 font-bold'
                                          : 'bg-slate-800 text-slate-400 border border-slate-700 hover:text-cyan-300 hover:border-cyan-700'
                                      }`}
                                    >
                                      {robot.level_name || DEFAULT_LEVEL}
                                    </button>
                                  </span>
                                  <Badge
                                    variant={
                                      isEstopped
                                        ? 'destructive'
                                        : isOffGraph
                                        ? 'warning'
                                        : isWaiting
                                        ? 'warning'
                                        : robot.status === 'moving'
                                        ? 'success'
                                        : robot.status === 'charging' || robot.status === 'paused'
                                        ? 'warning'
                                        : robot.status === 'offline'
                                        ? 'destructive'
                                        : 'secondary'
                                    }
                                    className={`text-[10px] uppercase font-mono ${
                                      isEstopped
                                        ? 'bg-rose-600 text-white animate-pulse'
                                        : isOffGraph
                                        ? 'bg-amber-600/30 text-amber-300 border-amber-500/50'
                                        : isWaiting
                                        ? 'bg-amber-500/30 text-amber-300 border-amber-400/50 animate-pulse'
                                        : ''
                                    }`}
                                  >
                                    {isEstopped
                                      ? 'EMERGENCY STOP'
                                      : isOffGraph
                                      ? `偏離路網 (${robot.off_graph_distance || '>3'}m)`
                                      : isWaiting
                                      ? 'WAITING (等待放行)'
                                      : robot.status === 'idle'
                                      ? 'IDLE'
                                      : robot.status.toUpperCase()}
                                  </Badge>
                                </div>
                                <div className="flex items-center justify-between text-[11px] text-slate-400">
                                  <span>車隊: {robot.fleet}</span>
                                  <span className={robot.battery < 30 ? 'text-rose-400 font-bold' : 'text-emerald-400'}>
                                    ⚡ {Math.round(robot.battery)}%
                                  </span>
                                </div>
                                <div
                                  className={`text-[10px] px-2 py-1 rounded ${
                                    isEstopped
                                      ? 'bg-rose-950/60 text-rose-300 border border-rose-900/50 truncate'
                                      : isOffGraph
                                      ? 'bg-amber-950/60 text-amber-200 border border-amber-600/50 whitespace-normal leading-relaxed'
                                      : 'bg-slate-950 text-slate-400 truncate'
                                  }`}
                                >
                                  {isEstopped ? (
                                    '🛑 緊急停車中（動作暫停等待解除）'
                                  ) : isOffGraph ? (
                                    <div className="space-y-0.5">
                                      <div className="font-semibold text-amber-300 flex items-center gap-1">
                                        <span>⚠️ 偏離路網過遠 ({robot.off_graph_distance ? `${robot.off_graph_distance}m` : `>${OFF_GRAPH_THRESHOLD_M}m`})</span>
                                      </div>
                                      <div className="text-[10px] text-amber-300/80">
                                        超出吸附上限 ({OFF_GRAPH_THRESHOLD_M}m)，無法操控接單。請手動移車或在路網編輯器中將路網連過去。
                                      </div>
                                    </div>
                                  ) : robot.current_task && robot.current_task !== 'nothing' && robot.status === 'moving' ? (
                                    robot.current_task
                                  ) : isWaiting ? (
                                    '⏸️ 步驟完成，等待操作員放行 (Waiting for Proceed)'
                                  ) : (
                                    '在線待命中'
                                  )}
                                </div>

                                {/* Emergency Stop / Release Action Row */}
                                <div className="pt-1.5 border-t border-slate-800/80 flex items-center justify-between">
                                  <span className="text-[10px] text-slate-500">
                                    {isEstopped ? '安全鎖定' : '單車控制'}
                                  </span>
                                  <div className="flex items-center space-x-1.5">
                                    {isEstopped ? (
                                      <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={actionPendingRobotId === robot.id}
                                        onClick={() => handleRobotEmergencyStop(robot.id, robot.fleet, 'release')}
                                        className="h-6 px-2 text-[10px] text-emerald-400 border-emerald-800 bg-emerald-950/40 hover:bg-emerald-900/60 hover:text-emerald-300"
                                      >
                                        <RotateCcw className="w-3 h-3 mr-1" />
                                        解除急停
                                      </Button>
                                    ) : (
                                      <>
                                        {isWaiting && (
                                          <Button
                                            size="sm"
                                            disabled={proceedPendingRobotId === robot.id}
                                            onClick={() => handleProceedTask(robot.id)}
                                            className="h-6 px-2 text-[10px] bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-sm animate-pulse"
                                            title="放行並繼續執行多步任務的下一個步驟"
                                          >
                                            {proceedPendingRobotId === robot.id ? (
                                              <span className="text-[9px]">放行中...</span>
                                            ) : (
                                              <>
                                                <Play className="w-3 h-3 mr-1 fill-white" />
                                                放行 (Proceed)
                                              </>
                                            )}
                                          </Button>
                                        )}
                                        {hasActiveTask && (
                                          <Button
                                            size="sm"
                                            variant="ghost"
                                            disabled={actionPendingRobotId === robot.id}
                                            onClick={() => handleRobotEmergencyStop(robot.id, robot.fleet, 'estop', true)}
                                            className="h-6 px-1.5 text-[10px] text-rose-400 hover:text-rose-300 hover:bg-rose-950/40 border border-rose-900/40"
                                            title="緊急停車並同時取消當前任務"
                                          >
                                            急停+取消
                                          </Button>
                                        )}
                                        <Button
                                          size="sm"
                                          variant="destructive"
                                          disabled={actionPendingRobotId === robot.id}
                                          onClick={() => handleRobotEmergencyStop(robot.id, robot.fleet, 'estop', false)}
                                          className="h-6 px-2 text-[10px] bg-rose-600/90 hover:bg-rose-600 text-white font-medium shadow-sm"
                                          title="緊急停車（暫停動作並保留任務）"
                                        >
                                          <OctagonAlert className="w-3 h-3 mr-1" />
                                          緊急停車
                                        </Button>
                                      </>
                                    )}
                                  </div>
                                </div>
                              </CardContent>
                            </Card>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Tasks Queue (Collapsible Card matching PlantCameraList) */}
              <div className="bg-[#0d1424] border border-slate-800/80 hover:border-slate-700/80 rounded-xl overflow-hidden shadow-lg transition-all">
                {/* Header / Bar */}
                <div
                  onClick={toggleTasksExpanded}
                  className="flex items-center justify-between px-3 py-2 bg-[#0f182c] border-b border-slate-800/80 cursor-pointer select-none"
                  title={isTasksExpanded ? '收合調度任務佇列' : '展開調度任務佇列'}
                >
                  <div className="flex items-center space-x-2.5 min-w-0 flex-1 mr-2">
                    <div className="w-6 h-6 rounded-lg bg-primary/20 text-primary flex items-center justify-center shrink-0">
                      <Send className="w-3.5 h-3.5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-xs font-bold text-slate-100 truncate">
                          調度任務佇列
                        </span>
                        <Badge
                          variant="outline"
                          className="text-[10px] px-1.5 py-0 border-primary/40 text-primary font-mono shrink-0"
                        >
                          {tasks.length} 筆
                        </Badge>
                        {tasks.filter((t) => t.status === 'active').length > 0 && (
                          <Badge variant="default" className="text-[9px] px-1.5 py-0 h-4">
                            {tasks.filter((t) => t.status === 'active').length} 執行中
                          </Badge>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center space-x-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleTasksExpanded();
                      }}
                      className="text-slate-400 hover:text-white p-1 rounded-md hover:bg-slate-800 transition-colors"
                      title={isTasksExpanded ? '收合調度任務佇列' : '展開調度任務佇列'}
                    >
                      {isTasksExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {/* Tasks Content */}
                {isTasksExpanded && (
                  <div className="p-2.5">
                    {tasks.length === 0 ? (
                      <div className="text-center py-4 px-2 border border-dashed border-slate-800/80 rounded-lg">
                        <Send className="w-4 h-4 mx-auto text-slate-500 mb-1" />
                        <p className="text-xs text-slate-400 font-medium">目前尚無調度任務</p>
                        <p className="text-[10px] text-slate-500 mt-0.5">點擊地圖上的任意站點即可快速派遣任務</p>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {tasks.map((task) => {
                          const isWaiting = task.status === 'waiting' || Boolean(task.detail && task.detail.includes('等待放行'));
                          const isCancellable = task.status === 'active' || task.status === 'queued' || isWaiting;
                          const isMultiStep = task.type === 'multi_step' || task.type === 'compose';
                          return (
                            <div
                              key={task.id}
                              className={`bg-slate-900/40 border p-2.5 rounded-lg text-xs space-y-1.5 ${
                                task.status === 'canceled'
                                  ? 'border-rose-900/40 bg-rose-950/10'
                                  : isWaiting
                                  ? 'border-amber-500/50 bg-amber-950/20'
                                  : 'border-slate-800/80'
                              }`}
                            >
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-1.5 min-w-0">
                                  {isMultiStep && (
                                    <Badge variant="outline" className="text-[9px] px-1 py-0 border-indigo-500/40 text-indigo-300 shrink-0 font-mono">
                                      多步
                                    </Badge>
                                  )}
                                  {task.type === 'patrol' && (
                                    <Badge variant="outline" className="text-[9px] px-1 py-0 border-primary/40 text-primary shrink-0 font-mono">
                                      設定 {task.rounds ?? 1} 輪
                                    </Badge>
                                  )}
                                  <span className="font-medium text-slate-300 capitalize truncate max-w-[130px]" title={task.target_waypoint}>
                                    {task.type}: {task.target_waypoint}
                                  </span>
                                </div>
                                <div className="flex items-center space-x-1.5">
                                  <Badge
                                    variant={
                                      task.status === 'completed'
                                        ? 'success'
                                        : task.status === 'active'
                                        ? 'default'
                                        : isWaiting
                                        ? 'warning'
                                        : task.status === 'failed' || task.status === 'canceled'
                                        ? 'destructive'
                                        : 'outline'
                                    }
                                    className={`text-[10px] ${isWaiting ? 'bg-amber-500/30 text-amber-300 border-amber-400/50 animate-pulse' : ''}`}
                                  >
                                    {task.status === 'canceled' ? '已取消' : isWaiting ? '等待放行' : task.status}
                                  </Badge>
                                  {isWaiting && task.robot_id && (
                                    <Button
                                      size="sm"
                                      disabled={proceedPendingRobotId === task.robot_id}
                                      onClick={() => handleProceedTask(task.robot_id!, task.cmd_id)}
                                      className="h-5 px-1.5 text-[10px] bg-emerald-600 hover:bg-emerald-500 text-white animate-pulse"
                                      title="放行並執行下一步驟"
                                    >
                                      <Play className="w-2.5 h-2.5 mr-0.5 fill-white" />
                                      放行
                                    </Button>
                                  )}
                                  {isCancellable && (
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      disabled={cancelingTaskId === task.id}
                                      onClick={() => handleCancelTask(task.id)}
                                      className="h-5 px-1.5 text-[10px] text-rose-400 hover:text-rose-300 hover:bg-rose-950/40 border border-rose-900/50"
                                      title="取消此項任務"
                                    >
                                      {cancelingTaskId === task.id ? (
                                        <span className="text-[9px]">取消中...</span>
                                      ) : (
                                        <>
                                          <Ban className="w-2.5 h-2.5 mr-1" />
                                          取消
                                        </>
                                      )}
                                    </Button>
                                  )}
                                </div>
                              </div>
                              {/* Progress bar */}
                              <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                                <div
                                  className={`h-full transition-all duration-300 ${
                                    task.status === 'canceled'
                                      ? 'bg-rose-500'
                                      : isWaiting
                                      ? 'bg-amber-400'
                                      : task.type === 'patrol' && task.status === 'active'
                                      ? 'bg-primary/80 animate-pulse'
                                      : 'bg-primary'
                                  }`}
                                  style={{
                                    width:
                                      task.type === 'patrol' && task.status === 'active'
                                        ? `${Math.max(15, task.progress)}%`
                                        : `${task.progress}%`,
                                  }}
                                />
                              </div>
                              <div className="flex justify-between text-[10px] text-slate-500">
                                <span>車輛: {task.robot_id || '自動'}</span>
                                {task.total_steps && task.total_steps > 1 ? (
                                  <span className="text-indigo-400/90 font-mono">
                                    步驟 {task.status === 'completed' ? task.total_steps : ((task.current_step ?? 0) + 1)} / {task.total_steps} ({task.progress}%)
                                  </span>
                                ) : task.type === 'patrol' && task.status === 'active' ? (
                                  <span className="text-primary font-medium">
                                    巡邏執行中
                                  </span>
                                ) : (
                                  <span>{task.progress}%</span>
                                )}
                              </div>
                              {task.detail && (
                                <div className={`text-[10px] px-1.5 py-0.5 rounded truncate ${isWaiting ? 'text-amber-300 bg-amber-950/40 border border-amber-500/30' : 'text-slate-400 bg-slate-950/50'}`}>
                                  {task.detail}
                                </div>
                              )}
                              {task.status === 'failed' && (task.error || task.detail) && (
                                <div className="flex items-start gap-1 text-[10px] text-rose-300 bg-rose-950/30 border border-rose-900/40 rounded px-1.5 py-1 break-words">
                                  <AlertTriangle className="w-3 h-3 mt-px flex-shrink-0" />
                                  <span>{task.error || task.detail}</span>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
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
        onOpenMultiStep={() => setMultiStepModalOpen(true)}
      />

      {/* Multi-Step Task Modal */}
      <MultiStepTaskModal
        open={multiStepModalOpen}
        onOpenChange={setMultiStepModalOpen}
        waypoints={waypoints}
        robots={robots}
        initialWaypoint={dispatchPresetWp}
        onDispatch={handleDispatchMultiStepTask}
      />

      {/* Import SLAM Map Modal */}
      <ImportMapModal
        open={importModalOpen}
        onOpenChange={setImportModalOpen}
        currentLevel={currentLevel}
        levels={levels}
        onMapLoaded={(newMap, targetLevel) => {
          const floor = targetLevel || currentLevel;
          setFloorSlamMaps((prev) => {
            const next = { ...prev, [floor]: newMap };
            try {
              localStorage.setItem('rmf_floor_slam_maps', JSON.stringify(next));
              if (floor === currentLevel) {
                localStorage.setItem('rmf_active_slam_map', JSON.stringify(newMap));
              }
            } catch (e) {}
            return next;
          });
          setSaveToast(`已成功載入樓層 [${floor}] 的 SLAM 底圖`);
          setTimeout(() => setSaveToast(null), 3000);
        }}
      />



      {/* Visual Multi-Floor Map Overlay Aligner Modal */}
      <MapOverlayAlignModal
        open={overlayAlignModalOpen}
        onOpenChange={setOverlayAlignModalOpen}
        levels={levels}
        currentLevelName={currentLevel}
        floorSlamMaps={floorSlamMaps}
        waypoints={waypoints}
        lifts={activeLifts}
        onSaveLevelTransform={handleSaveLevelTransform}
      />

      {/* Floor / Level Definition Modal */}
      <LevelModal
        open={levelModalOpen}
        onOpenChange={setLevelModalOpen}
        mode={levelModalMode}
        initialLevel={editingLevel}
        existingLevels={levels}
        onSave={handleSaveLevelDefinition}
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
