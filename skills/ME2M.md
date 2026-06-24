# SOP: ME2M

## 目的
依物料查詢採購訂單清單，列出指定物料在特定期間的所有 PO，方便確認採購進度與在途數量。

## 前提條件
- 已登入 SAP GUI，具有 ME2M 執行權限。
- 知道要查詢的物料號碼（Material Number）。

## 操作步驟

### 一、進入 ME2M

1. 進入交易 ME2M
   - 在 T-Code 欄位（`wnd[0]/tbar[0]/okcd`）輸入 `ME2M`，按 Enter。
   - 系統進入「依物料的採購訂單」選擇畫面。

### 二、輸入查詢條件

2. 輸入選擇條件

   | 欄位 | Element ID | 說明 | 範例 |
   |---|---|---|---|
   | 物料（Material） From | `wnd[0]/usr/ctxtKORG-MATNR` | 物料號起始（必填） | `MAT-001` |
   | 物料（Material） To | `wnd[0]/usr/ctxtKORG-MATNR2` | 物料號結束（選填） | `MAT-999` |
   | 工廠（Plant） | `wnd[0]/usr/ctxtKORG-WERKS` | 收料工廠（選填） | `1000` |
   | 採購組織（Purch. Org.） | `wnd[0]/usr/ctxtKORG-EKORG` | 採購組織（選填） | `1000` |
   | 文件日期 From | `wnd[0]/usr/ctxtKORG-BEDAT` | PO 建立日期起始（選填） | `2026/01/01` |
   | 文件日期 To | `wnd[0]/usr/ctxtKORG-BEDAT2` | PO 建立日期結束（選填） | `2026/12/31` |
   | 範圍（Scope of List） | `wnd[0]/usr/ctxtMEIN-UMSON` | B=未結項目、A=全部（選填） | `B` |

3. 設定顯示範圍（選填）
   - `B`：僅顯示尚有未結數量的 PO 項目。
   - `A`：顯示所有 PO（含已全數收貨者）。

### 三、執行與查看結果

4. 執行查詢
   - 按 F8（`wnd[0]/tbar[1]/btn[8]`）執行。

5. 查看採購訂單清單
   - ALV 清單顯示各 PO 的物料、供應商、採購數量、已收貨量、未結量、交期。
   - 結果 Grid：`wnd[0]/usr/cntlGRID/shellcont/shell`
   - 雙擊任一行可進入 ME23N 查看 PO 明細。

6. 返回
   - 按 F3（`wnd[0]/tbar[0]/btn[3]`）返回。

## 常見問題

| 問題 | 原因 | 處理方式 |
|---|---|---|
| 查詢結果空白 | 物料號錯誤或無符合的 PO | 確認物料號，或改用 * 通配搜尋 |
| 找不到某筆 PO | Scope 設為 B 但 PO 已全數收貨 | 改為 Scope A 重新查詢 |
| 物料號不知道 | 需先查物料主檔 | 使用 MM03 或 F4 搜尋物料說明 |
| 在途數量不正確 | GR 尚未過帳 | 確認收貨人員是否已在 MIGO 完成過帳 |

-- End of SOP --
