# -*- coding: utf-8 -*-
"""调 uapis.cn 的那一层：密钥不能用就退回匿名（访客额度）。

**为什么非要有这一层**：uapis 的免费端点本来不带密钥也能用，但一旦带上一个
**无效或没积分的密钥，反而会被拒**（官方错误码表里 ``UNAUTHORIZED`` 401 的说明
就是"Key 无效、过期、已删除**或未携带**"，``INSUFFICIENT_CREDITS`` 402 是"积分与
余额不足"）。也就是说配了一把坏密钥，比不配还糟——每日一言、农历、天气会一起挂掉。

所以这里统一做：带密钥请求 → 撞上 401/402 就把它记成"暂时别用了"，**当场改用匿名
重试一次**（访客额度，不影响这次请求），随后一段时间不再带这个头。等冷却过去会再
试一次密钥，所以充值之后不用重启服务，自己就恢复了。

密钥换了（``uapi_key`` 变了）就重新认，不受上一把的冷却影响。
"""
from __future__ import annotations

import threading
import time

from . import net
from .log import debug, out, warn

# 踩到 401/402 之后，多久之内不再带这个密钥。短了会一直白挨拒，
# 长了充值之后要等太久才恢复（到点会自动再试）。
COOLDOWN_SECONDS = 30 * 60

# 401 = UNAUTHORIZED（密钥无效/过期/已删），402 = INSUFFICIENT_CREDITS（积分用完）
DEAD_STATUS = (401, 402)

_lock = threading.Lock()
_state: dict = {"key": "", "until": 0.0}


def _mask(key: str) -> str:
    return (key[:9] + "***" + key[-4:]) if len(key) > 14 else "***"


def auth_headers(config) -> dict:
    """这次请求该带的认证头。没配密钥、或密钥正在冷却期，都返回空字典。"""
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
