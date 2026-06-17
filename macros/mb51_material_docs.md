---
name: mb51_material_docs
description: MB51 物料憑證查詢，進入選擇畫面並填入物料、工廠、日期等條件
mode: both
tags: MM,MB51,Material Document,List
aliases: 物料憑證,物料文件,物料憑證清單,查物料憑證,移動紀錄,MB51
---

# Macro: mb51_material_docs

## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| material | 物料 |  | false | 物料號 |
| plant | 工廠 |  | false | 工廠 |
| storage_location | 儲位 |  | false | 儲位 |
| movement_type | 移動類型 |  | false | 移動類型 |
| posting_date_from | 過帳日期起 |  | false | 過帳日期起 |
| posting_date_to | 過帳日期迄 |  | false | 過帳日期迄 |
| execute_key | 執行按鍵 |  | false | 若要直接執行，傳 Execute；留空則只填條件 |

## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| element | wnd[0]/tbar[0]/okcd |  | true | 必須能找到 T-Code 欄位 |

## Steps
| step | action | element_id | value | label | element_type | skip_if_empty | description |
|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | MB51 | 進入 MB51 | GuiOkCodeField | false | 開啟物料憑證清單 |
| 2 | input | wnd[0]/usr/ctxtMATNR-LOW | {{material}} | 物料 | GuiCTextField | true | 填入物料 |
| 3 | input | wnd[0]/usr/ctxtWERKS-LOW | {{plant}} | 工廠 | GuiCTextField | true | 填入工廠 |
| 4 | input | wnd[0]/usr/ctxtLGORT-LOW | {{storage_location}} | 儲位 | GuiCTextField | true | 填入儲位 |
| 5 | input | wnd[0]/usr/ctxtBWART-LOW | {{movement_type}} | 移動類型 | GuiCTextField | true | 填入移動類型 |
| 6 | input | wnd[0]/usr/ctxtBUDAT-LOW | {{posting_date_from}} | 過帳日期起 | GuiCTextField | true | 填入過帳日期起 |
| 7 | input | wnd[0]/usr/ctxtBUDAT-HIGH | {{posting_date_to}} | 過帳日期迄 | GuiCTextField | true | 填入過帳日期迄 |
| 8 | key |  | {{execute_key}} | 執行 |  | true | 若提供 Execute 則送出查詢 |

## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | MB51 | true | 應停在 MB51 |
