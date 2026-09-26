# -*- coding: utf-8 -*-
"""底标里显示的版本号：GitHub 上 main 分支最新提交的短哈希。

**主页请求绝不等网络**。做法是"读到就用，过期丢给后台线程去刷"：

* 有缓存 → 立刻返回缓存值；发现过期就顺手叫醒一个后台线程去刷新，本次仍用旧值。
* 没缓存 → 返回"未知"，同时让后台线程去取；下一次请求就有了。
* 取不到（没网、API 限流、仓库改名）→ 保留上一次的值；一次都没有就一直是"未知"。

不用 token：仓库是公开的，匿名调公开 API 就够了，也不用把 PAT 放进服务端。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .config import VAR_DIR
from .log import out, warn
from .net import fetch_json

VERSION_FILE = VAR_DIR / "version.json"

REPO = "TopGrilledFish/PCLEndPage"
API = "https://api.github.com/repos/" + REPO + "/commits/main"
TTL = 3600.0                       # 缓存多久算新鲜
TIMEOUT = 6.0
UNKNOWN = ""

_lock = threading.Lock()
_refreshing = False


def _read_cache() -> dict:
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        warn("[Version] 缓存读不了：" + repr(exc))
        return {}


def _write_cache(data: dict) -> None:
    try:
        VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = VERSION_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(VERSION_FILE)
    except Exception as exc:
        warn("[Version] 缓存写不了：" + repr(exc))


def _fetch() -> str:
    """问一次 GitHub。返回短哈希，失败返回空串。"""
    data = fetch_json(API, timeout=TIMEOUT, retries=0,
                      headers={"Accept": "application/vnd.github+json"})
    sha = str((data or {}).get("sha") or "").strip()
    return sha[:7] if len(sha) >= 7 else ""


def _refresh() -> None:
    global _refreshing
    try:
        short = _fetch()
        if short:
            _write_cache({"version": short, "ts": time.time()})
            out("[Version] GitHub 最新提交：" + short)
        else:
            warn("[Version] 拿不到 GitHub 最新提交，继续用上一次的值")
    finally:
        with _lock:
            _refreshing = False


def _kick_refresh() -> None:
    """叫醒后台刷新。同一时间只跑一个，失败也不影响调用方。"""
    global _refreshing
    with _lock:
        if _refreshing:
            return
        _refreshing = True
    try:
        threading.Thread(target=_refresh, name="version-refresh", daemon=True).start()
    except Exception as exc:                       # 线程都起不来就算了
        with _lock:
            _refreshing = False
        warn("[Version] 起不了刷新线程：" + repr(exc))


def current() -> str:
    """当前版本号（短哈希）。可能是旧值，也可能是空串（显示成"未知"）。"""
    cached = _read_cache()
    short = str(cached.get("version") or "").strip()
    age = time.time() - float(cached.get("ts") or 0)
    if not short or age > TTL:
        _kick_refresh()
    return short
