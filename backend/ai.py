from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .audit import write_audit
from .auth import PermissionDenied, UserContext, describe_permissions
from .db import Database, json_value, utc_now_text
from .erp import ACTION_REGISTRY, execute_or_request


DEFAULT_CONFIG: dict[str, Any] = {
    "external_enabled": False,
    "external_base_url": "https://api.openai.com/v1",
    "external_api_key": "",
    "external_model": "gpt-5-mini",
    "ollama_enabled": True,
    "ollama_base_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen3:8b",
    "timeout_seconds": 2.5,
}


class ProviderUnavailable(RuntimeError):
    pass


class ConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        config = dict(DEFAULT_CONFIG)
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    config.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass
        return config

    def save(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_CONFIG)
        current = self.load()
        for key, value in updates.items():
            if key in allowed:
                current[key] = value
        self.path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.public(current)

    def public(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        data = dict(config or self.load())
        key = str(data.pop("external_api_key", ""))
        data["external_api_key_configured"] = bool(key)
        data["external_api_key_masked"] = f"••••{key[-4:]}" if key else ""
        return data


class AIService:
    def __init__(self, db: Database, config: ConfigStore):
        self.db = db
        self.config_store = config

    def status(self) -> dict[str, Any]:
        config = self.config_store.load()
        return {
            "external": {
                "enabled": bool(config["external_enabled"]),
                "configured": bool(config["external_api_key"] and config["external_model"]),
                "label": "外部 AI",
            },
            "ollama": {
                "enabled": bool(config["ollama_enabled"]),
                "configured": bool(config["ollama_base_url"] and config["ollama_model"]),
                "label": "本地 Ollama",
            },
            "rules": {"enabled": True, "configured": True, "label": "内置规则引擎"},
            "fallback_order": ["external", "ollama", "rules"],
        }

    def handle_command(self, user: UserContext, command: str) -> dict[str, Any]:
        command = command.strip()
        if not command:
            raise ValueError("请输入指令")
        started = time.perf_counter()
        plan, provider, trail = self._plan_with_fallback(command)
        action = plan["action"]
        try:
            if action == "permissions.describe":
                execution = {"mode": "executed", "action": action, "result": describe_permissions(user)}
            elif action == "none":
                execution = {"mode": "guidance", "result": {"message": plan["summary"]}}
            else:
                execution = execute_or_request(
                    self.db, user, action, plan.get("params", {}), plan.get("reason", plan["summary"])
                )
        except PermissionDenied as exc:
            duration = int((time.perf_counter() - started) * 1000)
            write_audit(
                self.db, user, "permission", action, "denied",
                {"command": command, "required": exc.permission, "role": exc.role}, provider, duration,
            )
            raise

        duration = int((time.perf_counter() - started) * 1000)
        response = {
            "provider": provider,
            "provider_label": {"external": "外部 AI", "ollama": "本地 Ollama", "rules": "规则引擎"}[provider],
            "provider_trail": trail,
            "plan": plan,
            "execution": execution,
            "duration_ms": duration,
        }
        self.db.execute(
            """INSERT INTO ai_interactions(user_id, command, provider, provider_trail, plan, result, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user.id, command, provider, json_value(trail), json_value(plan), json_value(execution), utc_now_text()),
        )
        write_audit(
            self.db, user, "ai", action, "success",
            {"command": command, "mode": execution["mode"], "trail": trail}, provider, duration,
        )
        return response

    def _plan_with_fallback(self, command: str) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        config = self.config_store.load()
        trail: list[dict[str, Any]] = []

        if config["external_enabled"] and config["external_api_key"]:
            try:
                started = time.perf_counter()
                plan = self._external_plan(command, config)
                trail.append({"provider": "external", "status": "success", "duration_ms": _elapsed(started)})
                return self._normalize_plan(plan), "external", trail
            except Exception as exc:
                trail.append({"provider": "external", "status": "failed", "reason": _safe_error(exc)})
        else:
            trail.append({"provider": "external", "status": "skipped", "reason": "未启用或未配置密钥"})

        if config["ollama_enabled"]:
            try:
                started = time.perf_counter()
                plan = self._ollama_plan(command, config)
                trail.append({"provider": "ollama", "status": "success", "duration_ms": _elapsed(started)})
                return self._normalize_plan(plan), "ollama", trail
            except Exception as exc:
                trail.append({"provider": "ollama", "status": "failed", "reason": _safe_error(exc)})
        else:
            trail.append({"provider": "ollama", "status": "skipped", "reason": "未启用"})

        started = time.perf_counter()
        plan = self._rule_plan(command)
        trail.append({"provider": "rules", "status": "success", "duration_ms": _elapsed(started)})
        return plan, "rules", trail

    def _external_plan(self, command: str, config: dict[str, Any]) -> dict[str, Any]:
        base_url = str(config["external_base_url"]).rstrip("/")
        endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
        payload = {
            "model": config["external_model"],
            "messages": [
                {"role": "system", "content": _planner_prompt()},
                {"role": "user", "content": command},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        data = _post_json(
            endpoint, payload, float(config["timeout_seconds"]),
            {"Authorization": f"Bearer {config['external_api_key']}"},
        )
        try:
            content = data["choices"][0]["message"]["content"]
            return _extract_json(content)
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable("外部模型返回格式不正确") from exc

    def _ollama_plan(self, command: str, config: dict[str, Any]) -> dict[str, Any]:
        endpoint = str(config["ollama_base_url"]).rstrip("/") + "/api/chat"
        payload = {
            "model": config["ollama_model"],
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _planner_prompt()},
                {"role": "user", "content": command},
            ],
            "options": {"temperature": 0.1},
        }
        data = _post_json(endpoint, payload, float(config["timeout_seconds"]))
        try:
            return _extract_json(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise ProviderUnavailable("Ollama 返回格式不正确") from exc

    def _rule_plan(self, command: str) -> dict[str, Any]:
        text = command.lower()
        if any(word in text for word in ("补货", "采购方案", "采购单")):
            return _make_plan(
                "purchase.create_replenishment",
                "为航空铝板生成 700kg 补货采购方案，并提交人工审批。",
                {"material_code": "M-AL-6061", "quantity": 700, "supplier_id": 1,
                 "affected_orders": ["SO-202608-0208", "SO-202608-0213", "SO-202608-0219"]},
                "预计缺口 520kg，需要补足安全库存并保留交付缓冲。",
            )
        if any(word in text for word in ("出货", "开票", "发票")):
            return _make_plan(
                "fulfillment.ship_and_invoice",
                "为受影响订单创建出货和开票执行申请。",
                {"order_ids": [1, 2, 3]},
                "出货与开票属于高风险业务动作，需要管理员审批。",
            )
        if any(word in text for word in ("权限", "能做什么", "能干什么", "我是谁")):
            return _make_plan("permissions.describe", "说明当前身份及 AI 继承的权限。", {}, "权限透明可解释。")
        if any(word in text for word in ("缺料", "短缺", "风险", "影响订单", "供应链")):
            return _make_plan(
                "risk.analyze", "分析航空铝板短缺及其供应链影响。",
                {"material_code": "M-AL-6061"}, "基于库存、在途采购和生产任务进行可解释计算。",
            )
        if any(word in text for word in ("总览", "经营", "看板", "订单情况")):
            return _make_plan("dashboard.get", "读取经营总览和核心指标。", {}, "经营数据查询属于低风险操作。")
        return {
            "intent": "guidance",
            "action": "none",
            "summary": "我可以分析缺料风险、说明权限、生成补货方案，或发起出货开票审批。",
            "params": {},
            "reason": "规则引擎未匹配到明确业务动作。",
            "risk_level": "low",
            "requires_approval": False,
            "steps": [{"name": "识别意图", "status": "completed", "detail": "未匹配到受控业务动作"}],
        }

    def _normalize_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        action = str(plan.get("action", "none"))
        if action not in ACTION_REGISTRY and action != "none":
            raise ProviderUnavailable(f"模型请求了未登记动作：{action}")
        if action == "none":
            return self._rule_plan(str(plan.get("summary", "")))
        spec = ACTION_REGISTRY[action]
        return {
            "intent": str(plan.get("intent", action)),
            "action": action,
            "summary": str(plan.get("summary", spec["label"])),
            "params": plan.get("params") if isinstance(plan.get("params"), dict) else {},
            "reason": str(plan.get("reason", "由 AI 根据用户指令生成。")),
            "risk_level": spec["risk"],
            "requires_approval": spec["risk"] == "high",
            "steps": plan.get("steps") if isinstance(plan.get("steps"), list) else _default_steps(action, spec),
        }


def _make_plan(action: str, summary: str, params: dict[str, Any], reason: str) -> dict[str, Any]:
    spec = ACTION_REGISTRY[action]
    return {
        "intent": action,
        "action": action,
        "summary": summary,
        "params": params,
        "reason": reason,
        "risk_level": spec["risk"],
        "requires_approval": spec["risk"] == "high",
        "steps": _default_steps(action, spec),
    }


def _default_steps(action: str, spec: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"name": "识别业务意图", "status": "completed", "detail": spec["label"]},
        {"name": "继承当前用户权限", "status": "completed", "detail": "使用统一权限边界"},
        {"name": "校验动作与参数", "status": "completed", "detail": f"受控动作：{action}"},
        {"name": "执行或提交审批", "status": "pending" if spec["risk"] == "high" else "completed",
         "detail": "高风险动作等待人工审批" if spec["risk"] == "high" else "低风险动作自动执行"},
    ]


def _planner_prompt() -> str:
    actions = {
        name: {"label": spec["label"], "risk": spec["risk"], "permissions": spec["permissions"]}
        for name, spec in ACTION_REGISTRY.items()
    }
    return (
        "你是制造业 ERP 的计划生成器。只能输出 JSON，不得输出代码或 SQL。"
        "格式：{intent, action, summary, params, reason, steps:[{name,detail}]}。"
        f"允许动作：{json.dumps(actions, ensure_ascii=False)}。"
        "涉及航空铝板默认 material_code=M-AL-6061；出货开票默认 order_ids=[1,2,3]。"
    )


def _post_json(url: str, payload: dict[str, Any], timeout: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.5, min(timeout, 15.0))) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise ProviderUnavailable(str(exc)) from exc


def _extract_json(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    text = str(content).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("计划必须是 JSON 对象")
    return value


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _safe_error(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:160] or exc.__class__.__name__

