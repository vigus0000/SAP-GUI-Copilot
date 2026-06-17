---
name: mmbe_stock_overview
description: MMBE 庫存總覽，輸入物料後顯示庫存概況
mode: both
tags: MM,MMBE,Stock,Material
aliases: 查看物料庫存,查物料庫存,顯示物料庫存,物料庫存,庫存概況,MMBE
---

# Macro: mmbe_stock_overview

## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| material | 物料 |  | true | 要查詢庫存的物料 |
| plant | 工廠 |  | false | 工廠 |
| storage_location | 儲位 |  | false | 儲位 |
| batch | 批次 |  | false | 批次 |
| execute_key | 執行按鍵 | Execute | false | 預設直接執行 |

## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| element | wnd[0]/tbar[0]/okcd |  | true | 必須能找到 T-Code 欄位 |

## Steps
| step | action | element_id | value | label | element_type | skip_if_empty | alternate_ids | description |
|---|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | MMBE | 進入 MMBE | GuiOkCodeField | false |  | 開啟庫存總覽 |
| 2 | input | wnd[0]/usr/ctxtMS_MATNR-LOW | {{material}} | 物料 | GuiCTextField | false | wnd[0]/usr/ctxtMATNR-LOW;wnd[0]/usr/ctxtS_MATNR-LOW;wnd[0]/usr/ctxtRMMG1-MATNR;wnd[0]/usr/ctxtMARA-MATNR | 填入物料 |
| 3 | input | wnd[0]/usr/ctxtMS_WERKS-LOW | {{plant}} | 工廠 | GuiCTextField | true | wnd[0]/usr/ctxtWERKS-LOW;wnd[0]/usr/ctxtS_WERKS-LOW;wnd[0]/usr/ctxtRMMG1-WERKS | 填入工廠 |
| 4 | input | wnd[0]/usr/ctxtMS_LGORT-LOW | {{storage_location}} | 儲位 | GuiCTextField | true | wnd[0]/usr/ctxtLGORT-LOW;wnd[0]/usr/ctxtS_LGORT-LOW;wnd[0]/usr/ctxtRMMG1-LGORT | 填入儲位 |
| 5 | input | wnd[0]/usr/ctxtMS_CHARG-LOW | {{batch}} | 批次 | GuiCTextField | true | wnd[0]/usr/ctxtCHARG-LOW;wnd[0]/usr/ctxtS_CHARG-LOW | 填入批次 |
| 6 | key |  | {{execute_key}} | 執行 |  | true |  | 顯示庫存總覽 |

## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | MMBE | true | 應停在 MMBE |
