---
name: va05_list_orders
description: VA05 銷售訂單清單，進入選擇畫面並填入可選查詢條件
mode: both
tags: SD,VA05,Sales Order,List
aliases: 銷售訂單清單,查詢銷售訂單清單,列出銷售訂單,訂單清單,VA05
---

# Macro: va05_list_orders

## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| sold_to | 買方 |  | false | 銷售訂單清單的買方/售達方 |
| sales_org | 銷售組織 |  | false | 銷售組織 |
| distribution_channel | 配銷通路 |  | false | 配銷通路 |
| division | 產品組 |  | false | 產品組 |
| document_date_from | 文件日期起 |  | false | 銷售訂單文件日期起 |
| document_date_to | 文件日期迄 |  | false | 銷售訂單文件日期迄 |
| execute_key | 執行按鍵 |  | false | 若要直接執行，傳 Execute；留空則只填條件 |

## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| element | wnd[0]/tbar[0]/okcd |  | true | 必須能找到 T-Code 欄位 |

## Steps
| step | action | element_id | value | label | element_type | skip_if_empty | description |
|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | VA05 | 進入 VA05 | GuiOkCodeField | false | 開啟銷售訂單清單 |
| 2 | input | wnd[0]/usr/ctxtVBCOM-KUNDE | {{sold_to}} | 買方 | GuiCTextField | true | 填入買方/售達方 |
| 3 | input | wnd[0]/usr/ctxtVBCOM-VKORG | {{sales_org}} | 銷售組織 | GuiCTextField | true | 填入銷售組織 |
| 4 | input | wnd[0]/usr/ctxtVBCOM-VTWEG | {{distribution_channel}} | 配銷通路 | GuiCTextField | true | 填入配銷通路 |
| 5 | input | wnd[0]/usr/ctxtVBCOM-SPART | {{division}} | 產品組 | GuiCTextField | true | 填入產品組 |
| 6 | input | wnd[0]/usr/ctxtVBCOM-AUDAT | {{document_date_from}} | 文件日期起 | GuiCTextField | true | 填入文件日期起 |
| 7 | input | wnd[0]/usr/ctxtVBCOM-AUDAT_BIS | {{document_date_to}} | 文件日期迄 | GuiCTextField | true | 填入文件日期迄 |
| 8 | key |  | {{execute_key}} | 執行 |  | true | 若提供 Execute 則送出查詢 |

## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | VA05 | true | 應停在 VA05 |
