from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.auth import PermissionDenied, get_user
from backend.db import Database, json_value, utc_now_text
from backend.erp import create_approval, decide_approval, list_approvals
from backend.risk import analyze_material_risk, dashboard_data


class CoreWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.db.initialize()
        self.admin = get_user(self.db, 1)
        self.procurement = get_user(self.db, 2)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_risk_calculation_is_explainable(self) -> None:
        result = analyze_material_risk(self.db)
        self.assertEqual(result["metrics"]["open_demand"], 980)
        self.assertEqual(result["metrics"]["incoming"], 240)
        self.assertEqual(result["metrics"]["shortage"], 520)
        self.assertEqual(len(result["explanation"]), 3)

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
