# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote

from .config import USER_AGENT, VAR_DIR, Config
from .i18n import DEFAULT_LANG, t
from . import palette
from . import profiles
from .log import debug, error, out, warn
from .store import STATS_DB
from .xaml import (HOME_EVENT_DATA, HOME_EVENT_TYPE, ICON_AI, ICON_BACK, ICON_HOME,
                   ICON_KEY, ICON_REFRESH, ICON_SAVE, ICON_TRASH, attr, bind_to,
                   divider, escape_attr, grid2, heading, help_button, input_row,
                   nav_row, note, url_attr)

JOBS_FILE = VAR_DIR / "ai_jobs.json"

AI_LOG_FILE = VAR_DIR / "ai_log.json"
AI_LOG_MAX = 300                 # 只留最近这么多条，文件别无限长
AI_LOG_INPUT_CHARS = 4000        # 记录里输入原文留多少字
AI_LOG_TEXT_CHARS = 6000         # 结果/错误留多少字

# 额度按这个时区跨天重置
BEIJING = timezone(timedelta(hours=8))

# 结果保留多久（超过就当过期，卡片不再显示）
JOB_TTL = 7 * 86400


def system_prompt(lang: str = DEFAULT_LANG) -> str:

    return t("ai.system_prompt", lang)



_URL_RE = re.compile(r"^https?://\S+$", re.I)
_MCLOGS_RE = re.compile(r"^https?://(?:www\.)?mclo\.gs/([A-Za-z0-9]+)", re.I)

MAX_INLINE_CHARS = 1200


def split_input(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "empty", ""
    if _URL_RE.match(text):
        return "url", text
    return "text", text[:MAX_INLINE_CHARS]


def normalize_log_url(url: str) -> str:
    match = _MCLOGS_RE.search(url or "")
    if match:
        return "https://api.mclo.gs/1/raw/" + match.group(1)
    return url



class Quota:
    """按 IP + 时间窗口记 **token 额度** 和调用次数。

    以前这里记的是「用了几次」，现在记的是「用掉多少 token」——
    一条塞满 40KB 的日志和一条十行报错，本来就不该算同一个价。
    ``used`` 存的是**加权后**的 token（高峰期会被乘上 ``ai_peak_multiplier``），
    所以额度判断不需要知道当时是不是高峰。
    """

    def __init__(self, path: Path | None = None):
        self.path = path or STATS_DB
        self._lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        if not self._ready:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(ai_usage)")}
            if columns and "bucket" not in columns:
                print("[AI] ai_usage 是旧的按天结构，重建为按额度窗口计数")
                conn.execute("DROP TABLE ai_usage")
                columns = set()
            conn.execute("CREATE TABLE IF NOT EXISTS ai_usage ("
                         "ip TEXT NOT NULL, bucket TEXT NOT NULL, "
                         "used INTEGER NOT NULL DEFAULT 0, "
                         "calls INTEGER NOT NULL DEFAULT 0, "
                         "ts INTEGER NOT NULL, "
                         "PRIMARY KEY (ip, bucket))")
            if columns and "calls" not in columns:
                conn.execute("ALTER TABLE ai_usage ADD COLUMN calls INTEGER NOT NULL DEFAULT 0")
            conn.commit()
            self._ready = True
        return conn

    def used(self, ip: str, bucket: str) -> int:
        """已经用掉的加权 token。"""
        return self._read(ip, bucket)[0]

    def calls(self, ip: str, bucket: str) -> int:
        """这个窗口里已经调用过几次。"""
        return self._read(ip, bucket)[1]

    def _read(self, ip: str, bucket: str) -> tuple[int, int]:
        try:
            with self._lock:
                conn = self._connect()
                try:
                    row = conn.execute(
                        "SELECT used, calls FROM ai_usage WHERE ip=? AND bucket=?",
                        (ip, bucket)).fetchone()
                    return (int(row[0]), int(row[1])) if row else (0, 0)
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 读额度失败：" + str(exc))
            return 0, 0

    def reserve(self, ip: str, bucket: str, amount: int,
                limit: int, max_calls: int = 0) -> tuple[bool, str, int]:
        """先预扣一笔再调用（token 只有调完才知道，不预留会被并发穿透）。

        返回 ``(是否放行, 拒绝原因, 实际预扣的 token)``；原因取
        ``token`` / ``calls`` / ``error``，用来给用户挑不同的提示。
        """
        want = max(1, int(amount or 1))
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT used, calls FROM ai_usage WHERE ip=? AND bucket=?",
                        (ip, bucket)).fetchone()
                    used, calls = (int(row[0]), int(row[1])) if row else (0, 0)
                    if max_calls > 0 and calls >= max_calls:
                        conn.rollback()
                        return False, "calls", 0
                    if limit > 0 and used >= limit:
                        conn.rollback()
                        return False, "token", 0
                    take = want
                    if limit > 0:
                        # 剩余不多时只预扣剩下的，别把「最后一次」也挡在门外
                        take = max(1, min(want, limit - used))
                    now = int(time.time())
                    conn.execute(
                        "INSERT INTO ai_usage (ip, bucket, used, calls, ts) VALUES (?, ?, ?, 1, ?) "
                        "ON CONFLICT(ip, bucket) DO UPDATE SET "
                        "used = used + ?, calls = calls + 1, ts = ?",
                        (ip, bucket, take, now, take, now))
                    conn.commit()
                    return True, "", take
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 扣额度失败：" + str(exc))
            return False, "error", 0

    def settle(self, ip: str, bucket: str, reserved: int, actual: int) -> None:
        """调用完按实际用量找零：``used = used - 预留 + 实际``。"""
        delta = int(actual or 0) - int(reserved or 0)
        if delta == 0:
            return
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(
                        "UPDATE ai_usage SET used = MAX(0, used + ?), ts = ? "
                        "WHERE ip=? AND bucket=?", (delta, int(time.time()), ip, bucket))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 结算额度失败：" + str(exc))

    def rows_for(self, bucket: str) -> list[dict]:
        try:
            with self._lock:
                conn = self._connect()
                try:
                    rows = conn.execute(
                        "SELECT ip, used, calls, ts FROM ai_usage WHERE bucket=? "
                        "ORDER BY used DESC, ts DESC", (bucket,)).fetchall()
                    return [{"ip": r[0], "used": int(r[1]), "calls": int(r[2]),
                             "ts": int(r[3])} for r in rows]
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 读用量明细失败：" + str(exc))
            return []

    def reset(self, bucket: str | None = None, ip: str | None = None) -> int:
        clauses, params = [], []
        if bucket:
            clauses.append("bucket=?")
            params.append(bucket)
        if ip:
            clauses.append("ip=?")
            params.append(ip)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute("DELETE FROM ai_usage" + where, tuple(params))
                    conn.commit()
                    return cur.rowcount
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 重置用量失败：" + str(exc))
            return 0

    def refund(self, ip: str, bucket: str, amount: int = 0) -> None:
        """整笔退回：token 归零那一部分 + 次数减一。"""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(
                        "UPDATE ai_usage SET used = MAX(0, used - ?), "
                        "calls = MAX(0, calls - 1), ts = ? WHERE ip=? AND bucket=?",
                        (max(0, int(amount or 0)), int(time.time()), ip, bucket))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 退还额度失败：" + str(exc))


QUOTA = Quota()


def beijing_bucket(window: str, ts: float | None = None) -> str:
    moment = datetime.fromtimestamp(ts if ts is not None else time.time(), BEIJING)
    return moment.strftime("%Y-%m-%dT%H") if window == "hour" else moment.strftime("%Y-%m-%d")


def beijing_day(ts: float | None = None) -> str:
    return beijing_bucket("day", ts)


BEIJING_OFFSET = 8 * 3600


def _window_slot(config: Config, moment: float) -> tuple[int, int, int]:
    """把时刻切成「北京时间的第几天 + 当天第几格」，返回 ``(天, 第几格, 格秒数)``。

    从当天 0 点起算，窗口边界就落在 00:00 / 05:00 / 10:00 …… 这种整点上——
    直接拿 epoch 整除 5 小时的话，边界会落在 02:00 这种鬼时间，说明不了也记不住。
    """
    seconds = config.ai_window_seconds()
    local = moment + BEIJING_OFFSET          # 平移到北京时间再算，别受本机时区影响
    day = int(local // 86400)
    index = int((local - day * 86400) // seconds)
    return day, index, seconds


def token_bucket(config: Config, ts: float | None = None) -> str:
    """token 额度窗口的桶键：「第几天-当天第几格」。

    窗口长度可配（默认 5 小时），所以键由配置算出来——改配置不用动表结构。
    """
    moment = ts if ts is not None else time.time()
    day, index, _ = _window_slot(config, moment)
    return str(day) + "-" + str(index)


def bucket_reset_at(config: Config, ts: float | None = None) -> float:
    """当前窗口什么时候结束（时间戳），给「还剩多久恢复」用。"""
    moment = ts if ts is not None else time.time()
    day, index, seconds = _window_slot(config, moment)
    end = min(day * 86400 + (index + 1) * seconds, (day + 1) * 86400)
    return end - BEIJING_OFFSET


def is_peak(ts: float | None = None) -> bool:
    """DeepSeek 的高峰时段（北京时间 8:30–次日 0:30）。

    官方按「空闲 / 高峰」两档计价：北京时间 00:30–08:30 是空闲（半价），
    其余都是高峰。高峰期我们按 ``ai_peak_multiplier`` 倍记额度，
    这样「高峰期烧得快」在额度上也是诚实的。
    """
    moment = datetime.fromtimestamp(ts if ts is not None else time.time(), BEIJING)
    minutes = moment.hour * 60 + moment.minute
    return not (30 <= minutes < 510)


def peak_factor(config: Config, ts: float | None = None) -> float:
    return config.ai_peak_factor() if is_peak(ts) else 1.0


def usage_tokens(usage: dict | None, config: Config, ts: float | None = None) -> int:
    """这次调用折算成多少额度（输入+输出，高峰再乘倍数）。"""
    total = int((usage or {}).get("in") or 0) + int((usage or {}).get("out") or 0)
    return int(round(total * peak_factor(config, ts)))


def format_tokens(count: int) -> str:
    """60000 -> 60k，1234 -> 1.2k，让页面上的数字别太长。"""
    number = int(count or 0)
    if number >= 10000:
        return str(int(round(number / 1000.0))) + "k"
    if number >= 1000:
        return str(round(number / 1000.0, 1)) + "k"
    return str(number)



_own_keys: dict[str, dict] = {}
_own_lock = threading.RLock()


def mask_key(key: str) -> str:
    key = key or ""
    if len(key) <= 10:
        return "***"
    return key[:6] + "***" + key[-4:]


def set_own_key(ip: str, raw: str) -> bool:
    key = (raw or "").strip()
    if not key:
        return False
    with _own_lock:
        conf = _own_keys.get(ip) or {}
        conf["key"] = key
        _own_keys[ip] = conf
    out("[AI] " + ip + " 设置了自带密钥 " + mask_key(key))
    return True


def set_own_base(ip: str, raw: str, protocol: str) -> bool:
    base = (raw or "").split("|")[0].strip()
    with _own_lock:
        conf = _own_keys.get(ip) or {}
        if base:
            conf["base"] = base.rstrip("/")
        else:
            conf.pop("base", None)
        conf["protocol"] = protocol
        _own_keys[ip] = conf
    out("[AI] " + ip + " 自带密钥改为 " + PROTOCOLS[protocol][0]
        + "，地址 " + describe_own_key(ip))
    return True


def set_own_model(ip: str, raw: str) -> bool:
    model = (raw or "").strip()
    with _own_lock:
        conf = _own_keys.get(ip) or {}
        if model:
            conf["model"] = model
        else:
            conf.pop("model", None)
        _own_keys[ip] = conf
    out("[AI] " + ip + " 自带密钥模型改为 " + describe_own_key(ip))
    return True


def describe_own_key(ip: str, lang: str = DEFAULT_LANG) -> str:
    conf = get_own_key(ip) or {}
    if not conf:
        return t("ai.own_unset", lang)
    protocol = str(conf.get("protocol") or "openai")
    base = conf.get("base") or (config_default_base(protocol))
    model = conf.get("model") or PROTOCOLS.get(protocol, ("", ""))[1]
    return protocol_name(protocol, lang) + " · " + base + " · " + model


def protocol_name(protocol: str, lang: str = DEFAULT_LANG) -> str:
    key = "ai.protocol." + str(protocol or "")
    from .i18n import has
    return t(key, lang) if has(key, lang) else PROTOCOLS.get(protocol, ("", ""))[0]


def config_default_base(protocol: str) -> str:
    return ("https://api.anthropic.com" if protocol == "anthropic"
            else "https://api.openai.com/v1")


def get_own_key(ip: str) -> dict | None:
    with _own_lock:
        return _own_keys.get(ip)


def clear_own_key(ip: str) -> bool:
    with _own_lock:
        return _own_keys.pop(ip, None) is not None


def reset_keys(ip: str | None = None) -> int:
    with _own_lock:
        if ip:
            return 1 if _own_keys.pop(ip, None) is not None else 0
        count = len(_own_keys)
        _own_keys.clear()
        return count


def reset_jobs(ip: str | None = None) -> int:
    with _jobs_lock:
        if ip:
            removed = 1 if _jobs.pop(ip, None) is not None else 0
        else:
            removed = len(_jobs)
            _jobs.clear()
        _save_jobs()
    return removed



_jobs: dict[str, dict] = {}
_jobs_lock = threading.RLock()


def _load_jobs() -> None:
    try:
        if not JOBS_FILE.exists():
            return
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        for ip, job in data.items():
            if not isinstance(job, dict):
                continue
            # 上次多半是进程被杀掉了，任务不可能还在跑
            if job.get("state") == "running":
                job["state"] = "error"
                # 用任务自己记的语言，别让英文用户看到一句中文
                job["text"] = t("ai.err.interrupted", job.get("lang") or DEFAULT_LANG)
            _jobs[ip] = job
    except Exception as exc:
        warn("[AI] 任务记录读取失败：" + str(exc))


def _save_jobs() -> None:
    try:
        tmp = JOBS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_jobs, ensure_ascii=False), encoding="utf-8")
        tmp.replace(JOBS_FILE)
    except Exception as exc:
        warn("[AI] 任务记录写入失败：" + str(exc))


def _set_job(ip: str, **fields) -> None:
    with _jobs_lock:
        job = _jobs.get(ip) or {}
        job.update(fields)
        job["ts"] = time.time()
        _jobs[ip] = job
        _save_jobs()


def get_job(ip: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(ip)
    if not job:
        return None
    if time.time() - float(job.get("ts") or 0) > JOB_TTL:
        return None
    return dict(job)


_load_jobs()


# ============ 分析记录（后台「最近分析」） ============

_ai_log_lock = threading.Lock()


def _load_ai_log() -> list:
    if not AI_LOG_FILE.exists():
        return []
    try:
        data = json.loads(AI_LOG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        warn("[AI] 分析记录读取失败：" + str(exc))
        return []


def _save_ai_log(entries: list) -> None:
    try:
        tmp = AI_LOG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        # 里面可能有访客自带的明文密钥，只给 root 读，别让同机的其他账号顺手看到
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(AI_LOG_FILE)
    except Exception as exc:
        warn("[AI] 分析记录写入失败：" + str(exc))


def record_analysis(ip: str, who: str, key: str, model: str, protocol: str,
                    kind: str, input_text: str, ok: bool, output: str,
                    elapsed_ms: int, tokens: dict | None = None,
                    base: str = "", cost: int = 0, peak: bool = False) -> None:
    """记一条分析：谁、用什么密钥、往哪个地址请求、分析的是什么、成没成、花了多久。

    访客自带的密钥**原样存**——站长要能看到访客填的是哪把（列表里打码，点开才
    显示完整值）。站长自己的内置密钥不落盘，只留打码形式：它已经在 config.json
    里了，没必要在另一个文件里再抄一份。
    这个文件在 ``var/`` 里，跟 store.json 一样属于服务器私有数据。
    """
    is_own = who == "own"
    entry = {
        "t": int(time.time()),
        "time": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
        "ip": ip,
        "who": "own" if is_own else "builtin",
        "key": str(key or "") if is_own else "",
        "key_masked": mask_key(str(key or "")),
        "model": str(model or ""),
        "protocol": str(protocol or "openai"),
        "base": str(base or config_default_base(protocol or "openai")).rstrip("/"),
        "kind": kind,
        "input": str(input_text or "")[:AI_LOG_INPUT_CHARS],
        "ok": bool(ok),
        "output": str(output or "")[:AI_LOG_TEXT_CHARS],
        "ms": int(elapsed_ms or 0),
        "tokens": {
            "in": int((tokens or {}).get("in") or 0),
            "out": int((tokens or {}).get("out") or 0),
        },
        # 这次实际扣掉的额度（含高峰倍数）——后台一眼看出「为什么掉这么快」
        "cost": int(cost or 0),
        "peak": bool(peak),
    }
    with _ai_log_lock:
        entries = _load_ai_log()
        entries.append(entry)
        if len(entries) > AI_LOG_MAX:
            entries = entries[-AI_LOG_MAX:]
        _save_ai_log(entries)
    debug("[AI] 记录一条分析：" + ip + " " + entry["who"] + " " + str(entry["ms"]) + "ms")


def recent_analyses(limit: int = 50) -> list:
    """最近的分析记录，最新的在前。"""
    with _ai_log_lock:
        entries = _load_ai_log()
    limit = max(1, min(int(limit or 50), AI_LOG_MAX))
    return list(reversed(entries))[:limit]


def clear_analyses() -> int:
    """清空分析记录，返回删掉的条数。"""
    with _ai_log_lock:
        entries = _load_ai_log()
        _save_ai_log([])
    return len(entries)


# ============ 抓日志 ============

def fetch_log(config: Config, url: str, lang: str = DEFAULT_LANG) -> tuple[bool, str]:
    """抓日志。返回 ``(是否成功, 文本或错误说明)``。"""
    target = normalize_log_url(url)
    request = urllib.request.Request(target, headers={
        "User-Agent": USER_AGENT, "Accept": "text/plain, */*"})
    try:
        with urllib.request.urlopen(request, timeout=config.http_timeout) as resp:
            raw = resp.read(config.ai_max_log_chars * 4)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, t("ai.err.log_404", lang)
        return False, t("ai.err.log_http", lang, code=exc.code)
    except Exception as exc:
        return False, t("ai.err.log_fetch", lang, err=str(exc))

    text = raw.decode("utf-8", "replace")
    if not text.strip():
        return False, t("ai.err.log_empty", lang)
    return True, text


def trim_log(text: str, limit: int, lang: str = DEFAULT_LANG) -> str:
    """超长就掐中间：崩溃原因一般在结尾，mod 列表在开头，两头都比中间有用。"""
    if len(text) <= limit:
        return text
    head = int(limit * 0.25)
    tail = limit - head
    return (text[:head] + "\n\n"
            + t("ai.trim_note", lang, n=len(text) - limit)
            + "\n\n" + text[-tail:])


# ============ 调 AI ============

# 用户自带密钥时可以选接口协议。两种协议的请求头、请求体、返回体都不一样。
PROTOCOLS = {
    "openai": ("OpenAI 兼容", "gpt-4o-mini"),
    "anthropic": ("Anthropic", "claude-sonnet-5"),
}


def _endpoint(base: str, protocol: str) -> str:
    """拼出真正的请求地址。

    地址让用户手填，写法不一致很正常，所以这里容错：
    ``https://api.openai.com/v1`` 和 ``https://api.openai.com`` 都当同一个用。
    """
    base = (base or "").rstrip("/")
    if protocol == "anthropic":
        return base + ("/messages" if base.endswith("/v1") else "/v1/messages")
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


def _http_error(exc: urllib.error.HTTPError, lang: str = DEFAULT_LANG) -> tuple[bool, str]:
    body = ""
    try:
        body = exc.read().decode("utf-8", "replace")[:300]
    except Exception:
        pass
    detail = "：" + body if body else ""
    known = {401: "ai.err.key_invalid", 402: "ai.err.no_balance",
             403: "ai.err.forbidden", 429: "ai.err.rate_limit"}
    if exc.code in known:
        return False, t(known[exc.code], lang) + t("ai.err.http_code", lang, code=exc.code) + detail
    return False, t("ai.err.http", lang, code=exc.code) + detail


def _too_long(config: Config, lang: str = DEFAULT_LANG) -> tuple[bool, str]:
    """推理模型把 token 全花在思考上、没写出答案时的提示。"""
    return False, t("ai.err.too_long", lang, n=config.ai_max_tokens)


def _post(url: str, payload: dict, headers: dict, config: Config,
          lang: str = DEFAULT_LANG):
    """POST JSON。返回 ``(数据, 错误信息)``，两者必有一个为 None。"""
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=config.ai_timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as exc:
        return None, _http_error(exc, lang)
    except Exception as exc:
        return None, (False, t("ai.err.unreachable", lang, err=str(exc)))


def _parse_openai(config: Config, data: dict, lang: str = DEFAULT_LANG) -> tuple[bool, str]:
    try:
        choice = data["choices"][0]
        text = str((choice.get("message") or {}).get("content") or "").strip()
        finish = str(choice.get("finish_reason") or "")
    except Exception:
        return False, t("ai.err.unparsable", lang,
                        body=json.dumps(data, ensure_ascii=False)[:280])
    if text:
        return True, text
    # deepseek-flash / v4-pro 都是推理模型：先花 token 想，再写答案。
    # max_tokens 给少了，token 全用在思考上，content 就是空的、finish_reason=length。
    if finish == "length":
        return _too_long(config, lang)
    return False, t("ai.err.empty_finish", lang, reason=finish or t("ai.none", lang))


def _parse_anthropic(config: Config, data: dict, lang: str = DEFAULT_LANG) -> tuple[bool, str]:
    text = "".join(str(block.get("text") or "")
                   for block in (data.get("content") or [])
                   if isinstance(block, dict) and block.get("type") == "text").strip()
    if text:
        return True, text
    if str(data.get("stop_reason") or "") == "max_tokens":
        return _too_long(config, lang)
    return False, t("ai.err.empty_stop", lang,
                    reason=str(data.get("stop_reason") or t("ai.none", lang)))


def _reasoning_of(data: dict) -> str:
    """把模型的思考过程捞出来。

    DeepSeek / 通义这类推理模型把思考放在 ``message.reasoning_content``，
    有的兼容接口叫 ``reasoning``，Anthropic 是 content 里 type=thinking 的块。
    拿不到就返回空字符串——页面上就不显示那一块。
    """
    try:
        message = (data.get("choices") or [{}])[0].get("message") or {}
        for key in ("reasoning_content", "reasoning"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        blocks = data.get("content")
        if isinstance(blocks, list):
            parts = [str(block.get("thinking") or "")
                     for block in blocks
                     if isinstance(block, dict) and block.get("type") == "thinking"]
            joined = "".join(parts).strip()
            if joined:
                return joined
    except Exception:
        pass
    return ""


def analyze(config: Config, messages: list[dict], key_conf: dict,
            lang: str = DEFAULT_LANG) -> tuple[bool, str, dict]:
    """调 AI。返回 ``(是否成功, 结果或错误说明, 用量)``。

    ``messages`` 是**不带 system** 的对话历史（``[{"role","content"}]``）：
    第一次是一条 user（日志），追问时后面接着上一轮的 user/assistant。
    ``key_conf`` 里可以带 ``protocol``（openai / anthropic）、``base``、``model``；
    缺哪项就用 config 里的默认值。``lang`` 决定系统提示词——AI 要用访客那种语言回答。
    第三个返回值是 ``{"in", "out", "ms", "reasoning"}``，给后台记录和页面用。
    """
    key = str(key_conf.get("key") or config.ai_api_key)
    if not key:
        return False, t("ai.err.no_key", lang), {}
    protocol = str(key_conf.get("protocol") or "openai").lower()
    if protocol not in PROTOCOLS:
        protocol = "openai"
    base = str(key_conf.get("base") or config.ai_api_base)
    model = str(key_conf.get("model") or config.ai_model)
    url = _endpoint(base, protocol)
    prompt = system_prompt(lang)
    turns = [m for m in (messages or []) if isinstance(m, dict) and m.get("content")]

    if protocol == "anthropic":
        payload = {"model": model, "max_tokens": config.ai_max_tokens,
                   "system": prompt, "messages": turns}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        parse = _parse_anthropic
    else:
        payload = {"model": model, "max_tokens": config.ai_max_tokens, "temperature": 0.3,
                   "stream": False,
                   "messages": [{"role": "system", "content": prompt}] + turns}
        # 推理强度：low 让模型少想、把 token 留给答案。认不出的值就不发这个参数，
        # 免得某些兼容接口因为多一个字段直接 400。
        effort = str(config.ai_reasoning_effort or "").strip().lower()
        if effort in ("low", "medium", "high"):
            payload["reasoning_effort"] = effort
        headers = {"Authorization": "Bearer " + key}
        parse = _parse_openai

    started = time.perf_counter()
    data, error = _post(url, payload, headers, config, lang)
    if error:
        return False, error, {}
    elapsed = round((time.perf_counter() - started) * 1000)
    ok, result = parse(config, data, lang)
    if not ok:
        return False, result, {}

    usage = data.get("usage") or {}
    tokens = {
        "in": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
        "out": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
    }
    debug("[AI] " + protocol + " 完成（" + str(elapsed) + "ms，输入 "
          + str(tokens["in"] or "?")
          + " tokens，输出 "
          + str(tokens["out"] or "?")
          + " tokens）")
    return True, result, {"in": tokens["in"], "out": tokens["out"],
                          "ms": elapsed, "reasoning": _reasoning_of(data)}


# ============ 提交任务 ============

# 一次分析最多留几轮对话在 job 里（追问用的上下文），太多会撑爆 ai_jobs.json
HISTORY_MAX_TURNS = 8
HISTORY_MAX_CHARS = 12000


def _trim_history(history: list[dict]) -> list[dict]:
    """只留最近几轮、总字数也别失控。追问是加分项，不能把磁盘写满。"""
    turns = [m for m in (history or []) if isinstance(m, dict) and m.get("content")]
    turns = turns[-HISTORY_MAX_TURNS * 2:]
    total, kept = 0, []
    for message in reversed(turns):
        length = len(str(message.get("content") or ""))
        if total + length > HISTORY_MAX_CHARS and kept:
            break
        kept.append(message)
        total += length
    return list(reversed(kept))


def _quota_state(config: Config, ip: str, lang: str = DEFAULT_LANG) -> dict:
    """页面/后台用：这个 IP 在当前窗口的额度状况（不计数的一律返回 unlimited）。"""
    limit = config.ai_token_limit_or_zero()
    bucket = token_bucket(config)
    used = QUOTA.used(ip, bucket)
    calls = QUOTA.calls(ip, bucket)
    return {
        "bucket": bucket,
        "limit": limit,
        "used": used,
        "calls": calls,
        "left": max(0, limit - used) if limit > 0 else 0,
        "unlimited": limit <= 0,
        "percent": (min(100.0, used * 100.0 / limit) if limit > 0 else 0.0),
        "reset_at": bucket_reset_at(config),
        "window": config.ai_window_label(lang),
        "peak": is_peak(),
        "factor": peak_factor(config),
    }


def submit(config: Config, ip: str, raw_input: str, use_own: bool,
           lang: str = DEFAULT_LANG, follow: bool = False) -> tuple[bool, str]:
    """校验 + 扣额度 + 起后台线程。返回 ``(是否受理, 给用户看的说明)``。

    ``follow=True`` 是「对上一个结果追问」：不再抓日志，把上一轮的对话历史
    接上新问题一起发。追问同样占额度——它比首轮便宜（不用重发整份日志），
    但也不是白送的。

    ``lang`` 一并交给后台线程：结果文字是按**提交时**的语言生成的，
    存进任务记录后就不再变（用户中途换语言不会把已有结果变成另一种文字）。
    """
    if not config.enable_ai:
        return False, t("ai.err.disabled", lang)

    job = get_job(ip)
    if job and job.get("state") == "running":
        return False, t("ai.err.busy", lang)

    if follow:
        history = _trim_history((job or {}).get("history") or [])
        if not history or (job or {}).get("state") != "done":
            return False, t("ai.err.no_follow", lang)
        question = (raw_input or "").strip()
        if not question:
            return False, t("ai.err.follow_empty", lang)
        kind, value = "follow", question[:MAX_INLINE_CHARS]
        display = t("ai.follow_note", lang, n=len(value))
    else:
        kind, value = split_input(raw_input)
        if kind == "empty":
            return False, t("ai.err.empty_input", lang)
        display = value if kind == "url" else t("ai.inline_note", lang, n=len(value))

    # who 存的是机器可读的标记（own / builtin），显示时才按语言翻——
    # 以前存的是「私人密钥」这种中文，页脚判断直接拿它比字符串。
    who = "own" if use_own else "builtin"
    key_conf: dict = {}
    if use_own:
        saved = get_own_key(ip)
        if not saved:
            return False, t("ai.err.no_saved_key", lang)
        key_conf = dict(saved)
        # 没填地址就用该协议的官方地址，别掉进 config 里那个 DeepSeek 地址
        protocol = str(key_conf.get("protocol") or "openai")
        if not key_conf.get("base"):
            key_conf["base"] = config_default_base(protocol)
    else:
        key_conf = {"key": config.ai_api_key}
        if not key_conf["key"]:
            return False, t("ai.err.no_site_key", lang)

    # 私人密钥不占额度；用公用密钥才扣。token 只能事后知道，所以先预留一笔。
    bucket = token_bucket(config)
    reserved = 0
    counted = not use_own and ip not in (config.ai_free_ips or [])
    if counted:
        limit = config.ai_token_limit_or_zero()
        allowed, reason, reserved = QUOTA.reserve(
            ip, bucket, config.ai_reserve_tokens, limit, config.ai_rate_limit)
        if not allowed:
            state = _quota_state(config, ip, lang)
            if reason == "calls":
                return False, t("ai.err.calls_out", lang,
                                n=config.ai_rate_limit, window=state["window"])
            return False, t("ai.err.quota_out", lang,
                            used=format_tokens(state["used"]),
                            limit=format_tokens(state["limit"]),
                            window=state["window"])
        state = _quota_state(config, ip, lang)
        remaining = state["left"]
    else:
        state = _quota_state(config, ip, lang)
        remaining = None

    out("[AI] " + ip + " 提交" + ("追问" if follow else "分析")
        + "（" + who + "，" + display + "）")
    _set_job(ip, state="running", text="", query=display, who=who, lang=lang,
             remaining=remaining, started=time.time(),
             reasoning="", cost=0, reserved=reserved, follow=follow)

    threading.Thread(target=_worker,
                     args=(config, ip, kind, value, key_conf, use_own, lang, reserved, follow),
                     name="ai-" + ip, daemon=True).start()
    return True, t("ai.follow_submitted" if follow else "ai.submitted", lang)


def _worker(config: Config, ip: str, kind: str, value: str,
            key_conf: dict, use_own: bool, lang: str = DEFAULT_LANG,
            reserved: int = 0, follow: bool = False) -> None:
    """后台干活：抓日志 → 调 AI → 写结果。异常都变成给用户看的说明。

    每一条都往 ``var/ai_log.json`` 记一份（成功、失败、抓日志失败都记），
    后台「最近分析」看的就是它。
    """
    who = "own" if use_own else "builtin"
    key = str(key_conf.get("key") or config.ai_api_key)
    model = str(key_conf.get("model") or config.ai_model)
    protocol = str(key_conf.get("protocol") or "openai").lower()
    # 这次请求实际发去哪个地址：自带密钥用他填的（没填就是该协议的官方地址），
    # 公用密钥用 config.ai_api_base。后台要能看到这个，不然访客说「我填的是
    # 某某中转站」时对不上账。
    base = str(key_conf.get("base") or config.ai_api_base or "").rstrip("/")

    def note_record(input_text: str, ok: bool, output: str, ms: int = 0,
                    tokens: dict | None = None, cost: int = 0) -> None:
        try:
            record_analysis(ip, who, key, model, protocol, kind, input_text,
                            ok, output, ms, tokens, base,
                            cost=cost, peak=is_peak())
        except Exception as exc:                       # 记录失败不能影响分析
            warn("[AI] 写分析记录失败：" + str(exc))

    started = time.time()
    try:
        previous = _trim_history((get_job(ip) or {}).get("history") or [])
        if follow:
            turns = previous + [{"role": "user", "content": value}]
            input_text = value
            debug("[AI] " + ip + " 追问：" + value[:80])
        else:
            if kind == "url":
                ok, log_text = fetch_log(config, value, lang)
                if not ok:
                    note_record(value, False, log_text)
                    _fail(config, ip, use_own, log_text, lang, reserved)
                    return
            else:
                log_text = value
            input_text = value if kind == "url" else log_text
            log_text = trim_log(log_text, config.ai_max_log_chars, lang)
            debug("[AI] " + ip + " 日志 " + str(len(log_text)) + " 字，开始调 " + config.ai_model)
            turns = [{"role": "user",
                      "content": t("ai.user_prefix", lang) + "\n\n" + log_text}]

        ok, result, usage = analyze(config, turns, key_conf, lang)
        ms = int((time.time() - started) * 1000)
        if not ok:
            note_record(input_text, False, result, ms)
            _fail(config, ip, use_own, result, lang, reserved)
            return

        cost = usage_tokens(usage, config)
        _settle(config, ip, reserved, cost, use_own)
        history = _trim_history(turns + [{"role": "assistant", "content": result}])
        note_record(input_text, True, result, usage.get("ms") or ms,
                    {"in": usage.get("in") or 0, "out": usage.get("out") or 0},
                    cost=int(cost))
        _set_job(ip, state="done", text=result, error="", history=history,
                 reasoning=str(usage.get("reasoning") or "")[:config.ai_reasoning_max_chars],
                 cost=int(cost), tokens={"in": usage.get("in") or 0,
                                         "out": usage.get("out") or 0},
                 remaining=(None if use_own or ip in (config.ai_free_ips or [])
                            else _quota_state(config, ip, lang)["left"]))
        out("[AI] " + ip + " 分析完成，结果 " + str(len(result)) + " 字，"
            + str(cost) + " 额度")
    except Exception as exc:
        error("[AI] 任务异常：" + repr(exc))
        note_record(value, False, repr(exc))
        _fail(config, ip, use_own, t("ai.err.crashed", lang, err=str(exc)), lang, reserved)


def _settle(config: Config, ip: str, reserved: int, actual: int, use_own: bool) -> None:
    """按实际用量结算：预留多了退回，少了补扣。"""
    if use_own or ip in (config.ai_free_ips or []):
        return
    QUOTA.settle(ip, token_bucket(config), reserved, actual)


def _fail(config: Config, ip: str, use_own: bool, message: str,
          lang: str = DEFAULT_LANG, reserved: int = 0) -> None:
    """失败：把预留的额度整笔还回去（可配置），并把原因写给用户看。"""
    if not use_own and config.ai_refund_on_failure and ip not in (config.ai_free_ips or []):
        QUOTA.refund(ip, token_bucket(config), reserved)
        message += "\n" + t("ai.refunded", lang)
    warn("[AI] " + ip + " 分析失败：" + message.splitlines()[0][:160])
    _set_job(ip, state="error", text=message, error=message, reasoning="", cost=0)


# ============ 页面构建 ============

# 页面零件现在住在 xaml.py（计算器页也要用），这里起回原来的短名字，
# 下面几百行正文就不用跟着改。
_attr = attr
_url_attr = url_attr
_bind = bind_to


def _status_line(config: Config, ip: str, lang: str = DEFAULT_LANG) -> str:
    """页面上那行「现在用的是哪个密钥、额度还剩多少」。"""
    if get_own_key(ip):
        return t("ai.status_own", lang, desc=describe_own_key(ip, lang))
    if ip in (config.ai_free_ips or []):
        return t("ai.status_free", lang)
    state = _quota_state(config, ip, lang)
    if state["unlimited"]:
        return t("ai.status_unlimited", lang)
    if state["left"] <= 0:
        return t("ai.status_out", lang, window=state["window"],
                 at=_reset_text(config, lang))
    return t("ai.status_left", lang, left=format_tokens(state["left"]),
             window=state["window"], at=_reset_text(config, lang))


# 页面零件统一从 xaml.py 取（计算器页共用同一套），这里起回短名字。
_input_row = input_row
_help_button = help_button
_grid2 = grid2
_divider = divider
_heading = heading
_note = note
_nav_row = nav_row


def _result_block(config: Config, job: dict | None, lang: str = DEFAULT_LANG) -> str:
    """结果区：正在分析 / 分析结果 / 分析失败，外加底部的来源说明。

    结果文字是**提交时**按当时的语言生成的（存在任务记录里），所以这里
    只翻标题和页脚，不碰 ``job["text"]``。
    """
    if not job:
        return ""
    state = job.get("state")
    if state == "running":
        return ('<local:MyHint Theme="Blue" Margin="0,14,0,0" Text="'
                + _attr(t("ai.running", lang, query=str(job.get("query") or "")))
                + '" />')
    if state not in ("done", "error"):
        return ""

    done = state == "done"
    # 页脚文案：公用密钥点名模型，私人密钥不点（各家模型不一样）。
    # 老的记录里 who 存的是中文「私人密钥」，一并认掉。
    who = str(job.get("who") or "")
    if who in ("own", "私人密钥"):
        footer = t("ai.footer_own", lang)
    else:
        footer = t("ai.footer", lang, name=config.ai_display_name)

    if not done:
        return (
            '<StackPanel Margin="0,14,0,0">'
            '<TextBlock Text="' + escape_attr(t("ai.result_fail", lang)) + '" FontSize="14" '
            'FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" Margin="0,0,0,8" />'
            '<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14">'
            '<TextBlock Text="' + _attr(str(job.get("text") or "")) + '" FontSize="13" '
            'LineHeight="24" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush1}" />'
            "</Border>"
            '<TextBlock Text="' + _attr(footer) + '" FontSize="11" Margin="0,10,0,0" '
            'HorizontalAlignment="Right" Foreground="{DynamicResource ColorBrush3}" />'
            "</StackPanel>")

    # ---- 成功：对话式展示（首轮 + 追问）----
    parts = ['<StackPanel Margin="0,14,0,0">',
             '<TextBlock Text="' + escape_attr(t("ai.result_ok", lang)) + '" FontSize="14" '
             'FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" Margin="0,0,0,8" />']
    history = job.get("history") or []
    # history 里最后一条 assistant 就是本次回答；前面的是之前的轮次。
    # 全部平铺出来，用户往回翻能看到自己问过什么。
    if history:
        for message in history:
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            if not content:
                continue
            if role == "assistant":
                parts.append(
                    '<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" '
                    'Padding="16,14" Margin="0,0,0,8">'
                    '<TextBlock Text="' + _attr(content) + '" FontSize="13" '
                    'LineHeight="24" TextWrapping="Wrap" '
                    'Foreground="{DynamicResource ColorBrush1}" /></Border>')
            else:
                parts.append(
                    '<TextBlock Text="' + _attr(t("ai.you_asked", lang, q=content[:400]))
                    + '" FontSize="11" LineHeight="18" TextWrapping="Wrap" Margin="0,0,0,6" '
                    'Foreground="{DynamicResource ColorBrush3}" />')
    else:
        # 老记录没有 history（改造前存的），退回只显示最后那段文字
        parts.append(
            '<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" '
            'Padding="16,14"><TextBlock Text="' + _attr(str(job.get("text") or ""))
            + '" FontSize="13" LineHeight="24" TextWrapping="Wrap" '
            'Foreground="{DynamicResource ColorBrush1}" /></Border>')

    reasoning = str(job.get("reasoning") or "")
    if reasoning:
        parts.append(
            '<local:MyCard Title="' + escape_attr(t("ai.reasoning_title", lang)) + '" '
            'CanSwap="True" Margin="0,10,0,0">'
            '<StackPanel Margin="18,26,16,14">'
            '<TextBlock Text="' + _attr(reasoning) + '" FontSize="11" LineHeight="18" '
            'TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush2}" '
            'Opacity="0.85" /></StackPanel></local:MyCard>')

    cost = int(job.get("cost") or 0)
    if cost > 0:
        parts.append('<TextBlock Text="' + _attr(t("ai.cost_line", lang,
                                                  n=format_tokens(cost))) + '" '
                     'FontSize="11" Margin="0,8,0,0" '
                     'Foreground="{DynamicResource ColorBrush3}" />')
    parts.append('<TextBlock Text="' + _attr(footer) + '" FontSize="11" Margin="0,8,0,0" '
                 'HorizontalAlignment="Right" Foreground="{DynamicResource ColorBrush3}" />')
    parts.append("</StackPanel>")
    return "".join(parts)


def _reset_text(config: Config, lang: str = DEFAULT_LANG) -> str:
    """窗口还剩多久重置，写成「16:30」这种人话。"""
    moment = datetime.fromtimestamp(bucket_reset_at(config), BEIJING)
    return moment.strftime("%H:%M")


def _quota_bar(config: Config, ip: str, lang: str = DEFAULT_LANG) -> str:
    """5 小时额度进度条：上面一行字，下面一条轨道。

    轨道用两列 star 宽度按比例分——跟访问页省份排行的做法一致，
    不依赖 ProgressBar（那玩意在 PCL 的配色里不一定好看）。
    """
    state = _quota_state(config, ip, lang)
    if state["unlimited"]:
        return _note(t("ai.quota_unlimited", lang, window=state["window"]))
    percent = state["percent"]
    fill = 100 - max(0, min(100, int(round(percent))))
    # 用量极少时也留一点宽度，不然看起来像空的
    left_cols = max(0, 100 - fill)
    right_cols = max(1, fill) if left_cols < 100 else 1
    left_cols = max(1, left_cols) if percent < 1 and state["used"] > 0 else left_cols
    bar = (
        '<Grid Margin="0,6,0,0">'
        '<Grid.ColumnDefinitions><ColumnDefinition Width="' + str(left_cols)
        + '*" /><ColumnDefinition Width="' + str(right_cols) + '*" /></Grid.ColumnDefinitions>'
        '<Border Grid.Column="0" Height="7" CornerRadius="3.5" '
        'Background="{DynamicResource ColorBrush1}" Opacity="0.62" />'
        '<Border Grid.Column="1" Height="7" CornerRadius="3.5" '
        'Background="{DynamicResource ColorBrush7}" /></Grid>')
    label = t("ai.quota_line", lang,
              used=format_tokens(state["used"]),
              limit=format_tokens(state["limit"]),
              percent=str(int(round(percent))),
              window=state["window"],
              at=_reset_text(config, lang))
    return ('<TextBlock Text="' + _attr(label) + '" FontSize="11" LineHeight="18" '
            'TextWrapping="Wrap" Margin="0,10,0,0" '
            'Foreground="{DynamicResource ColorBrush1}" />' + bar)


def build_page(config: Config, ip: str, base_url: str, lang: str = DEFAULT_LANG) -> str:
    """「AI 日志分析」主页。入口是欢迎卡片那排按钮里的第 4 个。

    这一页只管公用密钥那条线：粘链接、点按钮、看结果。私人密钥的设置和用法
    都挪到了 :func:`build_own_page`，免得正事被一长串设置项埋掉。
    """
    if not config.enable_ai:
        return build_popup(t("ai.title", lang), t("ai.err.page_disabled", lang), "Yellow", lang=lang)

    base = _url_attr(base_url)
    job = get_job(ip)

    return (
        '<local:MyCard Title="' + escape_attr(t("ai.title", lang)) + '" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'

        '<TextBlock Text="' + escape_attr(t("ai.headline", lang)) + '" FontSize="24" '
        'FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />'
        '<TextBlock TextWrapping="Wrap" FontSize="12" LineHeight="20" Margin="0,10,0,0" '
        'Foreground="{DynamicResource ColorBrush3}" Text="'
        + escape_attr(t("ai.intro", lang)) + '" />'
        # 「点击我上传」得能点。MyTextButton 是 PCL 里唯一没有边框、看着就是
        # 一行字的按钮（见帮助文件「自定义事件」那节），点一下开 mclo.gs。
        '<local:MyTextButton Margin="0,4,0,0" HorizontalAlignment="Left" '
        'Text="' + escape_attr(t("ai.upload", lang)) + '" ToolTip="'
        + escape_attr(t("ai.upload_tip", lang)) + '" '
        'EventType="打开网页" EventData="https://mclo.gs/" />'

        + _input_row("ailoginput", t("ai.log_hint", lang), 40, "0,14,0,0")

        + _grid2(
            _help_button(t("ai.builtin_key", lang), ICON_AI, _bind("ailoginput", base, "/ai.json"),
                         46, column=0, margin="0,0,5,0"),
            _help_button(t("ai.own_key", lang), ICON_KEY, base + "/ai_own_page.json",
                         46, column=1, margin="5,0,0,0"),
            "0,10,0,0")

        # 额度和窗口都跟着配置走。写死「多少次」「每天」的话，站长一改配置
        # 这行就开始骗人了。
        + _note(t("ai.limit", lang,
                  limit=format_tokens(config.ai_token_limit_or_zero()),
                  window=config.ai_window_label(lang),
                  factor=str(int(config.ai_peak_factor()))))

        + _divider("0,20,0,12")

        + '<TextBlock Text="' + _attr(_status_line(config, ip, lang)) + '" FontSize="12" '
        'FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" />'

        + _quota_bar(config, ip, lang)

        + _result_block(config, job, lang)

        # 追问：只有上一次跑成功了才显示，否则用户会对着空对话框打字
        + (_follow_block(config, ip, base, job, lang)
           if (job or {}).get("state") == "done" else "")

        + _nav_row(refresh_url=base + "/ai_page.json", lang=lang)

        + "</StackPanel>"
        "</local:MyCard>")


def _follow_block(config: Config, ip: str, base: str, job: dict | None,
                  lang: str = DEFAULT_LANG) -> str:
    """结果下面的「接着问」输入框。

    追问带上一轮的完整对话，所以不用把日志再粘一遍；额度照扣，
    但比首轮便宜（日志只在第一轮发一次）。
    """
    return (
        '<local:MyCard Title="' + escape_attr(t("ai.follow_title", lang)) + '" '
        'CanSwap="True" Margin="0,14,0,0">'
        '<StackPanel Margin="18,26,16,14">'
        '<TextBlock Text="' + escape_attr(t("ai.follow_hint", lang)) + '" '
        'FontSize="11" LineHeight="18" TextWrapping="Wrap" Margin="0,0,0,8" '
        'Foreground="{DynamicResource ColorBrush3}" />'
        + _input_row("aifollowinput", t("ai.follow_placeholder", lang), 38)
        + _help_button(t("ai.follow_send", lang), ICON_AI,
                       _bind("aifollowinput", base, "/ai_follow.json"),
                       40, margin="0,8,0,0")
        + "</StackPanel></local:MyCard>")


def build_own_page(config: Config, ip: str, base_url: str, lang: str = DEFAULT_LANG) -> str:
    """私人密钥页：填自己的密钥、请求地址、模型，然后就在这一页发起分析。"""
    if not config.enable_ai:
        return build_popup(t("ai.title", lang), t("ai.err.page_disabled", lang), "Yellow", lang=lang)

    base = _url_attr(base_url)
    job = get_job(ip)
    own = get_own_key(ip)

    protocol = str((own or {}).get("protocol") or "openai")
    saved_base = str((own or {}).get("base") or "")
    saved_model = str((own or {}).get("model") or "")
    key_hint = (t("ai.key_saved", lang, key=mask_key(str(own.get("key") or ""))) if own
                else t("ai.key_hint", lang))
    base_hint = (t("ai.base_saved", lang, base=saved_base) if saved_base
                 else t("ai.base_hint", lang))
    model_hint = (t("ai.model_saved", lang, model=saved_model) if saved_model
                  else t("ai.model_hint", lang, model=PROTOCOLS.get(protocol, ("", ""))[1]))

    return (
        '<local:MyCard Title="' + escape_attr(t("ai.own_key", lang)) + '" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'

        '<TextBlock Text="' + escape_attr(t("ai.own_headline", lang)) + '" FontSize="24" '
        'FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />'
        '<TextBlock TextWrapping="Wrap" FontSize="12" LineHeight="20" Margin="0,10,0,0" '
        'Foreground="{DynamicResource ColorBrush3}" Text="'
        + escape_attr(t("ai.own_intro", lang)) + '" />'

        + _heading(t("ai.log_link", lang), "0,18,0,0")
        + _input_row("ailoginput", t("ai.log_hint", lang))

        + _heading(t("ai.api_key", lang))
        + _input_row("aikeyinput", key_hint)
        + _grid2(
            _help_button(t("ai.save_key", lang), ICON_SAVE,
                         _bind("aikeyinput", base, "/ai_key.json"),
                         38, column=0, margin="0,0,5,0"),
            _help_button(t("ai.clear", lang), ICON_TRASH, base + "/ai_clear.json",
                         38, column=1, margin="5,0,0,0", color=""))

        + _heading(t("ai.base_title", lang))
        + _input_row("aibaseinput", base_hint)
        + _grid2(
            _help_button(t("ai.protocol.openai", lang), ICON_SAVE,
                         _bind("aibaseinput", base, "/ai_base_openai.json"),
                         38, column=0, margin="0,0,5,0"),
            _help_button(t("ai.protocol.anthropic", lang), ICON_SAVE,
                         _bind("aibaseinput", base, "/ai_base_anthropic.json"),
                         38, column=1, margin="5,0,0,0"))
        + _note(t("ai.base_note", lang))

        + _heading(t("ai.model_title", lang))
        + _input_row("aimodelinput", model_hint)
        + _help_button(t("ai.save_model", lang), ICON_SAVE,
                       _bind("aimodelinput", base, "/ai_model.json"), 38, margin="0,8,0,0")
        + _note(t("ai.model_note", lang, openai=PROTOCOLS["openai"][1],
                  anthropic=PROTOCOLS["anthropic"][1]))

        + _divider("0,22,0,12")

        + _help_button(t("ai.start", lang), ICON_AI, _bind("ailoginput", base, "/ai_own.json"), 46)

        + '<TextBlock Text="' + _attr(_status_line(config, ip, lang)) + '" FontSize="12" '
        'FontWeight="Bold" Margin="0,12,0,0" Foreground="{DynamicResource ColorBrush1}" />'

        + _result_block(config, job, lang)

        + _nav_row(refresh_url=base + "/ai_own_page.json", lang=lang,
                   extra_text=t("ai.back_to_analysis", lang), extra_url=base + "/ai_page.json")

        + "</StackPanel>"
        "</local:MyCard>")


def admin_state(config: Config) -> dict:
    """后台「AI 日志分析」那块要显示的东西。"""
    if not config.enable_ai:
        return {"enabled": False}
    bucket = token_bucket(config)
    rows = QUOTA.rows_for(bucket)
    with _jobs_lock:
        jobs = len(_jobs)
        running = sum(1 for j in _jobs.values() if j.get("state") == "running")
    with _own_lock:
        keys = len(_own_keys)
    return {
        "enabled": True,
        "model": config.ai_model,
        "base": config.ai_api_base,
        "display_name": config.ai_display_name,
        "limit": config.ai_token_limit_or_zero(),
        "window": config.ai_window_label("zh-hans"),
        "window_text": config.ai_window_label("zh-hans"),
        "window_seconds": config.ai_window_seconds(),
        "peak": is_peak(),
        "peak_factor": config.ai_peak_factor(),
        "bucket": bucket,
        "rows": rows,
        "calls": sum(int(r.get("calls") or 0) for r in rows),
        "tokens": sum(int(r.get("used") or 0) for r in rows),
        "jobs": jobs,
        "running": running,
        "own_keys": keys,
    }


def reset(scope: str = "all", ip: str = "", window: str = "hour",
          config: Config | None = None) -> dict:
    """后台重置。scope 取 quota / jobs / keys / all；给了 ip 就只动这个 IP。

    不传 ip 时只清**当前这一格**的（历史留着好看趋势）；传了 ip 就把那个 IP 清干净。
    ``window`` 是老参数，留着兼容老调用；新代码走 ``config`` 算格子。
    """
    ip = (ip or "").strip() or None
    bucket = token_bucket(config) if config is not None else beijing_bucket(window)
    done = {}
    if scope in ("quota", "all"):
        done["quota"] = QUOTA.reset(None if ip else bucket, ip)
    if scope in ("jobs", "all"):
        done["jobs"] = reset_jobs(ip)
    if scope in ("keys", "all"):
        done["keys"] = reset_keys(ip)
    out("[AI] 后台重置 " + scope + ("（" + str(ip) + "）" if ip else "（全部）")
        + "：" + json.dumps(done, ensure_ascii=False))
    return done


def build_popup(title: str, message: str, theme: str = "Blue", back: str = "",
                home: bool = False, lang: str = DEFAULT_LANG) -> str:
    """提示弹窗。

    「继续」按钮重新打开对应页面（``打开帮助``）：PCL 会重新下载那对 .json/.xaml，
    于是显示的是最新状态（正在分析 / 结果）。用 ``刷新页面`` 只会把当前这个弹窗
    自己重画一遍，看不到任何新东西。

    ``home=True`` 再加一个「返回主页」：这些页是一层层叠上去的，点几下就深了，
    有这个按钮就能一步回去，不用沿路把左上角按穿。它走 ``切换页面|Launch`` 切回
    启动器主页——以前是再翻开一份 home.json，那出来的其实是帮助页。
    """
    cells = []
    if home:
        cells.append('<local:MyButton Height="35" Width="110" Text="'
                     + escape_attr(t("btn.back_home", lang)) + '" '
                     'EventType="' + HOME_EVENT_TYPE + '" EventData="'
                     + HOME_EVENT_DATA + '" />')
    if back:
        cells.append('<local:MyButton Height="35" Width="110" Text="'
                     + escape_attr(t("btn.continue", lang)) + '" ColorType="Highlight" '
                     'EventType="打开帮助" EventData="' + _url_attr(back) + '" />')
    else:
        cells.append('<local:MyButton Height="35" Width="110" Text="'
                     + escape_attr(t("btn.ok", lang)) + '" ColorType="Highlight" '
                     'EventType="刷新主页" EventData="-" />')

    row = "".join(cell if index == 0 else '<StackPanel Margin="8,0,0,0">' + cell + "</StackPanel>"
                  for index, cell in enumerate(cells))

    return (
        '<local:MyCard Title="' + escape_attr(title) + '" CanSwap="False" '
        'Width="460" HorizontalAlignment="Center" VerticalAlignment="Center">'
        '<StackPanel Margin="25,40,25,20">'
        '<local:MyHint Theme="' + theme + '" Text="' + _attr(message) + '" />'
        '<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,24,0,0">'
        + row +
        "</StackPanel>"
        "</StackPanel>"
        "</local:MyCard>")


def popup_json(title: str, desc: str) -> str:
    return json.dumps({"Title": title, "Description": desc}, ensure_ascii=False)


# ============ 路由 ============

# EventData 指向的 .json（PCL 先读它拿页面标题），以及同名的 .xaml（PCL 接着读它当内容）。
# 值改成 i18n 键，请求期按访客语言取。
_META = {
    "/ai_page.json": ("ai.title", "ai.meta.page"),
    "/ai_own_page.json": ("ai.own_key", "ai.meta.own_page"),
    "/ai.json": ("ai.title", "ai.meta.builtin"),
    "/ai_own.json": ("ai.title", "ai.meta.own"),
    "/ai_follow.json": ("ai.follow_title", "ai.meta.follow"),
    "/ai_key.json": ("ai.meta.save_key_title", "ai.meta.save_key"),
    "/ai_base_openai.json": ("ai.meta.base_openai_title", "ai.meta.base_openai"),
    "/ai_base_anthropic.json": ("ai.meta.base_anthropic_title", "ai.meta.base_anthropic"),
    "/ai_model.json": ("ai.save_model", "ai.meta.model"),
    "/ai_clear.json": ("ai.meta.clear_title", "ai.meta.clear"),
}
_DO = {"/ai_page.xaml": "page", "/ai_own_page.xaml": "own_page",
       "/ai.xaml": "builtin", "/ai_own.xaml": "own", "/ai_follow.xaml": "follow",
       "/ai_key.xaml": "savekey", "/ai_base_openai.xaml": "base_openai",
       "/ai_base_anthropic.xaml": "base_anthropic", "/ai_model.xaml": "savemodel",
       "/ai_clear.xaml": "clearkey"}

AI_PATHS = set(_META) | set(_DO)


def _json_response(body: str):
    from .server import Response
    return Response.text(body, content_type="application/json; charset=utf-8",
                         headers={"Cache-Control": "no-store"})


def _xaml_response(body: str):
    from .server import Response
    return Response.xaml(body)


def _styled(body: str, ip: str) -> str:
    """弹窗也要套上访客选的配色——它和页面一样是我们自己拼的 XAML。"""
    return palette.apply(body, profiles.PROFILES.palette_for(ip))


def handle(service, path: str, query: str, ip: str, origin: str = ""):
    """处理 ``/ai*`` 请求；不是我们的路径就返回 None。"""
    # 语言与配色都是纯内存查表，先算出来给下面所有分支用
    params = parse_query(query)
    lang = profiles.lang_for(ip)

    if path in _META:
        title_key, desc_key = _META[path]
        return _json_response(popup_json(t(title_key, lang), t(desc_key, lang)))

    action = _DO.get(path)
    if not action:
        return None

    config = service.config
    base = config.resolved_base_url(origin)
    page_url = base + "/ai_page.json"
    own_url = base + "/ai_own_page.json"

    if action == "page":
        return _xaml_response(palette.apply(build_page(config, ip, base, lang),
                                          profiles.PROFILES.palette_for(ip)))

    if action == "own_page":
        return _xaml_response(
            palette.apply(build_own_page(config, ip, base, lang),
                          profiles.PROFILES.palette_for(ip)))

    if action == "savekey":
        if set_own_key(ip, params.get("q", "")):
            return _xaml_response(_styled(build_popup(
                t("ai.popup.key_saved", lang), t("ai.popup.key_saved_msg", lang),
                back=own_url, home=True, lang=lang), ip))
        return _xaml_response(_styled(build_popup(
            t("ai.popup.key_empty", lang), t("ai.popup.key_empty_msg", lang),
            "Yellow", back=own_url, home=True, lang=lang), ip))

    if action in ("base_openai", "base_anthropic"):
        protocol = "openai" if action == "base_openai" else "anthropic"
        if not get_own_key(ip):
            return _xaml_response(_styled(build_popup(
                t("ai.popup.need_key", lang), t("ai.popup.need_key_base", lang),
                "Yellow", back=own_url, home=True, lang=lang), ip))
        set_own_base(ip, params.get("q", ""), protocol)
        return _xaml_response(_styled(build_popup(
            t("ai.popup.base_saved", lang, protocol=protocol_name(protocol, lang)),
            t("ai.popup.now", lang, desc=describe_own_key(ip, lang)),
            back=own_url, home=True, lang=lang), ip))

    if action == "savemodel":
        if not get_own_key(ip):
            return _xaml_response(_styled(build_popup(
                t("ai.popup.need_key", lang), t("ai.popup.need_key_model", lang),
                "Yellow", back=own_url, home=True, lang=lang), ip))
        set_own_model(ip, params.get("q", ""))
        return _xaml_response(_styled(build_popup(
            t("ai.popup.model_saved", lang),
            t("ai.popup.model_saved_msg", lang, desc=describe_own_key(ip, lang)),
            back=own_url, home=True, lang=lang), ip))

    if action == "clearkey":
        had = clear_own_key(ip)
        return _xaml_response(_styled(build_popup(
            t("ai.popup.cleared" if had else "ai.popup.nothing", lang),
            t("ai.popup.cleared_msg" if had else "ai.popup.nothing_msg", lang),
            back=own_url, home=True, lang=lang), ip))

    ok, message = submit(config, ip, params.get("q", ""), use_own=(action == "own"),
                         lang=lang, follow=(action == "follow"))
    return _xaml_response(_styled(build_popup(
        t("ai.popup.started" if ok else "ai.popup.not_started", lang), message,
        "Blue" if ok else "Yellow",
        back=own_url if action == "own" else page_url, home=True, lang=lang), ip))


def parse_query(query: str) -> dict:
    """宽松解析查询串。

    PCL 是把输入框原文**直接拼进 URL** 的（不做 URL 编码），所以链接里的 ``/``、
    ``:``、``?`` 都会原样出现，还可能夹着 ``%``。标准 parse_qs 遇到非法百分号
    转义会抛异常，这里统一兜住。
    """
    try:
        values = parse_qs(query or "", keep_blank_values=True)
        return {key: val[0] for key, val in values.items() if val}
    except Exception:
        result = {}
        for pair in (query or "").split("&"):
            if "=" in pair:
                key, _, value = pair.partition("=")
                try:
                    result[key] = unquote(value)
                except Exception:
                    result[key] = value
        return result
