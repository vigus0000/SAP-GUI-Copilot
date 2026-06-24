# SOP: FBL5N

## 目的

依客戶與公司代碼查詢應收帳款明細，可篩選未收、已收或全部項目。

## 前提條件

- 使用者已具備 FBL5N 執行權限
- 已知客戶帳號（KUNNR）及公司代碼（BUKRS）
- SAP GUI 已登入並連線至目標系統

## 操作步驟

1. 進入 FBL5N：在交易碼欄位 `wnd[0]/tbar[0]/okcd` 輸入 `FBL5N`，按 Enter。

2. 輸入選擇條件：

   | 欄位 | Element ID | 說明 | 範例 |
   |---|---|---|---|
   | 客戶帳號（起）KUNNR | `wnd[0]/usr/ctxtDD_KUNNR-LOW` | 客戶代碼起始值（必填） | `2000` |
   | 客戶帳號（迄）KUNNR | `wnd[0]/usr/ctxtDD_KUNNR-HIGH` | 客戶代碼結束值（選填） | `2999` |
   | 公司代碼 BUKRS | `wnd[0]/usr/ctxtDD_BUKRS-LOW` | 公司代碼（必填） | `1000` |
   | 過帳日期（起）BUDAT | `wnd[0]/usr/ctxtDD_BUDAT-LOW` | 查詢日期起始（選填） | `01.01.2024` |
   | 過帳日期（迄）BUDAT | `wnd[0]/usr/ctxtDD_BUDAT-HIGH` | 查詢日期結束（選填） | `31.12.2024` |

3. 選擇項目狀態（必須選擇一項）：

   | 狀態 | Element ID | 說明 |
   |---|---|---|
   | 未結項目（Open items） | `wnd[0]/usr/radX_OPSEL` | 僅顯示尚未清帳的應收項目 |
   | 已清項目（Cleared items） | `wnd[0]/usr/radX_CLSEL` | 僅顯示已清帳的項目（選填） |
   | 所有項目（All items） | `wnd[0]/usr/radX_AISEL` | 顯示全部項目（選填） |

4. 執行查詢：點擊執行按鈕 `wnd[0]/tbar[1]/btn[8]`（或按 F8）。

5. 查看帳款明細：
   - 結果 Grid 顯示憑證號、客戶、金額、到期日、清帳狀態等欄位
   - 結果區 Grid：`wnd[0]/usr/cntlGRID/shellcont/shell`
   - 雙擊任一資料列，可進入該憑證的 FI 文件明細

6. 返回上一畫面：點擊 `wnd[0]/tbar[0]/btn[3]`（或按 F3）。

## 常見問題

| 問題現象 | 可能原因 | 解決方式 |
|---|---|---|
| 找不到客戶 | 客戶代碼輸入錯誤 | 確認客戶代碼，或在起始欄輸入 `*` 使用通配符搜尋 |
| 未顯示應收帳款科目 | 客戶主檔的對帳科目設定異常 | 至客戶主檔（XD03）確認對帳科目（Reconciliation Acct）設定 |
| 查詢結果無資料 | 所選狀態下無符合項目 | 改選「所有項目」重新查詢，或確認日期範圍是否涵蓋目標期間 |
| 無法執行交易碼 | 使用者未授權 | 聯絡系統管理員確認角色授權 |

-- End of SOP --
