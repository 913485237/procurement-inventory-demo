from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.ai import AIService, ConfigStore, ProviderUnavailable
from backend.auth import PermissionDenied, get_user
from backend.db import Database


class AITest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.db = Database(base / "test.db")
        self.db.initialize()
        self.config = ConfigStore(base / "config.json")
        self.config.save({"external_enabled": False, "ollama_enabled": False})
        self.service = AIService(self.db, self.config)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_rule_engine_analyzes_shortage_without_ai_services(self) -> None:
        admin = get_user(self.db, 1)
        response = self.service.handle_command(admin, "分析航空铝板缺料风险和影响订单")
        self.assertEqual(response["provider"], "rules")
        self.assertEqual(response["plan"]["action"], "risk.analyze")
        metrics = response["execution"]["result"]["metrics"]
        self.assertEqual(metrics["shortage"], 520)
        self.assertEqual(metrics["affected_orders"], 3)

    def test_ai_inherits_user_permission(self) -> None:
        sales = get_user(self.db, 4)
        with self.assertRaises(PermissionDenied):
            self.service.handle_command(sales, "生成航空铝板补货采购单")

    def test_external_and_ollama_fail_then_rules_take_over(self) -> None:
        self.config.save({
            "external_enabled": True,
            "external_api_key": "test-key",
            "ollama_enabled": True,
        })
        admin = get_user(self.db, 1)
        with patch.object(self.service, "_external_plan", side_effect=ProviderUnavailable("offline")), \
             patch.object(self.service, "_ollama_plan", side_effect=ProviderUnavailable("offline")):
            response = self.service.handle_command(admin, "查看经营总览")
        self.assertEqual(response["provider"], "rules")
        self.assertEqual([item["status"] for item in response["provider_trail"]], ["failed", "failed", "success"])


if __name__ == "__main__":
    unittest.main()

