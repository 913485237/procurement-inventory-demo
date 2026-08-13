from __future__ import annotations

import unittest
from unittest.mock import patch

import app


class PublicDemoModeTest(unittest.TestCase):
    def test_public_demo_blocks_system_mutations(self) -> None:
        with patch.object(app, "PUBLIC_DEMO", True):
            with self.assertRaisesRegex(app.PublicDemoReadOnly, "修改 AI 配置"):
                app.require_private_mode("修改 AI 配置")
            with self.assertRaisesRegex(app.PublicDemoReadOnly, "重置共享演示数据"):
                app.require_private_mode("重置共享演示数据")

    def test_private_mode_keeps_local_management_available(self) -> None:
        with patch.object(app, "PUBLIC_DEMO", False):
            self.assertIsNone(app.require_private_mode("修改 AI 配置"))


if __name__ == "__main__":
    unittest.main()
