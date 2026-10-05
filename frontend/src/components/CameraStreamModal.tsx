import React, { useState } from 'react';
import { CameraInfo, PlantSite } from '@/types/rmf';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  X,
  Video,
  Power,
  ExternalLink,
  Copy,
  Check,
  Maximize2,
  RefreshCw,
  Tv,
} from 'lucide-react';

interface CameraStreamModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  camera: CameraInfo | null;
  plant: PlantSite | null;
  onToggleCamera: (camera: CameraInfo) => void;
}

export function CameraStreamModal({
  open,
  onOpenChange,
  camera,
  plant,
  onToggleCamera,
}: CameraStreamModalProps) {
  const [copiedRTSP, setCopiedRTSP] = useState(false);
  const [copiedWebRTC, setCopiedWebRTC] = useState(false);
  const [iframeKey, setIframeKey] = useState(0);

  if (!open || !camera) return null;

  const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
  const rtspUrl = `rtsp://${host}:8554/${camera.rtsp_path}`;
  const webrtcUrl = `http://${host}:8889/${camera.rtsp_path}/`;

  const handleCopyRTSP = () => {
    navigator.clipboard.writeText(rtspUrl);
    setCopiedRTSP(true);
    setTimeout(() => setCopiedRTSP(false), 2000);
  };

  const handleCopyWebRTC = () => {
    navigator.clipboard.writeText(webrtcUrl);
    setCopiedWebRTC(true);
    setTimeout(() => setCopiedWebRTC(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-4xl bg-[#0b111e] border border-cyan-800/80 rounded-2xl shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-800 bg-[#0d1527]">
          <div className="flex items-center space-x-3">
            <div className="w-8 h-8 rounded-lg bg-cyan-500/20 text-cyan-400 flex items-center justify-center">
              <Video className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h3 className="text-sm font-bold text-slate-100">{camera.name}</h3>
                <Badge
                  variant={camera.enabled ? 'success' : 'secondary'}
                  className="text-[10px] px-2"
                >
                  {camera.enabled ? '已啟動相機 (ON)' : '相機未啟動 (OFF)'}
                </Badge>
                {plant && (
                  <Badge variant="outline" className="text-[10px] border-slate-700 text-slate-400">
                    {plant.name}
                  </Badge>
                )}
              </div>
              <p className="text-[11px] text-slate-400 font-mono mt-0.5">
                {camera.width}x{camera.height} @ {camera.fps}fps • MediaMTX WebRTC 低延遲串流
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setIframeKey((k) => k + 1)}
              className="h-8 px-2.5 text-xs border-slate-700 bg-slate-900 text-slate-300 hover:text-white"
              title="重新載入串流播放器"
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1" />
              重新整理
            </Button>
            <button
              onClick={() => onOpenChange(false)}
              className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Video Player Area */}
        <div className="relative aspect-video w-full bg-slate-950 flex items-center justify-center overflow-hidden border-b border-slate-800">
          <iframe
            key={iframeKey}
            src={webrtcUrl}
            title={camera.name}
            className="w-full h-full border-0"
            allow="autoplay; fullscreen"
          />

          {/* Overlay when camera is disabled */}
          {!camera.enabled && (
            <div className="absolute inset-0 bg-slate-950/85 backdrop-blur-sm flex flex-col items-center justify-center p-6 text-center z-10">
              <div className="w-14 h-14 rounded-2xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400 mb-3 shadow-lg shadow-amber-500/10">
                <Power className="w-7 h-7" />
              </div>
              <h4 className="text-base font-bold text-slate-200 mb-1">設備端相機尚未啟動</h4>
              <p className="text-xs text-slate-400 max-w-md mb-4">
                此鏡頭當前處於休眠待機狀態。點擊下方按鈕將透過 MQTT 指令即時通知設備端開啟相機並推流至 MediaMTX。
              </p>
              <Button
                size="sm"
                onClick={() => onToggleCamera(camera)}
                className="bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-white font-bold text-xs h-9 px-5 shadow-lg shadow-emerald-500/20"
              >
                <Power className="w-4 h-4 mr-1.5" />
                立即透過 MQTT 開啟此鏡頭
              </Button>
            </div>
          )}
        </div>

        {/* Footer Details & Quick URLs */}
        <div className="p-4 bg-[#0d1322] space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
            {/* Control Toggle */}
            <div className="flex items-center space-x-2">
              <Button
                size="sm"
                onClick={() => onToggleCamera(camera)}
                variant={camera.enabled ? 'destructive' : 'default'}
                className="h-8 text-xs font-semibold"
              >
                <Power className="w-3.5 h-3.5 mr-1.5" />
                {camera.enabled ? '發送 MQTT 關閉鏡頭' : '發送 MQTT 開啟鏡頭'}
              </Button>
              <span className="text-[11px] text-slate-500 font-mono">
                控制主題: {camera.enable_topic}
              </span>
            </div>

            {/* Direct Open in New Tab */}
            <a
              href={webrtcUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center text-xs text-cyan-400 hover:text-cyan-300 font-medium"
            >
              <ExternalLink className="w-3.5 h-3.5 mr-1" />
              在新視窗獨立開啟播放器
            </a>
          </div>

          {/* Stream Links bar */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 pt-1 border-t border-slate-800/80">
            <div className="flex items-center justify-between bg-slate-900/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-[11px] font-mono text-slate-300">
              <span className="truncate mr-2">WebRTC: {webrtcUrl}</span>
              <button
                onClick={handleCopyWebRTC}
                className="text-slate-400 hover:text-white p-1 shrink-0"
                title="複製 WebRTC 播放網址"
              >
                {copiedWebRTC ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
            <div className="flex items-center justify-between bg-slate-900/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-[11px] font-mono text-slate-300">
              <span className="truncate mr-2">RTSP: {rtspUrl}</span>
              <button
                onClick={handleCopyRTSP}
                className="text-slate-400 hover:text-white p-1 shrink-0"
                title="複製 RTSP 串流網址"
              >
                {copiedRTSP ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
