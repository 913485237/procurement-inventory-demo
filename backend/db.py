from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    title TEXT NOT NULL,
    avatar_color TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    rating TEXT NOT NULL,
    lead_days INTEGER NOT NULL,
    on_time_rate REAL NOT NULL,
    contact TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    specification TEXT NOT NULL,
    unit TEXT NOT NULL,
    current_stock REAL NOT NULL,
    safety_stock REAL NOT NULL,
    unit_cost REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY,
    po_number TEXT NOT NULL UNIQUE,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    status TEXT NOT NULL,
    expected_date TEXT NOT NULL,
    total_amount REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    id INTEGER PRIMARY KEY,
    purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    material_id INTEGER NOT NULL REFERENCES materials(id),
    quantity REAL NOT NULL,
    received_quantity REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    tier TEXT NOT NULL,
    industry TEXT NOT NULL,
    contact TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_orders (
    id INTEGER PRIMARY KEY,
    order_number TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL,
    due_date TEXT NOT NULL,
    total_amount REAL NOT NULL,
    priority TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_order_items (
    id INTEGER PRIMARY KEY,
    sales_order_id INTEGER NOT NULL REFERENCES sales_orders(id),
    product_name TEXT NOT NULL,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    quantity REAL NOT NULL,
    material_qty_per_unit REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS production_tasks (
    id INTEGER PRIMARY KEY,
    task_number TEXT NOT NULL UNIQUE,
    sales_order_id INTEGER NOT NULL REFERENCES sales_orders(id),
    material_id INTEGER NOT NULL REFERENCES materials(id),
    required_qty REAL NOT NULL,
    completed_qty REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY,
    event_code TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    shortage_qty REAL NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by INTEGER REFERENCES users(id),
    decision_note TEXT,
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_number TEXT NOT NULL UNIQUE,
    sales_order_id INTEGER NOT NULL REFERENCES sales_orders(id),
    status TEXT NOT NULL,
    shipped_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL UNIQUE,
    sales_order_id INTEGER NOT NULL REFERENCES sales_orders(id),
    amount REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS ai_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    command TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_trail TEXT NOT NULL,
    plan TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    user_id INTEGER,
    role TEXT NOT NULL,
    event_type TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT NOT NULL,
    provider TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sales_orders_status ON sales_orders(status);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp DESC);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if existing == 0:
                self._seed(conn)

    def reset(self) -> None:
        drop_order = [
            "audit_logs", "ai_interactions", "invoices", "shipments", "approvals",
            "risk_events", "production_tasks", "sales_order_items", "sales_orders",
            "customers", "purchase_order_items", "purchase_orders", "materials",
            "suppliers", "users",
        ]
        with self.connection() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            for table in drop_order:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.executescript(SCHEMA)
            self._seed(conn)
            conn.execute("PRAGMA foreign_keys = ON")

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.connection() as conn:
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid or 0)

    def _seed(self, conn: sqlite3.Connection) -> None:
        today = date.today()
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        d = lambda days: (today + timedelta(days=days)).isoformat()

        conn.executemany(
            "INSERT INTO users(id, name, role, title, avatar_color) VALUES (?, ?, ?, ?, ?)",
            [
                (1, "林澈", "admin", "数字化平台主管", "#375DFB"),
                (2, "周岚", "procurement", "采购经理", "#7C3AED"),
                (3, "陈宇", "warehouse", "仓库主管", "#0891B2"),
                (4, "赵敏", "sales", "销售经理", "#EA580C"),
                (5, "沈言", "finance", "财务专员", "#059669"),
            ],
        )
        conn.executemany(
            "INSERT INTO suppliers VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "SUP-001", "华东铝业集团", "A", 7, 96.8, "王经理 · 138****3812"),
                (2, "SUP-002", "恒力紧固件", "A", 5, 98.1, "刘经理 · 139****1046"),
                (3, "SUP-003", "南方密封科技", "B", 9, 91.6, "黄经理 · 136****5920"),
                (4, "SUP-004", "北辰新材料", "B", 12, 89.4, "冯经理 · 137****6641"),
            ],
        )
        conn.executemany(
            "INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "M-AL-6061", "航空铝板", "6061-T6 / 3mm", "kg", 420, 200, 28.50),
                (2, "M-BOLT-M8", "高强螺栓", "M8×35 / 12.9级", "件", 9600, 2000, 1.25),
                (3, "M-SEAL-42", "工业密封圈", "耐高温 / 42mm", "件", 1800, 600, 3.80),
                (4, "M-PCB-C1", "控制板组件", "C1-Rev.4", "件", 310, 120, 186.00),
            ],
        )
        conn.executemany(
            "INSERT INTO purchase_orders VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "PO-202608-0142", 1, "运输中", d(5), 6840, now),
                (2, "PO-202608-0137", 2, "已确认", d(2), 15000, now),
                (3, "PO-202608-0129", 3, "部分到货", d(1), 9120, now),
            ],
        )
        conn.executemany(
            "INSERT INTO purchase_order_items VALUES (?, ?, ?, ?, ?)",
            [(1, 1, 1, 240, 0), (2, 2, 2, 12000, 0), (3, 3, 3, 3200, 1600)],
        )
        conn.executemany(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "CUS-001", "智造科技股份", "战略客户", "智能制造", "陆总 · 138****2108"),
                (2, "CUS-002", "星河自动化", "重点客户", "工业自动化", "冯经理 · 139****8314"),
                (3, "CUS-003", "宏远装备", "重点客户", "高端装备", "许经理 · 136****4766"),
                (4, "CUS-004", "联创机电", "普通客户", "机电集成", "郑经理 · 137****3207"),
            ],
        )
        conn.executemany(
            "INSERT INTO sales_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "SO-202608-0208", 1, "生产中", d(3), 688000, "紧急", now),
                (2, "SO-202608-0213", 2, "生产中", d(5), 426000, "高", now),
                (3, "SO-202608-0219", 3, "待排产", d(8), 315000, "高", now),
                (4, "SO-202608-0226", 4, "待出货", d(1), 186000, "普通", now),
            ],
        )
        conn.executemany(
            "INSERT INTO sales_order_items VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, 1, "智能装配站 A8", 1, 12, 45),
                (2, 2, "柔性输送单元 F3", 1, 10, 26),
                (3, 3, "视觉检测平台 V6", 1, 6, 30),
                (4, 4, "控制柜 C2", 4, 8, 1),
            ],
        )
        conn.executemany(
            "INSERT INTO production_tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "WO-202608-20405", 1, 1, 540, 120, "生产中"),
                (2, "WO-202608-20418", 2, 1, 260, 0, "待料"),
                (3, "WO-202608-20431", 3, 1, 180, 0, "待排产"),
                (4, "WO-202608-20444", 4, 4, 8, 6, "生产中"),
            ],
        )
        conn.execute(
            "INSERT INTO risk_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "RM-202405", "原材料短缺", 1, "处理中", "CRITICAL", 520,
             "航空铝板安全库存不足，将影响三张高优先级订单。", now),
        )
        approval_payload = json.dumps(
            {"material_code": "M-AL-6061", "quantity": 700, "supplier_id": 1,
             "affected_orders": ["SO-202608-0208", "SO-202608-0213", "SO-202608-0219"]},
            ensure_ascii=False,
        )
        conn.execute(
            """INSERT INTO approvals(action_type, requested_by, payload, status, risk_level,
               reason, created_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("purchase.create_replenishment", 2, approval_payload, "pending", "high",
             "航空铝板预计缺口 520kg，建议补货 700kg 并恢复安全库存。", now,
             "seed-replenishment-001"),
        )
        conn.execute(
            "INSERT INTO shipments VALUES (?, ?, ?, ?, ?, ?)",
            (1, "SHP-202608-0096", 4, "待承运", now, "seed-shipment-001"),
        )
        conn.executemany(
            """INSERT INTO audit_logs(timestamp, user_id, role, event_type, action, status,
               details, provider, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (now, 1, "admin", "system", "demo.initialized", "success",
                 json.dumps({"message": "演示环境初始化完成"}, ensure_ascii=False), "local", 18),
                (now, 2, "procurement", "risk", "risk.detected", "warning",
                 json.dumps({"event": "RM-202405", "shortage_qty": 520}, ensure_ascii=False), "rules", 42),
                (now, 2, "procurement", "approval", "purchase.create_replenishment", "pending",
                 json.dumps({"approval_id": 1, "quantity": 700}, ensure_ascii=False), "rules", 76),
            ],
        )


def json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def utc_now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")
