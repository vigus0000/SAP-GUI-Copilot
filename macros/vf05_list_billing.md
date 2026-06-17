---
name: vf05_list_billing
description: VF05 請款單清單，進入選擇畫面並填入付款人與請款日期條件
mode: both
tags: SD,VF05,Billing,List
aliases: 請款單清單,請款文件清單,查詢請款文件,發票清單,billing list,VF05
---

# Macro: vf05_list_billing

## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| payer | 付款人 |  | false | 付款人代號 |
| billing_date_from | 請款日期起 |  | false | 請款文件日期起 |
| billing_date_to | 請款日期迄 |  | false | 請款文件日期迄 |
| include_all | 所有請款文件 | true | false | true 時選取所有請款文件 radio |
| execute_key | 執行按鍵 |  | false | 若要直接執行，傳 Execute；留空則只填條件 |

## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| element | wnd[0]/tbar[0]/okcd |  | true | 必須能找到 T-Code 欄位 |

## Steps
| step | action | element_id | value | label | element_type | skip_if_empty | description |
|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | VF05 | 進入 VF05 | GuiOkCodeField | false | 開啟請款文件清單 |
| 2 | input | wnd[0]/usr/ctxtFACOM-FKDAT | {{billing_date_from}} | 請款日期起 | GuiCTextField | true | 填入請款日期起 |
| 3 | input | wnd[0]/usr/ctxtFACOM-FKDAT_BIS | {{billing_date_to}} | 請款日期迄 | GuiCTextField | true | 填入請款日期迄 |
| 4 | input | wnd[0]/usr/ctxtFACOM-KUNDE | {{payer}} | 付款人 | GuiCTextField | true | 填入付款人 |
| 5 | radio | wnd[0]/usr/radFACOM-VBALL | {{include_all}} | 所有請款文件 | GuiRadioButton | true | 選取所有請款文件 |
| 6 | key |  | {{execute_key}} | 執行 |  | true | 若提供 Execute 則送出查詢 |

## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | VF05 | true | 應停在 VF05 |
