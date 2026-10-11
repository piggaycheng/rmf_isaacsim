# 透過 MQTT 操作相機與雲台

- **適用對象**：前端／FMS／外部系統開發人員
- **用途**：以 MQTT 取得相機清單、開關相機 RTSP 串流，並控制雲台（pan/tilt）旋轉；適用車載雲台相機與天花板相機
- **與 [amr_mqtt_interface_spec.md](amr_mqtt_interface_spec.md) 的關係**：該文件的 `rmf/<fleet>/robot/<robot>/*` 是導航／任務介面；本文件的 topic 是**獨立**的相機與雲台介面，使用同一個 broker 但不同的 topic 前綴，不經過 Fleet Adapter。

模擬器端（Isaac Sim）負責相機清單與串流開關；雲台由 `gimbal_mqtt_bridge.py` 負責（車載雲台在車的容器內，天花板相機在 `ceiling-gimbals` 容器內）。兩者均須連到同一個 MQTT broker（`--mqtt-host`）。

---

## 1. 相機清單與串流開關

### 1.1 相機清單（訂閱）

| 項目 | 內容 |
|---|---|
| Topic | `slam/cameras`（可由 `--mqtt-topic` 修改） |
| 方向 | 模擬器 → 外部 |
| QoS／Retain | 1 ／ retained（新訂閱者立即收到最新清單） |

Payload：

```json
{
  "online": true,
  "rtsp_port": 8554,
  "cameras": [
    {
      "name": "carter1/camera/gimbal",
      "display_name": "a gimbal",
      "rtsp_path": "carter1/camera/gimbal",
      "enable_topic": "slam/cameras/carter1/camera/gimbal/enable",
      "width": 1280,
      "height": 720,
      "fps": 30,
      "enabled": false,
      "gimbal_topic": "slam/gimbal/carter1/command"
    },
    {
      "name": "ceiling_cams/ceiling_cam_1",
      "display_name": "Ceiling camera 1",
      "rtsp_path": "ceiling_cams/ceiling_cam_1",
      "enable_topic": "slam/cameras/ceiling_cams/ceiling_cam_1/enable",
      "width": 1280,
      "height": 720,
      "fps": 30,
      "enabled": false,
      "gimbal_topic": "slam/gimbal/ceiling_cams/ceiling_cam_1/command"
    }
  ]
}
```

- 清單僅在相機開關狀態改變時更新。
- 模擬器正常結束時發布 `{"online": false, ...}`；異常斷線由 broker 以 LWT 發布相同訊息。
- 設有雲台的車在指定 `--mqtt-host` 時，雲台相機會自動加入清單，名稱為 `[ROBOT/]camera/gimbal`。
- 天花板相機 `ceiling_cams/ceiling_cam_1`～`4`（模擬器 `--no-ceiling-cameras` 可關閉）。
- 可旋轉的相機帶有 `gimbal_topic`：該相機雲台的 command topic（見第 2 節），其餘 `result`／`state`／`status` 把結尾的 `command` 換掉即可；沒有此欄位的相機不能旋轉。前綴由模擬器 `--gimbal-mqtt-topic`（預設 `slam/gimbal`）決定。

### 1.2 開關串流（發布）

| 項目 | 內容 |
|---|---|
| Topic | 清單中該相機的 `enable_topic`，即 `slam/cameras/[ROBOT/]camera/NAME/enable` |
| 方向 | 外部 → 模擬器 |
| Payload | 純文字：`true`／`1`／`on` 開啟；`false`／`0`／`off` 關閉（不分大小寫） |
| QoS | 1 |

所有相機**預設關閉**，收到 enable 才開始串流。開啟後從 `rtsp://<HOST>:<rtsp_port>/<rtsp_path>` 取流，例如 `rtsp://HOST:8554/carter1/camera/gimbal`。狀態改變後清單中的 `enabled` 會隨之更新，可據此確認結果。

```bash
mosquitto_pub -h HOST -t slam/cameras/carter1/camera/gimbal/enable -m true
mosquitto_sub -h HOST -t slam/cameras -v
```

---

## 2. 雲台旋轉控制

以下 topic 的 `NAME` 為雲台的 namespace：車載雲台為車名（如 `carter1`），天花板相機為 `ceiling_cams/ceiling_cam_N`（N = 1～4），payload 中的 `robot_id` 也是此值；前綴 `slam/gimbal` 可由節點參數 `mqtt_topic` 修改。角度單位一律為**度**；pan 繞 z 軸、可 360° 旋轉並折回 (-180, 180]，tilt 正值為向下，受限位約束（預設 ±90°）。車載雲台的 pan 0° 為車頭方向；天花板相機的 pan 0° 為場景（Office）+x 方向、逆時針為正，tilt 0° 為水平，初始姿態朝向場景原點地面，轉速 60°/s。

### 2.1 Topic 一覽

| Topic | 方向 | 說明 |
|---|---|---|
| `slam/gimbal/NAME/command` | 外部 → 雲台 | 雲台指令（QoS 1） |
| `slam/gimbal/NAME/result` | 雲台 → 外部 | 每個指令的結果（QoS 1） |
| `slam/gimbal/NAME/state` | 雲台 → 外部 | 實測姿態，約 2 Hz（QoS 0，非 retained） |
| `slam/gimbal/NAME/status` | 雲台 → 外部 | `{"robot_id","online":true/false}`，retained；異常斷線由 LWT 發布 `false` |

### 2.2 指令（command）

`cmd_id` 必填，整數或字串，用來對應 `result`。

| 動作 | 範例 | 說明 |
|---|---|---|
| `move` | `{"cmd_id":1,"action":"move","pan_deg":90,"tilt_deg":10}` | 轉到絕對角度；`pan_deg`、`tilt_deg` 都必填 |
| `rotate` | `{"cmd_id":2,"action":"rotate","pan_deg":15,"tilt_deg":0}` | 相對目前姿態轉動；缺省的欄位視為 0；`pan_deg` 的絕對值須 ≤ 180 |
| `cancel` | `{"cmd_id":3,"action":"cancel"}` | 取消進行中的動作 |

行為：

- pan 走最短路徑；到達容差內（預設 1°）才回報成功，30 秒內未到達則 `aborted`。
- 新的 `move`／`rotate` 會取代進行中的舊指令，舊指令回報 `aborted`（`Superseded by a newer goal`）。
- `cancel` 本身回報 `succeeded`（已送出取消；沒有進行中動作時訊息為 `Nothing to cancel`），被取消的指令另外回報 `canceled`。
- 格式錯誤、角度非有限數值、tilt 超出限位等回報 `rejected`。JSON 無法解析、或沒有 `cmd_id` 時無法對應，只會在 bridge 端記錄警告。

### 2.3 結果（result）

```json
{
  "robot_id": "carter1",
  "cmd_id": 1,
  "status": "succeeded",
  "message": "Reached target pose",
  "pan_deg": 90.2,
  "tilt_deg": 10.0,
  "timestamp": 1760000000.0
}
```

`status`：`succeeded`、`aborted`、`canceled`、`rejected`。`pan_deg`／`tilt_deg` 為結束時的實測姿態，沒有雲台狀態時省略。

### 2.4 狀態（state）

```json
{"robot_id": "carter1", "pan_deg": 45.1, "tilt_deg": 5.0, "busy": false,
 "current_cmd_id": null, "timestamp": 1760000000.0}
```

`busy` 為 `true` 時 `current_cmd_id` 是進行中的指令。沒有新的雲台狀態時不發布。

### 2.5 範例

```bash
# 轉到 pan 90°、tilt 10°
mosquitto_pub -h HOST -t slam/gimbal/carter1/command \
  -m '{"cmd_id":1,"action":"move","pan_deg":90,"tilt_deg":10}'

# 再向右轉 15°
mosquitto_pub -h HOST -t slam/gimbal/carter1/command \
  -m '{"cmd_id":2,"action":"rotate","pan_deg":15}'

# 監看結果與姿態
mosquitto_sub -h HOST -t 'slam/gimbal/carter1/#' -v

# 天花板相機 2：轉向 pan 90°、略往下 20°
mosquitto_pub -h HOST -t slam/gimbal/ceiling_cams/ceiling_cam_2/command \
  -m '{"cmd_id":1,"action":"move","pan_deg":90,"tilt_deg":20}'
```

Python（paho-mqtt 2.x）：

```python
import json, paho.mqtt.client as mqtt

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect("HOST", 1883)
client.subscribe("slam/gimbal/carter1/result", qos=1)
client.on_message = lambda c, u, m: print(json.loads(m.payload))
client.publish("slam/gimbal/carter1/command", json.dumps(
    {"cmd_id": 1, "action": "move", "pan_deg": 90, "tilt_deg": 10}), qos=1)
client.loop_forever()
```

---

## 3. 典型流程（看向某方向）

1. 訂閱 `slam/cameras`，找到要用的相機（如 `carter1/camera/gimbal` 或 `ceiling_cams/ceiling_cam_2`）及其 `gimbal_topic`。
2. 發布 `true` 到其 `enable_topic`，用 RTSP 播放。
3. 發布 `move`／`rotate` 到 `gimbal_topic`，同時看畫面。
4. 以 `result` 的 `succeeded` 確認到位；看完後發布 `false` 關閉串流。

## 4. 啟動

`run_multi_nav_online.sh` 會自動啟動：每台設有雲台的車在其容器內執行 action server 與 MQTT bridge（`robot.launch.py`），天花板相機由 `ceiling-gimbals` 容器執行四組。手動啟動：

```bash
# 一次啟動多組（; 分隔），mqtt_host 留空則只啟動 action server
ros2 launch slam_sensor_control gimbals.launch.py \
  'namespaces:=carter1;ceiling_cams/ceiling_cam_1' mqtt_host:=HOST

# 在車的 namespace 下，先啟動 action server，再啟動 MQTT 橋接
ros2 run slam_sensor_control gimbal_action_server.py --ros-args -r __ns:=/carter1
ros2 run slam_sensor_control gimbal_mqtt_bridge.py --ros-args -r __ns:=/carter1 \
  -p mqtt_host:=HOST [-p mqtt_port:=1883 -p mqtt_username:=U -p mqtt_password:=P]
```

節點需安裝 `paho-mqtt`；沒有雲台的車不需要啟動這兩個節點。實作細節見 SLAM 專案的 `docs/isaac_bridge.md` 與 `docs/online.md`。
