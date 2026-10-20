# -*- coding: utf-8 -*-
"""管理后台（对应上游 functions/admin.js 的核心部分）。

上游后台 1636 行，一半在跟 Cloudflare API 打交道（用量统计、KV 批量读写）。
复刻版落到单机，需要的其实是**把整套运维动作收进一个网页**：

    总览      访问量走势 / 地区分布 / 最近访客 / 一键开关维护
    内容      多公告轮播、单条公告、每日一言、节日、倒计时
    访客      维护模式与白名单、封禁列表、最近访问（可一键封禁）
    AI        额度用量、运行中任务、重置
    系统      服务信息、体检、日志、缓存、模板重新生成与预览
    高级      store.json 原始编辑器、导出/导入、配置开关

页面本体在 :mod:`pclhome.admin_page`，这里只管鉴权、路由和读写。

鉴权：``config.admin_token`` 换一个会话 cookie（默认 8 小时）。后台只应在
本机或内网使用；对外暴露请套一层反代并强制 HTTPS。
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import secrets
import sys
import threading
import time
import urllib.parse
from dataclasses import replace
from pathlib import Path

from . import ai
from .admin_page import ADMIN_PAGE
from .config import CACHE_DIR, CONFIG_FILE, ROOT, VAR_DIR, Config
from .generate import generate
from .log import info, uptime, warn
from .weather import WeatherService

SESSION_COOKIE = "admin_session"
SESSION_TTL = 8 * 3600

# 后台版本号，跟 server.py 的 server_version 保持一致
ADMIN_VERSION = "1.0"

# 日志文件：serve --log-file var/server.log；没开文件日志时后台会提示
LOG_FILE = VAR_DIR / "server.log"

_sessions: dict[str, float] = {}
_sessions_lock = threading.Lock()

# 后台可写的配置项：键名与上游 KV 完全一致
WRITABLE_KEYS = {
    "maint_mode_live", "maint_eta", "maint_reason", "maint_whitelist",
    "block:list", "banners", "homepage_banner", "quote_custom",
    "custom_festivals", "custom_countdown",
}

# 后台能改的 config.json 字段：只放不影响监听地址、密钥和鉴权的项
CONFIG_FIELDS = {
    "site_name": str,
    "enable_weather": bool,
    "enable_wallpaper": bool,
    "enable_saying": bool,
    "enable_lunar": bool,
    "enable_geo": bool,
    "guard_clients": bool,
    "weather_city": str,
}

# 缓存清理目标 → 人话
CACHE_TARGETS = {
    "saying": "每日一言缓存（下一天重新取接口）",
    "lunar": "农历与节气缓存",
    "wallpaper": "横幅壁纸缓存",
    "geo": "IP 归属地内存缓存",
    "weather": "天气内存缓存",
    "all": "以上全部",
}

# 体检结果缓存：跑一次约 3 秒，别每次刷新页面都跑
_doctor_lock = threading.Lock()
_doctor_cache: dict = {"at": 0.0, "report": None}
DOCTOR_TTL = 60


# ============ 会话 ============

def _new_session() -> str:
    token = secrets.token_hex(32)
    now = time.time()
    with _sessions_lock:
        for key, expires in list(_sessions.items()):
            if expires < now:
                _sessions.pop(key, None)
        _sessions[token] = now + SESSION_TTL
    return token


def _valid_session(token: str) -> bool:
    if not token:
        return False
    with _sessions_lock:
        expires = _sessions.get(token, 0)
        if expires < time.time():
            _sessions.pop(token, None)
            return False
    return True


def _session_count() -> int:
    now = time.time()
    with _sessions_lock:
        return sum(1 for expires in _sessions.values() if expires >= now)


def _get_cookie(handler, name: str) -> str:
    header = handler.headers.get("Cookie") or ""
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return urllib.parse.unquote(value)
    return ""


def _authed(service, handler) -> bool:
    return _valid_session(_get_cookie(handler, SESSION_COOKIE))


# ============ 响应工具 ============

def client_hint(handler) -> str:
    """日志里的"谁在操作"：优先反代头，其次 socket 对端。"""
    for header in ("CF-Connecting-IP", "X-Real-IP"):
        value = handler.headers.get(header)
        if value:
            return value.strip()
    forwarded = handler.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return handler.client_address[0] if handler.client_address else "?"


def summarize(value, limit: int = 60) -> str:
    """日志里别把整篇公告刷屏。"""
    text = str(value).replace("\n", " / ")
    return text if len(text) <= limit else text[:limit] + "…"


def _json(payload: dict, status: int = 200, cookie: str | None = None,
          headers: dict | None = None):
    from .server import Response
    extra = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if cookie:
        extra["Set-Cookie"] = cookie
    if headers:
        extra.update(headers)
    return Response(status=status,
                    body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    content_type="application/json; charset=utf-8",
                    headers=extra)


def _html(body: str):
    from .server import Response
    return Response.text(body, "text/html; charset=utf-8",
                         headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _download(name: str, body: str):
    from .server import Response
    return Response.text(body, "application/json; charset=utf-8", headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'attachment; filename="' + name + '"',
    })


def _read_body(handler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return {}
    if length <= 0 or length > 2_000_000:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    if (handler.headers.get("Content-Type") or "").startswith("application/json"):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


# ============ 小工具 ============

def _mask(value: str, keep: int = 4) -> str:
    """密钥只露头尾几位，别让后台自己变成泄露点。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "***"
    return text[:keep] + "***" + text[-keep:]


def _size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return (str(int(value)) if unit == "B" else "%.1f" % value) + " " + unit
        value /= 1024
    return str(size) + " B"


def _mtime_of(path: Path) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
    except OSError:
        return ""


def _valid_ip(text: str) -> str:
    """封禁/白名单只收合法 IP，别把半截域名写进去。"""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return ""


def _service_info(service) -> dict:
    """后台「系统」那块的基本盘。"""
    config: Config = service.config
    store_file = VAR_DIR / "store.json"
    db_file = VAR_DIR / "stats.db"
    return {
        "version": ADMIN_VERSION,
        "pcl_version": _pcl_version(),
        "uptime": uptime(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pid": os.getpid(),
        "listen": config.host + ":" + str(config.port),
        "base_url": config.base_url or "（按请求 Host 推导）",
        "site_name": config.site_name,
        "guard_clients": config.guard_clients,
        "features": {
            "天气": config.enable_weather,
            "壁纸": config.enable_wallpaper,
            "每日一言": config.enable_saying,
            "农历": config.enable_lunar,
            "IP 归属地": config.enable_geo,
            "AI 分析": config.enable_ai,
        },
        "secrets": {
            "admin_token": _mask(config.admin_token, 3),
            "ai_api_key": _mask(config.ai_api_key),
            "uapi_key": _mask(config.uapi_key),
        },
        "ai": {
            "enabled": config.enable_ai,
            "model": config.ai_model,
            "rate": str(config.ai_rate_limit) + " 次 / " + config.ai_window_text(),
            "base": config.ai_api_base,
        },
        "paths": {
            "root": str(ROOT),
            "var": str(VAR_DIR),
            "store": str(store_file),
            "log": str(LOG_FILE),
            "config": str(CONFIG_FILE),
            "store_size": _human_size(_size_of(store_file)),
            "db_size": _human_size(_size_of(db_file)),
            "store_mtime": _mtime_of(store_file),
            "log_size": _human_size(_size_of(LOG_FILE)),
            "log_mtime": _mtime_of(LOG_FILE),
        },
        "sessions": _session_count(),
        "store_keys": sorted(service.store.all().keys()),
    }


def _pcl_version() -> str:
    """PCL 版本号走缓存文件，取不到就算了，别在后台里阻塞。"""
    try:
        from .version import current
        return current()
    except Exception:
        return ""


def _read_log(lines: int = 200) -> dict:
    if not LOG_FILE.exists():
        return {"available": False, "file": str(LOG_FILE), "lines": [],
                "hint": "没有找到日志文件。用 serve --log-file var/server.log 启动才会有。"}
    lines = max(20, min(int(lines or 200), 1000))
    try:
        size = LOG_FILE.stat().st_size
        with LOG_FILE.open("rb") as handle:
            start = max(0, size - 256 * 1024)
            handle.seek(start)
            raw = handle.read()
    except OSError as exc:
        return {"available": False, "file": str(LOG_FILE), "lines": [],
                "hint": "读取日志失败：" + str(exc)}
    text = raw.decode("utf-8", errors="replace")
    if start > 0:
        text = text.split("\n", 1)[-1]                    # 丢掉被截断的第一行
    chunks = text.splitlines()[-lines:]
    return {"available": True, "file": str(LOG_FILE), "lines": chunks,
            "size": _human_size(size), "mtime": _mtime_of(LOG_FILE)}


def _clear_cache(service, target: str) -> list[str]:
    """清缓存。都是"删了会自己重建"的东西，失败也不该 500。"""
    from . import saying as saying_module
    from . import geo as geo_module

    done: list[str] = []
    targets = list(CACHE_TARGETS) if target == "all" else [target]
    for name in targets:
        if name == "all":
            continue
        try:
            if name == "saying":
                saying_module.clear_cache()
                done.append("每日一言")
            elif name == "lunar":
                (VAR_DIR / "lunar.json").unlink(missing_ok=True)
                done.append("农历")
            elif name == "wallpaper":
                removed = 0
                for item in CACHE_DIR.glob("wallpaper*"):
                    item.unlink(missing_ok=True)
                    removed += 1
                done.append("壁纸" + ("（" + str(removed) + " 个文件）" if removed else ""))
            elif name == "geo":
                done.append("IP 归属地（" + str(geo_module.clear_cache()) + " 条）")
            elif name == "weather":
                service.weather.clear_cache()
                done.append("天气")
        except Exception as exc:
            warn("[Admin] 清理缓存 " + name + " 失败：" + str(exc))
    return done


# ============ 路由 ============

def handle(service, handler, path: str, query: str):
    config: Config = service.config
    store = service.store

    if path == "/admin":
        return _html(ADMIN_PAGE)

    if not path.startswith("/admin/api/"):
        return _json({"ok": False, "error": "not_found"}, 404)

    action = path[len("/admin/api/"):]

    # ---- 免鉴权 ----
    if action == "login":
        body = _read_body(handler)
        token = str(body.get("token") or "")
        if not config.admin_token:
            return _json({"ok": False, "error": "admin_disabled"}, 403)
        if not secrets.compare_digest(token, config.admin_token):
            warn("[Admin] 令牌错误，来自 " + client_hint(handler))
            time.sleep(0.5)                       # 拖慢暴力尝试
            return _json({"ok": False, "error": "bad_token"}, 401)
        info("[Admin] 登录成功：" + client_hint(handler))
        session = _new_session()
        cookie = (SESSION_COOKIE + "=" + session + "; Path=/; HttpOnly; SameSite=Strict; Max-Age="
                  + str(SESSION_TTL))
        return _json({"ok": True}, cookie=cookie)

    if action == "logout":
        token = _get_cookie(handler, SESSION_COOKIE)
        with _sessions_lock:
            _sessions.pop(token, None)
        return _json({"ok": True}, cookie=SESSION_COOKIE + "=; Path=/; Max-Age=0")

    if not _authed(service, handler):
        return _json({"ok": False, "error": "unauthorized"}, 401)

    # ---- 需要登录 ----

    if action == "state":
        return _json({
            "ok": True,
            "config": {
                "site_name": config.site_name,
                "base_url": config.base_url,
                "guard_clients": config.guard_clients,
                "enable_weather": config.enable_weather,
                "enable_wallpaper": config.enable_wallpaper,
                "enable_saying": config.enable_saying,
                "enable_lunar": config.enable_lunar,
                "enable_geo": config.enable_geo,
                "enable_ai": config.enable_ai,
                "weather_city": config.weather_city,
                "sites": [{"name": s.get("name", ""), "url": s.get("url", "")}
                          for s in (config.sites or [])],
            },
            "store": store.all(),
            "ai": ai.admin_state(config),
            "info": _service_info(service),
            "stats": service.stats.summary(),
        })

    if action == "stats":
        return _json({"ok": True, "stats": service.stats.summary()})

    if action == "info":
        return _json({"ok": True, "info": _service_info(service)})

    if action == "set":
        body = _read_body(handler)
        key = str(body.get("key") or "")
        if key not in WRITABLE_KEYS:
            return _json({"ok": False, "error": "key_not_writable", "key": key}, 400)
        value = body.get("value")
        if isinstance(value, str):
            value = value.replace("\r\n", "\n")
        store.set(key, value)
        info("[Admin] 修改 " + key + " → " + summarize(value) + "（" + client_hint(handler) + "）")
        return _json({"ok": True, "key": key})

    if action == "set_batch":
        body = _read_body(handler)
        items = body.get("items")
        if not isinstance(items, dict) or not items:
            return _json({"ok": False, "error": "missing_items"}, 400)
        saved = []
        for key, value in items.items():
            if key not in WRITABLE_KEYS:
                return _json({"ok": False, "error": "key_not_writable", "key": key}, 400)
            if isinstance(value, str):
                value = value.replace("\r\n", "\n")
            store.set(key, value)
            saved.append(key)
        info("[Admin] 批量修改 " + "、".join(saved) + "（" + client_hint(handler) + "）")
        return _json({"ok": True, "keys": saved})

    if action == "delete":
        body = _read_body(handler)
        key = str(body.get("key") or "")
        if key not in WRITABLE_KEYS:
            return _json({"ok": False, "error": "key_not_writable"}, 400)
        store.delete(key)
        info("[Admin] 删除配置项 " + key + "（" + client_hint(handler) + "）")
        return _json({"ok": True})

    if action == "block":
        body = _read_body(handler)
        raw = str(body.get("ip") or "").strip()
        ip = _valid_ip(raw)
        if not ip:
            return _json({"ok": False, "error": "bad_ip", "ip": raw}, 400)
        block_list = store.block_list()
        if body.get("remove"):
            block_list.pop(ip, None)
        else:
            block_list[ip] = str(body.get("reason") or "手动封禁")
        store.set("block:list", block_list)
        info("[Admin] " + ("解封 " if body.get("remove") else "封禁 ") + ip
             + ("（" + str(body.get("reason")) + "）" if not body.get("remove") else "")
             + "（" + client_hint(handler) + "）")
        return _json({"ok": True, "block_list": block_list})

    if action == "block_batch":
        body = _read_body(handler)
        raw_list = body.get("ips")
        if isinstance(raw_list, str):
            raw_list = raw_list.replace(",", "\n").split("\n")
        if not isinstance(raw_list, list):
            return _json({"ok": False, "error": "missing_ips"}, 400)
        remove = bool(body.get("remove"))
        reason = str(body.get("reason") or "手动封禁")
        block_list = store.block_list()
        added, skipped = [], []
        for raw in raw_list[:500]:
            ip = _valid_ip(str(raw))
            if not ip:
                if str(raw).strip():
                    skipped.append(str(raw).strip())
                continue
            if remove:
                block_list.pop(ip, None)
            else:
                block_list[ip] = reason
            added.append(ip)
        store.set("block:list", block_list)
        info("[Admin] " + ("批量解封 " if remove else "批量封禁 ") + str(len(added)) + " 个 IP（"
             + client_hint(handler) + "）")
        return _json({"ok": True, "count": len(added), "skipped": skipped,
                      "block_list": block_list})

    if action == "whitelist":
        body = _read_body(handler)
        raw_list = body.get("ips")
        if raw_list is None:
            raw_list = [body.get("ip") or ""]
        if isinstance(raw_list, str):
            raw_list = raw_list.replace(",", "\n").split("\n")
        remove = bool(body.get("remove"))
        whitelist = store.get_json("maint_whitelist", {}) or {}
        added = []
        for raw in raw_list[:500]:
            ip = _valid_ip(str(raw))
            if not ip:
                continue
            if remove:
                whitelist.pop(ip, None)
            else:
                whitelist[ip] = 1
            added.append(ip)
        store.set("maint_whitelist", whitelist)
        return _json({"ok": True, "count": len(added), "whitelist": whitelist})

    if action == "maintenance":
        """一次请求把维护模式三个键写完，省得前端连发三次。"""
        body = _read_body(handler)
        updates = {
            "maint_mode_live": "1" if body.get("on") else "0",
            "maint_eta": str(body.get("eta") or ""),
            "maint_reason": str(body.get("reason") or ""),
        }
        for key, value in updates.items():
            store.set(key, value)
        info("[Admin] 维护模式 " + ("开" if body.get("on") else "关")
             + "（" + client_hint(handler) + "）")
        return _json({"ok": True})

    if action == "ai_reset":
        if not config.enable_ai:
            return _json({"ok": False, "error": "ai_disabled"}, 400)
        body = _read_body(handler)
        scope = str(body.get("scope") or "all")
        if scope not in ("quota", "jobs", "keys", "all"):
            return _json({"ok": False, "error": "bad_scope"}, 400)
        return _json({"ok": True, "scope": scope,
                      "result": ai.reset(scope, str(body.get("ip") or ""),
                                         config.ai_window())})

    if action == "regenerate":
        try:
            generate(config, offline=not config.enable_wallpaper)
        except Exception as exc:
            return _json({"ok": False, "error": str(exc)}, 500)
        service._templates.clear()
        info("[Admin] 已重新生成 XAML（" + client_hint(handler) + "）")
        return _json({"ok": True})

    if action == "preview":
        ip = str(handler.headers.get("X-Forwarded-For") or "127.0.0.1").split(",")[0].strip()
        origin = config.resolved_base_url("http://" + (handler.headers.get("Host") or "localhost"))
        try:
            # homepage() 返回的是 (XAML, HomeData)，后者是给日志用的，别整个塞进 JSON
            body, _data = service.homepage(ip, origin)
        except Exception as exc:
            return _json({"ok": False, "error": str(exc)}, 500)
        return _json({"ok": True, "xaml": body})

    # ---- 系统 ----

    if action == "log":
        params = urllib.parse.parse_qs(query or "")
        try:
            count = int((params.get("lines") or ["200"])[0])
        except ValueError:
            count = 200
        return _json({"ok": True, **_read_log(count)})

    if action == "doctor":
        body = _read_body(handler)
        force = bool(body.get("force"))
        now = time.time()
        cached = _doctor_cache.get("report")
        if cached is not None and not force and now - float(_doctor_cache.get("at") or 0) < DOCTOR_TTL:
            return _json({"ok": True, "report": cached, "cached": True,
                          "at": int(_doctor_cache.get("at") or 0)})
        if not _doctor_lock.acquire(blocking=False):
            return _json({"ok": False, "error": "busy"}, 409)
        try:
            from . import doctor as doctor_module
            started = time.time()
            report = doctor_module.run_checks(config, online=False)
            report["took_ms"] = int((time.time() - started) * 1000)
            _doctor_cache["at"] = time.time()
            _doctor_cache["report"] = report
            info("[Admin] 体检完成：通过 " + str(report.get("ok")) + " / 警告 "
                 + str(report.get("warn")) + " / 失败 " + str(report.get("fail"))
                 + "（" + client_hint(handler) + "）")
            return _json({"ok": True, "report": report, "cached": False, "at": int(time.time())})
        except Exception as exc:
            warn("[Admin] 体检失败：" + str(exc))
            return _json({"ok": False, "error": str(exc)}, 500)
        finally:
            _doctor_lock.release()

    if action == "cache":
        body = _read_body(handler)
        target = str(body.get("target") or "all")
        if target not in CACHE_TARGETS:
            return _json({"ok": False, "error": "bad_target"}, 400)
        cleared = _clear_cache(service, target)
        service._templates.clear()
        info("[Admin] 清理缓存 " + target + "（" + client_hint(handler) + "）")
        return _json({"ok": True, "cleared": cleared})

    if action == "sessions":
        body = _read_body(handler)
        if str(body.get("action") or "") != "revoke_others":
            return _json({"ok": False, "error": "bad_action"}, 400)
        keep = _get_cookie(handler, SESSION_COOKIE)
        with _sessions_lock:
            removed = len(_sessions)
            _sessions.clear()
            if keep:
                _sessions[keep] = time.time() + SESSION_TTL
                removed -= 1
        info("[Admin] 清掉其它后台会话 " + str(removed) + " 个（" + client_hint(handler) + "）")
        return _json({"ok": True, "removed": removed})

    # ---- 数据 / 配置 ----

    if action == "store_get":
        return _json({"ok": True, "store": store.all(),
                      "text": json.dumps(store.all(), ensure_ascii=False, indent=2)})

    if action == "store_set":
        body = _read_body(handler)
        if not isinstance(body, dict) or not body:
            return _json({"ok": False, "error": "empty"}, 400)
        current = store.all()
        backup = VAR_DIR / "store.backup.json"
        try:
            backup.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            warn("[Admin] 备份 store.json 失败：" + str(exc))
        store.replace_all(body)
        info("[Admin] 整体替换 store.json（" + str(len(body)) + " 个键，" + client_hint(handler) + "）")
        return _json({"ok": True, "keys": sorted(body.keys()), "backup": str(backup)})

    if action == "export":
        payload = json.dumps(store.all(), ensure_ascii=False, indent=2)
        name = "pclhome-store-" + time.strftime("%Y%m%d-%H%M%S") + ".json"
        return _download(name, payload)

    if action == "config_set":
        body = _read_body(handler)
        updates = body.get("updates")
        if not isinstance(updates, dict) or not updates:
            return _json({"ok": False, "error": "missing_updates"}, 400)
        clean = {}
        for key, value in updates.items():
            caster = CONFIG_FIELDS.get(key)
            if caster is None:
                return _json({"ok": False, "error": "key_not_writable", "key": key}, 400)
            if caster is bool:
                if isinstance(value, str):
                    value = value.strip().lower() in ("1", "true", "on", "yes")
                clean[key] = bool(value)
            else:
                clean[key] = str(value)[:200]
        raw = {}
        try:
            if CONFIG_FILE.exists():
                raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            return _json({"ok": False, "error": "config_unreadable: " + str(exc)}, 500)
        raw.update(clean)
        try:
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(CONFIG_FILE)
        except OSError as exc:
            return _json({"ok": False, "error": "write_failed: " + str(exc)}, 500)

        # 内存里的配置同步换掉，天气服务持有旧引用，得重建
        new_config = replace(config, **clean)
        service.config = new_config
        try:
            service.weather = WeatherService(new_config)
        except Exception as exc:
            warn("[Admin] 重建天气服务失败：" + str(exc))
        service._templates.clear()
        info("[Admin] 修改 config.json：" + "、".join(clean.keys()) + "（" + client_hint(handler) + "）")
        return _json({"ok": True, "keys": sorted(clean.keys())})

    return _json({"ok": False, "error": "unknown_action"}, 404)
