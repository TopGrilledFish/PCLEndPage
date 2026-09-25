# -*- coding: utf-8 -*-
"""每日一言。

数据源：uapis.cn 的 ``/api/v1/saying/random?mode=daily``
（``mode=daily`` 的含义是"一整天返回同一句"，正好满足"所有人都看到同一句"）。

三层保障，任何一层挂掉都不会让横幅空着：

    1. 后台自定义文案（``quote_custom``，每行一条）——按日期轮换，全站同一句
    2. uapis 接口——按北京时间日期缓存到 ``var/saying.json``，一天只请求一次，
       API 挂了还能继续用当天已缓存的内容（也顺便避开它 4 QPS 的限制）
    3. 内置的 68 条文案——按日期选取，同样全站同一句
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from urllib.parse import quote as urlquote

from .log import out, warn
from .config import VAR_DIR, Config
from .data.text import QUOTES
from .net import fetch_json
from .store import Store

SAYING_CACHE = VAR_DIR / "saying.json"
_lock = threading.Lock()


@dataclass(frozen=True)
class Saying:
    text: str
    author: str = ""
    source_name: str = ""       # 来源标记：custom / api / builtin

    @property
    def display(self) -> str:
        """给页面用的文本：有作者就带上，符合一言的惯例。"""
        if self.author:
            return self.text + " —— " + self.author
        return self.text


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
        warn("[Saying] 缓存写入失败：" + str(exc))


def _pick_by_date(pool: list[str], today: date) -> str:
    """按日期（而非 IP）取，保证同一天所有人拿到同一句。"""
    return pool[today.toordinal() % len(pool)]


def _from_api(config: Config, today: date, cache_path: Path) -> Saying | None:
    cached = _read_cache(cache_path)
    if cached.get("date") == today.isoformat() and cached.get("text"):
        return Saying(cached["text"], cached.get("author", ""), "api")

    url = config.sayings_api + "?mode=daily"
    if config.saying_source:
        url += "&source=" + urlquote(config.saying_source)
    data = fetch_json(url, timeout=config.http_timeout, retries=config.max_retries)
    item = (data or {}).get("item") if isinstance(data, dict) else None
    if isinstance(item, dict):
        text, author = str(item.get("content") or ""), str(item.get("author") or "")
    elif isinstance(data, dict) and data.get("text"):
        text, author = str(data["text"]), ""          # 兼容 /api/v1/saying 的简单形态
    else:
        text, author = "", ""

    if not text.strip():
        if cached.get("text"):                        # 接口挂了：用旧的也比没有强
            out("[Saying] 接口不可用，沿用缓存内容")
            return Saying(cached["text"], cached.get("author", ""), "api")
        return None

    _write_cache(cache_path, {"date": today.isoformat(), "text": text, "author": author})
    out("[Saying] 今日一言已更新：" + text[:24] + ("…" if len(text) > 24 else ""))
    return Saying(text, author, "api")


def get_saying(config: Config, store: Store, today: date, cache_path: Path | None = None) -> Saying:
    """取今日一言（同一天全站同一句）。"""
    cache_path = cache_path or SAYING_CACHE

    custom = store.get("quote_custom", None)
    if custom:
        pool = [line.strip() for line in str(custom).splitlines() if line.strip()]
        if pool:
            return Saying(_pick_by_date(pool, today), "", "custom")

    if config.enable_saying:
        with _lock:                                   # 并发请求时只让一个线程去拉
            result = _from_api(config, today, cache_path)
        if result:
            return result

    return Saying(_pick_by_date(QUOTES, today), "", "builtin")


def clear_cache(cache_path: Path | None = None) -> None:
    """删掉缓存，下次请求强制回源（后台改完接口地址后可以调一下）。"""
    try:
        (cache_path or SAYING_CACHE).unlink(missing_ok=True)
        time.sleep(0)                                  # 保持接口形态一致
    except Exception as exc:
        warn("[Saying] 清缓存失败：" + str(exc))
