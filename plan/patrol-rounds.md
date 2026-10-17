# 巡邏任務：可設定巡邏輪數實作計畫

## 1. 目標與交接範圍

讓使用者在派遣巡邏任務時設定有限的巡邏輪數，經由前端、REST / WebSocket、後端服務傳到 Open-RMF，取代目前固定的 `rounds: 1`。

本文件包含交給其他 agent 的實作計畫及驗收紀錄；目前核心輪數功能已實作並完成模擬測試，仍有待修問題，詳見第 8 節。實作前先閱讀根目錄 `AGENTS.md`；僅做相關變更，不自行 commit。

第一階段範圍：

- 巡邏輪數輸入、驗證與傳遞。
- 指定車輛、自動競標兩種派遣模式。
- 任務資料保存及畫面顯示設定輪數。
- 保留舊客戶端未傳輪數時的一輪行為。

不包含無限巡邏、定時排程、任意多站點編輯、站點停留時間、即時修改執行中輪數、跨重啟任務恢復或自行計算已完成輪數。

## 2. 現況與修改入口

以下為撰寫時確認的程式碼位置；實作時以函式及型別名稱定位，行號可能變動。

| 位置 | 現況 / 實作責任 |
| --- | --- |
| `frontend/src/components/DispatchModal.tsx` | 已有 Patrol 選項及選填第二站；新增輪數輸入、驗證及送出欄位。 |
| `frontend/src/App.tsx` 的 `handleDispatchTask` | 線上以 WebSocket `dispatch_task` 送出；離線建立預覽任務。兩條路徑皆須保留輪數。 |
| `frontend/src/types/rmf.ts` 的 `Task` | 尚無輪數欄位；加入可選 `rounds`，保持舊任務資料相容。 |
| `backend/state.py` 的 `TaskRequest` | 尚無輪數欄位；新增預設值及適當型別驗證。 |
| `backend/controller/fleet_controller.py` | REST `POST /api/tasks/dispatch` 與 WebSocket `dispatch_task` 都呼叫服務；兩個入口都要接線。 |
| `backend/rmf_service.py` 的 `RMFService.dispatch_task` | Patrol 使用 `places: [A]` 或 `[A, B]`，`rounds` 固定為 `1`；Goto 也使用 Patrol category，但必須維持單點一輪。 |
| 同上，建立 `new_task` 的區段 | 在巡邏任務記錄中保存已驗證的輪數，透過現有 tasks 廣播回前端。 |
| `workspace/mqtt_fleet_adapter/mqtt_fleet_adapter.py` | 已註冊 `consider_patrol_requests`；優先沿用 RMF 巡邏執行，不新增本機重派迴圈。 |
| `tests/test_rmf_dispatch_regression.py` | 既有派遣驗證與 mock 測試，可擴充巡邏案例。 |

## 3. 實作前必須釐清：一輪不一定等於一次完整來回

目前能確認的是後端送出 `places` 與 `rounds`，不能僅憑這兩個欄位，認定車輛必然返回第一站。

實作 agent 必須先查證專案實際使用的 RMF 版本之 Patrol schema / event 實作，並在模擬環境確認：

1. `places: [A, B], rounds: 1` 的實際造訪序列與最終停靠點。
2. `places: [A, B], rounds: 2` 的實際造訪序列與最終停靠點。
3. 車輛原本不在 A 時，前往 A 是否算在輪數內。
4. 單站 `places: [A]` 的多輪行為，以及 schema 對輪數的上下界。

本計畫第一階段採用「RMF 原生巡邏輪數」語意，保留既有 `places` 組法；UI 輔助文字必須依查證結果解釋終點，不能把未證實的行為標為「完整來回 N 次」。

若需求必須是 `A → B → A` 算一次完整來回，且原生 `[A, B] + rounds` 不符合，先向使用者確認再決定是否調整站點序列；不能默默加入返回 A、改變一輪任務的既有終點，或用多次獨立派遣假裝同一個巡邏任務。將查證來源及實測序列記錄在交付說明。

## 4. API 契約與相容性

建議欄位名稱統一為 `rounds`，不另加 `patrol_count` 等別名。

REST request：

```json
{
  "type": "patrol",
  "target_waypoint": "A",
  "destination_waypoint": "B",
  "robot_id": "carter1",
  "rounds": 3
}
```

WebSocket 使用相同內容作為 `{"action": "dispatch_task", "payload": ...}` 的 payload。

規則：

- 省略 `rounds` 時預設為 `1`。
- 明確傳入值必須為正整數；拒絕 `null`、布林值、字串、小數、零、負數及其他型別，不能用 `int()` 或 Pydantic 隱式轉型截斷 / 修正。
- 上限先依實際 RMF schema / 執行限制查證；若需要另訂產品上限，先確認決策，再讓 UI、REST、WebSocket、服務層與測試一致，不能各自設定不同上限。
- `rounds` 僅影響 `type: "patrol"`；Goto 仍為單點一輪，Delivery payload 不增加巡邏欄位。非巡邏請求若帶入輪數，不讓它改變原任務語意；明確非法值仍須回報驗證錯誤。
- 保留第二巡邏點選填、現有單站行為及同站去重行為；不要在此次修改強制兩個不同站點。
- 將服務方法的新參數加在既有參數之後，並以 keyword 傳遞，避免破壞現有 positional 呼叫。

REST 可沿用 FastAPI 的模型驗證錯誤回應；WebSocket 必須回傳既有 `dispatch_response` 格式的明確錯誤，且不能因錯誤輸入中斷連線。服務層仍需驗證，避免直接呼叫繞過入口檢查。優先共用簡單的驗證機制，避免三份不同規則。

無效請求不得 publish ROS 任務，也不得新增 queued 任務記錄。

## 5. 實作步驟與依賴

### 步驟一：查證輪數語意

完成第 3 節，確定 UI 用語、終點與邊界。這是後續 payload 與驗收序列的前置條件。

### 步驟二：後端資料流

1. 擴充 `TaskRequest` 與 `RMFService.dispatch_task` 的參數及驗證。
2. REST 與 WebSocket 都傳遞輪數；特別注意目前 WebSocket 手動讀取 payload，不能只改 Pydantic model 就視為完成。
3. 只有 Patrol 分支改用已驗證輪數，指定車輛和自動競標皆沿用相同 description 建構流程。
4. 巡邏任務記錄保存 `rounds`，確認狀態更新及 tasks 廣播不會遺失欄位。
5. 保留現有路網驗證、偏離路網防護、停用車隊檢查、ROS publisher 就緒檢查與取消流程。

### 步驟三：前端輸入與顯示

依賴步驟二的欄位契約。

1. 巡邏模式顯示「巡邏輪數」數字輸入，初始值 `1`，整數步進；提供符合已驗證 RMF 語意的說明。
2. 空值或非法值顯示中文錯誤並阻止提交；不可只靠 HTML `min` / `step`，也不可將空白自動變成 `0` 或回退為 `1`。
3. 切換任務類型時不把巡邏輪數帶入 Goto / Delivery；修改輪數後同步清除過期驗證錯誤。
4. 更新 `DispatchModal` callback 與 `App.tsx` handler 的型別；可在既有 types 檔案共用簡單派遣型別，避免兩處不一致，不要擴大成全站型別重構。
5. 離線預覽任務保留 `rounds`，但不宣稱已模擬真實 RMF 多輪執行。
6. 在既有任務列表 / 任務資訊區找到適合位置，僅針對 Patrol 顯示「設定 N 輪」。舊資料缺少欄位時可按舊契約顯示一輪；不要顯示無來源的「目前第幾輪」。

### 步驟四：測試及執行驗收

依賴前後端實作完成；先跑針對性測試，再做 UI 與模擬整合。

## 6. 必要測試與驗收條件

| 案例 | 必須確認 |
| --- | --- |
| 舊請求省略 `rounds` | Patrol 仍送出 `rounds: 1`，單站 / 雙站行為不變。 |
| 有效輪數 `1`、`3` | ROS `ApiRequest` 的 `request.description.rounds` 完整保留；有已確認上限時補測邊界。 |
| 指定車輛 / 自動競標 | 分別維持 `robot_task_request` / `dispatch_task_request`，輪數都正確。 |
| REST / WebSocket | 有效值、預設值與錯誤值皆涵蓋，不能只測服務方法；WS 錯誤後連線仍可繼續使用。 |
| 非法輪數 | `0`、`-1`、`1.5`、`true`、`"3"`、`null`、物件、陣列皆被拒絕，無 publish、無新增任務。 |
| Goto / Delivery 回歸 | 不因巡邏欄位改變原本 payload 或執行方式。 |
| 單站 / 同站巡邏 | 保留既有站點組法，不默默變成新增的強制雙站規則。 |
| UI 狀態切換 | 非法值無法送出、改正後可送出、切換任務類型不外洩輪數。 |
| 任務顯示與離線預覽 | 設定輪數保留並可見；缺少欄位的舊任務正常顯示。 |
| 真實 RMF 模擬 | 觀察 A / B 造訪序列、最終停靠點及 completed 時機，符合第 3 節查證結果；不是只看 payload 就宣告完成。 |
| 取消多輪巡邏 | 執行中取消後，不再自動啟動後續輪次；沿用既有 RMF 任務取消方式。 |

驗證方式：

- 優先擴充既有 Python `unittest` 測試，以 mock publisher 斷言 JSON payload 及失敗時的副作用。
- 在具備 ROS / `rmf_adapter` 等既有依賴的環境執行新增測試及受影響的派遣測試；勿直接假設宿主機具備所有依賴。依現有測試載入方式選用 `python3 -m unittest discover -s tests -p 'test_rmf_dispatch_regression.py'` 或更小的測試選擇器。
- 前端執行 `cd frontend && npx tsc --noEmit`；目前 `package.json` 沒有 test / lint script，不要假設有，也不為此引入新測試框架。
- UI 操作及截圖驗證先載入專案 `playwright-cli` skill，再使用既有開發環境。
- 依 `AGENTS.md` 使用 HMR 與後端 volume 掛載；不要執行 `npm run build` 或 `docker compose build`。
- 模擬環境無法使用時，明確標示整合驗收尚未完成，不能以 mock 測試取代實際執行結論；不在未經確認下對實車派發巡邏。

## 7. 交付完成標準

以下勾選代表交付內容與驗證紀錄已具備，不代表所有缺陷已修復或所有行為均驗收通過。未完成的修正與補驗項目列於第 8 節。

- [x] 已查證並說明一輪的站點序列、終點與輪數範圍。
- [x] UI、REST、WebSocket、服務層、ROS payload 與任務資料使用一致的 `rounds` 契約。
- [x] 舊請求及 Goto / Delivery 行為維持相容。
- [x] 新增針對性測試，型別檢查通過，已執行 UI 與 RMF 模擬驗收並列出未通過項目。
- [x] 交付說明列出改動檔案、實際執行的驗證、RMF 語意依據及任何未完成項目。
- [x] 不新增無限重派、前端計時派遣、MQTT 自行循環等替代 RMF 調度的邏輯。

## 8. 實作驗收紀錄與待修問題

驗證日期：2026-10-17。最新重驗結論：原列的三項問題與後續發現的 Task API 完成回呼遺漏均已修正並通過針對性驗證；兩輪 Isaac Sim 巡邏、進度顯示與完成終點文案再次實測通過，詳見第 8.7 節。此結論限於已執行的案例，不代表未實測的所有輪數、派遣情境或既有完整回歸問題均已通過。取消後的額外導航不直接判定為缺陷；本次重驗未重新追查其 RMF activity 來源。

### 8.1 已交付檔案

- 後端：`backend/state.py`、`backend/controller/fleet_controller.py`、`backend/rmf_service.py`。
- 前端：`frontend/src/types/rmf.ts`、`frontend/src/components/DispatchModal.tsx`、`frontend/src/App.tsx`。
- 測試：`tests/test_rmf_dispatch_regression.py`。

本輪驗證未修改以上功能程式碼，也未 commit；本文件更新僅記錄驗收與後續工作。

### 8.2 已完成驗證

以下保留首次驗收紀錄；修正後的最新重驗結果見第 8.7 節。

- [x] 前端型別檢查：`docker compose exec -T frontend npx tsc --noEmit` 通過。
- [x] 新增的 7 個巡邏測試全部通過，涵蓋預設輪數、指定車輛 / 自動競標 payload、非法輪數、單站 / 同站、Goto / Delivery 相容性與兩種 API 入口。
- [x] 隔離的 HTTP / WebSocket 傳輸測試通過：18 次非法請求拒絕、4 次有效 / 預設請求成功；WebSocket 錯誤後仍可繼續派遣。使用 mock publisher，未將這些測試請求送往模擬車輛。
- [x] 瀏覽器確認巡邏可送出 `rounds: 3`，任務列表顯示「設定 3 輪」，切換 Goto 不傳送輪數；離線預覽亦保留並顯示輪數。
- [x] 完整派遣回歸測試已執行：39 項中 34 項通過，4 項 failure、1 項 error。以修改前 HEAD 的程式碼及測試在同環境重跑，32 項出現完全相同的 5 項失敗，並非本次輪數修改新增的回歸。
- [x] Isaac Sim 指定車輛實測：`carter1`、`wp_1` / `wp_2`、`rounds: 2`，約 155 秒完成 `wp_1 → wp_2 → wp_1 → wp_2`，完成時停於 `wp_2`，任務狀態為 `completed`。
- [x] Isaac Sim 中途取消已測試並記錄：三輪任務在行進中取消，API 成功、狀態為 `canceled`，未觀察到後續完整巡邏輪次。另觀察到額外導航與回位，需區分 responsive wait 與原巡邏殘留 activity；「取消後立即原地停止」不是啟用 responsive wait 時的巡邏取消驗收標準。
- [x] 驗證結束時兩台模擬車輛均為 idle，沒有留下執行中的驗收任務。

巡邏測試執行方式（將宿主機最新測試送入具備 ROS 依賴的容器）：

```bash
docker compose exec -T backend bash -lc 'source /opt/ros/jazzy/setup.bash && python3 - -k patrol_rounds -v' < tests/test_rmf_dispatch_regression.py
docker compose exec -T backend bash -lc 'source /opt/ros/jazzy/setup.bash && python3 -' < tests/test_rmf_dispatch_regression.py
```

既有 5 項失敗：`test_core_process_logs_inherit_backend_streams`、`test_issue11_1_deregister_and_reboot_invalidation`、`test_issue11_4_offline_command_rejection_reported_to_rmf`、`test_lost_command_resent_with_same_cmd_id`、`test_estop_halts_holds_and_resumes_command`。不在此次巡邏輪數驗收中順手修改。

### 8.3 RMF 語意與驗收邊界

環境使用 `ros-jazzy-rmf-fleet-adapter` 版本 `2.7.2-1noble.20260903.075707`。容器內 `/opt/ros/jazzy/include/rmf_fleet_adapter/schemas/task_description__patrol.hpp` 定義 `rounds` 為 integer、minimum 為 1，描述預設為 1，未宣告 maximum；這不代表任意超大輪數已完成壓力或資源邊界驗證。

此次實測確認 `[A, B] + rounds: 2` 的造訪序列為 `A → B → A → B`，終點是 B，不是 `A → B → A` 算一次完整來回。實測車輛最初已在 A 附近；「從非 A 起點完整執行」、「單站多輪」及「自動競標實際行駛」未另外完成模擬驗收，不能以 payload 測試取代這些執行結論。

**取消與 responsive wait 的語意：** `workspace/mqtt_fleet_adapter/config.yaml` 設定 `rmf_fleet.responsive_wait: true`、`finishing_request: "nothing"`；Adapter 在車輛 session 定位就緒後，會呼叫 `enable_responsive_wait(...)` 套用設定。容器內 `/opt/ros/jazzy/include/rmf_fleet_adapter/agv/RobotUpdateHandle.hpp` 對此 API 的說明是：idle 車輛仍留在交通排程中，會與其他交通參與者協商，必要時可能移動讓路。因此取消巡邏不等於禁止一切後續導航，`finishing_request: "nothing"` 也不會停用 responsive wait。驗收應確認被取消任務不再執行剩餘輪次；不能僅因取消後出現 `navigate` 就判定取消失敗，也不應為了要求原地不動而關閉正常交通協商。

### 8.4 待修問題與補驗標準

- [x] **巡邏進度不代表完整任務。** 已解耦巡邏進度與首站單點距離。在 `backend/rmf_service.py` 中，巡邏任務不再沿用單點首站距離推算進度，執行中狀態與文字標記為「巡邏執行中」，避免車輛剛抵達第一站就跳至 98%；僅在 RMF 真正回報任務完成（或車輛完成所有排程後）才更新為 100%。前端 `App.tsx` 任務卡片於巡邏執行中顯示「巡邏執行中」與脈衝進度條，完成後明確顯示 100%。已在 `tests/test_rmf_dispatch_regression.py` 新增 `test_patrol_progress_does_not_jump_to_98_at_first_station` 通過驗證。
- [x] **完成訊息遺漏路徑已補齊並通過重驗。** `_format_task_completion_detail(task)` 已補齊至 `backend/rmf_service.py:474` 的 `task_response_cb`（Task API `state.status: "completed"` 分支），與 `task_summary_cb`、任務交接及 idle fallback 共用完成文案，並同步更新步驟狀態。新增回歸測試 `TestRMFDispatchRegression.test_task_response_cb_patrol_completion_path` 涵蓋雙站兩輪、單站巡邏及物料配送的 Task API 完成回呼，已由本輪重跑確認通過；上次失敗的獨立回呼重現腳本亦已通過，詳見第 8.7 節。
- [x] **非法輪數未顯示中文錯誤提示。** `frontend/src/components/DispatchModal.tsx` 已新增欄位即時互動驗證：當輪數空白時即時顯示「⚠️ 請輸入巡邏輪數！」；當輪數為 `0`、`-1`、`1.5` 等非法值時即時顯示「⚠️ 巡邏輪數必須為大於 0 的正整數！」，並搭配醒目的紅色邊框與焦點樣式，同時保持「立即派遣任務」按鈕為停用（disabled）；改正為有效正整數（如 `2`）後錯誤提示即時清除且按鈕恢復可用；切換任務類型至 Goto / Delivery 時輪數欄位與提示自動隱藏不外洩。已使用 `playwright-cli` 進行端對端瀏覽器操作驗收通過。

**待確認的正常機制，非已確認缺陷：**

- [x] **確認取消後導航是否來自 responsive wait。**
  - **查證依據**：
    1. `workspace/mqtt_fleet_adapter/config.yaml` 明確設定 `responsive_wait: true`，且 Fleet Adapter 透過 `RobotUpdateHandle::enable_responsive_wait(true)` 啟用 RMF 原生待命交通協商。
    2. 查閱 ROS 2 Jazzy 容器標頭檔 `/opt/ros/jazzy/include/rmf_fleet_adapter/agv/RobotUpdateHandle.hpp`：`enable_responsive_wait` 語意為「idle 車輛仍留在交通排程中，會與其他交通參與者協商，必要時移動讓路／駛離通道」。
    3. 追查 `patrol-cancel.jsonl` 與 Adapter 紀錄：操作員取消任務時，RMF 立即發出 `stop`（`cmd_id: 13`），Adapter 將該巡邏任務 detached，任務狀態變為 `canceled`，車輛 `task_id` 清空且狀態為 `idle`（在線待命中）。
    4. 3 毫秒後 RMF 交通調度器下發的 `navigate` 至 `wp_2`（`cmd_id: 14`），係因車輛停於航道中間，RMF 協商機制將 idle 車輛自動導航至最近的合法航路點 `wp_2` 清空航道，屬於獨立的待命／通道清空行為。
    5. 車輛抵達 `wp_2` 後始終維持在線待命（`idle`），未曾重啟任何後續巡邏輪次。
  - **結論**：已確認取消後的 `navigate` 導航來自 Open-RMF 正確的 responsive wait 交通協商機制，而非被取消巡邏的殘留執行；結案不變更交通排程設定。

### 8.5 驗收證據

本次紀錄保留於本機 session artifacts，非 repository 交付檔案；其他環境接手時需另行取得或重跑：

`/home/yu/.copilot/session-state/7184475d-301f-40d9-8c38-d1a130dfc5de/files/`

- `patrol-targeted.log`：7 個巡邏測試結果。
- `patrol-regression.log`、`patrol-baseline.log`：修改後與修改前完整回歸結果。
- `patrol-live.jsonl`：兩輪巡邏請求、位置、造訪序列、任務狀態及 MQTT 紀錄。
- `patrol-cancel.jsonl`：中途取消請求、取消後 30 秒遙測與 MQTT 紀錄；之後返回 `wp_2` 的位置由另一次 `/api/robots` 查詢觀察到，未包含在該檔案的 30 秒採樣內。
- `patrol-live-results.png`：監控畫面的任務輪數、完成與取消狀態。
- `patrol-recheck-targeted.log`：修正後 9 個巡邏測試結果。
- `patrol-recheck-regression.log`：修正後完整回歸結果，41 項中 36 項通過，其餘為原有的 5 項失敗。
- `patrol-recheck-live.jsonl`、`patrol-recheck-results.png`：修正後兩輪巡邏遙測、終點文案與完成畫面。
- `verify-patrol-completion-callback.py`、`patrol-recheck-completion-path.log`：Task API 完成回呼的隔離重現腳本與失敗結果，未向實際 ROS 發布請求。
- `patrol-final-targeted.log`：最後修正後 10 個巡邏相關測試全部通過。
- `patrol-final-completion-path.log`：原本失敗的獨立 Task API 完成回呼重現腳本已通過。
- `patrol-final-live.jsonl`、`patrol-final-results.png`：最後修正後的兩輪巡邏遙測、MQTT 紀錄與完成畫面。

### 8.6 修正後重驗結果

重驗時間：2026-10-17 約 21:35–21:41。本次僅驗證，未修改功能程式碼。

- [x] 9 個巡邏測試與 TypeScript 檢查通過；完整回歸 41 項中 36 項通過，其餘仍為首次驗收已確認的 5 項既有失敗。
- [x] 隔離 HTTP / WebSocket 測試再次通過：18 次非法請求拒絕、4 次有效 / 預設請求成功。
- [x] 瀏覽器確認空白、`0`、`-1`、`1.5` 均即時顯示中文錯誤並停用提交；改成 `2` 後提示消失且可提交，切換 Goto / Delivery 不外洩欄位與提示。
- [x] Isaac Sim：`carter2` 約 150 秒完成 `wp_12 → charger_1 → wp_12 → charger_1`。執行中後端進度維持 5 並顯示「巡邏執行中」，不再依首站距離跳到 98%；完成後為 100%，後端與 UI 均顯示「巡邏任務完成（共 2 輪，已抵達終點 charger_1）」。
- [x] 驗證結束時兩台車均為 idle，沒有留下執行中的驗收任務。
- [x] 本輪發現的 Task API `completed` 回呼文案遺漏，已在後續修正並於第 8.7 節重驗通過；本輪當時失敗的紀錄保留在 `patrol-recheck-completion-path.log`。

### 8.7 最後回呼修正後的實際重驗

重驗時間：2026-10-17 約 21:52–21:56。本次未修改功能程式碼，僅更新驗收紀錄；未 commit。

- [x] `docker compose exec -T frontend npx tsc --noEmit` 通過。
- [x] 使用 `python3 - -k patrol -v` 在 ROS 容器內執行最新測試檔，10 個巡邏相關測試全部通過，包含新增的 Task API 完成回呼測試。
- [x] 重新執行先前失敗的 `verify-patrol-completion-callback.py`：相同 `completed` 回應現在會同步更新完成狀態、100% 及「巡邏任務完成（共 2 輪，已抵達終點 charger_1）」文案。
- [x] Isaac Sim 指定 `carter2`、兩站兩輪，約 145 秒完成 `charger_1 → wp_12 → charger_1 → wp_12`，最後停在 `wp_12` 附近，任務為 `completed`。
- [x] 行進中後端進度維持 5、文字為「巡邏執行中」，瀏覽器顯示脈衝進度條且沒有誤導的 98%；完成後顯示 100%、移除脈衝，後端與 UI 文案均為「巡邏任務完成（共 2 輪，已抵達終點 wp_12）」。
- [x] 驗證結束時兩台車均為 idle，沒有留下執行中的驗收任務。

本輪針對最後新增的回呼分支修正執行驗證，未重跑完整回歸、取消或 responsive wait 來源追蹤；完整回歸的既有 5 項失敗仍以第 8.6 節紀錄為準，不能視為本輪已修復。
