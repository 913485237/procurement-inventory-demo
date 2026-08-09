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

            risk = request_json(base_url + "/api/risk?user_id=1")
            assert risk["metrics"]["shortage"] == 520
            assert risk["metrics"]["affected_orders"] == 3

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
            print(f"FULL OK · dashboard/risk/ai/approval/permission/audit · {base_url}")
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

