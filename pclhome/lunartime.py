# -*- coding: utf-8 -*-
"""横幅日期下面的那一行农历，数据源 uapis.cn ``/api/v1/misc/lunartime``。

显示形态：``农历八月十五``，当天有节气时补一句 `` · 秋分``。

**月日与节气分工**：月日一律用本地 ``lunar.py`` 的换算（那套已经拿上游 JS 的
输出当基准向量对齐过），接口只用来取节气名——节气要另一张表，本地算不出来。
这样三种语言的农历是从同一组数字渲染的，不会出现"简中说八月十五、英文说别的"。

接口挂了不会让这行空着，最多是少一句节气：
本地换算算不出（日期越界）才整行消失。

按北京时间日期缓存到 ``var/lunar.json``：农历一天只变一次，没必要每次请求都外呼。
**缓存里存的是数字不是渲染好的文字**，所以一份缓存三种语言都能用。
"""
from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from .config import VAR_DIR, Config
from .i18n import DEFAULT_LANG, has, t
from .log import debug, out, warn
from .lunar import solar_to_lunar
from .net import fetch_json
from .xaml import escape_attr

LUNAR_CACHE = VAR_DIR / "lunar.json"
_lock = threading.Lock()

# 农历日名的汉字写法：初一……初十、十一……十九、二十、廿一……廿九、三十。
# 这是中文的数字写法（不是需要翻译的文案），所以留在这儿；英文那边用
# ``lunar.day_n``（"day 15"）走另一条路。
_DAY_TENS = ("初", "十", "廿", "三")
_DAY_UNITS = ("十", "一", "二", "三", "四", "五", "六", "七", "八", "九")


def _is_chinese(lang: str) -> bool:
    return str(lang or "").lower().startswith("zh")


def format_lunar(month: int, day: int, is_leap: bool = False,
                 lang: str = DEFAULT_LANG) -> str:
    """农历月日 → 该语言的写法。中文是「八月十五」，英文是「8th month, day 15」。"""
    if not 1 <= month <= 12 or not 1 <= day <= 30:
        return ""
    name = t("lunar.month." + str(month), lang)
    if is_leap:
        name = t("lunar.leap", lang) + name
    if not _is_chinese(lang):
        return t("lunar.date", lang, month=name, day=t("lunar.day_n", lang, n=day))
    if day == 10:
        return name + "初十"
    if day == 20:
        return name + "二十"
    if day == 30:
        return name + "三十"
    return name + _DAY_TENS[day // 10] + _DAY_UNITS[day % 10]


def format_lunar_cn(month: int, day: int, is_leap: bool = False) -> str:
    """中文农历（老接口，日志和体检脚本还在用）。"""
    return format_lunar(month, day, is_leap, "zh-hans")


def format_term(term: str, lang: str) -> str:
    """节气名。表里没有的原样显示（接口加了新词也不会变空）。"""
    key = "lunar.term." + str(term or "")
    return t(key, lang) if has(key, lang) else str(term or "")


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


def _parts(today: date, cached: dict) -> dict:
    """今天的农历数字 + 节气。数字本地算，节气从缓存里拿（拿不到就没有）。"""
    try:
        _, month, day, is_leap = solar_to_lunar(today)
    except Exception:
        return {}
    return {"month": month, "day": day, "leap": bool(is_leap),
            "term": str(cached.get("term") or "")}


def _fetch_term(config: Config, today: date, cache_path: Path, cached: dict) -> dict:
    """问一次接口补上节气名，结果连同数字一起写回缓存。"""
    if not config.enable_lunar:
        return cached
    if cached.get("date") == today.isoformat() and cached.get("term"):
        return cached

    data = fetch_json(config.lunar_api, timeout=config.http_timeout,
                      retries=config.max_retries)
    term = str((data or {}).get("solar_term") or "").strip() if isinstance(data, dict) else ""
    if not term:
        # 接口没给出节气：今天的农历照样显示（没有节气那一截），缓存不动
        return cached
    fresh = dict(cached)
    fresh.update({"date": today.isoformat(), "term": term})
    _write_cache(cache_path, fresh)
    return fresh


def get_lunar_text(config: Config, today: date, lang: str = DEFAULT_LANG,
                   cache_path: Path | None = None) -> str:
    """返回该显示在日期下面的那行农历文本（节气取不到就少那一截）。"""
    cache_path = cache_path or LUNAR_CACHE
    with _lock:
        cached = _read_cache(cache_path)
        cached = _fetch_term(config, today, cache_path, cached)

    parts = _parts(today, cached)
    if not parts:
        warn("[Lunar] 这个日期本地换算不出来：" + today.isoformat())
        return ""

    body = format_lunar(parts["month"], parts["day"], parts["leap"], lang)
    if not body:
        return ""
    text = t("lunar.prefix", lang) + body
    if parts["term"]:
        text += " · " + format_term(parts["term"], lang)
    debug("[Lunar] " + lang + "：" + text)
    return text


def build_lunar_xaml(text: str) -> str:
    """日期下面那行；没有内容时返回空串（整行消失）。"""
    if not text:
        return ""
    return ('<TextBlock Text="' + escape_attr(text) + '" FontSize="12" Foreground="#D9FFFFFF" '
            'HorizontalAlignment="Center" Margin="0,6,0,0" />')
