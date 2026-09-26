# -*- coding: utf-8 -*-
"""访客的个性设置：按 IP 和玩家名记，30 天没来就清掉。

存 ``var/profiles.json``，结构是：

    {"<id>": {"name": "Steve", "ips": ["1.2.3.4"], "lang": "zh-hant",
              "created": 1690000000, "updated": 1690000000}}

**为什么按 IP 找、玩家名只是"名字"**：服务端看不到玩家名——主页里的 ``{user}``
是 PCL 在客户端替换的，请求里根本没有。所以主页渲染时只能按 IP 匹配；玩家名是
用户在设置页自己填的，用来给这份记录命名，以及换了 IP 之后靠它找回来
（同名记录会把新 IP 追加进去，这就是"IP 变了就记新 IP"）。

**30 天 TTL**：照抄 ``store.py`` 里 ``Stats`` 的做法——写在同一个操作里顺手清，
每天最多清一次，不另起线程。清理整段包在 try 里：这些调用都在主页关键路径上，
清不掉最多是文件大一点，绝不能让主页挂掉。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .config import VAR_DIR
from .i18n import DEFAULT_LANG, normalize_lang
from .log import warn

PROFILES_FILE = VAR_DIR / "profiles.json"

KEEP_DAYS = 30                       # 多久没来就清掉
_CLEANUP_INTERVAL = 86400.0          # 清理本身每天最多跑一次
_NAME_MAX = 32
_MAX_IPS = 16                        # 一份记录最多记几个 IP，防着扫


def _now() -> float:
    return time.time()


class Profiles:
    """全部访客的个性设置。进程内一份，读写都过锁。"""

    def __init__(self, path: Path = PROFILES_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._records: dict = {}
        self._last_cleanup = 0.0
        self._load()

    # ---- 读写盘 ----

    def _load(self) -> None:
        try:
            self._records = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._records = {}
        except Exception as exc:
            warn("[Profiles] " + str(self.path) + " 读不了，当空的用：" + repr(exc))
            self._records = {}

    def _flush(self) -> None:
        """先写临时文件再 replace，避免写一半被读到。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._records, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:
            warn("[Profiles] 写盘失败：" + repr(exc))

    def _cleanup(self, force: bool = False) -> int:
        """把超过 KEEP_DAYS 没更新的记录删掉。返回删了几条。"""
        now = _now()
        if not force and now - self._last_cleanup < _CLEANUP_INTERVAL:
            return 0
        self._last_cleanup = now
        cutoff = now - KEEP_DAYS * 86400
        stale = [key for key, rec in self._records.items()
                 if float(rec.get("updated") or 0) < cutoff]
        for key in stale:
            self._records.pop(key, None)
        return len(stale)

    # ---- 查询 ----

    def by_ip(self, ip: str):
        """按 IP 找记录；找到就把 updated 往前推（"来过就续期"）。"""
        if not ip:
            return None
        with self._lock:
            for record in self._records.values():
                if ip in (record.get("ips") or []):
                    record["updated"] = _now()
                    return record
        return None

    def by_name(self, name: str):
        key = self._norm_name(name)
        if not key:
            return None
        with self._lock:
            for record in self._records.values():
                if self._norm_name(record.get("name") or "") == key:
                    return record
        return None

    @staticmethod
    def _norm_name(name: str) -> str:
        return (name or "").strip().casefold()

    def lang_for(self, ip: str) -> str:
        """主页渲染时用：这个 IP 选的语言，没有就用默认。"""
        record = self.by_ip(ip)
        return normalize_lang((record or {}).get("lang") or DEFAULT_LANG)

    def palette_for(self, ip: str) -> dict:
        """主页渲染时用：这个 IP 选的配色（四个颜色号）。"""
        from .palette import brushes
        return brushes(self.by_ip(ip))

    # ---- 写入 ----

    def bind(self, ip: str, name: str = "", lang: str = "",
             panel=None, text=None):
        """记下这个 IP 的设置。返回 (记录, 是否是新建的)。

        找记录的次序：这个 IP 已有的 → 同名记录（把新 IP 追加进去）→ 都没有就新建。
        ``panel`` / ``text`` 是配色档位，传 None 表示这次不动它。
        """
        from .palette import PANEL_CHOICES, TEXT_CHOICES, normalize
        ip = (ip or "").strip()
        lang = normalize_lang(lang) if lang else ""
        name = (name or "").strip()[:_NAME_MAX]
        with self._lock:
            record, fresh = None, False
            if ip:
                for item in self._records.values():
                    if ip in (item.get("ips") or []):
                        record = item
                        break
            if record is None and name:
                for item in self._records.values():
                    if self._norm_name(item.get("name") or "") == self._norm_name(name):
                        record = item
                        break
            if record is None:
                record = {"name": "", "ips": [], "lang": DEFAULT_LANG,
                          "created": _now(), "updated": _now()}
                self._records[self._new_id()] = record
                fresh = True

            if ip and ip not in (record.get("ips") or []):
                record.setdefault("ips", []).append(ip)
                del record["ips"][:-_MAX_IPS]          # 只留最近几个
            if name:
                record["name"] = name
            if lang:
                record["lang"] = lang
            if panel is not None:
                record["panel"] = normalize(panel, PANEL_CHOICES, 7)
            if text is not None:
                record["text"] = normalize(text, TEXT_CHOICES, 1)
            record["updated"] = _now()

            removed = self._cleanup()
            self._flush()
            if removed:
                warn("[Profiles] 清掉了 " + str(removed) + " 份过期记录")
            return dict(record), fresh

    def _new_id(self) -> str:
        import secrets
        while True:
            key = secrets.token_hex(6)
            if key not in self._records:
                return key


#: 进程内唯一一份。仿 ai.py 里 ``QUOTA = Quota()`` 的写法。
PROFILES = Profiles()


def lang_for(ip: str) -> str:
    """这个 IP 选的语言（没有记录就是默认）。主页每请求都会调一次。"""
    return PROFILES.lang_for(ip)


# ============ 个性码 ============
#
# 形如 PCLP1-eyJsYW5nIjoiemgtaGFudCJ9-K7F2：前缀 + base64url(紧凑 JSON) + 4 位校验。
#
# **自带全部设置**，不依赖服务端还留着记录：换台电脑、记录被清掉、甚至换了个
# 部署都能粘回来。校验位是为了拦住"复制少了一截"这种——base64 解出来往往是
# 合法的乱码，不校验就会静默套上一堆错设置。

CODE_PREFIX = "PCLP1"


def _checksum(payload: str) -> str:
    import hashlib
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
    return digest[:4].upper()


def encode_code(record: dict) -> str:
    """把一份设置编成个性码。"""
    import base64
    from .palette import DEFAULTS as PALETTE_DEFAULTS, brushes
    data = {"lang": normalize_lang(record.get("lang") or DEFAULT_LANG)}
    name = (record.get("name") or "").strip()
    if name:
        data["name"] = name
    # 配色只在不是默认档时才写进码里：默认档的人拿到的码短一点、也好看一点
    palette = brushes(record)
    for role in ("panel", "text"):
        if palette[role] != PALETTE_DEFAULTS[role]:
            data[role] = palette[role]
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    body = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return CODE_PREFIX + "-" + body + "-" + _checksum(body)


def decode_code(code: str) -> dict | None:
    """解个性码。格式不对、校验位对不上都返回 None（调用方给"码不对"的提示）。"""
    import base64
    from .palette import PANEL_CHOICES, TEXT_CHOICES, normalize
    text = (code or "").strip()
    parts = text.split("-")
    if len(parts) != 3 or parts[0] != CODE_PREFIX:
        return None
    body = parts[1]
    if parts[2].upper() != _checksum(body):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    out = {"lang": normalize_lang(data.get("lang"))}
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        out["name"] = name.strip()[:_NAME_MAX]
    if "panel" in data:
        out["panel"] = normalize(data["panel"], PANEL_CHOICES, 7)
    if "text" in data:
        out["text"] = normalize(data["text"], TEXT_CHOICES, 1)
    return out
