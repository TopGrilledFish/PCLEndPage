# -*- coding: utf-8 -*-

from __future__ import annotations

import threading
import time

from . import net
from .log import debug, out, warn

COOLDOWN_SECONDS = 30 * 60

DEAD_STATUS = (401, 402)

_lock = threading.Lock()
_state: dict = {"key": "", "until": 0.0}


def _mask(key: str) -> str:
    return (key[:9] + "***" + key[-4:]) if len(key) > 14 else "***"


def auth_headers(config) -> dict:
    key = str(getattr(config, "uapi_key", "") or "").strip()
    if not key:
        return {}
    with _lock:
        cooling = _state["key"] == key and time.time() < _state["until"]
    return {} if cooling else {"Authorization": "Bearer " + key}


def cooling_down(config) -> float:
    """还要多少秒才重新试密钥；0 表示正常（给体检看的）。"""
    key = str(getattr(config, "uapi_key", "") or "").strip()
    with _lock:
        if key and _state["key"] == key:
            return max(0.0, _state["until"] - time.time())
    return 0.0


def _mark_dead(config, status: int) -> None:
    """记下"这把密钥先别用了"。只在冷却开始那一刻打一条 warn，免得刷屏。"""
    key = str(getattr(config, "uapi_key", "") or "").strip()
    now = time.time()
    with _lock:
        # 只有"从没冷却"或"这不是同一把密钥/上一轮已经过期"才算新一轮
        fresh = _state["key"] != key or now >= _state["until"]
        _state["key"] = key
        _state["until"] = now + COOLDOWN_SECONDS
    if fresh:
        why = "积分用完（402）" if status == 402 else "密钥无效（401）"
        warn("[UAPI] 密钥 " + _mask(key) + " " + why + "，改用匿名额度（访客额度）请求；"
             + str(round(COOLDOWN_SECONDS / 60)) + " 分钟后会自动再试一次"
             + "（充值或换密钥之后不用重启，到点自己恢复）")


def reset() -> None:
    """清掉冷却状态（体检和测试用）。"""
    with _lock:
        _state["key"] = ""
        _state["until"] = 0.0


def fetch_json_ex(url: str, config, timeout: float = 6.0, retries: int = 1):
    """带密钥 GET 一次；密钥被判失效就**立刻匿名重试**。返回 ``(数据, 状态码)``。"""
    headers = auth_headers(config)
    data, status = net.fetch_json_ex(url, timeout=timeout, retries=retries, headers=headers)
    if headers and status in DEAD_STATUS:
        _mark_dead(config, status)
        data, status = net.fetch_json_ex(url, timeout=timeout, retries=retries)
        debug("[UAPI] 匿名重试 " + url[:80] + " → " + str(status))
    return data, status


def fetch_json(url: str, config, timeout: float = 6.0, retries: int = 1):
    """只要数据的那份（不需要状态码的调用方用这个）。"""
    return fetch_json_ex(url, config, timeout=timeout, retries=retries)[0]


def log_state(config) -> None:
    """启动时把密钥状态写一行日志——**不打印密钥本身**。"""
    key = str(getattr(config, "uapi_key", "") or "").strip()
    out("[UAPI] uapis.cn 密钥：" + ("已配置 " + _mask(key) if key else "未配置（用匿名额度）"))
