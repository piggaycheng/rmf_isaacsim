import React, { useState } from 'react';
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from './ui/dialog';
import { Button } from './ui/button';
import { Waypoint, Robot, DispatchTaskRequest } from '@/types/rmf';
import { OFF_GRAPH_THRESHOLD_M } from '@/lib/constants';
import { Send, Navigation, PackageCheck, Repeat, AlertTriangle, ListOrdered } from 'lucide-react';

interface DispatchModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedWaypoint: Waypoint | null;
  waypoints: Waypoint[];
  robots: Robot[];
  onDispatchTask: (task: DispatchTaskRequest) => void;
  onOpenMultiStep?: () => void;
}

export const DispatchModal: React.FC<DispatchModalProps> = ({
  open,
  onOpenChange,
  selectedWaypoint,
  waypoints,
  robots,
  onDispatchTask,
  onOpenMultiStep,
}) => {
  const [taskType, setTaskType] = useState<'goto' | 'patrol' | 'delivery'>('goto');
  const [targetWaypointId, setTargetWaypointId] = useState<string>(selectedWaypoint?.id || '');
  const [destinationWaypointId, setDestinationWaypointId] = useState<string>('');
  const [selectedRobotId, setSelectedRobotId] = useState<string>('any');
  const [rounds, setRounds] = useState<string>('1');
  const [roundsTouched, setRoundsTouched] = useState<boolean>(false);
  const [validationError, setValidationError] = useState<string>('');

  const getRoundsError = (val: string): string => {
    const trimmed = val.trim();
    if (!trimmed) {
      return '請輸入巡邏輪數！';
    }
    const num = Number(trimmed);
    if (!Number.isInteger(num) || num <= 0) {
      return '巡邏輪數必須為大於 0 的正整數！';
    }
    return '';
  };

  const allRobotsOffGraph = robots.length > 0 && robots.every((r) => r.is_off_graph || r.status === 'off_graph');
  const selectedRobot = robots.find((r) => r.id === selectedRobotId);
  const isSelectedRobotOffGraph = Boolean(selectedRobot && (selectedRobot.is_off_graph || selectedRobot.status === 'off_graph'));

  React.useEffect(() => {
    if (selectedWaypoint) {
      setTargetWaypointId(selectedWaypoint.id);
    }
  }, [selectedWaypoint]);

  React.useEffect(() => {
    if (open) {
      setValidationError('');
      setRounds('1');
      setRoundsTouched(false);
    }
  }, [open]);

  React.useEffect(() => {
    setValidationError('');
  }, [taskType, targetWaypointId, destinationWaypointId, selectedRobotId]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError('');

    if (isSelectedRobotOffGraph) {
      setValidationError(`車輛 '${selectedRobot?.name}' 偏離路網過遠 (${selectedRobot?.off_graph_distance || `>${OFF_GRAPH_THRESHOLD_M}`}m)，無法指派任務！`);
      return;
    }

    if (selectedRobotId === 'any' && allRobotsOffGraph) {
      setValidationError(`目前所有車輛皆偏離路網過遠 (>${OFF_GRAPH_THRESHOLD_M}m)，無法派遣任務！`);
      return;
    }

    if (!targetWaypointId) {
      setValidationError('請選擇目標站點！');
      return;
    }

    const targetWp = waypoints.find((w) => w.id === targetWaypointId);
    if (!targetWp) {
      setValidationError('所選目標站點不存在！');
      return;
    }

    if (taskType === 'delivery') {
      if (!destinationWaypointId) {
        setValidationError('物料配送任務必須指定卸貨站點 (Dropoff)！');
        return;
      }
      if (destinationWaypointId === targetWaypointId) {
        setValidationError('取貨站點與卸貨站點不能相同！');
        return;
      }
    }

    let parsedRounds: number | undefined = undefined;
    if (taskType === 'patrol') {
      const err = getRoundsError(rounds);
      if (err) {
        setRoundsTouched(true);
        setValidationError(err);
        return;
      }
      parsedRounds = Number(rounds.trim());
    }

    const destWp = waypoints.find((w) => w.id === destinationWaypointId);

    onDispatchTask({
      type: taskType,
      target_waypoint: targetWp.name,
      destination_waypoint: destWp?.name,
      robot_id: selectedRobotId === 'any' ? undefined : selectedRobotId,
      rounds: taskType === 'patrol' ? parsedRounds : undefined,
    });
    onOpenChange(false);
  };

  const roundsError = taskType === 'patrol' && roundsTouched ? getRoundsError(rounds) : '';
  const isRoundsInvalid = taskType === 'patrol' && Boolean(getRoundsError(rounds));

  const isSubmitDisabled =
    !targetWaypointId ||
    (taskType === 'delivery' && (!destinationWaypointId || destinationWaypointId === targetWaypointId)) ||
    (selectedRobotId === 'any' && allRobotsOffGraph) ||
    isSelectedRobotOffGraph ||
    isRoundsInvalid;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle className="flex items-center space-x-2 text-xl font-bold">
          <Send className="w-5 h-5 text-primary" />
          <span>派遣機器人任務 (Task Dispatch)</span>
        </DialogTitle>
        <DialogDescription>
          向 Open-RMF 調度核心發起真實交通與物料任務。
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="space-y-4">
        {validationError && (
          <div className="p-2.5 rounded-md bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs font-medium">
            ⚠️ {validationError}
          </div>
        )}

        {/* Task Type Selection */}
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
            任務類型
          </label>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <button
              type="button"
              onClick={() => setTaskType('goto')}
              className={`flex flex-col items-center justify-center p-2.5 rounded-lg border text-xs font-medium transition-all ${
                taskType === 'goto'
                  ? 'border-primary bg-primary/10 text-primary shadow-sm'
                  : 'border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700'
              }`}
            >
              <Navigation className="w-4 h-4 mb-1" />
              <span>單點前往 (Goto)</span>
            </button>
            <button
              type="button"
              onClick={() => setTaskType('patrol')}
              className={`flex flex-col items-center justify-center p-2.5 rounded-lg border text-xs font-medium transition-all ${
                taskType === 'patrol'
                  ? 'border-primary bg-primary/10 text-primary shadow-sm'
                  : 'border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700'
              }`}
            >
              <Repeat className="w-4 h-4 mb-1" />
              <span>路網巡邏 (Patrol)</span>
            </button>
            <button
              type="button"
              onClick={() => setTaskType('delivery')}
              className={`flex flex-col items-center justify-center p-2.5 rounded-lg border text-xs font-medium transition-all ${
                taskType === 'delivery'
                  ? 'border-primary bg-primary/10 text-primary shadow-sm'
                  : 'border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700'
              }`}
            >
              <PackageCheck className="w-4 h-4 mb-1" />
              <span>物料配送 (Delivery)</span>
            </button>
            {onOpenMultiStep && (
              <button
                type="button"
                onClick={() => {
                  onOpenChange(false);
                  onOpenMultiStep();
                }}
                className="flex flex-col items-center justify-center p-2.5 rounded-lg border border-indigo-500/40 bg-indigo-950/30 text-indigo-300 hover:border-indigo-400 hover:bg-indigo-900/40 transition-all group"
              >
                <ListOrdered className="w-4 h-4 mb-1 text-indigo-400 group-hover:scale-110 transition-transform" />
                <span className="font-semibold">多步任務</span>
                <span className="text-[10px] text-indigo-400/80">雲台/導航連動</span>
              </button>
            )}
          </div>
        </div>

        {/* Target Waypoint */}
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
            {taskType === 'delivery' ? '取貨站點 (Pickup)' : '目標站點 (Destination)'}
          </label>
          <select
            value={targetWaypointId}
            onChange={(e) => setTargetWaypointId(e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">請選擇站點...</option>
            {waypoints.map((wp) => (
              <option key={wp.id} value={wp.id}>
                {wp.level ? `[${wp.level}] ` : ''}
                {wp.type === 'lift' ? '🛗 ' : ''}
                {wp.name} ({wp.type}) [{wp.x.toFixed(1)}, {wp.y.toFixed(1)}]
              </option>
            ))}
          </select>
        </div>

        {/* Delivery / Patrol second waypoint */}
        {(taskType === 'delivery' || taskType === 'patrol') && (
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
              {taskType === 'delivery' ? '卸貨站點 (Dropoff - 必填)' : '第二巡邏點 (可選)'}
            </label>
            <select
              value={destinationWaypointId}
              onChange={(e) => setDestinationWaypointId(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="">{taskType === 'delivery' ? '請選擇卸貨站點...' : '無第二巡邏點'}</option>
              {waypoints
                .filter((w) => w.id !== targetWaypointId)
                .map((wp) => (
                  <option key={wp.id} value={wp.id}>
                    {wp.level ? `[${wp.level}] ` : ''}
                    {wp.type === 'lift' ? '🛗 ' : ''}
                    {wp.name} ({wp.type}) [{wp.x.toFixed(1)}, {wp.y.toFixed(1)}]
                  </option>
                ))}
            </select>
          </div>
        )}

        {/* Patrol Rounds */}
        {taskType === 'patrol' && (
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
              巡邏輪數 (Rounds)
            </label>
            <input
              type="number"
              min="1"
              step="1"
              value={rounds}
              onChange={(e) => {
                setRounds(e.target.value);
                setRoundsTouched(true);
              }}
              onBlur={() => setRoundsTouched(true)}
              className={`w-full bg-slate-900 border ${
                roundsError ? 'border-rose-500 focus:ring-rose-500' : 'border-slate-700 focus:ring-primary'
              } rounded-md px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-1`}
              placeholder="請輸入巡邏輪數 (預設: 1)"
            />
            {roundsError ? (
              <p className="text-xs text-rose-400 mt-1.5 flex items-center gap-1 font-medium">
                ⚠️ {roundsError}
              </p>
            ) : (
              <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
                每輪依序走訪所選站點（例如站點 A、B 設定 2 輪時，行進順序為 A → B → A → B，最終停靠於 B）。
              </p>
            )}
          </div>
        )}

        {/* Target Robot */}
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
            指定調度車輛
          </label>
          <select
            value={selectedRobotId}
            onChange={(e) => setSelectedRobotId(e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="any" disabled={allRobotsOffGraph}>
              {allRobotsOffGraph
                ? '由 RMF 最佳化指派 (Auto) - ⚠️ 全車偏離路網無法指派'
                : '由 RMF 最佳化指派 (Auto Optimal)'}
            </option>
            {robots.map((r) => {
              const isEstopped = r.emergency_stop || r.status === 'emergency_stop';
              const isOff = Boolean(r.is_off_graph || r.status === 'off_graph');
              const statusLabel = isEstopped
                ? '🛑 緊急停車 (無法指派)'
                : isOff
                ? `⚠️ 偏離路網 (${r.off_graph_distance || '>3'}m - 無法操控)`
                : r.status;
              return (
                <option key={r.id} value={r.id} disabled={isEstopped || isOff}>
                  {r.name} ({r.fleet} · {r.level_name || '1F'}) - 電量 {Math.round(r.battery)}% [{statusLabel}]
                </option>
              );
            })}
          </select>
        </div>

        {allRobotsOffGraph && (
          <div className="p-2.5 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs font-medium space-y-1">
            <div className="flex items-center gap-1 font-semibold">
              <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
              <span>目前車隊所有在線車輛皆偏離路網過遠 (&gt;{OFF_GRAPH_THRESHOLD_M}m)</span>
            </div>
            <p className="text-[11px] text-amber-300/80 leading-relaxed">
              Open-RMF 無法對偏離路網的車輛進行路徑規劃與操控接單。請手動移車至路網附近，或在路網編輯器中將路網連至車輛位置。
            </p>
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button type="submit" disabled={isSubmitDisabled}>
            立即派遣任務
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
};
