# 多樓層 3D 即時監控與跨樓層電梯視覺化實作計畫

## 1. 目標與交接範圍

在保留現有 2D 即時監控與路網編輯功能的前提下，於「即時車隊監控 (`monitor` 模式)」新增 **3D 多樓層同屏監控模式**，讓操作員無須頻繁切換上方樓層分頁，即可同時綜覽各樓層地圖、路網、所有樓層的車輛動態，以及跨樓層運作中的 3D 電梯。

本文件提供給實作 agent 作為完整規格與實作藍圖。實作前請先閱讀根目錄 `AGENTS.md`，遵守開發模式與熱重載規範（切勿執行 `npm run build` 或 `docker compose build`），僅做相關變更，不自行 commit。

### 核心需求範圍

1. **多樓層地圖依 Z 軸高度分層呈現 + 車輛維持 2D 風格繪製於地圖平面上**：
   - 每張樓層地圖（含 SLAM 雷達底圖、導航點 Waypoints、路線 Lanes、方向箭頭）依據樓層在 3D 空間中以不同的 Z 高度（Elevation）水平展開呈現。
   - 各樓層的車輛（AMR）維持與既有 2D 畫面一致的視覺呈現方式（圓形車身底盤、狀態光暈、方向箭頭、車名與電量膠囊標籤、規劃路徑線），直接貼合繪製在所屬樓層的 3D 地圖平面上。
2. **保留既有 2D 模式，提供 2D / 3D 自由切換**：
   - 2D 模式（`MapCanvas.tsx`）的所有功能與操作體驗完整保留。
   - 在即時監控畫面（`monitor` 模式）頂部工具列提供「2D 平面 / 3D 立體」切換按鈕。
   - 切換回 `edit`（路網編輯）模式時，固定使用既有 2D `MapCanvas` 進行精準點位編輯。
3. **跨樓層 3D 電梯井道與動態電梯車廂呈現**：
   - 依據地圖中的 `lifts` 定義，在各樓層地圖之間繪製貫穿樓層的 3D 電梯井道（Shaft）與 3D 電梯車廂（Cabin）。
   - 串接後端 Open-RMF `/lift_states` 即時電梯狀態（樓層位置、升降方向、門開關狀態），當電梯運作時，在兩張樓層地圖之間動態呈現 3D 電梯車廂的升降移動與開關門狀態；當車輛搭乘電梯跨樓層時，車輛可隨電梯或樓層狀態平滑過渡。

### 不包含範圍

- 不在 3D 模式中實作路網點位新增、拖曳或框選編輯（編輯維持於 2D 模式進行）。
- 不引入複雜的外部 3D 車輛 `.gltf` / `.glb` 模型資產管理（依需求 1，車輛維持與 2D 相同的圖示風格繪製於各樓層地圖平面上）。
- 不修改 Open-RMF 交通排程、Fleet Adapter 或 Lift Adapter 的底層控制邏輯。

---

## 2. 技術可行性與架構評估

### 2.1 可行性結論：高度可行，且現有資料結構已具備關鍵基礎

經盤點現有程式碼，本專案已具備實作多樓層 3D 監控所需的核心要素：

1. **統一的建築座標系（Open-RMF Building Frame）已存在**：
   - `frontend/src/types/rmf.ts` 已定義 `MapLevel`（含 `transform: LevelTransform` 與可選 `elevation`）、`toBuildingCoords()` 與 `toRobotCoords()`。
   - `MapOverlayAlignModal.tsx` 已實作多樓層底圖對齊，各樓層的 `transform`（`rotation`, `scale`, `translation`）可將各樓層獨立的 SLAM 地圖座標精準轉換至共用的 **Building Frame**。
   - 在 Building Frame 下，不同樓層的同一部電梯站點（如 `lift_1_1F` 與 `lift_1_B1`）在 $(X, Y)$ 平面上完全重疊對齊（誤差 $< 1\text{m}$，由 `nav_graph_builder.py` 強制對齊至 `lift.x, lift.y`），因此在 3D 空間中只要沿 Z 軸拉開樓層高度，電梯井道即可垂直貫穿各樓層的電梯口。
2. **多樓層 SLAM 底圖與車隊狀態已同步至前端**：
   - `App.tsx` 的 `floorSlamMaps: Record<string, SlamMap>` 已同時保存所有樓層的底圖資訊與圖片 URL（`/api/map/image/{level_name}`）。
   - 後端 `/fleet_states` 廣播的 `robots` 陣列已包含所有樓層的車輛，且每台車具有 `level_name`（所在樓層）、Building Frame 下的 `(x, y, yaw)` 與 `path`（含各路徑點的 `level_name`）。
3. **電梯狀態來源已存在於 ROS 2 / MQTT**：
   - `workspace/mqtt_lift_adapter/mqtt_lift_adapter.py` 已將 MQTT 電梯狀態（`rmf/lift/<name>/state`）橋接發布至 ROS 2 `/lift_states`（`rmf_lift_msgs/msg/LiftState`）。
   - 後端 `backend/rmf_service.py` 只需新增 `/lift_states` 訂閱並透過 WebSocket `/ws/fleet` 廣播，前端即可取得所有電梯的即時樓層、目標樓層、升降方向與開關門狀態。

### 2.2 3D 渲染套件選型：`three` + `@react-three/fiber` + `@react-three/drei`

採用 **`@react-three/fiber` (R3F) + `@react-three/drei`** 搭配 `three`，相較於手寫 Imperative Three.js 具有顯著的開發效率與維護優勢：

| 實作項目 | 使用 `@react-three/fiber` + `@react-three/drei` 的優勢 |
| --- | --- |
| **React 宣告式架構** | 直接以 `<FloorPlane />`、`<LiftShaft3D />` JSX 元件映射 `levels` 與 `lifts` 陣列，當樓層、點位、車輛或電梯狀態更新時自動響應，無須手寫複雜的 Scene Graph 增刪同步邏輯。 |
| **互動與點擊派單（Raycasting）** | R3F 內建 `<mesh onClick={(e) => ...} />` 事件系統，直接從 `e.point` 或 `e.uv` 取得點擊在該樓層平面的座標，省去手寫 `THREE.Raycaster` 與螢幕正規化座標換算。 |
| **3D 電梯與視覺特效元件** | `@react-three/drei` 內建 `<OrbitControls />`（相機控制）、`<Edges />`（一鍵繪製 3D 電梯井道與車廂發光邊框）、`<Html />` / `<Billboard />`（在 3D 電梯與樓層旁直接渲染銳利的 Tailwind CSS 狀態標籤），大幅減少樣板程式碼。 |
| **平滑動畫與生命週期管理** | 透過 `useFrame((_, delta) => ...)` 即可在 `<LiftShaft3D />` 內部獨立驅動電梯 Z 軸平滑升降與車廂門開合；`<Canvas />` 自動處理視窗 Resize、高 DPI 與 Unmount 時的 WebGL 資源釋放。 |

> [!IMPORTANT]
> **React 18 版本相容性注意**：
> 本專案前端使用 `react@^18.3.1`。`@react-three/fiber` v9 僅支援 React 19，因此安裝時**必須指定搭配 React 18 的穩定版本**：
> - `three@^0.170.0`、`@types/three@^0.170.0`
> - `@react-three/fiber@^8.17.10`
> - `@react-three/drei@^9.117.3`

### 2.3 「2D 地圖與車輛繪製在 3D 樓層平面」的混合渲染設計

為滿足需求 1「每張地圖依樓層在 3D 不同 Z 高度呈現，且車子呈現方式和 2D 一樣畫在地圖上」，採用 **R3F `<FloorLevelPlane />` + Offscreen 2D `CanvasTexture`** 設計：

1. **Three.js 採用 Z-Up 座標系**：
   - 在 R3F `<Canvas camera={{ position: [0, -35, 28], up: [0, 0, 1], fov: 45 }} />` 設定 `up: [0, 0, 1]`，使 3D 世界座標 $(X, Y, Z)$ 直接對應 Open-RMF Building Frame 的 $(X_{\text{building}}, Y_{\text{building}}, Z_{\text{elevation}})$。
2. **每層樓一個 `<FloorLevelPlane />` 元件**：
   - 每個樓層元件內部維護一個 `useRef<HTMLCanvasElement>(document.createElement('canvas'))` 與 `THREE.CanvasTexture`。
   - 該樓層的 SLAM 底圖、網格、Lanes（路線與方向箭頭）、Waypoints（導航點與名稱標籤）以及該樓層上的 Robots（圓形底盤、狀態光暈、方向箭頭、車名/電量標籤、規劃路徑），皆直接呼叫與 `MapCanvas.tsx` 相同視覺規範的 2D Canvas 繪製函式，繪製於該樓層的 Offscreen `<canvas>` 上，完成後設定 `texture.needsUpdate = true`。
   - **座標對齊方式（Local Floor Canvas + Group Transform）**：
     - 每個樓層的 Offscreen Canvas 在該樓層自身座標系（Local Floor Frame）繪製，與 2D `MapCanvas.tsx` 及 `visibleRobots` 邏輯 100% 一致，確保 SLAM 底圖為正矩形且無須裁切。
     - 在 R3F 中，將該樓層 `<group position={[bCenter.x, bCenter.y, elevation]} rotation={[0, 0, -transform.rotation]} scale={[1 / transform.scale, 1 / transform.scale, 1]}>` 套用 `level.transform` 的逆變換（與 `MapOverlayAlignModal.tsx` 第 295–313 行相同），即可讓所有樓層在 3D Building Frame 中精準對齊！
3. **跨樓層 3D 電梯元件 `<LiftStructure3D />`**：
   - 使用宣告式 `<mesh>` + `<boxGeometry>` + `<Edges />` 繪製貫穿樓層的電梯井道與電梯車廂。
   - 使用 `useFrame` 對電梯車廂 `<group ref={cabinRef}>` 的 `position.z` 進行平滑插值（Lerp），並搭配 `<Html center distanceFactor={25}>` 渲染高質感 Tailwind 狀態徽章（如 `🛗 lift_1 · 1F → B1 ▼`）。

---

## 3. 現況與修改入口

以下為撰寫計畫時確認的程式碼位置；實作時請以函式、元件與型別名稱定位。

| 檔案位置 | 現況說明 | 實作責任 |
| --- | --- | --- |
| `frontend/package.json` | 尚無 Three.js / R3F 依賴 | 新增 `three`, `@types/three`, `@react-three/fiber@^8.17.10`, `@react-three/drei@^9.117.3` |
| `backend/state.py` | 已有 `current_robots`, `current_tasks`, `discovered_robots`，尚無 `current_lifts` | 新增 `current_lifts: Dict[str, Dict[str, Any]] = {}` 儲存即時電梯狀態 |
| `backend/rmf_service.py` | `init_ros2_node` 已訂閱 `/fleet_states` 等，尚未訂閱 `/lift_states` | 新增訂閱 `rmf_lift_msgs/msg/LiftState`（topic `/lift_states`），更新 `state.current_lifts` |
| `backend/controller/fleet_controller.py` | `/ws/fleet` 連線初始與 `simulation_loop` 廣播 `fleet_states`、`tasks` | 新增 `GET /api/lifts` 端點；於 `/ws/fleet` 初始連線與狀態變更時廣播 `{"type": "lift_states", "lifts": ...}` |
| `frontend/src/types/rmf.ts` | 已有 `MapLevel`, `MapLift`, `Robot`, `SlamMap` | 新增 `LiftRuntimeState` 型別定義與樓層高度計算輔助函式 `resolveLevelElevations` |
| `frontend/src/components/MapCanvas.tsx` | 2D Canvas 繪圖與互動元件 | 保留全部 2D 行為；可抽離共用 2D 繪圖邏輯至輔助函式供 2D 與 3D 樓層材質共用 |
| `frontend/src/components/Map3DCanvas.tsx` *(新增)* | 尚不存在 | 使用 `@react-three/fiber` + `@react-three/drei` 實作多樓層 3D 場景、`<FloorLevelPlane />`、`<LiftStructure3D />`、視角控制與站點點擊派單 |
| `frontend/src/App.tsx` | `monitor` 模式僅渲染 `MapCanvas` 與頂部樓層切換 Tabs | 新增 `monitorViewMode: '2d' | '3d'` 狀態、WebSocket `lift_states` 接收、頂部 2D/3D 切換按鈕 |

---

## 4. 座標系、樓層 Z 高度與電梯狀態契約

### 4.1 樓層 Z 高度（Elevation）推算規則

目前 `active_nav_graph.json` 中的 `levels` 陣列順序通常以基準層（如 `1F`）為第一項，其餘樓層依新增順序排列（例如 `["1F", "B1"]`），且不一定帶有 `elevation` 數值欄位。因此在 3D 畫面中**不可單純以陣列 index 乘上高度**（否則 `B1` 會在 `1F` 上方）。

請在 `frontend/src/types/rmf.ts` 實作 `parseFloorOrder(levelName: string): number | null` 與 `resolveLevelElevations(levels: MapLevel[], spacingM = 12): Record<string, number>`：

1. **明確指定優先**：若 `level.elevation` 為有效有限數值（`typeof level.elevation === 'number' && Number.isFinite(level.elevation)`），優先採用其相對順序或數值。
2. **樓層名稱語意解析**：
   - 地下室：`/^B(\d+)$/i` $\rightarrow -\text{Number}(match[1])$（例如 `B1` $\rightarrow -1$，`B2` $\rightarrow -2$）。
   - 地上樓層：`/^(\d+)F$/i` 或 `/^[LF](\d+)$/i` $\rightarrow \text{Number}(match[1]) - 1$（例如 `1F` / `L1` / `F1` $\rightarrow 0$，`2F` / `L2` $\rightarrow +1$，`3F` $\rightarrow +2$）。
   - 地面層別名：`/^G(F)?$/i` $\rightarrow 0$。
3. **Fallback 排序**：若名稱無法由上述規則解析，則依其在 `levels` 陣列中的索引順序，接續排列於已解析樓層之後。
4. **3D 視覺層高間距（`floorSpacingM`）**：
   - 預設間距為 `12` 公尺（即 `1F` 在 $Z = 0$、`B1` 在 $Z = -12$、`2F` 在 $Z = +12$）。
   - 3D 介面提供「樓層間距」滑桿（範圍 `5m ~ 30m`），方便使用者針對不同大小的廠區地圖自由拉開或收攏 Z 軸距離，避免上下樓層互相遮擋。

### 4.2 Local Floor Frame 與 Building Frame 3D 轉換公式

根據 `frontend/src/types/rmf.ts` 與 `backend/nav_graph_builder.py` 的定義：
- 已知樓層 `transform` 為 $(T_x, T_y, \theta, s)$，由 Building Frame $(x_b, y_b)$ 轉為該樓層地圖座標 $(x_r, y_r)$ 的公式為 `toRobotCoords`；反向由該樓層地圖座標 $(x_r, y_r)$ 轉回 Building Frame $(x_b, y_b)$ 的公式為 `toBuildingCoords`：
  $$\begin{bmatrix} x_b \\ y_b \end{bmatrix} = \frac{1}{s} \begin{bmatrix} \cos\theta & \sin\theta \\ -\sin\theta & \cos\theta \end{bmatrix} \begin{bmatrix} x_r - T_x \\ y_r - T_y \end{bmatrix}$$
- 在 R3F（Z-Up，世界座標為 Building Frame）中：
  - 設該樓層 2D Canvas 涵蓋局部座標範圍 $[x_{\min}, x_{\max}] \times [y_{\min}, y_{\max}]$，局部中心點為 $(c_x, c_y) = \left(\frac{x_{\min}+x_{\max}}{2}, \frac{y_{\min}+y_{\max}}{2}\right)$，寬高為 $(W, H) = (x_{\max}-x_{\min}, y_{\max}-y_{\min})$。
  - 透過 `const bCenter = toBuildingCoords(level.transform, cx, cy)` 算出該平面中心在 Building Frame 的座標。
  - 在 R3F 中渲染該樓層平面：
    ```tsx
    <group
      position={[bCenter.x, bCenter.y, elevation]}
      rotation={[0, 0, -t.rotation]}
      scale={[1 / t.scale, 1 / t.scale, 1]}
    >
      <mesh onClick={handleFloorClick}>
        <planeGeometry args={[W, H]} />
        <meshBasicMaterial map={canvasTexture} transparent opacity={floorOpacity} depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
    </group>
    ```
  - **注意 Canvas Y 軸方向**：HTML5 2D Canvas 的 Y 軸向下（螢幕座標），而笛卡兒/Building 座標系 Y 軸向上。在局部 Canvas 中繪製點位 $(x_r, y_r)$ 時，映射為 `canvasX = (x_r - x_min) * pxPerMeter`、`canvasY = (y_max - y_r) * pxPerMeter`（即 Y 軸反向，與 `MapCanvas.tsx` 的 `worldToScreen` 完全相同），則貼到 Three.js `planeGeometry`（預設 UV 左下為 $(0,0)$、右上為 $(1,1)$）時方向即 100% 正確對應！
- **驗證指標**：套用轉換後，`active_nav_graph.json` 中 `1F` 的 `lift_1_1F (-15.77, -4.5)` 與 `B1` 的 `lift_1_B1 (0.19, 0.24)` 在 3D 空間中的 $(X, Y)$ 座標必須雙雙對齊於 `lift_1` 中心 `(-15.77, -4.5)`，且位於電梯井道正中央。

### 4.3 電梯即時狀態（`LiftRuntimeState`）API 契約

後端將 ROS 2 `rmf_lift_msgs/msg/LiftState` 轉換為與 `mqtt_lift_adapter.py` 一致的 JSON 結構：

```json
{
  "lift_name": "lift_1",
  "available_floors": ["1F", "B1"],
  "current_floor": "1F",
  "destination_floor": "B1",
  "door_state": "closed",
  "motion_state": "down",
  "current_mode": "agv",
  "session_id": "deliveryRobot_carter1",
  "updated_at": 1760712000.123
}
```

欄位規範：
- `door_state`: `"closed"` (`0`) | `"moving"` (`1`) | `"open"` (`2`) | `"unknown"`
- `motion_state`: `"stopped"` (`0`) | `"up"` (`1`) | `"down"` (`2`) | `"unknown"` (`3`)
- `current_mode`: `"human"` (`1`) | `"agv"` (`2`) | `"fire"` (`3`) | `"offline"` (`4`) | `"emergency"` (`5`) | `"unknown"` (`0`)
- WebSocket 訊息格式：`{"type": "lift_states", "lifts": [...]}`
- REST 端點：`GET /api/lifts` 回傳 `list(state.current_lifts.values())`
- **無電梯遙測時的 Fallback 機制**：
  - 若環境尚未啟動 `mock_lift.py` 或實機電梯，使得 `current_lifts` 為空，前端 3D 畫面仍須依據 `lifts: MapLift[]`（靜態路網定義）繪製出 3D 電梯井道與停靠在預設樓層（如 `1F`）的 3D 電梯車廂。
  - 進階推斷 Fallback：若某台在線車輛正在執行跨樓層任務且座標位於電梯中心 `1.0m` 內（或車輛 `level_name` 變更），電梯目標樓層可自動跟隨該車輛所在樓層平滑移動。

---

## 5. 實作步驟與依賴

### 步驟一：安裝前端 Three.js 與 R3F / Drei 依賴

於 `frontend` 容器內安裝與 React 18 相容的套件版本：

```bash
docker compose exec -T frontend npm install three@^0.170.0 @types/three@^0.170.0 @react-three/fiber@^8.17.10 @react-three/drei@^9.117.3
```

安裝後執行 `docker compose exec -T frontend npx tsc --noEmit` 確認無型別或 peer dependency 衝突。

### 步驟二：後端電梯狀態訂閱與廣播

1. **`backend/state.py`**：
   - 新增全域狀態字典 `current_lifts: Dict[str, Dict[str, Any]] = {}`。
2. **`backend/rmf_service.py`**：
   - 在 `init_ros2_node()` 中以 `try...except` 匯入 `from rmf_lift_msgs.msg import LiftState`。
   - 建立 `/lift_states` 訂閱，將 `LiftState` 訊息映射為第 4.3 節定義的字典，存入 `state.current_lifts[msg.lift_name]`。
3. **`backend/controller/fleet_controller.py`**：
   - 新增 `GET /api/lifts` 回傳 `list(state.current_lifts.values())`。
   - 在 `/ws/fleet` 建立連線時，發送初始 `{"type": "lift_states", "lifts": list(state.current_lifts.values())}`。
   - 在 `simulation_loop()` 定期廣播區段中，同步廣播 `{"type": "lift_states", "lifts": list(state.current_lifts.values())}`。

### 步驟三：前端型別擴充與 R3F 3D 多樓層監控元件 (`Map3DCanvas.tsx`)

1. **`frontend/src/types/rmf.ts`**：
   - 新增 `LiftRuntimeState` 介面。
   - 新增 `parseFloorOrder(levelName: string)` 與 `resolveLevelElevations(levels: MapLevel[], spacingM?: number)`。

2. **建立 `frontend/src/components/Map3DCanvas.tsx`（使用 `@react-three/fiber` + `@react-three/drei`）**：
   - **子元件一：`<FloorLevelPlane />`（單一樓層 2D 畫布平面與互動）**
     - 接收該樓層的 `level`, `elevation`, `slamMap`, `waypoints`, `lanes`, `robots`, `graphs`, `selectedWaypointIds`, `floorOpacity`。
     - 內部使用 Offscreen `<canvas>`（解析度建議 `32 px/m`，尺寸依該樓層 SLAM 底圖與點位 Bounding Box 自動決定，並限制最大邊長 `<= 2048px`）繪製：
       1. 深色半透明底板與 1m 網格、外框線。
       2. 該樓層 `SlamMap` 雷達底圖影像。
       3. 該樓層 `Lanes`（含方向箭頭與雙向/單向線型）與 `Waypoints`（含類型圖示與高對比名稱框）。
       4. 該樓層 `Robots`（先透過 `toRobotCoords(level.transform, r.x, r.y)` 轉換，繪製規劃路徑發光線、圓形車身底盤、狀態光暈、方向箭頭、急停/偏離路網警示環、車名與電量膠囊標籤，與 2D 模式完全一致）。
     - 每次繪製完成後將 `canvasTexture.needsUpdate = true`。
     - 在樓層平面角落透過 `<Html position={[...]} center={false}>` 顯示清晰的樓層銘牌（例如 `1F · 2台車`、`B1 · 1台車`），點擊銘牌可聚焦該樓層。
     - 在 `<mesh onClick={(e) => ...}>` 中利用 `e.uv` 換算回該樓層局部座標 `(localX, localY)`，檢查是否有距離 $\le 0.6\text{m}$ 的 `Waypoint`；若有，則呼叫 `e.stopPropagation()` 並觸發 `onSelectWaypoint(wp.id)` 與 `onWaypointClickInMonitor(wp)` 開啟派單視窗。

   - **子元件二：`<LiftStructure3D />`（跨樓層 3D 電梯井道與動態車廂）**
     - 針對每個 `MapLift`（位於 Building Frame `[lift.x, lift.y]`，尺寸 `width` × `depth`，旋轉 `[0, 0, lift.yaw || 0]`）：
     - **電梯井道（Shaft）**：
       - 高度涵蓋所有樓層（從 $\min(Z) - 0.2$ 到 $\max(Z) + 2.6$），使用 `<mesh>` + `<boxGeometry args={[lift.width, lift.depth, shaftHeight]} />` 搭配半透明材質（`opacity: 0.08`）與 `<Edges color="#818cf8" />` 呈現立體科技感井道。
       - 在每個樓層高度 $Z_i$ 處繪製一圈樓層電梯門框底座環。
     - **3D 電梯車廂（Cabin）**：
       - 使用 `<group ref={cabinGroupRef}>` 包覆車廂主體（高約 `2.2m`）與左右兩扇可滑動車廂門（`<mesh ref={leftDoorRef}>`, `<mesh ref={rightDoorRef}>`），並加上 `<Edges color={statusColor} />`。
       - **平滑升降與開關門動畫（`useFrame`）**：
         - 根據 `liftState`（`current_floor`, `destination_floor`, `motion_state`, `door_state`）計算目標高度 `targetZ`：
           - 當 `motion_state === 'up' | 'down'` 且 `current_floor !== destination_floor` 時，根據移動經過時間在 `elevations[current_floor]` 與 `elevations[destination_floor]` 之間平滑插值（`THREE.MathUtils.lerp`），讓使用者清楚看到電梯車廂在兩張地圖之間上下移動。
           - 當 `motion_state === 'stopped'` 時，平滑停靠於 `elevations[current_floor]`。
         - 根據 `door_state === 'open'` 平滑調整左右兩扇車廂門的開合位移。
       - **3D 電梯浮動狀態標籤（`<Html />`）**：
         - 在車廂旁使用 `<Html position={[0, 0, 1.4]} center distanceFactor={25}>` 渲染不隨 3D 旋轉變形的膠囊狀態列（顯示電梯名稱、當前樓層 $\rightarrow$ 目標樓層、升降箭頭 ▲/▼、開門/關門狀態 badge）。
       - **電梯內搭乘車輛同步顯示**：
         - 若有車輛正位於此電梯內且電梯正在跨樓層移動，可在車廂地板平面同步渲染該車輛的圓形方向圖示隨車廂一起升降。

   - **子元件三：`<CameraRig />` 與 `<OrbitControls />` + 浮動 HUD 控制面板**
     - 使用 `@react-three/drei` 的 `<OrbitControls makeDefault enableDamping dampingFactor={0.08} maxPolarAngle={Math.PI * 0.48} />`。
     - 支援預設視角切換（等角 3D 俯視、正面平視觀察電梯、聚焦特定樓層）與「樓層間距 (`5m ~ 30m`)」、「底圖透明度 (`0.2 ~ 1.0`)」即時滑桿。

### 步驟四：`App.tsx` 整合 2D / 3D 模式切換

1. 在 `App.tsx` 新增狀態 `monitorViewMode: '2d' | '3d'`（持久化於 `localStorage` 的 `'rmf_monitor_view_mode'`）與 `liftStates: Record<string, LiftRuntimeState>`。
2. 在 WebSocket `ws.onmessage` 中新增處理 `data.type === 'lift_states'`，並於啟動時 `fetch('/api/lifts')` 初始化 `liftStates`。
3. 在 `mode === 'monitor'` 的頂部浮動工具列（Floor Switcher Tabs 旁）新增 **「2D 平面 / 3D 立體」切換按鈕組**：
   - 當處於 `2d` 模式時：渲染原有的 `<MapCanvas ... />`，所有既有操作 100% 維持不變。
   - 當處於 `3d` 模式時：渲染 `<Map3DCanvas ... />`，同時呈現所有樓層、各層車輛與 3D 電梯；點擊頂部樓層按鈕或右側車隊列表中的樓層標籤時，可讓 3D 相機平滑聚焦至該樓層高度。
   - 當切換至 `edit`（路網編輯）模式時，固定渲染 2D `<MapCanvas />`。

---

## 6. 必要測試與驗收條件

| 驗收項目 | 必須確認條件 |
| --- | --- |
| **1. 2D / 3D 模式自由切換與保留** | 在「即時車隊監控」畫面頂部可隨時切換「2D 平面」與「3D 立體」；切回 2D 時所有原有功能（平移、縮放、站點點擊派單、單層切換）100% 正常；切換至「路網編輯」時維持 2D 編輯畫布。 |
| **2. 多樓層 Z 軸分層與座標對齊** | 在包含 `1F` 與 `B1` 的多樓層地圖中，3D 模式下 `1F` 位於上方（$Z = 0$）、`B1` 位於下方（$Z = -12$），且兩層樓的 SLAM 底圖、導航點、路線皆正確套用 `LevelTransform`，使 `lift_1_1F` 與 `lift_1_B1` 在垂直方向精準對齊於 3D 電梯井道內。 |
| **3. 車輛於 3D 各樓層地圖上的 2D 呈現** | 位於 `1F` 與 `B1` 的車輛同時顯示在各自樓層的 3D 地圖平面上，無須切換樓層即可同時看到各層車輛；車輛外觀維持與 2D 相同的圓形底盤、方向箭頭、狀態光暈、名稱/電量膠囊標籤與規劃路徑線。 |
| **4. 3D 電梯井道與運作動畫** | 兩層地圖之間在 `lift_1`、`lift_2` 位置顯示貫穿樓層的 3D 電梯井道與 3D 電梯車廂；當電梯運作（可透過 `mock_lift.py` 或模擬跨樓層任務觸發 `/lift_states`）時，3D 電梯車廂在兩張地圖之間平滑升降，並反映開門/關門/移動狀態與樓層標籤。 |
| **5. 3D 視角控制與互動派單** | 支援滑鼠旋轉（Orbit）、平移（Pan）、滾輪縮放（Zoom）、調整樓層 Z 間距與透明度；在 3D 畫面點擊任一樓層的導航點可正常開啟任務派遣視窗並自動帶入該站點。 |
| **6. 型別安全與無 Console Error** | 前端 `docker compose exec -T frontend npx tsc --noEmit` 型別檢查零錯誤，瀏覽器切換 2D/3D 模式無 Runtime Error。 |

### 驗證指令參考

1. **前端型別檢查**：
   ```bash
   docker compose exec -T frontend npx tsc --noEmit
   ```
2. **後端電梯狀態 API 與 WebSocket 驗證**：
   - 啟動 `mock_lift.py` 測試電梯升降廣播：
     ```bash
     docker compose exec -T backend bash -lc 'source /opt/ros/jazzy/setup.bash && python3 /root/rmf_ws/workspace/mqtt_lift_adapter/mock_lift.py --lift lift_1 --floors 1F,B1'
     ```
   - 檢查 `GET /api/lifts` 回應是否包含 `lift_1` 即時狀態。
3. **Playwright UI 端對端截圖與互動驗收**：
   - 載入專案 `.agents/skills/playwright-cli/SKILL.md`，開啟 `http://localhost:5173`，驗證 2D 切換至 3D 多樓層畫面、視角控制、樓層間距調整及電梯 3D 呈現並截圖留存。

---

## 7. 交付完成標準

- [ ] `frontend/package.json` 已安裝相容 React 18 的 `three`, `@types/three`, `@react-three/fiber@^8.17.10`, `@react-three/drei@^9.117.3`，且 `npx tsc --noEmit` 通過。
- [ ] 後端已訂閱 `/lift_states` 並提供 `GET /api/lifts` 與 WebSocket `lift_states` 即時推播。
- [ ] `Map3DCanvas.tsx` 已基於 R3F + Drei 實作完成，支援多樓層 Z 高度展開、各樓層 2D 風格地圖與車輛繪製、Building Frame 座標對齊、3D 電梯井道與動態升降車廂。
- [ ] `App.tsx` 已提供 2D / 3D 模式自由切換按鈕，且完整保留既有 2D 監控與編輯功能。
- [ ] 已透過 Playwright 瀏覽器自動化與電梯狀態模擬完成視覺與互動驗收。
