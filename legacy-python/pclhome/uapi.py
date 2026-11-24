# -*- coding: utf-8 -*-
"""调 uapis.cn 的那一层：多把密钥轮着用，全都不行才退回匿名（访客额度）。

**为什么非要有这一层**：uapis 的免费端点本来不带密钥也能用，但一旦带上一个
**无效或没积分的密钥，反而会被拒**（官方错误码表里 ``UNAUTHORIZED`` 401 的说明
就是"Key 无效、过期、已删除**或未携带**"，``INSUFFICIENT_CREDITS`` 402 是"积分与
余额不足"）。也就是说配了一把坏密钥，比不配还糟——每日一言、农历、天气会一起挂掉。

现在的规则：

1. 按配置顺序挑**第一把没在冷却**的密钥带上去；
2. 撞上 401/402 只冷却**那一把**，当场换下一把重试；
3. 所有密钥都在冷却（或一把都没配 / 都被拒过），才用匿名额度请求；
4. 冷却到点（默认 30 分钟）会自动再试一次，所以充完积分不用重启。

密钥换了（``uapi_keys`` 变了）就按新表重新认，不受上一批的冷却影响。
"""
from __future__ import annotations

import threading
import time

from . import net
from .log import debug, out, warn

# 踩到 401/402 之后，多久之内不再用这把密钥。短了会一直白挨拒，
# 长了充值之后要等太久才恢复（到点会自动再试）。
COOLDOWN_SECONDS = 30 * 60

# 401 = UNAUTHORIZED（密钥无效/过期/已删），402 = INSUFFICIENT_CREDITS（积分用完）
DEAD_STATUS = (401, 402)

_lock = threading.Lock()
# 密钥 → 冷却截止时间。只记被拒过的，没配的/能用的都不在表里。
_state: dict[str, float] = {}


def _mask(key: str) -> str:
    return (key[:9] + "***" + key[-4:]) if len(key) > 14 else "***"


def keys(config) -> list[str]:
    """配置里所有 uapi 密钥（旧的单值 ``uapi_key`` 和新列表 ``uapi_keys`` 都算）。"""
    getter = getattr(config, "uapi_key_list", None)
    if callable(getter):
        try:
            return [str(k).strip() for k in getter() if str(k).strip()]
        except Exception:                                  # 配置写坏了别拖垮请求
            pass
    key = str(getattr(config, "uapi_key", "") or "").strip()
    return [key] if key else []


def _cooling(key: str, now: float | None = None) -> bool:
    moment = time.time() if now is None else now
    with _lock:
        return _state.get(key, 0.0) > moment


def usable_keys(config) -> list[str]:
    """这次请求可以按顺序尝试的密钥（跳过冷却中的）。"""
    now = time.time()
    with _lock:
        return [key for key in keys(config) if _state.get(key, 0.0) <= now]


def auth_headers(config) -> dict:
    """这次请求该带的认证头。没配密钥、或全都在冷却期，都返回空字典。"""
    for key in usable_keys(config):
        return {"Authorization": "Bearer " + key}
    return {}


def cooling_down(config) -> float:
    """还有多少秒才会重新有可用密钥；0 表示现在就有能用的（给体检看的）。"""
    now = time.time()
    with _lock:
        remain = [_state[key] - now for key in keys(config) if _state.get(key, 0.0) > now]
    if not remain:
        return 0.0
    if len(remain) < len(keys(config)):
        return 0.0                                          # 还有别的密钥能用
    return max(0.0, min(remain))                            # 全冷却：报最早恢复的那把


def _mark_dead(key: str, status: int) -> None:
    """记下"这把密钥先别用了"。只在冷却开始那一刻打一条 warn，免得刷屏。"""
    if not key:
        return
    now = time.time()
    with _lock:
        # 只有"从没冷却"或"上一轮已经过期"才算新一轮
        fresh = _state.get(key, 0.0) <= now
        _state[key] = now + COOLDOWN_SECONDS
    if fresh:
        why = "积分用完（402）" if status == 402 else "密钥无效（401）"
        warn("[UAPI] 密钥 " + _mask(key) + " " + why + "，换下一把；"
             + str(round(COOLDOWN_SECONDS / 60)) + " 分钟后会自动再试一次"
             + "（充值或换密钥之后不用重启，到点自己恢复）")


def reset() -> None:
    """清掉冷却状态（体检和测试用）。"""
    with _lock:
        _state.clear()


def fetch_json_ex(url: str, config, timeout: float = 6.0, retries: int = 1):
    """按顺序试密钥，逐把失败就换下一把，全不行才匿名。返回 ``(数据, 状态码)``。"""
    attempted: list[str] = []
    tried_status = 0
    with _lock:
        cooling = {key: _state.get(key, 0.0) for key in keys(config)}
    for key in keys(config):
        if cooling.get(key, 0.0) > time.time():
            continue
        data, status = net.fetch_json_ex(url, timeout=timeout, retries=retries,
                                         headers={"Authorization": "Bearer " + key})
        if status in DEAD_STATUS:
            _mark_dead(key, status)
            attempted.append(key)
            tried_status = status
            continue
        if attempted:
            debug("[UAPI] 换成 " + _mask(key) + " 之后成功（上一把 " + str(tried_status) + "）")
        return data, status
    if attempted:
        debug("[UAPI] " + str(len(attempted)) + " 把密钥都被拒，改用匿名额度：" + url[:80])
    # 没配密钥 / 全在冷却 / 刚被拒完 —— 一律走匿名额度
    return net.fetch_json_ex(url, timeout=timeout, retries=retries)


def fetch_json(url: str, config, timeout: float = 6.0, retries: int = 1):
    """只要数据的那份（不需要状态码的调用方用这个）。"""
    return fetch_json_ex(url, config, timeout=timeout, retries=retries)[0]


def log_state(config) -> None:
    """启动时把密钥状态写几行日志——**不打印密钥本身**。"""
    all_keys = keys(config)
    if not all_keys:
        out("[UAPI] uapis.cn 密钥：未配置（用匿名额度）")
        return
    now = time.time()
    with _lock:
        parts = ["可用" if _state.get(key, 0.0) <= now else "冷却中" for key in all_keys]
    out("[UAPI] uapis.cn 密钥 " + str(len(all_keys)) + " 把：" + "、".join(
        _mask(key) + "（" + state + "）" for key, state in zip(all_keys, parts)))
