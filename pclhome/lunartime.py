# -*- coding: utf-8 -*-
"""横幅日期下面的那一行农历，数据源 uapis.cn ``/api/v1/misc/lunartime``。

显示形态：``农历八月十五``，当天有节气时补一句 `` · 秋分``。

接口挂了不会让这行空着——本地 ``lunar.py`` 已经有一套对齐过上游的农历换算，
自己也能算出月日，只是写不出节气名（那需要另一张表）。所以：

    1. 接口 → 中文月日 + 节气
    2. 接口不可用 → 本地换算的中文月日（无节气）
    3. 连本地都算不出（日期越界）→ 整行不显示

按北京时间日期缓存到 ``var/lunar.json``：农历一天只变一次，没必要每次请求都外呼。
"""
from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from .config import VAR_DIR, Config
from .log import debug, out, warn
from .lunar import solar_to_lunar
from .net import fetch_json
from .xaml import escape_attr

LUNAR_CACHE = VAR_DIR / "lunar.json"
_lock = threading.Lock()

# 农历月名：正月、二月……十月、冬月、腊月
_MONTHS = ("正月", "二月", "三月", "四月", "五月", "六月",
           "七月", "八月", "九月", "十月", "冬月", "腊月")
# 农历日名：初一……初十、十一……十九、二十、廿一……廿九、三十
_DAY_TENS = ("初", "十", "廿", "三")
_DAY_UNITS = ("十", "一", "二", "三", "四", "五", "六", "七", "八", "九")


def format_lunar_cn(month: int, day: int, is_leap: bool = False) -> str:
    """本地换算结果 → 中文农历，如 (8, 15) → 八月十五。"""
    if not 1 <= month <= 12 or not 1 <= day <= 30:
        return ""
    name = ("闰" if is_leap else "") + _MONTHS[month - 1]
    if day == 10:
        return name + "初十"
    if day == 20:
        return name + "二十"
    if day == 30:
        return name + "三十"
    return name + _DAY_TENS[day // 10] + _DAY_UNITS[day % 10]


def _read_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(path: Path, payload: dict) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        warn("[Lunar] 缓存写入失败：" + str(exc))


def _local_fallback(today: date) -> str:
    """不联网时的兜底：用本地农历换算（没有节气，其余与接口形态一致）。"""
    try:
        _, month, day, is_leap = solar_to_lunar(today)
    except Exception:
        return ""
    text = format_lunar_cn(month, day, is_leap)
    return "农历" + text if text else ""


def get_lunar_text(config: Config, today: date, cache_path: Path | None = None) -> str:
    """返回该显示在日期下面的那行农历文本（接口不可用时退回本地换算）。"""
    cache_path = cache_path or LUNAR_CACHE

    if config.enable_lunar:
        with _lock:
            cached = _read_cache(cache_path)
            if cached.get("date") == today.isoformat() and cached.get("text"):
                debug("[Lunar] 命中缓存：" + str(cached["text"]))
                return str(cached["text"])

            data = fetch_json(config.lunar_api, timeout=config.http_timeout,
                              retries=config.max_retries)
            text = _format_from_api(data, today)
            if text:
                _write_cache(cache_path, {"date": today.isoformat(), "text": text})
                out("[Lunar] 今日农历：" + text)
                return text
            if cached.get("text"):
                warn("[Lunar] 接口不可用，沿用缓存：" + str(cached["text"]))
                return str(cached["text"])

    text = _local_fallback(today)
    if text:
        debug("[Lunar] 本地换算：" + text)
    return text


def _format_from_api(data: dict | None, today: date) -> str:
    if not isinstance(data, dict):
        return ""
    month_cn = str(data.get("lunar_month_cn") or "")
    day_cn = str(data.get("lunar_day_cn") or "")
    if not month_cn or not day_cn:
        return ""
    text = "农历" + ("闰" if data.get("is_leap_month") else "") + month_cn + day_cn
    term = str(data.get("solar_term") or "").strip()
    if term:
        text += " · " + term
    return text


def build_lunar_xaml(text: str) -> str:
    """日期下面那行；没有内容时返回空串（整行消失）。"""
    if not text:
        return ""
    return ('<TextBlock Text="' + escape_attr(text) + '" FontSize="12" Foreground="#D9FFFFFF" '
            'HorizontalAlignment="Center" Margin="0,6,0,0" />')
