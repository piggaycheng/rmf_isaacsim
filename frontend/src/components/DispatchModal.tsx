import React, { useState } from 'react';
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from './ui/dialog';
import { Button } from './ui/button';
import { Waypoint, Robot } from '@/types/rmf';
import { Send, Navigation, PackageCheck, Repeat } from 'lucide-react';

interface DispatchModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedWaypoint: Waypoint | null;
  waypoints: Waypoint[];
  robots: Robot[];
  onDispatchTask: (task: {
    type: 'patrol' | 'delivery' | 'goto';
    target_waypoint: string;
    destination_waypoint?: string;
    robot_id?: string;
  }) => void;
}

export const DispatchModal: React.FC<DispatchModalProps> = ({
  open,
  onOpenChange,
  selectedWaypoint,
  waypoints,
  robots,
  onDispatchTask,
}) => {
  const [taskType, setTaskType] = useState<'goto' | 'patrol' | 'delivery'>('goto');
  const [targetWaypointId, setTargetWaypointId] = useState<string>(selectedWaypoint?.id || '');
  const [destinationWaypointId, setDestinationWaypointId] = useState<string>('');
  const [selectedRobotId, setSelectedRobotId] = useState<string>('any');

  React.useEffect(() => {
    if (selectedWaypoint) {
      setTargetWaypointId(selectedWaypoint.id);
    }
  }, [selectedWaypoint]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const targetWp = waypoints.find((w) => w.id === targetWaypointId);
    if (!targetWp) return;

    onDispatchTask({
      type: taskType,
      target_waypoint: targetWp.name,
      destination_waypoint: waypoints.find((w) => w.id === destinationWaypointId)?.name,
      robot_id: selectedRobotId === 'any' ? undefined : selectedRobotId,
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle className="flex items-center space-x-2 text-xl font-bold">
          <Send className="w-5 h-5 text-primary" />
          <span>派遣機器人任務 (Task Dispatch)</span>
        </DialogTitle>
        <DialogDescription>
          在地圖上向 Open-RMF 交通調度器發起任務指令。
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Task Type Selection */}
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
            任務類型
          </label>
          <div className="grid grid-cols-3 gap-2">
            <button
              type="button"
              onClick={() => setTaskType('goto')}
              className={`flex flex-col items-center justify-center p-3 rounded-lg border text-sm font-medium transition-all ${
                taskType === 'goto'
                  ? 'border-primary bg-primary/10 text-primary shadow-sm'
                  : 'border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700'
              }`}
            >
              <Navigation className="w-4 h-4 mb-1.5" />
              <span>前往指定點</span>
            </button>
            <button
              type="button"
              onClick={() => setTaskType('patrol')}
              className={`flex flex-col items-center justify-center p-3 rounded-lg border text-sm font-medium transition-all ${
                taskType === 'patrol'
                  ? 'border-primary bg-primary/10 text-primary shadow-sm'
                  : 'border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700'
              }`}
            >
              <Repeat className="w-4 h-4 mb-1.5" />
              <span>定點巡邏</span>
            </button>
            <button
              type="button"
              onClick={() => setTaskType('delivery')}
              className={`flex flex-col items-center justify-center p-3 rounded-lg border text-sm font-medium transition-all ${
                taskType === 'delivery'
                  ? 'border-primary bg-primary/10 text-primary shadow-sm'
                  : 'border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700'
              }`}
            >
              <PackageCheck className="w-4 h-4 mb-1.5" />
              <span>物料取送</span>
            </button>
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
            {waypoints.map((wp) => (
              <option key={wp.id} value={wp.id}>
                {wp.name} ({wp.type}) [{wp.x.toFixed(1)}, {wp.y.toFixed(1)}]
              </option>
            ))}
          </select>
        </div>

        {/* Delivery destination */}
        {taskType === 'delivery' && (
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
              卸貨站點 (Dropoff)
            </label>
            <select
              value={destinationWaypointId}
              onChange={(e) => setDestinationWaypointId(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="">請選擇目的地站點...</option>
              {waypoints
                .filter((w) => w.id !== targetWaypointId)
                .map((wp) => (
                  <option key={wp.id} value={wp.id}>
                    {wp.name} ({wp.type})
                  </option>
                ))}
            </select>
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
            <option value="any">由 RMF 最佳化指派 (Auto Optimal)</option>
            {robots.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} ({r.fleet}) - 電量 {Math.round(r.battery)}% [{r.status}]
              </option>
            ))}
          </select>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button type="submit">
            立即派遣任務
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
};
