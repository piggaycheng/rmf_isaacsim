import React, { useState, useEffect, useRef } from 'react';
import { PlantSite } from '@/types/rmf';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  X,
  Maximize2,
  Minimize2,
  RefreshCw,
  Sliders,
  Radio,
  Video,
  VideoOff,
  Cpu,
  Eye,
  Camera,
  Layers,
  ArrowRight,
  Wifi,
  WifiOff,
  CheckCircle2,
  Info,
} from 'lucide-react';

interface IsaacSimStreamModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  plant: PlantSite | null;
  onNavigateToMonitor?: (plant: PlantSite) => void;
  onNavigateToEdit?: (plant: PlantSite) => void;
}

export function IsaacSimStreamModal({
  open,
  onOpenChange,
  plant,
  onNavigateToMonitor,
  onNavigateToEdit,
}: IsaacSimStreamModalProps) {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [signalingUrl, setSignalingUrl] = useState(plant?.isaacSimWebRTCUrl || 'ws://localhost:8080/webrtc');
  const [connectionState, setConnectionState] = useState<'connected' | 'connecting' | 'fallback' | 'disconnected'>('fallback');
  const [cameraView, setCameraView] = useState<'overhead' | 'robot_follow' | 'workcell'>('overhead');
  const [fps, setFps] = useState(60);
  const [latency, setLatency] = useState(14);
  const [statusMessage, setStatusMessage] = useState('展示模擬串流模式 (待機連線中)');

  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const animFrameRef = useRef<number | null>(null);

  // Sync plant URL when plant changes
  useEffect(() => {
    if (plant?.isaacSimWebRTCUrl) {
      setSignalingUrl(plant.isaacSimWebRTCUrl);
    }
  }, [plant]);

  // Attempt real WebRTC connection if user hits Connect
  const handleConnectWebRTC = () => {
    setConnectionState('connecting');
    setStatusMessage(`正在連線至 WebRTC 信令伺服器 ${signalingUrl}...`);

    try {
      if (pcRef.current) {
        pcRef.current.close();
      }
      if (wsRef.current) {
        wsRef.current.close();
      }

      // Initialize WebRTC Peer Connection
      const pc = new RTCPeerConnection({
        iceServers: [
          { urls: 'stun:stun.l.google.com:19302' },
        ],
      });
      pcRef.current = pc;

      // Prepare to receive video track
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });

      pc.ontrack = (event) => {
        if (videoRef.current && event.streams[0]) {
          videoRef.current.srcObject = event.streams[0];
          setConnectionState('connected');
          setStatusMessage('WebRTC 串流連線成功！即時畫面接收中');
        }
      };

      // Try WebSocket signaling
      const ws = new WebSocket(signalingUrl);
      wsRef.current = ws;

      const connectionTimeout = setTimeout(() => {
        if (connectionState === 'connecting') {
          setConnectionState('fallback');
          setStatusMessage('本地未偵測到 Isaac Sim WebRTC 服務，切換至高精數位雙生模擬畫面');
        }
      }, 3500);

      ws.onopen = async () => {
        clearTimeout(connectionTimeout);
        try {
          const offer = await pc.createOffer();
          await pc.setLocalDescription(offer);
          ws.send(JSON.stringify({ type: 'offer', sdp: offer.sdp }));
          setStatusMessage('信令已送出，等待 Isaac Sim 串流應答 (SDP Answer)...');
        } catch (err) {
          console.error('WebRTC offer error:', err);
        }
      };

      ws.onmessage = async (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (data.type === 'answer' && data.sdp) {
            await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: data.sdp }));
            setConnectionState('connected');
            setStatusMessage('Isaac Sim WebRTC 串流連線就緒');
          } else if (data.candidate) {
            await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
          }
        } catch (e) {
          console.error('Error handling signaling message:', e);
        }
      };

      ws.onerror = () => {
        clearTimeout(connectionTimeout);
        setConnectionState('fallback');
        setStatusMessage('本地未偵測到 Isaac Sim 服務 (可確認 8080/8011 埠號是否啟動)，已開啟數位雙生模擬畫面');
      };
    } catch (err) {
      setConnectionState('fallback');
      setStatusMessage('WebRTC 初始連線超時，啟動高精數位雙生即時預覽');
    }
  };

  // Close connection on unmount or modal close
  useEffect(() => {
    if (!open) {
      if (pcRef.current) pcRef.current.close();
      if (wsRef.current) wsRef.current.close();
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      setConnectionState('fallback');
    }
  }, [open]);

  // Fullscreen Toggle
  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen().catch(() => {});
      setIsFullscreen(true);
    } else {
      document.exitFullscreen().catch(() => {});
      setIsFullscreen(false);
    }
  };

  // Canvas Digital Twin Fallback Simulation Renderer (Isaac Sim Warehouse Simulator)
  useEffect(() => {
    if (!open || connectionState === 'connected') return;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let t = 0;

    // Simulated robots
    const simRobots = [
      { id: 'AMR-01 (tinyRobot)', color: '#38bdf8', cx: 300, cy: 220, rx: 160, ry: 90, speed: 0.008, angle: 0 },
      { id: 'AMR-02 (delivery)', color: '#10b981', cx: 500, cy: 300, rx: 120, ry: 140, speed: 0.006, angle: Math.PI / 2 },
      { id: 'FORK-03 (pallet)', color: '#f59e0b', cx: 720, cy: 240, rx: 90, ry: 110, speed: 0.005, angle: Math.PI },
    ];

    const render = () => {
      t += 1;
      const width = canvas.width;
      const height = canvas.height;

      // Background
      ctx.fillStyle = '#060a12';
      ctx.fillRect(0, 0, width, height);

      // Floor Grid (Perspective / 3D Grid)
      ctx.save();
      if (cameraView === 'robot_follow') {
        const lead = simRobots[0];
        const lx = lead.cx + Math.cos(lead.angle) * lead.rx;
        const ly = lead.cy + Math.sin(lead.angle) * lead.ry;
        ctx.translate(width / 2 - lx * 1.2, height / 2 - ly * 1.2);
        ctx.scale(1.2, 1.2);
      } else if (cameraView === 'workcell') {
        ctx.translate(-150, -50);
        ctx.scale(1.15, 1.15);
      }

      // Warehouse Grid lines
      ctx.strokeStyle = '#111d33';
      ctx.lineWidth = 1;
      const gridSize = 40;
      for (let x = 0; x < width * 1.5; x += gridSize) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height * 1.5);
        ctx.stroke();
      }
      for (let y = 0; y < height * 1.5; y += gridSize) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width * 1.5, y);
        ctx.stroke();
      }

      // Warehouse Racks / Obstacles
      const racks = [
        { x: 120, y: 80, w: 100, h: 400, label: '貨架區 A (Rack-A)' },
        { x: 380, y: 80, w: 100, h: 160, label: '自動立庫 B1' },
        { x: 380, y: 320, w: 100, h: 160, label: '自動立庫 B2' },
        { x: 640, y: 80, w: 120, h: 400, label: '托盤出入庫區 C' },
        { x: 880, y: 140, w: 80, h: 280, label: '機械手臂工作站 D' },
      ];

      racks.forEach((rack) => {
        // Drop shadow
        ctx.fillStyle = 'rgba(0, 0, 0, 0.4)';
        ctx.fillRect(rack.x + 8, rack.y + 8, rack.w, rack.h);

        // Rack body
        ctx.fillStyle = '#101a2e';
        ctx.strokeStyle = '#1e3a5f';
        ctx.lineWidth = 1.5;
        ctx.fillRect(rack.x, rack.y, rack.w, rack.h);
        ctx.strokeRect(rack.x, rack.y, rack.w, rack.h);

        // Shelves stripes
        ctx.strokeStyle = '#1d2f4a';
        for (let sy = rack.y + 25; sy < rack.y + rack.h; sy += 30) {
          ctx.beginPath();
          ctx.moveTo(rack.x + 4, sy);
          ctx.lineTo(rack.x + rack.w - 4, sy);
          ctx.stroke();
        }

        // Rack Label
        ctx.fillStyle = '#64748b';
        ctx.font = '10px monospace';
        ctx.fillText(rack.label, rack.x + 6, rack.y + 16);
      });

      // Navigation Lanes
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.25)';
      ctx.setLineDash([6, 6]);
      ctx.lineWidth = 2;

      // Draw path loops
      ctx.beginPath();
      ctx.ellipse(300, 220, 160, 90, 0, 0, Math.PI * 2);
      ctx.stroke();

      ctx.beginPath();
      ctx.ellipse(500, 300, 120, 140, 0, 0, Math.PI * 2);
      ctx.stroke();

      ctx.beginPath();
      ctx.ellipse(720, 240, 90, 110, 0, 0, Math.PI * 2);
      ctx.stroke();

      ctx.setLineDash([]);

      // Workcells & Charger Stations
      const stations = [
        { x: 260, y: 440, type: '⚡ 充電樁 C1', color: '#f59e0b' },
        { x: 50, y: 240, type: '📦 取貨站 P1', color: '#10b981' },
        { x: 880, y: 280, type: '🤖 機械手臂站 WS-1', color: '#38bdf8' },
      ];

      stations.forEach((st) => {
        ctx.fillStyle = '#1e293b';
        ctx.strokeStyle = st.color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(st.x, st.y, 16, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = st.color;
        ctx.font = '11px sans-serif';
        ctx.fillText(st.type, st.x - 30, st.y + 32);
      });

      // Render AMRs
      simRobots.forEach((robot) => {
        robot.angle += robot.speed;
        const rx = robot.cx + Math.cos(robot.angle) * robot.rx;
        const ry = robot.cy + Math.sin(robot.angle) * robot.ry;
        const yaw = Math.atan2(
          -Math.sin(robot.angle) * robot.rx,
          Math.cos(robot.angle) * robot.ry
        );

        // LiDAR Sensing Arc / Scan
        ctx.save();
        ctx.translate(rx, ry);
        ctx.rotate(yaw);

        // Lidar cone
        const lidarRadius = 65;
        const sweep = (t * 0.08) % (Math.PI * 2);
        const grad = ctx.createRadialGradient(0, 0, 5, 0, 0, lidarRadius);
        grad.addColorStop(0, 'rgba(56, 189, 248, 0.4)');
        grad.addColorStop(1, 'rgba(56, 189, 248, 0.0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.arc(0, 0, lidarRadius, sweep - 0.4, sweep + 0.4);
        ctx.closePath();
        ctx.fill();

        // Robot Chassis
        ctx.fillStyle = '#1e293b';
        ctx.strokeStyle = robot.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.roundRect(-16, -12, 32, 24, 6);
        ctx.fill();
        ctx.stroke();

        // Heading Direction Pointer
        ctx.fillStyle = robot.color;
        ctx.beginPath();
        ctx.moveTo(12, 0);
        ctx.lineTo(22, 0);
        ctx.stroke();

        // Wheels
        ctx.fillStyle = '#475569';
        ctx.fillRect(-8, -15, 16, 3);
        ctx.fillRect(-8, 12, 16, 3);

        ctx.restore();

        // Telemetry tag above robot
        ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
        ctx.strokeStyle = '#334155';
        ctx.lineWidth = 1;
        ctx.roundRect(rx - 48, ry - 38, 96, 20, 4);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = '#f8fafc';
        ctx.font = '9px monospace';
        ctx.fillText(robot.id.split(' ')[0], rx - 42, ry - 24);

        // Battery dot
        ctx.fillStyle = '#10b981';
        ctx.beginPath();
        ctx.arc(rx + 38, ry - 28, 3, 0, Math.PI * 2);
        ctx.fill();
      });

      ctx.restore();

      // Camera HUD Scanlines / Watermark
      ctx.fillStyle = 'rgba(255, 255, 255, 0.02)';
      for (let y = 0; y < height; y += 4) {
        ctx.fillRect(0, y, width, 1);
      }

      // Jitter stats slightly for realism
      if (t % 30 === 0) {
        setFps(Math.floor(58 + Math.random() * 4));
        setLatency(Math.floor(12 + Math.random() * 5));
      }

      animFrameRef.current = requestAnimationFrame(render);
    };

    animFrameRef.current = requestAnimationFrame(render);

    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    };
  }, [open, connectionState, cameraView]);

  if (!open || !plant) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4">
      <div
        ref={containerRef}
        className={`bg-[#080d19] border border-slate-700/80 rounded-2xl shadow-2xl flex flex-col overflow-hidden transition-all ${
          isFullscreen ? 'w-screen h-screen rounded-none border-none' : 'w-[96vw] max-w-6xl h-[88vh]'
        }`}
      >
        {/* 1. Modal Header */}
        <header className="h-14 border-b border-slate-800 bg-[#0c1322] px-4 flex items-center justify-between z-20 shrink-0">
          <div className="flex items-center space-x-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-emerald-500 to-cyan-500 flex items-center justify-center text-white shadow-md shadow-emerald-500/20">
              <Video className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h2 className="font-bold text-sm text-slate-100 flex items-center space-x-2">
                  <span>NVIDIA Isaac Sim™ 數位雙生模擬畫面</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                    WebRTC Stream
                  </span>
                </h2>
              </div>
              <p className="text-[11px] text-slate-400">
                廠區：<span className="text-slate-200 font-semibold">{plant.name}</span> ({plant.code})
              </p>
            </div>
          </div>

          {/* Center Connection Indicator */}
          <div className="hidden md:flex items-center space-x-2 bg-slate-900/80 border border-slate-800 px-3 py-1 rounded-full text-xs">
            {connectionState === 'connected' ? (
              <>
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                <span className="text-emerald-400 font-medium">即時 WebRTC 串流連線中</span>
              </>
            ) : (
              <>
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                <span className="text-cyan-300 font-medium">高精數位雙生模擬展示中</span>
              </>
            )}
            <span className="text-slate-600">|</span>
            <span className="text-slate-400 font-mono">{fps} FPS</span>
            <span className="text-slate-600">|</span>
            <span className="text-slate-400 font-mono">{latency} ms</span>
          </div>

          {/* Right Header Actions */}
          <div className="flex items-center space-x-2">
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs bg-slate-900/60 border-slate-700"
              onClick={() => setShowSettings(!showSettings)}
              title="WebRTC 串流信令伺服器設定"
            >
              <Sliders className="w-3.5 h-3.5 mr-1" />
              串流設定
            </Button>

            <Button
              variant="outline"
              size="sm"
              className="h-8 w-8 p-0 bg-slate-900/60 border-slate-700"
              onClick={handleConnectWebRTC}
              title="重新嘗試連線至 Isaac Sim WebRTC"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </Button>

            <Button
              variant="outline"
              size="sm"
              className="h-8 w-8 p-0 bg-slate-900/60 border-slate-700"
              onClick={toggleFullscreen}
              title={isFullscreen ? '退出全螢幕' : '全螢幕觀看'}
            >
              {isFullscreen ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
            </Button>

            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0 text-slate-400 hover:text-white"
              onClick={() => onOpenChange(false)}
            >
              <X className="w-4 h-4" />
            </Button>
          </div>
        </header>

        {/* 2. Optional Settings Dropdown / Drawer */}
        {showSettings && (
          <div className="bg-slate-900 border-b border-slate-800 px-4 py-3 flex flex-wrap items-center justify-between gap-3 text-xs">
            <div className="flex items-center space-x-2 flex-1 min-w-[280px]">
              <span className="text-slate-400 whitespace-nowrap">Isaac Sim 信令 URL:</span>
              <Input
                value={signalingUrl}
                onChange={(e) => setSignalingUrl(e.target.value)}
                placeholder="ws://localhost:8080/webrtc 或 http://localhost:8011"
                className="h-7 text-xs bg-slate-950 border-slate-700 flex-1 font-mono"
              />
              <Button size="sm" className="h-7 text-xs bg-emerald-600 hover:bg-emerald-500" onClick={handleConnectWebRTC}>
                連線
              </Button>
            </div>
            <div className="flex items-center space-x-2 text-slate-400">
              <Info className="w-3.5 h-3.5 text-cyan-400" />
              <span>Isaac Sim 預設 WebRTC 串流埠號為 8011 / 8080 (Omniverse Streaming Client)</span>
            </div>
          </div>
        )}

        {/* 3. Main Stream Viewport Area */}
        <div className="flex-1 relative bg-black flex items-center justify-center overflow-hidden">
          {/* Real WebRTC video tag */}
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className={`w-full h-full object-contain ${connectionState === 'connected' ? 'block' : 'hidden'}`}
          />

          {/* High-tech Canvas Simulation when offline or demo */}
          {connectionState !== 'connected' && (
            <canvas
              ref={canvasRef}
              width={1280}
              height={720}
              className="w-full h-full object-contain cursor-crosshair"
            />
          )}

          {/* Overlay Top-Left: Camera Controls & View Switcher */}
          <div className="absolute top-4 left-4 flex flex-col space-y-2 pointer-events-auto">
            <div className="flex items-center bg-slate-900/85 backdrop-blur-md border border-slate-800 p-1 rounded-lg shadow-lg">
              <button
                onClick={() => setCameraView('overhead')}
                className={`flex items-center space-x-1.5 px-2.5 py-1 rounded text-xs transition-colors ${
                  cameraView === 'overhead' ? 'bg-primary text-white font-medium' : 'text-slate-400 hover:text-white'
                }`}
              >
                <Eye className="w-3.5 h-3.5" />
                <span>俯瞰全景</span>
              </button>
              <button
                onClick={() => setCameraView('robot_follow')}
                className={`flex items-center space-x-1.5 px-2.5 py-1 rounded text-xs transition-colors ${
                  cameraView === 'robot_follow' ? 'bg-primary text-white font-medium' : 'text-slate-400 hover:text-white'
                }`}
              >
                <Camera className="w-3.5 h-3.5" />
                <span>車隊視角 (AMR-01)</span>
              </button>
              <button
                onClick={() => setCameraView('workcell')}
                className={`flex items-center space-x-1.5 px-2.5 py-1 rounded text-xs transition-colors ${
                  cameraView === 'workcell' ? 'bg-primary text-white font-medium' : 'text-slate-400 hover:text-white'
                }`}
              >
                <Layers className="w-3.5 h-3.5" />
                <span>工作站取放角</span>
              </button>
            </div>

            {/* Omniverse Live Telemetry Pill */}
            <div className="bg-slate-900/80 backdrop-blur border border-slate-800/80 px-2.5 py-1.5 rounded-lg text-[11px] text-slate-300 font-mono space-y-0.5 shadow">
              <div className="flex justify-between items-center text-slate-400 text-[10px]">
                <span>OMNIVERSE RENDER</span>
                <span className="text-emerald-400 font-bold">RTX REALTIME</span>
              </div>
              <div>解析度: 1920 × 1080 @ {fps}fps</div>
              <div>編碼格式: H.264 / WebRTC NVENC</div>
              <div>ROS 2 網橋: /isaac_sim/rmf_fleet_states</div>
            </div>
          </div>

          {/* Overlay Top-Right: Plant Quick Info */}
          <div className="absolute top-4 right-4 bg-slate-900/85 backdrop-blur-md border border-slate-800 p-3 rounded-xl shadow-lg text-xs space-y-1.5 max-w-[260px] pointer-events-auto">
            <div className="flex items-center justify-between">
              <span className="font-bold text-slate-100">{plant.name}</span>
              <Badge variant="outline" className="text-[10px] border-emerald-500/40 text-emerald-400">
                {plant.robotCount} 台車運行
              </Badge>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">{plant.description}</p>
            <div className="text-[10px] text-slate-500 pt-1 border-t border-slate-800 flex justify-between">
              <span>園區面積: {plant.areaM2.toLocaleString()} m²</span>
              <span>進行任務: {plant.activeTasks} 件</span>
            </div>
          </div>

          {/* Overlay Bottom Status Notification */}
          <div className="absolute bottom-4 left-4 bg-slate-900/90 backdrop-blur border border-slate-800 px-3 py-1.5 rounded-lg text-xs flex items-center space-x-2 text-slate-300 pointer-events-auto">
            <Cpu className="w-3.5 h-3.5 text-cyan-400 animate-pulse" />
            <span>{statusMessage}</span>
          </div>
        </div>

        {/* 4. Bottom Footer Actions */}
        <footer className="h-14 border-t border-slate-800 bg-[#0c1322] px-4 flex items-center justify-between shrink-0">
          <div className="flex items-center space-x-2 text-xs text-slate-400">
            <span>連線端點:</span>
            <code className="bg-slate-950 px-2 py-0.5 rounded border border-slate-800 text-slate-300 font-mono text-[11px]">
              {signalingUrl}
            </code>
          </div>

          <div className="flex items-center space-x-3">
            {onNavigateToEdit && (
              <Button
                variant="outline"
                size="sm"
                className="border-slate-700 text-slate-300 hover:text-white"
                onClick={() => {
                  onOpenChange(false);
                  onNavigateToEdit(plant);
                }}
              >
                ✏️ 編輯此廠區路網
              </Button>
            )}

            {onNavigateToMonitor && (
              <Button
                size="sm"
                className="bg-primary hover:bg-primary/90 text-primary-foreground font-medium shadow-md shadow-primary/20"
                onClick={() => {
                  onOpenChange(false);
                  onNavigateToMonitor(plant);
                }}
              >
                <Radio className="w-3.5 h-3.5 mr-1.5" />
                進入此廠區即時監控
                <ArrowRight className="w-3.5 h-3.5 ml-1" />
              </Button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}
