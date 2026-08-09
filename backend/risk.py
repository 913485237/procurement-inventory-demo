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
        "SELECT COUNT(*) AS count FROM approvals WHERE status = 'pending'"
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

