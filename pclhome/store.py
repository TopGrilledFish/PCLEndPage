# -*- coding: utf-8 -*-
"""后台配置存储 + 访问统计（替代上游的 Cloudflare KV + D1）。

键名与上游保持一一对应，方便照搬上游后台的语义：

    block:list          {"1.2.3.4": "刷屏"}          封禁列表
    maint_mode_live     "1" / "0"                    维护模式开关
    maint_eta           "10 分钟"                     维护预计完成时间
    maint_reason        "升级数据库"                  维护原因
    maint_whitelist     {"1.2.3.4": 1}               维护期白名单
    quote_custom        每行一条                      自定义每日一言
    custom_festivals    [{"month":1,"day":1,...}]     自定义公历节日
    custom_countdown    {"name": "...", "date": "..."} 自定义倒计时
    banners             每行一条                      多公告轮播（≥2 条生效）
    homepage_banner     {"enabled": true, "text": ""} 单条公告
    weather_version     "0"                          天气缓存版本号

存储用一个 JSON 文件 + 读写锁。单机部署够用；多实例部署请换成 Redis/数据库，
接口只有 get/set/delete 三个方法，替换成本很低。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .log import out, warn
from .config import VAR_DIR

STORE_FILE = VAR_DIR / "store.json"
STATS_DB = VAR_DIR / "stats.db"

# 访问统计保留天数（上游 D1 同样是 7 天）
STATS_KEEP_DAYS = 7


class Store:
    """简单的 JSON 文件 KV。"""

    def __init__(self, path: Path | None = None):
        self.path = path or STORE_FILE
        self._lock = threading.RLock()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            out("[Store] " + str(self.path) + " 解析失败，按空配置启动：" + str(exc))
            self._data = {}

    def _flush(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, key: str, fallback=None):
        with self._lock:
            value = self._data.get(key, fallback)
        return fallback if value is None else value

    def get_json(self, key: str, fallback=None):
        raw = self.get(key, None)
        if raw is None:
            return fallback
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return fallback

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            self._flush()

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)
            self._flush()

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)

    # ---- 常用配置的语义化读写 ----

    def block_list(self) -> dict:
        return self.get_json("block:list", {}) or {}

    def is_blocked(self, ip: str) -> bool:
        return bool(ip) and ip in self.block_list()

    def maintenance(self) -> dict:
        return {
            "on": str(self.get("maint_mode_live", "0")) not in ("0", "", "None"),
            "eta": str(self.get("maint_eta", "") or ""),
            "reason": str(self.get("maint_reason", "") or ""),
            "whitelist": self.get_json("maint_whitelist", {}) or {},
        }



class Stats:
    """访问统计，落在本地 SQLite（对应上游的 D1 visits 表）。"""

    def __init__(self, path: Path | None = None):
        self.path = path or STATS_DB
        self._lock = threading.Lock()
        self._ready = False
        self._last_cleanup = 0.0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure(self, conn: sqlite3.Connection) -> None:
        if self._ready:
            return
        conn.execute("CREATE TABLE IF NOT EXISTS visits ("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "ip TEXT NOT NULL, country TEXT, ts INTEGER NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts)")
        self._ready = True

    def record(self, ip: str, country: str = "XX") -> None:
        """记一次访问；写失败只打日志，绝不影响主页返回。"""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    self._ensure(conn)
                    conn.execute("INSERT INTO visits (ip, country, ts) VALUES (?, ?, ?)",
                                 (ip, country, int(time.time() * 1000)))
                    conn.commit()

                    now = time.time()
                    if now - self._last_cleanup < 86400:
                        return
                    cutoff = int((now - STATS_KEEP_DAYS * 86400) * 1000)
                    conn.execute("DELETE FROM visits WHERE ts < ?", (cutoff,))
                    conn.commit()
                    self._last_cleanup = now
                finally:
                    conn.close()
        except Exception as exc:
            warn("[Stats] 统计写入失败：" + str(exc))

    def summary(self, days: int = 7) -> dict:
        """总访问量 / 今日访问 / 独立 IP / 国家分布 / 最近记录。"""
        now = time.time()
        cutoff = int((now - days * 86400) * 1000)
        # 北京时间当天 0 点对应的 Unix 时间戳
        beijing = now + 8 * 3600
        today_start = int((beijing - beijing % 86400 - 8 * 3600) * 1000)
        empty = {"total": 0, "today": 0, "unique": 0, "countries": [], "hosts": [], "recent": []}
        try:
            with self._lock:
                conn = self._connect()
                try:
                    self._ensure(conn)
                    cur = conn.cursor()
                    total = cur.execute("SELECT COUNT(*) FROM visits WHERE ts >= ?", (cutoff,)).fetchone()[0]
                    today = cur.execute("SELECT COUNT(*) FROM visits WHERE ts >= ?", (today_start,)).fetchone()[0]
                    unique = cur.execute("SELECT COUNT(DISTINCT ip) FROM visits WHERE ts >= ?", (cutoff,)).fetchone()[0]
                    countries = cur.execute(
                        "SELECT COALESCE(country,'XX') c, COUNT(*) n FROM visits WHERE ts >= ? "
                        "GROUP BY c ORDER BY n DESC LIMIT 12", (cutoff,)).fetchall()
                    hosts = cur.execute(
                        "SELECT ip, COUNT(*) n, MAX(ts) last FROM visits WHERE ts >= ? "
                        "GROUP BY ip ORDER BY n DESC LIMIT 12", (cutoff,)).fetchall()
                    recent = cur.execute(
                        "SELECT ip, COALESCE(country,'XX'), ts FROM visits WHERE ts >= ? "
                        "ORDER BY ts DESC LIMIT 20", (cutoff,)).fetchall()
                finally:
                    conn.close()
        except Exception as exc:
            warn("[Stats] 统计读取失败：" + str(exc))
            return empty

        return {
            "total": total, "today": today, "unique": unique,
            "countries": [{"code": c, "count": n} for c, n in countries],
            "hosts": [{"ip": ip, "count": n, "last": last} for ip, n, last in hosts],
            "recent": [{"ip": ip, "code": c, "ts": ts} for ip, c, ts in recent],
        }
