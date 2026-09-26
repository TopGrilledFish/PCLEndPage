# -*- coding: utf-8 -*-
"""AI 崩溃日志分析。

用户在主页的输入框里粘一个日志链接，PCL 带着这个链接请求 ``/ai.json`` 与
``/ai.xaml``，服务端在后台线程里抓日志、调 AI，结果写回主页卡片。

**为什么输入框只能填链接，不能直接粘日志原文**

PCL 的事件参数是这么拼出来的::

    EventData="{Binding Path=Text,ElementName=ailoginput,
                StringFormat='{}<服务器地址>/ai.json?q={0}'}"

``{0}`` 是输入框原文，**PCL 不做 URL 编码**，直接塞进 URL。崩溃日志里全是换行、
空格和 ``&``，拼出来的请求行是非法的，PCL 那边直接失败——不是服务端能救的。
链接又短又没有空白字符，才塞得进去。单行的短文本仍可用（见 :func:`split_input`），
但正常用法是先把日志传到 mclo.gs，再粘链接。

**两种密钥**

1. 站长内置密钥（``config.ai_api_key``）：每 IP 每天 ``config.ai_daily_limit`` 次，
   额度记在 SQLite 里，按**北京时间**跨天重置。
2. 用户自带密钥：粘一次存下来，该 IP 不再受限。密钥**只存在内存**里，
   进程一重启就没了——不落盘，免得替别人保管密钥。日志里一律打码。

**任务状态**落在 ``var/ai_jobs.json``：额度是持久的，结果也必须持久，
否则服务重启后用户额度用掉了却看不到结果。
"""
from __future__ import annotations

import json
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
from .log import debug, error, out, warn
from .store import STATS_DB
from .xaml import (ICON_AI, ICON_BACK, ICON_HOME, ICON_KEY, ICON_REFRESH,
                   ICON_SAVE, ICON_TRASH, escape_attr, escape_url_attr)

JOBS_FILE = VAR_DIR / "ai_jobs.json"

# 额度按这个时区跨天重置
BEIJING = timezone(timedelta(hours=8))

# 结果保留多久（超过就当过期，卡片不再显示）
JOB_TTL = 7 * 86400

SYSTEM_PROMPT = (
    "你是 Minecraft 崩溃日志分析专家。用户会给你一份崩溃日志或报错日志。"
    "请用简体中文回答，直接给结论，不要客套话。严格按这个结构输出，每部分都很短：\n"
    "【结论】一句话说清崩在哪里。\n"
    "【原因】引用日志里的关键行（异常类名、mod 名、类名）说明为什么。\n"
    "【解决】给出可操作的步骤；能指名具体 mod、具体版本就指名。\n"
    "如果日志信息不足以判断，就直接说还需要看哪部分，不要编造。"
)


# ============ 输入解析 ============

_URL_RE = re.compile(r"^https?://\S+$", re.I)
# 只认 mclo.gs 的网页地址。必须锚定主机名，否则会把自家的
# https://api.mclo.gs/1/raw/<id> 匹配成「mclo.gs/1」而抓错东西。
_MCLOGS_RE = re.compile(r"^https?://(?:www\.)?mclo\.gs/([A-Za-z0-9]+)", re.I)

# 直接粘文本时截断到这个长度：URL 太长 PCL 那边发不出去
MAX_INLINE_CHARS = 1200


def split_input(raw: str) -> tuple[str, str]:
    """把输入框内容分成 ``("url" | "text" | "empty", 值)``。"""
    text = (raw or "").strip()
    if not text:
        return "empty", ""
    if _URL_RE.match(text):
        return "url", text
    return "text", text[:MAX_INLINE_CHARS]


def normalize_log_url(url: str) -> str:
    """mclo.gs 的网页地址换成它的 raw 接口；其它地址原样返回。"""
    match = _MCLOGS_RE.search(url or "")
    if match:
        return "https://api.mclo.gs/1/raw/" + match.group(1)
    return url


# ============ 配额（SQLite，按北京时间跨天）============

class Quota:
    """每个 IP 每天用了几次站长密钥。"""

    def __init__(self, path: Path | None = None):
        self.path = path or STATS_DB
        self._lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        if not self._ready:
            conn.execute("CREATE TABLE IF NOT EXISTS ai_usage ("
                         "ip TEXT NOT NULL, day TEXT NOT NULL, "
                         "used INTEGER NOT NULL DEFAULT 0, ts INTEGER NOT NULL, "
                         "PRIMARY KEY (ip, day))")
            conn.commit()
            self._ready = True
        return conn

    def used(self, ip: str, day: str) -> int:
        try:
            with self._lock:
                conn = self._connect()
                try:
                    row = conn.execute("SELECT used FROM ai_usage WHERE ip=? AND day=?",
                                       (ip, day)).fetchone()
                    return int(row[0]) if row else 0
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 读额度失败：" + str(exc))
            return 0

    def consume(self, ip: str, day: str, limit: int) -> bool:
        """够额度就 +1 返回 True，不够返回 False。整段在事务里，不会超发。"""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute("SELECT used FROM ai_usage WHERE ip=? AND day=?",
                                       (ip, day)).fetchone()
                    used = int(row[0]) if row else 0
                    if used >= limit:
                        conn.rollback()
                        return False
                    now = int(time.time())
                    conn.execute(
                        "INSERT INTO ai_usage (ip, day, used, ts) VALUES (?, ?, 1, ?) "
                        "ON CONFLICT(ip, day) DO UPDATE SET used = used + 1, ts = ?",
                        (ip, day, now, now))
                    conn.commit()
                    return True
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 扣额度失败：" + str(exc))
            return False

    def rows_for(self, day: str) -> list[dict]:
        """某天的用量明细，给后台看。"""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    rows = conn.execute(
                        "SELECT ip, used, ts FROM ai_usage WHERE day=? "
                        "ORDER BY used DESC, ts DESC", (day,)).fetchall()
                    return [{"ip": r[0], "used": int(r[1]), "ts": int(r[2])} for r in rows]
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 读用量明细失败：" + str(exc))
            return []

    def reset(self, day: str | None = None, ip: str | None = None) -> int:
        """删用量记录：带 day 只删那天的，带 ip 只删那个 IP 的。返回删掉的行数。"""
        clauses, params = [], []
        if day:
            clauses.append("day=?")
            params.append(day)
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

    def refund(self, ip: str, day: str) -> None:
        """调用失败时把次数还回去（见 config.ai_refund_on_failure）。"""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("UPDATE ai_usage SET used = used - 1 "
                                 "WHERE ip=? AND day=? AND used > 0", (ip, day))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:
            warn("[AI] 退还额度失败：" + str(exc))


QUOTA = Quota()


def beijing_day(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(),
                                  BEIJING).strftime("%Y-%m-%d")


# ============ 用户自带密钥（只在内存）============

_own_keys: dict[str, dict] = {}
_own_lock = threading.RLock()


def mask_key(key: str) -> str:
    """日志里只留头尾，别把明文密钥写进日志文件。"""
    key = key or ""
    if len(key) <= 10:
        return "***"
    return key[:6] + "***" + key[-4:]


def set_own_key(ip: str, raw: str) -> bool:
    """第一步：存密钥（不动已有的地址/协议设置）。"""
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
    """第二步：存请求地址与协议。

    地址留空 = 用该协议的官方地址（OpenAI → api.openai.com，Anthropic → api.anthropic.com）。
    模型在第三步单独填，这里只取地址——万一有人按老写法填了 ``地址|模型``，
    也只认前半段，免得两个地方都能改模型。
    """
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
    """第三步：存模型名。留空 = 用该协议的默认模型。"""
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


def describe_own_key(ip: str) -> str:
    """给日志与界面用的一句话，带上协议与真实地址（绝不带密钥）。"""
    conf = get_own_key(ip) or {}
    if not conf:
        return "未设置"
    protocol = str(conf.get("protocol") or "openai")
    base = conf.get("base") or (config_default_base(protocol))
    model = conf.get("model") or PROTOCOLS.get(protocol, ("", ""))[1]
    return PROTOCOLS.get(protocol, ("OpenAI 兼容",))[0] + " · " + base + " · " + model


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
    """清掉私人密钥（内存里的）。不传 ip 就全清。"""
    with _own_lock:
        if ip:
            return 1 if _own_keys.pop(ip, None) is not None else 0
        count = len(_own_keys)
        _own_keys.clear()
        return count


def reset_jobs(ip: str | None = None) -> int:
    """清掉分析记录（含结果）。不传 ip 就全清。"""
    with _jobs_lock:
        if ip:
            removed = 1 if _jobs.pop(ip, None) is not None else 0
        else:
            removed = len(_jobs)
            _jobs.clear()
        _save_jobs()
    return removed


# ============ 任务状态 ============

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
                job["text"] = "服务重启，这次分析被中断了。请重新提交。"
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


# ============ 抓日志 ============

def fetch_log(config: Config, url: str) -> tuple[bool, str]:
    """抓日志。返回 ``(是否成功, 文本或错误说明)``。"""
    target = normalize_log_url(url)
    request = urllib.request.Request(target, headers={
        "User-Agent": USER_AGENT, "Accept": "text/plain, */*"})
    try:
        with urllib.request.urlopen(request, timeout=config.http_timeout) as resp:
            raw = resp.read(config.ai_max_log_chars * 4)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, "日志不存在（HTTP 404），链接可能过期了"
        return False, "抓日志失败：HTTP " + str(exc.code)
    except Exception as exc:
        return False, "抓日志失败：" + str(exc)

    text = raw.decode("utf-8", "replace")
    if not text.strip():
        return False, "日志内容是空的"
    return True, text


def trim_log(text: str, limit: int) -> str:
    """超长就掐中间：崩溃原因一般在结尾，mod 列表在开头，两头都比中间有用。"""
    if len(text) <= limit:
        return text
    head = int(limit * 0.25)
    tail = limit - head
    return (text[:head]
            + "\n\n……（中间省略 " + str(len(text) - limit) + " 个字符，不影响判断）……\n\n"
            + text[-tail:])


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


def _http_error(exc: urllib.error.HTTPError) -> tuple[bool, str]:
    body = ""
    try:
        body = exc.read().decode("utf-8", "replace")[:300]
    except Exception:
        pass
    detail = "：" + body if body else ""
    known = {401: "API 密钥无效", 402: "API 余额不足",
             403: "API 拒绝访问（密钥权限或地区限制）",
             429: "AI 接口限流了，过一会儿再试"}
    if exc.code in known:
        return False, known[exc.code] + "（HTTP " + str(exc.code) + "）" + detail
    return False, "AI 接口返回 HTTP " + str(exc.code) + detail


def _too_long(config: Config) -> tuple[bool, str]:
    """推理模型把 token 全花在思考上、没写出答案时的提示。"""
    return False, ("AI 没想完就被长度上限截断了（ai_max_tokens="
                   + str(config.ai_max_tokens) + "）。次数已退还，直接重试即可；"
                   "如果反复出现，把 config.json 里的 ai_max_tokens 调大。")


def _post(url: str, payload: dict, headers: dict, config: Config):
    """POST JSON。返回 ``(数据, 错误信息)``，两者必有一个为 None。"""
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=config.ai_timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as exc:
        return None, _http_error(exc)
    except Exception as exc:
        return None, (False, "连不上 AI 接口：" + str(exc))


def _parse_openai(config: Config, data: dict) -> tuple[bool, str]:
    try:
        choice = data["choices"][0]
        text = str((choice.get("message") or {}).get("content") or "").strip()
        finish = str(choice.get("finish_reason") or "")
    except Exception:
        return False, "看不懂 AI 返回的内容：" + json.dumps(data, ensure_ascii=False)[:280]
    if text:
        return True, text
    # deepseek-flash / v4-pro 都是推理模型：先花 token 想，再写答案。
    # max_tokens 给少了，token 全用在思考上，content 就是空的、finish_reason=length。
    if finish == "length":
        return _too_long(config)
    return False, "AI 返回了空内容（finish_reason=" + (finish or "无") + "）"


def _parse_anthropic(config: Config, data: dict) -> tuple[bool, str]:
    text = "".join(str(block.get("text") or "")
                   for block in (data.get("content") or [])
                   if isinstance(block, dict) and block.get("type") == "text").strip()
    if text:
        return True, text
    if str(data.get("stop_reason") or "") == "max_tokens":
        return _too_long(config)
    return False, "AI 返回了空内容（stop_reason=" + str(data.get("stop_reason") or "无") + "）"


def analyze(config: Config, log_text: str, key_conf: dict) -> tuple[bool, str]:
    """调 AI。返回 ``(是否成功, 结果或错误说明)``。

    ``key_conf`` 里可以带 ``protocol``（openai / anthropic）、``base``、``model``；
    缺哪项就用 config 里的默认值。
    """
    key = str(key_conf.get("key") or config.ai_api_key)
    if not key:
        return False, "没有可用的 API 密钥"
    protocol = str(key_conf.get("protocol") or "openai").lower()
    if protocol not in PROTOCOLS:
        protocol = "openai"
    base = str(key_conf.get("base") or config.ai_api_base)
    model = str(key_conf.get("model") or config.ai_model)
    url = _endpoint(base, protocol)
    user_content = "日志如下：\n\n" + log_text

    if protocol == "anthropic":
        payload = {"model": model, "max_tokens": config.ai_max_tokens,
                   "system": SYSTEM_PROMPT,
                   "messages": [{"role": "user", "content": user_content}]}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        parse = _parse_anthropic
    else:
        payload = {"model": model, "max_tokens": config.ai_max_tokens, "temperature": 0.3,
                   "stream": False,
                   "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": user_content}]}
        headers = {"Authorization": "Bearer " + key}
        parse = _parse_openai

    started = time.perf_counter()
    data, error = _post(url, payload, headers, config)
    if error:
        return error
    elapsed = round((time.perf_counter() - started) * 1000)
    ok, result = parse(config, data)
    if not ok:
        return False, result

    usage = data.get("usage") or {}
    debug("[AI] " + protocol + " 完成（" + str(elapsed) + "ms，输入 "
          + str(usage.get("prompt_tokens") or usage.get("input_tokens") or "?")
          + " tokens，输出 "
          + str(usage.get("completion_tokens") or usage.get("output_tokens") or "?")
          + " tokens）")
    return True, result


# ============ 提交任务 ============

def submit(config: Config, ip: str, raw_input: str, use_own: bool) -> tuple[bool, str]:
    """校验 + 扣额度 + 起后台线程。返回 ``(是否受理, 给用户看的说明)``。"""
    if not config.enable_ai:
        return False, "AI 日志分析没有开启。"

    job = get_job(ip)
    if job and job.get("state") == "running":
        return False, "上一次分析还没结束，等结果出来再提交。"

    kind, value = split_input(raw_input)
    if kind == "empty":
        return False, "输入框是空的。请粘贴日志链接（推荐 mclo.gs），或单行报错文本。"
    display = value if kind == "url" else "（直接粘贴的文本 " + str(len(value)) + " 字）"

    key_conf: dict = {}
    who = "公用密钥" if not use_own else "私人密钥"
    if use_own:
        saved = get_own_key(ip)
        if not saved:
            return False, "还没有保存你的密钥。先在下面「私人密钥设置」里填好密钥并保存。"
        key_conf = dict(saved)
        # 没填地址就用该协议的官方地址，别掉进 config 里那个 DeepSeek 地址
        protocol = str(key_conf.get("protocol") or "openai")
        if not key_conf.get("base"):
            key_conf["base"] = config_default_base(protocol)
    else:
        key_conf = {"key": config.ai_api_key}
        if not key_conf["key"]:
            return False, "站长没有配置公用密钥，请改用「私人密钥」。"

    # 私人密钥不占额度；用公用密钥才扣
    remaining = None
    if not use_own and ip not in (config.ai_free_ips or []):
        if not QUOTA.consume(ip, beijing_day(), config.ai_daily_limit):
            return False, ("今天的 " + str(config.ai_daily_limit) + " 次已经用完了，"
                           "北京时间 0 点重置。\n"
                           "想立刻接着用，可以在下面配一个自己的私人密钥。")
        remaining = max(0, config.ai_daily_limit - QUOTA.used(ip, beijing_day()))

    out("[AI] " + ip + " 提交分析（" + who + "，" + display + "）")
    _set_job(ip, state="running", text="", query=display, who=who,
             remaining=remaining, started=time.time())

    threading.Thread(target=_worker,
                     args=(config, ip, kind, value, key_conf, use_own),
                     name="ai-" + ip, daemon=True).start()
    return True, ("已提交，正在分析。点下面的「确定」刷新主页就能看到结果"
                  "（通常 10～30 秒，失败的次数会退还）。")


def _worker(config: Config, ip: str, kind: str, value: str,
            key_conf: dict, use_own: bool) -> None:
    """后台干活：抓日志 → 调 AI → 写结果。异常都变成给用户看的说明。"""
    try:
        if kind == "url":
            ok, log_text = fetch_log(config, value)
            if not ok:
                _fail(config, ip, use_own, log_text)
                return
        else:
            log_text = value

        log_text = trim_log(log_text, config.ai_max_log_chars)
        debug("[AI] " + ip + " 日志 " + str(len(log_text)) + " 字，开始调 " + config.ai_model)

        ok, result = analyze(config, log_text, key_conf)
        if not ok:
            _fail(config, ip, use_own, result)
            return

        _set_job(ip, state="done", text=result, error="")
        out("[AI] " + ip + " 分析完成，结果 " + str(len(result)) + " 字")
    except Exception as exc:
        error("[AI] 任务异常：" + repr(exc))
        _fail(config, ip, use_own, "分析过程出错了：" + str(exc))


def _fail(config: Config, ip: str, use_own: bool, message: str) -> None:
    """失败：把额度还回去（可配置），并把原因写给用户看。"""
    if not use_own and config.ai_refund_on_failure and ip not in (config.ai_free_ips or []):
        QUOTA.refund(ip, beijing_day())
        message += "\n（今天的次数已经退还，可以直接重试）"
    warn("[AI] " + ip + " 分析失败：" + message.splitlines()[0][:160])
    _set_job(ip, state="error", text=message, error=message)


# ============ 页面构建 ============

def _attr(value) -> str:
    """属性值转义；换行写成字符引用，否则 XAML 会把属性值截断在第一个换行。"""
    return escape_attr(value).replace("\n", "&#xA;").replace("\r", "")


def _url_attr(value: str) -> str:
    """URL 用的属性转义（转发到 xaml.escape_url_attr，保留名字方便阅读）。"""
    return escape_url_attr(value)


def _bind(element: str, base: str, endpoint: str) -> str:
    """生成 ``EventData``：把某个输入框的原文拼到接口地址后面。

    ``{}`` 是 WPF StringFormat 的转义前缀（告诉它后面的大括号是字面量），
    ``{0}`` 才是绑定值。PCL 会把输入框原文原样替换进去，不做 URL 编码，
    所以输入框里只能是链接这种不含空白字符的内容。
    """
    fmt = "{}" + base + endpoint + "?q={0}"
    return "{Binding Path=Text,ElementName=" + element + ",StringFormat='" + fmt + "'}"


def _status_line(config: Config, ip: str) -> str:
    """页面上那行「现在用的是哪个密钥、还剩几次」。"""
    own = get_own_key(ip)
    if own:
        return "现在用私人密钥 · " + describe_own_key(ip)
    if ip in (config.ai_free_ips or []):
        return "现在用公用密钥（这台机器不计数）"
    left = max(0, config.ai_daily_limit - QUOTA.used(ip, beijing_day()))
    if left <= 0:
        return "公用密钥今天的次数用完了，北京时间 0 点恢复"
    return "公用密钥，今天还能用 " + str(left) + " 次"


# ============ 页面零件 ============

def _input_row(name: str, hint: str, height: int = 38, margin: str = "0,8,0,0") -> str:
    """一个带底色的输入框。"""
    return ('<Border Margin="' + margin + '" Height="' + str(height)
            + '" Background="{DynamicResource ColorBrush7}" CornerRadius="5">'
            '<local:MyTextBox x:Name="' + name + '" Height="' + str(height)
            + '" Margin="10,0" HintText="' + _attr(hint) + '" '
            'Foreground="{DynamicResource ColorBrush2}" VerticalAlignment="Center" /></Border>')


def _help_button(text: str, logo: str, url: str, height: int = 38, column=None,
                 margin: str = "", color: str = "Highlight") -> str:
    """走「打开帮助」的按钮——PCL 会去拉 ``url`` 对应的 .json/.xaml 并翻开新页。"""
    attrs = ""
    if column is not None:
        attrs += ' Grid.Column="' + str(column) + '"'
    if margin:
        attrs += ' Margin="' + margin + '"'
    if color:
        attrs += ' ColorType="' + color + '"'
    return ('<local:MyIconTextButton' + attrs + ' Height="' + str(height) + '" Text="'
            + escape_attr(text) + '" LogoScale="0.8" Logo="' + logo
            + '" EventType="打开帮助" EventData="' + _url_attr(url) + '" />')


def _grid2(left: str, right: str, margin: str = "0,8,0,0") -> str:
    """左右等宽两栏，各自放一个已经带好 Grid.Column 的按钮。"""
    return ('<Grid Margin="' + margin + '"><Grid.ColumnDefinitions>'
            '<ColumnDefinition Width="1*" /><ColumnDefinition Width="1*" />'
            "</Grid.ColumnDefinitions>" + left + right + "</Grid>")


def _divider(margin: str = "0,20,0,16") -> str:
    """中间那道渐隐的横线。"""
    return ('<Border Height="1" Margin="' + margin + '"><Border.Background>'
            '<LinearGradientBrush StartPoint="0,0" EndPoint="1,0">'
            '<GradientStop Color="#00000000" Offset="0" />'
            '<GradientStop Color="#33808080" Offset="0.5" />'
            '<GradientStop Color="#00000000" Offset="1" />'
            "</LinearGradientBrush></Border.Background></Border>")


def _heading(text: str, margin: str = "0,16,0,0") -> str:
    return ('<TextBlock Text="' + escape_attr(text) + '" FontSize="13" FontWeight="Bold" '
            'Foreground="{DynamicResource ColorBrush1}" Margin="' + margin + '" />')


def _note(text: str, margin: str = "0,8,0,0") -> str:
    return ('<TextBlock Text="' + _attr(text) + '" FontSize="11" TextWrapping="Wrap" '
            'Margin="' + margin + '" Foreground="{DynamicResource ColorBrush3}" />')


def _nav_row(base: str, refresh_url: str = "", extra_text: str = "",
             extra_url: str = "") -> str:
    """页面底部那排：刷新结果 / 回到某页 / 返回主页。

    「返回主页」是专门解决「进得太深、左上角要按好几下」的：它直接再翻开一份
    主页（见 handle 里的 ``home`` 动作），一下就到，不用沿路往回退。
    """
    items = []
    if refresh_url:
        items.append(("刷新结果", ICON_REFRESH, refresh_url))
    if extra_url:
        items.append((extra_text, ICON_BACK, extra_url))
    items.append(("返回主页", ICON_HOME, base + "/home.json"))
    cells = [_help_button(text, logo, url, 36, margin=("8,0,0,0" if index else ""))
             for index, (text, logo, url) in enumerate(items)]
    return ('<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" '
            'Margin="0,18,0,0">' + "".join(cells) + "</StackPanel>")


def _result_block(config: Config, job: dict | None) -> str:
    """结果区：正在分析 / 分析结果 / 分析失败，外加底部的来源说明。"""
    if not job:
        return ""
    state = job.get("state")
    if state == "running":
        return ('<local:MyHint Theme="Blue" Margin="0,14,0,0" Text="正在分析「'
                + _attr(str(job.get("query") or ""))
                + '」，大概十几秒。完了点「刷新结果」。" />')
    if state not in ("done", "error"):
        return ""

    done = state == "done"
    # 页脚文案：公用密钥点名模型，私人密钥不点（各家模型不一样）
    if str(job.get("who") or "") == "私人密钥":
        footer = "内容由 AI 生成，仅供参考"
    else:
        footer = "内容由 " + str(config.ai_display_name) + " 生成，仅供参考"

    return (
        '<StackPanel Margin="0,14,0,0">'
        '<TextBlock Text="' + ("分析结果" if done else "分析失败") + '" FontSize="14" '
        'FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" Margin="0,0,0,8" />'
        '<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14">'
        '<TextBlock Text="' + _attr(str(job.get("text") or "")) + '" FontSize="13" '
        'LineHeight="24" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush1}" />'
        "</Border>"
        '<TextBlock Text="' + _attr(footer) + '" FontSize="11" Margin="0,10,0,0" '
        'HorizontalAlignment="Right" Foreground="{DynamicResource ColorBrush3}" />'
        "</StackPanel>")


def build_page(config: Config, ip: str, base_url: str) -> str:
    """「AI 日志分析」主页。入口是欢迎卡片那排按钮里的第 4 个。

    这一页只管公用密钥那条线：粘链接、点按钮、看结果。私人密钥的设置和用法
    都挪到了 :func:`build_own_page`，免得正事被一长串设置项埋掉。
    """
    if not config.enable_ai:
        return build_popup("AI 日志分析", "站长没开这个功能。", "Yellow")

    base = _url_attr(base_url)
    job = get_job(ip)

    return (
        '<local:MyCard Title="AI 日志分析" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'

        '<TextBlock Text="MC崩溃？AI智能分析" FontSize="24" FontWeight="Bold" '
        'Foreground="#FF000000" HorizontalAlignment="Center" />'
        '<TextBlock TextWrapping="Wrap" FontSize="12" LineHeight="20" Margin="0,10,0,0" '
        'Foreground="{DynamicResource ColorBrush3}" Text="把崩溃日志传到 mclo.gs，链接粘到'
        '下面就行。日志会发到 AI 接口，里面有隐私的话先删掉再传。" />'

        + _input_row("ailoginput", "粘贴日志链接，比如 https://mclo.gs/xxxxxxx", 40, "0,14,0,0")

        + _grid2(
            _help_button("公用密钥", ICON_AI, _bind("ailoginput", base, "/ai.json"),
                         46, column=0, margin="0,0,5,0"),
            _help_button("私人密钥", ICON_KEY, base + "/ai_own_page.json",
                         46, column=1, margin="5,0,0,0"),
            "0,10,0,0")

        + _note("公用密钥是站长的，每天有次数限制。想用自己那份、或者公用的次数用完了，"
                "点右边的「私人密钥」去填。")

        + _divider("0,20,0,12")

        + '<TextBlock Text="' + _attr(_status_line(config, ip)) + '" FontSize="12" '
        'FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" />'

        + _result_block(config, job)

        + _nav_row(base, refresh_url=base + "/ai_page.json")

        + "</StackPanel>"
        "</local:MyCard>")


def build_own_page(config: Config, ip: str, base_url: str) -> str:
    """私人密钥页：填自己的密钥、请求地址、模型，然后就在这一页发起分析。"""
    if not config.enable_ai:
        return build_popup("AI 日志分析", "站长没开这个功能。", "Yellow")

    base = _url_attr(base_url)
    job = get_job(ip)
    own = get_own_key(ip)

    protocol = str((own or {}).get("protocol") or "openai")
    saved_base = str((own or {}).get("base") or "")
    saved_model = str((own or {}).get("model") or "")
    key_hint = ("已存 " + mask_key(str(own.get("key") or "")) + "，重填会覆盖"
                if own else "sk- 开头那一串")
    base_hint = (saved_base + "（留空改回官方地址）" if saved_base
                 else "只填地址，例如 https://api.deepseek.com/v1")
    model_hint = (saved_model + "（留空保存恢复默认）" if saved_model
                  else "例如 " + PROTOCOLS.get(protocol, ("", ""))[1])

    return (
        '<local:MyCard Title="私人密钥" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'

        '<TextBlock Text="用自己的密钥分析" FontSize="24" FontWeight="Bold" '
        'Foreground="#FF000000" HorizontalAlignment="Center" />'
        '<TextBlock TextWrapping="Wrap" FontSize="12" LineHeight="20" Margin="0,10,0,0" '
        'Foreground="{DynamicResource ColorBrush3}" Text="密钥只放在服务器内存里，不写进文件，'
        '服务重启后要重填；显示和日志里都只留打码后的样子。填过一次就能一直用。" />'

        + _heading("日志链接", "0,18,0,0")
        + _input_row("ailoginput", "粘贴日志链接，比如 https://mclo.gs/xxxxxxx")

        + _heading("API 密钥")
        + _input_row("aikeyinput", key_hint)
        + _grid2(
            _help_button("保存密钥", ICON_SAVE, _bind("aikeyinput", base, "/ai_key.json"),
                         38, column=0, margin="0,0,5,0"),
            _help_button("清除设置", ICON_TRASH, base + "/ai_clear.json",
                         38, column=1, margin="5,0,0,0", color=""))

        + _heading("请求地址")
        + _input_row("aibaseinput", base_hint)
        + _grid2(
            _help_button("OpenAI 接口", ICON_SAVE,
                         _bind("aibaseinput", base, "/ai_base_openai.json"),
                         38, column=0, margin="0,0,5,0"),
            _help_button("Anthropic 接口", ICON_SAVE,
                         _bind("aibaseinput", base, "/ai_base_anthropic.json"),
                         38, column=1, margin="5,0,0,0"))
        + _note("这一格只填地址，模型在下面单独填。")

        + _heading("模型名")
        + _input_row("aimodelinput", model_hint)
        + _help_button("保存模型", ICON_SAVE, _bind("aimodelinput", base, "/ai_model.json"),
                       38, margin="0,8,0,0")
        + _note("留空保存就回到默认：OpenAI 接口用 " + PROTOCOLS["openai"][1]
                + "，Anthropic 接口用 " + PROTOCOLS["anthropic"][1] + "。")

        + _divider("0,22,0,12")

        + _help_button("开始分析", ICON_AI, _bind("ailoginput", base, "/ai_own.json"), 46)

        + '<TextBlock Text="' + _attr(_status_line(config, ip)) + '" FontSize="12" '
        'FontWeight="Bold" Margin="0,12,0,0" Foreground="{DynamicResource ColorBrush1}" />'

        + _result_block(config, job)

        + _nav_row(base, refresh_url=base + "/ai_own_page.json",
                   extra_text="回到分析页", extra_url=base + "/ai_page.json")

        + "</StackPanel>"
        "</local:MyCard>")


def admin_state(config: Config) -> dict:
    """后台「AI 日志分析」那块要显示的东西。"""
    if not config.enable_ai:
        return {"enabled": False}
    day = beijing_day()
    rows = QUOTA.rows_for(day)
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
        "daily_limit": config.ai_daily_limit,
        "day": day,
        "today_rows": rows,
        "today_calls": sum(r["used"] for r in rows),
        "jobs": jobs,
        "running": running,
        "own_keys": keys,
    }


def reset(scope: str = "all", ip: str = "") -> dict:
    """后台重置。scope 取 quota / jobs / keys / all；给了 ip 就只动这个 IP。

    不传 ip 时，额度只清**今天**的（历史留着好看趋势）；传了 ip 就把那个 IP 清干净。
    """
    ip = (ip or "").strip() or None
    done = {}
    if scope in ("quota", "all"):
        done["quota"] = QUOTA.reset(None if ip else beijing_day(), ip)
    if scope in ("jobs", "all"):
        done["jobs"] = reset_jobs(ip)
    if scope in ("keys", "all"):
        done["keys"] = reset_keys(ip)
    out("[AI] 后台重置 " + scope + ("（" + str(ip) + "）" if ip else "（全部）")
        + "：" + json.dumps(done, ensure_ascii=False))
    return done


def build_popup(title: str, message: str, theme: str = "Blue", back: str = "",
                home: str = "") -> str:
    """提示弹窗。

    「继续」按钮重新打开对应页面（``打开帮助``）：PCL 会重新下载那对 .json/.xaml，
    于是显示的是最新状态（正在分析 / 结果）。用 ``刷新页面`` 只会把当前这个弹窗
    自己重画一遍，看不到任何新东西。

    再配一个「返回主页」：这些页是一层层叠上去的，点几下就深了，
    有这个按钮就能一步回去，不用沿路把左上角按穿。
    """
    cells = []
    if home:
        cells.append('<local:MyButton Height="35" Width="110" Text="返回主页" '
                     'EventType="打开帮助" EventData="' + _url_attr(home) + '" />')
    if back:
        cells.append('<local:MyButton Height="35" Width="110" Text="继续" ColorType="Highlight" '
                     'EventType="打开帮助" EventData="' + _url_attr(back) + '" />')
    else:
        cells.append('<local:MyButton Height="35" Width="110" Text="确定" ColorType="Highlight" '
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

# EventData 指向的 .json（PCL 先读它拿页面标题），以及同名的 .xaml（PCL 接着读它当内容）
_META = {
    "/ai_page.json": ("AI 日志分析", "MC崩溃？让 AI 帮你找原因"),
    "/ai_own_page.json": ("私人密钥", "用自己的密钥分析，不限次数"),
    "/home.json": ("返回主页", "回到 PCL 主页"),
    "/ai.json": ("AI 日志分析", "用站长的公用密钥分析"),
    "/ai_own.json": ("AI 日志分析", "用你自己的私人密钥分析"),
    "/ai_key.json": ("保存私人密钥", "密钥只存在服务器内存里"),
    "/ai_base_openai.json": ("存为 OpenAI 接口", "地址留空则用 api.openai.com"),
    "/ai_base_anthropic.json": ("存为 Anthropic 接口", "地址留空则用 api.anthropic.com"),
    "/ai_model.json": ("保存模型", "模型名，留空则用该协议的默认值"),
    "/ai_clear.json": ("清除私人密钥", "清除后回到公用密钥模式"),
}
_DO = {"/ai_page.xaml": "page", "/ai_own_page.xaml": "own_page", "/home.xaml": "home",
       "/ai.xaml": "builtin", "/ai_own.xaml": "own",
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


def handle(service, path: str, query: str, ip: str, origin: str = ""):
    """处理 ``/ai*`` 请求；不是我们的路径就返回 None。"""
    if path in _META:
        title, desc = _META[path]
        return _json_response(popup_json(title, desc))

    action = _DO.get(path)
    if not action:
        return None

    config = service.config
    base = config.resolved_base_url(origin)
    params = parse_query(query)
    page_url = base + "/ai_page.json"
    own_url = base + "/ai_own_page.json"
    home_url = base + "/home.json"

    if action == "page":
        return _xaml_response(build_page(config, ip, base))

    if action == "own_page":
        return _xaml_response(build_own_page(config, ip, base))

    if action == "home":
        # 这些页面是一层层叠着开的，想回主页就得按好几下左上角。
        # 这里直接再给一份主页，点一下就到。
        try:
            body, _data = service.homepage(ip, origin)
            return _xaml_response(body)
        except Exception as exc:
            warn("[AI] 返回主页时组装失败：" + repr(exc))
            return _xaml_response(build_popup(
                "主页没打开", "这会儿组装不出来，稍后再点一次。", "Yellow",
                back=page_url, home=home_url))

    if action == "savekey":
        if set_own_key(ip, params.get("q", "")):
            return _xaml_response(build_popup(
                "密钥存好了",
                "再把下面的请求地址和模型填上，就能点「开始分析」了。\n\n"
                "密钥只在服务器内存里，服务重启后得重填。",
                back=own_url, home=home_url))
        return _xaml_response(build_popup(
            "还没填密钥", "输入框是空的。把 sk- 开头那串粘进去再点一次。",
            "Yellow", back=own_url, home=home_url))

    if action in ("base_openai", "base_anthropic"):
        protocol = "openai" if action == "base_openai" else "anthropic"
        if not get_own_key(ip):
            return _xaml_response(build_popup(
                "先填密钥", "得先把上面的 API 密钥保存了，才能选接口。",
                "Yellow", back=own_url, home=home_url))
        set_own_base(ip, params.get("q", ""), protocol)
        return _xaml_response(build_popup(
            "记下了：" + PROTOCOLS[protocol][0] + " 接口",
            "现在的设置是 " + describe_own_key(ip) + "。",
            back=own_url, home=home_url))

    if action == "savemodel":
        if not get_own_key(ip):
            return _xaml_response(build_popup(
                "先填密钥", "得先把上面的 API 密钥保存了，才能存模型。",
                "Yellow", back=own_url, home=home_url))
        set_own_model(ip, params.get("q", ""))
        return _xaml_response(build_popup(
            "模型换好了",
            "现在的设置是 " + describe_own_key(ip) + "。\n\n"
            "留空再存一次就回到该协议的默认模型。",
            back=own_url, home=home_url))

    if action == "clearkey":
        had = clear_own_key(ip)
        return _xaml_response(build_popup(
            "清掉了" if had else "本来就没填过",
            "已经回到公用密钥。" if had else "这个 IP 名下没存过私人密钥。",
            back=own_url, home=home_url))

    ok, message = submit(config, ip, params.get("q", ""), use_own=(action == "own"))
    return _xaml_response(build_popup(
        "开始分析了" if ok else "没能开始", message,
        "Blue" if ok else "Yellow",
        back=own_url if action == "own" else page_url, home=home_url))


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
