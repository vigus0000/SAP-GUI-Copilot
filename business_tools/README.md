# SAP Business Tool Registry

此資料夾說明 SAP_Copilot 主專案如何管理「業務邏輯 MCP 工具」。

業務邏輯工具指的是流程級、跨交易、會彙整證據的 MCP tool，例如：

- 查詢銷售訂單全貌
- 診斷銷售訂單為何無法出貨
- 檢查客戶帳款、信用凍結、缺料或文件流

這類工具的 SAP GUI 操作實作仍放在 `external/mcp-sap-gui`。主專案只負責：

1. 記錄工具的業務語意與觸發情境。
2. 將工具加入 Auto Mode 的 core MCP allowlist。
3. 在 Auto prompt 中告訴模型何時優先使用 composite business tool。
4. 工具不存在或失敗時，回退一般 ReAct GUI 操作。

## 主檔案

- `sap_business_tools.py`

## 內建工具

目前主專案內建註冊：

| tool | module | business_cycle | 用途 |
|---|---|---|---|
| `sap_get_order_overview` | SD | Order-to-Cash | 用 VA03 查詢銷售訂單客戶、金額與憑證流進度 |
| `sap_analyze_delivery_block` | SD/FI/MM | Order-to-Cash | 巡檢 VA03、VKM1、MD04、FBL5N，診斷銷售訂單出貨卡關原因 |

## 新增工具規則

新增一個業務 MCP tool 時，請在 `sap_business_tools.py` 補一筆 `BusinessToolSpec`：

```python
BusinessToolSpec(
    name="sap_analyze_xxx",
    title="工具顯示名稱",
    module="SD/FI/MM",
    business_cycle="Order-to-Cash",
    purpose="工具會做什麼、讀哪些交易、回傳什麼證據。",
    required_args=("order_number",),
    triggers=("不能出貨", "卡關", "credit block"),
    prefer_when="使用者提供單號並詢問特定業務狀態時優先使用。",
    notes=("read-only composite workflow",),
)
```

必要欄位：

- `name`：MCP server 實際提供的 tool name。
- `title`：人可讀名稱。
- `module`：SAP 模組或跨模組，例如 `SD`、`FI`、`MM`、`SD/FI/MM`。
- `business_cycle`：業務循環，例如 `Order-to-Cash`、`Procure-to-Pay`。
- `purpose`：工具的業務目的與證據來源。
- `required_args`：必要參數名稱。
- `triggers`：常見自然語言觸發詞。
- `prefer_when`：模型應優先使用此工具的情境。

## 設定

`.env` / `.env.example`：

```env
SAP_BUSINESS_TOOLS_ENABLED=true
SAP_BUSINESS_TOOLS_REGISTRY=
SAP_BUSINESS_EXTRA_MCP_TOOLS=
SAP_BUSINESS_DISABLED_MCP_TOOLS=
FBL5N_COMPANY_CODE=
```

- `SAP_BUSINESS_TOOLS_ENABLED=false`：關閉主專案業務工具註冊。
- `SAP_BUSINESS_TOOLS_REGISTRY`：可指定一個 JSON 檔，額外載入工具 metadata。
- `SAP_BUSINESS_EXTRA_MCP_TOOLS`：快速把工具名加入 core allowlist，但不提供完整 prompt metadata。
- `SAP_BUSINESS_DISABLED_MCP_TOOLS`：停用指定工具。
- `FBL5N_COMPANY_CODE`：給帳款檢查類工具作為公司代碼 fallback。

## 外部 JSON Registry 格式

若不想修改 Python，可用 `SAP_BUSINESS_TOOLS_REGISTRY=business_tools/custom_tools.json`：

```json
{
  "tools": [
    {
      "name": "sap_analyze_xxx",
      "title": "工具顯示名稱",
      "module": "SD",
      "business_cycle": "Order-to-Cash",
      "purpose": "工具用途",
      "required_args": ["order_number"],
      "triggers": ["卡關", "不能出貨"],
      "prefer_when": "使用者提供銷售訂單號並詢問出貨阻塞原因時",
      "notes": ["read-only"]
    }
  ]
}
```

## 安全原則

- 業務工具預設應是 read-only。
- 不應在業務工具內自動儲存、過帳、刪除、核准或釋放。
- 若工具需要修改資料，必須在工具名稱、metadata 與 prompt 中明確標示，並交由主專案的 confirmation 流程控管。
