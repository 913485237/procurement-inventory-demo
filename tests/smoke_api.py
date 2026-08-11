from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_until_ready(base_url: str) -> dict:
    last_error = None
    for _ in range(50):
        try:
            return request_json(base_url + "/api/health")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"服务未就绪：{last_error}")


def run(quick: bool) -> None:
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        config_path = temp / "config.json"
        config_path.write_text(
            json.dumps({"external_enabled": False, "ollama_enabled": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["ERP_DB_PATH"] = str(temp / "smoke.db")
        environment["ERP_CONFIG_PATH"] = str(config_path)
        process = subprocess.Popen(
            [sys.executable, "app.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            health = wait_until_ready(base_url)
            assert health["status"] == "ok"
            if quick:
                print(f"QUICK OK · health · {base_url}")
                return

            dashboard = request_json(base_url + "/api/dashboard?user_id=1")
            assert dashboard["metrics"]["order_count"] == 4

            order = request_json(base_url + "/api/orders/4?user_id=1")
            assert order["order"]["order_number"] == "SO-202608-0226"
            assert order["items"][0]["product_name"] == "控制柜 C2"
            assert order["shipments"][0]["shipment_number"] == "SHP-202608-0096"

            material = request_json(base_url + "/api/business/materials/1?user_id=1")
            assert material["record"]["code"] == "M-AL-6061"
            supplier = request_json(base_url + "/api/business/suppliers/1?user_id=1")
            assert supplier["stats"]["purchase_count"] == 1
            purchase = request_json(base_url + "/api/business/purchases/1?user_id=1")
            assert purchase["items"][0]["material_code"] == "M-AL-6061"
            shipment = request_json(base_url + "/api/business/shipments/1?user_id=1")
            assert shipment["record"]["order_number"] == "SO-202608-0226"

            risk = request_json(base_url + "/api/risk?user_id=1")
            assert risk["metrics"]["shortage"] == 520
            assert risk["metrics"]["affected_orders"] == 3

            advice = request_json(
                base_url + "/api/replenishment/manual",
                {
                    "user_id": 3, "material_code": "M-AL-6061", "quantity": 660,
                    "suggested_supplier": "华东铝业集团", "expected_date": "2099-08-20",
                    "urgency": "紧急", "situation": "仓库盘点确认缺口扩大。",
                    "rationale": "依据现场盘点和历史耗用判断。",
                },
            )
            assert advice["mode"] == "advice"
            assert advice["approval"]["status"] == "pending_review"
            converted = request_json(
                base_url + f"/api/replenishment/advice/{advice['approval']['id']}/convert",
                {
                    "user_id": 2, "material_code": "M-AL-6061", "quantity": 660,
                    "supplier_id": 1, "expected_date": "2099-08-20", "urgency": "紧急",
                    "situation": "仓库盘点确认缺口扩大。", "rationale": "采购复核确认。",
                },
            )
            manual_approval_id = converted["approval"]["id"]
            manual_approved = request_json(
                base_url + f"/api/approvals/{manual_approval_id}/decision",
                {"user_id": 1, "decision": "approved", "note": "冒烟测试批准人工方案"},
            )
            assert manual_approved["execution"]["purchase_order"].startswith("PO-MAN-")
            assert manual_approved["execution"]["expected_date"] == "2099-08-20"

            ai = request_json(
                base_url + "/api/ai/command",
                {"user_id": 1, "command": "分析航空铝板缺料风险和影响订单"},
            )
            assert ai["provider"] == "rules"
            assert ai["plan"]["action"] == "risk.analyze"

            request = request_json(
                base_url + "/api/ai/command",
                {"user_id": 2, "command": "生成航空铝板补货采购方案"},
            )
            approval_id = request["execution"]["approval"]["id"]
            approved = request_json(
                base_url + f"/api/approvals/{approval_id}/decision",
                {"user_id": 1, "decision": "approved", "note": "冒烟测试批准"},
            )
            assert approved["execution"]["quantity"] == 700

            fulfillment = request_json(
                base_url + "/api/ai/command",
                {"user_id": 1, "command": "为受影响订单出货并开票"},
            )
            fulfillment_approval_id = fulfillment["execution"]["approval"]["id"]
            request_json(
                base_url + f"/api/approvals/{fulfillment_approval_id}/decision",
                {"user_id": 1, "decision": "approved", "note": "冒烟测试交付批准"},
            )
            invoice_list = request_json(base_url + "/api/business?type=invoices&user_id=1")
            invoice_id = invoice_list["items"][0]["id"]
            invoice = request_json(base_url + f"/api/business/invoices/{invoice_id}?user_id=1")
            assert invoice["record"]["order_number"] == "SO-202608-0219"

            try:
                request_json(base_url + "/api/business/materials/1?user_id=5")
                raise AssertionError("财务角色越权查看物料详情未被拒绝")
            except urllib.error.HTTPError as exc:
                assert exc.code == 403

            try:
                request_json(
                    base_url + "/api/ai/command",
                    {"user_id": 4, "command": "生成航空铝板补货采购方案"},
                )
                raise AssertionError("销售角色越权采购未被拒绝")
            except urllib.error.HTTPError as exc:
                assert exc.code == 403

            audits = request_json(base_url + "/api/audits?user_id=1&limit=100")
            assert len(audits["audits"]) >= 6
            print(f"FULL OK · dashboard/details/risk/manual/ai/approval/permission/audit · {base_url}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    run(parser.parse_args().quick)
