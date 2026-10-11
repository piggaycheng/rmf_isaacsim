import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  CameraInfo,
  PlantSite,
  GimbalCommandPayload,
  GimbalStatePayload,
  GimbalResultPayload,
  GimbalStatusPayload,
} from '@/types/rmf';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  ChevronUp,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  RotateCcw,
  Target,
  Sliders,
  Compass,
  ArrowUpLeft,
  ArrowUpRight,
  ArrowDownLeft,
  ArrowDownRight,
  Gamepad2,
  Check,
  AlertTriangle,
  XCircle,
  Square,
  Activity,
  Send,
  Wifi,
  WifiOff,
} from 'lucide-react';
import mqtt, { MqttClient } from 'mqtt';

interface CameraPtzControllerProps {
  camera: CameraInfo;
  plant: PlantSite | null;
  className?: string;
  defaultExpanded?: boolean;
}

export function CameraPtzController({
  camera,
  plant,
  className = '',
  defaultExpanded = true,
}: CameraPtzControllerProps) {
  // Real-time Gimbal posture & status
  const [pan, setPan] = useState<number>(0.0);
  const [tilt, setTilt] = useState<number>(0.0);
  const [isBusy, setIsBusy] = useState<boolean>(false);
  const [isGimbalOnline, setIsGimbalOnline] = useState<boolean>(true);
  const [mqttConnected, setMqttConnected] = useState<boolean>(false);

  // Controller UI settings
  const [step, setStep] = useState<number>(5.0); // 1°, 5°, 15°, 30°
  const [isExpanded, setIsExpanded] = useState<boolean>(defaultExpanded);
  const [activeDirection, setActiveDirection] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<GimbalResultPayload | null>(null);
  const [resultToast, setResultToast] = useState<{ status: string; message: string } | null>(null);

  // Manual absolute target inputs
  const [inputPan, setInputPan] = useState<string>('0');
  const [inputTilt, setInputTilt] = useState<string>('0');

  const mqttClientRef = useRef<MqttClient | null>(null);
  const cmdIdRef = useRef<number>(1);
  const toastTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Resolve target MQTT topics strictly according to docs/camera_mqtt_control.md
  const { commandTopic, resultTopic, stateTopic, statusTopic, gimbalName } = useMemo(() => {
    let cmdTopic = camera.gimbal_topic;

    if (!cmdTopic) {
      const cleanName = camera.name.startsWith('/') ? camera.name.substring(1) : camera.name;
      let targetNs = cleanName;
      if (cleanName.includes('ceiling_cams')) {
        targetNs = cleanName;
      } else if (cleanName.includes('/camera/gimbal')) {
        targetNs = cleanName.split('/camera/gimbal')[0];
      } else if (cleanName.includes('/')) {
        targetNs = cleanName.split('/')[0];
      }
      cmdTopic = `slam/gimbal/${targetNs}/command`;
    }

    const resTopic = cmdTopic.replace(/\/command$/, '/result');
    const stTopic = cmdTopic.replace(/\/command$/, '/state');
    const statTopic = cmdTopic.replace(/\/command$/, '/status');

    // Extract human-readable gimbal identifier (e.g., "carter1" or "ceiling_cams/ceiling_cam_1")
    const match = cmdTopic.match(/slam\/gimbal\/(.+)\/command/);
    const gName = match ? match[1] : camera.display_name || camera.name;

    return {
      commandTopic: cmdTopic,
      resultTopic: resTopic,
      stateTopic: stTopic,
      statusTopic: statTopic,
      gimbalName: gName,
    };
  }, [camera.gimbal_topic, camera.name, camera.display_name]);

  // Connect to EMQX MQTT Broker via WebSocket
  useEffect(() => {
    const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const brokerUrl = plant?.mqttBrokerUrl || `${protocol}//${host}:8083/mqtt`;
    const clientId = `web_ptz_${Math.random().toString(16).substring(2, 8)}`;

    const client = mqtt.connect(brokerUrl, {
      clientId,
      clean: true,
      connectTimeout: 4000,
      reconnectPeriod: 4000,
    });
    mqttClientRef.current = client;

    client.on('connect', () => {
      console.log(`[PTZ MQTT] Connected to broker: ${brokerUrl}`);
      setMqttConnected(true);

      // Subscribe to Gimbal state (QoS 0), result (QoS 1), and status (QoS 1)
      client.subscribe([resultTopic, statusTopic], { qos: 1 });
      client.subscribe(stateTopic, { qos: 0 });
      console.log(`[PTZ MQTT] Subscribed to ${resultTopic}, ${stateTopic}, ${statusTopic}`);
    });

    client.on('message', (topic, message) => {
      try {
        const raw = message.toString();
        const data = JSON.parse(raw);

        if (topic === stateTopic) {
          const stateData = data as GimbalStatePayload;
          if (typeof stateData.pan_deg === 'number') {
            setPan(parseFloat(stateData.pan_deg.toFixed(1)));
          }
          if (typeof stateData.tilt_deg === 'number') {
            setTilt(parseFloat(stateData.tilt_deg.toFixed(1)));
          }
          if (typeof stateData.busy === 'boolean') {
            setIsBusy(stateData.busy);
          }
        } else if (topic === resultTopic) {
          const resData = data as GimbalResultPayload;
          setLastResult(resData);
          if (typeof resData.pan_deg === 'number') {
            setPan(parseFloat(resData.pan_deg.toFixed(1)));
          }
          if (typeof resData.tilt_deg === 'number') {
            setTilt(parseFloat(resData.tilt_deg.toFixed(1)));
          }

          // Trigger result toast message
          setResultToast({
            status: resData.status,
            message: resData.message || resData.status,
          });
          if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
          toastTimeoutRef.current = setTimeout(() => setResultToast(null), 3000);
        } else if (topic === statusTopic) {
          const statusData = data as GimbalStatusPayload;
          if (typeof statusData.online === 'boolean') {
            setIsGimbalOnline(statusData.online);
          }
        }
      } catch (err) {
        console.error(`[PTZ MQTT] Error parsing message on ${topic}:`, err);
      }
    });

    client.on('close', () => {
      setMqttConnected(false);
    });

    client.on('error', (err) => {
      console.error('[PTZ MQTT] Client error:', err);
      setMqttConnected(false);
    });

    return () => {
      if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
      if (mqttClientRef.current) {
        mqttClientRef.current.end(true);
        mqttClientRef.current = null;
      }
    };
  }, [plant?.mqttBrokerUrl, commandTopic, resultTopic, stateTopic, statusTopic]);

  // Publish generic command helper
  const publishCommand = useCallback(
    (payload: GimbalCommandPayload, feedbackLabel?: string) => {
      if (!mqttClientRef.current || !mqttClientRef.current.connected) {
        console.warn('[PTZ MQTT] Cannot send command: MQTT broker not connected.');
        return;
      }

      const jsonStr = JSON.stringify(payload);
      mqttClientRef.current.publish(commandTopic, jsonStr, { qos: 1 }, (err) => {
        if (err) {
          console.error(`[PTZ MQTT] Failed to publish command to ${commandTopic}:`, err);
        } else {
          console.log(`[PTZ MQTT] Published to ${commandTopic}:`, payload);
        }
      });

      if (feedbackLabel) {
        setResultToast({ status: 'sent', message: feedbackLabel });
        if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
        toastTimeoutRef.current = setTimeout(() => setResultToast(null), 2500);
      }
    },
    [commandTopic]
  );

  // 1. Relative rotation (docs/camera_mqtt_control.md Section 2.2: action: 'rotate')
  // NOTE: Tilt positive is DOWN, negative is UP per specification!
  const sendRotate = useCallback(
    (deltaPan: number, deltaTilt: number, dirLabel: string) => {
      setActiveDirection(dirLabel);
      const cmdId = ++cmdIdRef.current;
      const payload: GimbalCommandPayload = {
        cmd_id: cmdId,
        action: 'rotate',
        pan_deg: deltaPan,
        tilt_deg: deltaTilt,
      };
      publishCommand(
        payload,
        `微調轉動 (${deltaPan >= 0 ? `+${deltaPan}` : deltaPan}°, ${deltaTilt >= 0 ? `+${deltaTilt}` : deltaTilt}°)`
      );
      setTimeout(() => setActiveDirection(null), 200);
    },
    [publishCommand]
  );

  // 2. Absolute move (docs/camera_mqtt_control.md Section 2.2: action: 'move')
  const sendMove = useCallback(
    (targetPan: number, targetTilt: number, label?: string) => {
      const clampedPan = Math.max(-180, Math.min(180, parseFloat(targetPan.toFixed(1))));
      const clampedTilt = Math.max(-90, Math.min(90, parseFloat(targetTilt.toFixed(1))));

      const cmdId = ++cmdIdRef.current;
      const payload: GimbalCommandPayload = {
        cmd_id: cmdId,
        action: 'move',
        pan_deg: clampedPan,
        tilt_deg: clampedTilt,
      };
      publishCommand(payload, label || `導向指定角度 (${clampedPan}°, ${clampedTilt}°)`);
    },
    [publishCommand]
  );

  // 3. Cancel ongoing motion (docs/camera_mqtt_control.md Section 2.2: action: 'cancel')
  const sendCancel = useCallback(() => {
    setActiveDirection('cancel');
    const cmdId = ++cmdIdRef.current;
    const payload: GimbalCommandPayload = {
      cmd_id: cmdId,
      action: 'cancel',
    };
    publishCommand(payload, '已發送急停 / 取消動作');
    setTimeout(() => setActiveDirection(null), 200);
  }, [publishCommand]);

  // Home reset
  const handleHome = useCallback(() => {
    setActiveDirection('home');
    sendMove(0.0, 0.0, '重設歸位 (正前方 0°, 0°)');
    setTimeout(() => setActiveDirection(null), 200);
  }, [sendMove]);

  // Directional jog handlers
  // In camera_mqtt_control.md / ROS REP 103 (+Z up):
  // Tilt UP = negative tilt_deg (-step)
  // Tilt DOWN = positive tilt_deg (+step)
  // Pan LEFT (CCW) = positive pan_deg (+step)
  // Pan RIGHT (CW) = negative pan_deg (-step)
  const handleMoveUp = () => sendRotate(0, -step, 'up');
  const handleMoveDown = () => sendRotate(0, step, 'down');
  const handleMoveLeft = () => sendRotate(step, 0, 'left');
  const handleMoveRight = () => sendRotate(-step, 0, 'right');

  const handleMoveUpLeft = () => sendRotate(step, -step, 'up-left');
  const handleMoveUpRight = () => sendRotate(-step, -step, 'up-right');
  const handleMoveDownLeft = () => sendRotate(step, step, 'down-left');
  const handleMoveDownRight = () => sendRotate(-step, step, 'down-right');

  // Keyboard navigation support
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      handleMoveUp();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      handleMoveDown();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      handleMoveLeft();
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      handleMoveRight();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      sendCancel();
    } else if (e.key.toLowerCase() === 'h' || e.key === '0') {
      e.preventDefault();
      handleHome();
    }
  };

  const handleApplyCustomMove = (e: React.FormEvent) => {
    e.preventDefault();
    const p = parseFloat(inputPan);
    const t = parseFloat(inputTilt);
    if (!isNaN(p) && !isNaN(t)) {
      sendMove(p, t);
    }
  };

  return (
    <div
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className={`bg-[#080d19]/95 border-t border-slate-800/90 text-slate-200 select-none outline-none focus:ring-1 focus:ring-cyan-500/30 transition-all ${className}`}
    >
      {/* Sub-Header Bar (Collapsible Header with Live Posture & Status) */}
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center justify-between px-3.5 py-2 bg-[#0a1224] border-b border-slate-800/70 cursor-pointer hover:bg-[#0c162d] transition-colors"
        title={isExpanded ? '收合雲台控制面板' : '展開雲台控制面板'}
      >
        <div className="flex items-center space-x-2 min-w-0 flex-1 mr-2">
          <div
            className={`w-5 h-5 rounded-md flex items-center justify-center shrink-0 ${
              isBusy
                ? 'bg-amber-500/20 text-amber-400 animate-pulse'
                : 'bg-cyan-500/20 text-cyan-400'
            }`}
          >
            <Gamepad2 className="w-3 h-3" />
          </div>
          <div className="flex items-center space-x-2 min-w-0 flex-wrap">
            <span className="text-xs font-bold text-slate-200 truncate">
              {gimbalName.includes('ceiling') ? '天花板相機雲台 (Ceiling PTZ)' : '車載雲台操控 (AMR Gimbal)'}
            </span>

            {/* Real-time Posture Display */}
            <Badge
              variant="outline"
              className="text-[9px] px-1.5 py-0 border-cyan-700/50 text-cyan-300 font-mono shrink-0 hidden sm:inline-flex"
            >
              Pan: {pan > 0 ? `+${pan}` : pan}° • Tilt: {tilt > 0 ? `+${tilt}` : tilt}°
            </Badge>

            {/* Busy / Running Badge */}
            {isBusy ? (
              <Badge className="text-[9px] px-1.5 py-0 bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse shrink-0">
                <Activity className="w-2.5 h-2.5 mr-0.5 animate-spin" />
                運轉中 (Busy)
              </Badge>
            ) : (
              <Badge className="text-[9px] px-1.5 py-0 bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 shrink-0">
                就緒 (Idle)
              </Badge>
            )}

            {/* Offline warning if detected */}
            {!isGimbalOnline && (
              <Badge className="text-[9px] px-1.5 py-0 bg-rose-500/20 text-rose-300 border border-rose-500/40 shrink-0">
                離線 (Offline)
              </Badge>
            )}
          </div>
        </div>

        <div className="flex items-center space-x-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
          {/* Emergency Stop / Cancel Button (Visible when busy or available) */}
          {isBusy && (
            <button
              onClick={sendCancel}
              className="text-[10px] px-2 py-0.5 rounded border border-rose-500/60 bg-rose-950/60 text-rose-300 hover:bg-rose-900/80 hover:text-white flex items-center space-x-1 transition-colors animate-pulse"
              title="停止進行中的雲台旋轉動作 (action: cancel)"
            >
              <Square className="w-2.5 h-2.5 fill-current" />
              <span>停止動作</span>
            </button>
          )}

          {/* Quick Home Reset button */}
          <button
            onClick={handleHome}
            className="text-[10px] px-1.5 py-0.5 rounded border border-slate-700/80 bg-slate-900/80 text-slate-300 hover:text-cyan-300 hover:border-cyan-600/60 flex items-center space-x-1 transition-colors"
            title="視角重設為正前方原點 (0°, 0°)"
          >
            <RotateCcw className="w-2.5 h-2.5" />
            <span>歸位</span>
          </button>

          {/* Toggle Panel Chevron */}
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="text-slate-400 hover:text-white p-0.5 rounded hover:bg-slate-800 transition-colors"
            title={isExpanded ? '收合雲台操作' : '展開雲台操作'}
          >
            {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Expanded Controls Panel */}
      {isExpanded && (
        <div className="p-3 space-y-3">
          {/* Status Message / Result Toast Bar */}
          {resultToast && (
            <div
              className={`text-[11px] px-2.5 py-1 rounded flex items-center justify-between border ${
                resultToast.status === 'succeeded'
                  ? 'bg-emerald-950/50 border-emerald-500/40 text-emerald-300'
                  : resultToast.status === 'rejected'
                  ? 'bg-rose-950/50 border-rose-500/40 text-rose-300'
                  : resultToast.status === 'aborted'
                  ? 'bg-amber-950/50 border-amber-500/40 text-amber-300'
                  : resultToast.status === 'canceled'
                  ? 'bg-slate-900 border-slate-700 text-slate-300'
                  : 'bg-cyan-950/50 border-cyan-500/40 text-cyan-300'
              }`}
            >
              <div className="flex items-center space-x-1.5 min-w-0">
                {resultToast.status === 'succeeded' ? (
                  <Check className="w-3.5 h-3.5 shrink-0 text-emerald-400" />
                ) : resultToast.status === 'rejected' ? (
                  <XCircle className="w-3.5 h-3.5 shrink-0 text-rose-400" />
                ) : resultToast.status === 'aborted' ? (
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0 text-amber-400" />
                ) : (
                  <Activity className="w-3.5 h-3.5 shrink-0 text-cyan-400" />
                )}
                <span className="truncate">{resultToast.message}</span>
              </div>
              <span className="text-[9px] uppercase font-mono opacity-70 ml-2 shrink-0">
                {resultToast.status}
              </span>
            </div>
          )}

          {/* D-Pad Matrix (Left) + Step Selector & Direct Inputs (Right) */}
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-[#0a1122]/90 border border-slate-800/80 rounded-xl p-2.5">
            {/* Left: 8-Direction Virtual D-Pad */}
            <div className="flex flex-col items-center shrink-0">
              <span className="text-[10px] text-slate-400 font-medium mb-1.5 flex items-center">
                <Compass className="w-3 h-3 mr-1 text-cyan-400" />
                方向操控盤 (Pan & Tilt)
              </span>

              {/* 3x3 D-Pad Grid */}
              <div className="grid grid-cols-3 gap-1 w-32 h-32 p-1 bg-slate-950/80 border border-slate-800 rounded-xl shadow-inner relative">
                {/* 1. Up-Left */}
                <button
                  onClick={handleMoveUpLeft}
                  className={`flex items-center justify-center rounded-lg border text-slate-400 hover:text-white transition-all ${
                    activeDirection === 'up-left'
                      ? 'bg-cyan-600/40 border-cyan-400 scale-95'
                      : 'border-slate-800/80 bg-slate-900/60 hover:bg-cyan-950/40 hover:border-cyan-600/50'
                  }`}
                  title={`左上微調 (左旋 ${step}°, 仰角 ${step}°)`}
                >
                  <ArrowUpLeft className="w-3.5 h-3.5" />
                </button>

                {/* 2. Tilt Up (仰角: -tilt) */}
                <button
                  onClick={handleMoveUp}
                  className={`flex flex-col items-center justify-center rounded-lg border transition-all ${
                    activeDirection === 'up'
                      ? 'bg-cyan-600/40 border-cyan-400 text-white scale-95 shadow-sm shadow-cyan-500/20'
                      : 'border-slate-800/80 bg-slate-900/80 text-cyan-300 hover:bg-cyan-950/60 hover:border-cyan-500/70'
                  }`}
                  title={`向上抬頭 / 仰角 (-${step}°)`}
                >
                  <ChevronUp className="w-4 h-4" />
                </button>

                {/* 3. Up-Right */}
                <button
                  onClick={handleMoveUpRight}
                  className={`flex items-center justify-center rounded-lg border text-slate-400 hover:text-white transition-all ${
                    activeDirection === 'up-right'
                      ? 'bg-cyan-600/40 border-cyan-400 scale-95'
                      : 'border-slate-800/80 bg-slate-900/60 hover:bg-cyan-950/40 hover:border-cyan-600/50'
                  }`}
                  title={`右上微調 (右旋 ${step}°, 仰角 ${step}°)`}
                >
                  <ArrowUpRight className="w-3.5 h-3.5" />
                </button>

                {/* 4. Pan Left (左旋: +pan) */}
                <button
                  onClick={handleMoveLeft}
                  className={`flex items-center justify-center rounded-lg border transition-all ${
                    activeDirection === 'left'
                      ? 'bg-cyan-600/40 border-cyan-400 text-white scale-95 shadow-sm shadow-cyan-500/20'
                      : 'border-slate-800/80 bg-slate-900/80 text-cyan-300 hover:bg-cyan-950/60 hover:border-cyan-500/70'
                  }`}
                  title={`向左旋轉 (+${step}°)`}
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>

                {/* 5. Center Home Button */}
                <button
                  onClick={handleHome}
                  className={`flex items-center justify-center rounded-lg border transition-all ${
                    activeDirection === 'home'
                      ? 'bg-cyan-500 text-slate-950 border-cyan-300 scale-95'
                      : 'border-cyan-500/40 bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30 hover:border-cyan-400 shadow-sm shadow-cyan-500/10'
                  }`}
                  title="視角居中歸零 (Home 0°, 0°)"
                >
                  <Target className="w-4 h-4" />
                </button>

                {/* 6. Pan Right (右旋: -pan) */}
                <button
                  onClick={handleMoveRight}
                  className={`flex items-center justify-center rounded-lg border transition-all ${
                    activeDirection === 'right'
                      ? 'bg-cyan-600/40 border-cyan-400 text-white scale-95 shadow-sm shadow-cyan-500/20'
                      : 'border-slate-800/80 bg-slate-900/80 text-cyan-300 hover:bg-cyan-950/60 hover:border-cyan-500/70'
                  }`}
                  title={`向右旋轉 (-${step}°)`}
                >
                  <ChevronRight className="w-4 h-4" />
                </button>

                {/* 7. Down-Left */}
                <button
                  onClick={handleMoveDownLeft}
                  className={`flex items-center justify-center rounded-lg border text-slate-400 hover:text-white transition-all ${
                    activeDirection === 'down-left'
                      ? 'bg-cyan-600/40 border-cyan-400 scale-95'
                      : 'border-slate-800/80 bg-slate-900/60 hover:bg-cyan-950/40 hover:border-cyan-600/50'
                  }`}
                  title={`左下微調 (左旋 ${step}°, 俯角 ${step}°)`}
                >
                  <ArrowDownLeft className="w-3.5 h-3.5" />
                </button>

                {/* 8. Tilt Down (俯角: +tilt) */}
                <button
                  onClick={handleMoveDown}
                  className={`flex flex-col items-center justify-center rounded-lg border transition-all ${
                    activeDirection === 'down'
                      ? 'bg-cyan-600/40 border-cyan-400 text-white scale-95 shadow-sm shadow-cyan-500/20'
                      : 'border-slate-800/80 bg-slate-900/80 text-cyan-300 hover:bg-cyan-950/60 hover:border-cyan-500/70'
                  }`}
                  title={`向下低頭 / 俯角 (+${step}°)`}
                >
                  <ChevronDown className="w-4 h-4" />
                </button>

                {/* 9. Down-Right */}
                <button
                  onClick={handleMoveDownRight}
                  className={`flex items-center justify-center rounded-lg border text-slate-400 hover:text-white transition-all ${
                    activeDirection === 'down-right'
                      ? 'bg-cyan-600/40 border-cyan-400 scale-95'
                      : 'border-slate-800/80 bg-slate-900/60 hover:bg-cyan-950/40 hover:border-cyan-600/50'
                  }`}
                  title={`右下微調 (右旋 ${step}°, 俯角 ${step}°)`}
                >
                  <ArrowDownRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* Right: Step Size & Direct Angle Navigation */}
            <div className="flex-1 min-w-[170px] space-y-2.5 w-full">
              {/* 1. Step Size (步進檔位) */}
              <div className="bg-slate-950/70 border border-slate-800 rounded-lg p-2 space-y-1">
                <div className="flex items-center justify-between text-[10px] text-slate-400">
                  <span className="flex items-center space-x-1">
                    <Sliders className="w-3 h-3 text-cyan-400" />
                    <span>旋轉步進檔位 (Step)</span>
                  </span>
                  <span className="text-[9px] text-slate-500 font-mono">每次點擊角度</span>
                </div>

                <div className="grid grid-cols-4 gap-1">
                  {[1.0, 5.0, 15.0, 30.0].map((deg) => (
                    <button
                      key={deg}
                      onClick={() => setStep(deg)}
                      className={`text-[10px] py-1 rounded font-mono transition-all border ${
                        step === deg
                          ? 'bg-cyan-500/20 border-cyan-500/80 text-cyan-300 font-bold shadow-sm'
                          : 'bg-slate-900 border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700'
                      }`}
                    >
                      {deg}°
                    </button>
                  ))}
                </div>
              </div>

              {/* 2. Direct Move To Coordinates (指定絕對角度) */}
              <form
                onSubmit={handleApplyCustomMove}
                className="bg-slate-950/70 border border-slate-800 rounded-lg p-2 space-y-1.5"
              >
                <div className="flex items-center justify-between text-[10px] text-slate-400">
                  <span className="flex items-center space-x-1">
                    <Target className="w-3 h-3 text-cyan-400" />
                    <span>指定絕對角度 (Move To)</span>
                  </span>
                  <span className="text-[9px] text-slate-500 font-mono">Pan ±180° / Tilt ±90°</span>
                </div>

                <div className="flex items-center space-x-1.5">
                  <div className="flex-1 flex items-center space-x-1 bg-slate-900 border border-slate-800 rounded px-1.5 py-0.5">
                    <span className="text-[10px] text-slate-400 font-mono">P:</span>
                    <input
                      type="number"
                      step="1"
                      min="-180"
                      max="180"
                      value={inputPan}
                      onChange={(e) => setInputPan(e.target.value)}
                      className="w-full bg-transparent text-[11px] font-mono text-cyan-300 outline-none"
                      placeholder="0"
                    />
                    <span className="text-[10px] text-slate-500">°</span>
                  </div>

                  <div className="flex-1 flex items-center space-x-1 bg-slate-900 border border-slate-800 rounded px-1.5 py-0.5">
                    <span className="text-[10px] text-slate-400 font-mono">T:</span>
                    <input
                      type="number"
                      step="1"
                      min="-90"
                      max="90"
                      value={inputTilt}
                      onChange={(e) => setInputTilt(e.target.value)}
                      className="w-full bg-transparent text-[11px] font-mono text-cyan-300 outline-none"
                      placeholder="0"
                    />
                    <span className="text-[10px] text-slate-500">°</span>
                  </div>

                  <Button
                    type="submit"
                    size="sm"
                    className="h-6 px-2 py-0 text-[10px] bg-cyan-600 hover:bg-cyan-500 text-white shrink-0"
                  >
                    <Send className="w-2.5 h-2.5 mr-1" />
                    前往
                  </Button>
                </div>
              </form>
            </div>
          </div>

          {/* Bottom Row: Quick Presets (視角預設位快捷標籤) */}
          <div className="space-y-1.5 pt-0.5">
            <div className="flex items-center justify-between text-[10px] text-slate-400">
              <span className="font-semibold text-slate-300 flex items-center space-x-1">
                <span>預設視角捷徑 (Preset Angles)</span>
              </span>
              <div className="flex items-center space-x-2 text-[10px] text-slate-500">
                <span className="flex items-center space-x-1">
                  {mqttConnected ? (
                    <Wifi className="w-2.5 h-2.5 text-emerald-400" />
                  ) : (
                    <WifiOff className="w-2.5 h-2.5 text-rose-400" />
                  )}
                  <span>MQTT {mqttConnected ? '連線' : '斷線'}</span>
                </span>
                <span>•</span>
                <span className="font-mono text-slate-400">{commandTopic}</span>
              </div>
            </div>

            <div className="grid grid-cols-5 gap-1 text-[10px]">
              <button
                onClick={() => sendMove(0.0, 0.0, '正前方原點 (0°, 0°)')}
                className="py-1 px-1 rounded bg-slate-900/90 hover:bg-cyan-950/40 border border-slate-800 hover:border-cyan-600/50 text-slate-300 hover:text-white transition-colors truncate"
                title="正前方原點 (Pan 0°, Tilt 0°)"
              >
                正前 [0°]
              </button>
              <button
                onClick={() => sendMove(90.0, 0.0, '向左 90 度視角 (+90°, 0°)')}
                className="py-1 px-1 rounded bg-slate-900/90 hover:bg-cyan-950/40 border border-slate-800 hover:border-cyan-600/50 text-slate-300 hover:text-white transition-colors truncate"
                title="左側視角 (Pan +90°, Tilt 0°)"
              >
                左側 [+90°]
              </button>
              <button
                onClick={() => sendMove(-90.0, 0.0, '向右 90 度視角 (-90°, 0°)')}
                className="py-1 px-1 rounded bg-slate-900/90 hover:bg-cyan-950/40 border border-slate-800 hover:border-cyan-600/50 text-slate-300 hover:text-white transition-colors truncate"
                title="右側視角 (Pan -90°, Tilt 0°)"
              >
                右側 [-90°]
              </button>
              <button
                onClick={() => sendMove(0.0, 30.0, '俯視地面視角 (0°, +30°)')}
                className="py-1 px-1 rounded bg-slate-900/90 hover:bg-cyan-950/40 border border-slate-800 hover:border-cyan-600/50 text-slate-300 hover:text-white transition-colors truncate"
                title="俯角低頭 (+30° 向下)"
              >
                俯角 [+30°]
              </button>
              <button
                onClick={() => sendMove(0.0, -30.0, '仰視天花板視角 (0°, -30°)')}
                className="py-1 px-1 rounded bg-slate-900/90 hover:bg-cyan-950/40 border border-slate-800 hover:border-cyan-600/50 text-slate-300 hover:text-white transition-colors truncate"
                title="仰角抬頭 (-30° 向上)"
              >
                仰角 [-30°]
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
