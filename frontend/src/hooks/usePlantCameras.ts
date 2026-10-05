import { useState, useEffect, useRef, useCallback } from 'react';
import mqtt, { MqttClient } from 'mqtt';
import { PlantSite, CameraInfo, PlantCameraPayload } from '@/types/rmf';

export type MqttConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export function usePlantCameras(plant: PlantSite | null) {
  const [connectionStatus, setConnectionStatus] = useState<MqttConnectionStatus>('disconnected');
  const [cameraData, setCameraData] = useState<PlantCameraPayload | null>(null);
  const [cameras, setCameras] = useState<CameraInfo[]>([]);
  const [activeTopic, setActiveTopic] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const clientRef = useRef<MqttClient | null>(null);
  const currentSubscribedTopicRef = useRef<string | null>(null);

  // Helper to resolve the WebSocket URL for EMQX
  const getBrokerUrl = useCallback((site: PlantSite | null) => {
    if (site?.mqttBrokerUrl) {
      return site.mqttBrokerUrl;
    }
    const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${host}:8083/mqtt`;
  }, []);

  useEffect(() => {
    // 1. If no plant or no cameraTopic, clean up current subscription
    if (!plant || !plant.cameraTopic) {
      if (clientRef.current && currentSubscribedTopicRef.current) {
        clientRef.current.unsubscribe(currentSubscribedTopicRef.current);
        currentSubscribedTopicRef.current = null;
      }
      setCameraData(null);
      setCameras([]);
      setActiveTopic(null);
      return;
    }

    const topicToSubscribe = plant.cameraTopic;
    const brokerUrl = getBrokerUrl(plant);

    // If client is already connected or connecting
    let client = clientRef.current;

    const setupSubscriptions = (c: MqttClient) => {
      // Unsubscribe from previous topic if different
      if (currentSubscribedTopicRef.current && currentSubscribedTopicRef.current !== topicToSubscribe) {
        c.unsubscribe(currentSubscribedTopicRef.current);
      }

      console.log(`[MQTT] Subscribing to topic: ${topicToSubscribe} for plant: ${plant.name}`);
      c.subscribe(topicToSubscribe, { qos: 0 }, (err) => {
        if (err) {
          console.error(`[MQTT] Subscription error for ${topicToSubscribe}:`, err);
        } else {
          currentSubscribedTopicRef.current = topicToSubscribe;
          setActiveTopic(topicToSubscribe);
          console.log(`[MQTT] Successfully subscribed to: ${topicToSubscribe}`);
        }
      });
    };

    if (!client || !client.connected) {
      setConnectionStatus('connecting');
      const clientId = `web_rmf_${Math.random().toString(16).substring(2, 10)}`;

      client = mqtt.connect(brokerUrl, {
        clientId,
        clean: true,
        connectTimeout: 4000,
        reconnectPeriod: 3000,
      });

      clientRef.current = client;

      client.on('connect', () => {
        console.log(`[MQTT] Connected to EMQX at ${brokerUrl}`);
        setConnectionStatus('connected');
        if (client) {
          setupSubscriptions(client);
        }
      });

      client.on('message', (topic, message) => {
        if (topic === currentSubscribedTopicRef.current) {
          try {
            const raw = message.toString();
            const payload: PlantCameraPayload = JSON.parse(raw);
            setCameraData(payload);
            setCameras(payload.cameras || []);
            setLastUpdated(new Date());
          } catch (e) {
            console.error(`[MQTT] Failed to parse payload from ${topic}:`, e);
          }
        }
      });

      client.on('error', (err) => {
        console.error('[MQTT] Connection error:', err);
        setConnectionStatus('error');
      });

      client.on('close', () => {
        setConnectionStatus('disconnected');
      });
    } else {
      // Re-use existing connected client to switch topic on-demand
      setupSubscriptions(client);
    }

    return () => {
      // When plant changes or unmounts, unsubscribe immediately
      if (clientRef.current && currentSubscribedTopicRef.current) {
        console.log(`[MQTT] Unsubscribing from topic: ${currentSubscribedTopicRef.current}`);
        clientRef.current.unsubscribe(currentSubscribedTopicRef.current);
        currentSubscribedTopicRef.current = null;
      }
    };
  }, [plant?.id, plant?.cameraTopic, getBrokerUrl]);

  // Clean up client on complete unmount
  useEffect(() => {
    return () => {
      if (clientRef.current) {
        clientRef.current.end(true);
        clientRef.current = null;
      }
    };
  }, []);

  // Set camera enabled state explicitly (true = start camera & stream, false = stop camera)
  const setCameraEnabled = useCallback((cam: CameraInfo, enabled: boolean) => {
    if (!clientRef.current || !clientRef.current.connected) {
      console.warn('[MQTT] Client not connected. Cannot set camera state.');
      return;
    }

    // Resolve target topic: Isaac Sim expects slam/cameras/<camera_name>/enable
    let topic = cam.enable_topic;
    if (!topic || topic.startsWith('/')) {
      const cleanName = cam.name.startsWith('/') ? cam.name.substring(1) : cam.name;
      topic = activeTopic ? `${activeTopic}/${cleanName}/enable` : `slam/cameras/${cleanName}/enable`;
    }

    // Isaac Sim mqtt_cameras.py strictly checks: payload in ("true", "1", "on") / ("false", "0", "off")
    const payload = enabled ? 'true' : 'false';

    console.log(`[MQTT] Setting camera ${cam.name} enabled=${enabled} on ${topic}`);
    clientRef.current.publish(topic, payload, { qos: 1 }, (err) => {
      if (err) {
        console.error(`[MQTT] Failed to publish state to ${topic}:`, err);
      } else {
        console.log(`[MQTT] Camera state successfully published to ${topic}: ${payload}`);
      }
    });

    // Optimistically update camera state in UI
    setCameras((prev) =>
      prev.map((c) => (c.name === cam.name ? { ...c, enabled } : c))
    );
  }, [activeTopic]);

  // Toggle or open a specific camera
  const toggleCamera = useCallback((cam: CameraInfo) => {
    setCameraEnabled(cam, !cam.enabled);
  }, [setCameraEnabled]);

  return {
    connectionStatus,
    cameraData,
    cameras,
    activeTopic,
    lastUpdated,
    setCameraEnabled,
    toggleCamera,
  };
}
