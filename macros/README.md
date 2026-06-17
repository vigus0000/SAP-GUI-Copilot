# SAP Macro 標準格式規則

本文件定義 `macros/*.md` 的標準格式。Macro 的目標是加速 Auto / Study 的穩定流程：先掃描目前 SAP 畫面，完整匹配起始條件與元件，再依結構化步驟執行。若 Macro 缺資料或匹配失敗，必須能安全停止或回退，不應影響原本 Auto / Study / Ask / Solve 功能。

## 適用範圍

Macro 適合：

- T-Code 固定、流程穩定、欄位位置可預期的查詢或顯示流程。
- 每次只有少量動態值不同，例如物料號、訂單號、日期、公司代碼、工廠。
- 可用 SAP GUI 元件 ID 清楚定位的欄位、checkbox、radio、button、table row 或 popup。

Macro 不適合：

- 高風險提交流程，例如過帳、刪除、核准、正式儲存，除非另有人工確認設計。
- 畫面流程高度依資料而變化，且尚未有足夠 recording 或 live evidence。
- 元件 ID 未確認，只靠猜測欄位名稱的流程。

## 檔案命名

- 一個 Macro 一個 Markdown 檔。
- 檔名使用小寫 snake_case，例如 `mmbe_stock_overview.md`、`va03_display_order.md`。
- 檔名建議格式：`<tcode>_<intent>.md`。
- `name` 必須與檔名一致或高度一致，方便自然語言匹配與 audit。

## 必要結構

每份 Macro 必須包含：

1. YAML front matter
2. `# Macro: <name>`
3. `## Inputs`
4. `## Start`
5. `## Steps`
6. `## End`

缺少 `## Start` 或 `## End` 時，Macro 不應直接執行，避免在錯誤畫面寫入資料。

## Front Matter

```md
---
name: mmbe_stock_overview
description: MMBE 庫存總覽，輸入物料後顯示庫存概況
mode: both
tags: MM,MMBE,Stock,Material
aliases: 查看物料庫存,查物料庫存,顯示物料庫存,物料庫存,庫存概況,MMBE
---
```

欄位規則：

| 欄位 | 必填 | 規則 |
|---|---:|---|
| `name` | 是 | Macro 穩定識別碼，使用小寫 snake_case |
| `description` | 是 | 用一句話說明用途，供 `/macros` 與 router 使用 |
| `mode` | 是 | `auto`、`study`、`both` 其中之一 |
| `tags` | 建議 | 以逗號分隔，至少包含模組、T-Code、流程類型 |
| `aliases` | 建議 | 常見自然語言觸發詞，以逗號分隔 |

`aliases` 會影響 Auto pre-route。常見說法要放進去，例如「查看物料庫存」「顯示所有庫存」「查請款文件」。

## Inputs

`## Inputs` 定義執行時可由使用者或自然語言抽取的動態值。

```md
## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| material | 物料 |  | true | 要查詢的物料號 |
| plant | 工廠 |  | false | 工廠 |
| execute_key | 執行按鍵 | Execute | false | 若要直接執行，使用 Execute |
```

欄位規則：

| 欄位 | 必填 | 規則 |
|---|---:|---|
| `name` | 是 | 使用 ASCII snake_case，例如 `material`、`sales_order` |
| `label` | 是 | 使用者看得懂的中文欄位名稱 |
| `default` | 否 | 預設值；空白代表無預設 |
| `required` | 是 | `true` 或 `false` |
| `description` | 建議 | 說明值的用途與格式 |

動態值在 steps 中用 `{{input_name}}` 引用，例如 `{{material}}`。若要在步驟內設定 fallback，可用 `{{view_row|default=0}}`。

可選輸入對應的 step 應設定 `skip_if_empty=true`。不要用空字串覆蓋 SAP 既有值。

## Start

`## Start` 用來確認 Macro 可以從目前畫面開始執行。

```md
## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| element | wnd[0]/tbar[0]/okcd |  | true | 必須在 SAP 主視窗 |
```

支援的 condition：

| condition | 用途 |
|---|---|
| `tcode` | 確認目前交易碼 |
| `screen_number` | 確認目前 screen number |
| `title` | 完整比對畫面標題 |
| `title_contains` | 標題包含指定文字 |
| `active_window` | 確認目前視窗，例如 `wnd[0]` 或 `wnd[1]` |
| `element` | 確認指定元件存在 |
| `status_text` | 確認狀態列文字 |
| `status_type` | 確認狀態列訊息類型 |

注意：

- `wnd[0]/tbar[0]/okcd` 是全域命令欄，某些 MCP 掃描不會回傳工具列元素。若 active window 仍是 `wnd[0]`，執行器可容忍 okcd 掃不到。
- Start 條件應確認「可安全開始」，不要把結果畫面的欄位當成選擇畫面的 Start 條件。

## Steps

`## Steps` 是 Macro 的核心。每一列代表一個可驗證、可執行的 GUI 動作。

建議完整欄位：

```md
## Steps
| step | action | element_id | value | label | element_type | skip_if_empty | alternate_ids | expected_label | expected_text | expected_value | description |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | MMBE | 進入 MMBE | GuiOkCodeField | false |  |  |  |  | 開啟庫存總覽 |
| 2 | input | wnd[0]/usr/ctxtMS_MATNR-LOW | {{material}} | 物料 | GuiCTextField | false | wnd[0]/usr/ctxtMATNR-LOW;wnd[0]/usr/ctxtS_MATNR-LOW |  |  |  | 填入物料 |
| 3 | input | wnd[0]/usr/ctxtMS_WERKS-LOW | {{plant}} | 工廠 | GuiCTextField | true | wnd[0]/usr/ctxtWERKS-LOW |  |  |  | 填入工廠 |
| 4 | key |  | {{execute_key}} | 執行 |  | true |  |  |  |  | 顯示結果 |
```

欄位規則：

| 欄位 | 必填 | 規則 |
|---|---:|---|
| `step` | 是 | 從 1 開始的連續整數 |
| `action` | 是 | 見下方 action 清單 |
| `element_id` | 視 action | 使用 SAP GUI short id，例如 `wnd[0]/usr/...`，不要寫 `/app/con[0]/ses[0]/...` |
| `value` | 視 action | 可為固定值或 `{{input}}` |
| `label` | 是 | 使用者可理解的欄位名，也是 self-healing 重要線索 |
| `element_type` | 建議 | 例如 `GuiCTextField`、`GuiCheckBox`、`GuiRadioButton` |
| `skip_if_empty` | 建議 | 可選欄位必須設為 `true` |
| `alternate_ids` | 否 | 以 `;` 分隔的候選 ID，只放同一畫面用途相同的欄位 |
| `expected_label` | 否 | 執行前用於嚴格比對 label |
| `expected_text` | 否 | 執行前用於嚴格比對 text |
| `expected_value` | 否 | 執行前用於嚴格比對目前值 |
| `description` | 建議 | 簡短描述此步用途 |

支援的 action：

| action | 用途 | 備註 |
|---|---|---|
| `tcode` | 切換交易碼 | 優先走 `sap_execute_transaction`，必要時 fallback 到 OKCode + Enter |
| `input` / `set_field` | 輸入文字欄位 | 可批次化執行 |
| `checkbox` | 勾選或取消 checkbox | `value` 使用 `true` / `false` |
| `radio` | 選取 radio | 通常只記錄要變成 `true` 的選項 |
| `combo` | 選擇下拉選單 | `value` 應是實際 key 或可選文字 |
| `click` | 點擊按鈕 | 高風險按鈕需避免自然語言自動觸發 |
| `tab` | 切換頁籤 | 用於穩定 tabstrip |
| `key` | 發送按鍵 | 常用 `Enter`、`F8`、`Execute` |
| `popup` | 處理一般 popup | 用於確認、取消或關閉 |
| `table_row` | 選取一般表格列 | `value` 通常是 row index |
| `popup_table_confirm` | 選 popup table row 並確認 | 適合 MM03 選擇檢視 |
| `textedit` | 編輯 SAP text editor / GuiShell | 僅在已驗證可寫時使用 |
| `focus` | 將游標移到欄位 | 常用於 Study 引導 |
| `wait` | 等待畫面穩定 | 只用於必要等待，不應濫用 |

## End

`## End` 用來確認 Macro 已達到目標狀態。

```md
## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | MMBE | true | 應停在 MMBE |
| title_contains |  | 庫存 | false | 結果畫面通常包含庫存 |
```

End 條件應盡量描述「業務目標已達成」，例如：

- T-Code 正確。
- 畫面標題包含清單、顯示、總覽等結果關鍵字。
- screen number 符合結果畫面。
- 結果畫面出現指定物料、單號或付款人。

不要只用 `tcode` 當唯一 End 條件處理複雜流程；至少補一個 title 或 screen condition。

## alternate_ids 規則

`alternate_ids` 是自我修復的重要基礎，但不能亂放。

可以放：

- 同一 T-Code、同一畫面角色、同一業務欄位的候選 ID。
- 從 `/macro learn recordings`、`/macro doctor`、成功 live run 得到的候選 ID。

不可以放：

- 結果畫面的顯示欄位混入選擇畫面的輸入欄位。
- 類似但語意不同的欄位，例如付款人與售達方、工廠與公司代碼。
- 未在實際 SAP 畫面驗證過的猜測 ID。

範例：

```md
| 2 | input | wnd[0]/usr/ctxtMS_MATNR-LOW | {{material}} | 物料 | GuiCTextField | false | wnd[0]/usr/ctxtMATNR-LOW;wnd[0]/usr/ctxtS_MATNR-LOW | 填入物料 |
```

若 MMBE 結果畫面出現 `wnd[0]/usr/ctxtIO_MATERIAL`，它應作為「已達成狀態」或 result evidence，不應混入上方選擇畫面物料欄位的 `alternate_ids`。

## 自我修復與寫回

相關設定：

```env
SAP_MACRO_LEARNING_ENABLED=true
SAP_MACRO_AUTO_WRITEBACK=true
SAP_MACRO_LEARNED_INDEX=macros/_learned_index.json
SAP_MACRO_BACKUP_DIR=macros/_backups
SAP_MACRO_RESOLVE_MIN_CONFIDENCE=0.85
SAP_MACRO_PROMOTE_PRIMARY_AFTER=3
```

行為規則：

- primary `element_id` 找不到時，執行器會依序嘗試 `alternate_ids`、learned IDs、recording evidence、live MCP elements 與 label/name/type scoring。
- 成功使用替代 ID 後，會先寫入 `alternate_ids`，不會立刻覆蓋 primary `element_id`。
- 同一替代 ID 連續成功達 `SAP_MACRO_PROMOTE_PRIMARY_AFTER` 次後，才可提升為 primary。
- 寫回前會在 `macros/_backups/` 建立備份。
- evidence 會記錄到 `macros/_learned_index.json`。
- `_backups/` 與 `_learned_index.json` 是執行產物，通常不應 commit。

## Recording 學習

使用：

```text
/macro learn recordings
```

規則：

- 從 `recordings/*.json` 抽取穩定欄位 ID。
- 過濾逐字輸入的中間值，例如 `V`、`VF`、`VF0`，只保留最後穩定值。
- T-Code 欄位標準化為 `wnd[0]/tbar[0]/okcd`。
- Recording 是輔助 evidence，不是唯一真相；live MCP 成功結果優先。

## 新增 Macro 流程

建議流程：

1. 先用 SAP GUI 手動操作一次，確認 T-Code、欄位、必要 popup 與結果畫面。
2. 若可能，使用 `/record <流程名>` 錄製一份 recording。
3. 新增 `macros/<name>.md`，只填已確認的 primary ID。
4. 對可選欄位設定 `skip_if_empty=true`。
5. 執行 `/macro doctor <name>`，確認目前畫面可匹配哪些 step。
6. 用 `/macro run <name> key=value ...` 在測試資料上執行。
7. 成功後再測自然語言 Auto pre-route。
8. 若有自動寫回，檢查 md diff，確認 learned ID 沒有污染錯誤畫面。

## 標準模板

```md
---
name: example_macro
description: 一句話說明此 Macro 的用途
mode: both
tags: MM,T-CODE,Intent
aliases: 常見說法1,常見說法2,T-CODE
---

# Macro: example_macro

## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| material | 物料 |  | false | 物料號 |
| plant | 工廠 |  | false | 工廠 |
| execute_key | 執行按鍵 |  | false | 若要直接執行，傳 Execute |

## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| element | wnd[0]/tbar[0]/okcd |  | true | 必須在 SAP 主視窗 |

## Steps
| step | action | element_id | value | label | element_type | skip_if_empty | alternate_ids | expected_label | expected_text | expected_value | description |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | TCODE | 進入 TCODE | GuiOkCodeField | false |  |  |  |  | 開啟交易 |
| 2 | input | wnd[0]/usr/ctxtFIELD-LOW | {{material}} | 物料 | GuiCTextField | true |  |  |  |  | 填入物料 |
| 3 | input | wnd[0]/usr/ctxtWERKS-LOW | {{plant}} | 工廠 | GuiCTextField | true |  |  |  |  | 填入工廠 |
| 4 | key |  | {{execute_key}} | 執行 |  | true |  |  |  |  | 若提供 Execute 則送出 |

## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | TCODE | true | 應停在指定 T-Code |
| title_contains |  | 結果 | false | 若已執行，畫面應進入結果頁 |
```

## 品質檢查清單

提交 Macro 前確認：

- Front matter 有 `name`、`description`、`mode`、`tags`、`aliases`。
- `## Start`、`## Steps`、`## End` 都存在。
- `step` 是連續整數。
- `element_id` 使用 `wnd[0]/...` 或 `wnd[1]/...` short id。
- 可選欄位都有 `skip_if_empty=true`。
- 動態值使用 `{{input_name}}`，不要把物料號、訂單號、日期硬寫死。
- `label` 是使用者能理解的欄位名稱。
- `alternate_ids` 只包含同畫面、同用途欄位。
- End 條件能驗證目標狀態，不只是流程中間狀態。
- 高風險 action 不會被自然語言誤觸。

## 常見錯誤

- 把結果畫面欄位當成選擇畫面 input 欄位。
- 將 `/app/con[0]/ses[0]/...` raw id 寫進 macro。
- 沒有 `skip_if_empty=true`，導致空白值覆蓋 SAP 預設值。
- `aliases` 太少，Auto pre-route 無法命中。
- `aliases` 太泛，例如只寫「查詢」，導致錯誤命中。
- 對未驗證欄位硬加 guessed `alternate_ids`。
- End 條件太弱，Macro 已失敗卻被判定完成。
