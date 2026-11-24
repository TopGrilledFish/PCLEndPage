# -*- coding: utf-8 -*-
"""访问者 IP → 城市，给天气接口当参数用。

**为什么需要这一层**：uapis 的天气接口只能按*调用方* IP 定位，而"调用方"永远是
我们这台服务器。实测给它塞伪造头全都不认——``X-Forwarded-For``、``X-Real-IP``、
``CF-Connecting-IP``、``True-Client-IP``、``X-Client-IP``、``X-Originating-IP``、
``Forwarded`` 逐个试过，返回的城市一律是服务器所在地；接口也没有 ``?ip=`` 参数。

所以只能自己先把访问者 IP 换成城市，再用接口支持的 ``?adcode=`` / ``?city=`` 去查。
接口文档里 adcode 优先级最高且没有歧义（"福州"能指福建省福州市，也能指江西省抚州市），
所以优先用 adcode。

数据源按顺序试，第一个给出结果的算数：

    1. whois.pconline.com.cn   中文城市名 + cityCode（正是天气接口要的 adcode），约 200ms
    2. ip-api.com              境外可达性更好；国内 IP 精度一般，只当备胎

境外 IP 在 pconline 会回 ``err=noprovince``，这**不算失败**——直接返回 None，
让调用方退回"不带参数"的裸调用，由天气接口按服务器 IP 自己定位。
"""
from __future__ import annotations

import ipaddress
import json
import threading
import time
import urllib.request
from urllib.parse import quote as urlquote

from .config import BROWSER_UA
from .log import debug, out

# 查一个 IP 的耗时上限。这个查询串在用户请求的关键路径上，不能让它拖慢主页。
DEFAULT_TIMEOUT = 4.0

# 查到结果后缓存多久：IP 归属地基本不变，缓存久一点没坏处
DEFAULT_CACHE_HOURS = 24

# 查不到时也缓存一小会儿，避免同一个查不到的 IP 每次都去外呼
NEGATIVE_TTL = 600.0

# 内存缓存上限；超了直接清空（条目很便宜，重建代价只有一次查询）
MAX_ENTRIES = 4000

_CACHE: dict[str, tuple[float, dict | None]] = {}
_lock = threading.Lock()


def is_unlocatable(ip: str) -> bool:
    """内网/回环/保留地址没法定位（本机跑 PCL 时访问者就是 127.0.0.1）。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True                     # 不是合法 IP（"unknown" 等）
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


_is_unlocatable = is_unlocatable        # 兼容旧名字


def _get(url: str, timeout: float, encoding: str = "utf-8", referer: str = "") -> str | None:
    """取文本；失败返回 None（换下一个源，不抛异常）。"""
    headers = {"User-Agent": BROWSER_UA}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read().decode(encoding, errors="replace")
    except Exception as exc:
        debug("[Geo] " + url.split("/")[2] + " 取数失败：" + str(exc))
        return None


def _source_pconline(ip: str, timeout: float) -> dict | None:
    """太平洋电脑网。返回中文省市名 + cityCode（就是 adcode）。"""
    text = _get("https://whois.pconline.com.cn/ipJson.jsp?ip=" + urlquote(ip) + "&json=true",
                timeout, encoding="gbk", referer="https://whois.pconline.com.cn/")
    if not text:
        return None
    try:
        data = json.loads(text.strip())
    except Exception:
        return None
    city = str(data.get("city") or "").strip()
    code = str(data.get("cityCode") or "").strip()
    # 境外 IP：city 为空、cityCode 为 "0"、err=noprovince —— 当"这个源回答不了"处理
    if not city or code in ("", "0"):
        return None
    return {"adcode": code, "city": city, "province": str(data.get("pro") or "").strip()}


def _source_ipapi(ip: str, timeout: float) -> dict | None:
    """ip-api.com，备胎。免费版只有 HTTP，且 city 精度一般。"""
    text = _get("http://ip-api.com/json/" + urlquote(ip) + "?lang=zh-CN&fields="
                "status,message,country,regionName,city", timeout)
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("status") != "success":
        return None
    city = str(data.get("city") or "").strip()
    if not city:
        return None
    return {"adcode": "", "city": city, "province": str(data.get("regionName") or "").strip()}


# 顺序即优先级
_SOURCES = (("pconline", _source_pconline), ("ip-api", _source_ipapi))


def _resolve(ip: str, timeout: float) -> dict | None:
    for name, source in _SOURCES:
        try:
            info = source(ip, timeout)
        except Exception as exc:                       # 单个源炸了不能影响其它源
            debug("[Geo] " + name + " 异常：" + str(exc))
            continue
        if info:
            info["source"] = name
            return info
    debug("[Geo] " + ip + " 所有源都没结果")
    return None


def locate(ip: str, timeout: float = DEFAULT_TIMEOUT,
           cache_hours: int = DEFAULT_CACHE_HOURS) -> dict | None:
    """把访问者 IP 换成 ``{"adcode", "city", "province", "source"}``；查不到返回 None。"""
    if not ip or _is_unlocatable(ip):
        return None

    now = time.time()
    with _lock:
        hit = _CACHE.get(ip)
    if hit and now < hit[0]:
        return hit[1]

    info = _resolve(ip, timeout)
    ttl = (cache_hours * 3600.0) if info else NEGATIVE_TTL
    with _lock:
        if len(_CACHE) >= MAX_ENTRIES:
            _CACHE.clear()
        _CACHE[ip] = (now + ttl, info)

    if info:
        out("[Geo] " + ip + " → " + describe(info))
    return info


def describe(info: dict) -> str:
    """给日志用的一句话：``泉州市 350500（pconline）``。"""
    if not info:
        return "未定位"
    parts = [str(info.get("city") or "?")]
    if info.get("adcode"):
        parts.append(str(info["adcode"]))
    return " ".join(parts) + "（" + str(info.get("source") or "?") + "）"


def clear_cache() -> int:
    """清空缓存，返回清掉的条数（后台/调试用）。"""
    with _lock:
        count = len(_CACHE)
        _CACHE.clear()
    return count


def cache_size() -> int:
    with _lock:
        return len(_CACHE)
