# Open-RMF Web Studio 系統架構文檔

本文檔說明本專案的整體系統架構、前後端整合設計、Docker 容器掛載關係，以及 Open-RMF 的資料通訊流。

---

## 一、系統整體架構 (System Overview)

本系統整合了 **Open-RMF 核心調度引擎**、**FastAPI 後端橋接服務** 與 **React + shadcn/ui 前端互動介面**，前後端皆掛載於同一個 ROS 2 Jazzy Docker 環境中。

```mermaid
flowchart TD
    subgraph Host ["宿主機 (Ubuntu 22.04 LTS)"]
        Browser["使用者瀏覽器 (http://localhost:8000)"]
        Screen["X11 螢幕顯示 (Gazebo / RViz2 視窗)"]
        HostCode["本機專案目錄 (/home/yu/Documents/code/open_rmf)"]
    end

    subgraph Docker ["Docker 環境 (open-rmf:jazzy / Ubuntu 24.04)"]
        subgraph WebService ["Web 服務容器 (open_rmf_web)"]
            FastAPI["FastAPI 伺服器 (Port 8000)"]
            Static["React 前端靜態託管"]
            WSServer["WebSocket 廣播 (/ws/fleet)"]
            ROS2Bridge["ROS 2 背景節點 (rclpy)"]
        end

        subgraph CoreService ["RMF 核心容器 (open_rmf_jazzy)"]
            RMFCore["Open-RMF 交通調度器 (rmf_traffic)"]
            FleetAdapter["車隊轉接器 (rmf_fleet_adapter)"]
            Gazebo["Gazebo Harmonic 物理模擬器"]
        end
    end

    HostCode -->|"Volume 掛載 (./backend, ./frontend)"| FastAPI
    HostCode -->|"Volume 掛載 (./workspace)"| RMFCore
    Browser <-->|"HTTP / WebSocket (Port 8000)"| FastAPI
    Gazebo -->|"X11 顯示傳發 (/tmp/.X11-unix)"| Screen
    ROS2Bridge <-->|"ROS 2 Topics (/fleet_states)"| FleetAdapter
```

---

## 二、Open-RMF 官方標準架構 vs 本專案整合架構

### 1. 官方標準前後端分離架構
在 Open-RMF 官方標準設計中，核心與 Web 是分離的微服務：

```mermaid
flowchart LR
    A["RMF 核心 / 模擬器 (open-rmf:jazzy)"] <-->|ROS 2 / WebSocket| B["API Server (Port 8000)"]
    B <-->|REST / Socket.io| C["Web Dashboard 前端 (Port 3000)"]
    C <-->|瀏覽器操作| D["使用者瀏覽器"]
```

### 2. 本專案「編輯 + 監控二合一」整合架構
為解決官方 Web 只能監控、無法線上拉線標點的限制，本專案將**「路網編輯器 (Graph Editor)」**與**「即時監控派單中心 (Fleet Monitor)」**整合至單一 Web 應用中：

```mermaid
flowchart TD
    subgraph WebUI ["前端二合一網頁 (React + TypeScript + Canvas + shadcn/ui)"]
        ModeSwitch{"模式切換"}
        
        subgraph EditMode ["✏️ 編輯路網模式 (Edit Mode)"]
            Tool1["打點工具 (Waypoints)"]
            Tool2["連線工具 (Lanes)"]
            Tool3["站點類型 (Charger / Parking / Workcell)"]
            Tool4["路網匯出 (Export YAML / JSON)"]
        end

        subgraph MonitorMode ["📡 即時監控模式 (Monitor Mode)"]
            RobotDisplay["AMR 即時位置與方位角 (x, y, yaw)"]
            BatteryDisplay["電量狀態與車況 (Moving / Idle / Charging)"]
            InteractiveDispatch["點擊地圖站點快速派單 (Dispatch Modal)"]
            TaskQueue["任務進度追蹤佇列 (Task Queue)"]
        end
    end

    subgraph Backend ["後端橋接服務 (FastAPI)"]
        RESTAPI["REST API (地圖讀寫、任務指令)"]
        WS["WebSocket (即時推播車隊遙測資料)"]
        SimEngine["內建平滑軌跡模擬器 (Fallback)"]
        ROS2Node["原生 ROS 2 訂閱者 (/fleet_states)"]
    end

    subgraph RMFCore ["Open-RMF 核心環境"]
        RMFSchedule["RMF 交通排程與避障演算法"]
        RMFAdapters["各廠牌 Fleet Adapter"]
    end

    ModeSwitch -->|切換至編輯| EditMode
    ModeSwitch -->|切換至監控| MonitorMode

    EditMode -->|儲存與讀取路網| RESTAPI
    MonitorMode <-->|雙向資料同步| WS
    InteractiveDispatch -->|發起任務請求| RESTAPI

    RESTAPI <--> SimEngine
    WS <--> SimEngine
    SimEngine <--> ROS2Node
    ROS2Node <--> RMFCore
```

---

## 三、任務派遣與資料流序向圖 (Sequence Diagram)

以下展示中控人員於網頁端點選地圖進行「任務派遣」至車輛實際移動的完整資料生命週期：

```mermaid
sequenceDiagram
    autonumber
    actor User as 中控操作員
    participant UI as React 前端 (RMF Studio)
    participant API as FastAPI 後端
    participant ROS as ROS 2 / RMF 核心
    participant Robot as AMR 機器人 / Gazebo 模擬

    User->>UI: 1. 點擊地圖上的站點 (例如: coe)
    UI->>User: 2. 彈出任務派發視窗 (選擇巡邏/送貨/前往)
    User->>UI: 3. 確認提交「立即派遣任務」
    UI->>API: 4. POST /api/tasks/dispatch 或 WebSocket 發送指令
    API->>ROS: 5. 呼叫 RMF 任務派發介面 (Task Dispatcher)
    ROS->>ROS: 6. 交通衝突評估、避障規劃最佳軌跡
    ROS->>Robot: 7. 指派任務至目標車輛
    loop 即時狀態同步 (每 100ms)
        Robot->>ROS: 8. 回傳座標 (x, y, yaw)、電量、模式
        ROS->>API: 9. 發布 /fleet_states 主題
        API->>UI: 10. WebSocket 廣播最新車隊狀態
        UI->>User: 11. 地圖畫布動態渲染車輛平滑移動與任務進度條
    end
```

---

## 四、目錄結構與 Docker 掛載映射

專案所有程式碼皆置於宿主機，透過 Docker Compose 雙向 Volume 掛載至容器內部：

| 宿主機路徑 | 容器內掛載路徑 | 說明 |
| :--- | :--- | :--- |
| `./backend` | `/root/rmf_ws/backend` | FastAPI 後端伺服器與 ROS 2 橋接程式 |
| `./frontend` | `/root/rmf_ws/frontend` | React + Vite 前端原始碼與建置產物 (`dist/`) |
| `./workspace` | `/root/rmf_ws/src/custom_ws` | 自訂 ROS 2 節點、自製 Fleet Adapter 或地圖設定檔 |
| `/tmp/.X11-unix` | `/tmp/.X11-unix` | X11 畫面通道（支援 Gazebo 與 RViz2 視窗顯示） |

### 服務埠號 (Ports)
- **`8000`**：FastAPI 後端 API、WebSocket 伺服器與 React 前端託管入口 (`http://localhost:8000`)
- **`5173`**：Vite 開發伺服器（於宿主機執行 `npm run dev` 時使用）

---

## 五、路網持久化與 SLAM 底圖儲存機制 (Map Persistence & SLAM Map)

本系統具備完整的伺服器端與本地雙層持久化機制，編輯好的路網不會因網頁重新整理或容器重啟而遺失：

1. **路網持久化檔案 (`./backend/saved_maps/`)**：
   - **`active_nav_graph.json`**：保留完整頂點、路徑與前端介面屬性，供前端開機自動載入。
   - **`active_nav_graph.yaml`**：自動即時轉譯為標準 Open-RMF Building Map 規格（包含 `vertices`、`lanes`、`is_charger`、`is_parking_spot`、`is_workcell` 等屬性），可直接作為 Open-RMF 導航排程器與 Fleet Adapter 的輸入檔。
2. **SLAM 雷達底圖存儲 (`./backend/uploads/`)**：
   - 上傳的 `.pgm` 與 `map.yaml` 會自動轉譯為 PNG 與空間解析度參數，並與 YAML 中的 `drawing.filename` 關聯。
3. **前端自動恢復與快取機制**：
   - 當瀏覽器載入時，自動向 `GET /api/map` 請求最新儲存的路網；若離線則以 `localStorage` 雙層容災載入。
   - 點擊頂部導航列「💾 儲存路網」即可一鍵寫入硬碟並透過 WebSocket 廣播更新至所有連線用戶端。

---

## 六、前後端分離開發工作流 (Decoupled Development Workflow)

為了提供最流暢的開發體驗，系統支援前後端獨立運作的模式：

1. **後端服務 (Backend Service)**：
   - 運行於 Docker 容器內，提供 FastAPI REST API 與 WebSocket 車隊資料串流 (`http://localhost:8000`)。
2. **前端服務 (Frontend Vite Dev Server)**：
   - 運行於本機 Node.js 環境 (`http://localhost:5173`)，具備秒級熱模組替換 (HMR)。
   - 任何在 VS Code 中的程式碼修改（UI、樣式、狀態邏輯）存檔後瞬間在瀏覽器呈現，**完全不需執行 `npm run build`**。
3. **透明反向代理 (Reverse Proxy)**：
   - 透過 `frontend/vite.config.ts` 中的 `proxy` 配置，前端送往 `/api` 與 `/ws` 的所有請求會自動無縫轉發至後端 Port `8000`，解決 CORS 跨來源問題。
4. **一鍵啟動腳本**：
   - 專案根目錄已建立 `./start_dev.sh`，執行即可同時確保後端 Docker 運行並啟動前端熱重載環境。


