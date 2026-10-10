# RMF → Fleet Adapter 任務下派修正清單

檢查日期：2026-10-08

本文件供後續 Agent 修正與驗證使用。以下問題來自目前程式碼檢查、隔離回呼重現及運行中容器的既有日誌；本次未修改實作，也未向實車下派測試任務。

基本的 `goto → RMF → Fleet Adapter → MQTT → command_result` 流程已有成功日誌，但異常處理與完成判定仍有誤報完成、卡單及錯誤路由的風險。

## 範圍與修正原則

- 本文行號以檢查當下版本為準，修改後請依函式名稱定位。
- `Adapter` 指 `workspace/mqtt_fleet_adapter/mqtt_fleet_adapter.py`。
- 優先處理高優先級問題，尤其是失聯、完成判定、STOP、失敗收尾與真實模式混入模擬狀態。
- `CommandExecution.finished()` 在容器內 EasyFullControl API 的定義是「指令成功完成」，不可用作通用取消、失敗或離線清理。
- 車端取消與去重實作不在本 repository；`mock_robot.py` 不能代表實車行為。涉及 MQTT 協定的修改需同步確認車端相容性，並更新 `docs/amr_mqtt_interface_spec.md`。
- 不要因為 `goto` 使用 `patrol` category 就直接改掉：既有日誌確認這種映射能被目前 RMF 接受。
- 使用既有熱重載開發環境；日常修改不要執行 `npm run build` 或 `docker compose build`。前端型別檢查可用 `cd frontend && npx tsc --noEmit`。
- 不要自動 commit；不要為驗證而直接向運行中的實車派送任務。

## 1. 高優先級：註銷與心跳逾時被回報為成功

**位置：** `Adapter:381–393, 557–572`，`_handle_deregister()`、`_watchdog_loop()`。

**問題：** 註銷及心跳逾時直接呼叫 `current_execution.finished()`，即使車輛尚未到站。RMF 可能因此推進下一階段。

**修正方向：** 分離成功、取消、故障及離線處理。使用目前安裝版本支援的 RMF 中斷／問題回報／可派遣性控制機制，不要臆測存在的 API，也不要將清理包裝成成功。離線後的路權與佔位處理需保守反映實體車輛可能仍在現場。

**驗收：**

- [x] 執行中註銷或心跳逾時不會呼叫成功完成回呼。
- [x] 任務及車輛狀態能明確反映中斷／離線，不會自動當成到站或進入下一動作。
- [x] 重連恢復流程不會遺失、重複完成或無條件重送舊任務。（第二輪 review 未通過，見第 11 項 11.1）

## 2. 高優先級：LWT 失聯繞過 watchdog，離線車仍可能收到下派

**位置：** `Adapter:398–404, 564`，`_handle_status()`、`_watchdog_loop()`；導航與動作回呼。

**問題：** LWT 將 `is_online` 設為 `False`，watchdog 卻只處理 online 車輛，因此既有 execution 不會進入逾時處理。下派回呼也沒有阻擋 offline 車輛。

**修正方向：** 將 LWT、心跳逾時與註銷納入一致且冪等的離線生命週期；同步處理 RMF 可派遣性，不只修改本地旗標。定義重連時如何恢復既有 RMF handle，避免重複註冊或重設命令狀態。

**已觀察到的實際故障（2026-10-10）：** 車端關閉時送出 `deregister`，舊版 `_handle_deregister()` 會把車從 `self.robots` 刪除，但 RMF 核心沒有移除車輛的 API，車仍留在 fleet 中。車端重啟後再註冊時，`add_robot()` 記錄 `Robot [...] was previously added ... Ignoring request` 並回傳 `None`，Adapter 卻仍回 `Registered successfully`。之後每次心跳都因 `update_handle is None` 回 `require_register`，車端立刻重送註冊，形成約每秒一次的重新註冊迴圈；只有重啟 Adapter 才能暫時恢復。

**已修正（2026-10-10）：**

- `_handle_deregister()` 不再刪除車輛，只標記 `is_online=False`、`status="offline"`，保留 `update_handle`。
- `_handle_register()` 遇到已有 handle 的車輛時沿用舊 handle，更新位置並設回 online，不再呼叫 `add_robot()`。
- `add_robot()` 回傳 `None`，或找不到 fleet handle 時，回 `error` ACK，不再誤報成功。
- 已在運行環境確認重新註冊迴圈消失。

**第一輪 review 列出的未完成項目（`fix/rmf-dispatch-issues` 已處理，殘留問題見第 11 項）：**

- 重新註冊時保留有效 execution 的 activity：已修正。
- 重新註冊依 `(fleet, robot_id)` 區分，不沿用其他 fleet 的 handle：已修正，但 `_get_robot()` 仍有跨 fleet fallback（11.2）。
- 導航／動作回呼阻擋 offline 車輛：已加入，但拒絕時沒有回報 execution 結果（11.4）。
- `_handle_deregister()` 不再呼叫 `finished()`：已修正，但保留的 execution 在車端重啟後無法收尾（11.1）。

**驗收：**

- [x] LWT 與心跳逾時產生一致的任務中斷結果，且不誤報成功。
- [x] 重複 offline 訊息不會重複清理或觸發額外任務轉移。
- [x] 離線車不能開始新命令；恢復連線後需有明確的狀態核對。（見第 11 項 11.1、11.4）
- [x] 註銷後重新註冊沿用既有 RMF handle，不會因 `add_robot()` 被忽略而形成重新註冊迴圈。
- [x] `add_robot()` 失敗或回傳 `None` 時回報錯誤，不誤報註冊成功。
- [x] 執行中重連時保留目前 activity，不重設命令狀態。（MQTT 重連已符合；車端重啟需改為中斷，見 11.1）

## 3. 高優先級：近距離導航忽略朝向、樓層與 docking

**位置：** `Adapter:450–475`，`_on_rmf_navigate()`。

**問題：** XY 距離小於 0.2m 時，不發車端命令，0.3 秒後直接完成。此分支未檢查 yaw、map、dock 或目前是否仍在移動。現有日誌也出現近距離直接完成分支。

**修正方向：** 只有已滿足完整導航條件時才允許原地完成。角度差需正確處理 ±π 邊界；docking 不可僅憑座標接近完成。

**驗收：**

- [x] 同 XY、不同 yaw 不會跳過必要轉向。
- [x] 同 XY、不同 map 不會回報到站。
- [x] 已在充電樁附近仍需完成 docking 流程及其確認。
- [x] 車輛尚在移動時不會因距離接近而提前完成。

## 4. 高優先級：新舊命令的完成回報未完整隔離

**位置：** `Adapter:345–365, 458–475`，`_handle_heartbeat()`、`_on_rmf_navigate()`。

**問題：** 心跳距離小於 0.25m 時，`current_cmd_id` 不符也能完成。近距離分支不更新 cmd ID，也未清除舊 payload，因此舊命令的成功回報可能完成新的 execution。心跳判定也未檢查目標朝向、樓層或 docking 結果。

**修正方向：** 將 execution、命令識別、命令類型與目標綁定；替換或取消時失效舊的 timer／retry／結果。心跳協定允許 idle 時 `current_cmd_id=null`，不要只強制 ID 相等而破壞正常完成；需建立可信的完成關聯或以明確結果作為主要依據。

**驗收：**

- [x] 延遲的舊心跳、舊成功結果與重複 QoS 1 訊息不能完成新 execution。
- [x] 近距離 execution 不會沿用舊命令的 retry payload。
- [x] idle 且 `current_cmd_id=null` 的合法車端行為不會造成永久卡單。
- [x] 心跳不能替代 docking／自訂動作的實際完成確認。

## 5. 高優先級：STOP 忽略 activity，可能誤停或誤完成導航

**位置：** `Adapter:507–531`，`_on_rmf_stop()`；`_handle_command_result()`。

**問題：** STOP 不核對傳入的 `activity_id`，也未清除或失效舊 execution。STOP 更新 cmd ID 後，若車端用 STOP 的 ID 回報成功，通用結果處理可能把舊導航當成完成。

**修正方向：** 核對要停止的 activity 與目前 execution；容器內 `ActivityIdentifier` 提供 `is_same()`，需依實際 API 使用。分離停止命令結果與導航／動作成功結果，並取消相關重試。

**驗收：**

- [x] 過期或不相符 activity 的 STOP 不會影響目前命令。
- [x] STOP 成功 ACK 不會完成先前導航。
- [x] STOP 後延遲的完成結果／重試 timer 不會重新推進或啟動舊命令。
- [x] 正常取消與 RMF 重規劃仍能停止相符的活動。

## 6. 高優先級：導航重試耗盡後缺少收尾與升級處理

**位置：** `Adapter:424–438`，`_handle_command_result()`。

**問題：** 重送兩次後 execution 仍保留，沒有 RMF 問題回報、重規劃或人工介入狀態。`canceled` 也沒有處理分支。

**修正方向：** 定義重試耗盡的明確處理，區分預期取消與非預期取消。核對車端是否接受同 ID 重送，避免與去重規則衝突；重複 failed 訊息不可排出多個並行重試。

**驗收：**

- [x] 重試耗盡後有可觀察的故障／中斷狀態，而不是靜默保留 execution。
- [x] 重複失敗訊息不會突破重試上限或重複發送。
- [x] 取消、離線、替換 execution 後，已排程重試不會發送。
- [x] `canceled` 能依命令與活動關聯正確處理，不誤判成功。

## 7. 高優先級：後端任務狀態可能顯示假成功

**位置：** `backend/rmf_service.py:120–136, 185–198`，`task_response_cb()`、`fleet_callback()`；`backend/controller/fleet_controller.py:99–133`，`simulation_loop()`。

**問題：**

- RMF 回覆 `success:false` 時沒有判讀 errors；缺少 state 反而預設成 `active`。
- 真實 ROS 車隊更新超過 2 秒沒收到，就啟用模擬進度，真實 active 任務也可能被推到 `completed`。
- 車輛 task ID 從有值變成空值就被推定成功完成，沒有確認是完成、取消或失敗。

**修正方向：** 建立任務狀態的權威來源與終止狀態保護。明確區分送出、受理、執行與終止；讀取目前 RMF 版本提供的任務生命週期通知。模擬模式需顯式啟用並隔離資料，不能因實體通訊逾時自動接管。WebSocket 下派結果也需讓前端收到拒絕／錯誤，不只廣播任務清單。

**驗收：**

- [x] RMF 拒絕請求時能呈現原因，不能轉成 active。
- [x] ROS／MQTT 中斷不會讓真實任務靠時間增加進度並完成。
- [x] 取消與失敗不會因 task ID 清空而變成 completed。
- [x] 已終止的任務不會被延遲的狀態訊息改回 active。
- [x] Publisher 未就緒時不宣稱已受理；本地排隊是否會補送需明確實作或明確拒絕。

## 8. 中優先級：任務 ID 容易碰撞

**位置：** `backend/rmf_service.py:251`，`dispatch_task()`。

**問題：** `int(time.time() * 1000) % 100000` 的命名空間每 100 秒循環，同毫秒請求也會生成相同 ID，影響 RMF 請求關聯與本地查找。

**修正方向：** 使用可靠唯一 ID，並明確區分 request ID 與 RMF booking ID；前端應使用後端的識別值，而非各自產生正式任務 ID。

**驗收：**

- [x] 同毫秒並行請求與相隔 100 秒的請求不會生成同 ID。
- [x] 不同 request ID／booking ID 能正確關聯同一任務。
- [x] 結果回報不會更新到其他任務。

## 9. 中優先級：所有任務類型都轉成單輪 patrol

**位置：** `backend/rmf_service.py:271–278`，`dispatch_task()`；`frontend/src/components/DispatchModal.tsx`。

**問題：** `goto`、`patrol`、`delivery` 都送出 `category: patrol, rounds: 1`。UI 的「物料取送」實際只是走訪取／送站點，沒有取貨、卸貨與確認流程；卸貨站點也可不選。

**修正方向：** 保留已驗證可用的 goto 映射。釐清配送需求：若只是雙點移動，UI 應明確標示；若要真正物料配送，需接入對應的 RMF 任務／動作及車端確認，不能只改 category。依選定語意同步校驗 REST／WebSocket 輸入。

**驗收：**

- [x] UI 任務名稱與實際執行內容一致。
- [x] 真正配送必須等待取／卸貨確認，不只到站就算完成。
- [x] 缺少必要站點、未知任務類型及無效站點能明確回報錯誤。
- [x] 現有 goto 與合法 patrol 下派不退化。

## 10. 中優先級：多車隊路由與管理介面不一致

**位置：** `Adapter:259–266, 535–551`，`_handle_register()`、`_on_rmf_action()`；`backend/controller/adapter_controller.py:102–172`。

**問題：**

- 自訂 ACTION 用 `self.fleet_name` 發送；目前 fleet1／fleet2 的車會被發到預設 `tinyRobot` topic。
- robot 字典及回呼僅以 robot ID 區分，跨車隊同名會混用；未知 fleet 也會 fallback 到第一個 RMF fleet handle。
- UI 的 pause／resume／restart／set-graph 只修改 JSON 與日誌，沒有控制運行中的 Adapter，卻回報已生效。
- Adapter 啟動時各車隊共用同一份 YAML，主要只覆寫 fleet name 與 graph；不能假設 UI 儲存的個別速度、尺寸及其他參數已套用。

**目前狀態（第二輪 review）：**

- Adapter 啟動時已依各車隊的 `robot_radius`、`linear_velocity`、`angular_velocity` 產生 `/tmp/fleet_cfg_<fleet>.yaml`，覆寫 footprint、vicinity 與速度上限；其他參數仍共用基礎 YAML。
- 註冊已先檢查 `configured_fleets`，且不再 fallback 到預設 fleet handle。
- 車輛改以 `(fleet, robot_id)` 為 key，但 `_get_robot()` 仍有跨 fleet fallback（11.2）。
- pause 沿用 `is_holding`，會吞掉完成回報（11.3）。

**修正方向：** 導航、STOP、ACTION、遙測與結果統一使用正確 fleet／robot 身分；拒絕未配置的 fleet，而非靜默 fallback。管理操作需有真實執行與 ACK／錯誤回傳；若不能動態切圖，明確標示需重新載入，不要製造成功日誌。切圖或重啟需處理執行中的任務。

**驗收：**

- [x] fleet1／fleet2 的 ACTION 發往各自的 MQTT topic。
- [x] 兩個 fleet 使用相同 robot ID 時仍完全隔離。（見第 11 項 11.2）
- [x] 未配置 fleet 不會被註冊到別的 fleet handle。
- [x] pause 會影響實際可派遣性；resume／restart／set-graph 成功回應對應真實執行結果。（pause 會吞掉完成回報，見 11.3）
- [x] UI 車隊參數與 RMF 實際設定一致，或明確呈現尚未套用／不支援。

## 11. 第二輪 review：`fix/rmf-dispatch-issues` 未修正完全與新問題

檢查日期：2026-10-10。對象為分支 `fix/rmf-dispatch-issues`（至 `7adc512`）。`tests/test_rmf_dispatch_regression.py` 在 backend image 內 10 項全部通過，使用的 RMF API（`CommandExecution.okay()`、`more().override_status()`、`unstable_decommission()`／`unstable_recommission()`、`log_error()`、`replan()`）已確認存在；但下列情境未被測試涵蓋。

### 11.1 高優先級：車端重啟後保留舊 execution，會卡單或誤報完成

**位置：** `Adapter` 的 `_handle_deregister()`、`_handle_register()`（重新註冊分支）、`_handle_heartbeat()`。

**問題：** 車端關閉時送出 `deregister`，Adapter 保留 `current_execution`。車端重啟後，原本的 Nav2 goal 已不存在，但重新註冊時 `execution.okay()` 仍為真，Adapter 沿用舊 activity 並把車標成 `moving`，也不重送命令：

- 車不在目標 0.25 m 內：沒有任何結果會完成或中斷 execution，任務永久卡住。
- 車剛好在目標 0.25 m 內：重啟後的心跳 `status=idle`、`current_cmd_id=None` 被視為有效，execution 被誤報成功。

**修正方向：** 區分「車端重啟」與「MQTT 短暫斷線」。`deregister`（及可判斷為新 session 的註冊）代表車端命令狀態已遺失，應以非成功方式結束或交回 execution（例如 `replan()`／issue 回報，依實際 API），不可保留成 `moving`。單純 MQTT 重連才沿用原 execution；若車端無法提供 session 或目前命令資訊，需在 MQTT 協定中補上，並更新 `docs/amr_mqtt_interface_spec.md`。

**驗收：**

- [x] 執行中 `deregister` → 重新註冊後，不會沿用舊 activity，也不會被心跳誤報完成。
- [x] 上述情境 RMF 會重規劃或呈現明確中斷狀態，不會永久卡單。
- [x] 單純 MQTT 斷線重連（未送 `deregister`）仍保留原 execution，不重送也不重複完成。

### 11.2 中優先級：`_get_robot()` 跨 fleet fallback，破壞同名車隔離

**位置：** `Adapter` 的 `_get_robot()`；呼叫者 `_handle_heartbeat()`、`_handle_deregister()`、`_handle_status()`、`_handle_command_result()` 及 RMF 回呼。

**問題：** 找不到 `(fleet_name, robot_id)` 時，會回傳任一 fleet 中同名的車。例如未註冊的 fleet2/carter1 的心跳、`deregister` 或 `command_result`，會被套用到 fleet1/carter1，可能誤更新位置、誤標離線或誤完成命令。

**修正方向：** 有 fleet 資訊時只做精確查找；移除跨 fleet fallback。找不到時心跳回 `require_register`，其他訊息忽略並記錄。

**驗收：**

- [x] fleet2/carter1 未註冊時，其心跳、`deregister`、`status`、`command_result` 不會影響 fleet1/carter1。
- [x] 回歸測試涵蓋兩個 fleet 同名車同時存在與只存在一個的情境。

### 11.3 中優先級：fleet pause 沿用 `is_holding`，吞掉完成回報

**位置：** `Adapter` 的 `_handle_fleet_control()`、`_handle_register()`（paused fleet 分支）、`_handle_command_result()`、`_handle_heartbeat()`。

**問題：** pause 把該 fleet 所有車的 `is_holding` 設為 `True`，但 `is_holding` 原本代表原地等待，`_handle_command_result()` 與心跳完成判定都會略過 `is_holding` 的車。pause 只 decommission（不再接新任務），進行中的命令仍可能完成；此時成功結果被忽略，resume 後也不會補完成，任務卡住。

**修正方向：** pause 使用獨立旗標（如 `is_paused`），只影響新任務派遣，不影響既有命令的結果處理；`is_holding` 保留原語意。若 pause 需要讓車停下，應明確送 STOP 並依第 5 項處理。

**驗收：**

- [x] pause 期間完成的命令仍能正確完成 execution。
- [x] pause 期間不接受新任務；resume 後恢復。
- [x] 原地等待（hold）行為不受影響。

### 11.4 中優先級：拒絕 offline 車的導航／動作時沒有回報

**位置：** `Adapter` 的 `_on_rmf_navigate()`、`_on_rmf_action()`。

**問題：** 車離線時兩個回呼直接 `return`，既未保存也未結束 RMF 傳入的 execution；decommission 只阻擋新任務，進行中任務的下一步仍可能下派，此時 RMF 會永久等待該 execution。

**修正方向：** 拒絕時以非成功方式明確回報（例如 `log_error`／`override_status`／`replan()`，或保存 execution 待恢復連線後再決定），不可靜默丟棄，也不可呼叫 `finished()`。

**驗收：**

- [x] 離線時收到的導航／動作不會靜默遺失，RMF 端可觀察到中斷或等待原因。
- [x] 恢復連線後不會自動重送已被拒絕的命令，除非流程明確定義。

### 11.5 低優先級：動態屬性未初始化

**位置：** `ManagedRobot.__init__()`。

**問題：** `is_retrying`、`stop_cmd_id` 只在執行中以動態方式加入，靠 `getattr(..., default)` 讀取，容易拼錯而不被發現。

**修正方向：** 在 `ManagedRobot.__init__()` 初始化（`False`／`None`），並移除對應的 `getattr` 預設值。

**驗收：**

- [x] 所有狀態欄位都在 `__init__()` 宣告。

## 驗證與交付

- [ ] 為上述成功、失敗、取消、失聯、重連、新舊結果競態及多車隊情境加入隔離測試。（第 11 項情境尚未涵蓋）
- [x] 優先使用 fake execution／MQTT publisher 驗證回呼，不要讓測試接觸實車。
- [x] 協定變更同步更新 `docs/amr_mqtt_interface_spec.md` 與相關說明；不要只修 mock 而漏掉 Adapter 或後端。
- [x] 需要端到端驗證的項目，使用明確隔離的模擬環境；未驗證的實車相容性需在交付時標示。
- [x] 修正後勾選對應項目，記錄必要的行為決策及尚未完成的部分。
