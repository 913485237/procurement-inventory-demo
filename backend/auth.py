from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .db import Database


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "procurement": {
        "dashboard.read", "risk.read", "supplier.read", "purchase.read",
        "purchase.create", "replenishment.advise", "approval.read", "audit.read",
    },
    "warehouse": {
        "dashboard.read", "risk.read", "inventory.read", "order.read",
        "shipment.read", "shipment.confirm", "replenishment.advise", "approval.read", "audit.read",
    },
    "sales": {
        "dashboard.read", "risk.read", "customer.read", "order.read",
        "order.coordinate", "shipment.read", "replenishment.advise", "approval.read", "audit.read",
    },
    "finance": {
        "dashboard.read", "risk.read", "order.read", "shipment.read", "invoice.read",
        "invoice.create", "replenishment.advise", "approval.read", "audit.read",
    },
}


class PermissionDenied(Exception):
    def __init__(self, permission: str, role: str):
        self.permission = permission
        self.role = role
        super().__init__(f"角色 {role} 缺少权限：{permission}")


@dataclass(frozen=True)
class UserContext:
    id: int
    name: str
    role: str
    title: str
    avatar_color: str

    @property
    def permissions(self) -> list[str]:
        values = ROLE_PERMISSIONS.get(self.role, set())
        return sorted(values)


def get_user(db: Database, user_id: int) -> UserContext:
    row = db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not row:
        raise ValueError("用户不存在")
    return UserContext(**row)


def list_users(db: Database) -> list[dict]:
    users = db.fetch_all("SELECT * FROM users ORDER BY id")
    for user in users:
        user["permissions"] = sorted(ROLE_PERMISSIONS.get(user["role"], set()))
    return users


def can(role: str, permission: str) -> bool:
    allowed = ROLE_PERMISSIONS.get(role, set())
    return "*" in allowed or permission in allowed


def require(user: UserContext, permission: str) -> None:
    if not can(user.role, permission):
        raise PermissionDenied(permission, user.role)


def describe_permissions(user: UserContext) -> dict:
    permission_labels = {
        "*": "全部业务与系统配置",
        "dashboard.read": "查看经营总览",
        "risk.read": "查看供应链风险",
        "supplier.read": "查看供应商",
        "purchase.read": "查看采购单",
        "purchase.create": "创建采购草稿",
        "replenishment.advise": "提交人工补货建议",
        "inventory.read": "查看库存",
        "order.read": "查看销售订单",
        "order.coordinate": "发起交付协调",
        "shipment.read": "查看出货单",
        "shipment.confirm": "确认出货",
        "customer.read": "查看客户",
        "invoice.read": "查看发票",
        "invoice.create": "创建发票",
        "approval.read": "查看审批",
        "audit.read": "查看审计记录",
    }
    permissions = user.permissions
    return {
        "user": user.name,
        "title": user.title,
        "role": user.role,
        "permissions": [{"code": p, "label": permission_labels.get(p, p)} for p in permissions],
        "message": f"AI 当前继承 {user.name}（{user.title}）的权限边界。",
    }
