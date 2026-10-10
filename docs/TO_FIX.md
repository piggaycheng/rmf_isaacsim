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

- [ ] 執行中註銷或心跳逾時不會呼叫成功完成回呼。
- [ ] 任務及車輛狀態能明確反映中斷／離線，不會自動當成到站或進入下一動作。
- [ ] 重連恢復流程不會遺失、重複完成或無條件重送舊任務。

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

**仍未完成：**

- 重新註冊時固定呼叫 `update_handle.update(state, None)` 並把 `status` 設為 `idle`；若 MQTT 在執行中重連且 execution 仍有效，RMF 會短暫認為車輛沒有活動。應沿用 `current_execution.identifier`，並只在沒有 execution 時重設狀態。
- 重新註冊沒有核對 fleet：同名車改用其他 fleet 時，會沿用舊 fleet 的 handle，卻把 `fleet_name` 改成新的（見第 10 項）。
- `_on_rmf_navigate()`／`_on_rmf_action()` 仍未阻擋 offline 車輛；註銷後車仍在 RMF 核心，可能被派遣。
- `_handle_deregister()` 仍呼叫 `current_execution.finished()`（見第 1 項）。

**驗收：**

- [ ] LWT 與心跳逾時產生一致的任務中斷結果，且不誤報成功。
- [ ] 重複 offline 訊息不會重複清理或觸發額外任務轉移。
- [ ] 離線車不能開始新命令；恢復連線後需有明確的狀態核對。
- [x] 註銷後重新註冊沿用既有 RMF handle，不會因 `add_robot()` 被忽略而形成重新註冊迴圈。
- [x] `add_robot()` 失敗或回傳 `None` 時回報錯誤，不誤報註冊成功。
- [ ] 執行中重連時保留目前 activity，不重設命令狀態。

## 3. 高優先級：近距離導航忽略朝向、樓層與 docking

**位置：** `Adapter:450–475`，`_on_rmf_navigate()`。

**問題：** XY 距離小於 0.2m 時，不發車端命令，0.3 秒後直接完成。此分支未檢查 yaw、map、dock 或目前是否仍在移動。現有日誌也出現近距離直接完成分支。

**修正方向：** 只有已滿足完整導航條件時才允許原地完成。角度差需正確處理 ±π 邊界；docking 不可僅憑座標接近完成。

**驗收：**

- [ ] 同 XY、不同 yaw 不會跳過必要轉向。
- [ ] 同 XY、不同 map 不會回報到站。
- [ ] 已在充電樁附近仍需完成 docking 流程及其確認。
- [ ] 車輛尚在移動時不會因距離接近而提前完成。

## 4. 高優先級：新舊命令的完成回報未完整隔離

**位置：** `Adapter:345–365, 458–475`，`_handle_heartbeat()`、`_on_rmf_navigate()`。

**問題：** 心跳距離小於 0.25m 時，`current_cmd_id` 不符也能完成。近距離分支不更新 cmd ID，也未清除舊 payload，因此舊命令的成功回報可能完成新的 execution。心跳判定也未檢查目標朝向、樓層或 docking 結果。

**修正方向：** 將 execution、命令識別、命令類型與目標綁定；替換或取消時失效舊的 timer／retry／結果。心跳協定允許 idle 時 `current_cmd_id=null`，不要只強制 ID 相等而破壞正常完成；需建立可信的完成關聯或以明確結果作為主要依據。

**驗收：**

- [ ] 延遲的舊心跳、舊成功結果與重複 QoS 1 訊息不能完成新 execution。
- [ ] 近距離 execution 不會沿用舊命令的 retry payload。
- [ ] idle 且 `current_cmd_id=null` 的合法車端行為不會造成永久卡單。
- [ ] 心跳不能替代 docking／自訂動作的實際完成確認。

## 5. 高優先級：STOP 忽略 activity，可能誤停或誤完成導航

**位置：** `Adapter:507–531`，`_on_rmf_stop()`；`_handle_command_result()`。

**問題：** STOP 不核對傳入的 `activity_id`，也未清除或失效舊 execution。STOP 更新 cmd ID 後，若車端用 STOP 的 ID 回報成功，通用結果處理可能把舊導航當成完成。

**修正方向：** 核對要停止的 activity 與目前 execution；容器內 `ActivityIdentifier` 提供 `is_same()`，需依實際 API 使用。分離停止命令結果與導航／動作成功結果，並取消相關重試。

**驗收：**

- [ ] 過期或不相符 activity 的 STOP 不會影響目前命令。
- [ ] STOP 成功 ACK 不會完成先前導航。
- [ ] STOP 後延遲的完成結果／重試 timer 不會重新推進或啟動舊命令。
- [ ] 正常取消與 RMF 重規劃仍能停止相符的活動。

## 6. 高優先級：導航重試耗盡後缺少收尾與升級處理

**位置：** `Adapter:424–438`，`_handle_command_result()`。

**問題：** 重送兩次後 execution 仍保留，沒有 RMF 問題回報、重規劃或人工介入狀態。`canceled` 也沒有處理分支。

**修正方向：** 定義重試耗盡的明確處理，區分預期取消與非預期取消。核對車端是否接受同 ID 重送，避免與去重規則衝突；重複 failed 訊息不可排出多個並行重試。

**驗收：**

- [ ] 重試耗盡後有可觀察的故障／中斷狀態，而不是靜默保留 execution。
- [ ] 重複失敗訊息不會突破重試上限或重複發送。
- [ ] 取消、離線、替換 execution 後，已排程重試不會發送。
- [ ] `canceled` 能依命令與活動關聯正確處理，不誤判成功。

## 7. 高優先級：後端任務狀態可能顯示假成功

**位置：** `backend/rmf_service.py:120–136, 185–198`，`task_response_cb()`、`fleet_callback()`；`backend/controller/fleet_controller.py:99–133`，`simulation_loop()`。

**問題：**

- RMF 回覆 `success:false` 時沒有判讀 errors；缺少 state 反而預設成 `active`。
- 真實 ROS 車隊更新超過 2 秒沒收到，就啟用模擬進度，真實 active 任務也可能被推到 `completed`。
- 車輛 task ID 從有值變成空值就被推定成功完成，沒有確認是完成、取消或失敗。

**修正方向：** 建立任務狀態的權威來源與終止狀態保護。明確區分送出、受理、執行與終止；讀取目前 RMF 版本提供的任務生命週期通知。模擬模式需顯式啟用並隔離資料，不能因實體通訊逾時自動接管。WebSocket 下派結果也需讓前端收到拒絕／錯誤，不只廣播任務清單。

**驗收：**

- [ ] RMF 拒絕請求時能呈現原因，不能轉成 active。
- [ ] ROS／MQTT 中斷不會讓真實任務靠時間增加進度並完成。
- [ ] 取消與失敗不會因 task ID 清空而變成 completed。
- [ ] 已終止的任務不會被延遲的狀態訊息改回 active。
- [ ] Publisher 未就緒時不宣稱已受理；本地排隊是否會補送需明確實作或明確拒絕。

## 8. 中優先級：任務 ID 容易碰撞

**位置：** `backend/rmf_service.py:251`，`dispatch_task()`。

**問題：** `int(time.time() * 1000) % 100000` 的命名空間每 100 秒循環，同毫秒請求也會生成相同 ID，影響 RMF 請求關聯與本地查找。

**修正方向：** 使用可靠唯一 ID，並明確區分 request ID 與 RMF booking ID；前端應使用後端的識別值，而非各自產生正式任務 ID。

**驗收：**

- [ ] 同毫秒並行請求與相隔 100 秒的請求不會生成同 ID。
- [ ] 不同 request ID／booking ID 能正確關聯同一任務。
- [ ] 結果回報不會更新到其他任務。

## 9. 中優先級：所有任務類型都轉成單輪 patrol

**位置：** `backend/rmf_service.py:271–278`，`dispatch_task()`；`frontend/src/components/DispatchModal.tsx`。

**問題：** `goto`、`patrol`、`delivery` 都送出 `category: patrol, rounds: 1`。UI 的「物料取送」實際只是走訪取／送站點，沒有取貨、卸貨與確認流程；卸貨站點也可不選。

**修正方向：** 保留已驗證可用的 goto 映射。釐清配送需求：若只是雙點移動，UI 應明確標示；若要真正物料配送，需接入對應的 RMF 任務／動作及車端確認，不能只改 category。依選定語意同步校驗 REST／WebSocket 輸入。

**驗收：**

- [ ] UI 任務名稱與實際執行內容一致。
- [ ] 真正配送必須等待取／卸貨確認，不只到站就算完成。
- [ ] 缺少必要站點、未知任務類型及無效站點能明確回報錯誤。
- [ ] 現有 goto 與合法 patrol 下派不退化。

## 10. 中優先級：多車隊路由與管理介面不一致

**位置：** `Adapter:259–266, 535–551`，`_handle_register()`、`_on_rmf_action()`；`backend/controller/adapter_controller.py:102–172`。

**問題：**

- 自訂 ACTION 用 `self.fleet_name` 發送；目前 fleet1／fleet2 的車會被發到預設 `tinyRobot` topic。
- robot 字典及回呼僅以 robot ID 區分，跨車隊同名會混用；未知 fleet 也會 fallback 到第一個 RMF fleet handle。
- UI 的 pause／resume／restart／set-graph 只修改 JSON 與日誌，沒有控制運行中的 Adapter，卻回報已生效。
- Adapter 啟動時各車隊共用同一份 YAML，主要只覆寫 fleet name 與 graph；不能假設 UI 儲存的個別速度、尺寸及其他參數已套用。

**目前狀態（2026-10-10）：**

- Adapter 啟動時已依各車隊的 `robot_radius`、`linear_velocity`、`angular_velocity` 產生 `/tmp/fleet_cfg_<fleet>.yaml`，覆寫 footprint、vicinity 與速度上限；其他參數仍共用基礎 YAML。
- `_handle_register()` 找不到 fleet handle 時會回錯誤，但仍先執行 `self.fleet_handles.get(fleet_name) or self.fleet_handle`，未知 fleet 實際上仍會 fallback 到預設 handle。
- 重新註冊時沿用既有 handle，但沒有核對 fleet 是否相同。

**修正方向：** 導航、STOP、ACTION、遙測與結果統一使用正確 fleet／robot 身分；拒絕未配置的 fleet，而非靜默 fallback。管理操作需有真實執行與 ACK／錯誤回傳；若不能動態切圖，明確標示需重新載入，不要製造成功日誌。切圖或重啟需處理執行中的任務。

**驗收：**

- [ ] fleet1／fleet2 的 ACTION 發往各自的 MQTT topic。
- [ ] 兩個 fleet 使用相同 robot ID 時仍完全隔離。
- [ ] 未配置 fleet 不會被註冊到別的 fleet handle。
- [ ] pause 會影響實際可派遣性；resume／restart／set-graph 成功回應對應真實執行結果。
- [ ] UI 車隊參數與 RMF 實際設定一致，或明確呈現尚未套用／不支援。

## 驗證與交付

- [ ] 為上述成功、失敗、取消、失聯、重連、新舊結果競態及多車隊情境加入隔離測試。
- [ ] 優先使用 fake execution／MQTT publisher 驗證回呼，不要讓測試接觸實車。
- [ ] 協定變更同步更新 `docs/amr_mqtt_interface_spec.md` 與相關說明；不要只修 mock 而漏掉 Adapter 或後端。
- [ ] 需要端到端驗證的項目，使用明確隔離的模擬環境；未驗證的實車相容性需在交付時標示。
- [ ] 修正後勾選對應項目，記錄必要的行為決策及尚未完成的部分。
