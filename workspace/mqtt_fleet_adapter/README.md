# Open-RMF MQTT Fleet Adapter

本模組提供基於 **MQTT** 與 **Open-RMF EasyFullControl API** 的車隊轉接器，用於將自主移動機器人 (AMR) 連接至 Open-RMF 交通排程系統。

---

## 📂 目錄結構

```
workspace/mqtt_fleet_adapter/
├── mqtt_fleet_adapter.py    # 主程式：MQTT Fleet Adapter (支援即時 Open-RMF 與獨立 Standalone 測試模式)
├── config.yaml              # 車隊參數設定檔 (速度、尺寸、電池特性等)
├── mock_robot.py            # 模擬車輛客戶端 (可用於模擬開機註冊、心跳發送、接收任務並移動)
├── run_adapter.sh           # 一鍵啟動腳本 (自動載入 ROS 2 Jazzy 環境)
└── README.md                # 說明文件
```

---

## 🚀 快速啟動

### 1. 啟動 Fleet Adapter
在宿主機或 Docker 容器內執行：

```bash
# 方式 A：使用啟動腳本
./workspace/mqtt_fleet_adapter/run_adapter.sh

# 方式 B：獨立測試模式 (無需先啟動 RMF Core 排程節點，專用於驗證 MQTT 通訊)
python3 workspace/mqtt_fleet_adapter/mqtt_fleet_adapter.py --standalone
```

### 2. 啟動模擬機器人 (Mock AMR Client)
開啟另一個終端機，執行模擬車輛連線：

```bash
python3 workspace/mqtt_fleet_adapter/mock_robot.py --id mock_amr_01 --x 0.7 --y 0.9
```

- 車輛會立即向 EMQX 發布開機註冊訊息 (`.../register`)。
- Fleet Adapter 驗證成功後回覆 `register_ack`。
- 車輛開始以 2Hz 發送座標與電量心跳 (`.../heartbeat`)。
- 按下 `Ctrl + C`，車輛會主動發送註銷 (`.../deregister`) 並安全離線。

---

## 📡 模擬派發任務測試

當 Adapter 與 Mock AMR 都在運行時，可使用任意 MQTT 客戶端（例如 `mosquitto_pub` 或 EMQX Dashboard）向車輛下發移動指令：

```bash
# 下發導航指令讓 mock_amr_01 移動至 (x=3.0, y=5.0)
docker exec emqx_broker emqx ctl ... # 或於宿主機執行：
mosquitto_pub -h 127.0.0.1 -p 1883 -t "rmf/tinyRobot/robot/mock_amr_01/command" -m '{
  "cmd_id": 101,
  "action": "navigate",
  "target": {"x": 3.0, "y": 5.0, "yaw": 0.0, "level_name": "L1"},
  "speed_limit": 1.2
}'
```

模擬車輛接收到指令後會自動朝目標點前進，抵達後自動發送 `command_result` 並通知 FA 完成。

---

## 🏢 多樓層 / 電梯

- 路網 (`active_nav_graph.json`) 可含 `levels`（樓層名稱與 `transform`：RMF 建築座標 → 該樓層地圖座標）與 `lifts`；站點以 `level` 標記樓層，以 `lift` 標記電梯車廂點。
- FA 依 `levels[].transform` 產生 fleet `transforms`。進入車廂的 `navigate` 必須收到車端對應的 `command_result (completed)`，才發送 `prepare_floor_change`；準備完成回報後才通知 RMF 完成進梯，允許電梯關門與升降。
- 抵達目的樓層開門後，RMF `localize` 觸發 MQTT `change_floor`（見規格書 4.4）；`initial_pose` 為 `prepare_floor_change` 結果 `final_location` 記錄的進梯位姿，經兩層 `levels[].transform` 換算到目的樓層（含 yaw），等待車端換圖與定位完成，再繼續目標樓層的導航。心跳座標不作為電梯點位的到點判定。
- 一般點位的 `navigate` 在心跳距目標 < 0.25 m、朝向差 < 0.2 rad 時即完成，讓車子不必在每個中途點停下對準朝向；電梯車廂、`dock`、充電樁、物料取放站及路網 `precise_arrival: true` 的精準點位則必須等車端 `command_result`。
- 電梯本身由 `workspace/mqtt_lift_adapter` 透過 MQTT 控制（見該目錄 README）。
- `mock_robot.py --level 1F` 支援 `prepare_floor_change`／`change_floor`，可搭配 `mock_lift.py` 做無模擬器的端對端測試。

## 📏 位置判定與距離參數

下列常數定義在 `mqtt_fleet_adapter.py`，**目前寫在程式碼中，沒有對應的設定檔欄位**；修改後需重啟 Fleet Adapter。

| 參數 | 值 | 誰使用 | 作用 |
| --- | --- | --- | --- |
| `ARRIVAL_DISTANCE_M` / `ARRIVAL_YAW_RAD` | 0.25 m / 0.2 rad | Adapter | 一般點位的 `navigate` 在心跳距目標與朝向差小於此值時即完成，不等車端 `command_result`（精準點位例外） |
| `WAYPOINT_MERGE_DISTANCE_M` | 0.3 m | Open-RMF | 車子離某 waypoint 在此距離內，RMF 視為站在該點上。競標時以該點估算路程 |
| `LANE_MERGE_DISTANCE_M` | 0.3 m | Open-RMF | 不在 waypoint 上時，RMF 尋找所在 lane 的基準距離。RMF 會依序乘上 1、2、3、5、10 倍放寬，最遠約 3 m。範圍內的車子啟動後會被 responsive wait 移動到最近的 waypoint 待命（見下方「閒置待命」） |
| `OFF_GRAPH_THRESHOLD_M` | 3.0 m | Adapter（後端與前端各有同值鏡像） | 車子離路網超過此距離即標記為偏離路網：禁止競標與操控 |

### 為什麼要這樣設

- **`WAYPOINT_MERGE_DISTANCE_M` 必須大於 `ARRIVAL_DISTANCE_M`。** 車子在到點判定內（≤ 0.25 m）就被視為完成，實際位置可能偏離 waypoint 0.1～0.25 m。若吸附距離太小，RMF 會把它放在相鄰的 lane 上，競標時改從該 lane 的**終點**估算，導致離任務近的車反而輸給遠的車。
- **不能設太大。** RMF 會吸附到範圍內**編號最小**的 waypoint，而不是最近的；例如停在 parking_2 的車，5 m 內有 wp_5，會被誤判成在 wp_5。
- **`OFF_GRAPH_THRESHOLD_M` 對齊 RMF 的 lane 搜尋上限（0.3 m × 10 = 3 m）。** 超過此範圍 RMF 會將車子標記為 LOST：不能估算、也不會執行 responsive wait，所以 Adapter 同步將其視為偏離路網。修改時須同步 `backend/state.py` 與 `frontend/src/lib/constants.ts`。

### Fleet 設定不會傳到每台車（RMF 限制）

在 Python 設定 `FleetConfiguration.default_max_merge_*` **不會生效**：Python 的 `RobotConfiguration` 只接收 chargers，其餘欄位使用 C++ 的非空預設值（waypoint 1e-3、lane 0.3），`add_robot()` 會以車輛設定蓋掉車隊預設值。因此：

- waypoint 吸附距離在每台車註冊成功後，以 `update_handle.set_max_merge_waypoint_distance()` 個別設定（只存在記憶體，不寫檔）。
- Adapter 重啟後車輛會重新註冊，設定會再套用一次。
- 車隊層級的 `default_max_merge_*` 目前只為保持數值一致，未來 RMF 版本若修正傳遞才會生效。

### 閒置待命（responsive wait）

車子閒置時移動到附近 waypoint 待命並讓道，由 Open-RMF 的 `responsive_wait` 控制，目標點是 RMF 判定位置中離車子最近的 waypoint。

- 註冊時 Adapter 先關閉（`cfg.default_responsive_wait = False`），避免車子尚未定位就被叫去移動。
- 確認定位後，依 `config.yaml` 的 `rmf_fleet.responsive_wait` 對每台車開啟；設為 `false` 則閒置時停在原地。
- **啟動時車子可能自己移動：** 車子若不在 waypoint 上（超出 `WAYPOINT_MERGE_DISTANCE_M`），但在 `LANE_MERGE_DISTANCE_M` 放寬後的範圍內（最遠約 3 m），RMF 會把它判定在某條 lane 上，候選點為該 lane 兩端的 waypoint。responsive wait 開啟後，RMF 會讓它開到其中離車子最近的 waypoint 待命。離路網越遠，移動越明顯；超過約 3 m 則為 LOST，不會移動（見上方 `OFF_GRAPH_THRESHOLD_M`）。
- 這段移動期間車子的 RMF 位置仍以第一個候選點（lane 終點）計算，競標時需注意（見下節）。
- 車子急停、暫停或 session 重置中時，Adapter 會透過 `commission.perform_idle_behavior` 暫時停用。

### 派單競標的注意事項

RMF 競標只使用車子位置判定結果的**第一個**候選點，且不考慮交通：

- 車子被判定在 lane 上時，會以該 lane 終點估算，與實際位置可能不同。
- 即使車子較近、競標得標，路上仍可能需要讓道，這不影響得標結果。

## 📑 車端通訊規格書

完整且詳細的 MQTT 通訊欄位規範、JSON Schema 與生命週期狀態機，請參閱：
👉 **[docs/amr_mqtt_interface_spec.md](../../docs/amr_mqtt_interface_spec.md)**
