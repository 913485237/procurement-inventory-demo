from __future__ import annotations

import math
from typing import Any

from .db import Database


def analyze_material_risk(db: Database, material_code: str = "M-AL-6061") -> dict[str, Any]:
    material = db.fetch_one("SELECT * FROM materials WHERE code = ?", (material_code,))
    if not material:
        raise ValueError(f"未找到物料：{material_code}")

    incoming_row = db.fetch_one(
        """SELECT COALESCE(SUM(poi.quantity - poi.received_quantity), 0) AS incoming
           FROM purchase_order_items poi
           JOIN purchase_orders po ON po.id = poi.purchase_order_id
           WHERE poi.material_id = ? AND po.status NOT IN ('已完成', '已取消')""",
        (material["id"],),
    )
    demand_row = db.fetch_one(
        """SELECT COALESCE(SUM(required_qty), 0) AS demand
           FROM production_tasks WHERE material_id = ? AND status NOT IN ('已完成', '已取消')""",
        (material["id"],),
    )
    incoming = float(incoming_row["incoming"])
    demand = float(demand_row["demand"])
    projected = float(material["current_stock"]) + incoming - demand
    shortage = max(0.0, float(material["safety_stock"]) - projected)

    affected_orders = db.fetch_all(
        """SELECT so.id, so.order_number, so.status, so.due_date, so.total_amount, so.priority,
                  c.name AS customer_name, c.tier AS customer_tier,
                  pt.task_number, pt.required_qty, pt.completed_qty
           FROM production_tasks pt
           JOIN sales_orders so ON so.id = pt.sales_order_id
           JOIN customers c ON c.id = so.customer_id
           WHERE pt.material_id = ? AND pt.status NOT IN ('已完成', '已取消')
           ORDER BY CASE so.priority WHEN '紧急' THEN 1 WHEN '高' THEN 2 ELSE 3 END, so.due_date""",
        (material["id"],),
    )
    purchase_sources = db.fetch_all(
        """SELECT po.po_number, po.status, po.expected_date, s.name AS supplier_name,
                  s.rating, s.on_time_rate, (poi.quantity - poi.received_quantity) AS incoming_qty
           FROM purchase_order_items poi
           JOIN purchase_orders po ON po.id = poi.purchase_order_id
           JOIN suppliers s ON s.id = po.supplier_id
           WHERE poi.material_id = ? AND po.status NOT IN ('已完成', '已取消')""",
        (material["id"],),
    )
    recommended_qty = int(math.ceil(shortage / 100.0) * 100 + 100) if shortage else 0

    chain = {
        "source": {
            "type": "supplier",
            "label": purchase_sources[0]["supplier_name"] if purchase_sources else "暂无在途供应商",
            "meta": f"在途 {incoming:g}{material['unit']}",
        },
        "risk": {
            "type": "material",
            "label": f"{material['name']}短缺",
            "meta": f"预计缺口 {shortage:g}{material['unit']}",
        },
        "orders": [
            {
                "type": "order",
                "label": row["order_number"],
                "meta": f"{row['customer_name']} · {row['priority']}",
                "priority": row["priority"],
            }
            for row in affected_orders
        ],
    }

    return {
        "event_code": "RM-202405",
        "severity": "CRITICAL" if shortage > 0 else "NORMAL",
        "material": material,
        "metrics": {
            "current_stock": material["current_stock"],
            "safety_stock": material["safety_stock"],
            "open_demand": demand,
            "incoming": incoming,
            "projected_stock": projected,
            "shortage": shortage,
            "affected_orders": len(affected_orders),
            "critical_orders": sum(1 for row in affected_orders if row["priority"] in {"紧急", "高"}),
        },
        "affected_orders": affected_orders,
        "purchase_sources": purchase_sources,
        "recommendation": {
            "quantity": recommended_qty,
            "supplier_id": 1,
            "supplier_name": "华东铝业集团",
            "reason": f"补足预计缺口 {shortage:g}{material['unit']}，并保留交付波动缓冲。",
        },
        "chain": chain,
        "explanation": [
            f"未完成生产任务需要 {demand:g}{material['unit']}。",
            f"当前库存 {material['current_stock']:g}{material['unit']}，在途 {incoming:g}{material['unit']}。",
            f"计入安全库存 {material['safety_stock']:g}{material['unit']} 后，预计缺口 {shortage:g}{material['unit']}。",
        ],
    }


def dashboard_data(db: Database) -> dict[str, Any]:
    metrics = db.fetch_one(
        """SELECT
           COALESCE(SUM(CASE WHEN status NOT IN ('已完成','已取消') THEN total_amount ELSE 0 END), 0) AS open_order_amount,
           SUM(CASE WHEN status = '待出货' THEN 1 ELSE 0 END) AS pending_shipments,
           COUNT(*) AS order_count
           FROM sales_orders"""
    )
    pending_approvals = db.fetch_one(
        "SELECT COUNT(*) AS count FROM approvals WHERE status IN ('pending', 'pending_review')"
    )["count"]
    active_risks = db.fetch_one(
        "SELECT COUNT(*) AS count FROM risk_events WHERE status NOT IN ('已关闭','已解决')"
    )["count"]
    uninvoiced = db.fetch_one(
        """SELECT COUNT(*) AS count FROM sales_orders so
           WHERE so.status NOT IN ('已取消') AND NOT EXISTS (
             SELECT 1 FROM invoices i WHERE i.sales_order_id = so.id
           )"""
    )["count"]
    orders = db.fetch_all(
        """SELECT so.order_number, c.name AS customer_name, so.status, so.due_date,
                  so.total_amount, so.priority
           FROM sales_orders so JOIN customers c ON c.id = so.customer_id
           ORDER BY CASE so.priority WHEN '紧急' THEN 1 WHEN '高' THEN 2 ELSE 3 END, so.due_date"""
    )
    return {
        "metrics": {
            "open_order_amount": metrics["open_order_amount"],
            "order_count": metrics["order_count"],
            "active_risks": active_risks,
            "pending_approvals": pending_approvals,
            "pending_shipments": metrics["pending_shipments"],
            "uninvoiced_orders": uninvoiced,
        },
        "orders": orders,
        "trend": [
            {"month": "3月", "orders": 68, "delivery": 92},
            {"month": "4月", "orders": 74, "delivery": 94},
            {"month": "5月", "orders": 71, "delivery": 91},
            {"month": "6月", "orders": 82, "delivery": 96},
            {"month": "7月", "orders": 88, "delivery": 95},
            {"month": "8月", "orders": 93, "delivery": 89},
        ],
        "risk_distribution": [
            {"label": "原料短缺", "value": 48, "color": "#F04438"},
            {"label": "交付延期", "value": 27, "color": "#F79009"},
            {"label": "质量波动", "value": 15, "color": "#7A5AF8"},
            {"label": "其他", "value": 10, "color": "#98A2B3"},
        ],
    }


def business_data(db: Database, data_type: str) -> list[dict[str, Any]]:
    queries = {
        "orders": """SELECT so.*, c.name AS customer_name, c.tier AS customer_tier
                    FROM sales_orders so JOIN customers c ON c.id = so.customer_id
                    ORDER BY so.id DESC""",
        "materials": "SELECT * FROM materials ORDER BY id",
        "suppliers": "SELECT * FROM suppliers ORDER BY id",
        "purchases": """SELECT po.*, s.name AS supplier_name FROM purchase_orders po
                       JOIN suppliers s ON s.id = po.supplier_id ORDER BY po.id DESC""",
        "shipments": """SELECT sh.*, so.order_number, c.name AS customer_name FROM shipments sh
                       JOIN sales_orders so ON so.id = sh.sales_order_id
                       JOIN customers c ON c.id = so.customer_id ORDER BY sh.id DESC""",
        "invoices": """SELECT i.*, so.order_number, c.name AS customer_name FROM invoices i
                      JOIN sales_orders so ON so.id = i.sales_order_id
                      JOIN customers c ON c.id = so.customer_id ORDER BY i.id DESC""",
    }
    if data_type not in queries:
        raise ValueError("不支持的业务数据类型")
    return db.fetch_all(queries[data_type])


def business_detail(db: Database, data_type: str, record_id: int) -> dict[str, Any]:
    handlers = {
        "materials": _material_detail,
        "suppliers": _supplier_detail,
        "purchases": _purchase_detail,
        "shipments": _shipment_detail,
        "invoices": _invoice_detail,
    }
    handler = handlers.get(data_type)
    if not handler:
        raise ValueError("不支持的业务详情类型")
    return handler(db, record_id)


def _required_record(db: Database, sql: str, record_id: int, label: str) -> dict[str, Any]:
    record = db.fetch_one(sql, (record_id,))
    if not record:
        raise ValueError(f"未找到{label}：{record_id}")
    return record


def _material_detail(db: Database, material_id: int) -> dict[str, Any]:
    material = _required_record(db, "SELECT * FROM materials WHERE id = ?", material_id, "物料")
    purchases = db.fetch_all(
        """SELECT po.id, po.po_number, po.status, po.expected_date, po.created_at,
                  s.code AS supplier_code, s.name AS supplier_name,
                  poi.quantity, poi.received_quantity,
                  poi.quantity - poi.received_quantity AS pending_quantity
           FROM purchase_order_items poi
           JOIN purchase_orders po ON po.id = poi.purchase_order_id
           JOIN suppliers s ON s.id = po.supplier_id
           WHERE poi.material_id = ? AND po.status NOT IN ('已完成', '已取消')
           ORDER BY po.id DESC""",
        (material_id,),
    )
    production = db.fetch_all(
        """SELECT pt.id, pt.task_number, pt.required_qty, pt.completed_qty, pt.status,
                  so.id AS order_id, so.order_number, so.due_date,
                  c.name AS customer_name
           FROM production_tasks pt
           JOIN sales_orders so ON so.id = pt.sales_order_id
           JOIN customers c ON c.id = so.customer_id
           WHERE pt.material_id = ? AND pt.status NOT IN ('已完成', '已取消')
           ORDER BY pt.id""",
        (material_id,),
    )
    risks = db.fetch_all(
        """SELECT id, event_code, event_type, status, severity, shortage_qty,
                  description, created_at
           FROM risk_events
           WHERE material_id = ? AND status NOT IN ('已解决', '已关闭')
           ORDER BY id DESC""",
        (material_id,),
    )
    return {"type": "materials", "record": material, "purchases": purchases, "production": production, "risks": risks}


def _supplier_detail(db: Database, supplier_id: int) -> dict[str, Any]:
    supplier = _required_record(db, "SELECT * FROM suppliers WHERE id = ?", supplier_id, "供应商")
    purchases = db.fetch_all(
        """SELECT id, po_number, status, expected_date, total_amount, created_at
           FROM purchase_orders WHERE supplier_id = ? ORDER BY id DESC""",
        (supplier_id,),
    )
    stats = db.fetch_one(
        """SELECT COUNT(*) AS purchase_count,
                  SUM(CASE WHEN status NOT IN ('已完成', '已取消') THEN 1 ELSE 0 END) AS open_count,
                  COALESCE(SUM(total_amount), 0) AS total_amount,
                  COALESCE(AVG(total_amount), 0) AS average_amount
           FROM purchase_orders WHERE supplier_id = ?""",
        (supplier_id,),
    )
    materials = db.fetch_all(
        """SELECT m.id, m.code, m.name, m.specification, m.unit,
                  SUM(poi.quantity) AS purchased_quantity,
                  SUM(poi.received_quantity) AS received_quantity,
                  SUM(poi.quantity - poi.received_quantity) AS pending_quantity
           FROM purchase_order_items poi
           JOIN purchase_orders po ON po.id = poi.purchase_order_id
           JOIN materials m ON m.id = poi.material_id
           WHERE po.supplier_id = ?
           GROUP BY m.id, m.code, m.name, m.specification, m.unit
           ORDER BY m.id""",
        (supplier_id,),
    )
    return {"type": "suppliers", "record": supplier, "stats": stats, "purchases": purchases, "materials": materials}


def _purchase_detail(db: Database, purchase_id: int) -> dict[str, Any]:
    purchase = _required_record(
        db,
        """SELECT po.*, s.code AS supplier_code, s.name AS supplier_name,
                  s.rating AS supplier_rating, s.lead_days AS supplier_lead_days,
                  s.on_time_rate AS supplier_on_time_rate, s.contact AS supplier_contact
           FROM purchase_orders po
           JOIN suppliers s ON s.id = po.supplier_id
           WHERE po.id = ?""",
        purchase_id,
        "采购单",
    )
    items = db.fetch_all(
        """SELECT poi.id, poi.quantity, poi.received_quantity,
                  poi.quantity - poi.received_quantity AS pending_quantity,
                  m.id AS material_id, m.code AS material_code, m.name AS material_name,
                  m.specification AS material_specification, m.unit AS material_unit
           FROM purchase_order_items poi
           JOIN materials m ON m.id = poi.material_id
           WHERE poi.purchase_order_id = ? ORDER BY poi.id""",
        (purchase_id,),
    )
    risks = db.fetch_all(
        """SELECT DISTINCT re.id, re.event_code, re.event_type, re.status, re.severity,
                  re.shortage_qty, re.description, re.created_at,
                  m.code AS material_code, m.name AS material_name, m.unit AS material_unit
           FROM risk_events re
           JOIN materials m ON m.id = re.material_id
           JOIN purchase_order_items poi ON poi.material_id = re.material_id
           WHERE poi.purchase_order_id = ? AND re.status NOT IN ('已解决', '已关闭')
           ORDER BY re.id DESC""",
        (purchase_id,),
    )
    return {"type": "purchases", "record": purchase, "items": items, "risks": risks}


def _shipment_detail(db: Database, shipment_id: int) -> dict[str, Any]:
    shipment = _required_record(
        db,
        """SELECT sh.*, so.order_number, so.status AS order_status, so.due_date,
                  so.total_amount AS order_amount, so.priority AS order_priority,
                  c.code AS customer_code, c.name AS customer_name, c.tier AS customer_tier,
                  c.industry AS customer_industry, c.contact AS customer_contact
           FROM shipments sh
           JOIN sales_orders so ON so.id = sh.sales_order_id
           JOIN customers c ON c.id = so.customer_id
           WHERE sh.id = ?""",
        shipment_id,
        "出货单",
    )
    items = db.fetch_all(
        """SELECT soi.id, soi.product_name, soi.quantity,
                  m.code AS material_code, m.name AS material_name,
                  m.specification AS material_specification
           FROM sales_order_items soi
           JOIN materials m ON m.id = soi.material_id
           WHERE soi.sales_order_id = ? ORDER BY soi.id""",
        (shipment["sales_order_id"],),
    )
    invoices = db.fetch_all(
        """SELECT id, invoice_number, amount, status, created_at
           FROM invoices WHERE sales_order_id = ? ORDER BY id DESC""",
        (shipment["sales_order_id"],),
    )
    return {"type": "shipments", "record": shipment, "items": items, "invoices": invoices}


def _invoice_detail(db: Database, invoice_id: int) -> dict[str, Any]:
    invoice = _required_record(
        db,
        """SELECT i.*, so.order_number, so.status AS order_status, so.due_date,
                  so.total_amount AS order_amount, so.priority AS order_priority,
                  c.code AS customer_code, c.name AS customer_name, c.tier AS customer_tier,
                  c.industry AS customer_industry, c.contact AS customer_contact
           FROM invoices i
           JOIN sales_orders so ON so.id = i.sales_order_id
           JOIN customers c ON c.id = so.customer_id
           WHERE i.id = ?""",
        invoice_id,
        "发票",
    )
    items = db.fetch_all(
        """SELECT soi.id, soi.product_name, soi.quantity,
                  m.code AS material_code, m.name AS material_name,
                  m.specification AS material_specification
           FROM sales_order_items soi
           JOIN materials m ON m.id = soi.material_id
           WHERE soi.sales_order_id = ? ORDER BY soi.id""",
        (invoice["sales_order_id"],),
    )
    shipments = db.fetch_all(
        """SELECT id, shipment_number, status, shipped_at
           FROM shipments WHERE sales_order_id = ? ORDER BY id DESC""",
        (invoice["sales_order_id"],),
    )
    return {"type": "invoices", "record": invoice, "items": items, "shipments": shipments}


def order_detail(db: Database, order_id: int) -> dict[str, Any]:
    order = db.fetch_one(
        """SELECT so.*, c.code AS customer_code, c.name AS customer_name,
                  c.tier AS customer_tier, c.industry AS customer_industry,
                  c.contact AS customer_contact
           FROM sales_orders so
           JOIN customers c ON c.id = so.customer_id
           WHERE so.id = ?""",
        (order_id,),
    )
    if not order:
        raise ValueError(f"未找到订单：{order_id}")

    items = db.fetch_all(
        """SELECT soi.id, soi.product_name, soi.quantity, soi.material_qty_per_unit,
                  m.code AS material_code, m.name AS material_name,
                  m.specification AS material_specification, m.unit AS material_unit,
                  soi.quantity * soi.material_qty_per_unit AS required_material_qty
           FROM sales_order_items soi
           JOIN materials m ON m.id = soi.material_id
           WHERE soi.sales_order_id = ?
           ORDER BY soi.id""",
        (order_id,),
    )
    production_tasks = db.fetch_all(
        """SELECT pt.id, pt.task_number, pt.required_qty, pt.completed_qty, pt.status,
                  m.code AS material_code, m.name AS material_name, m.unit AS material_unit
           FROM production_tasks pt
           JOIN materials m ON m.id = pt.material_id
           WHERE pt.sales_order_id = ?
           ORDER BY pt.id""",
        (order_id,),
    )
    shipments = db.fetch_all(
        """SELECT id, shipment_number, status, shipped_at
           FROM shipments WHERE sales_order_id = ? ORDER BY id DESC""",
        (order_id,),
    )
    invoices = db.fetch_all(
        """SELECT id, invoice_number, amount, status, created_at
           FROM invoices WHERE sales_order_id = ? ORDER BY id DESC""",
        (order_id,),
    )
    risks = db.fetch_all(
        """SELECT DISTINCT re.id, re.event_code, re.event_type, re.status, re.severity,
                  re.shortage_qty, re.description, re.created_at,
                  m.code AS material_code, m.name AS material_name, m.unit AS material_unit
           FROM risk_events re
           JOIN materials m ON m.id = re.material_id
           JOIN production_tasks pt ON pt.material_id = re.material_id
           WHERE pt.sales_order_id = ? AND re.status NOT IN ('已解决', '已关闭')
           ORDER BY re.id DESC""",
        (order_id,),
    )
    return {
        "order": order,
        "items": items,
        "production_tasks": production_tasks,
        "shipments": shipments,
        "invoices": invoices,
        "risks": risks,
    }
