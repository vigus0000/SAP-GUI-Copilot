---
name: mb52_stock_list
description: MB52 庫存清單，查詢物料/工廠/儲位層級的庫存列表
mode: both
tags: MM,MB52,Stock,List,Inventory
aliases: 顯示所有庫存,庫存清單,查詢庫存清單,列出庫存,所有庫存,庫存列表,MB52
---

# Macro: mb52_stock_list

## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| material | 物料 |  | false | 物料號，留空代表不限制物料 |
| plant | 工廠 |  | false | 工廠 |
| storage_location | 儲位 |  | false | 儲位 |
| material_type | 物料類型 |  | false | 物料類型 |
| material_group | 物料群組 |  | false | 物料群組 |
| batch | 批次 |  | false | 批次 |
| execute_key | 執行按鍵 |  | false | 若要直接執行，傳 Execute；留空則只填條件 |

## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| element | wnd[0]/tbar[0]/okcd |  | true | 必須能找到 T-Code 欄位 |

## Steps
| step | action | element_id | value | label | element_type | skip_if_empty | clear_if_empty | description |
|---|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | MB52 | 進入 MB52 | GuiOkCodeField | false | false | 開啟庫存清單 |
| 2 | input | wnd[0]/usr/ctxtMATNR-LOW | {{material}} | 物料 | GuiCTextField | true | true | 填入物料；未指定時清空以避免沿用 SAP 記憶值 |
| 3 | input | wnd[0]/usr/ctxtWERKS-LOW | {{plant}} | 工廠 | GuiCTextField | true | true | 填入工廠；未指定時清空以避免沿用 SAP 記憶值 |
| 4 | input | wnd[0]/usr/ctxtLGORT-LOW | {{storage_location}} | 儲位 | GuiCTextField | true | true | 填入儲位；未指定時清空以避免沿用 SAP 記憶值 |
| 5 | input | wnd[0]/usr/ctxtMTART-LOW | {{material_type}} | 物料類型 | GuiCTextField | true | true | 填入物料類型；未指定時清空以避免沿用 SAP 記憶值 |
| 6 | input | wnd[0]/usr/ctxtMATKL-LOW | {{material_group}} | 物料群組 | GuiCTextField | true | true | 填入物料群組；未指定時清空以避免沿用 SAP 記憶值 |
| 7 | input | wnd[0]/usr/ctxtCHARG-LOW | {{batch}} | 批次 | GuiCTextField | true | true | 填入批次；未指定時清空以避免沿用 SAP 記憶值 |
| 8 | key |  | {{execute_key}} | 執行 |  | true | false | 若提供 Execute 則送出查詢 |

## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | MB52 | true | 應停在 MB52 |
