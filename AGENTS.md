# Open-RMF Web Studio - Agent 開發與協作規範 (AGENTS.md)

本文件定義 AI Agent 與開發者在 **Open-RMF Web Studio** 專案中協作時的開發指引、架構規範與工作流程。

---

## ⚡ 核心原則：開發模式與熱重載 (Dev Mode & Hot Reload)

> [!IMPORTANT]
> **切勿在每次修改程式碼後執行全量建置 (Build)！**
> 本專案已配置完整的前後端熱重載 (Hot Reload / HMR) 與 Docker 容器目錄掛載機制，日常開發與除錯皆在 Dev Mode 下即時生效。

1. **前端熱模組替換 (Vite HMR in Container)**：
   - 前端開發伺服器運行於 `frontend` 容器 (`node:22-alpine`，Port `5173`)。
   - `./frontend` 目錄透過 Docker Volume 掛載至容器內 `/app`，並已啟用 `usePolling` 監聽。
   - 任何在宿主機 `frontend/src/` 中的 React / TypeScript / CSS 程式碼修改，存檔後 Vite 容器會自動透過 HMR 即時反映於瀏覽器。
   - **禁止**在常規功能修改或微調後執行 `npm run build`。
   - 若需驗證 TypeScript 型別安全，可於宿主機或容器內執行輕量型別檢查即可：
     ```bash
     cd frontend && npx tsc --noEmit
     ```
2. **後端即時掛載 (Docker Volume Mount)**：
   - `./backend` 目錄已透過 Volume 雙向掛載至容器內 `/root/rmf_ws/backend`。
   - 本機修改 Python 腳本或 Controller 邏輯存檔即時反映在容器內環境。
   - **禁止**在常規代碼修改後執行 `docker compose build`。只有在 `Dockerfile` 或底層系統相依套件有結構性變更時，才需重新構建映像檔。
3. **一鍵啟動全容器化開發環境**：
   - 專案根目錄提供了一鍵啟動腳本：
     ```bash
     ./start_dev.sh
     ```
   - 此腳本會啟動 Docker 所有開發服務（FastAPI 後端 `backend`、Vite 前端 `frontend`、EMQX MQTT Broker `emqx`、MediaMTX 影像串流 `mediamtx`），宿主機無須安裝 Node.js 環境即可即時開發。
   - 若欲停止服務，可執行 `./stop_dev.sh` 或 `docker compose stop`。

---

## 🏗️ 系統架構概覽 (Architecture Overview)

本系統整合了 **Open-RMF 交通排程核心**、**FastAPI 後端橋接服務** 與 **React + Tailwind CSS 互動式 Web Studio**：

| 服務模組 | 主要技術棧 | 職責說明 |
| :--- | :--- | :--- |
| **前端 (`frontend/`)** | React 18, Vite, TypeScript, Tailwind CSS, MapLibre GL, Lucide Icons | 整合「路網編輯 (Graph Editor)」與「即時車隊監控 (Fleet Monitor)」二合一介面 |
| **後端 (`backend/`)** | Python, FastAPI, WebSocket, `rclpy` (ROS 2 Jazzy) | 提供 REST API、WebSocket 車隊遙測廣播、路網持久化檔案轉換、ROS 2 節點通訊 |
| **MQTT 與通訊** | EMQX (MQTT Broker) | 提供車載設備、感測器與 Fleet Adapter 雙向通訊匯流排 |
| **影像串流** | MediaMTX | RTSP / WebRTC 攝影機串流傳輸與轉發 |
| **核心排程與模擬** | Open-RMF (`rmf_traffic`), Gazebo Harmonic | 交通衝突評估、避障路徑規劃與 AMR 車輛模擬 |

- **詳細架構文檔**：請參閱 [docs/architecture.md](file:///home/yu/Documents/code/open_rmf/docs/architecture.md)。

---

## 📂 目錄結構與關鍵路徑 (Key Directory Structure)

```
.
├── backend/                  # FastAPI 後端伺服器與 ROS 2 橋接程式
│   ├── controller/           # 模組化 API 路由控制層 (adapters, cameras, 等)
│   ├── saved_maps/           # 路網持久化目錄 (active_nav_graph.json / .yaml)
│   ├── uploads/              # SLAM 地圖 (.pgm / map.yaml) 上傳目錄
│   ├── server.py             # FastAPI 啟動入口
│   └── state.py              # 全域狀態管理與 WebSocket 廣播
├── frontend/                 # React + Vite 前端應用
│   ├── src/
│   │   ├── components/       # UI 元件 (FleetAdapterManager, MapEditor, 等)
│   │   ├── hooks/            # 自訂 React Hooks (WebSocket, MQTT, 等)
│   │   ├── types/            # TypeScript 型別定義 (rmf.ts, 等)
│   │   ├── App.tsx           # 主頁面與路由容器
│   │   └── main.tsx          # 進入點
│   └── vite.config.ts        # Vite 反向代理配置 (/api, /ws 轉發至 8088)
├── docker-compose.yml        # Docker 多容器編排設定 (rmf, backend, frontend, emqx, mediamtx)
├── Dockerfile                # Open-RMF Jazzy 映像檔建置配置
├── start_dev.sh              # 一鍵啟動前後端分離開發環境腳本
└── docs/                     # 詳細系統架構與設計文檔
```

---

## 🛠️ Agent 開發與代碼修改準則 (Coding & Collaboration Guidelines)

1. **保持代碼風格一致**：
   - 前端採用 TypeScript 強型別、React Functional Components 與 Tailwind CSS 工具類別。
   - 後端遵循 Python 模組化架構，新 API 路由請置於 `backend/controller/` 並透過 `backend/state.py` 操作全域狀態。
2. **前後端反向代理 (Reverse Proxy)**：
   - 前端發送 `/api` 與 `/ws` 請求時，均由 Vite 配置代理自動轉發至 `http://localhost:8088`，切勿在組件中寫死 `http://localhost:8088`。
3. **路網持久化規範**：
   - 導航路網同時維護 JSON (前端屬性全量) 與 YAML (標準 Open-RMF Building Map 規格)，修改地圖資料結構時務必確保雙向相容。
4. **驗證與測試流程**：
   - **型別檢查**：若需驗證前端型別，可使用 `cd frontend && npx tsc --noEmit`。
   - **UI 畫面驗證**：專案支援 Playwright 工具（位於 `.agents/skills/playwright-cli`），可用於端對端畫面巡檢與截圖比對。
5. **Git Commit 規範**：
   - 遵循 Conventional Commits 格式，保持 commit 歷史乾淨清晰：
     - `feat(...)`: 新功能
     - `fix(...)`: 問題修復
     - `refactor(...)`: 程式碼重構
     - `docs(...)`: 文件更新
