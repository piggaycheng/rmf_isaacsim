# AMR 車載端與 Open-RMF Fleet Adapter (FA) MQTT 通訊介面規格書

- **文件版本**：v1.0.0
- **修訂日期**：2026-10-07
- **適用對象**：AMR 車載軟體研發人員、車隊管理系統 (FMS) 開發團隊、系統整合工程師
- **目標系統**：Open-RMF Web Studio (ROS 2 Jazzy + EMQX MQTT Broker)

---

## 1. 概述 (Overview)

本規格書定義自主移動機器人（Autonomous Mobile Robot, 簡稱 **AMR**）與 Open-RMF **Fleet Adapter（車隊轉接器，簡稱 FA）** 之間的標準通訊介面。

本系統採用 **MQTT 3.1.1 / 5.0** 作為雙向通訊協定，具備以下架構特點：
1. **註冊與心跳分離 (Separation of Registration & Heartbeat)**：
   - **開機註冊 (`register`)**：包含車輛尺寸、動力性能、充電樁指派等靜態參數（低頻率、只在開機或重連時發送一次）。
   - **常態心跳 (`heartbeat`)**：包含即時座標、朝向角、電量與運作狀態（高頻率、極簡輕量 JSON 封包）。
2. **具備雙向握手機制 (Handshake)**：車輛於開機後需取得 FA 回覆的 `register_ack` 確認，方可進入可派遣（Online）狀態。
3. **優雅離線與意外斷網守護 (Graceful Deregister & LWT)**：支援正常關機註銷，並利用 MQTT **Last Will and Testament (LWT 遺囑訊息)** 機制防止車輛非預期斷電或失聯造成排程死鎖。

---

## 2. MQTT 連線規範與環境設定

### 2.1 Broker 連線參數
- **預設 Host**：`127.0.0.1`（本機 Docker 服務）或現場配置之 EMQX 伺服器 IP
- **預設連接埠 (Port)**：
  - `1883`：標準 MQTT TCP（推薦車載端使用）
  - `8883`：MQTT over TLS/SSL（安全傳輸）
  - `8083` / `8084`：MQTT over WebSocket / WSS
- **Client ID 命名規範**：
  ```text
  amr_{fleet_name}_{robot_id}
  ```
  *(範例：`amr_tinyRobot_tinyRobot1`，請確保同一個 Client ID 在整個 Broker 中唯一)*
- **Keep Alive 間隔**：建議設置為 `15` ~ `30` 秒。
- **Clean Session**：建議設置為 `True`。

### 2.2 座標系與單位標準 (ROS 2 / Open-RMF 標準)
車載端所有數值回報與指令解析均須嚴格遵循以下物理單位：

| 物理量 | 單位 | 符號 / 格式 | 說明 |
| :--- | :--- | :--- | :--- |
| **位置座標 $(x, y)$** | 公尺 (meters) | `float` (例如: `2.45`) | 參考 2D SLAM 地圖原點之右手直角座標系 |
| **朝向角度 $(\theta / yaw)$** | 弧度 (radians) | `float` (例如: `1.5708`) | 範圍 $[-\pi, +\pi]$，以 X 軸正向為 0，逆時針為正 |
| **線速度 $(v)$** | 公尺/秒 (m/s) | `float` (例如: `1.0`) | 車體正向移動速度 |
| **角速度 $(\omega)$** | 弧度/秒 (rad/s) | `float` (例如: `0.8`) | 車體旋轉角速度 |
| **電池電量 (Battery)** | 百分比 (%) | `float` (`0.0` ~ `100.0`) | 例如 `85.5` 代表 85.5% |
| **時間戳記 (Timestamp)** | 秒 (seconds) | `float` (Unix Epoch) | 秒級浮點數 (精確至毫秒，例如: `1728312000.123`) |

---

## 3. MQTT 主題總覽 (Topic Catalog)

主題採用階層式命名：`rmf/{fleet_name}/robot/{robot_id}/{action}`

| 主題名稱 (Topic) | 傳輸方向 | 推薦 QoS | 觸發頻率 | 職責簡述 |
| :--- | :---: | :---: | :---: | :--- |
| `rmf/{fleet_name}/robot/{robot_id}/register` | 車端 $\rightarrow$ FA | **1** | 開機發送 (未獲 ACK 則每 3 秒自動重試) | 提交車體規格並申請註冊進 Open-RMF |
| `rmf/{fleet_name}/robot/{robot_id}/register_ack` | FA $\rightarrow$ 車端 | **1** | 回應註冊請求時 | FA 確認接受註冊並回傳系統圖資配置 |
| `rmf/{fleet_name}/robot/{robot_id}/heartbeat` | 車端 $\rightarrow$ FA | **0** | 週期性 (1 ~ 5 Hz) | 回報即時位置、電量與當前運動狀態 |
| `rmf/{fleet_name}/robot/{robot_id}/command` | FA $\rightarrow$ 車端 | **1** | 事件觸發 (任務派遣) | RMF 核心派發之導航目標點或停止指令 |
| `rmf/{fleet_name}/robot/{robot_id}/command_result` | 車端 $\rightarrow$ FA | **1** | 動作完成或異常時 | 車端回報指定 `cmd_id` 之執行結果 |
| `rmf/{fleet_name}/robot/{robot_id}/deregister` | 車端 $\rightarrow$ FA | **1** | 正常關機或維護時 | 主動註銷並釋放預約路權與站點 |
| `rmf/{fleet_name}/robot/{robot_id}/status` | 車端 $\rightarrow$ FA | **1** | 斷線時 (LWT 遺囑) | 遺囑訊息：非預期失聯時由 Broker 代發 |
| `rmf/{fleet_name}/control` | 後端 $\rightarrow$ FA | **1** | 車隊管理控制 (暫停/恢復) | 控制指定車隊暫停或恢復可派遣狀態 |

---

## 4. 詳細通訊封包規格 (Payload Specifications)

### 4.1 開機註冊請求 (`register`)
- **Topic**：`rmf/{fleet_name}/robot/{robot_id}/register`
- **方向**：車載端 $\rightarrow$ Fleet Adapter
- **QoS**：`1`
- **觸發與發送時機**：
  1. **開機首次發送**：車輛系統啟動完成且定位成功後立即發送。
  2. **3 秒自動重試機制（晚開機應對）**：發送後**若超過 3 秒未收到 `register_ack`**（例如 Fleet Adapter 較晚啟動或重啟中），車端**必須以 3 秒為間隔持續自動重發**，直到收到 `status: "success"` 的 ACK 為止。
  3. **逆向要求重註冊**：若車輛運行中收到 `register_ack` 帶有 `status: "require_register"`（代表 FA 剛重啟遺失了名冊），車端**必須立即重新發送**此註冊封包。
  4. **MQTT 重連沿用 session**：同一車端程序的網路重連沿用相同 `session_id`，恢復 heartbeat 即可；若 FA 要求補註冊，必須使用原 ID。重複 `register` 不會取消同一 session 的活動。
  5. **重新開機產生新 session**：每次車端控制程序啟動時以 `str(uuid.uuid4())` 產生 ID，僅存在該次執行的記憶體中；不可在 MQTT `on_connect` 中重新產生。Isaac Sim 若重置模擬、位置或命令執行狀態，即使 Python 程序未退出，也必須開始新 session。
- **說明**：告知 FA 該車輛的物理幾何形狀與預設站點。未完成註冊前，車輛不可切換至 `idle` 亦不可接受調度指令。

#### JSON Schema 與欄位定義
```json
{
  "robot_id": "tinyRobot1",
  "fleet_name": "tinyRobot",
  "session_id": "6e479742-f91a-4cb7-9648-7109abc089cf",
  "timestamp": 1728312000.123,
  "initial_location": {
    "x": 0.7,
    "y": 0.9,
    "yaw": 0.0,
    "level_name": "L1",
    "waypoint_name": "wp_1"
  },
  "specs": {
    "footprint_radius": 0.35,
    "max_linear_velocity": 1.2,
    "max_angular_velocity": 1.0
  },
  "default_charger": "charger_1",
  "default_parking": "parking_1"
}
```

| 欄位名稱 | 型別 | 必填 | 說明 |
| :--- | :---: | :---: | :--- |
| `robot_id` | String | **是** | 車輛唯一識別碼 (需與 Client ID 相符) |
| `fleet_name` | String | **是** | 所屬車隊名稱 (需與 FA 管理之車隊名稱相符) |
| `session_id` | String | **是（新版車端）** | 每次開機／模擬重置重新產生的 UUID；重連、註冊重試及 FA 重啟後補註冊時不得更換，長度上限 128 |
| `timestamp` | Float | **是** | 發送時之 Unix 時間戳記 (秒) |
| `initial_location.x` | Float | **是** | 開機完成時的定位 X 座標 (m) |
| `initial_location.y` | Float | **是** | 開機完成時的定位 Y 座標 (m) |
| `initial_location.yaw` | Float | **是** | 開機完成時的車頭朝向角 (rad) |
| `initial_location.level_name` | String | 否 | 所在樓層地圖，預設 `"L1"` |
| `initial_location.waypoint_name` | String | 否 | 若停在特定站點，填寫站點名稱 (例: `"parking_1"`) |
| `specs.footprint_radius` | Float | 否 | 車體迴轉安全半徑 (m)，預設使用 FA 配置 |
| `specs.max_linear_velocity` | Float | 否 | 車輛最大線速度 (m/s) |
| `specs.max_angular_velocity`| Float | 否 | 車輛最大角速度 (rad/s) |
| `default_charger` | String | 否 | 該車指定充電樁名稱 (若無則使用車隊預設) |
| `default_parking` | String | 否 | 該車指定待命停泊點名稱 |

#### Session 切換與舊工作處理

FA 將最後接受的 session 與已淘汰的 ID 原子寫入 `backend/saved_maps/robot_sessions.json`，
因此 FA 重啟後仍能辨識相同 session 與車端重新開機。重複／延遲的舊 ID 註冊會被拒絕。

同 ID 重連不取消任務；新 ID 則禁止下發導航、清除舊 command／deferred command／多步任務定時器，
透過 RMF Task API 取消該車待執行的直接與派遣任務、終止執行中的任務，並取消舊 idle behavior。
未完成的舊工作會標記取消或中止，不會當作成功完成。其他車輛的工作不受影響。
FA 確認清理完成，且收到相同 session 的新 heartbeat（`localization_ready: true`、
有限座標、已知樓層、路網範圍內、`status: idle/charging`、`current_cmd_id: null`）後，
才依原設定恢復接單及 responsive wait。清理失敗會保持禁止下發，並回報 `session_reset_error`；
排除錯誤後可用相同 ID 重新註冊重試。這不是開機後永久原地鎖定：新的 responsive wait 仍可能移往附近等待點或讓路。

ACK 與所有下行 command 都包含對應 `session_id`；車端必須忽略不同 ID 的 ACK／命令，
避免 MQTT 延遲封包跨 session 執行。車端所有上行封包（heartbeat、command_result、task_state、
deregister、status/LWT）都必須攜帶相同 ID，FA 會忽略舊 session 或缺少 ID 的封包。
`localization_ready` 必須來自真正的定位就緒狀態，不得只因 MQTT 已連線就設為 true。

尚未帶 `session_id` 的舊車端保留舊協定行為並記錄警告，**不具備上述開機隔離保證**；
一旦某車採用新版 ID，之後缺少 ID 的註冊與上行封包將被拒絕。Repo 內的 `mock_robot.py`
已示範新協定；外部 Isaac Sim client 必須同步更新，不能只更新 FA。

---

### 4.2 註冊應答 (`register_ack`)
- **Topic**：`rmf/{fleet_name}/robot/{robot_id}/register_ack`
- **方向**：Fleet Adapter $\rightarrow$ 車載端
- **QoS**：`1`
- **說明**：FA 收到 `register` 並在 Open-RMF 建立 `RobotUpdateHandle` 成功後回覆。車載端必須收到此訊息才能開始發送日常心跳並接受指令。

#### 成功範例 (Success)
```json
{
  "robot_id": "tinyRobot1",
  "session_id": "6e479742-f91a-4cb7-9648-7109abc089cf",
  "status": "success",
  "message": "Robot registered to Open-RMF scheduler successfully",
  "assigned_graph_idx": 0,
  "server_time": 1728312000.456
}
```

#### 拒絕範例 (Rejected)
```json
{
  "robot_id": "tinyRobot1",
  "status": "error",
  "session_id": "6e479742-f91a-4cb7-9648-7109abc089cf",
  "error_code": "GRAPH_OUT_OF_BOUNDS",
  "message": "Initial location is too far from any valid waypoint in nav graph 0",
  "server_time": 1728312000.456
}
```

#### 晚啟動/重啟要求補註冊範例 (Require Registration)
```json
{
  "robot_id": "tinyRobot1",
  "status": "require_register",
  "session_id": "6e479742-f91a-4cb7-9648-7109abc089cf",
  "message": "Robot not recognized by FA, please re-register immediately",
  "server_time": 1728312000.456
}
```
> **重要自癒機制**：若 FA 比車輛晚開機、或 FA 曾中途重啟，當 FA 收到未知心跳時會主動發送此訊息。車載端收到 `status: "require_register"` 時，**必須立即自動補發 `/register`**。

---

### 4.3 週期心跳與遙測 (`heartbeat`)
- **Topic**：`rmf/{fleet_name}/robot/{robot_id}/heartbeat`
- **方向**：車載端 $\rightarrow$ Fleet Adapter
- **QoS**：`0`
- **建議發送頻率**：`2 Hz` ~ `5 Hz` (每 200ms ~ 500ms 一次)
- **說明**：包含車輛識別碼與 Open-RMF 交通調度與避障所需的座標、狀態及 session 欄位。自包含 `robot_id` 有助於日誌追蹤與封包除錯。

#### 核心標準 JSON 封包範例
```json
{
  "robot_id": "tinyRobot1",
  "session_id": "6e479742-f91a-4cb7-9648-7109abc089cf",
  "localization_ready": true,
  "x": 2.345,
  "y": 1.120,
  "yaw": 0.785,
  "battery": 88.5,
  "status": "moving",
  "current_cmd_id": 101
}
```

| 欄位名稱 | 型別 | 必填 | 單位 / 範圍 | 說明與 Open-RMF 對應 |
| :--- | :---: | :---: | :---: | :--- |
| **`robot_id`** | String | **是** | 字串 (例: `"tinyRobot1"`) | 車輛唯一識別碼 (需與註冊及 Topic 一致) |
| **`session_id`** | String | **新版必填** | 與 register 相同 | 車端本次執行識別碼 |
| **`localization_ready`** | Boolean | **新版必填** | true / false | 定位與樓層已確認就緒；新 session 須收到 true 才能恢復調度 |
| **`x`** | Float | **是** | 公尺 (m) | 當前 2D SLAM 世界座標 X $\rightarrow$ `RobotState.position[0]` |
| **`y`** | Float | **是** | 公尺 (m) | 當前 2D SLAM 世界座標 Y $\rightarrow$ `RobotState.position[1]` |
| **`yaw`** | Float | **是** | 弧度 (rad, $[-\pi, \pi]$) | 車頭偏航角（逆時針為正） $\rightarrow$ `RobotState.position[2]` |
| **`battery`** | Float | **是** | 百分比 (`0.0` ~ `100.0`) | 電池電量（FA 自動轉為 RMF SoC: 0.0~1.0） |
| **`status`** | String | **是** | `"idle"` \| `"moving"` \| `"charging"` \| `"error"` | 當前運作狀態 |
| **`current_cmd_id`** | Integer / Null | **是** | 整數編號 或 `null` | 正在執行的指令 ID（待命未接單時為 `null`，移動時為任務號） |

> **選填補充欄位**（單樓層場域可完全省略）：
> - `level_name` (String): 多樓層場域時標記所在樓層（例：`"1F"`、`"B1"`；省略時使用路網第一個樓層，預設 `"1F"`）。多樓層車輛**必須**回報，FA 依此判斷所在樓層與路網距離。

心跳只對**一般點位**的 `navigate` 判定到點：心跳的 `current_cmd_id` 與樓層相符（未帶 `current_cmd_id` 時不檢查），
且與目標距離 < 0.25 m、朝向差 < 0.2 rad 時，FA 直接通知 RMF 完成，不要求車端停車；車端仍保有原 Nav2 目標，
由 RMF 的下一個 `navigate` 取代，之後該 `cmd_id` 的 `command_result` 會被忽略。
**精準點位**一律等待車端對應的 `command_result`，心跳不判定完成：
- 電梯車廂點位（之後接 `prepare_floor_change`，避免未到點就關閉定位）。
- `dock` 指令、多步任務 (`task`) 與其他動作。
- 路網頂點 `precise_arrival: true`（編輯器勾選「精準到點」；充電樁、物料取放站固定開啟）。

---

### 4.4 RMF 下發指令 (`command`)
- **Topic**：`rmf/{fleet_name}/robot/{robot_id}/command`
- **方向**：Fleet Adapter $\rightarrow$ 車載端
- **QoS**：`1`
- **說明**：由 Open-RMF 交通調度器計算出無衝突路徑點後，由 FA 派發至車端執行。

#### 1. 前往目標點 (Navigate / Goto)
```json
{
  "robot_id": "tinyRobot1",
  "cmd_id": 101,
  "action": "navigate",
  "target": {
    "x": 5.40,
    "y": 7.80,
    "yaw": 1.5708,
    "level_name": "L1"
  },
  "speed_limit": 1.0,
  "timestamp": 1728312005.100
}
```

#### 2. 靠泊/充電對齊 (Dock)
```json
{
  "robot_id": "tinyRobot1",
  "cmd_id": 102,
  "action": "dock",
  "dock_name": "charger_1",
  "target": {
    "x": 0.7,
    "y": -4.43,
    "yaw": 0.0,
    "level_name": "L1"
  },
  "timestamp": 1728312010.500
}
```

#### 3. 緊急停止 / 暫停 (Stop / Pause)
```json
{
  "robot_id": "tinyRobot1",
  "cmd_id": 103,
  "action": "stop",
  "reason": "Traffic conflict pause by RMF schedule",
  "timestamp": 1728312015.000
}
```
- 下發時機：RMF 交通管制/任務取消（`reason` 為 RMF hold），或操作員緊急停車（`reason: "Emergency stop by operator"`）。車端處理方式相同：立即中止目前導航並回報 `command_result`。
- 緊急停車期間 Fleet Adapter 不會再下發任何指令；解除後若原任務仍有效，會以新的 `cmd_id` 重新下發被中斷的 `navigate`/`dock`/`action`。

#### 4. 電梯內準備切換樓層／暫停導航 (Prepare Floor Change)
```json
{
  "robot_id": "tinyRobot1",
  "cmd_id": 104,
  "action": "prepare_floor_change",
  "lift_name": "lift_1",
  "floor_name": "1F",
  "pose": {"x": -16.214, "y": -4.395, "yaw": 0.0},
  "timestamp": 1728312018.000
}
```
- 下發時機：多樓層任務中，車輛完成進入電梯車廂點位的 `navigate` 並回報 `command_result (completed)` 後，由 FA **在通知 RMF 關門前**自動下發。
- 車端必要欄位只有 `robot_id`、`cmd_id`、`action`；`lift_name`、`floor_name`、`pose` 為 FA 的來源樓層／車廂資訊，不要求車端依此換圖。此指令不重設 LIO／IMU／Local EKF。
- 車端回報的 `final_location`（凍結定位當下的實際位姿）會被 FA 記錄為進梯位姿，供第 5 項 `change_floor` 的 `initial_pose` 使用；建議車端回報此欄位，未回報時 FA 改用進梯 `navigate` 結果的位姿。
- 車端處理：暫停／凍結導航與定位更新（如 hold `cmd_vel_safety`、`global_pose_adapter`、`global_tf_gate`、`global_localization` 與 pause Nav2 lifecycle，避免電梯升降過程造成 IMU 或里程計漂移）→ 回報 `command_result`（`status: "completed"`）。
- 安全互鎖：FA 必須收到此 `command_result (completed)` 後，才會通知 Open-RMF 完成進電梯動作並觸發電梯關門與升降；同時在 `prepare_floor_change` 完成至 `change_floor` 完成期間，FA 會鎖定該車於電梯車廂座標，忽略心跳座標漂移。

#### 5. 切換樓層地圖並恢復導航 (Change Floor)
```json
{
  "robot_id": "tinyRobot1",
  "cmd_id": 105,
  "action": "change_floor",
  "floor_name": "B1",
  "initial_pose": {"x": 1.468, "y": -2.289, "yaw": 2.150},
  "timestamp": 1728312020.000
}
```
- 下發時機：多樓層任務中，電梯抵達目的樓層且門已開啟後（RMF `localize`），由 FA 自動下發；上位系統不需另外呼叫。
- `initial_pose` 為**目的樓層地圖座標**：FA 取 `prepare_floor_change` 完成時記錄的進梯位姿，先以出發樓層 `levels[].transform` 轉回 RMF 建築座標，再以目的樓層 transform 轉換（yaw 加上兩層 rotation 差），保留車輛在車廂內的實際位置與朝向。
  - 換算位置距 RMF 車廂點超過 1.5 m 時，視為 transform 或記錄異常，改用 RMF 車廂點位置、保留換算後的 yaw。
  - 沒有記錄進梯位姿時，使用 RMF 換算的車廂點位姿。
- 車端處理：載入該樓層地圖 → 以 `initial_pose` 重新定位並重新啟用導航 → 導航 Ready 後回報 `command_result`（`status: "completed"`，`final_location.level_name` 為新樓層）。之後心跳的 `level_name` 必須改為新樓層，接著 RMF 才會下發走出電梯的下一個點位。
- 失敗（樓層不存在、車輛移動中等）回報 `status: "failed"`，RMF 任務會因此卡在電梯內，需人工處理。

---

### 4.5 指令完成或異常回報 (`command_result`)
- **Topic**：`rmf/{fleet_name}/robot/{robot_id}/command_result`
- **方向**：車載端 $\rightarrow$ Fleet Adapter
- **QoS**：`1`
- **說明**：車端接收到 `command` 並完成該階段動作（或發生被障礙物阻擋等錯誤）時發送。
- 必須包含對應的 `cmd_id` 與明確的 `status`；省略 `status` 不視為成功，過期指令的結果不會完成目前階段。

```json
{
  "robot_id": "tinyRobot1",
  "cmd_id": 101,
  "status": "completed",
  "message": "Target reached within tolerance",
  "final_location": {
    "x": 5.398,
    "y": 7.802,
    "yaw": 1.569
  },
  "timestamp": 1728312020.300
}
```

`status` 列舉值：
- `"completed"`：成功抵達目標點或完成對位。
- `"failed"`：遇到死路或硬體故障無法抵達。
- `"canceled"`：車端收到新指令覆蓋或收到 `stop` 成功中斷。

---

### 4.6 正常離線註銷 (`deregister`)
- **Topic**：`rmf/{fleet_name}/robot/{robot_id}/deregister`
- **方向**：車載端 $\rightarrow$ Fleet Adapter
- **QoS**：`1`
- **說明**：車輛欲關機、重啟、回廠保養或切換為手動遙控 (Manual) 前發送，FA 先將車輛標為離線、不接受新工作。新版車端下次開機必須產生新的 `session_id`；FA 在接受新 session 時才確認取消舊任務、排隊工作與 idle behavior，並等待有效定位後恢復。`deregister` 本身不代表舊 RMF 工作已清除。若只是 Wi-Fi／MQTT 短暫斷線，無須發送 `deregister` 或改變 ID，恢復 heartbeat 即可沿用任務。

```json
{
  "robot_id": "tinyRobot1",
  "fleet_name": "tinyRobot",
  "reason": "Shutdown maintenance",
  "timestamp": 1728312100.000
}
```

---

### 4.7 異常失聯防護：MQTT 遺囑訊息 (LWT)
- **Topic**：`rmf/{fleet_name}/robot/{robot_id}/status`
- **QoS**：`1`
- **Retained**：`False`
- **說明**：車載端在建立 MQTT 連線時，**必須**向 Broker 設定遺囑訊息 (Will Message)。若車輛遭遇異常斷電或無線訊號中斷，EMQX Broker 會立即自動發布此訊息給 FA。
- **FA 離線處理**（LWT、`deregister` 或心跳逾時 10 秒）：車輛標為 `offline` 並 decommission（不接新任務），已下發的命令保留不結束。離線期間 RMF 下派的導航／動作**不會發送**，FA 以 `log_error` 回報並暫存（同一車只保留最新一筆，不呼叫 `replan` 以免循環）。**同 session** 恢復時，暫存命令以新的 `cmd_id` 下發一次；若 RMF 已取消該 activity 則丟棄。**新 session** 則依 4.1 清除舊工作，禁止重播暫存命令。恢復後仍遵守離線、緊急停車及車隊暫停狀態，不會因重連自行恢復接單。
- **遺失命令重送**：MQTT 使用 clean session，斷線期間（FA 尚未判定離線前）下發的命令可能遺失。已下發命令若超過 5 秒心跳仍未回報該 `cmd_id`，FA 會以**相同 `cmd_id`** 重送；車端收到與執行中命令相同 `cmd_id` 時**必須**去重，不得重新開始。

#### 車端建立 MQTT 連線時設定之 LWT 內容：
```json
{
  "robot_id": "tinyRobot1",
  "status": "offline",
  "reason": "Unexpected connection loss (LWT triggered)",
  "timestamp": 1728312200.000
}
```

---

### 4.8 車隊管理控制 (`control`)
- **Topic**：`rmf/{fleet_name}/control`
- **方向**：Fleet Adapter 內部控制介面（後端管理系統改經 ROS 2 `/rmf/fleet_control` 下發，見 `architecture.md`「Backend ↔ Fleet Adapter ROS 2 介面」）
- **QoS**：`1`
- **說明**：用於動態暫停 (`pause`) 或恢復 (`resume`) 指定車隊的調度。車隊暫停期間，RMF 與 Fleet Adapter 會將該車隊所有機器人退役 (decommission)，且拒絕新調度任務指派；恢復時重新上線 (recommission)，但仍離線的車維持 decommission 直到恢復連線。暫停不影響進行中命令的完成回報。

#### Payload 格式範例：
```json
{
  "action": "pause",
  "fleet": "fleet1"
}
```

---

## 5. 車輛完整生命週期與狀態機 (State Machine & Sequence)

### 5.1 開機與任務派遣循序圖

```mermaid
sequenceDiagram
    autonumber
    participant AMR as AMR 車載系統
    participant Broker as EMQX MQTT Broker
    participant FA as Open-RMF Fleet Adapter
    participant RMF as Open-RMF Core 調度引擎

    Note over AMR,Broker: 【階段一：建立連線與註冊】
    AMR->>Broker: 1. MQTT Connect (攜帶 LWT 遺囑設定)
    AMR->>Broker: 2. Publish /register (包含車輛規格、初始座標)
    Broker->>FA: 3. 轉發 /register
    FA->>RMF: 4. 呼叫 adapter.add_robot(...)
    RMF-->>FA: 5. 註冊成功，產生 RobotHandle
    FA->>Broker: 6. Publish /register_ack (status: success)
    Broker->>AMR: 7. 接收 /register_ack
    
    Note over AMR,FA: 【階段二：常態心跳與監控】
    loop 每 200ms ~ 500ms
        AMR->>Broker: 8. Publish /heartbeat (x, y, yaw, battery, status)
        Broker->>FA: 9. 轉發 /heartbeat
        FA->>RMF: 10. update_position() / update_battery()
    end

    Note over FA,AMR: 【階段三：任務派遣與導航】
    RMF->>FA: 11. 觸發 navigate() 回呼函式 (分配無衝突路徑點)
    FA->>Broker: 12. Publish /command (action: navigate, cmd_id: 101, target: [x,y,yaw])
    Broker->>AMR: 13. 收到導航指令
    AMR->>AMR: 14. 自主避障移動中 (心跳回報 status: "moving", current_cmd_id: 101)
    AMR->>Broker: 15. 抵達目標點，Publish /command_result (status: "completed", cmd_id: 101)
    Broker->>FA: 16. 收到完成回報
    FA->>RMF: 17. 呼叫 execution.finished()
    RMF->>FA: 18. 排程器指派下一個路徑點或宣告任務結束
```

### 5.2 晚啟動與斷線自癒機制循序圖 (Late Startup & Reconnection)

當車載端比 FA 提早開機、或 FA 伺服器中途當機重開時，系統透過以下雙層防護機制在 1 秒內自動修復連線：

```mermaid
sequenceDiagram
    autonumber
    participant AMR as AMR 車載系統 (早開機)
    participant Broker as EMQX MQTT Broker
    participant FA as Fleet Adapter (晚開機/重啟)

    Note over AMR: 車輛 08:00 開機，此時 FA 尚未啟動
    AMR->>Broker: 1. Publish /register
    Note over AMR: ⏳ 等待 register_ack 超時 (3秒未收到 ACK)
    AMR->>Broker: 2. 定期重發 /register (每 3 秒一次)

    Note over FA: FA 於 08:05 啟動上線並訂閱主題！
    
    alt 分支 A：捕捉到車端的定期重發
        AMR->>Broker: 3. 下一次重發 /register
        Broker->>FA: 4. 收到註冊請求
        FA->>Broker: 5. Publish /register_ack (status: "success")
        Broker->>AMR: 6. 註冊成功，握手閉環
    else 分支 B：由心跳逆向觸發補註冊 (更即時)
        AMR->>Broker: 3. 車端持續發送 /heartbeat
        Broker->>FA: 4. FA 收到未知車輛心跳
        Note over FA: 發現此車不在名冊中！
        FA->>Broker: 5. Publish /register_ack (status: "require_register")
        Broker->>AMR: 6. 收到要求補註冊指示
        AMR->>Broker: 7. 立即補發 /register
        Broker->>FA: 8. 收到補發之註冊
        FA->>Broker: 9. Publish /register_ack (status: "success")
        Broker->>AMR: 10. 註冊成功，自動恢復連線！
    end
```

---

## 6. 車載端實作檢查清單 (Implementation Checklist)

車載工程師在撰寫對接模組時，請依照下列項目自我檢查：

- [ ] **坐標系校正**：確認車端傳出的座標系原點與 SLAM 2D 地圖的原點完全一致（右手定則，角度逆時針為正）。
- [ ] **連線設置 LWT**：連線時是否已設定 `.../status` 的遺囑訊息？
- [ ] **握手確認**：開機後是否確實等待 `register_ack` 回覆成功後，才將車況設定為 `idle`？
- [ ] **註冊定時重試**：發出 `/register` 後若超過 3 秒未收到 ACK，是否具備定時重試重發機制？
- [ ] **斷線自癒支援**：若收到 `register_ack` 為 `status: "require_register"`，是否立即自動補發 `/register`？
- [ ] **指令確認 (cmd_id 追蹤)**：收到 `/command` 後，心跳中的 `current_cmd_id` 是否有對應更新？完成時是否送出包含同一個 `cmd_id` 的 `command_result`？
- [ ] **心跳頻率穩定**：心跳維持在 2~5 Hz，避免過低造成 RMF 誤判為掉線，亦避免高於 20 Hz 浪費頻寬。
- [ ] **角度範圍規範**：朝向角度是否嚴格限制在 $[-\pi, +\pi]$（約 $-3.14159$ 至 $+3.14159$）之間？
- [ ] **正常關機處理**：在車體主程式關閉時，是否發送 `/deregister` 告知 FA？
