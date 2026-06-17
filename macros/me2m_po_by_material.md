---
name: me2m_po_by_material
description: ME2M 依物料查詢採購單清單
mode: both
tags: MM,ME2M,Purchase Order,Material,List
aliases: 採購單清單,依物料查採購單,查採購單,物料採購單,PO清單,ME2M
---

# Macro: me2m_po_by_material

## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| material | 物料 |  | true | 要查詢採購單的物料 |
| plant | 工廠 |  | false | 工廠 |
| purchasing_org | 採購組織 |  | false | 採購組織 |
| purchasing_group | 採購群組 |  | false | 採購群組 |
| document_date_from | 文件日期起 |  | false | 採購文件日期起 |
| document_date_to | 文件日期迄 |  | false | 採購文件日期迄 |
| execute_key | 執行按鍵 | Execute | false | 預設直接執行 |

## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| element | wnd[0]/tbar[0]/okcd |  | true | 必須能找到 T-Code 欄位 |

## Steps
| step | action | element_id | value | label | element_type | skip_if_empty | description |
|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | ME2M | 進入 ME2M | GuiOkCodeField | false | 開啟依物料查詢採購單 |
| 2 | input | wnd[0]/usr/ctxtEM_MATNR-LOW | {{material}} | 物料 | GuiCTextField | false | 填入物料 |
| 3 | input | wnd[0]/usr/ctxtS_WERKS-LOW | {{plant}} | 工廠 | GuiCTextField | true | 填入工廠 |
| 4 | input | wnd[0]/usr/ctxtS_EKORG-LOW | {{purchasing_org}} | 採購組織 | GuiCTextField | true | 填入採購組織 |
| 5 | input | wnd[0]/usr/ctxtS_EKGRP-LOW | {{purchasing_group}} | 採購群組 | GuiCTextField | true | 填入採購群組 |
| 6 | input | wnd[0]/usr/ctxtS_BEDAT-LOW | {{document_date_from}} | 文件日期起 | GuiCTextField | true | 填入文件日期起 |
| 7 | input | wnd[0]/usr/ctxtS_BEDAT-HIGH | {{document_date_to}} | 文件日期迄 | GuiCTextField | true | 填入文件日期迄 |
| 8 | key |  | {{execute_key}} | 執行 |  | true | 執行採購單清單 |

## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | ME2M | true | 應停在 ME2M |
