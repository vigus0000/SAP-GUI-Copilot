# SOP: ME51N

## 目的
建立請購單（Create Purchase Requisition）並儲存，確認系統回傳請購號碼。

## 前提條件
- 已登入 SAP GUI、具有 ME51N 的使用權限。

## 操作步驟
1. 進入交易 ME51N  
   - 在 T-Code 欄位（wnd[0]/tbar[0]/okcd）輸入 "ME51N"，按 Enter 進入「建立請購單」畫面。

2. 等待畫面載入並確認主畫面標題為「建立請購單」（wnd[0]/titl）。  

3. 確認請購單類型（採購憑單類型）  
   - 檢查欄位「請購單類型」（元素 ID：wnd[0]/usr/subSUB0:SAPLMEGUI:0013/subSUB0:SAPLMEGUI:0030/subSUB1:SAPLMEGUI:3327/cmbMEREQ_TOPLINE-BSART）。  
   - 若系統已預設為「NB 請購單」且您欲沿用，請直接沿用；如需更改，從下拉選單選擇正確類型（例如 FO、RV、ZNB 等）。

4. 選擇或確認「項目」（新增項目選單）  
   - 元件 ID：wnd[0]/usr/subSUB0:SAPLMEGUI:0013/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB1:SAPLMEGUI:6000/cmbDYN_6000-LIST  
   - 若顯示「1 新項目」且要建立新項目，直接沿用該選項；若要選其他已存在項目，從下拉選擇對應項目。

5. 在項目表格中輸入項目明細（至少一列）  
   - 表格位置（整個 Grid）：wnd[0]/usr/subSUB0:SAPLMEGUI:0013/subSUB2:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:3212/cntlGRIDCONTROL/shellcont/shell  
   - 在表格第 1 列填寫常用欄位（以您的實際值取代 <> 內文字）：  
     - 物料（MATNR）：在第 1 列的「物料」欄輸入 <物料編號>。  
     - 短文（TXZ01）：在「短文」欄輸入 <項目描述>（選填）。  
     - 需求數量（MENGE）：在「需求數量」欄輸入 <數量>。  
     - 單位（MEINS）：在「單位」欄輸入 <單位>（例如 EA、KG）。  
     - 交貨日期（EEIND）：在「交貨日期」欄輸入 <交貨日期>（請使用系統接受的日期格式）。  
   - 編輯方式：在 Grid 上點兩下或選中儲存格後輸入；確認每個欄位輸入後移至下一欄位以儲存輸入值。  
   - 如果您已在畫面上看到可接受的現值（系統或先前輸入），請直接確認沿用，不必重新輸入（遵循 Study Mode 原則）。

6. 新增或插入項目（如需）  
   - 若要建立項目集：按「建立項目集」（元素 ID：wnd[0]/usr/.../btnCREATEPRITEMSET）。  
   - 若要建立階層項目：按「建立階層項目」（元素 ID：wnd[0]/usr/.../btnCREATEHRITEM）。  
   - 若要插入項目或插入階層項目，使用對應的「插入」按鈕（元素 ID：wnd[0]/usr/.../btnINSERTPRITEMSET 或 btnINSERTPRITM）。  
   - 若需複製/剪下/貼上項目，對應按鈕分別為 COPY、CUT、PASTE（元素 ID：wnd[0]/usr/.../btnCOPYPRHIERARCHY、btnCUTPRHIERARCHY、btnPASTEPRHIERARCHY）。

7. 編輯請購抬頭說明或內文（如需）  
   - 抬頭說明欄位：wnd[0]/usr/subSUB0:SAPLMEGUI:0013/subSUB0:SAPLMEGUI:0030/subSUB1:SAPLMEGUI:3327/txtMEREQ_TOPLINE-PURREQNDESCRIPTION  
   - 若要使用文字編輯器輸入備註或內文，可選擇文字編輯器類型（元素 ID：wnd[0]/usr/.../cmbMEPOTEXT-EDITOR），並在對應欄位（MEPOTEXT-TDINFO）輸入文字。

8. 執行檢查（建議）  
   - 點選檢查按鈕以執行系統驗證（元素 ID：wnd[0]/tbar[1]/btn[39]，提示：檢查 (Ctrl+Shift+F3)），修正畫面提示的錯誤或警告。

9. 儲存請購單  
   - 點選儲存按鈕（元素 ID：wnd[0]/tbar[0]/btn[11]，提示：儲存 (Ctrl+S)），或使用 Ctrl+S 快捷鍵儲存。

10. 確認請購單建立結果  
    - 儲存完成後，確認狀態列訊息（元素 ID：wnd[0]/sbar）出現「請購號碼 xxxxxx 已建立」的訊息，例如錄製時顯示：請購號碼 0010002231 已建立。  
    - 若看見該訊息，表示請購單已建立成功；若出現錯誤或警告，依畫面提示進行修正並重複儲存。

## 補充與注意事項
- 若系統在畫面載入時已自動帶入某些值（系統預設），請確認該預設值正確即可（元件 ID 同上），不必再次輸入。  
- 若需鍵入欄位的精確元件 ID 以支援自動化或問題排除，可參考本 SOP 中每一項步驟所列的元素 ID。  
- 若欲取消作業可使用「返回」或「取消」按鈕（wnd[0]/tbar[0]/btn[3] 返回；wnd[0]/tbar[0]/btn[12] 取消）。