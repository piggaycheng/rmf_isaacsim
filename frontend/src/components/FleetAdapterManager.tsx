import React, { useState, useEffect } from 'react';
import { FleetAdapter, NavGraph, Robot, GRAPH_PALETTE } from '@/types/rmf';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Input } from './ui/input';
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from './ui/dialog';
import {
  Cpu,
  Plus,
  RotateCcw,
  Play,
  Pause,
  Trash2,
  Terminal,
  FileCode,
  Sliders,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Radio,
  Download,
  Copy,
  Check,
  Search,
  Zap,
  ArrowRightLeft,
  Server,
  Layers,
  BatteryCharging,
  Info,
} from 'lucide-react';

interface FleetAdapterManagerProps {
  graphs: NavGraph[];
  robots: Robot[];
  onOpenIsaacSim?: () => void;
}

export const FleetAdapterManager: React.FC<FleetAdapterManagerProps> = ({
  graphs,
  robots,
}) => {
  const [adapters, setAdapters] = useState<FleetAdapter[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedGraphFilter, setSelectedGraphFilter] = useState<string>('all');

  // Modal States
  const [createEditModalOpen, setCreateEditModalOpen] = useState<boolean>(false);
  const [editingAdapter, setEditingAdapter] = useState<FleetAdapter | null>(null);

  const [logsModalOpen, setLogsModalOpen] = useState<boolean>(false);
  const [logsAdapter, setLogsAdapter] = useState<FleetAdapter | null>(null);

  const [yamlModalOpen, setYamlModalOpen] = useState<boolean>(false);
  const [yamlAdapter, setYamlAdapter] = useState<FleetAdapter | null>(null);
  const [copiedYaml, setCopiedYaml] = useState<boolean>(false);

  // Dynamic set_graph feedback toast
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

  // Form State for Create/Edit
  const [formData, setFormData] = useState<Partial<FleetAdapter>>({
    name: '',
    fleet_name: '',
    adapter_type: 'easy_full_control',
    status: 'online',
    graph_idx: 0,
    ros2_domain_id: 0,
    linear_velocity: 1.2,
    angular_velocity: 1.0,
    robot_radius: 0.35,
    recharge_threshold: 20,
    recharge_target: 90,
    robots: [],
  });
  const [robotsInputStr, setRobotsInputStr] = useState<string>('');

  // Fetch adapters from server
  const fetchAdapters = async () => {
    try {
      const res = await fetch('/api/fleet-adapters');
      if (res.ok) {
        const data = await res.json();
        setAdapters(data);
      }
    } catch (e) {
      console.warn('Failed to fetch fleet adapters, using fallback:', e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchAdapters();
  }, []);

  const showToast = (msg: string) => {
    setActionFeedback(msg);
    setTimeout(() => setActionFeedback(null), 4000);
  };

  // Open Edit Modal
  const handleOpenEdit = (adapter: FleetAdapter) => {
    setEditingAdapter(adapter);
    setFormData({ ...adapter });
    setRobotsInputStr(adapter.robots.join(', '));
    setCreateEditModalOpen(true);
  };

  // Open Create Modal
  const handleOpenCreate = () => {
    setEditingAdapter(null);
    const nextId = `adapter_${Date.now()}`;
    setFormData({
      id: nextId,
      name: `FleetAdapter_${adapters.length + 1}`,
      fleet_name: `fleet_${adapters.length + 1}`,
      adapter_type: 'easy_full_control',
      status: 'online',
      graph_idx: 0,
      ros2_domain_id: 0,
      linear_velocity: 1.2,
      angular_velocity: 1.0,
      robot_radius: 0.4,
      recharge_threshold: 20,
      recharge_target: 90,
      robots: [],
      latency_ms: 15,
      updated_at: '即時連線中',
      logs: [
        `[INFO] [easy_full_control]: Fleet adapter created on ROS 2 Domain 0`,
        `[INFO] [easy_full_control]: Ready to connect to Open-RMF Core`,
      ],
    });
    setRobotsInputStr('');
    setCreateEditModalOpen(true);
  };

  // Submit Create or Edit
  const handleSaveAdapter = async (e: React.FormEvent) => {
    e.preventDefault();
    const parsedRobots = robotsInputStr
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s.length > 0);

    const adapterToSave: FleetAdapter = {
      id: editingAdapter ? editingAdapter.id : (formData.id || `adapter_${Date.now()}`),
      name: formData.name || 'Unnamed Adapter',
      fleet_name: formData.fleet_name || 'unnamed_fleet',
      adapter_type: formData.adapter_type || 'easy_full_control',
      status: formData.status || 'online',
      graph_idx: formData.graph_idx ?? 0,
      ros2_domain_id: formData.ros2_domain_id ?? 0,
      linear_velocity: Number(formData.linear_velocity) || 1.0,
      angular_velocity: Number(formData.angular_velocity) || 1.0,
      robot_radius: Number(formData.robot_radius) || 0.4,
      recharge_threshold: Number(formData.recharge_threshold) || 20,
      recharge_target: Number(formData.recharge_target) || 90,
      default_charger: formData.default_charger || 'charger_1',
      default_parking: formData.default_parking || 'parking_1',
      robots: parsedRobots,
      latency_ms: formData.latency_ms ?? 14,
      updated_at: '已儲存',
      logs: editingAdapter?.logs || [
        `[INFO] [adapter]: Registered ${formData.fleet_name} adapter`,
      ],
    };

    try {
      const res = await fetch('/api/fleet-adapters', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(adapterToSave),
      });
      if (res.ok) {
        showToast(`Fleet Adapter '${adapterToSave.name}' 已成功儲存！`);
        fetchAdapters();
      }
    } catch (e) {
      console.error(e);
      // Local fallback
      setAdapters((prev) => {
        const idx = prev.findIndex((a) => a.id === adapterToSave.id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = adapterToSave;
          return next;
        }
        return [...prev, adapterToSave];
      });
      showToast(`Fleet Adapter '${adapterToSave.name}' 已儲存 (本地)`);
    }

    setCreateEditModalOpen(false);
  };

  // Delete Adapter
  const handleDeleteAdapter = async (id: string, name: string) => {
    if (!window.confirm(`確定要刪除「${name}」嗎？此操作將解除該車隊與 RMF Core 的連線。`)) {
      return;
    }
    try {
      const res = await fetch(`/api/fleet-adapters/${id}`, { method: 'DELETE' });
      if (res.ok) {
        showToast(`已成功移除 Adapter '${name}'`);
        fetchAdapters();
      }
    } catch (e) {
      setAdapters((prev) => prev.filter((a) => a.id !== id));
      showToast(`已移除 Adapter '${name}'`);
    }
  };

  // Dynamic set_graph
  const handleDynamicSetGraph = async (adapterId: string, newGraphIdx: number) => {
    try {
      const res = await fetch(`/api/fleet-adapters/${adapterId}/set-graph`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph_idx: newGraphIdx }),
      });
      const data = await res.json();
      if (res.ok) {
        showToast(data.message || `已成功切換至 Graph ${newGraphIdx}！(免重啟熱更新)`);
        fetchAdapters();
      }
    } catch (e) {
      setAdapters((prev) =>
        prev.map((a) => (a.id === adapterId ? { ...a, graph_idx: newGraphIdx } : a))
      );
      showToast(`車隊已切換至 Graph ${newGraphIdx} (免重啟)`);
    }
  };

  // Action (Restart / Pause / Resume)
  const handleAdapterAction = async (adapterId: string, action: 'restart' | 'pause' | 'resume') => {
    try {
      const res = await fetch(`/api/fleet-adapters/${adapterId}/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (res.ok) {
        const actionLabels = { restart: '重新載入服務', pause: '已暫停', resume: '已恢復連線' };
        showToast(`Adapter ${actionLabels[action]}`);
        fetchAdapters();
      }
    } catch (e) {
      showToast(`執行指令: ${action}`);
    }
  };

  // Generate Open-RMF YAML Configuration
  const generateYamlConfig = (a: FleetAdapter) => {
    return `# =======================================================
# Open-RMF Fleet Adapter Configuration
# Generated by RMF Web Studio (ROS 2 Jazzy Compatible)
# =======================================================

rmf_fleet:
  name: "${a.fleet_name}"
  adapter_type: "${a.adapter_type}" # easy_full_control | full_control | read_and_request
  ros2:
    domain_id: ${a.ros2_domain_id}
    node_name: "${a.fleet_name}_adapter"
  
  # Navigation Graph settings (Supports dynamic set_graph)
  nav_graph:
    default_graph_idx: ${a.graph_idx}
    graph_file: "nav_graphs/${a.graph_idx}.yaml"

  # Kinematic & Motion limits
  limits:
    linear_velocity: ${a.linear_velocity} # m/s
    linear_acceleration: ${(a.linear_velocity * 0.5).toFixed(2)} # m/s^2
    angular_velocity: ${a.angular_velocity} # rad/s
    angular_acceleration: ${(a.angular_velocity * 0.5).toFixed(2)} # rad/s^2

  # Footprint & Physical Specs
  profile:
    footprint_radius: ${a.robot_radius} # meters
    vicinity_radius: ${(a.robot_radius + 0.3).toFixed(2)} # meters
    reversible: true

  # Battery & Autonomous Charging Policy
  recharge:
    auto_recharge: true
    threshold: ${(a.recharge_threshold / 100).toFixed(2)} # ${a.recharge_threshold}%
    target: ${(a.recharge_target / 100).toFixed(2)} # ${a.recharge_target}%
    default_charger_waypoint: "${a.default_charger || 'charger_1'}"
    default_parking_waypoint: "${a.default_parking || 'parking_1'}"

  # Managed Robot Fleet
  robots:
${a.robots.map((r) => `    - name: "${r}"\n      model: "amr_standard"`).join('\n')}
`;
  };

  // Filtered adapters
  const filteredAdapters = adapters.filter((a) => {
    const matchesSearch =
      a.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      a.fleet_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      a.robots.some((r) => r.toLowerCase().includes(searchQuery.toLowerCase()));
    const matchesGraph =
      selectedGraphFilter === 'all' || a.graph_idx === parseInt(selectedGraphFilter, 10);
    return matchesSearch && matchesGraph;
  });

  return (
    <div className="flex-1 flex flex-col bg-[#0b0f19] text-slate-100 overflow-y-auto p-6 space-y-6">
      {/* Top Header & Actions */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-5">
        <div>
          <div className="flex items-center space-x-2.5">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-cyan-600 to-blue-500 flex items-center justify-center text-white shadow-lg shadow-cyan-950/50">
              <Cpu className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h1 className="text-xl font-bold tracking-tight text-slate-100">
                  Fleet Adapter 車隊適配器管理
                </h1>
                <Badge className="bg-primary/20 text-primary border-primary/30 text-[10px]">
                  Jazzy EasyFullControl
                </Badge>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                管理與設定各 AMR/AGV 車隊的 Open-RMF 適配器，支援即時動態熱更換導航路網 (set_graph)
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center space-x-2.5">
          <Button
            size="sm"
            variant="outline"
            onClick={fetchAdapters}
            className="text-xs h-9 bg-slate-900 border-slate-800 hover:bg-slate-800 text-slate-300"
          >
            <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
            重新整理由伺服器載入
          </Button>

          <Button
            size="sm"
            onClick={handleOpenCreate}
            className="text-xs h-9 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold shadow-md shadow-primary/20"
          >
            <Plus className="w-3.5 h-3.5 mr-1.5" />
            新增 Fleet Adapter
          </Button>
        </div>
      </div>

      {/* Action Notification Toast */}
      {actionFeedback && (
        <div className="bg-emerald-950/80 border border-emerald-800/80 text-emerald-200 text-xs px-4 py-2.5 rounded-xl shadow-lg flex items-center justify-between animate-in fade-in slide-in-from-top-2">
          <div className="flex items-center space-x-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            <span className="font-medium">{actionFeedback}</span>
          </div>
          <button
            onClick={() => setActionFeedback(null)}
            className="text-emerald-400 hover:text-emerald-200 text-xs"
          >
            關閉
          </button>
        </div>
      )}

      {/* KPI Stats Overview */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-1">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>納管車隊總數</span>
            <Server className="w-4 h-4 text-cyan-400" />
          </div>
          <div className="text-2xl font-bold text-slate-100">{adapters.length} <span className="text-xs font-normal text-slate-400">組 Adapters</span></div>
          <p className="text-[11px] text-slate-500">
            {adapters.filter((a) => a.status === 'online').length} 組在線連線中
          </p>
        </div>

        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-1">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>納管機器人數量</span>
            <Radio className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-2xl font-bold text-slate-100">
            {adapters.reduce((acc, a) => acc + a.robots.length, 0)}{' '}
            <span className="text-xs font-normal text-slate-400">台 AMR / AGV</span>
          </div>
          <p className="text-[11px] text-slate-500">
            即時位置與電量由 RMF Core 協調
          </p>
        </div>

        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-1">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>可用導航路網</span>
            <Layers className="w-4 h-4 text-purple-400" />
          </div>
          <div className="text-2xl font-bold text-slate-100">{graphs.length} <span className="text-xs font-normal text-slate-400">組 Nav Graphs</span></div>
          <p className="text-[11px] text-slate-500">
            Graph 0 ~ Graph {Math.max(0, graphs.length - 1)} 可供各車隊指派
          </p>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-slate-900/40 border border-slate-800 p-2.5 rounded-xl">
        <div className="relative flex-1 max-w-sm">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜尋 Adapter 名稱、車隊或車輛 ID..."
            className="pl-9 h-8 text-xs bg-slate-950 border-slate-800 text-slate-200"
          />
        </div>

        <div className="flex items-center space-x-2 text-xs">
          <span className="text-slate-400 text-[11px]">指派路網篩選：</span>
          <select
            value={selectedGraphFilter}
            onChange={(e) => setSelectedGraphFilter(e.target.value)}
            className="bg-slate-950 border border-slate-800 rounded px-2.5 py-1 text-xs text-slate-200"
          >
            <option value="all">全部路網 ({graphs.length} 組)</option>
            {graphs.map((g) => (
              <option key={g.id} value={g.id.toString()}>
                {g.name} (Graph {g.id})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Adapters Grid Cards */}
      {isLoading ? (
        <div className="py-16 text-center text-slate-500 text-xs">
          正在從伺服器讀取 Fleet Adapter 設定...
        </div>
      ) : filteredAdapters.length === 0 ? (
        <div className="py-16 text-center border border-dashed border-slate-800 rounded-2xl space-y-2">
          <p className="text-slate-400 text-sm font-medium">目前沒有符合條件的 Fleet Adapter</p>
          <p className="text-xs text-slate-500">點擊右上角「新增 Fleet Adapter」按鈕建立新車隊適配器。</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
          {filteredAdapters.map((adapter) => {
            const currentGraph = graphs.find((g) => g.id === adapter.graph_idx);
            const graphColor =
              GRAPH_PALETTE[(adapter.graph_idx ?? 0) % GRAPH_PALETTE.length];
            const isEasyFull = adapter.adapter_type === 'easy_full_control';

            return (
              <div
                key={adapter.id}
                className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 space-y-4 hover:border-slate-700 transition-all shadow-md"
              >
                {/* Adapter Card Header */}
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center space-x-2">
                      <span className="font-bold text-base text-slate-100">{adapter.name}</span>
                      <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 border border-slate-700">
                        fleet: {adapter.fleet_name}
                      </span>
                    </div>

                    <div className="flex items-center space-x-2 text-xs">
                      <Badge className="bg-amber-500/15 text-amber-400 border-amber-500/30 text-[10px] flex items-center space-x-1">
                        <Zap className="w-3 h-3 mr-0.5" />
                        <span>EasyFullControl (Jazzy)</span>
                      </Badge>

                      <span className="text-[11px] text-slate-400">
                        Domain ID: <span className="font-mono text-slate-200">{adapter.ros2_domain_id}</span>
                      </span>
                    </div>
                  </div>

                  {/* Status Indicator */}
                  <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded-full bg-slate-950/80 border border-slate-800 text-[11px]">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        adapter.status === 'online'
                          ? 'bg-emerald-400 animate-pulse'
                          : adapter.status === 'standby'
                          ? 'bg-amber-400'
                          : 'bg-rose-400'
                      }`}
                    />
                    <span
                      className={`capitalize font-medium ${
                        adapter.status === 'online'
                          ? 'text-emerald-400'
                          : adapter.status === 'standby'
                          ? 'text-amber-400'
                          : 'text-rose-400'
                      }`}
                    >
                      {adapter.status}
                    </span>
                    {adapter.latency_ms && (
                      <span className="text-slate-500 text-[10px]">({adapter.latency_ms}ms)</span>
                    )}
                  </div>
                </div>

                {/* Section: Associated Nav Graph & Dynamic set_graph */}
                <div className="bg-slate-950/70 border border-slate-800/80 rounded-xl p-3.5 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-slate-300 flex items-center space-x-1.5">
                      <Layers className="w-3.5 h-3.5 text-primary" />
                      <span>目前指派導航路網 (Nav Graph)</span>
                    </span>
                    <div className="flex items-center space-x-1.5">
                      <span
                        className="w-2.5 h-2.5 rounded-full shadow-sm"
                        style={{ backgroundColor: graphColor }}
                      />
                      <span className="text-xs font-bold text-slate-200">
                        Graph {adapter.graph_idx} ({currentGraph?.name || '自訂路網'})
                      </span>
                    </div>
                  </div>

                  {/* Dynamic set_graph selector & action */}
                  <div className="flex items-center space-x-2 pt-1 border-t border-slate-800/60">
                    <span className="text-[11px] text-slate-400 shrink-0">動態更換路網：</span>
                    <select
                      value={adapter.graph_idx}
                      onChange={(e) => {
                        const targetIdx = parseInt(e.target.value, 10);
                        if (!isNaN(targetIdx)) {
                          handleDynamicSetGraph(adapter.id, targetIdx);
                        }
                      }}
                      className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-slate-200"
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
                      className="h-7 text-xs bg-cyan-950/50 hover:bg-cyan-900/60 text-cyan-300 border border-cyan-800/50"
                      onClick={() => handleDynamicSetGraph(adapter.id, adapter.graph_idx)}
                      title="發送 set_graph 指令至 Fleet Adapter"
                    >
                      <ArrowRightLeft className="w-3 h-3 mr-1" />
                      set_graph
                    </Button>
                  </div>

                  {isEasyFull && (
                    <p className="text-[10px] text-amber-400/90 leading-tight">
                      💡 Jazzy EasyFullControl 原生支援動態路網熱更換，車隊執行此指令無須重啟節點即可直接套用新圖層。
                    </p>
                  )}
                </div>

                {/* Section: Kinematic Specs & Recharge Policy */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                  <div className="bg-slate-950/40 border border-slate-800/60 rounded-lg p-2">
                    <span className="text-[10px] text-slate-500 block">最大線速度</span>
                    <span className="font-mono font-bold text-slate-200">{adapter.linear_velocity}</span>{' '}
                    <span className="text-[10px] text-slate-400">m/s</span>
                  </div>

                  <div className="bg-slate-950/40 border border-slate-800/60 rounded-lg p-2">
                    <span className="text-[10px] text-slate-500 block">最大角速度</span>
                    <span className="font-mono font-bold text-slate-200">{adapter.angular_velocity}</span>{' '}
                    <span className="text-[10px] text-slate-400">rad/s</span>
                  </div>

                  <div className="bg-slate-950/40 border border-slate-800/60 rounded-lg p-2">
                    <span className="text-[10px] text-slate-500 block">車體半徑</span>
                    <span className="font-mono font-bold text-slate-200">{adapter.robot_radius}</span>{' '}
                    <span className="text-[10px] text-slate-400">m</span>
                  </div>

                  <div className="bg-slate-950/40 border border-slate-800/60 rounded-lg p-2">
                    <span className="text-[10px] text-slate-500 block">自動回充策略</span>
                    <span className="font-mono font-bold text-emerald-400">
                      {adapter.recharge_threshold}% ➔ {adapter.recharge_target}%
                    </span>
                  </div>
                </div>

                {/* Section: Managed Robots List */}
                <div className="space-y-1.5">
                  <span className="text-[11px] font-medium text-slate-400 flex items-center justify-between">
                    <span>納管車輛 ({adapter.robots.length} 台)</span>
                    <span className="text-[10px] text-slate-500">
                      預設充電樁: {adapter.default_charger || 'charger_1'}
                    </span>
                  </span>

                  <div className="flex flex-wrap gap-1.5">
                    {adapter.robots.length === 0 ? (
                      <span className="text-[11px] text-slate-500 italic">尚無納管車輛</span>
                    ) : (
                      adapter.robots.map((robotId) => {
                        const liveRobot = robots.find((r) => r.id === robotId);
                        return (
                          <div
                            key={robotId}
                            className="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg bg-slate-950 border border-slate-800 text-xs"
                          >
                            <span
                              className={`w-1.5 h-1.5 rounded-full ${
                                liveRobot?.status === 'moving'
                                  ? 'bg-cyan-400 animate-pulse'
                                  : liveRobot?.status === 'charging'
                                  ? 'bg-amber-400'
                                  : 'bg-emerald-400'
                              }`}
                            />
                            <span className="font-semibold text-slate-200">{robotId}</span>
                            {liveRobot && (
                              <span className="text-[10px] text-slate-400 flex items-center ml-1">
                                <BatteryCharging className="w-2.5 h-2.5 mr-0.5 text-emerald-400" />
                                {Math.round(liveRobot.battery)}%
                              </span>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>

                {/* Section: Action Buttons */}
                <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-800">
                  <div className="flex items-center space-x-1.5">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-xs bg-slate-900 border-slate-800 text-slate-300 hover:text-white"
                      onClick={() => handleOpenEdit(adapter)}
                    >
                      <Sliders className="w-3 h-3 mr-1" />
                      編輯設定
                    </Button>

                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-xs bg-slate-900 border-slate-800 text-slate-300 hover:text-white"
                      onClick={() => {
                        setLogsAdapter(adapter);
                        setLogsModalOpen(true);
                      }}
                    >
                      <Terminal className="w-3 h-3 mr-1 text-cyan-400" />
                      即時日誌
                    </Button>

                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-xs bg-slate-900 border-slate-800 text-slate-300 hover:text-white"
                      onClick={() => {
                        setYamlAdapter(adapter);
                        setYamlModalOpen(true);
                      }}
                      title="檢視並下載 Open-RMF 格式的車隊適配器設定檔"
                    >
                      <FileCode className="w-3 h-3 mr-1 text-amber-400" />
                      匯出 YAML
                    </Button>
                  </div>

                  <div className="flex items-center space-x-1.5">
                    {adapter.status === 'online' ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 text-xs text-amber-400 hover:text-amber-300 hover:bg-amber-950/30"
                        onClick={() => handleAdapterAction(adapter.id, 'pause')}
                        title="暫停此車隊適配器"
                      >
                        <Pause className="w-3 h-3 mr-1" />
                        暫停
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 text-xs text-emerald-400 hover:text-emerald-300 hover:bg-emerald-950/30"
                        onClick={() => handleAdapterAction(adapter.id, 'resume')}
                        title="啟動/恢復此車隊適配器"
                      >
                        <Play className="w-3 h-3 mr-1" />
                        啟動
                      </Button>
                    )}

                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 text-xs text-rose-400 hover:text-rose-300 hover:bg-rose-950/30"
                      onClick={() => handleDeleteAdapter(adapter.id, adapter.name)}
                      title="刪除此 Adapter"
                    >
                      <Trash2 className="w-3 h-3" />
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 1. Modal: Create / Edit Adapter */}
      <Dialog open={createEditModalOpen} onOpenChange={setCreateEditModalOpen}>
        <DialogHeader>
          <DialogTitle className="flex items-center space-x-2 text-base font-bold">
            <Cpu className="w-4 h-4 text-primary" />
            <span>{editingAdapter ? `編輯 Fleet Adapter (${editingAdapter.name})` : '新增 Fleet Adapter'}</span>
          </DialogTitle>
          <DialogDescription className="text-xs text-slate-400">
            設定車隊名稱、所屬導航路網、適配器類型與運動學參數。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSaveAdapter} className="space-y-4 py-2">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-[11px] text-slate-400 block mb-1">適配器名稱 (Display Name)</label>
              <Input
                required
                value={formData.name || ''}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="例如：Forklift EasyControl"
                className="h-8 text-xs bg-slate-950 border-slate-800"
              />
            </div>

            <div>
              <label className="text-[11px] text-slate-400 block mb-1">車隊名稱 (Fleet Name)</label>
              <Input
                required
                value={formData.fleet_name || ''}
                onChange={(e) => setFormData({ ...formData, fleet_name: e.target.value })}
                placeholder="例如：forkliftFleet"
                className="h-8 text-xs bg-slate-950 border-slate-800"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-[11px] text-slate-400 block mb-1">適配器架構標準</label>
              <div className="h-8 flex items-center px-2.5 bg-slate-950 border border-slate-800 rounded-md text-xs text-amber-400 font-medium space-x-1.5">
                <Zap className="w-3.5 h-3.5 text-amber-400" />
                <span>EasyFullControl (Jazzy 原生動態路網)</span>
              </div>
            </div>

            <div>
              <label className="text-[11px] text-slate-400 block mb-1">指派導航路網 (Nav Graph)</label>
              <select
                value={formData.graph_idx}
                onChange={(e) => setFormData({ ...formData, graph_idx: parseInt(e.target.value, 10) })}
                className="w-full bg-slate-950 border border-slate-800 rounded-md px-2.5 py-1.5 text-xs text-slate-200"
              >
                {graphs.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} (Graph {g.id})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-[11px] text-slate-400 block mb-1">最大速度 (m/s)</label>
              <Input
                type="number"
                step="0.1"
                value={formData.linear_velocity}
                onChange={(e) => setFormData({ ...formData, linear_velocity: parseFloat(e.target.value) || 0 })}
                className="h-8 text-xs bg-slate-950 border-slate-800"
              />
            </div>
            <div>
              <label className="text-[11px] text-slate-400 block mb-1">最大角速度 (rad/s)</label>
              <Input
                type="number"
                step="0.1"
                value={formData.angular_velocity}
                onChange={(e) => setFormData({ ...formData, angular_velocity: parseFloat(e.target.value) || 0 })}
                className="h-8 text-xs bg-slate-950 border-slate-800"
              />
            </div>
            <div>
              <label className="text-[11px] text-slate-400 block mb-1">車體半徑 (m)</label>
              <Input
                type="number"
                step="0.05"
                value={formData.robot_radius}
                onChange={(e) => setFormData({ ...formData, robot_radius: parseFloat(e.target.value) || 0 })}
                className="h-8 text-xs bg-slate-950 border-slate-800"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[11px] text-slate-400 block mb-1">回充電量閾值 (%)</label>
              <Input
                type="number"
                value={formData.recharge_threshold}
                onChange={(e) => setFormData({ ...formData, recharge_threshold: parseInt(e.target.value, 10) || 0 })}
                className="h-8 text-xs bg-slate-950 border-slate-800"
              />
            </div>
            <div>
              <label className="text-[11px] text-slate-400 block mb-1">回充目標電量 (%)</label>
              <Input
                type="number"
                value={formData.recharge_target}
                onChange={(e) => setFormData({ ...formData, recharge_target: parseInt(e.target.value, 10) || 0 })}
                className="h-8 text-xs bg-slate-950 border-slate-800"
              />
            </div>
          </div>

          <div>
            <label className="text-[11px] text-slate-400 block mb-1">
              納管車輛名單 (以逗號分隔機器人 ID)
            </label>
            <Input
              value={robotsInputStr}
              onChange={(e) => setRobotsInputStr(e.target.value)}
              placeholder="例如：tinyRobot1, tinyRobot2, tinyRobot3"
              className="h-8 text-xs bg-slate-950 border-slate-800"
            />
          </div>

          <DialogFooter className="pt-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setCreateEditModalOpen(false)}
              className="text-xs"
            >
              取消
            </Button>
            <Button type="submit" size="sm" className="text-xs bg-primary text-primary-foreground font-semibold">
              儲存設定
            </Button>
          </DialogFooter>
        </form>
      </Dialog>

      {/* 2. Modal: Live Console Logs */}
      <Dialog open={logsModalOpen} onOpenChange={setLogsModalOpen}>
        <DialogHeader>
          <DialogTitle className="flex items-center space-x-2 text-base font-bold">
            <Terminal className="w-4 h-4 text-cyan-400" />
            <span>Adapter 即時控制台日誌 — {logsAdapter?.name}</span>
          </DialogTitle>
          <DialogDescription className="text-xs text-slate-400">
            Open-RMF Fleet Adapter ROS 2 節點日誌輸出與事件歷史。
          </DialogDescription>
        </DialogHeader>

        <div className="py-2 space-y-3">
          <div className="bg-[#050811] border border-slate-800 rounded-xl p-3 font-mono text-[11px] text-slate-300 max-h-80 overflow-y-auto space-y-1">
            {logsAdapter?.logs && logsAdapter.logs.length > 0 ? (
              logsAdapter.logs.map((log, idx) => {
                const isWarn = log.includes('[WARN]');
                const isError = log.includes('[ERROR]');
                return (
                  <p
                    key={idx}
                    className={
                      isError
                        ? 'text-rose-400'
                        : isWarn
                        ? 'text-amber-400'
                        : 'text-slate-300'
                    }
                  >
                    {log}
                  </p>
                );
              })
            ) : (
              <p className="text-slate-500 italic">無即時日誌記錄</p>
            )}
          </div>

          <div className="flex items-center justify-between text-xs text-slate-400 pt-1">
            <span>ROS 2 節點狀態：正常運作中</span>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => {
                if (logsAdapter) {
                  const simulatedLog = `[INFO] [heartbeat]: Heartbeat pulse at ${new Date().toLocaleTimeString()} (Graph ${logsAdapter.graph_idx} active)`;
                  setLogsAdapter({
                    ...logsAdapter,
                    logs: [...(logsAdapter.logs || []), simulatedLog],
                  });
                }
              }}
            >
              測試發送 Heartbeat
            </Button>
          </div>
        </div>

        <DialogFooter>
          <Button size="sm" variant="outline" onClick={() => setLogsModalOpen(false)} className="text-xs">
            關閉
          </Button>
        </DialogFooter>
      </Dialog>

      {/* 3. Modal: YAML Configuration Export */}
      <Dialog open={yamlModalOpen} onOpenChange={setYamlModalOpen}>
        <DialogHeader>
          <DialogTitle className="flex items-center space-x-2 text-base font-bold">
            <FileCode className="w-4 h-4 text-amber-400" />
            <span>Open-RMF Fleet Adapter YAML 設定檔</span>
          </DialogTitle>
          <DialogDescription className="text-xs text-slate-400">
            可用於啟動 ROS 2 EasyFullControl 節點或作為車隊接入標準配置。
          </DialogDescription>
        </DialogHeader>

        {yamlAdapter && (
          <div className="py-2 space-y-3">
            <div className="relative">
              <pre className="bg-[#050811] border border-slate-800 rounded-xl p-3.5 font-mono text-[11px] text-emerald-300/90 max-h-80 overflow-y-auto leading-relaxed">
                {generateYamlConfig(yamlAdapter)}
              </pre>

              <button
                onClick={() => {
                  navigator.clipboard.writeText(generateYamlConfig(yamlAdapter));
                  setCopiedYaml(true);
                  setTimeout(() => setCopiedYaml(false), 2000);
                }}
                className="absolute top-2.5 right-2.5 bg-slate-800/80 hover:bg-slate-700 text-slate-200 text-xs px-2.5 py-1 rounded-md border border-slate-700 flex items-center space-x-1"
              >
                {copiedYaml ? (
                  <>
                    <Check className="w-3 h-3 text-emerald-400" />
                    <span>已複製</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-3 h-3" />
                    <span>複製</span>
                  </>
                )}
              </button>
            </div>

            <div className="flex items-center justify-end space-x-2 pt-1">
              <Button
                size="sm"
                variant="secondary"
                className="text-xs h-8"
                onClick={() => {
                  const blob = new Blob([generateYamlConfig(yamlAdapter)], {
                    type: 'application/x-yaml',
                  });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `${yamlAdapter.fleet_name}_adapter_config.yaml`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                <Download className="w-3.5 h-3.5 mr-1" />
                下載 .yaml 檔案
              </Button>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button size="sm" variant="outline" onClick={() => setYamlModalOpen(false)} className="text-xs">
            完成
          </Button>
        </DialogFooter>
      </Dialog>
    </div>
  );
};
