# SOP: MIRO

## 目的

輸入供應商的發票資料，與採購訂單（PO）或收貨記錄（GR）進行三方比對（PO-GR-Invoice Verification），產生應付帳款憑證。

## 前提條件

- 已取得 MIRO 過帳授權
- 採購訂單（PO）已建立且已完成收貨（MIGO 已過帳）
- 已取得供應商實體發票，並確認發票金額、日期及稅額
- 已知對應的採購訂單號（PO Number）

## 操作步驟

1. 進入 MIRO：`wnd[0]/tbar[0]/okcd` 輸入 `MIRO`，按 Enter

2. 選擇交易類型：
   - 交易類型（Transaction）：`wnd[0]/usr/subHEADER:SAPLMRMP:0610/cmbRM08M-VORGN` 選 Invoice（發票）或 Credit Memo（貸項）

3. 輸入抬頭基本資料：

   | 欄位 | Element ID | 說明 | 範例 |
   |---|---|---|---|
   | 發票日期（Invoice Date） | `wnd[0]/usr/subHEADER:SAPLMRMP:0610/ctxtRBKP-BLDAT` | 供應商發票日期（必填） | `2026/06/20` |
   | 過帳日期（Posting Date） | `wnd[0]/usr/subHEADER:SAPLMRMP:0610/ctxtRBKP-BUDAT` | 系統過帳日期（必填） | `2026/06/24` |
   | 發票金額（Amount） | `wnd[0]/usr/subHEADER:SAPLMRMP:0610/txtRBKP-WRBTR` | 發票含稅總金額（必填） | `105000.00` |
   | 幣別（Currency） | `wnd[0]/usr/subHEADER:SAPLMRMP:0610/ctxtRBKP-WAERS` | 幣別（必填） | `TWD` |
   | 參考號（Reference） | `wnd[0]/usr/subHEADER:SAPLMRMP:0610/txtRBKP-XBLNR` | 供應商發票號（選填） | `INV-2026-001` |
   | 稅碼（Tax Code） | `wnd[0]/usr/subHEADER:SAPLMRMP:0610/ctxtRBST-MWSKZ` | 進項稅代碼（選填） | `V1` |

4. 輸入參考採購訂單號：
   - 在「PO Reference」頁籤輸入 PO 號碼（`wnd[0]/usr/tabsHEADER_TAB/tabpOB/ssubHEADER_BODY:SAPLMRMP:0630/ctxtRM08M-BELNR`），按 Enter
   - 系統自動帶入已收貨的項目清單

5. 確認項目明細：
   - 核對各行物料、數量、單價是否與發票相符
   - 若有差異，確認是否在允許容差範圍內

6. 確認差額為 0（系統顯示 Balance = 0）

7. 儲存：`wnd[0]/tbar[0]/btn[11]`（Ctrl+S）
   - 狀態列顯示「憑證 xxxxxxxxxx 已過帳於公司代碼 xxxx」

## 常見問題

| 問題 | 原因 | 處理方式 |
|---|---|---|
| 差額不為 0 | 發票金額與 PO/GR 金額不符 | 確認數量或單價差異，在容差範圍內可直接過帳 |
| 找不到 GR 紀錄 | 收貨尚未在 MIGO 過帳 | 請收貨人員先完成 MIGO 過帳 |
| 發票已存在（重複） | 相同參考號已過帳 | 確認參考號，避免重複入帳 |
| 稅額計算不符 | 稅碼設定與發票不一致 | 確認稅碼對應的稅率是否正確 |

-- End of SOP --
