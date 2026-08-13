from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from backend.ai import AIService, ConfigStore
from backend.audit import list_audits, write_audit
from backend.auth import PermissionDenied, get_user, list_users, require
from backend.db import Database
from backend.erp import (
    convert_replenishment_advice,
    decide_approval,
    list_approvals,
    submit_manual_replenishment,
)
from backend.risk import analyze_material_risk, business_data, business_detail, dashboard_data, order_detail


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DB_PATH = Path(os.environ.get("ERP_DB_PATH", ROOT / "data" / "erp_demo.db"))
CONFIG_PATH = Path(os.environ.get("ERP_CONFIG_PATH", ROOT / "config.json"))
PUBLIC_DEMO = os.environ.get("PUBLIC_DEMO", "").strip().lower() in {"1", "true", "yes", "on"}

db = Database(DB_PATH)
config_store = ConfigStore(CONFIG_PATH)
ai_service = AIService(db, config_store)


BUSINESS_PERMISSIONS = {
    "orders": "order.read",
    "materials": "inventory.read",
    "suppliers": "supplier.read",
    "purchases": "purchase.read",
    "shipments": "shipment.read",
    "invoices": "invoice.read",
}


class PublicDemoReadOnly(Exception):
    """公开演示环境不允许执行的系统级变更。"""


def require_private_mode(action: str) -> None:
    if PUBLIC_DEMO:
        raise PublicDemoReadOnly(f"公开演示模式禁止{action}")


class ERPRequestHandler(BaseHTTPRequestHandler):
    server_version = "AetherERP/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._common_headers("application/json; charset=utf-8", 0)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/"):
                self._handle_api_get(parsed.path, parse_qs(parsed.query))
            else:
                self._serve_static(parsed.path)
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
            if parsed.path == "/api/ai/command":
                user = get_user(db, int(body.get("user_id", 1)))
                self._send_json(ai_service.handle_command(user, str(body.get("command", ""))))
                return
            if parsed.path == "/api/replenishment/manual":
                user = get_user(db, int(body.get("user_id", 1)))
                params = {key: value for key, value in body.items() if key != "user_id"}
                self._send_json(submit_manual_replenishment(db, user, params))
                return
            conversion_match = re.fullmatch(r"/api/replenishment/advice/(\d+)/convert", parsed.path)
            if conversion_match:
                user = get_user(db, int(body.get("user_id", 1)))
                params = {key: value for key, value in body.items() if key != "user_id"}
                self._send_json(
                    convert_replenishment_advice(db, user, int(conversion_match.group(1)), params)
                )
                return
            decision_match = re.fullmatch(r"/api/approvals/(\d+)/decision", parsed.path)
            if decision_match:
                user = get_user(db, int(body.get("user_id", 1)))
                result = decide_approval(
                    db, user, int(decision_match.group(1)), str(body.get("decision", "")),
                    str(body.get("note", "")),
                )
                self._send_json(result)
                return
            if parsed.path == "/api/settings":
                require_private_mode("修改 AI 配置")
                user = get_user(db, int(body.get("user_id", 1)))
                require(user, "system.configure")
                updates = body.get("settings", {})
                if not isinstance(updates, dict):
                    raise ValueError("设置数据格式错误")
                saved = config_store.save(updates)
                write_audit(db, user, "system", "settings.update", "success", {"fields": sorted(updates)})
                self._send_json({"settings": saved, "status": ai_service.status()})
                return
            if parsed.path == "/api/reset":
                require_private_mode("重置共享演示数据")
                user = get_user(db, int(body.get("user_id", 1)))
                require(user, "system.reset")
                if body.get("confirm") != "RESET":
                    raise ValueError("重置演示数据需要明确确认")
                db.reset()
                reset_user = get_user(db, 1)
                write_audit(db, reset_user, "system", "demo.reset", "success", {"message": "演示数据已重置"})
                self._send_json({"message": "演示数据已恢复到初始状态"})
                return
            self._send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._handle_error(exc)

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/health":
            self._send_json({
                "status": "ok", "service": "Aether AI ERP", "database": str(DB_PATH.name),
                "ai": ai_service.status(), "public_demo": PUBLIC_DEMO,
            })
            return
        if path == "/api/users":
            self._send_json({"users": list_users(db)})
            return

        user_id = int(query.get("user_id", ["1"])[0])
        user = get_user(db, user_id)
        if path == "/api/dashboard":
            require(user, "dashboard.read")
            self._send_json(dashboard_data(db))
            return
        if path == "/api/risk":
            require(user, "risk.read")
            material = query.get("material", ["M-AL-6061"])[0]
            self._send_json(analyze_material_risk(db, material))
            return
        if path == "/api/business":
            data_type = query.get("type", ["orders"])[0]
            permission = BUSINESS_PERMISSIONS.get(data_type)
            if not permission:
                raise ValueError("不支持的业务数据类型")
            require(user, permission)
            self._send_json({"type": data_type, "items": business_data(db, data_type)})
            return
        business_detail_match = re.fullmatch(r"/api/business/([a-z]+)/([0-9]+)", path)
        if business_detail_match:
            data_type = business_detail_match.group(1)
            permission = BUSINESS_PERMISSIONS.get(data_type)
            if not permission or data_type == "orders":
                raise ValueError("不支持的业务详情类型")
            require(user, permission)
            self._send_json(business_detail(db, data_type, int(business_detail_match.group(2))))
            return
        order_match = re.fullmatch(r"/api/orders/(\d+)", path)
        if order_match:
            require(user, "order.read")
            self._send_json(order_detail(db, int(order_match.group(1))))
            return
        if path == "/api/approvals":
            require(user, "approval.read")
            status = query.get("status", [None])[0]
            self._send_json({"approvals": list_approvals(db, status)})
            return
        if path == "/api/audits":
            require(user, "audit.read")
            limit = int(query.get("limit", ["100"])[0])
            self._send_json({"audits": list_audits(db, limit)})
            return
        if path == "/api/settings":
            require(user, "system.configure")
            self._send_json({"settings": config_store.public(), "status": ai_service.status()})
            return
        self._send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("请求数据过大")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON 请求格式错误") from exc
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    def _serve_static(self, request_path: str) -> None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            self._send_json({"error": "路径无效"}, HTTPStatus.BAD_REQUEST)
            return
        if not target.is_file():
            target = WEB_ROOT / "index.html"
        content = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self._common_headers(content_type, len(content))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._common_headers("application/json; charset=utf-8", len(content))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _common_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, PermissionDenied):
            self._send_json(
                {"error": str(exc), "code": "PERMISSION_DENIED", "permission": exc.permission, "role": exc.role},
                HTTPStatus.FORBIDDEN,
            )
        elif isinstance(exc, PublicDemoReadOnly):
            self._send_json(
                {"error": str(exc), "code": "PUBLIC_DEMO_READ_ONLY"},
                HTTPStatus.FORBIDDEN,
            )
        elif isinstance(exc, (ValueError, KeyError)):
            self._send_json({"error": str(exc), "code": "BAD_REQUEST"}, HTTPStatus.BAD_REQUEST)
        else:
            self._send_json({"error": "服务处理失败", "code": "INTERNAL_ERROR"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args) -> None:
        message = format % args
        print(f"[Aether ERP] {self.address_string()} · {message}")


def run_server(host: str, port: int, open_browser: bool = False) -> None:
    db.initialize()
    server = ThreadingHTTPServer((host, port), ERPRequestHandler)
    url = f"http://{host}:{port}"
    print("\nAether AI ERP 已启动")
    print(f"访问地址：{url}")
    print("按 Ctrl+C 停止服务\n")
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Aether AI 原生制造业 ERP")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--reset-data", action="store_true", help="启动前重置演示数据")
    args = parser.parse_args()
    db.initialize()
    if args.reset_data:
        db.reset()
    run_server(args.host, args.port, args.open)


if __name__ == "__main__":
    main()
