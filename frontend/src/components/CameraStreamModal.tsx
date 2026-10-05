import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
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
  RefreshCw,
  Tv,
  Loader2,
  Radio,
} from 'lucide-react';

interface CameraStreamModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  camera: CameraInfo | null;
  plant: PlantSite | null;
}

export function CameraStreamModal({
  open,
  onOpenChange,
  camera,
  plant,
}: CameraStreamModalProps) {
  const [copiedRTSP, setCopiedRTSP] = useState(false);
  const [copiedWebRTC, setCopiedWebRTC] = useState(false);
  const [iframeKey, setIframeKey] = useState(0);
  const [isReady, setIsReady] = useState(false);
  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
  const rtspUrl = camera ? `rtsp://${host}:8554/${camera.rtsp_path}` : '';
  const webrtcUrl = camera ? `http://${host}:8889/${camera.rtsp_path}/` : '';

  // Polling MediaMTX status to replace initial error with elegant '開啟中' state
  useEffect(() => {
    if (!open || !camera) {
      setIsReady(false);
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
      return;
    }

    setIsReady(false);
    let attempts = 0;
    const maxAttempts = 12; // 12 * 300ms = 3.6s max

    const checkStatus = async () => {
      attempts++;
      try {
        const res = await fetch(`/api/stream/status/${camera.rtsp_path}`);
        if (res.ok) {
          const data = await res.json();
          if (data && data.ready) {
            // Stream is ready on MediaMTX!
            setTimeout(() => {
              setIsReady(true);
              setIframeKey((k) => k + 1); // Refresh iframe directly into live stream
            }, 300);
            if (pollTimerRef.current) clearInterval(pollTimerRef.current);
            return;
          }
        }
      } catch (e) {
        // Fallback or offline
      }

      if (attempts >= maxAttempts) {
        setIsReady(true);
        if (pollTimerRef.current) clearInterval(pollTimerRef.current);
      }
    };

    // First check after 500ms (giving device time to receive MQTT)
    const initialTimeout = setTimeout(() => {
      checkStatus();
      pollTimerRef.current = setInterval(checkStatus, 350);
    }, 500);

    return () => {
      clearTimeout(initialTimeout);
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [open, camera?.rtsp_path]);

  if (!open || !camera) return null;

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

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-in fade-in duration-200"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onOpenChange(false);
        }
      }}
    >
      <div className="relative w-full max-w-4xl bg-[#0b111e] border border-cyan-800/80 rounded-2xl shadow-2xl overflow-hidden flex flex-col animate-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-800 bg-[#0d1527]">
          <div className="flex items-center space-x-3">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors ${
              isReady ? 'bg-emerald-500/20 text-emerald-400' : 'bg-cyan-500/20 text-cyan-400'
            }`}>
              <Tv className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h3 className="text-sm font-bold text-slate-100">{camera.name}</h3>
                <Badge
                  variant={isReady ? 'success' : 'outline'}
                  className={`text-[10px] px-2 flex items-center space-x-1 ${
                    !isReady ? 'border-cyan-500/50 text-cyan-300' : ''
                  }`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full mr-1 ${
                    isReady ? 'bg-emerald-300 animate-pulse' : 'bg-cyan-400 animate-ping'
                  }`} />
                  <span>{isReady ? '即時連線中 (Live)' : '相機開啟中...'}</span>
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
              onClick={() => {
                setIsReady(false);
                setIframeKey((k) => k + 1);
                setTimeout(() => setIsReady(true), 1200);
              }}
              className="h-8 px-2.5 text-xs border-slate-700 bg-slate-900 text-slate-300 hover:text-white"
              title="重新載入串流播放器"
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1" />
              重新整理
            </Button>
            <button
              onClick={() => onOpenChange(false)}
              className="text-slate-400 hover:text-white p-1.5 rounded-lg hover:bg-slate-800 transition-colors"
              title="關閉畫面（自動關閉相機）"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Video Player Area with Smooth Startup Overlay */}
        <div className="relative aspect-video w-full bg-slate-950 flex items-center justify-center overflow-hidden border-b border-slate-800">
          {/* WebRTC Video IFrame (always mounted in background to establish peer connection) */}
          <iframe
            key={iframeKey}
            src={webrtcUrl}
            title={camera.name}
            className="w-full h-full border-0"
            allow="autoplay; fullscreen"
          />

          {/* High-tech '相機開啟中' Loading Overlay: Covers initial MediaMTX error text */}
          <div
            className={`absolute inset-0 bg-[#070b14] flex flex-col items-center justify-center p-6 text-center z-20 transition-all duration-500 ${
              isReady ? 'opacity-0 pointer-events-none' : 'opacity-100'
            }`}
          >
            {/* Animated Pulse Sensor Icon */}
            <div className="relative mb-4">
              <div className="w-16 h-16 rounded-2xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-cyan-400 shadow-xl shadow-cyan-500/20">
                <Video className="w-8 h-8 animate-pulse" />
              </div>
              <span className="absolute -top-1 -right-1 flex h-4 w-4">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-4 w-4 bg-cyan-500"></span>
              </span>
            </div>

            {/* Status Titles */}
            <h4 className="text-base font-bold text-slate-100 flex items-center mb-1.5">
              <Loader2 className="w-4 h-4 mr-2 animate-spin text-cyan-400" />
              相機開啟中...
            </h4>
            <p className="text-xs text-slate-400 max-w-sm mb-4">
              已透過 MQTT 發送啟動訊號，正在等待設備端取像並建立 WebRTC 低延遲通道...
            </p>

            {/* Progress bar shimmer */}
            <div className="w-64 h-1.5 bg-slate-900 rounded-full overflow-hidden border border-slate-800">
              <div className="h-full bg-gradient-to-r from-cyan-500 via-teal-400 to-emerald-400 w-full animate-pulse" />
            </div>

            <div className="flex items-center space-x-2 text-[10px] text-slate-500 font-mono mt-3">
              <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping" />
              <span>目標路徑: {camera.rtsp_path}</span>
            </div>
          </div>
        </div>

        {/* Footer Details & Automatic Lifecycle Controls */}
        <div className="p-4 bg-[#0d1322] space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
            {/* Left: Close & Stop Camera Button */}
            <div className="flex items-center space-x-2.5">
              <Button
                size="sm"
                variant="destructive"
                onClick={() => onOpenChange(false)}
                className="h-8 text-xs font-semibold px-3 bg-rose-600 hover:bg-rose-500 shadow-sm shadow-rose-600/20"
                title="關閉此視窗，並自動發送 MQTT 指令關閉相機"
              >
                <Power className="w-3.5 h-3.5 mr-1.5" />
                關閉畫面（自動關閉相機）
              </Button>
              <span className="text-[11px] text-slate-400">
                💡 關閉此視窗將自動釋放設備端推流資源
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
              在新視窗獨立開啟
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
    </div>,
    document.body
  );
}
