from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .audit import write_audit
from .auth import PermissionDenied, UserContext, require
from .db import Database, json_value, utc_now_text
from .risk import analyze_material_risk, dashboard_data


ACTION_REGISTRY: dict[str, dict[str, Any]] = {
    "dashboard.get": {"permissions": ["dashboard.read"], "risk": "low", "label": "查看经营总览"},
    "risk.analyze": {"permissions": ["risk.read"], "risk": "low", "label": "分析供应链风险"},
    "permissions.describe": {"permissions": [], "risk": "low", "label": "查看 AI 权限"},
    "purchase.create_replenishment": {
        "permissions": ["purchase.create"], "risk": "high", "label": "创建补货采购单",
    },
    "fulfillment.ship_and_invoice": {
        "permissions": ["shipment.confirm", "invoice.create"], "risk": "high", "label": "确认出货并开票",
    },
}


def validate_action(user: UserContext, action: str) -> dict[str, Any]:
    spec = ACTION_REGISTRY.get(action)
    if not spec:
        raise ValueError(f"动作未登记：{action}")
    for permission in spec["permissions"]:
        require(user, permission)
    return spec


def execute_or_request(
    db: Database,
    user: UserContext,
    action: str,
    params: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    spec = validate_action(user, action)
    if spec["risk"] == "high":
        return create_approval(db, user, action, params, reason)
    if action == "risk.analyze":
        result = analyze_material_risk(db, params.get("material_code", "M-AL-6061"))
    elif action == "dashboard.get":
        result = dashboard_data(db)
    else:
        result = {"message": "权限信息由身份服务返回。"}
    write_audit(db, user, "action", action, "success", {"params": params})
    return {"mode": "executed", "action": action, "result": result}


def _idempotency_key(action: str, user_id: int, params: dict[str, Any]) -> str:
    source = f"{action}:{user_id}:{json_value(params)}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def create_approval(
    db: Database,
    user: UserContext,
    action: str,
    params: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    key = _idempotency_key(action, user.id, params)
    existing = db.fetch_one("SELECT * FROM approvals WHERE idempotency_key = ?", (key,))
    if existing:
        return {"mode": "approval", "approval": _parse_approval(existing), "deduplicated": True}
    approval_id = db.execute(
        """INSERT INTO approvals(action_type, requested_by, payload, status, risk_level,
           reason, created_at, idempotency_key) VALUES (?, ?, ?, 'pending', 'high', ?, ?, ?)""",
        (action, user.id, json_value(params), reason, utc_now_text(), key),
    )
    write_audit(
        db, user, "approval", action, "pending",
        {"approval_id": approval_id, "params": params, "reason": reason},
    )
    approval = db.fetch_one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    return {"mode": "approval", "approval": _parse_approval(approval), "deduplicated": False}


def list_approvals(db: Database, status: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT a.*, requester.name AS requester_name, decider.name AS decider_name
             FROM approvals a
             JOIN users requester ON requester.id = a.requested_by
             LEFT JOIN users decider ON decider.id = a.decided_by"""
    params: tuple[Any, ...] = ()
    if status:
        sql += " WHERE a.status = ?"
        params = (status,)
    sql += " ORDER BY CASE a.status WHEN 'pending' THEN 1 ELSE 2 END, a.id DESC"
    return [_parse_approval(row) for row in db.fetch_all(sql, params)]


def decide_approval(
    db: Database,
    user: UserContext,
    approval_id: int,
    decision: str,
    note: str = "",
) -> dict[str, Any]:
    require(user, "approval.decide")
    if decision not in {"approved", "rejected"}:
        raise ValueError("审批决定必须是 approved 或 rejected")
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if not row:
            raise ValueError("审批单不存在")
        approval = dict(row)
        if approval["status"] != "pending":
            return {"approval": _parse_approval(approval), "deduplicated": True}
        conn.execute(
            """UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?
               WHERE id = ?""",
            (decision, utc_now_text(), user.id, note, approval_id),
        )
        execution = None
        if decision == "approved":
            execution = _execute_approved(conn, approval)
        conn.execute(
            """INSERT INTO audit_logs(timestamp, user_id, role, event_type, action, status,
               details, provider, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (utc_now_text(), user.id, user.role, "approval", approval["action_type"], decision,
             json_value({"approval_id": approval_id, "note": note, "execution": execution}),
             "local", 0),
        )
    updated = db.fetch_one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    return {"approval": _parse_approval(updated), "execution": execution, "deduplicated": False}


def _execute_approved(conn, approval: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(approval["payload"])
    action = approval["action_type"]
    if action == "purchase.create_replenishment":
        material = conn.execute(
            "SELECT * FROM materials WHERE code = ?", (payload.get("material_code", "M-AL-6061"),)
        ).fetchone()
        if not material:
            raise ValueError("审批对应物料不存在")
        po_number = f"PO-AI-{datetime.now():%m%d}-{approval['id']:04d}"
        cursor = conn.execute(
            """INSERT INTO purchase_orders(po_number, supplier_id, status, expected_date,
               total_amount, created_at) VALUES (?, ?, '已确认', date('now', '+7 day'), ?, ?)""",
            (po_number, payload.get("supplier_id", 1),
             float(payload["quantity"]) * float(material["unit_cost"]), utc_now_text()),
        )
        conn.execute(
            """INSERT INTO purchase_order_items(purchase_order_id, material_id, quantity, received_quantity)
               VALUES (?, ?, ?, 0)""",
            (cursor.lastrowid, material["id"], payload["quantity"]),
        )
        conn.execute("UPDATE risk_events SET status = '缓解中' WHERE material_id = ?", (material["id"],))
        return {"purchase_order": po_number, "quantity": payload["quantity"], "status": "已确认"}
    if action == "fulfillment.ship_and_invoice":
        order_ids = payload.get("order_ids", [])
        if not order_ids:
            raise ValueError("没有可执行的订单")
        completed = []
        for order_id in order_ids:
            order = conn.execute("SELECT * FROM sales_orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                continue
            shipment_key = f"approval-{approval['id']}-shipment-{order_id}"
            invoice_key = f"approval-{approval['id']}-invoice-{order_id}"
            shipment = conn.execute(
                "SELECT shipment_number FROM shipments WHERE idempotency_key = ?", (shipment_key,)
            ).fetchone()
            invoice = conn.execute(
                "SELECT invoice_number FROM invoices WHERE idempotency_key = ?", (invoice_key,)
            ).fetchone()
            if not shipment:
                shipment_number = f"SHP-AI-{approval['id']:04d}-{order_id:02d}"
                conn.execute(
                    """INSERT INTO shipments(shipment_number, sales_order_id, status, shipped_at, idempotency_key)
                       VALUES (?, ?, '已出货', ?, ?)""",
                    (shipment_number, order_id, utc_now_text(), shipment_key),
                )
            else:
                shipment_number = shipment["shipment_number"]
            if not invoice:
                invoice_number = f"INV-AI-{approval['id']:04d}-{order_id:02d}"
                conn.execute(
                    """INSERT INTO invoices(invoice_number, sales_order_id, amount, status, created_at, idempotency_key)
                       VALUES (?, ?, ?, '已开票', ?, ?)""",
                    (invoice_number, order_id, order["total_amount"], utc_now_text(), invoice_key),
                )
            else:
                invoice_number = invoice["invoice_number"]
            conn.execute("UPDATE sales_orders SET status = '已出货' WHERE id = ?", (order_id,))
            completed.append({"order_number": order["order_number"], "shipment": shipment_number, "invoice": invoice_number})
        return {"completed": completed}
    raise ValueError(f"审批动作未实现：{action}")


def _parse_approval(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    result = dict(row)
    if isinstance(result.get("payload"), str):
        result["payload"] = json.loads(result["payload"])
    result["action_label"] = ACTION_REGISTRY.get(result["action_type"], {}).get("label", result["action_type"])
    return result

