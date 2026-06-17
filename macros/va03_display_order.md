---
name: va03_display_order
description: VA03 顯示銷售訂單，輸入銷售訂單號後進入顯示
mode: both
tags: SD,VA03,Sales Order,Display
aliases: 顯示銷售訂單,查看銷售訂單,開啟銷售訂單,查銷售訂單,VA03
---

# Macro: va03_display_order

## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| sales_order | 銷售訂單號 |  | true | 要顯示的銷售訂單號 |
| submit_key | 送出按鍵 | Enter | false | 預設 Enter |

## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| element | wnd[0]/tbar[0]/okcd |  | true | 必須能找到 T-Code 欄位 |

## Steps
| step | action | element_id | value | label | element_type | skip_if_empty | description |
|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | VA03 | 進入 VA03 | GuiOkCodeField | false | 開啟顯示銷售訂單 |
| 2 | input | wnd[0]/usr/ctxtVBAK-VBELN | {{sales_order}} | 銷售訂單 | GuiCTextField | false | 輸入銷售訂單號 |
| 3 | key |  | {{submit_key}} | 送出 |  | false | 進入訂單顯示 |

## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | VA03 | true | 應停在 VA03 |
