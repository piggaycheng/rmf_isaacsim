import React, { useEffect, useRef, useState } from 'react';
import { Map as MapLibreMap, Marker, NavigationControl, StyleSpecification, setWorkerUrl } from 'maplibre-gl';
import maplibreglWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import 'maplibre-gl/dist/maplibre-gl.css';

// Ensure WebWorker is loaded cleanly in Vite / Webpack
if (typeof window !== 'undefined' && setWorkerUrl) {
  setWorkerUrl(maplibreglWorkerUrl);
}
import { PlantSite } from '@/types/rmf';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Card, CardContent } from '@/components/ui/card';
import {
  Globe,
  MapPin,
  Search,
  Plus,
  Compass,
  Video,
  Radio,
  GitCommit,
  ExternalLink,
  Layers,
  ZoomIn,
  ZoomOut,
  Navigation,
  RotateCcw,
  Building2,
  Cpu,
  Boxes,
  Check,
  X,
  SlidersHorizontal,
} from 'lucide-react';

interface WorldMapProps {
  plants: PlantSite[];
  selectedPlant: PlantSite | null;
  onSelectPlant: (plant: PlantSite) => void;
  onOpenIsaacSim: (plant: PlantSite) => void;
  onNavigateToMonitor: (plant: PlantSite) => void;
  onNavigateToEdit: (plant: PlantSite) => void;
  onAddPlant: (newPlant: PlantSite) => void;
}

// Reliable Tile Styles (Public, high quality, zero API key required)
const TILE_STYLES: Record<string, { name: string; style: StyleSpecification | string }> = {
  maplibre: {
    name: 'MapLibre 官方風格 (maplibre.org)',
    style: 'https://demotiles.maplibre.org/style.json',
  },
  dark: {
    name: '科技深色 (Dark)',
    style: {
      version: 8,
      sources: {
        'esri-dark-base': {
          type: 'raster',
          tiles: [
            'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
          ],
          tileSize: 256,
          attribution: '&copy; Esri &copy; OpenStreetMap contributors',
          maxzoom: 16,
        },
        'esri-dark-ref': {
          type: 'raster',
          tiles: [
            'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
          ],
          tileSize: 256,
          maxzoom: 16,
        },
      },
      layers: [
        {
          id: 'esri-dark-base-layer',
          type: 'raster',
          source: 'esri-dark-base',
          minzoom: 0,
          maxzoom: 18,
        },
        {
          id: 'esri-dark-ref-layer',
          type: 'raster',
          source: 'esri-dark-ref',
          minzoom: 0,
          maxzoom: 18,
        },
      ],
    },
  },
  satellite: {
    name: '衛星航照 (Satellite)',
    style: {
      version: 8,
      sources: {
        'esri-satellite': {
          type: 'raster',
          tiles: [
            'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
          ],
          tileSize: 256,
          attribution: '&copy; Esri, Maxar, Earthstar Geographics',
          maxzoom: 19,
        },
        'esri-boundaries': {
          type: 'raster',
          tiles: [
            'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
          ],
          tileSize: 256,
          maxzoom: 19,
        },
      },
      layers: [
        {
          id: 'esri-satellite-layer',
          type: 'raster',
          source: 'esri-satellite',
          minzoom: 0,
          maxzoom: 20,
        },
        {
          id: 'esri-boundaries-layer',
          type: 'raster',
          source: 'esri-boundaries',
          minzoom: 0,
          maxzoom: 20,
        },
      ],
    },
  },
  streets: {
    name: '標準街道 (Streets)',
    style: {
      version: 8,
      sources: {
        'esri-streets': {
          type: 'raster',
          tiles: [
            'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
          ],
          tileSize: 256,
          attribution: '&copy; Esri &copy; OpenStreetMap contributors',
          maxzoom: 19,
        },
      },
      layers: [
        {
          id: 'esri-streets-layer',
          type: 'raster',
          source: 'esri-streets',
          minzoom: 0,
          maxzoom: 20,
        },
      ],
    },
  },
};

export function WorldMap({
  plants,
  selectedPlant,
  onSelectPlant,
  onOpenIsaacSim,
  onNavigateToMonitor,
  onNavigateToEdit,
  onAddPlant,
}: WorldMapProps) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef<Marker[]>([]);

  const [activeStyleKey, setActiveStyleKey] = useState<string>('dark');
  const [searchQuery, setSearchQuery] = useState('');
  const [showAddModal, setShowAddModal] = useState(false);
  const [showStyleMenu, setShowStyleMenu] = useState(false);

  // New Plant Form State
  const [newPlantName, setNewPlantName] = useState('');
  const [newPlantCode, setNewPlantCode] = useState('');
  const [newLocation, setNewLocation] = useState('');
  const [newCountry, setNewCountry] = useState('台灣');
  const [newLng, setNewLng] = useState('121.5654');
  const [newLat, setNewLat] = useState('25.0330');
  const [newRobots, setNewRobots] = useState('5');
  const [newWebRTCUrl, setNewWebRTCUrl] = useState('ws://localhost:8080/webrtc');
  const [newDesc, setNewDesc] = useState('');

  // 1. Initialize MapLibre Map
  useEffect(() => {
    if (!mapContainerRef.current) return;

    const initialCenter: [number, number] = selectedPlant ? selectedPlant.coordinates : [121.0, 24.5];
    const initialZoom = selectedPlant ? 12 : 2.5;

    const map = new MapLibreMap({
      container: mapContainerRef.current,
      style: TILE_STYLES[activeStyleKey].style,
      center: initialCenter,
      zoom: initialZoom,
      minZoom: 1.5,
      maxZoom: 18,
      attributionControl: false,
    });

    // Add navigation controls (zoom & compass)
    map.addControl(new NavigationControl({ showCompass: true, showZoom: true }), 'top-right');

    mapRef.current = map;

    // Window resize observer to update map dimensions
    const resizeObserver = new ResizeObserver(() => {
      map.resize();
    });
    resizeObserver.observe(mapContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // 2. Change Map Style
  const handleStyleChange = (key: string) => {
    setActiveStyleKey(key);
    setShowStyleMenu(false);
    if (mapRef.current) {
      mapRef.current.setStyle(TILE_STYLES[key].style);
    }
  };

  // 3. Render Plant Markers on the Map
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // Clear existing markers
    markersRef.current.forEach((m: Marker) => m.remove());
    markersRef.current = [];

    plants.forEach((plant) => {
      const isSelected = selectedPlant?.id === plant.id;

      // Create Custom HTML Marker element
      const el = document.createElement('div');
      el.className = 'group cursor-pointer select-none';
      el.style.transform = 'translate(-50%, -100%)';

      el.innerHTML = `
        <div class="relative flex flex-col items-center">
          <!-- Tooltip label on hover or when selected -->
          <div class="mb-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold whitespace-nowrap border shadow-xl transition-all duration-200 pointer-events-auto flex items-center space-x-1.5 ${
            isSelected
              ? 'bg-slate-900 border-primary text-primary-foreground shadow-primary/20 scale-110 z-30'
              : 'bg-slate-900/90 border-slate-700 text-slate-200 backdrop-blur-md opacity-90 group-hover:opacity-100 group-hover:scale-105 z-10'
          }">
            <span class="w-2 h-2 rounded-full ${plant.status === 'online' ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}"></span>
            <span>${plant.name}</span>
            <span class="text-[9px] px-1 py-0.2 rounded bg-slate-800 text-slate-400 font-mono">${plant.code}</span>
          </div>

          <!-- Pin Icon with Radar Ripple -->
          <div class="relative flex items-center justify-center">
            ${
              isSelected
                ? '<div class="absolute w-12 h-12 rounded-full bg-cyan-500/30 animate-ping"></div>'
                : '<div class="absolute w-8 h-8 rounded-full bg-primary/20 group-hover:animate-ping"></div>'
            }
            <div class="w-8 h-8 rounded-full flex items-center justify-center shadow-lg border transition-transform duration-200 ${
              isSelected
                ? 'bg-gradient-to-tr from-cyan-500 to-blue-600 border-cyan-300 text-white scale-125 shadow-cyan-500/50'
                : 'bg-gradient-to-tr from-slate-900 to-slate-800 border-slate-600 text-cyan-400 group-hover:border-primary group-hover:scale-110'
            }">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/>
                <circle cx="12" cy="10" r="3"/>
              </svg>
            </div>
            <div class="w-1.5 h-1.5 rounded-full bg-slate-900 mt-0.5"></div>
          </div>
        </div>
      `;

      // Marker click handler
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        onSelectPlant(plant);
        map.flyTo({
          center: plant.coordinates,
          zoom: Math.max(map.getZoom(), 13.5),
          speed: 1.4,
          curve: 1.42,
          essential: true,
        });
      });

      const marker = new Marker({ element: el })
        .setLngLat(plant.coordinates)
        .addTo(map);

      markersRef.current.push(marker);
    });
  }, [plants, selectedPlant]);

  // Fly to selected plant when selection changes
  const handleFlyToPlant = (plant: PlantSite) => {
    onSelectPlant(plant);
    if (mapRef.current) {
      mapRef.current.flyTo({
        center: plant.coordinates,
        zoom: 14,
        speed: 1.2,
        curve: 1.3,
        essential: true,
      });
    }
  };

  // Reset to World View
  const handleResetWorldView = () => {
    if (mapRef.current) {
      mapRef.current.flyTo({
        center: [100, 25],
        zoom: 2.2,
        speed: 1.0,
        essential: true,
      });
    }
  };

  // Filtered plant list
  const filteredPlants = plants.filter(
    (p) =>
      p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.code.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.locationName.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.country.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Submit Add New Plant
  const handleCreatePlant = (e: React.FormEvent) => {
    e.preventDefault();
    const lng = parseFloat(newLng);
    const lat = parseFloat(newLat);
    if (isNaN(lng) || isNaN(lat)) {
      alert('請輸入有效的經緯度數值！');
      return;
    }

    const createdPlant: PlantSite = {
      id: `site_${Date.now()}`,
      name: newPlantName.trim() || '自訂智慧廠區',
      code: newPlantCode.trim().toUpperCase() || `SITE-${plants.length + 1}`,
      locationName: newLocation.trim() || '自訂園區',
      country: newCountry.trim() || '台灣',
      coordinates: [lng, lat],
      status: 'online',
      robotCount: parseInt(newRobots) || 4,
      activeTasks: 0,
      areaM2: 15000,
      description: newDesc.trim() || '自訂 Open-RMF 車隊調度與 Isaac Sim 模擬廠區。',
      isaacSimWebRTCUrl: newWebRTCUrl.trim() || 'ws://localhost:8080/webrtc',
    };

    onAddPlant(createdPlant);
    setShowAddModal(false);

    // Reset Form
    setNewPlantName('');
    setNewPlantCode('');
    setNewLocation('');
    setNewDesc('');

    // Select and Fly
    setTimeout(() => {
      handleFlyToPlant(createdPlant);
    }, 200);
  };

  return (
    <div className="flex-1 flex w-full h-full relative overflow-hidden bg-[#060911]">
      {/* 1. Left Sidebar: Factory Plants Directory */}
      <aside className="w-80 md:w-96 border-r border-slate-800 bg-[#0c1220]/95 backdrop-blur-md flex flex-col z-10 shadow-xl select-none">
        {/* Sidebar Header */}
        <div className="p-4 border-b border-slate-800 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <div className="w-7 h-7 rounded-lg bg-primary/20 border border-primary/30 flex items-center justify-center text-primary">
                <Globe className="w-4 h-4" />
              </div>
              <h2 className="font-bold text-sm text-slate-100">全球廠區總覽</h2>
            </div>
            <Badge variant="secondary" className="text-[11px] font-mono">
              {plants.length} 個廠區
            </Badge>
          </div>

          {/* Search bar & Add Button */}
          <div className="flex items-center space-x-2">
            <div className="relative flex-1">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜尋廠區名稱、代碼、城市..."
                className="h-8 pl-8 text-xs bg-slate-950 border-slate-800 text-slate-200"
              />
            </div>
            <Button
              size="sm"
              onClick={() => setShowAddModal(true)}
              className="h-8 px-2.5 bg-primary hover:bg-primary/90 text-primary-foreground text-xs font-medium"
              title="新增智慧廠區"
            >
              <Plus className="w-3.5 h-3.5 mr-1" />
              新增
            </Button>
          </div>
        </div>

        {/* Plant Cards List */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
          {filteredPlants.map((plant) => {
            const isSelected = selectedPlant?.id === plant.id;
            return (
              <div
                key={plant.id}
                onClick={() => handleFlyToPlant(plant)}
                className={`p-3.5 rounded-xl border transition-all cursor-pointer relative ${
                  isSelected
                    ? 'bg-slate-900/90 border-cyan-500 shadow-md shadow-cyan-950/40 ring-1 ring-cyan-500/40'
                    : 'bg-slate-900/50 border-slate-800 hover:border-slate-700 hover:bg-slate-900/80'
                }`}
              >
                {/* Header */}
                <div className="flex items-start justify-between gap-2 mb-1.5">
                  <div>
                    <h3 className="text-xs font-bold text-slate-100 flex items-center space-x-1.5">
                      <span>{plant.name}</span>
                    </h3>
                    <p className="text-[11px] text-slate-400 flex items-center mt-0.5">
                      <MapPin className="w-3 h-3 mr-1 text-slate-500 shrink-0" />
                      <span className="truncate">{plant.locationName}</span>
                    </p>
                  </div>
                  <Badge
                    variant={plant.status === 'online' ? 'success' : 'warning'}
                    className="text-[10px] shrink-0 font-mono"
                  >
                    {plant.status === 'online' ? '● 運行中' : '● 待機'}
                  </Badge>
                </div>

                {/* Info grid */}
                <div className="grid grid-cols-2 gap-1.5 text-[11px] bg-slate-950/70 p-2 rounded-lg border border-slate-800/80 my-2">
                  <div className="flex items-center space-x-1.5 text-slate-400">
                    <Cpu className="w-3.5 h-3.5 text-cyan-400" />
                    <span>車隊: <strong className="text-slate-200">{plant.robotCount} 台</strong></span>
                  </div>
                  <div className="flex items-center space-x-1.5 text-slate-400">
                    <Radio className="w-3.5 h-3.5 text-emerald-400" />
                    <span>任務: <strong className="text-slate-200">{plant.activeTasks} 件</strong></span>
                  </div>
                </div>

                {/* Quick Action Buttons */}
                <div className="flex items-center justify-between pt-1 gap-2">
                  <Button
                    size="sm"
                    className="h-7 text-[11px] flex-1 bg-gradient-to-r from-emerald-600 to-cyan-600 hover:from-emerald-500 hover:to-cyan-500 text-white font-medium shadow-sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpenIsaacSim(plant);
                    }}
                    title="開啟 Isaac Sim WebRTC 數位雙生模擬畫面"
                  >
                    <Video className="w-3 h-3 mr-1" />
                    Isaac Sim 模擬畫面
                  </Button>

                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-[11px] border-slate-700 bg-slate-950/80 hover:bg-slate-800 text-slate-300"
                    onClick={(e) => {
                      e.stopPropagation();
                      onNavigateToMonitor(plant);
                    }}
                    title="進入該廠區 Open-RMF 即時監控"
                  >
                    <Radio className="w-3 h-3 mr-1 text-primary" />
                    即時監控
                  </Button>
                </div>
              </div>
            );
          })}

          {filteredPlants.length === 0 && (
            <div className="p-8 text-center text-xs text-slate-500 space-y-2">
              <Search className="w-6 h-6 mx-auto text-slate-600" />
              <p>無符合搜尋條件的廠區</p>
            </div>
          )}
        </div>

        {/* Global Summary Footer */}
        <div className="p-3 border-t border-slate-800 bg-slate-950/80 text-[11px] text-slate-400 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span>全球 AMR 總計: <strong className="text-slate-200">{plants.reduce((acc, p) => acc + p.robotCount, 0)} 台</strong></span>
          </div>
          <button
            onClick={handleResetWorldView}
            className="text-xs text-cyan-400 hover:text-cyan-300 flex items-center space-x-1"
          >
            <Compass className="w-3.5 h-3.5" />
            <span>全球視角</span>
          </button>
        </div>
      </aside>

      {/* 2. Center: MapLibre GL Canvas Container */}
      <main className="flex-1 h-full relative">
        <div ref={mapContainerRef} className="w-full h-full" />

        {/* Floating Map Controls (Top-Left of map area) */}
        <div className="absolute top-4 left-4 z-10 flex items-center space-x-2 pointer-events-auto">
          {/* Style Selector */}
          <div className="relative">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowStyleMenu(!showStyleMenu)}
              className="h-8 text-xs bg-slate-900/90 backdrop-blur border-slate-700 shadow-lg text-slate-200"
            >
              <Layers className="w-3.5 h-3.5 mr-1 text-cyan-400" />
              {TILE_STYLES[activeStyleKey].name}
            </Button>

            {showStyleMenu && (
              <div className="absolute top-10 left-0 bg-slate-900 border border-slate-700 rounded-lg shadow-xl p-1 z-20 w-60 space-y-0.5">
                {Object.entries(TILE_STYLES).map(([key, item]) => (
                  <button
                    key={key}
                    onClick={() => handleStyleChange(key)}
                    className={`w-full text-left px-2.5 py-1.5 text-xs rounded-md transition-colors flex items-center justify-between ${
                      activeStyleKey === key
                        ? 'bg-primary text-white font-medium'
                        : 'text-slate-300 hover:bg-slate-800'
                    }`}
                  >
                    <span>{item.name}</span>
                    {activeStyleKey === key && <Check className="w-3 h-3" />}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Reset World View Button */}
          <Button
            variant="outline"
            size="sm"
            onClick={handleResetWorldView}
            className="h-8 text-xs bg-slate-900/90 backdrop-blur border-slate-700 shadow-lg text-slate-200"
            title="縮放至全球全景視角"
          >
            <Compass className="w-3.5 h-3.5 mr-1 text-emerald-400" />
            全球視角
          </Button>
        </div>

        {/* Bottom Floating Inspector: Selected Plant Banner */}
        {selectedPlant && (
          <div className="absolute bottom-5 left-4 right-4 md:left-8 md:right-8 z-10 pointer-events-auto">
            <div className="bg-[#0b111e]/95 backdrop-blur-md border border-cyan-800/60 rounded-2xl p-4 shadow-2xl flex flex-col md:flex-row md:items-center justify-between gap-4">
              {/* Plant Meta */}
              <div className="flex items-start space-x-3.5">
                <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-600 to-blue-600 flex items-center justify-center text-white shadow-lg shadow-cyan-600/30 shrink-0">
                  <Building2 className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center space-x-2">
                    <h3 className="font-bold text-sm md:text-base text-slate-100">{selectedPlant.name}</h3>
                    <Badge variant="outline" className="text-[10px] border-cyan-500/50 text-cyan-400 font-mono">
                      {selectedPlant.code}
                    </Badge>
                    <Badge variant="success" className="text-[10px]">
                      {selectedPlant.status === 'online' ? '連線運行中' : '待機'}
                    </Badge>
                  </div>
                  <p className="text-xs text-slate-400 mt-0.5">{selectedPlant.locationName}</p>
                  <p className="text-[11px] text-slate-500 font-mono mt-0.5">
                    經緯度: [{selectedPlant.coordinates[0].toFixed(4)}, {selectedPlant.coordinates[1].toFixed(4)}] •
                    面積: {selectedPlant.areaM2.toLocaleString()} m² •
                    WebRTC 信令: {selectedPlant.isaacSimWebRTCUrl}
                  </p>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center space-x-2.5 shrink-0 flex-wrap">
                {/* 1. Primary: Isaac Sim WebRTC Button */}
                <Button
                  size="sm"
                  className="h-9 px-4 bg-gradient-to-r from-emerald-500 via-teal-500 to-cyan-500 hover:from-emerald-400 hover:to-cyan-400 text-white font-bold text-xs shadow-lg shadow-emerald-500/20"
                  onClick={() => onOpenIsaacSim(selectedPlant)}
                  title="開啟 Isaac Sim WebRTC 數位雙生模擬串流"
                >
                  <Video className="w-4 h-4 mr-1.5" />
                  打開 Isaac Sim 模擬畫面
                </Button>

                {/* 2. Enter Plant Monitor */}
                <Button
                  size="sm"
                  variant="outline"
                  className="h-9 text-xs border-slate-700 bg-slate-900/80 hover:bg-slate-800 text-slate-200"
                  onClick={() => onNavigateToMonitor(selectedPlant)}
                  title="進入此廠區 Open-RMF 即時車隊監控"
                >
                  <Radio className="w-3.5 h-3.5 mr-1.5 text-primary" />
                  進入即時監控
                </Button>

                {/* 3. Edit Traffic Network */}
                <Button
                  size="sm"
                  variant="secondary"
                  className="h-9 text-xs text-slate-300 hover:text-white"
                  onClick={() => onNavigateToEdit(selectedPlant)}
                  title="編輯此廠區 Open-RMF 導航路網"
                >
                  <GitCommit className="w-3.5 h-3.5 mr-1 text-slate-400" />
                  編輯路網
                </Button>

                {/* Close card button */}
                <button
                  onClick={() => onSelectPlant(null as any)}
                  className="text-slate-500 hover:text-slate-300 p-1.5 rounded-lg"
                  title="關閉資訊列"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* 3. Add Plant Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#0b111e] border border-slate-700 rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden">
            <header className="px-5 py-4 border-b border-slate-800 flex items-center justify-between bg-[#0e1626]">
              <div className="flex items-center space-x-2">
                <Plus className="w-4 h-4 text-primary" />
                <h3 className="font-bold text-sm text-slate-100">新增智慧工廠 / 倉儲廠區</h3>
              </div>
              <button
                onClick={() => setShowAddModal(false)}
                className="text-slate-400 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </header>

            <form onSubmit={handleCreatePlant} className="p-5 space-y-3.5 text-xs">
              <div>
                <label className="text-slate-400 block mb-1">廠區名稱 *</label>
                <Input
                  required
                  placeholder="例如：高雄前鎮自動化二廠"
                  value={newPlantName}
                  onChange={(e) => setNewPlantName(e.target.value)}
                  className="h-8 bg-slate-950 border-slate-800 text-xs"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-slate-400 block mb-1">廠區代碼 (Code)</label>
                  <Input
                    placeholder="KH-AUTO-02"
                    value={newPlantCode}
                    onChange={(e) => setNewPlantCode(e.target.value)}
                    className="h-8 bg-slate-950 border-slate-800 text-xs font-mono"
                  />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">國家 / 地區</label>
                  <Input
                    value={newCountry}
                    onChange={(e) => setNewCountry(e.target.value)}
                    className="h-8 bg-slate-950 border-slate-800 text-xs"
                  />
                </div>
              </div>

              <div>
                <label className="text-slate-400 block mb-1">地理位置說明</label>
                <Input
                  placeholder="台灣 高雄市前鎮科技產業園區"
                  value={newLocation}
                  onChange={(e) => setNewLocation(e.target.value)}
                  className="h-8 bg-slate-950 border-slate-800 text-xs"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-slate-400 block mb-1">經度 (Longitude, X) *</label>
                  <Input
                    required
                    type="number"
                    step="0.0001"
                    placeholder="120.3120"
                    value={newLng}
                    onChange={(e) => setNewLng(e.target.value)}
                    className="h-8 bg-slate-950 border-slate-800 text-xs font-mono"
                  />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">緯度 (Latitude, Y) *</label>
                  <Input
                    required
                    type="number"
                    step="0.0001"
                    placeholder="22.5850"
                    value={newLat}
                    onChange={(e) => setNewLat(e.target.value)}
                    className="h-8 bg-slate-950 border-slate-800 text-xs font-mono"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-slate-400 block mb-1">預估車隊規模 (台數)</label>
                  <Input
                    type="number"
                    min="1"
                    value={newRobots}
                    onChange={(e) => setNewRobots(e.target.value)}
                    className="h-8 bg-slate-950 border-slate-800 text-xs"
                  />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">Isaac Sim WebRTC URL</label>
                  <Input
                    value={newWebRTCUrl}
                    onChange={(e) => setNewWebRTCUrl(e.target.value)}
                    placeholder="ws://localhost:8080/webrtc"
                    className="h-8 bg-slate-950 border-slate-800 text-xs font-mono"
                  />
                </div>
              </div>

              <div>
                <label className="text-slate-400 block mb-1">備註說明</label>
                <Input
                  placeholder="廠區主要搬運流程、AMR 類型與調度規則簡述"
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  className="h-8 bg-slate-950 border-slate-800 text-xs"
                />
              </div>

              <div className="pt-2 flex items-center justify-end space-x-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setShowAddModal(false)}
                  className="text-xs border-slate-700"
                >
                  取消
                </Button>
                <Button type="submit" size="sm" className="text-xs bg-primary hover:bg-primary/90">
                  確定新增廠區
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
