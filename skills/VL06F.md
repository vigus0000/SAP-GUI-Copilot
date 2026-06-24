# SOP: VL06F

## 目的

依條件查詢出貨單清單，監控待揀料、待發貨、已發貨等各狀態的交貨單，並可批次執行發貨過帳作業。

## 前提條件

- 已取得 VL06F 交易代碼的執行權限
- 了解欲查詢的出貨點代碼或客戶代碼

## 操作步驟

1. 進入 VL06F：在交易代碼輸入框 `wnd[0]/tbar[0]/okcd` 輸入 `VL06F`，按 Enter。

2. 畫面分頁說明：

   - 「待發貨（For Goods Issue）」頁籤：顯示已揀料、尚未執行發貨過帳的交貨單。
   - 「已發貨（Goods Issue）」頁籤：顯示已完成發貨過帳的交貨單。
   - 「待揀料（For Picking）」頁籤：顯示尚未完成揀料確認的交貨單。
   - 點選對應頁籤切換查詢範圍。

3. 輸入篩選條件（以「待發貨（For Goods Issue）」頁籤為例）：

   | 欄位 | Element ID | 說明 | 範例 |
   |------|-----------|------|------|
   | 出貨點（Shipping Point） | `wnd[0]/usr/tabsTABSTRIP/tabpFI/ssubTAB_BODY:VL:0210/ctxtSD_VSTEL-LOW` | 選填，限定查詢的出貨點 | `SH01` |
   | 計畫發貨日 From（Planned GI Date From） | `wnd[0]/usr/tabsTABSTRIP/tabpFI/ssubTAB_BODY:VL:0210/ctxtSD_WADAT-LOW` | 選填，計畫發貨日起始範圍 | `2026/07/01` |
   | 計畫發貨日 To（Planned GI Date To） | `wnd[0]/usr/tabsTABSTRIP/tabpFI/ssubTAB_BODY:VL:0210/ctxtSD_WADAT-HIGH` | 選填，計畫發貨日結束範圍 | `2026/07/31` |
   | 客戶（Ship-to Party） | `wnd[0]/usr/tabsTABSTRIP/tabpFI/ssubTAB_BODY:VL:0210/ctxtSD_KUNNR-LOW` | 選填，限定送貨方客戶代碼 | `CUST001` |

4. 執行查詢：點選執行按鈕 `wnd[0]/tbar[1]/btn[8]`（或按 F8）。

5. 查看清單與批次作業：

   - 結果以 ALV 格式顯示交貨單號、客戶名稱、物料、交貨數量、計畫發貨日、目前狀態。
   - 雙擊單筆交貨單行，系統跳轉至 VL02N 進行修改。
   - 批次過帳：在清單中勾選（點選最左側選取框）多筆交貨單，點選「過帳發貨（Post Goods Issue）」批次執行。

6. 返回選擇畫面：點選返回按鈕 `wnd[0]/tbar[0]/btn[3]`（或按 F3）。

## 常見問題

| 問題 | 原因 | 處理方式 |
|------|------|---------|
| 清單未顯示某張交貨單 | 出貨點或日期範圍不符，或交貨單狀態與選擇的頁籤不一致 | 確認出貨點及日期範圍後重新查詢，或切換至對應的狀態頁籤（如「已發貨」頁籤） |
| 無法批次過帳，部分交貨單略過 | 被略過的交貨單揀料數量尚未確認 | 進入 VL02N 逐筆確認揀料數量後，再回 VL06F 重新批次過帳 |
| 批次過帳後仍顯示在待發貨清單 | 部分交貨單過帳失敗（庫存不足或其他錯誤） | 查看系統訊息日誌，針對失敗的交貨單個別在 VL02N 處理後重試 |

-- End of SOP --
