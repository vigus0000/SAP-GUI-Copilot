# SOP: MIGO（採購單收貨）

## 目的
說明如何在 SAP GUI 使用交易 MIGO 對採購單進行「收貨（Goods Receipt）」處理，包含確認主要欄位、載入採購單、檢視並過帳收貨。

## 前提條件
- 已登入 SAP GUI 且有 MIGO 使用權限。
- 已知欲收貨之採購單號（範例：4500004223）。

## 操作步驟
1. 進入交易 MIGO  
   - 在 SAP 主畫面 T‑Code 欄位（wnd[0]/tbar[0]/okcd）輸入 "MIGO"，按 Enter 進入。  
   - ※（錄製事件顯示交易為 MIGO）

2. 確認「動作（Action）」為 收貨（A01）  
   - 位置：上方第一列的動作下拉選單。  
   - 元件 ID：wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/cmbGODYNPRO-ACTION  
   - 操作：確認下拉值為 "A01 收貨"（若不是，請從下拉選擇 A01）。  
   - 註：若畫面已預設為 A01，則直接沿用即可。

3. 確認「參考文件類型（Reference document）」為 採購單（R01）  
   - 元件 ID：wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/cmbGODYNPRO-REFDOC  
   - 操作：確認下拉值為 "R01 採購單"（若不是，請從下拉選擇 R01）。  
   - 註：若畫面已預設為 R01，則直接沿用即可。

4. 輸入或確認「採購單號（Purchase Order）」  
   - 元件 ID：wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/ctxtGODYNPRO-PO_NUMBER  
   - 操作：若尚未顯示採購單號，請輸入欲收貨之採購單號（範例："4500004223"），然後按 Enter 或點選畫面上的執行/載入按鈕以載入該採購單明細。  
   - 註：如果畫面已顯示欲處理的採購單號（例如畫面標題或項目清單中已見 EBELN = 4500004223），則可跳過輸入步驟，直接進行確認與過帳。

5. （選用）輸入或確認「採購項次（PO Item）」  
   - 元件 ID：wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/txtGODYNPRO-PO_ITEM  
   - 操作：如需只處理單一項次，請在此輸入項次號（例如 "10"）；如欲處理整張採購單則留空並載入所有項次。

6. 確認或調整「文件日期（Document Date）」  
   - 元件 ID：wnd[0]/usr/ssubSUB_HEADER:SAPLMIGO:0101/subSUB_HEADER:SAPLMIGO:0100/tabsTS_GOHEAD/tabpOK_GOHEAD_GENERAL/ssubSUB_TS_GOHEAD_GENERAL:SAPLMIGO:0110/ctxtGOHEAD-BLDAT  
   - 操作：確認畫面上文件日期是否符合需求（範例顯示為 "2026/06/24"）；如需變更請直接輸入所需日期。

7. 確認「異動類型（Movement Type）」  
   - 元件 ID（項目層級）：wnd[0]/usr/.../ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/ctxtGOITEM-BWART  
   - 操作：確認異動類型是否為 101（範例值為 "101"），若需要其他異動類型請修改為正確代碼。  
   - 註：在表格內每一項目也會顯示異動類型（例如表格欄位 GOITEM-BWART[10,0]），請一併確認。

8. 確認「庫存類型（Stock Type）」（如需要）  
   - 元件 ID（表頭/項目）：wnd[0]/usr/.../ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/cmbGOITEM-MIGO_INSMK  
   - 操作：預設通常為「未限制使用」；如需收至檢驗或凍結庫存，從下拉選擇對應選項（例如 "2 品質檢驗"、"3 凍結"）。

9. 檢視並確認項目明細（數量、單位、料號、儲位等）  
   - 重要欄位示例與元件 ID：  
     - 品名：GOITEM-MAKTX（表格）例：wnd[0]/usr/.../tblSAPLMIGOTV_GOITEM/ctxtGOITEM-MAKTX[1,0]（顯示 "BENQ 24\"螢幕"）  
     - 收貨數量：GOITEM-ERFMG（例：wnd[0]/usr/.../tblSAPLMIGOTV_GOITEM/txtGOITEM-ERFMG[4,0]，值 "10"）  
     - 單位：GOITEM-ERFME（例：wnd[0]/usr/.../tblSAPLMIGOTV_GOITEM/ctxtGOITEM-ERFME[5,0]，值 "EA"）  
     - 採購單號（表格）：GOITEM-EBELN（例：wnd[0]/usr/.../tblSAPLMIGOTV_GOITEM/ctxtGOITEM-EBELN[28,0]，值 "4500004223"）  
   - 操作：逐筆檢查欲過帳之項目數量與單位是否正確，必要時可在表格中修正數量欄位（若欄位允許變更）。

10. 執行檢查（Check）  
    - 操作：建議先按畫面上的「檢查」或功能鍵（如畫面上有檢查按鈕或可使用相應功能）以確認是否有錯誤或必填欄位未填。  
    - 註：若有系統錯誤或警告，依系統提示修正後再繼續。

11. 過帳收貨（Post）  
    - 操作：確認無誤後，按工具列的「過帳（Post）」按鈕或按 F8 執行過帳。  
    - 註：工具列按鈕位置因客戶化而異；若看不到請依系統畫面上的「過帳」或「Post」圖示操作。

12. 確認過帳結果與狀態列訊息  
    - 操作：過帳完成後，查看畫面下方狀態列（Status Bar）是否顯示成功訊息（如過帳文件號等），並記下過帳文件編號以便追蹤。  
    - 若出現錯誤或警告，依系統訊息處理並重覆相關步驟。

## 補充說明 / 注意事項
- 若畫面中某些欄位為系統預設值（SAP 自動帶入），請確認該預設值是否符合需求；依需求才變更。  
- 若畫面標題已顯示「收貨 採購單 4500004223」，代表採購單已被載入，可直接進行步驟 6~11 的確認與過帳。  
- 本 SOP 中的元件 ID 以錄製時畫面為準，實際環境若有畫面客製化或版本差異，元件路徑可能略有不同；請以畫面顯示的欄位名稱為識別依據。