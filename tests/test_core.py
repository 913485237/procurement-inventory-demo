from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.auth import PermissionDenied, get_user
from backend.db import Database, json_value, utc_now_text
from backend.erp import (
    convert_replenishment_advice,
    create_approval,
    decide_approval,
    list_approvals,
    submit_manual_replenishment,
)
from backend.risk import analyze_material_risk, business_detail, dashboard_data, order_detail


class CoreWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.db.initialize()
        self.admin = get_user(self.db, 1)
        self.procurement = get_user(self.db, 2)
        self.warehouse = get_user(self.db, 3)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_risk_calculation_is_explainable(self) -> None:
        result = analyze_material_risk(self.db)
        self.assertEqual(result["metrics"]["open_demand"], 980)
        self.assertEqual(result["metrics"]["incoming"], 240)
        self.assertEqual(result["metrics"]["shortage"], 520)
        self.assertEqual(len(result["explanation"]), 3)

    def test_order_detail_aggregates_fulfillment_and_production(self) -> None:
        detail = order_detail(self.db, 4)
        self.assertEqual(detail["order"]["order_number"], "SO-202608-0226")
        self.assertEqual(detail["items"][0]["product_name"], "控制柜 C2")
        self.assertEqual(detail["production_tasks"][0]["task_number"], "WO-202608-20444")
        self.assertEqual(detail["shipments"][0]["shipment_number"], "SHP-202608-0096")
        self.assertEqual(detail["invoices"], [])
        self.assertEqual(detail["risks"], [])

    def test_order_detail_rejects_unknown_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "未找到订单"):
            order_detail(self.db, 999)

    def test_business_details_aggregate_related_records(self) -> None:
        material = business_detail(self.db, "materials", 1)
        self.assertEqual(material["record"]["code"], "M-AL-6061")
        self.assertEqual(material["purchases"][0]["supplier_name"], "华东铝业集团")
        self.assertEqual(len(material["production"]), 3)
        self.assertEqual(material["risks"][0]["event_code"], "RM-202405")

        supplier = business_detail(self.db, "suppliers", 1)
        self.assertEqual(supplier["stats"]["purchase_count"], 1)
        self.assertEqual(supplier["materials"][0]["code"], "M-AL-6061")

        purchase = business_detail(self.db, "purchases", 1)
        self.assertEqual(purchase["record"]["supplier_code"], "SUP-001")
        self.assertEqual(purchase["items"][0]["pending_quantity"], 240)
        self.assertEqual(purchase["risks"][0]["event_code"], "RM-202405")

        shipment = business_detail(self.db, "shipments", 1)
        self.assertEqual(shipment["record"]["order_number"], "SO-202608-0226")
        self.assertEqual(shipment["items"][0]["product_name"], "控制柜 C2")

        invoice_id = self.db.execute(
            """INSERT INTO invoices(invoice_number, sales_order_id, amount, status, created_at, idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("INV-TEST-001", 4, 186000, "已开票", utc_now_text(), "invoice-detail-test"),
        )
        invoice = business_detail(self.db, "invoices", invoice_id)
        self.assertEqual(invoice["record"]["customer_name"], "联创机电")
        self.assertEqual(invoice["shipments"][0]["shipment_number"], "SHP-202608-0096")

    def test_business_detail_rejects_unknown_type_and_record(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持的业务详情类型"):
            business_detail(self.db, "orders", 1)
        with self.assertRaisesRegex(ValueError, "未找到物料"):
            business_detail(self.db, "materials", 999)

    def test_replenishment_requires_and_executes_approval(self) -> None:
        request = create_approval(
            self.db, self.procurement, "purchase.create_replenishment",
            {"material_code": "M-AL-6061", "quantity": 700, "supplier_id": 1},
            "缺料处置",
        )
        approval_id = request["approval"]["id"]
        result = decide_approval(self.db, self.admin, approval_id, "approved", "同意执行")
        self.assertEqual(result["execution"]["quantity"], 700)
        po = self.db.fetch_one("SELECT * FROM purchase_orders WHERE po_number LIKE 'PO-AI-%'")
        self.assertIsNotNone(po)
        second = decide_approval(self.db, self.admin, approval_id, "approved", "重复请求")
        self.assertTrue(second["deduplicated"])

    def test_non_admin_cannot_decide_approval(self) -> None:
        approval_id = list_approvals(self.db, "pending")[0]["id"]
        with self.assertRaises(PermissionDenied):
            decide_approval(self.db, self.procurement, approval_id, "approved")

    def test_warehouse_submits_advice_without_ai_interaction(self) -> None:
        result = submit_manual_replenishment(
            self.db,
            self.warehouse,
            {
                "material_code": "M-AL-6061",
                "quantity": 640,
                "suggested_supplier": "现场长期合作供应商",
                "expected_date": "2099-08-20",
                "urgency": "紧急",
                "situation": "现有库存批次存在额外损耗，需要按现场情况补足。",
                "rationale": "依据近三次同类任务的实际耗用记录。",
            },
        )
        self.assertEqual(result["mode"], "advice")
        self.assertEqual(result["approval"]["status"], "pending_review")
        self.assertEqual(result["approval"]["risk_level"], "medium")
        self.assertEqual(result["approval"]["payload"]["source"], "manual")
        self.assertEqual(self.db.fetch_one("SELECT COUNT(*) AS count FROM ai_interactions")["count"], 0)

    def test_procurement_submits_manual_plan_and_admin_executes_exact_values(self) -> None:
        result = submit_manual_replenishment(
            self.db,
            self.procurement,
            {
                "material_code": "M-AL-6061",
                "quantity": 615,
                "supplier_id": 4,
                "expected_date": "2099-08-21",
                "urgency": "特急",
                "situation": "管理者确认需要补货。",
                "rationale": "结合现场损耗与客户优先级判断。",
            },
        )
        self.assertEqual(result["mode"], "approval")
        approved = decide_approval(
            self.db, self.admin, result["approval"]["id"], "approved", "批准人工方案",
        )
        self.assertEqual(approved["execution"]["source"], "manual")
        self.assertEqual(approved["execution"]["quantity"], 615)
        self.assertEqual(approved["execution"]["supplier_id"], 4)
        self.assertEqual(approved["execution"]["expected_date"], "2099-08-21")
        self.assertTrue(approved["execution"]["purchase_order"].startswith("PO-MAN-"))

    def test_procurement_converts_advice_exactly_once(self) -> None:
        advice = submit_manual_replenishment(
            self.db,
            self.warehouse,
            {
                "material_code": "M-AL-6061",
                "quantity": 680,
                "suggested_supplier": "华东铝业集团",
                "expected_date": "2099-08-22",
                "urgency": "紧急",
                "situation": "仓库盘点确认缺口扩大。",
                "rationale": "现场盘点和未完成工单共同证明。",
            },
        )["approval"]
        params = {
            "material_code": "M-AL-6061",
            "quantity": 680,
            "supplier_id": 1,
            "expected_date": "2099-08-22",
            "urgency": "紧急",
            "situation": "仓库盘点确认缺口扩大。",
            "rationale": "采购复核后确认建议有效。",
        }
        first = convert_replenishment_advice(self.db, self.procurement, advice["id"], params)
        second = convert_replenishment_advice(self.db, self.procurement, advice["id"], {})
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["approval"]["id"], second["approval"]["id"])
        converted = self.db.fetch_one("SELECT status, payload FROM approvals WHERE id = ?", (advice["id"],))
        self.assertEqual(converted["status"], "converted")
        self.assertEqual(json.loads(converted["payload"])["converted_approval_id"], first["approval"]["id"])

    def test_manual_plan_validation_rejects_invalid_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "补货数量必须大于 0"):
            submit_manual_replenishment(
                self.db,
                self.warehouse,
                {
                    "material_code": "M-AL-6061",
                    "quantity": 0,
                    "suggested_supplier": "供应商",
                    "expected_date": "2099-08-20",
                    "urgency": "一般",
                    "situation": "现场确认。",
                    "rationale": "经验判断。",
                },
            )

    def test_failed_execution_rolls_back_approval(self) -> None:
        approval_id = self.db.execute(
            """INSERT INTO approvals(action_type, requested_by, payload, status, risk_level,
               reason, created_at, idempotency_key) VALUES (?, ?, ?, 'pending', 'high', ?, ?, ?)""",
            ("unknown.action", 1, json_value({}), "测试事务回滚", utc_now_text(), "rollback-test"),
        )
        with self.assertRaises(ValueError):
            decide_approval(self.db, self.admin, approval_id, "approved")
        row = self.db.fetch_one("SELECT status FROM approvals WHERE id = ?", (approval_id,))
        self.assertEqual(row["status"], "pending")

    def test_fulfillment_is_idempotent(self) -> None:
        request = create_approval(
            self.db, self.admin, "fulfillment.ship_and_invoice", {"order_ids": [1, 2]}, "交付执行"
        )
        approval_id = request["approval"]["id"]
        decide_approval(self.db, self.admin, approval_id, "approved", "批准")
        decide_approval(self.db, self.admin, approval_id, "approved", "再次批准")
        shipments = self.db.fetch_one("SELECT COUNT(*) AS count FROM shipments WHERE idempotency_key LIKE ?", (f"approval-{approval_id}-%",))
        invoices = self.db.fetch_one("SELECT COUNT(*) AS count FROM invoices WHERE idempotency_key LIKE ?", (f"approval-{approval_id}-%",))
        self.assertEqual(shipments["count"], 2)
        self.assertEqual(invoices["count"], 2)

    def test_reset_restores_seed_data(self) -> None:
        self.db.execute(
            "INSERT INTO users(id, name, role, title, avatar_color) VALUES (?, ?, ?, ?, ?)",
            (99, "临时用户", "sales", "测试", "#000000"),
        )
        self.db.reset()
        self.assertEqual(dashboard_data(self.db)["metrics"]["order_count"], 4)
        self.assertIsNone(self.db.fetch_one("SELECT id FROM users WHERE id = 99"))


if __name__ == "__main__":
    unittest.main()
