"""Shared SAP table inspection helpers for CLI/UI and evidence collection."""

import json

from mcp_client import MCPClientUnavailable, get_default_sync_client
from sap_agent_tools import inspect_sap_tables as inspect_sap_tables_legacy


def parse_mcp_json_text(raw_text):
    """Parse MCP text output that is expected to contain JSON."""
    text = str(raw_text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return {"raw": text}
    return {"raw": text}


def inspect_current_tables(
    session=None,
    container_id="",
    max_depth=10,
    max_rows=10,
    include_rows=False,
    use_focus=True,
):
    """Inspect visible SAP tables, preferring MCP and falling back to legacy COM."""
    mcp_error = ""
    try:
        client = get_default_sync_client()
        tools = client.get_available_tools()
        tool_names = {
            item.get("function", {}).get("name", "")
            for item in tools
            if isinstance(item, dict)
        }
        if "sap_inspect_tables" in tool_names:
            client.ensure_sap_connected(list(tool_names))
            args = {
                "max_depth": max_depth,
                "max_rows": max_rows,
                "include_rows": include_rows,
                "use_focus": use_focus,
            }
            if container_id:
                args["container_id"] = container_id
            raw = client.call_tool("sap_inspect_tables", args)
            report = parse_mcp_json_text(raw)
            if isinstance(report, dict):
                report["backend"] = "mcp_sap_inspect_tables"
                if mcp_error:
                    report["mcp_error"] = mcp_error
                return report
    except (MCPClientUnavailable, Exception) as exc:
        mcp_error = str(exc)

    if session is None:
        return {
            "backend": "unavailable",
            "tables": [],
            "candidates": [],
            "errors": [{"phase": "mcp", "error": mcp_error or "MCP unavailable and no legacy session"}],
        }

    report = inspect_sap_tables_legacy(
        session,
        container_id=container_id,
        max_depth=max_depth,
        max_rows=max_rows,
        include_rows=include_rows,
        use_focus=use_focus,
    )
    if mcp_error:
        report["mcp_error"] = mcp_error
    return report


def _column_label(column):
    if isinstance(column, dict):
        return (
            str(column.get("title") or "").strip()
            or str(column.get("name") or "").strip()
            or str(column.get("index") or "").strip()
        )
    return str(column or "").strip()


def format_table_inspection(report):
    """Format an inspection report for humans in CLI/UI logs."""
    if not isinstance(report, dict):
        return str(report)
    lines = [
        "SAP table inspection:",
        f"Backend: {report.get('backend', '-')}",
        f"Container: {report.get('container_id', '-')}",
    ]
    if report.get("mcp_error"):
        lines.append(f"MCP fallback reason: {report.get('mcp_error')}")
    focused = report.get("focused_element")
    if isinstance(focused, dict) and focused.get("id"):
        label = focused.get("text") or focused.get("name") or focused.get("type") or ""
        lines.append(f"Focused: {focused.get('id')} ({label})")
    lines.append(f"Candidates: {report.get('candidate_count', len(report.get('candidates') or []))}")

    tables = report.get("tables") or []
    if tables:
        lines.append(f"Readable tables: {len(tables)}")
        for index, table in enumerate(tables, 1):
            table_id = table.get("table_id") or table.get("id") or ""
            table_type = table.get("table_type") or table.get("source_element_type") or ""
            source = table.get("candidate_source") or ""
            lines.append(f"{index}. {table_type} {table_id} source={source}")
            columns = table.get("column_info") or table.get("columns") or []
            labels = [_column_label(column) for column in columns]
            labels = [label for label in labels if label]
            if labels:
                max_labels = 80
                suffix = ""
                if len(labels) > max_labels:
                    suffix = f" ... ({len(labels) - max_labels} more)"
                lines.append("   Columns: " + ", ".join(labels[:max_labels]) + suffix)
            rows = table.get("rows") or table.get("data") or []
            if rows:
                lines.append(f"   Rows returned: {len(rows)}")
    else:
        lines.append("Readable tables: 0")

    candidates = report.get("candidates") or []
    if candidates:
        lines.append("Candidate ids:")
        for item in candidates[:12]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            lines.append(
                f"- {item.get('type', '')} {item.get('id')} "
                f"source={item.get('candidate_source', '')}"
            )

    errors = report.get("errors") or []
    if errors:
        lines.append("Errors:")
        for item in errors[:8]:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('phase', item.get('type', ''))} "
                    f"{item.get('table_id', item.get('container_id', ''))}: {item.get('error', item.get('text', ''))}"
                )
            else:
                lines.append(f"- {item}")
    return "\n".join(lines)
