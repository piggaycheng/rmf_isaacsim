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
- FA 依 `levels[].transform` 產生 fleet `transforms`，並在 RMF 乘電梯抵達目的樓層後，透過 MQTT `change_floor` 指令（見規格書 4.4）要求車端載入新樓層地圖並重新定位。
- 電梯本身由 `workspace/mqtt_lift_adapter` 透過 MQTT 控制（見該目錄 README）。
- `mock_robot.py --level 1F` 支援 `change_floor`，可搭配 `mock_lift.py` 做無模擬器的端對端測試。

## 📑 車端通訊規格書

完整且詳細的 MQTT 通訊欄位規範、JSON Schema 與生命週期狀態機，請參閱：
👉 **[docs/amr_mqtt_interface_spec.md](../../docs/amr_mqtt_interface_spec.md)**
