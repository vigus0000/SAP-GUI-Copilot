# SOP: ME2L

## 目的
依供應商查詢採購訂單清單，列出指定供應商在特定期間的所有 PO，方便核對交期與未結金額。

## 前提條件
- 已登入 SAP GUI，具有 ME2L 執行權限。
- 知道要查詢的供應商代碼（Vendor）。

## 操作步驟

### 一、進入 ME2L

1. 進入交易 ME2L
   - 在 T-Code 欄位（`wnd[0]/tbar[0]/okcd`）輸入 `ME2L`，按 Enter。
   - 系統進入「依供應商的採購訂單」選擇畫面。

### 二、輸入查詢條件

2. 輸入選擇條件

   | 欄位 | Element ID | 說明 | 範例 |
   |---|---|---|---|
   | 供應商（Vendor） From | `wnd[0]/usr/ctxtKORG-LIFNR` | 供應商代碼起始（必填） | `1000` |
   | 供應商（Vendor） To | `wnd[0]/usr/ctxtKORG-LIFNR2` | 供應商代碼結束（選填，留空只查單一供應商） | `1999` |
   | 採購組織（Purch. Org.） | `wnd[0]/usr/ctxtKORG-EKORG` | 採購組織代碼（選填） | `1000` |
   | 採購群組（Purch. Group） | `wnd[0]/usr/ctxtKORG-EKGRP` | 採購群組（選填） | `001` |
   | 文件日期 From | `wnd[0]/usr/ctxtKORG-BEDAT` | PO 建立日期起始（選填） | `2026/01/01` |
   | 文件日期 To | `wnd[0]/usr/ctxtKORG-BEDAT2` | PO 建立日期結束（選填） | `2026/12/31` |
   | 範圍（Scope of List） | `wnd[0]/usr/ctxtMEIN-UMSON` | 顯示範圍：B=未結項目、A=全部（選填） | `B` |

3. 設定顯示範圍（選填）
   - 在「Scope of List」欄輸入 `B` 僅顯示未結項目，輸入 `A` 顯示所有 PO。
   - 若要同時篩選工廠，可輸入工廠代碼（`wnd[0]/usr/ctxtKORG-WERKS`）。

### 三、執行與查看結果

4. 執行查詢
   - 按 F8 或工具列執行按鈕（`wnd[0]/tbar[1]/btn[8]`）。

5. 查看採購訂單清單
   - 結果以 ALV 清單顯示，欄位包含：PO 號碼、採購日期、供應商、物料、數量、已收貨量、未結金額。
   - 結果 Grid：`wnd[0]/usr/cntlGRID/shellcont/shell`
   - 雙擊任一 PO 行可跳至 ME23N 查看明細。

6. 返回
   - 按 F3（`wnd[0]/tbar[0]/btn[3]`）返回上層。

## 常見問題

| 問題 | 原因 | 處理方式 |
|---|---|---|
| 查詢結果空白 | 供應商代碼錯誤或無符合條件的 PO | 確認供應商代碼，或放寬日期範圍 |
| 結果筆數過多 | 供應商範圍過大 | 縮小供應商範圍或加入日期篩選 |
| 看不到已交貨的 PO | Scope of List 設為 B（僅未結） | 改為 A（全部）重新執行 |
| 「無授權」錯誤 | 缺少採購組織授權 | 請 SAP 管理員授與 M_BEST_WRK 權限 |

-- End of SOP --
