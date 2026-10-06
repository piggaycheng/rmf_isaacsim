import React, { useState } from 'react';
import { PlantSite, CameraInfo } from '@/types/rmf';
import { usePlantCameras } from '@/hooks/usePlantCameras';
import { CameraStreamModal } from '@/components/CameraStreamModal';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Video,
  Tv,
  Wifi,
  WifiOff,
  Radio,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';

interface PlantCameraListProps {
  plant: PlantSite | null;
  className?: string;
  defaultExpanded?: boolean;
}

export function PlantCameraList({
  plant,
  className = '',
  defaultExpanded = true,
}: PlantCameraListProps) {
  const {
    connectionStatus,
    cameras,
    activeTopic,
    lastUpdated,
    setCameraEnabled,
  } = usePlantCameras(plant);

  const [selectedCameraForStream, setSelectedCameraForStream] = useState<CameraInfo | null>(null);
  const [streamModalOpen, setStreamModalOpen] = useState(false);
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

  if (!plant) {
    return null;
  }

  // 1. 開啟即時畫面時，自動發送 MQTT 指令啟動該相機 (true)
  const handleOpenStream = (camera: CameraInfo) => {
    setCameraEnabled(camera, true);
    setSelectedCameraForStream({ ...camera, enabled: true });
    setStreamModalOpen(true);
  };

  // 2. 關閉畫面時，自動發送 MQTT 指令關閉相機 (false) 節省資源
  const handleCloseStream = (open: boolean) => {
    if (!open) {
      if (selectedCameraForStream) {
        setCameraEnabled(selectedCameraForStream, false);
      }
      setSelectedCameraForStream(null);
      setStreamModalOpen(false);
    } else {
      setStreamModalOpen(true);
    }
  };

  return (
    <div className={`bg-[#0d1424] border border-cyan-800/50 rounded-xl overflow-hidden shadow-lg ${className}`}>
      {/* Top Header / Bar */}
      <div className="flex items-center justify-between px-3.5 py-2.5 bg-[#0f182c] border-b border-slate-800/80">
        <div className="flex items-center space-x-2.5">
          <div className="w-7 h-7 rounded-lg bg-cyan-500/20 text-cyan-400 flex items-center justify-center">
            <Video className="w-3.5 h-3.5" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="text-xs font-bold text-slate-100">
                廠區相機鏡頭列表
              </span>
              <Badge variant="outline" className="text-[10px] border-cyan-600/40 text-cyan-400 font-mono">
                {cameras.length} 支相機
              </Badge>
              {/* MQTT status badge */}
              <Badge
                variant={connectionStatus === 'connected' ? 'success' : connectionStatus === 'connecting' ? 'warning' : 'secondary'}
                className="text-[10px] flex items-center space-x-1"
              >
                {connectionStatus === 'connected' ? (
                  <>
                    <Wifi className="w-3 h-3 mr-0.5 text-emerald-400" />
                    <span>EMQX 已連線</span>
                  </>
                ) : connectionStatus === 'connecting' ? (
                  <>
                    <Radio className="w-3 h-3 mr-0.5 animate-pulse text-amber-400" />
                    <span>連線中...</span>
                  </>
                ) : (
                  <>
                    <WifiOff className="w-3 h-3 mr-0.5" />
                    <span>離線</span>
                  </>
                )}
              </Badge>
            </div>
            {activeTopic && (
              <p className="text-[10px] text-slate-400 font-mono mt-0.5">
                訂閱主題: <span className="text-cyan-400 font-semibold">{activeTopic}</span>
                {lastUpdated && (
                  <span className="ml-2 text-slate-500">
                    最後更新: {lastUpdated.toLocaleTimeString()}
                  </span>
                )}
              </p>
            )}
          </div>
        </div>

        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="text-slate-400 hover:text-white p-1 rounded-md hover:bg-slate-800 transition-colors"
          title={isExpanded ? '收合相機列表' : '展開相機列表'}
        >
          {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
      </div>

      {/* Cameras Grid */}
      {isExpanded && (
        <div className="p-3">
          {cameras.length === 0 ? (
            <div className="text-center py-5 px-3 border border-dashed border-slate-800/80 rounded-lg">
              <Radio className="w-5 h-5 mx-auto text-slate-500 animate-pulse mb-1.5" />
              <p className="text-xs text-slate-400 font-medium">
                {connectionStatus === 'connected'
                  ? '已訂閱主題，等待設備端發佈相機資訊...'
                  : '正在連接 EMQX 訊息伺服器...'}
              </p>
              <p className="text-[11px] text-slate-500 mt-1">
                （若設備已在該主題發佈 Retained Message，連線完成後將自動列出）
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
              {cameras.map((cam) => {
                const displayName = cam.display_name || cam.name;
                return (
                  <div
                    key={cam.name}
                    className={`p-3 rounded-lg border transition-all flex flex-col justify-between ${
                      cam.enabled
                        ? 'bg-emerald-950/20 border-emerald-500/40 shadow-sm shadow-emerald-500/10'
                        : 'bg-slate-900/60 border-slate-800 hover:border-slate-700'
                    }`}
                  >
                    <div>
                      {/* Title & Status */}
                      <div className="flex items-center justify-between mb-1.5">
                        <div className="flex items-center space-x-1.5">
                          <span
                            className={`w-2 h-2 rounded-full ${
                              cam.enabled ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'
                            }`}
                          />
                          <h4 className="text-xs font-bold text-slate-200 truncate" title={cam.name}>
                            {displayName}
                          </h4>
                        </div>
                        <Badge
                          variant={cam.enabled ? 'success' : 'outline'}
                          className="text-[9px] px-1.5 py-0"
                        >
                          {cam.enabled ? '串流中 (ON)' : '待機 (OFF)'}
                        </Badge>
                      </div>

                      {/* Specs */}
                      <div className="flex items-center space-x-2 text-[10px] text-slate-400 font-mono mb-2">
                        <span>{cam.width}x{cam.height}</span>
                        <span>•</span>
                        <span>{cam.fps} FPS</span>
                        <span>•</span>
                        <span className="truncate max-w-[80px]" title={cam.rtsp_path}>
                          RTSP
                        </span>
                      </div>
                    </div>

                    {/* Single On-Demand Stream Action Button */}
                    <div className="pt-2 border-t border-slate-800/80">
                      <Button
                        size="sm"
                        onClick={() => handleOpenStream(cam)}
                        className={`w-full h-7 text-[11px] font-medium px-2 shadow-sm transition-all ${
                          cam.enabled
                            ? 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/20'
                            : 'bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white shadow-cyan-600/20'
                        }`}
                        title="開啟即時畫面（自動啟動相機推流）"
                      >
                        <Tv className="w-3.5 h-3.5 mr-1.5 text-cyan-200" />
                        開啟即時畫面
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Stream Viewer Modal */}
      <CameraStreamModal
        open={streamModalOpen}
        onOpenChange={handleCloseStream}
        camera={selectedCameraForStream}
        plant={plant}
      />
    </div>
  );
}
