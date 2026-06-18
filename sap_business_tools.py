"""Registry for business-level MCP tools.

This module keeps SAP_Copilot's knowledge of process-level MCP tools out of
the generic ReAct prompt and tool filtering code. The implementation of each
tool still lives in the MCP SAP GUI server; this registry describes when the
main agent should expose and prefer those tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Iterable


def _env_enabled(name: str, default: str = "true") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class BusinessToolSpec:
    name: str
    title: str
    module: str
    business_cycle: str
    purpose: str
    required_args: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    prefer_when: str = ""
    safety: str = "read_only"
    source: str = "builtin"
    notes: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: dict) -> "BusinessToolSpec":
        def tuple_value(key: str) -> tuple[str, ...]:
            value = data.get(key, ())
            if isinstance(value, str):
                return tuple(item.strip() for item in value.split(",") if item.strip())
            if isinstance(value, Iterable):
                return tuple(str(item).strip() for item in value if str(item).strip())
            return ()

        return cls(
            name=str(data.get("name", "")).strip(),
            title=str(data.get("title", "")).strip(),
            module=str(data.get("module", "")).strip(),
            business_cycle=str(data.get("business_cycle", "")).strip(),
            purpose=str(data.get("purpose", "")).strip(),
            required_args=tuple_value("required_args"),
            triggers=tuple_value("triggers"),
            prefer_when=str(data.get("prefer_when", "")).strip(),
            safety=str(data.get("safety", "read_only")).strip() or "read_only",
            source=str(data.get("source", "custom")).strip() or "custom",
            notes=tuple_value("notes"),
        )

    def is_valid(self) -> bool:
        return bool(self.name and self.title and self.purpose)


BUILTIN_BUSINESS_TOOLS: tuple[BusinessToolSpec, ...] = (
    BusinessToolSpec(
        name="sap_get_order_overview",
        title="銷售訂單全貌查詢",
        module="SD",
        business_cycle="Order-to-Cash",
        purpose="以 VA03 查詢銷售訂單，彙整客戶、金額、幣別與憑證流中的出貨/開票/過帳進度。",
        required_args=("order_number",),
        triggers=(
            "查銷售訂單",
            "查看訂單",
            "訂單狀態",
            "出貨進度",
            "開票進度",
            "憑證流",
            "VA03",
        ),
        prefer_when="使用者提供銷售訂單號，並要求總覽、狀態、出貨或開票進度時，優先使用此工具。",
        notes=(
            "此工具是 read-only composite workflow。",
            "若工具不存在或失敗，回退一般 VA03 ReAct 操作。",
        ),
    ),
    BusinessToolSpec(
        name="sap_analyze_delivery_block",
        title="銷售訂單卡關診斷",
        module="SD/FI/MM",
        business_cycle="Order-to-Cash",
        purpose="巡檢 VA03、VKM1、MD04 與 FBL5N，分析銷售訂單無法出貨的可能原因與證據。",
        required_args=("order_number",),
        triggers=(
            "不能出貨",
            "無法出貨",
            "出不了貨",
            "訂單卡關",
            "delivery block",
            "credit block",
            "信用凍結",
            "缺料",
            "逾期帳款",
        ),
        prefer_when="使用者提供銷售訂單號，並詢問為何不能出貨、是否被信用/交貨凍結、是否缺料或帳款卡住時，優先使用此工具。",
        notes=(
            "此工具會跨交易讀取證據，但仍是 read-only。",
            "FBL5N 公司代碼可用 FBL5N_COMPANY_CODE 作為 fallback。",
        ),
    ),
)


def _load_custom_specs() -> list[BusinessToolSpec]:
    registry_path = os.getenv("SAP_BUSINESS_TOOLS_REGISTRY", "").strip()
    if not registry_path:
        return []

    path = Path(registry_path)
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    raw_items = payload.get("tools") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return []

    specs = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        spec = BusinessToolSpec.from_dict(item)
        if spec.is_valid():
            specs.append(spec)
    return specs


def business_tools_enabled() -> bool:
    return _env_enabled("SAP_BUSINESS_TOOLS_ENABLED", "true")


def get_business_tool_specs() -> list[BusinessToolSpec]:
    if not business_tools_enabled():
        return []

    disabled = set(_env_list("SAP_BUSINESS_DISABLED_MCP_TOOLS"))
    specs = [spec for spec in (*BUILTIN_BUSINESS_TOOLS, *_load_custom_specs()) if spec.name not in disabled]

    extra_names = _env_list("SAP_BUSINESS_EXTRA_MCP_TOOLS")
    for name in extra_names:
        if any(spec.name == name for spec in specs):
            continue
        specs.append(BusinessToolSpec(
            name=name,
            title=name,
            module="custom",
            business_cycle="custom",
            purpose="Custom business-level MCP tool registered through SAP_BUSINESS_EXTRA_MCP_TOOLS.",
            source="env",
        ))
    return specs


def get_business_mcp_tool_names() -> set[str]:
    return {spec.name for spec in get_business_tool_specs()}


def format_business_tools_prompt() -> str:
    specs = get_business_tool_specs()
    if not specs:
        return ""

    lines = [
        "## 業務邏輯 MCP 工具",
        "以下是主專案註冊的流程級/業務級 MCP tools。若本輪工具清單有提供，且使用者需求符合情境，優先使用這些 composite tools；若工具不存在或回傳失敗，再回退一般 ReAct GUI 操作。",
    ]
    for spec in specs:
        args = ", ".join(spec.required_args) if spec.required_args else "無"
        triggers = "、".join(spec.triggers[:8]) if spec.triggers else "依工具描述判斷"
        lines.extend([
            f"- `{spec.name}` - {spec.title}",
            f"  - module / cycle: {spec.module} / {spec.business_cycle}",
            f"  - purpose: {spec.purpose}",
            f"  - required args: {args}",
            f"  - triggers: {triggers}",
        ])
        if spec.prefer_when:
            lines.append(f"  - prefer when: {spec.prefer_when}")
        if spec.notes:
            lines.append(f"  - notes: {'; '.join(spec.notes)}")
    return "\n".join(lines)
