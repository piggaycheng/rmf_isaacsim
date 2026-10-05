import { PlantSite } from '@/types/rmf';

export const INITIAL_PLANT_SITES: PlantSite[] = [
  {
    id: 'site_hsinchu_fab1',
    name: '新竹科學園區先進晶圓一廠',
    code: 'HSINCHU-FAB1',
    locationName: '台灣 新竹科學園區 (Hsinchu Science Park)',
    country: '台灣',
    coordinates: [121.0135, 24.7820],
    status: 'online',
    robotCount: 6,
    activeTasks: 3,
    areaM2: 18500,
    description: '主力半導體潔淨室與自動化物料搬運 (AMR / OHT) 示範基地，運行 Open-RMF 車隊核心。',
    isaacSimWebRTCUrl: 'ws://localhost:8080/webrtc',
    cameraTopic: 'slam/cameras',
    mqttBrokerUrl: 'ws://localhost:8083/mqtt',
  },
  {
    id: 'site_tainan_logistics',
    name: '台南智慧立體物流中心',
    code: 'TAINAN-LOG1',
    locationName: '台灣 台南科學園區 (Southern Taiwan Science Park)',
    country: '台灣',
    coordinates: [120.2785, 23.1090],
    status: 'online',
    robotCount: 12,
    activeTasks: 7,
    areaM2: 42000,
    description: '高吞吐量高架立體倉儲，全自動堆高機與潛伏式 AGV 混合協同調度。',
    isaacSimWebRTCUrl: 'ws://localhost:8081/webrtc',
    cameraTopic: 'tainan/cameras',
  },
  {
    id: 'site_taichung_precision',
    name: '台中精密機械製造二廠',
    code: 'TAICHUNG-MCH2',
    locationName: '台灣 台中精密機械科技創新園區',
    country: '台灣',
    coordinates: [120.5980, 24.1610],
    status: 'online',
    robotCount: 4,
    activeTasks: 2,
    areaM2: 12500,
    description: 'CNC 自動化加工與機械手臂上下料 AMR 無人化黑燈工廠產線。',
    isaacSimWebRTCUrl: 'ws://localhost:8082/webrtc',
    cameraTopic: 'taichung/cameras',
  },
  {
    id: 'site_silicon_valley',
    name: '矽谷機器人自主研發中心',
    code: 'SV-ROBO-RND',
    locationName: '美國 加州山景城 (Mountain View, CA)',
    country: '美國',
    coordinates: [-122.0839, 37.3861],
    status: 'online',
    robotCount: 5,
    activeTasks: 1,
    areaM2: 9800,
    description: '次世代具身智能 (Embodied AI) AMR 與 NVIDIA Isaac Sim 數位雙生高精模擬測試場。',
    isaacSimWebRTCUrl: 'ws://localhost:8083/webrtc',
    cameraTopic: 'silicon_valley/cameras',
  },
  {
    id: 'site_munich_plant',
    name: '德國慕尼黑自動化組裝廠',
    code: 'MUC-AUTO-01',
    locationName: '德國 慕尼黑工業區 (Munich Industrial Hub)',
    country: '德國',
    coordinates: [11.5820, 48.1351],
    status: 'online',
    robotCount: 8,
    activeTasks: 4,
    areaM2: 31000,
    description: '歐洲車規級零件智慧物流，支援 VDA 5050 與 Open-RMF 雙軌協同通訊。',
    isaacSimWebRTCUrl: 'ws://localhost:8084/webrtc',
    cameraTopic: 'munich/cameras',
  },
  {
    id: 'site_yokohama_center',
    name: '日本橫濱先端技術驗證廠',
    code: 'YOKOHAMA-TECH',
    locationName: '日本 神奈川縣橫濱市 (Yokohama, Japan)',
    country: '日本',
    coordinates: [139.6380, 35.4437],
    status: 'online',
    robotCount: 6,
    activeTasks: 2,
    areaM2: 15600,
    description: '高密度協作無人搬運系統，整合人機共融安全防護與即時語音派遣。',
    isaacSimWebRTCUrl: 'ws://localhost:8085/webrtc',
    cameraTopic: 'yokohama/cameras',
  },
];

const LOCAL_STORAGE_KEY = 'rmf_plant_sites';

export function loadPlantSites(): PlantSite[] {
  try {
    const cached = localStorage.getItem(LOCAL_STORAGE_KEY);
    if (cached) {
      const parsed = JSON.parse(cached);
      if (Array.isArray(parsed) && parsed.length > 0) {
        // Hydrate newly added fields from defaults
        return parsed.map((p: PlantSite) => {
          const init = INITIAL_PLANT_SITES.find((s) => s.id === p.id);
          return init ? { ...init, ...p, cameraTopic: p.cameraTopic || init.cameraTopic } : p;
        });
      }
    }
  } catch (e) {
    console.error('Failed to parse cached plant sites', e);
  }
  return INITIAL_PLANT_SITES;
}

export function savePlantSites(sites: PlantSite[]): void {
  try {
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(sites));
  } catch (e) {
    console.error('Failed to save plant sites to localStorage', e);
  }
}
