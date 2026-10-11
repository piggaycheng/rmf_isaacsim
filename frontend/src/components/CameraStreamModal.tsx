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
  ChevronDown,
  ChevronUp,
  Maximize2,
  Minimize2,
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
  const [isMaximized, setIsMaximized] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [mountTarget, setMountTarget] = useState<HTMLElement | null>(null);
  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
  const rtspUrl = camera ? `rtsp://${host}:8554/${camera.rtsp_path}` : '';
  const webrtcUrl = camera ? `http://${host}:8889/${camera.rtsp_path}/` : '';

  // Resolve target portal element (Map top-right container if available, fallback to body)
  useEffect(() => {
    if (!open) {
      setIsMaximized(false);
      setIsCollapsed(false);
      return;
    }

    const updateTarget = () => {
      const target = document.getElementById('map-camera-pip-target');
      if (target) {
        setMountTarget(target);
      } else if (typeof document !== 'undefined') {
        setMountTarget(document.body);
      }
    };

    updateTarget();
    const timer = setTimeout(updateTarget, 100);
    return () => clearTimeout(timer);
  }, [open]);

  // Handle ESC key: restore from maximized or close
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (isMaximized) {
          setIsMaximized(false);
        } else {
          onOpenChange(false);
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, isMaximized, onOpenChange]);

  // Polling MediaMTX status to replace initial error with elegant '開啟中' state
  useEffect(() => {
    if (!open || !camera) {
      setIsReady(false);
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
      return;
    }

    setIsReady(false);
    let attempts = 0;
    const maxAttempts = 12; // 12 * 350ms = 4.2s max

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

  // Fast-path auto stop when user refreshes (F5) or closes the browser window/tab
  useEffect(() => {
    if (!open || !camera) return;

    const handleUnload = () => {
      try {
        const payload = JSON.stringify({
          camera_name: camera.name,
          topic_prefix: plant?.cameraTopic || 'slam/cameras',
        });
        const blob = new Blob([payload], { type: 'application/json' });
        navigator.sendBeacon('/api/stream/camera/disable', blob);
      } catch (e) {
        // Ignore fallback
      }
    };

    window.addEventListener('beforeunload', handleUnload);
    window.addEventListener('pagehide', handleUnload);
    return () => {
      window.removeEventListener('beforeunload', handleUnload);
      window.removeEventListener('pagehide', handleUnload);
    };
  }, [open, camera, plant]);

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

  // 1. Fullscreen Maximized Modal View (Only triggered when user explicitly clicks Maximize)
  if (isMaximized) {
    return createPortal(
      <div
        className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200"
        onClick={(e) => {
          if (e.target === e.currentTarget) {
            setIsMaximized(false);
          }
        }}
      >
        <div className="relative w-full max-w-4xl bg-[#0b111e] border border-cyan-800/80 rounded-2xl shadow-2xl overflow-hidden flex flex-col animate-in zoom-in-95 duration-150">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-800 bg-[#0d1527]">
            <div className="flex items-center space-x-3">
              <div
                className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors ${
                  isReady ? 'bg-emerald-500/20 text-emerald-400' : 'bg-cyan-500/20 text-cyan-400'
                }`}
              >
                <Tv className="w-4 h-4" />
              </div>
              <div>
                <div className="flex items-center space-x-2">
                  <h3 className="text-sm font-bold text-slate-100">{camera.display_name || camera.name}</h3>
                  <Badge
                    variant={isReady ? 'success' : 'outline'}
                    className={`text-[10px] px-2 flex items-center space-x-1 ${
                      !isReady ? 'border-cyan-500/50 text-cyan-300' : ''
                    }`}
                  >
                    <span
                      className={`w-1.5 h-1.5 rounded-full mr-1 ${
                        isReady ? 'bg-emerald-300 animate-pulse' : 'bg-cyan-400 animate-ping'
                      }`}
                    />
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
              <Button
                size="sm"
                variant="outline"
                onClick={() => setIsMaximized(false)}
                className="h-8 px-2.5 text-xs border-slate-700 bg-slate-900 text-cyan-400 hover:text-white"
                title="還原為地圖右上角小視窗 (PiP)"
              >
                <Minimize2 className="w-3.5 h-3.5 mr-1" />
                右上角縮圖
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

          {/* Video Player */}
          <div className="relative aspect-video w-full bg-slate-950 flex items-center justify-center overflow-hidden border-b border-slate-800">
            <iframe
              key={iframeKey}
              src={webrtcUrl}
              title={camera.name}
              className="w-full h-full border-0"
              allow="autoplay; fullscreen"
            />
            {!isReady && (
              <div className="absolute inset-0 bg-[#070b14] flex flex-col items-center justify-center p-6 text-center z-20">
                <div className="relative mb-4">
                  <div className="w-16 h-16 rounded-2xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-cyan-400 shadow-xl shadow-cyan-500/20">
                    <Video className="w-8 h-8 animate-pulse" />
                  </div>
                  <span className="absolute -top-1 -right-1 flex h-4 w-4">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-4 w-4 bg-cyan-500"></span>
                  </span>
                </div>
                <h4 className="text-base font-bold text-slate-100 flex items-center mb-1.5">
                  <Loader2 className="w-4 h-4 mr-2 animate-spin text-cyan-400" />
                  相機開啟中...
                </h4>
                <p className="text-xs text-slate-400 max-w-sm mb-4">
                  已透過 MQTT 發送啟動訊號，正在等待設備端取像並建立 WebRTC 低延遲通道...
                </p>
                <div className="w-64 h-1.5 bg-slate-900 rounded-full overflow-hidden border border-slate-800">
                  <div className="h-full bg-gradient-to-r from-cyan-500 via-teal-400 to-emerald-400 w-full animate-pulse" />
                </div>
              </div>
            )}
          </div>

          {/* Footer Controls */}
          <div className="p-4 bg-[#0d1322] flex items-center justify-between">
            <Button
              size="sm"
              variant="destructive"
              onClick={() => onOpenChange(false)}
              className="h-8 text-xs font-semibold px-3 bg-rose-600 hover:bg-rose-500 shadow-sm"
            >
              <Power className="w-3.5 h-3.5 mr-1.5" />
              關閉畫面（自動關閉相機）
            </Button>
            <div className="flex items-center space-x-2">
              <button
                onClick={handleCopyWebRTC}
                className="text-xs px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-slate-300 hover:text-white flex items-center space-x-1"
              >
                {copiedWebRTC ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                <span>複製 WebRTC</span>
              </button>
              <button
                onClick={handleCopyRTSP}
                className="text-xs px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-slate-300 hover:text-white flex items-center space-x-1"
              >
                {copiedRTSP ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                <span>複製 RTSP</span>
              </button>
              <a
                href={webrtcUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-cyan-400 hover:text-cyan-300 flex items-center ml-2"
              >
                <ExternalLink className="w-3.5 h-3.5 mr-1" />
                新分頁
              </a>
            </div>
          </div>
        </div>
      </div>,
      document.body
    );
  }

  // 2. Default View: Picture-in-Picture floating in the top-right corner of the map!
  // No full-screen backdrop, avoids blocking map canvas.
  const pipCardContent = (
    <div
      className={`w-[360px] sm:w-[420px] max-w-[calc(100vw-2rem)] bg-[#0b111e]/95 backdrop-blur-md border border-cyan-700/80 rounded-2xl shadow-2xl overflow-hidden flex flex-col ring-1 ring-cyan-500/20 animate-in fade-in slide-in-from-top-4 duration-200 select-none ${
        mountTarget === document.body
          ? 'fixed top-18 right-4 md:right-[336px] z-30 pointer-events-auto'
          : ''
      }`}
    >
      {/* Header Bar */}
      <div
        className="flex items-center justify-between px-3 py-2 bg-[#0d1527]/95 border-b border-slate-800/90 cursor-pointer"
        onClick={() => setIsCollapsed(!isCollapsed)}
        title={isCollapsed ? '展開即時畫面' : '收合即時畫面'}
      >
        <div className="flex items-center space-x-2 min-w-0 flex-1 mr-2">
          <div
            className={`w-6 h-6 rounded-lg flex items-center justify-center shrink-0 transition-colors ${
              isReady ? 'bg-emerald-500/20 text-emerald-400' : 'bg-cyan-500/20 text-cyan-400'
            }`}
          >
            <Tv className="w-3.5 h-3.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center space-x-1.5">
              <h4 className="text-xs font-bold text-slate-100 truncate" title={camera.display_name || camera.name}>
                {camera.display_name || camera.name}
              </h4>
              <Badge
                variant={isReady ? 'success' : 'outline'}
                className={`text-[9px] px-1.5 py-0 flex items-center shrink-0 font-mono ${
                  !isReady ? 'border-cyan-500/50 text-cyan-300' : ''
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full mr-1 ${
                    isReady ? 'bg-emerald-300 animate-pulse' : 'bg-cyan-400 animate-ping'
                  }`}
                />
                <span>{isReady ? 'Live' : '開啟中'}</span>
              </Badge>
            </div>
            <p className="text-[10px] text-slate-400 font-mono truncate">
              {camera.width}x{camera.height} @ {camera.fps}fps
              {plant ? ` • ${plant.name}` : ''}
            </p>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center space-x-1 shrink-0" onClick={(e) => e.stopPropagation()}>
          {/* Refresh Stream */}
          <button
            onClick={() => {
              setIsReady(false);
              setIframeKey((k) => k + 1);
              setTimeout(() => setIsReady(true), 1200);
            }}
            className="text-slate-400 hover:text-white p-1 rounded hover:bg-slate-800 transition-colors"
            title="重新載入畫面"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
          {/* Collapse / Expand Video */}
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="text-slate-400 hover:text-white p-1 rounded hover:bg-slate-800 transition-colors"
            title={isCollapsed ? '展開即時畫面' : '收合即時畫面'}
          >
            {isCollapsed ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />}
          </button>
          {/* Maximize to Center Dialog */}
          <button
            onClick={() => setIsMaximized(true)}
            className="text-slate-400 hover:text-cyan-400 p-1 rounded hover:bg-slate-800 transition-colors"
            title="全螢幕放大檢視"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
          {/* Close & Stop Camera via MQTT */}
          <button
            onClick={() => onOpenChange(false)}
            className="text-slate-400 hover:text-rose-400 p-1 rounded hover:bg-slate-800 transition-colors"
            title="關閉畫面（自動關閉相機）"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Video Content & Footer Controls (Collapsible) */}
      {!isCollapsed && (
        <>
          <div className="relative aspect-video w-full bg-slate-950 flex items-center justify-center overflow-hidden border-b border-slate-800">
            <iframe
              key={iframeKey}
              src={webrtcUrl}
              title={camera.name}
              className="w-full h-full border-0"
              allow="autoplay; fullscreen"
            />

            {/* Smooth Startup Overlay */}
            <div
              className={`absolute inset-0 bg-[#070b14] flex flex-col items-center justify-center p-4 text-center z-20 transition-all duration-500 ${
                isReady ? 'opacity-0 pointer-events-none' : 'opacity-100'
              }`}
            >
              <div className="relative mb-2.5">
                <div className="w-10 h-10 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-cyan-400 shadow-md shadow-cyan-500/20">
                  <Video className="w-5 h-5 animate-pulse" />
                </div>
                <span className="absolute -top-0.5 -right-0.5 flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-cyan-500"></span>
                </span>
              </div>
              <h4 className="text-xs font-bold text-slate-100 flex items-center mb-1">
                <Loader2 className="w-3 h-3 mr-1.5 animate-spin text-cyan-400" />
                相機開啟中...
              </h4>
              <p className="text-[10px] text-slate-400 max-w-xs mb-2">
                已發送 MQTT 訊號，正在建立 WebRTC 串流通道...
              </p>
              <div className="w-44 h-1 bg-slate-900 rounded-full overflow-hidden border border-slate-800">
                <div className="h-full bg-gradient-to-r from-cyan-500 via-teal-400 to-emerald-400 w-full animate-pulse" />
              </div>
            </div>
          </div>

          {/* Compact PiP Footer Bar */}
          <div className="px-3 py-1.5 bg-[#0d1322] flex items-center justify-between text-[11px] border-t border-slate-800/80">
            <div className="flex items-center space-x-1.5">
              <Button
                size="sm"
                variant="destructive"
                onClick={() => onOpenChange(false)}
                className="h-6 text-[10px] font-medium px-2 bg-rose-600 hover:bg-rose-500 shadow-sm"
                title="關閉此視窗並發送指令關閉相機"
              >
                <Power className="w-3 h-3 mr-1" />
                關閉相機
              </Button>
            </div>

            <div className="flex items-center space-x-1.5">
              <button
                onClick={handleCopyWebRTC}
                className="text-[10px] px-2 py-0.5 rounded border border-slate-700 bg-slate-900 text-slate-300 hover:text-white flex items-center space-x-1"
                title="複製 WebRTC 播放連結"
              >
                {copiedWebRTC ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3 text-slate-400" />}
                <span>WebRTC</span>
              </button>
              <button
                onClick={handleCopyRTSP}
                className="text-[10px] px-2 py-0.5 rounded border border-slate-700 bg-slate-900 text-slate-300 hover:text-white flex items-center space-x-1"
                title="複製 RTSP 串流連結"
              >
                {copiedRTSP ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3 text-slate-400" />}
                <span>RTSP</span>
              </button>
              <a
                href={webrtcUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-slate-400 hover:text-cyan-300 p-1 rounded"
                title="在新分頁獨立開啟"
              >
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          </div>
        </>
      )}
    </div>
  );

  // If a dedicated portal mount element (#map-camera-pip-target) exists, portal inside it
  if (mountTarget && mountTarget !== document.body) {
    return createPortal(pipCardContent, mountTarget);
  }

  // Fallback to body with fixed top-right positioning
  return createPortal(pipCardContent, document.body);
}
