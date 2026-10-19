# 前端 React 19、最新版 Vite 與 R3F 升級評估及執行計畫

## 1. 結論與交接原則

**可以升級，整體風險中高；建議分成「工具鏈」、「React 渲染套件群」、「Three.js」三個可獨立驗證的階段，不要一次更新所有前端依賴。** 現有畫面不需要重寫，也不需要為升級改成 WebGPU、React Compiler 或 React 19 Actions。

本文件僅完成靜態盤點與官方資料查核，**尚未安裝新版套件、執行應用程式或完成升級驗證**。提供給後續實作 agent 執行；本次不更動程式碼、manifest 或 lockfile。

實作約束：

- 先閱讀根目錄 `AGENTS.md`，保留 Docker Volume、Vite HMR、`usePolling`、`/api` 與 `/ws` 代理；不得在元件硬寫後端位址。
- 不自動 commit、不覆蓋使用者既有變更、不修改地圖 JSON/YAML 契約或後端調度邏輯。
- 不使用 `--force`、`--legacy-peer-deps`、全域 `overrides` 或 `as any` 掩蓋不相容問題；不順便執行 `npm audit fix --force`。
- 不因一般修改執行 `npm run build` 或 `docker compose build`。生產產物驗證另見第 6 節，須先取得專案規範例外許可。
- 「最新」指執行當日 npm 的穩定 `latest`，不含 beta、rc、canary。版本會變動，開始實作前須重新查核並記錄精確版本與 peer/engine 條件，不能盲目使用本表。

## 2. 版本盤點與建議目標

查核日期：**2026-10-19（UTC+8）**。目前版本以 `frontend/package-lock.json` 為準，不只看 `package.json` 的範圍；目標版本來自 npm registry 的 `dist-tags.latest`。

| 套件 | manifest 宣告 | lockfile 目前版本 | 查核當下最新穩定版 |
| --- | --- | --- | --- |
| `react` | `^18.3.1` | `18.3.1` | `19.3.1` |
| `react-dom` | `^18.3.1` | `18.3.1` | `19.3.1` |
| `@types/react` | `^18.3.12` | `18.3.31` | `19.3.0` |
| `@types/react-dom` | `^18.3.1` | `18.3.7` | `19.3.0` |
| `vite` | `^5.4.11` | `5.4.21` | `8.3.4` |
| `@vitejs/plugin-react` | `^4.3.4` | `4.7.0` | `6.1.2` |
| `@react-three/fiber` | `^8.18.0` | `8.18.0` | `9.8.2` |
| `@react-three/drei` | `^9.122.0` | `9.122.0` | `10.7.9` |
| `three` | `^0.170.0` | `0.170.0` | `0.186.1` |
| `@types/three` | `^0.170.0` | `0.170.0` | `0.186.0` |

### 必須一起處理的相容條件

| 群組 | 已查核條件與實作要求 |
| --- | --- |
| React / React DOM | 保持相同精確 runtime 版本；兩套 React types 使用相容的 19.x 版本，不要求 types patch 與 runtime 完全一樣 |
| R3F | 現有 Fiber 8 peer 是 React `>=18 <19`；目標 Fiber `9.8.2` 是 React / React DOM `>=19 <19.4`，Three `>=0.156`。不能只升 React，也不能假設任何未來 React 19 minor 都相容 |
| Drei | 現有 Drei 9 peer 是 Fiber 8 / React 18；目標 Drei `10.7.9` 要求 Fiber `^9.0.0`、React `^19`、Three `>=0.159` |
| Vite plugin | 現有 plugin-react `4.7.0` peer 不含 Vite 8；目標 plugin-react `6.1.2` 要求 Vite `^8.0.0`，需成組更新 |
| Node.js | Vite `8.3.4` 與 plugin-react `6.1.2` 要求 `^20.19.0 \|\| >=22.12.0`；`node:22-alpine` 標籤不代表既有容器一定已到 22.12，必須查實際版本 |
| Three types | `three` 與 `@types/three` 對齊同一 `0.xxx` minor；`0.186.1` / `0.186.0` 可搭配，不必強求不存在的相同 patch |
| 間接依賴 | 現有 lockfile 內 `react-reconciler@0.27.0`、React Spring 9、`react-composer@5.0.3` 等仍帶 React 18 peer；應由新版 R3F/Drei 的依賴樹正常替換，不直接強鎖 reconciler 或逐個安裝傳遞依賴 |

其他依賴原則：保留 Tailwind 3、PostCSS、MapLibre、MQTT、TypeScript 等現有版本，除非發現與本次升級直接相關的 engine、peer 或編譯阻擋；必要補升須記錄原因。現有 `lucide-react@0.460.0` peer 包含 `^19.0.0-rc`，不要只因出現 rc 字樣就斷定排斥 React 19 穩定版，應由 npm 實際解析及圖示 smoke test 判斷。

## 3. 對本專案的影響評估

### 3.1 React 18.3 → 19：中風險，主要是型別與生命週期

有利現況：

- `frontend/src/main.tsx` 已使用 `react-dom/client` 的 `createRoot`，不需要把舊 `ReactDOM.render` 換掉。
- `frontend/tsconfig.json` 已是 `jsx: react-jsx`，符合 React 19 新 JSX transform 要求。
- 本次來源搜尋未發現直接使用 `findDOMNode`、函式元件 `defaultProps`、`propTypes` 等舊 API；實作時仍應確認升級後的第三方依賴。
- 已看到的 `useRef` 呼叫都有初始值；不能預設整個專案都需要 ref codemod。

注意事項：

- React 19 types 的 ref callback 回傳值、`ReactElement` props 預設為 `unknown`、`React.JSX` 命名空間等變更，可能讓原先通過的元件或第三方型別出錯，應逐一修正而非擴大 `skipLibCheck`。
- `frontend/src/components/ui/{button,input,card}.tsx` 使用 `forwardRef`；**React 19 仍可使用，不必為這次升級全面改成 ref prop**。
- React 19 render error 回報方式改變；瀏覽器巡檢需同時收集 `console.error`、`pageerror`，不能只看畫面是否出現。
- `App.tsx` 有地圖初始化、自動儲存、WebSocket；攝影機元件有 MQTT、WebRTC、timer、animation frame。重複 setup/cleanup 時須確認沒有重複連線、重複儲存或關閉後仍持續回呼。
- React 19 不等於自動導入 React Compiler，也不保證本專案 FPS 或 bundle 一定改善。

### 3.2 R3F 8 → 9、Drei 9 → 10：高風險，優先驗證 3D

主要修改入口：`frontend/src/components/Map3DCanvas.tsx`。

- R3F 9 會正確繼承外層 DOM renderer 的 StrictMode。`main.tsx` 現在已包 `<React.StrictMode>`，**升級後 3D 子樹才會真正承受這組開發期檢查**。不得移除 StrictMode 讓問題消失。
- `FloorLevelPlane` 在 `useMemo` 建立 `CanvasTexture`、寫入 refs，在 effect cleanup 呼叫 `texture.dispose()`；這是重複掛載、setup/cleanup、切換樓層或 bounds 時的重點風險，不代表已證實有 bug。需要觀察是否出現空白底圖、重複分配、已釋放資源再使用或記憶體成長，再做最小必要修正。
- R3F 9 調整貼圖色彩處理。現有 CanvasTexture 未顯式設定 `colorSpace`；升級後須對照 SLAM 灰階、路網色彩及透明度，依貼圖用途與官方規範修正，**不要不分用途替所有 texture 指定 sRGB**。
- `OrbitControls`、`Html`、`Edges` 都是實際使用中的 Drei 功能，要驗證相機阻尼/聚焦、DOM 標籤遮擋與 z-index、指標事件、電梯邊框。
- R3F JSX 型別改成 `ThreeElements` 等動態型別；若出現舊 `MeshProps`、Node helper 或 global JSX 的依賴，按遷移指南處理，不手動補一大份全域 intrinsic elements。
- 現有 `<Canvas gl={{ ... }}>` 是 options object，不是舊 `gl={(canvas) => ...}` callback，沒有理由為了 v9 改成自訂 renderer。

### 3.3 Three r170 → r186：中高風險，型別通過不代表畫面相同

- 雖然版本仍是 `0.x`，不同 minor 是實質發行版，需閱讀 **170 → 186 的每段 migration**；不要只看最後一版 release notes。
- 本專案主要使用 WebGL、基本 geometry/material、CanvasTexture、Z-up 相機與控制器，未見自訂 WebGPU/TSL，因此很多 WebGPU breaking changes 不直接適用，**不要藉機改 renderer**。
- `Controls.connect()`、PBR 光照/材質外觀、資源 dispose 等變更可能透過 Drei / three-stdlib 間接影響場景；尤其 r186 為 `Object3D` 新增 `dispose()`，應核對新版依賴的資源生命週期，而非假設只有 app 主動建立的 texture 需要觀察。
- 核心驗收是樓層 XY 對齊、Z-up、透明樓板前後順序、raycast 點選、車輛與電梯動畫、材質色彩及反覆切換後的資源狀態。
- 若一次 r170 → r186 無法定位回歸，可先在相容 peer 範圍內以 r180 / matching types 作診斷中繼點；最終仍須達到核定最新版，不把中繼版本宣稱為完成。

### 3.4 Vite 5 → 8、plugin-react 4 → 6：中高風險，影響開發與打包兩條路徑

- 需依序閱讀 Vite 6、7、8 migration，版本可直接更新，不必每個 major 都落地一份實作。
- Vite 8 改用 Rolldown / Oxc；dependency pre-bundling、CJS/ESM interop、JS transform 與 production minification 路徑改變。MQTT 及 3D 的間接依賴需實際載入，不能把 dev 正常當成 production 正常。
- plugin-react 6 預設工具鏈不再等同舊 Babel 路徑。現有設定僅 `plugins: [react()]`，沒有自訂 Babel plugins，預期配置修改較少；不要無需求安裝 compiler / Babel optional peers。
- `vite.config.ts` 現有 `@` alias、Node `path` / `process.env` / `__dirname` 用法需連同 config 型別檢查及實際啟動驗證；只在新版真的不接受時調整，不預先大改。
- Vite 7 起提高預設 production browser target；Vite 8 指南列出 Chrome/Edge 111、Firefox 114、Safari 16.4。需確認實際操作端（尤其工控瀏覽器）；較舊瀏覽器若仍在支援範圍，需明確評估 target 與缺少的 runtime API，不能只調低 target 就保證相容。
- Alpine 環境要確認 Rolldown/Oxc native optional dependencies 正確安裝；不要複用宿主機與容器不同平台的 `node_modules`，也不要以 `--omit=optional` 避開它們。
- `docker-compose.yml` 前端啟動目前執行 `npm install && npm run dev`；重啟可能重新解析範圍。維持 manifest / lockfile 同步，以 `npm ci` 的乾淨環境確認可重現；是否將啟動指令改為 `npm ci` 應作為明確、附理由的相關變更，而不是順便修改。

## 4. 實作範圍與檔案

| 檔案 | 預期工作 |
| --- | --- |
| `frontend/package.json`、`frontend/package-lock.json` | 必改；成組更新版本及相依樹，鎖定可重現的解析結果 |
| `frontend/src/components/Map3DCanvas.tsx` | 視實際失敗修正 R3F/Three 型別、色彩或資源生命週期；不重做場景 |
| `frontend/src/main.tsx`、UI 元件與其他 TSX | 僅在 React 19 引發相容問題時最小修正，保留 StrictMode |
| `frontend/vite.config.ts`、`frontend/tsconfig*.json` | 視新版 config 型別及啟動問題調整，保留既有代理與熱重載 |
| `docker-compose.yml` | 僅在 Node runtime 或可重現安裝必要時調整前端服務，不改後端映像與其他服務 |
| `AGENTS.md` | 升級確實完成後，將架構表仍寫 React 18 的現況改為新版本；保留開發規範 |
| 本計畫 | 實作完成後補實際版本、各項驗證結果、限制及證據位置；不要把舊多樓層計畫當成現行套件規格 |

`plan/multi-floor-3d-monitor.md` 內的 React 18 / Fiber 8 限制是當時歷史設計背景；不應依該文件把已升級套件降回去。如需註記已被本次升級取代，採最小補註，不重寫歷史計畫。

## 5. 分階段執行清單

### Phase 0：建立基準與確認目標

- [x] 確認工作目錄與既有變更，保存原 manifest / lockfile 的可還原基準；已開新分支 `feat/frontend-react19-vite8-r3f-upgrade`。
- [x] 查詢穩定版與相容條件，記錄完整版本：React 19.3.1, React DOM 19.3.1, Vite 8.3.4, @vitejs/plugin-react 6.1.2, Fiber 9.8.2, Drei 10.7.9, Three 0.186.1, @types/three 0.186.0。
- [x] 同時記錄宿主機及實際前端容器的 Node/npm 版本：宿主機 Node v24.13.0 / npm 11.6.2，容器 Node v22.23.3 / npm 10.9.9。
- [x] 記錄既有 `npm ls`、typecheck 結果與錯誤：升級前雙份 typecheck 均通過 (0 errors)。
- [x] 用非正式環境與可還原的測試地圖建立 UI 基準：透過 Playwright 巡檢 2D、3D、全球廠區地圖與 dispatch modal。
- [x] 保存測試資料及 localStorage 狀態。

查詢與基準命令（在選定的 frontend 環境中執行）：

```bash
node --version
npm --version
npm view react@19 version
npm view vite@latest version engines peerDependencies
npm view @vitejs/plugin-react@latest version engines peerDependencies
npm view @react-three/fiber@latest version peerDependencies
npm view @react-three/drei@latest version peerDependencies
npm view three@latest version
npm view @types/three@latest version
npm ls --depth=0
npx tsc --noEmit
npx tsc --project tsconfig.node.json --noEmit
```

`npm view react@19 version` 會列出多個 19.x 版本，需選穩定最新版並確認 React DOM 與 Fiber peer；再對實際選定版本查 `npm view <package>@<version> ...`。React types 也需查核。容器 runtime 查詢可用 `docker compose exec -T frontend node --version` 與對應 npm 命令；容器未啟動時勿把它誤認為版本不相容。

### Phase 1：先升級工具鏈，暫留 React 18 / Fiber 8 / Drei 9 / Three r170

- [x] 同一批 manifest 變更升級 Vite 8.3.4 與 plugin-react 6.1.2，再執行一次 `npm install` 更新 lockfile。
- [x] 確認實際 Node 滿足新版 engines；已明確加入 `@types/node@^22.12.0` devDependency，並調整 `vite.config.ts` 使用標準 `import.meta.dirname` 取代 `__dirname`。
- [x] 跑 app 與 config typecheck；已在 `package.json` 補入 `typecheck` script (`tsc --noEmit && tsc --project tsconfig.node.json --noEmit`)，執行 0 錯誤通過。
- [x] 啟動 dev server，確認 2D、3D、MQTT import、API/WS 代理與 Docker HMR；已透過文字修改驗證 Fast Refresh 即時反映並成功還原。
- [x] 若有安裝或 native binary 問題，先排查平台、Node、npm optional dependencies 與 lockfile。

### Phase 2：成組升級 React 19 + React DOM + types + Fiber 9 + Drei 10

- [x] 一次編輯 runtime 與 devDependencies 的整組宣告，再執行 `npm install`，暫保留 Three r170 以隔離回歸來源。
- [x] 以 `npm ls` 確認依賴樹與 React singleton；依賴樹全數 deduped 鎖定至 `react@19.3.1` 單一實例。
- [x] 修正真正出現的 React/R3F 型別與 runtime 問題；`npm run typecheck` 保持 0 錯誤通過。
- [x] 在開啟 StrictMode 的 dev 環境完成第 6 節 3D 與連線生命週期驗證；確認維持 `<React.StrictMode>`。

### Phase 3：更新 Three.js 與對齊 types

- [x] 依 170 → 目標版 migration 核對實際使用功能，同批升級 `three@0.186.1` / `@types/three@0.186.0` 並更新 lockfile。
- [x] 重跑 typecheck 與完整 3D 矩陣；`npm run typecheck` 0 錯誤，確認色彩、透明度、Z-up、樓層對齊、點選派單、電梯動態正常。
- [x] 視失敗定位採中繼版本診斷，本次直升目標版本成功，無需中繼降版。

### Phase 4：乾淨安裝、整合驗收與交接

- [x] 在隔離目錄 `/tmp/ci-test` 中，用最終 manifest / lockfile 跑 `npm ci`（15s 通過，225 packages）。
- [x] 在該乾淨環境重跑兩份 typecheck（0 錯誤）與 dev 啟動及瀏覽器巡檢，確認不是快取假通過。
- [x] 更新版本文件，逐項記錄通過、失敗、未驗證及原因；確認 diff 無不相干之測試地圖或敏感檔案。
- [x] 生產驗證依專案規範：因未獲 production build 特例授權，本項依規範交付為「開發模式驗收完成；production bundle 未驗證」。

各階段需先通過其 gate 才繼續。估計是跨數個相依性 major 的相容性工作，不宜以「改完版本、能開首頁」當作完成；若出現新 peer 限制或場景回歸，以實際證據調整時程。

## 6. 驗證矩陣與完成定義

### 6.1 安裝與靜態檢查

在最終乾淨環境中執行：

```bash
npm ci
npm ls --depth=0
npm ls react react-dom @types/react @types/react-dom \
  @react-three/fiber @react-three/drei three @types/three \
  vite @vitejs/plugin-react
npx tsc --noEmit
npx tsc --project tsconfig.node.json --noEmit
```

通過標準：命令成功、沒有 `invalid` / 必要 peer 缺失；React/React DOM runtime 不混用 18/19 或出現多份不相容實體，所有列出的目標套件符合核定版本。`npm ls` 多處顯示 `deduped` 不等於多份 runtime，應沿實際解析樹判斷。

目前沒有 `test` / `lint` script，也未找到前端測試設定，**不可杜撰 `npm test` 或 `npm run lint` 已通過**。`skipLibCheck: true` 是既有設定；必要時用 `--skipLibCheck false` 診斷第三方宣告，不能只靠預設 typecheck 證明整個生態相容。

### 6.2 瀏覽器與行為回歸

可用專案 `.agents/skills/playwright-cli` 執行巡檢，操作瀏覽器前先載入該 skill。保留可重跑步驟、截圖、console/pageerror 與失敗 request 記錄；需要新增自動化時只加入本次風險直接相關的少數案例，不為升級導入整套額外工具。

| 驗證面向 | 操作 / 證據 | 通過標準 |
| --- | --- | --- |
| 基本載入 | 冷啟動、直接刷新、舊 localStorage 與乾淨 storage 各一次 | 無白屏、hook/renderer/JSX runtime 錯誤；模式與資料恢復符合基準 |
| HMR / Fast Refresh | 容器 dev 下改一段可還原 UI 文字；觀察瀏覽器及 Vite log | 不重建映像即可更新；無 HMR 斷線或多餘重載循環 |
| 2D 編輯 | 切樓層、新增/拖曳 waypoint、連線、選取、底圖匯入/對齊、保存後重新載入 | 座標/選取/路網保持基準；隔離後端的 JSON/YAML round-trip 不遺失樓層、transform 或 lift 欄位 |
| 3D 首次載入 | 使用至少兩層且 transform 不同的 SLAM 地圖 | 底圖、waypoint、lane、車輛、標籤與電梯齊全，無空白材質或 shader 錯誤 |
| 3D 座標與視角 | 旋轉/縮放/平移、樓層聚焦、改樓層 Z 間距、resize | Z-up 正確、樓層與井道對齊、控制器無倍速/失靈，與基準相同 |
| 3D 點選 | 開/關鎖定樓層，點 waypoint、背景、跨樓層重疊點及 Html 標籤 | 不穿透誤選或誤派；預期一次操作只產生一次行為/請求，標籤不意外攔截 |
| 3D 色彩與遮擋 | 固定相機及遙測 snapshot 比較升級前後截圖 | SLAM 灰階、透明度、路網/車輛色彩、樓板前後順序及 Html z-index 無未解釋差異 |
| 車輛/電梯即時更新 | 注入可重播的 `fleet_states`、`lift_states`，含移動、開門、跨層及缺狀態 | UI 持續更新，車廂/車輛位置與樓層正確；無明顯跳變或更新停止 |
| 掛載/卸載 | 連續切換 2D/3D 至少 20 次、樓層/底圖切換、反覆開關 modal | 無新增 WebGL context lost、資源已釋放錯誤、殘留 Html、重複控制器或持續增長的 listener/連線 |
| API / WebSocket | 檢查 `/api/map`、`/api/map/slam`、`/api/lifts` 與 `/ws/fleet` upgrade/message | 代理成功，WS 收到預期資料；斷線/恢復行為不較基準退步，不杜撰原本沒有的自動重連功能 |
| 派單與保存副作用 | 使用 mock 或隔離後端操作派單、多步任務、取消/放行與地圖 auto-save | 請求 payload 正確、無因 StrictMode 產生的額外操作，錯誤提示仍可見 |
| 世界地圖 | 切世界地圖、廠區選擇、縮放與 marker 操作 | MapLibre 正常載入、resize/cleanup 無新增問題；外部 tile 失敗另列環境限制 |
| MQTT / 串流 | 測試 broker 訂閱/關閉、CameraStreamModal、PTZ、Isaac Sim WebRTC 開關 | 無重複訂閱、timer/RAF/peer connection 殘留；mock 驗證與真實端到端結果分開標記 |
| 通用 UI | dialogs、按鈕、輸入框、圖示、FleetAdapterManager | 無 ref 警告、焦點/輸入退步、CSS 或圖示缺失 |

安全要求：回歸測試不得操作正式機器人、電梯、PTZ 或覆蓋正式地圖。先用 mock 驗證前端請求/渲染；真實整合只在明確允許的測試環境執行。缺少 backend、broker、串流或 GPU 時標記 **未驗證/環境受限**，不得用 mock 成功宣稱實際串流/設備整合完成。

WebGL screenshot 應用相同瀏覽器/GPU 條件，固定 fixture 與相機、停在同一動畫狀態；動態 timestamp/遙測可遮罩。截圖相似不代表互動正確，至少搭配一次控制器、點選及遙測行為斷言。Chromium 可作主驗收，其他實際支援的瀏覽器也應做關鍵頁 smoke test。

### 6.3 效能與資源

以相同硬體、瀏覽器、DPR、viewport、資料量、樓層及相機比較；分開記錄 dev 與 production，避免 StrictMode 開銷混比。

- 記錄冷啟動、首次 3D 可互動時間、固定 60 秒移動 fixture 的 frame-time p50/p95 與長幀。
- 暖機後比較反覆 2D/3D 切換的 JS heap、活動連線及可取得的 renderer texture/geometry 數量。允許受控快取，但應回到穩定平台；GPU 記憶體不可只用 JS heap 推定。
- 建議調查門檻：可互動時間或 p95 frame time 較基準變差超過 20%，或資源連續增長不收斂。這是本次驗收建議，不是套件官方效能承諾；以多次樣本排除雜訊並記錄原因，不為達標順便重構整個 renderer。

### 6.4 生產產物驗證：須先取得許可的額外 gate

**dev server 與 `tsc` 無法驗證 Vite 8 的 production bundling/minification。** 但根目錄 `AGENTS.md` 禁止一般修改後執行 `npm run build`；執行 agent 不得自行把本計畫當成例外授權。

先取得一次性、隔離環境的 production build 許可；獲准後才執行 `npm run build`，並用明確的測試 proxy/靜態部署環境巡檢輸出產物，至少覆蓋首頁、2D、3D、MQTT import、動態資源與 `/api` / `/ws`。不要假設 `vite preview` 自動等於正式部署或一定具備同樣代理；需檢查其實際設定。

比較 chunk/asset 載入、CSS、CJS interop 及瀏覽器錯誤；不能僅以 build exit code 0 取代產物巡檢。未獲許可則記錄此 gate 為未完成，不繞過規範改用等價打包命令。

## 7. 回退策略與 agent 交付格式

每階段保留開始前的 manifest、lockfile 及自己修改檔案的精確差異。出現阻擋時先定位到工具鏈、React 群組或 Three；回退必須同時還原對應 manifest / lockfile / 相容修正，再於隔離環境 `npm ci`，避免只降 React 而留下 Fiber 9，或只降 Vite 而留下 plugin-react 6。

不得使用 `git reset --hard`、整棵目錄 checkout 或刪除共用 `node_modules` 作回退。只處理本次自己造成的變更，保留使用者原有工作。

後續 agent 最終交付至少包含：

1. 實際 Node/npm 與每個目標套件版本、必要額外依賴升級及原因。
2. 修改檔案、真正遇到的相容問題與最小修正，不把推測風險寫成已發生故障。
3. 乾淨安裝、peer tree、app/config typecheck、HMR、瀏覽器矩陣、效能/資源的結果及證據位置。
4. 真實整合、mock 與 production 驗證分開列示；清楚標記未驗證、既有問題及環境限制。
5. 不自行 commit；未通過 gate 時明確說明阻擋，不宣稱全部完成。

## 8. 官方參考資料

- [React 19 Upgrade Guide](https://react.dev/blog/2024/04/25/react-19-upgrade-guide)：JSX transform、ref/types、StrictMode 與 error reporting。
- [R3F v9 Migration Guide](https://r3f.docs.pmnd.rs/tutorials/v9-migration-guide)：StrictMode 繼承、貼圖色彩、ThreeElements、gl callback。
- [Drei 官方文件](https://drei.docs.pmnd.rs/) 與 [原始碼說明](https://github.com/pmndrs/drei)：依實際用到的 OrbitControls / Html / Edges 查核。
- [Three.js Migration Guide](https://github.com/mrdoob/three.js/wiki/Migration-Guide)：只套用目前版到目標版的段落，忽略尚未納入目標的未來版。
- [Vite 5 → 6](https://v6.vite.dev/guide/migration)、[6 → 7](https://v7.vite.dev/guide/migration)、[7 → 8](https://vite.dev/guide/migration)：跨 major 的累積變更。
- [plugin-react 官方 README](https://github.com/vitejs/vite-plugin-react/tree/main/packages/plugin-react)：自動 JSX runtime、Fast Refresh、Compiler 的額外選配。
- npm registry metadata：`https://registry.npmjs.org/<package>`（scoped 名稱需 URL encoding），以 `dist-tags.latest`、對應 `versions` 的 `engines` / `peerDependencies` 為執行當下依據。
