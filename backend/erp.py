from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from typing import Any

from .audit import write_audit
from .auth import PermissionDenied, UserContext, can, require
from .db import Database, json_value, utc_now_text
from .risk import analyze_material_risk, dashboard_data


ACTION_REGISTRY: dict[str, dict[str, Any]] = {
    "dashboard.get": {"permissions": ["dashboard.read"], "risk": "low", "label": "查看经营总览"},
    "risk.analyze": {"permissions": ["risk.read"], "risk": "low", "label": "分析供应链风险"},
    "permissions.describe": {"permissions": [], "risk": "low", "label": "查看 AI 权限"},
    "purchase.create_replenishment": {
        "permissions": ["purchase.create"], "risk": "high", "label": "创建补货采购单",
    },
    "purchase.replenishment_advice": {
        "permissions": ["replenishment.advise"], "risk": "medium", "label": "人工补货管理建议",
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


def submit_manual_replenishment(
    db: Database,
    user: UserContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """提交人工补货方案；采购角色直接形成审批，其他角色先形成管理建议。"""
    require(user, "replenishment.advise")
    formal = can(user.role, "purchase.create")
    normalized = _validate_manual_replenishment(db, params, formal=formal)
    risk = analyze_material_risk(db, normalized["material_code"])
    normalized["source"] = "manual"
    normalized["risk_snapshot"] = {
        "event_code": risk["event_code"],
        "severity": risk["severity"],
        "shortage": risk["metrics"]["shortage"],
        "affected_orders": risk["metrics"]["affected_orders"],
        "projected_stock": risk["metrics"]["projected_stock"],
    }
    reason = f"{normalized['urgency']}人工补货：{normalized['situation']}"
    if formal:
        return create_approval(db, user, "purchase.create_replenishment", normalized, reason)

    action = "purchase.replenishment_advice"
    key = _idempotency_key(action, user.id, normalized)
    existing = db.fetch_one("SELECT * FROM approvals WHERE idempotency_key = ?", (key,))
    if existing:
        return {"mode": "advice", "approval": _parse_approval(existing), "deduplicated": True}
    approval_id = db.execute(
        """INSERT INTO approvals(action_type, requested_by, payload, status, risk_level,
           reason, created_at, idempotency_key) VALUES (?, ?, ?, 'pending_review', 'medium', ?, ?, ?)""",
        (action, user.id, json_value(normalized), reason, utc_now_text(), key),
    )
    write_audit(
        db, user, "approval", action, "pending_review",
        {"approval_id": approval_id, "params": normalized, "reason": reason},
    )
    approval = db.fetch_one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    return {"mode": "advice", "approval": _parse_approval(approval), "deduplicated": False}


def convert_replenishment_advice(
    db: Database,
    user: UserContext,
    advice_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """采购人员将管理建议一次性转换为正式补货审批。"""
    require(user, "purchase.create")
    existing_advice = db.fetch_one("SELECT * FROM approvals WHERE id = ?", (advice_id,))
    if (
        existing_advice
        and existing_advice["action_type"] == "purchase.replenishment_advice"
        and existing_advice["status"] == "converted"
    ):
        advice_payload = json.loads(existing_advice["payload"])
        converted = db.fetch_one(
            "SELECT * FROM approvals WHERE id = ?", (advice_payload.get("converted_approval_id"),),
        )
        if not converted:
            raise ValueError("建议的转换记录不完整")
        return {"mode": "approval", "approval": _parse_approval(converted), "deduplicated": True}
    normalized = _validate_manual_replenishment(db, params, formal=True)
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (advice_id,)).fetchone()
        if not row or row["action_type"] != "purchase.replenishment_advice":
            raise ValueError("人工补货建议不存在")
        advice = dict(row)
        advice_payload = json.loads(advice["payload"])
        if advice["status"] == "converted":
            converted_id = advice_payload.get("converted_approval_id")
            converted = conn.execute("SELECT * FROM approvals WHERE id = ?", (converted_id,)).fetchone()
            if not converted:
                raise ValueError("建议的转换记录不完整")
            return {"mode": "approval", "approval": _parse_approval(dict(converted)), "deduplicated": True}
        if advice["status"] != "pending_review":
            raise ValueError("只有待采购确认的建议可以转换")

        normalized["source"] = "manual"
        normalized["source_advice_id"] = advice_id
        normalized["risk_snapshot"] = advice_payload.get("risk_snapshot", {})
        key = _idempotency_key("purchase.create_replenishment", user.id, {"source_advice_id": advice_id})
        cursor = conn.execute(
            """INSERT INTO approvals(action_type, requested_by, payload, status, risk_level,
               reason, created_at, idempotency_key) VALUES (?, ?, ?, 'pending', 'high', ?, ?, ?)""",
            (
                "purchase.create_replenishment", user.id, json_value(normalized),
                f"由管理建议 #{advice_id} 转为正式补货审批", utc_now_text(), key,
            ),
        )
        approval_id = int(cursor.lastrowid)
        advice_payload["converted_approval_id"] = approval_id
        conn.execute(
            """UPDATE approvals SET payload = ?, status = 'converted', decided_at = ?, decided_by = ?,
               decision_note = ? WHERE id = ?""",
            (json_value(advice_payload), utc_now_text(), user.id, f"已转为正式审批 #{approval_id}", advice_id),
        )
        _insert_audit(
            conn, user, "approval", "purchase.replenishment_advice.convert", "success",
            {"advice_id": advice_id, "approval_id": approval_id, "params": normalized},
        )
        converted = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    return {"mode": "approval", "approval": _parse_approval(dict(converted)), "deduplicated": False}


def list_approvals(db: Database, status: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT a.*, requester.name AS requester_name, decider.name AS decider_name
             FROM approvals a
             JOIN users requester ON requester.id = a.requested_by
             LEFT JOIN users decider ON decider.id = a.decided_by"""
    params: tuple[Any, ...] = ()
    if status:
        sql += " WHERE a.status = ?"
        params = (status,)
    sql += " ORDER BY CASE a.status WHEN 'pending_review' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END, a.id DESC"
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
        source = payload.get("source", "ai")
        quantity = _positive_number(payload.get("quantity"), "补货数量")
        supplier_id = int(payload.get("supplier_id", 1))
        supplier = conn.execute("SELECT id FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
        if not supplier:
            raise ValueError("补货供应商不存在")
        expected_date = payload.get("expected_date") if source == "manual" else None
        if expected_date:
            _valid_expected_date(expected_date, allow_past=True)
        else:
            expected_date = conn.execute("SELECT date('now', '+7 day') AS value").fetchone()["value"]
        prefix = "MAN" if source == "manual" else "AI"
        po_number = f"PO-{prefix}-{datetime.now():%m%d}-{approval['id']:04d}"
        cursor = conn.execute(
            """INSERT INTO purchase_orders(po_number, supplier_id, status, expected_date,
               total_amount, created_at) VALUES (?, ?, '已确认', ?, ?, ?)""",
            (po_number, supplier_id, expected_date,
             quantity * float(material["unit_cost"]), utc_now_text()),
        )
        conn.execute(
            """INSERT INTO purchase_order_items(purchase_order_id, material_id, quantity, received_quantity)
               VALUES (?, ?, ?, 0)""",
            (cursor.lastrowid, material["id"], quantity),
        )
        conn.execute("UPDATE risk_events SET status = '缓解中' WHERE material_id = ?", (material["id"],))
        return {
            "purchase_order": po_number, "quantity": quantity, "supplier_id": supplier_id,
            "expected_date": expected_date, "source": source, "status": "已确认",
        }
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


def _validate_manual_replenishment(
    db: Database,
    params: dict[str, Any],
    *,
    formal: bool,
) -> dict[str, Any]:
    material_code = str(params.get("material_code", "M-AL-6061")).strip()
    material = db.fetch_one("SELECT code FROM materials WHERE code = ?", (material_code,))
    if not material:
        raise ValueError(f"未找到物料：{material_code}")
    expected_date = str(params.get("expected_date", "")).strip()
    _valid_expected_date(expected_date)
    urgency = str(params.get("urgency", "")).strip()
    if urgency not in {"一般", "紧急", "特急"}:
        raise ValueError("紧急程度必须是一般、紧急或特急")
    situation = _required_text(params.get("situation"), "具体情况", 1000)
    rationale = _required_text(params.get("rationale"), "判断依据", 1000)
    result: dict[str, Any] = {
        "material_code": material_code,
        "quantity": _positive_number(params.get("quantity"), "补货数量"),
        "expected_date": expected_date,
        "urgency": urgency,
        "situation": situation,
        "rationale": rationale,
    }
    if formal:
        try:
            supplier_id = int(params.get("supplier_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("请选择系统供应商") from exc
        if not db.fetch_one("SELECT id FROM suppliers WHERE id = ?", (supplier_id,)):
            raise ValueError("补货供应商不存在")
        result["supplier_id"] = supplier_id
    else:
        result["suggested_supplier"] = _required_text(
            params.get("suggested_supplier"), "建议供应商", 100,
        )
    return result


def _positive_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是数字") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label}必须大于 0")
    return number


def _valid_expected_date(value: Any, *, allow_past: bool = False) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("预计到货日期格式无效") from exc
    if not allow_past and parsed < date.today():
        raise ValueError("预计到货日期不能早于今天")
    return text


def _required_text(value: Any, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"请填写{label}")
    if len(text) > limit:
        raise ValueError(f"{label}不能超过 {limit} 个字")
    return text


def _insert_audit(
    conn,
    user: UserContext,
    event_type: str,
    action: str,
    status: str,
    details: dict[str, Any],
) -> None:
    conn.execute(
        """INSERT INTO audit_logs(timestamp, user_id, role, event_type, action, status,
           details, provider, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, 'local', 0)""",
        (utc_now_text(), user.id, user.role, event_type, action, status, json_value(details)),
    )
