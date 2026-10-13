import React, { useState, useEffect } from 'react';
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from './ui/dialog';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Waypoint, Robot, MultiStepType, MultiStepItem, MultiStepTaskRequestData } from '@/types/rmf';
import {
  ListOrdered,
  Navigation,
  Camera,
  RotateCw,
  Plus,
  Trash2,
  ChevronUp,
  ChevronDown,
  Copy,
  Send,
  AlertTriangle,
  Compass,
  Sliders,
  ArrowRight,
  Clock,
  Layers,
} from 'lucide-react';

interface MultiStepTaskModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  waypoints: Waypoint[];
  robots: Robot[];
  initialWaypoint?: Waypoint | null;
  onDispatch: (data: MultiStepTaskRequestData) => Promise<{ success: boolean; message?: string }>;
}

export const MultiStepTaskModal: React.FC<MultiStepTaskModalProps> = ({
  open,
  onOpenChange,
  waypoints,
  robots,
  initialWaypoint,
  onDispatch,
}) => {
  // Dispatch Mode: 'compose' (Open-RMF scheduler) or 'direct' (Fleet Adapter direct sequence)
  const [mode, setMode] = useState<'compose' | 'direct'>('compose');
  const [selectedRobotId, setSelectedRobotId] = useState<string>('any');
  const [autoProceed, setAutoProceed] = useState<boolean>(true);
  const [stepDelay, setStepDelay] = useState<number>(0.5);
  const [validationError, setValidationError] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  // Steps state
  const [steps, setSteps] = useState<MultiStepItem[]>([]);

  // Initialize or reset steps when modal opens
  useEffect(() => {
    if (open) {
      setValidationError('');
      setSteps([]);
    }
  }, [open]);

  // Adjust selected robot when mode changes
  useEffect(() => {
    if (mode === 'direct' && selectedRobotId === 'any') {
      const firstAvailable = robots.find((r) => !r.is_off_graph && !r.emergency_stop);
      setSelectedRobotId(firstAvailable?.id || robots[0]?.id || '');
    }
  }, [mode, selectedRobotId, robots]);

  const selectedRobot = robots.find((r) => r.id === selectedRobotId);
  const isSelectedRobotOffGraph = Boolean(selectedRobot && (selectedRobot.is_off_graph || selectedRobot.status === 'off_graph'));
  const isSelectedRobotEstopped = Boolean(selectedRobot && (selectedRobot.emergency_stop || selectedRobot.status === 'emergency_stop'));
  const allRobotsOffGraph = robots.length > 0 && robots.every((r) => r.is_off_graph || r.status === 'off_graph');

  // Step manipulation handlers
  const handleAddStep = (type: MultiStepType) => {
    const newId = `step_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`;
    const lastNavStep = [...steps].reverse().find((s) => s.type === 'navigate');
    const defaultPlace = lastNavStep?.place || initialWaypoint?.name || (waypoints.length > 0 ? waypoints[0].name : '');

    if (type === 'navigate') {
      setSteps((prev) => [
        ...prev,
        {
          id: newId,
          type: 'navigate',
          place: defaultPlace,
        },
      ]);
    } else {
      setSteps((prev) => [
        ...prev,
        {
          id: newId,
          type: type,
          pan_deg: 0.0,
          tilt_deg: 0.0,
          duration_s: 5.0,
        },
      ]);
    }
  };

  const handleUpdateStep = (id: string, updates: Partial<MultiStepItem>) => {
    setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, ...updates } : s)));
  };

  const handleRemoveStep = (id: string) => {
    setSteps((prev) => prev.filter((s) => s.id !== id));
  };

  const handleMoveStep = (index: number, direction: 'up' | 'down') => {
    const targetIndex = direction === 'up' ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= steps.length) return;
    const nextSteps = [...steps];
    const [moved] = nextSteps.splice(index, 1);
    nextSteps.splice(targetIndex, 0, moved);
    setSteps(nextSteps);
  };

  const handleDuplicateStep = (index: number) => {
    const source = steps[index];
    const clone: MultiStepItem = {
      ...source,
      id: `step_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`,
    };
    const nextSteps = [...steps];
    nextSteps.splice(index + 1, 0, clone);
    setSteps(nextSteps);
  };

  // Submit Handler
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError('');

    if (steps.length === 0) {
      setValidationError('任務步驟不能為空！');
      return;
    }

    // Check navigate steps
    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      if (s.type === 'navigate' && !s.place) {
        setValidationError(`步驟 ${i + 1} (導航) 必須選擇目標站點！`);
        return;
      }
    }

    if (mode === 'direct' && (!selectedRobotId || selectedRobotId === 'any')) {
      setValidationError('車隊直派模式必須指定具體車輛！');
      return;
    }

    if (mode === 'direct' && isSelectedRobotEstopped) {
      setValidationError(`車輛 '${selectedRobot?.name}' 處於緊急停車狀態，無法下派任務！`);
      return;
    }

    if (mode === 'direct' && isSelectedRobotOffGraph) {
      setValidationError(`車輛 '${selectedRobot?.name}' 偏離路網過遠，無法下派任務！`);
      return;
    }

    if (mode === 'compose' && selectedRobotId === 'any' && allRobotsOffGraph) {
      setValidationError('目前所有車輛皆偏離路網，RMF 無法進行任務排程！');
      return;
    }

    const payloadSteps = steps.map((s) => {
      if (s.type === 'navigate') {
        const resolvedPlace = waypoints.find((w) => w.name === s.place || w.id === s.place)?.name || s.place;
        return {
          type: s.type,
          place: resolvedPlace,
          yaw: s.yaw !== undefined && s.yaw !== null ? (s.yaw * Math.PI) / 180.0 : undefined,
        };
      } else {
        return {
          type: s.type,
          pan_deg: Number(s.pan_deg || 0),
          tilt_deg: Number(s.tilt_deg || 0),
          duration_s: Number(s.duration_s || 5.0),
        };
      }
    });

    setIsSubmitting(true);
    try {
      const res = await onDispatch({
        mode,
        robot_id: selectedRobotId === 'any' ? undefined : selectedRobotId,
        fleet_name: selectedRobot?.fleet,
        auto_proceed: mode === 'direct' ? autoProceed : undefined,
        step_delay: mode === 'direct' ? stepDelay : undefined,
        steps: payloadSteps,
      });

      if (res.success) {
        onOpenChange(false);
      } else {
        setValidationError(res.message || '任務派遣失敗');
      }
    } catch (err: any) {
      setValidationError(err?.message || '派遣任務時發生未預期錯誤');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange} className="max-w-3xl max-h-[92vh] flex flex-col p-6">
      <DialogHeader className="shrink-0 mb-3">
        <div className="flex items-center justify-between pr-6">
          <DialogTitle className="flex items-center space-x-2.5 text-xl font-bold text-slate-100">
            <div className="w-8 h-8 rounded-lg bg-primary/20 text-primary flex items-center justify-center">
              <ListOrdered className="w-5 h-5" />
            </div>
            <span>多步複合任務派遣 (Multi-Step Task)</span>
          </DialogTitle>
        </div>
        <DialogDescription className="text-slate-400 text-xs mt-1">
          配置自訂連續步驟（如：導航到站點 ➔ 雲台轉向拍照 ➔ 導航返航），支援 Open-RMF 交通排程與車隊直派。
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto space-y-4 pr-1">
        {validationError && (
          <div className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-medium flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{validationError}</span>
          </div>
        )}

        {/* Top Configuration Grid: Mode & Target Robot */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5 bg-slate-950/40 border border-slate-800/60 p-3 rounded-xl">
          {/* Dispatch Mode */}
          <div>
            <label className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
              調度核心模式
            </label>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setMode('compose')}
                className={`flex flex-col items-start p-2.5 rounded-lg border text-left transition-all ${
                  mode === 'compose'
                    ? 'border-primary bg-primary/10 text-primary shadow-sm'
                    : 'border-slate-800 bg-slate-900/50 text-slate-400 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center gap-1.5 font-medium text-xs text-slate-200 mb-0.5">
                  <Layers className="w-3.5 h-3.5 text-primary" />
                  <span>RMF 交通排程 (Compose)</span>
                </div>
                <span className="text-[10px] text-slate-400 leading-tight">
                  由 Open-RMF 統一避障與排程
                </span>
              </button>
              <button
                type="button"
                onClick={() => setMode('direct')}
                className={`flex flex-col items-start p-2.5 rounded-lg border text-left transition-all ${
                  mode === 'direct'
                    ? 'border-primary bg-primary/10 text-primary shadow-sm'
                    : 'border-slate-800 bg-slate-900/50 text-slate-400 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center gap-1.5 font-medium text-xs text-slate-200 mb-0.5">
                  <Sliders className="w-3.5 h-3.5 text-primary" />
                  <span>車隊直派 (Direct)</span>
                </div>
                <span className="text-[10px] text-slate-400 leading-tight">
                  直連 Fleet Adapter 逐步執行
                </span>
              </button>
            </div>
          </div>

          {/* Assigned Robot */}
          <div>
            <label className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
              執行車輛
            </label>
            <select
              value={selectedRobotId}
              onChange={(e) => setSelectedRobotId(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-primary h-[42px]"
            >
              {mode === 'compose' && (
                <option value="any" disabled={allRobotsOffGraph}>
                  {allRobotsOffGraph
                    ? '由 RMF 最佳化指派 (Auto) - ⚠️ 全車偏離路網無法指派'
                    : '由 RMF 最佳化指派 (Auto Optimal)'}
                </option>
              )}
              {robots.map((r) => {
                const isEstopped = r.emergency_stop || r.status === 'emergency_stop';
                const isOff = Boolean(r.is_off_graph || r.status === 'off_graph');
                const statusLabel = isEstopped
                  ? '🛑 急停'
                  : isOff
                  ? `⚠️ 偏離 (${r.off_graph_distance || '>3'}m)`
                  : r.status;
                return (
                  <option key={r.id} value={r.id} disabled={isEstopped || isOff}>
                    {r.name} ({r.fleet}) - ⚡ {Math.round(r.battery)}% [{statusLabel}]
                  </option>
                );
              })}
            </select>
            {mode === 'direct' && !selectedRobotId && (
              <span className="text-[10px] text-amber-400 mt-1 block">直派模式必須指定車輛</span>
            )}
          </div>

          {/* Direct Mode Extra Options */}
          {mode === 'direct' && (
            <div className="col-span-1 md:col-span-2 pt-2 border-t border-slate-800/80 flex items-center justify-between gap-4 flex-wrap bg-slate-900/40 p-2.5 rounded-lg">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={autoProceed}
                  onChange={(e) => setAutoProceed(e.target.checked)}
                  className="rounded border-slate-700 text-primary focus:ring-primary h-4 w-4 bg-slate-950"
                />
                <span className="text-xs text-slate-300 font-medium">自動放行下一步 (Auto Proceed)</span>
                <span className="text-[10px] text-slate-500">
                  {autoProceed ? '步驟完成後自動繼續下一步' : '每步完成後停滯於 Release Gate 等待操作員放行'}
                </span>
              </label>

              {autoProceed && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-400 flex items-center gap-1">
                    <Clock className="w-3.5 h-3.5" /> 步驟延遲 (秒):
                  </span>
                  <input
                    type="number"
                    min="0"
                    max="60"
                    step="0.5"
                    value={stepDelay}
                    onChange={(e) => setStepDelay(parseFloat(e.target.value) || 0)}
                    className="w-16 bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 text-center"
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Steps List Header */}
        <div className="flex items-center justify-between pt-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold text-slate-300 uppercase tracking-wider">
              任務執行序列 (共 {steps.length} 個步驟)
            </span>
            <Badge variant="outline" className="text-[10px] py-0 border-primary/40 text-primary font-mono">
              Sequence
            </Badge>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => handleAddStep('navigate')}
              className="h-7 text-xs border-primary/40 text-primary hover:bg-primary/10"
            >
              <Plus className="w-3.5 h-3.5 mr-1" />
              新增導航站點
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => handleAddStep('gimbal_rotate')}
              className="h-7 text-xs border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10"
            >
              <Plus className="w-3.5 h-3.5 mr-1" />
              新增雲台動作
            </Button>
          </div>
        </div>

        {/* Steps Timeline Cards */}
        {steps.length === 0 ? (
          <div className="border border-dashed border-slate-800 rounded-xl py-10 px-4 text-center bg-slate-950/40">
            <ListOrdered className="w-8 h-8 mx-auto text-slate-600 mb-2" />
            <p className="text-xs font-semibold text-slate-300">目前尚未新增任何任務步驟</p>
            <p className="text-[11px] text-slate-500 mt-1">
              請點選右上方的「新增導航站點」或「新增雲台動作」開始建立序列
            </p>
          </div>
        ) : (
          <div className="space-y-2.5">
            {steps.map((step, index) => {
            const isNavigate = step.type === 'navigate';
            return (
              <div
                key={step.id}
                className="bg-slate-900/70 border border-slate-800 rounded-xl p-3 relative hover:border-slate-700 transition-all shadow-sm"
              >
                {/* Step Header */}
                <div className="flex items-center justify-between mb-2.5 pb-2 border-b border-slate-800/80">
                  <div className="flex items-center gap-2">
                    <span className="w-5 h-5 rounded-full bg-slate-800 text-slate-300 text-[11px] font-bold flex items-center justify-center">
                      {index + 1}
                    </span>

                    {/* Step Type Selector Buttons */}
                    <div className="flex items-center bg-slate-950 p-0.5 rounded-lg border border-slate-800">
                      <button
                        type="button"
                        onClick={() => handleUpdateStep(step.id, { type: 'navigate' })}
                        className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                          step.type === 'navigate'
                            ? 'bg-primary text-primary-foreground shadow-xs'
                            : 'text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        <Navigation className="w-3 h-3" />
                        <span>導航站點</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => handleUpdateStep(step.id, { type: 'gimbal_move' })}
                        className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                          step.type === 'gimbal_move'
                            ? 'bg-indigo-600 text-white shadow-xs'
                            : 'text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        <Camera className="w-3 h-3" />
                        <span>雲台絕對角</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => handleUpdateStep(step.id, { type: 'gimbal_rotate' })}
                        className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                          step.type === 'gimbal_rotate'
                            ? 'bg-indigo-600 text-white shadow-xs'
                            : 'text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        <RotateCw className="w-3 h-3" />
                        <span>雲台相對旋轉</span>
                      </button>
                    </div>
                  </div>

                  {/* Move & Actions */}
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      disabled={index === 0}
                      onClick={() => handleMoveStep(index, 'up')}
                      className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800 disabled:opacity-30 disabled:pointer-events-none transition-colors"
                      title="上移此步驟"
                    >
                      <ChevronUp className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      disabled={index === steps.length - 1}
                      onClick={() => handleMoveStep(index, 'down')}
                      className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800 disabled:opacity-30 disabled:pointer-events-none transition-colors"
                      title="下移此步驟"
                    >
                      <ChevronDown className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDuplicateStep(index)}
                      className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors"
                      title="複製此步驟"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleRemoveStep(step.id)}
                      className="p-1 rounded text-rose-400 hover:text-rose-300 hover:bg-rose-950/40 transition-colors"
                      title="刪除此步驟"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Step Body */}
                {isNavigate ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="text-[11px] font-medium text-slate-400 block mb-1">
                        目標站點 (Waypoint)
                      </label>
                      <select
                        value={waypoints.find((w) => w.name === step.place || w.id === step.place)?.name || step.place || ''}
                        onChange={(e) => handleUpdateStep(step.id, { place: e.target.value })}
                        className="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-primary"
                      >
                        <option value="">請選擇目標站點...</option>
                        {waypoints.map((wp) => (
                          <option key={wp.id} value={wp.name}>
                            {wp.name} ({wp.type}) [{wp.x.toFixed(1)}, {wp.y.toFixed(1)}]
                          </option>
                        ))}
                      </select>
                    </div>

                    <div>
                      <label className="text-[11px] font-medium text-slate-400 block mb-1">
                        到達朝向 (可選 Yaw 角度)
                      </label>
                      <div className="flex items-center gap-2">
                        <select
                          value={step.yaw === undefined || step.yaw === null ? 'none' : String(step.yaw)}
                          onChange={(e) => {
                            const val = e.target.value;
                            handleUpdateStep(step.id, { yaw: val === 'none' ? undefined : Number(val) });
                          }}
                          className="flex-1 bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-primary"
                        >
                          <option value="none">保持預設朝向 (無限制)</option>
                          <option value="0">0° (東向)</option>
                          <option value="90">90° (北向)</option>
                          <option value="180">180° (西向)</option>
                          <option value="-90">-90° (南向)</option>
                          <option value="45">45°</option>
                          <option value="-45">-45°</option>
                        </select>
                        <Compass className="w-4 h-4 text-slate-500 shrink-0" />
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {/* Pan Angle Slider & Input */}
                      <div className="bg-slate-950/60 p-2.5 rounded-lg border border-slate-800/80">
                        <div className="flex items-center justify-between text-[11px] mb-1.5">
                          <span className="text-slate-400 font-medium">
                            水平旋轉 (Pan): <span className="text-indigo-400 font-mono font-bold">{step.pan_deg || 0}°</span>
                          </span>
                          <span className="text-[10px] text-slate-500">-180° ~ +180°</span>
                        </div>
                        <div className="flex items-center gap-2 mb-2">
                          <input
                            type="range"
                            min="-180"
                            max="180"
                            step="1"
                            value={step.pan_deg || 0}
                            onChange={(e) => handleUpdateStep(step.id, { pan_deg: parseFloat(e.target.value) })}
                            className="flex-1 accent-indigo-500 cursor-pointer"
                          />
                          <input
                            type="number"
                            min="-180"
                            max="180"
                            value={step.pan_deg || 0}
                            onChange={(e) => handleUpdateStep(step.id, { pan_deg: parseFloat(e.target.value) || 0 })}
                            className="w-14 bg-slate-900 border border-slate-700 rounded px-1.5 py-0.5 text-xs text-slate-200 text-center font-mono"
                          />
                        </div>
                        {/* Quick Angles */}
                        <div className="flex items-center justify-between gap-1">
                          {[-90, -45, 0, 45, 90].map((deg) => (
                            <button
                              key={deg}
                              type="button"
                              onClick={() => handleUpdateStep(step.id, { pan_deg: deg })}
                              className={`px-1.5 py-0.5 rounded text-[10px] font-mono border transition-colors ${
                                (step.pan_deg || 0) === deg
                                  ? 'border-indigo-500 bg-indigo-500/20 text-indigo-300'
                                  : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:text-slate-200'
                              }`}
                            >
                              {deg > 0 ? `+${deg}°` : `${deg}°`}
                            </button>
                          ))}
                        </div>
                      </div>

                      {/* Tilt Angle Slider & Input */}
                      <div className="bg-slate-950/60 p-2.5 rounded-lg border border-slate-800/80">
                        <div className="flex items-center justify-between text-[11px] mb-1.5">
                          <span className="text-slate-400 font-medium">
                            俯仰角度 (Tilt): <span className="text-indigo-400 font-mono font-bold">{step.tilt_deg || 0}°</span>
                          </span>
                          <span className="text-[10px] text-slate-500">-90° ~ +90°</span>
                        </div>
                        <div className="flex items-center gap-2 mb-2">
                          <input
                            type="range"
                            min="-90"
                            max="90"
                            step="1"
                            value={step.tilt_deg || 0}
                            onChange={(e) => handleUpdateStep(step.id, { tilt_deg: parseFloat(e.target.value) })}
                            className="flex-1 accent-indigo-500 cursor-pointer"
                          />
                          <input
                            type="number"
                            min="-90"
                            max="90"
                            value={step.tilt_deg || 0}
                            onChange={(e) => handleUpdateStep(step.id, { tilt_deg: parseFloat(e.target.value) || 0 })}
                            className="w-14 bg-slate-900 border border-slate-700 rounded px-1.5 py-0.5 text-xs text-slate-200 text-center font-mono"
                          />
                        </div>
                        {/* Quick Angles */}
                        <div className="flex items-center justify-between gap-1">
                          {[-30, 0, 30, 45].map((deg) => (
                            <button
                              key={deg}
                              type="button"
                              onClick={() => handleUpdateStep(step.id, { tilt_deg: deg })}
                              className={`px-1.5 py-0.5 rounded text-[10px] font-mono border transition-colors ${
                                (step.tilt_deg || 0) === deg
                                  ? 'border-indigo-500 bg-indigo-500/20 text-indigo-300'
                                  : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:text-slate-200'
                              }`}
                            >
                              {deg > 0 ? `+${deg}°` : `${deg}°`}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center justify-end gap-2 text-xs text-slate-400 pt-1">
                      <Clock className="w-3.5 h-3.5 text-slate-500" />
                      <span>動作預估耗時:</span>
                      <input
                        type="number"
                        min="1"
                        max="60"
                        step="1"
                        value={step.duration_s || 5}
                        onChange={(e) => handleUpdateStep(step.id, { duration_s: parseFloat(e.target.value) || 5 })}
                        className="w-16 bg-slate-950 border border-slate-700 rounded px-2 py-0.5 text-xs text-slate-200 text-center"
                      />
                      <span>秒</span>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        )}

        {/* Visual Pipeline Preview */}
        {steps.length > 0 && (
          <div className="bg-slate-950/80 border border-slate-800/80 rounded-lg p-2.5">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold mb-1.5">
              序列預覽 (Pipeline Preview):
            </div>
            <div className="flex items-center gap-1.5 flex-wrap text-xs">
              {steps.map((s, idx) => (
                <React.Fragment key={s.id}>
                  <div
                    className={`px-2 py-1 rounded-md border text-[11px] font-mono flex items-center gap-1 ${
                      s.type === 'navigate'
                        ? 'bg-primary/10 border-primary/40 text-primary'
                        : 'bg-indigo-500/10 border-indigo-500/40 text-indigo-300'
                    }`}
                  >
                    {s.type === 'navigate' ? (
                      <>
                        <Navigation className="w-3 h-3" />
                        <span>{s.place || '?'}</span>
                      </>
                    ) : (
                      <>
                        <Camera className="w-3 h-3" />
                        <span>
                          {s.type === 'gimbal_rotate' ? 'Δ' : ''}({s.pan_deg || 0}°, {s.tilt_deg || 0}°)
                        </span>
                      </>
                    )}
                  </div>
                  {idx < steps.length - 1 && <ArrowRight className="w-3 h-3 text-slate-600 shrink-0" />}
                </React.Fragment>
              ))}
            </div>
          </div>
        )}
      </form>

      <DialogFooter className="shrink-0 mt-3 pt-3 border-t border-slate-800/80 flex items-center justify-between">
        <div className="text-xs text-slate-400">
          <span>共 {steps.length} 個步驟</span>
          {mode === 'direct' && !autoProceed && (
            <span className="ml-2 text-amber-400/90 font-medium">(手動放行模式)</span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={isSubmitting}
            className="text-slate-400 hover:text-slate-200"
          >
            取消
          </Button>
          <Button
            type="submit"
            onClick={handleSubmit}
            disabled={isSubmitting || steps.length === 0}
            className="bg-primary hover:bg-primary/90 text-primary-foreground min-w-[120px]"
          >
            {isSubmitting ? (
              <span className="flex items-center gap-1">
                <RotateCw className="w-3.5 h-3.5 animate-spin" />
                派送中...
              </span>
            ) : (
              <span className="flex items-center gap-1.5">
                <Send className="w-3.5 h-3.5" />
                派送多步任務
              </span>
            )}
          </Button>
        </div>
      </DialogFooter>
    </Dialog>
  );
};
