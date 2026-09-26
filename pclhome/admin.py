# -*- coding: utf-8 -*-
"""管理后台（对应上游 functions/admin.js 的核心部分）。

上游后台 1636 行，一半在跟 Cloudflare API 打交道（用量统计、KV 批量读写）。
复刻版落到单机，只需要保留真正有用的运维能力：

    维护模式 / 维护白名单        临时把主页切成"服务器正在更新"，自己仍可访问
    封禁列表                    按 IP 拒绝访问
    公告                        多公告轮播（≥2 条）与单条公告（跑马灯）
    每日一言                    自定义文案，支持 {date}/{weekday}/{year}
    节日与倒计时                自定义公历节日、自定义倒计时目标
    访问统计                    7 天总量 / 今日 / 独立 IP / 地区分布
    模板重新生成                改完模板或服务器列表后一键重跑 generate

鉴权：``config.admin_token`` 换一个会话 cookie（默认 8 小时）。后台只应在
本机或内网使用；对外暴露请套一层反代并强制 HTTPS。
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.parse

from . import ai
from .log import out, warn
from .config import Config
from .generate import generate
from .log import info, warn

SESSION_COOKIE = "admin_session"
SESSION_TTL = 8 * 3600

_sessions: dict[str, float] = {}
_sessions_lock = threading.Lock()

# 后台可写的配置项：键名与上游 KV 完全一致
WRITABLE_KEYS = {
    "maint_mode_live", "maint_eta", "maint_reason", "maint_whitelist",
    "block:list", "banners", "homepage_banner", "quote_custom",
    "custom_festivals", "custom_countdown",
}


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


def _json(payload: dict, status: int = 200, cookie: str | None = None):
    from .server import Response
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if cookie:
        headers["Set-Cookie"] = cookie
    return Response(status=status,
                    body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    content_type="application/json; charset=utf-8",
                    headers=headers)


def _html(body: str):
    from .server import Response
    return Response.text(body, "text/html; charset=utf-8",
                         headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _read_body(handler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return {}
    if length <= 0 or length > 1_000_000:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    if (handler.headers.get("Content-Type") or "").startswith("application/json"):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


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
            },
            "store": store.all(),
            "ai": ai.admin_state(config),
        })

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

    if action == "stats":
        return _json({"ok": True, "stats": service.stats.summary()})

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
        ip = str(body.get("ip") or "").strip()
        if not ip:
            return _json({"ok": False, "error": "missing_ip"}, 400)
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

    if action == "whitelist":
        body = _read_body(handler)
        ip = str(body.get("ip") or "").strip()
        if not ip:
            return _json({"ok": False, "error": "missing_ip"}, 400)
        whitelist = store.get_json("maint_whitelist", {}) or {}
        if body.get("remove"):
            whitelist.pop(ip, None)
        else:
            whitelist[ip] = 1
        store.set("maint_whitelist", whitelist)
        return _json({"ok": True, "whitelist": whitelist})

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
            body = service.homepage(ip, origin)
        except Exception as exc:
            return _json({"ok": False, "error": str(exc)}, 500)
        return _json({"ok": True, "xaml": body})

    return _json({"ok": False, "error": "unknown_action"}, 404)


# ============ 后台页面 ============

ADMIN_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>主页管理后台</title>
<style>
  :root{
    --bg:#0b0d10; --card:#12161b; --card2:#171c23; --line:#232a33;
    --ink:#eef1f5; --ink2:#98a3b2; --ink3:#5e6a7a;
    --accent:#4d8dff; --accent-ink:#04101f;
    --ok:#3fb950; --warn:#dfa02f; --danger:#e5544d;
    --r:12px; --r2:9px;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
    -webkit-font-smoothing:antialiased}
  a{color:var(--accent);text-decoration:none}
  a:hover{text-decoration:underline}
  .ic{fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}

  header{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:12px;
    padding:0 22px;height:58px;background:rgba(11,13,16,.86);
    backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
  .brand{display:flex;align-items:center;gap:9px;font-weight:600;font-size:14.5px}
  .mark{width:23px;height:23px;border-radius:7px;flex:none;display:grid;place-items:center;
    background:linear-gradient(135deg,#4d8dff,#7c5cff)}
  .mark svg{width:13px;height:13px}
  header .sp{flex:1}

  .card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;
    box-shadow:0 1px 2px rgba(0,0,0,.28)}
  .card > h2{margin:0;padding:13px 17px;font-size:13.5px;font-weight:600;
    border-bottom:1px solid var(--line);background:var(--card2);display:flex;align-items:center;gap:9px}
  .card > h2 small{color:var(--ink3);font-weight:400;font-size:12px}
  .body{padding:17px;display:grid;gap:15px}
  label{display:block;font-size:12px;color:var(--ink2);margin-bottom:6px;font-weight:500}
  input[type=text],input[type=password],textarea,select{
    width:100%;background:#0d1015;border:1px solid var(--line);border-radius:var(--r2);color:var(--ink);
    padding:9px 11px;font:13.5px/1.6 inherit;outline:none;transition:border-color .15s,box-shadow .15s}
  input:focus,textarea:focus,select:focus{border-color:var(--accent);
    box-shadow:0 0 0 3px rgba(77,141,255,.16)}
  input::placeholder,textarea::placeholder{color:#4a5563}
  textarea{min-height:96px;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
  .row{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}

  button{background:var(--accent);color:var(--accent-ink);border:0;border-radius:var(--r2);
    padding:9px 15px;font:600 13.5px/1 inherit;cursor:pointer;transition:filter .15s,background .15s}
  button:hover{filter:brightness(1.08)}
  button.ghost{background:#1a2029;color:var(--ink);border:1px solid var(--line)}
  button.ghost:hover{background:#20272f;filter:none}
  button.danger{background:var(--danger);color:#fff}
  button:disabled{opacity:.45;cursor:not-allowed;filter:none}
  .btns{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
  .hint{font-size:12px;color:var(--ink3)}
  .hint b{color:var(--ink2)}

  .pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
    font-size:11px;font-weight:600}
  .pill::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
  .pill.on{background:rgba(63,185,80,.14);color:var(--ok)}
  .pill.off{background:rgba(152,163,178,.12);color:var(--ink2)}

  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line)}
  tr:last-child td{border-bottom:0}
  th{color:var(--ink3);font-weight:500;font-size:11.5px;letter-spacing:.3px}
  td .ghost{padding:5px 10px;font-size:12px}

  .kpi{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}
  .kpi div{background:var(--card2);border:1px solid var(--line);border-radius:var(--r);padding:15px 16px}
  .kpi span{font-size:12px;color:var(--ink3)}
  .kpi b{display:block;font-size:26px;font-weight:650;margin-top:5px;letter-spacing:-.5px}

  /* 侧栏导航 + 主区 */
  .shell{display:grid;grid-template-columns:200px minmax(0,1fr);gap:24px;
    max-width:1180px;margin:0 auto;padding:22px}
  .side{position:sticky;top:80px;align-self:start}
  .side .cap{font-size:11px;font-weight:600;letter-spacing:.7px;text-transform:uppercase;
    color:var(--ink3);padding:0 11px 8px}
  #nav{display:grid;gap:3px}
  #nav button{display:flex;align-items:center;gap:9px;text-align:left;width:100%;
    background:transparent;color:var(--ink2);border:0;border-radius:var(--r2);
    padding:9px 11px;font:500 13.5px/1 inherit;cursor:pointer}
  #nav button:hover{background:#161b22;color:var(--ink);filter:none}
  #nav button.on{background:rgba(77,141,255,.13);color:#a9c8ff;font-weight:600}
  #nav .ic{width:15px;height:15px;flex:none;opacity:.85}
  .main{display:grid;gap:16px;min-width:0}

  .loginwrap{max-width:400px;margin:13vh auto;padding:0 22px}
  .loginwrap .body{padding:22px;gap:16px}
  .logo-big{width:44px;height:44px;border-radius:13px;margin:0 auto;
    background:linear-gradient(135deg,#4d8dff,#7c5cff);display:grid;place-items:center}
  .logo-big svg{width:22px;height:22px}

  .toast{position:fixed;right:20px;bottom:20px;z-index:50;background:var(--card2);
    border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;
    padding:11px 16px;font-size:13px;box-shadow:0 8px 28px rgba(0,0,0,.45);max-width:70vw;
    opacity:0;transform:translateY(10px);transition:.22s;pointer-events:none}
  .toast.show{opacity:1;transform:none}
  .hidden{display:none!important}

  @media (max-width:820px){
    .shell{grid-template-columns:1fr;gap:14px;padding:16px}
    .side{position:static}
    .side .cap{display:none}
    #nav{display:flex;overflow-x:auto;gap:7px;padding-bottom:2px;scrollbar-width:none}
    #nav::-webkit-scrollbar{display:none}
    #nav button{width:auto;white-space:nowrap;border:1px solid var(--line);background:#12161b}
    #nav button.on{border-color:transparent}
    header{padding:0 16px}
    .brand span{display:none}
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="mark"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4"
      stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/></svg></span>
    <span>主页管理后台</span>
  </div>
  <span id="status" class="pill off">未登录</span>
  <span class="sp"></span>
  <a href="/Custom.xaml" target="_blank" style="font-size:13px">查看主页</a>
  <button class="ghost hidden" id="logout">退出</button>
</header>

<div id="login" class="loginwrap">
  <div class="card">
    <div class="body">
      <div class="logo-big"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4"
        stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/></svg></div>
      <div style="text-align:center">
        <div style="font-size:16px;font-weight:600">主页管理后台</div>
        <div class="hint" style="margin-top:3px">输入管理令牌继续</div>
      </div>
      <div><label>管理令牌</label>
        <input type="password" id="token" placeholder="config.admin_token" autocomplete="current-password"></div>
      <div class="btns"><button id="doLogin" style="width:100%">登录</button></div>
    </div>
  </div>
</div>

<div class="shell hidden" id="panel">
  <aside class="side">
    <div class="cap">管理</div>
    <nav id="nav">
      <button data-sec="overview" class="on"><svg class="ic" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>总览</button>
      <button data-sec="content"><svg class="ic" viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>首页内容</button>
      <button data-sec="access"><svg class="ic" viewBox="0 0 24 24"><path d="M12 3l7 3v6c0 4.4-2.9 7.9-7 9-4.1-1.1-7-4.6-7-9V6z"/></svg>访客与维护</button>
      <button data-sec="ai"><svg class="ic" viewBox="0 0 24 24"><path d="M12 3l2.2 6.3L20.5 12l-6.3 2.7L12 21l-2.2-6.3L3.5 12l6.3-2.7z"/></svg>AI 分析</button>
      <button data-sec="system"><svg class="ic" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/><circle cx="9" cy="7" r="2.2"/><circle cx="15" cy="12" r="2.2"/><circle cx="8" cy="17" r="2.2"/></svg>系统</button>
    </nav>
  </aside>
  <main class="main" id="main"></main>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = (s) => document.querySelector(s);
const state = {};

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 2200);
}

async function api(action, data, method) {
  const opt = { method: method || (data ? 'POST' : 'GET'), headers: {} };
  if (data) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(data); }
  const res = await fetch('/admin/api/' + action, opt);
  const json = await res.json().catch(() => ({ ok: false, error: 'bad_json' }));
  if (res.status === 401) { showLogin(); throw new Error('unauthorized'); }
  if (!json.ok) throw new Error(json.error || 'failed');
  return json;
}

function showLogin() {
  $('#login').classList.remove('hidden');
  $('#panel').classList.add('hidden');
  $('#status').textContent = '未登录';
  $('#status').className = 'pill off';
}

function showPanel() {
  $('#login').classList.add('hidden');
  $('#panel').classList.remove('hidden');
  $('#logout').classList.remove('hidden');
  $('#status').textContent = '已登录';
  $('#status').className = 'pill on';
}

// 侧栏分区：卡片按 data-sec 归组，一次只显示一组，省得一屏堆九张卡
let curSec = 'overview';

function showSec(sec) {
  curSec = sec;
  document.querySelectorAll('#main .card').forEach(c => {
    c.classList.toggle('hidden', c.dataset.sec !== sec);
  });
  document.querySelectorAll('#nav button').forEach(b => {
    b.classList.toggle('on', b.dataset.sec === sec);
  });
}

document.querySelectorAll('#nav button').forEach(b => {
  b.onclick = () => showSec(b.dataset.sec);
});

async function load() {
  const s = await api('state');
  state.store = s.store || {};
  state.config = s.config || {};
  state.ai = s.ai || {};
  render();
  loadStats();
  showPanel();
}

function jsonOf(key, fallback) {
  const raw = state.store[key];
  if (raw === undefined || raw === null || raw === '') return fallback;
  if (typeof raw === 'object') return raw;
  try { return JSON.parse(raw); } catch (e) { return fallback; }
}

function textOf(key) {
  const raw = state.store[key];
  return raw === undefined || raw === null ? '' : String(raw);
}

function render() {
  const maint = ['1', 'true', 'on'].includes(textOf('maint_mode_live'));
  const banners = jsonOf('homepage_banner', {});
  const blocks = jsonOf('block:list', {});
  const wl = jsonOf('maint_whitelist', {});
  const cd = jsonOf('custom_countdown', {});
  const fests = jsonOf('custom_festivals', []);

  $('#main').innerHTML = `
  <div class="card" data-sec="overview"><h2>KPI <small>近 7 天</small></h2><div class="body"><div class="kpi" id="kpi"></div></div></div>

  <div class="card" data-sec="overview"><h2>访问统计 <small>近 7 天</small></h2>
    <div class="body"><div id="stats"></div></div></div>

  <div class="card" data-sec="content"><h2>公告</h2>
    <div class="body">
      <div><label>多公告轮播（每行一条，≥2 条生效，最多 4 条，每条 8 秒）</label>
        <textarea id="banners" placeholder="第一条公告&#10;第二条公告">${esc(textOf('banners'))}</textarea></div>
      <div class="row">
        <div><label>单条公告开关</label>
          <select id="banner_on"><option value="false">关闭</option><option value="true"${banners.enabled ? ' selected' : ''}>开启</option></select></div>
        <div><label>单条公告内容（≤40 字淡入，超长跑马灯）</label>
          <input type="text" id="banner_text" value="${esc(banners.text || '')}"></div>
      </div>
      <div class="btns"><button id="saveBanner">保存公告</button>
        <span class="hint">多公告优先于单条公告</span></div>
    </div></div>

  <div class="card" data-sec="content"><h2>每日一言 <small>每行一条；支持 {date} {weekday} {year}</small></h2>
    <div class="body">
      <div class="hint">这里填了就用你的，一天轮换一条，接口不再调用；留空才走 uapis 接口。</div>
      <textarea id="quote" placeholder="留空则使用 uapis 接口（接口挂了再用内置的 68 条文案）">${esc(textOf('quote_custom'))}</textarea>
      <div class="btns"><button id="saveQuote">保存</button>
        <button class="ghost" id="clearQuote">清空，改用接口</button></div>
    </div></div>

  <div class="card" data-sec="content"><h2>节日与倒计时</h2>
    <div class="body">
      <div class="row">
        <div><label>自定义倒计时名称</label><input type="text" id="cd_name" value="${esc(cd.name || '')}" placeholder="如：我的生日"></div>
        <div><label>目标日期</label><input type="text" id="cd_date" value="${esc(cd.date || '')}" placeholder="2026-10-01"></div>
      </div>
      <div class="btns"><button id="saveCd">保存倒计时</button><button class="ghost" id="clearCd">清除</button></div>
      <div><label>自定义公历节日（JSON 数组）</label>
        <textarea id="fests">${esc(JSON.stringify(fests, null, 2))}</textarea>
        <div class="hint">格式：[{"month":10,"day":1,"name":"国庆节","msg":"祝祖国繁荣昌盛！"}]</div></div>
      <div class="btns"><button id="saveFests">保存节日</button></div>
    </div></div>

  <div class="card" data-sec="access"><h2>维护模式 <small>开启后除白名单外都会看到"服务器正在更新"</small></h2>
    <div class="body">
      <div class="row">
        <div><label>开关</label>
          <select id="maint_on"><option value="0">关闭</option><option value="1"${maint ? ' selected' : ''}>开启</option></select></div>
        <div><label>预计完成（可留空）</label><input type="text" id="maint_eta" value="${esc(textOf('maint_eta'))}" placeholder="如：10 分钟"></div>
        <div><label>原因（可留空）</label><input type="text" id="maint_reason" value="${esc(textOf('maint_reason'))}" placeholder="如：升级数据库"></div>
      </div>
      <div class="btns"><button id="saveMaint">保存维护设置</button>
        <span class="hint">白名单 ${Object.keys(wl).length} 个 IP</span></div>
      <div><label>维护白名单（每行一个 IP）</label>
        <textarea id="maint_wl">${esc(Object.keys(wl).join('\n'))}</textarea></div>
      <div class="btns"><button class="ghost" id="saveWl">保存白名单</button></div>
    </div></div>

  <div class="card" data-sec="access"><h2>封禁列表</h2>
    <div class="body">
      <div class="row">
        <div><label>IP</label><input type="text" id="block_ip" placeholder="1.2.3.4"></div>
        <div><label>原因</label><input type="text" id="block_reason" placeholder="刷屏"></div>
      </div>
      <div class="btns"><button class="danger" id="doBlock">封禁</button></div>
      <div id="blockTable"></div>
    </div></div>

  <div class="card" data-sec="ai"><h2>AI 日志分析</h2>
    <div class="body"><div id="aiBox"></div></div></div>

  <div class="card" data-sec="system"><h2>模板</h2>
    <div class="body"><div class="btns">
      <button id="regen">重新生成 XAML</button>
      <button class="ghost" id="preview">预览渲染结果</button>
      <span class="hint">改过 templates/*.tpl 或 config.json 后点一下</span>
    </div>
    <textarea id="pv" class="hidden" readonly style="min-height:220px"></textarea></div></div>
  `;

  $('#blockTable').innerHTML = Object.keys(blocks).length ? `
    <table><tr><th>IP</th><th>原因</th><th></th></tr>
    ${Object.entries(blocks).map(([ip, reason]) => `<tr><td>${esc(ip)}</td><td>${esc(reason)}</td>
      <td><button class="ghost" data-unblock="${esc(ip)}">解封</button></td></tr>`).join('')}
    </table>` : '<div class="hint">暂无封禁记录</div>';

  const ai = state.ai || {};
  if (!ai.enabled) {
    $('#aiBox').innerHTML = '<div class="hint">未开启（config.json 里 enable_ai 设为 true 才生效）</div>';
  } else {
    const rows = ai.rows || [];
    const win = ai.window === 'day' ? '天' : '小时';
    $('#aiBox').innerHTML = `
      <div class="hint">模型 <b>${esc(ai.model)}</b> @ ${esc(ai.base)} · 每个 IP 每${win} ${ai.limit} 次</div>
      <div class="hint">${esc(ai.bucket)}：${rows.length} 个 IP 用过，共 ${ai.calls} 次 ·
        正在分析 ${ai.running} 个 · 留存记录 ${ai.jobs} 条 · 私人密钥 ${ai.own_keys} 个</div>
      ${rows.length ? `<table><tr><th>IP</th><th>这${win}用了</th><th></th></tr>
        ${rows.map(r => `<tr><td>${esc(r.ip)}</td><td>${r.used}</td>
          <td><button class="ghost" data-aireset="${esc(r.ip)}">重置这个 IP</button></td></tr>`).join('')}
        </table>` : `<div class="hint">这${win}还没有人用过</div>`}
      <div class="btns" style="margin-top:12px">
        <button id="aiResetQuota">重置本${win}用量</button>
        <button class="ghost" id="aiResetJobs">清空分析记录</button>
        <button class="ghost" id="aiResetKeys">清空私人密钥</button>
        <button class="danger" id="aiResetAll">全部重置</button>
      </div>`;
  }

  bind();
  showSec(curSec);
}

function bind() {
  const set = (key, value) => api('set', { key, value }).then(() => toast('已保存')).then(load);

  $('#saveMaint').onclick = () => {
    api('set', { key: 'maint_mode_live', value: $('#maint_on').value })
      .then(() => api('set', { key: 'maint_eta', value: $('#maint_eta').value }))
      .then(() => api('set', { key: 'maint_reason', value: $('#maint_reason').value }))
      .then(() => toast('维护设置已保存')).then(load);
  };
  $('#saveWl').onclick = () => {
    const map = {};
    $('#maint_wl').value.split('\n').map(s => s.trim()).filter(Boolean).forEach(ip => map[ip] = 1);
    set('maint_whitelist', map);
  };
  $('#saveBanner').onclick = () => {
    api('set', { key: 'banners', value: $('#banners').value })
      .then(() => api('set', { key: 'homepage_banner', value: { enabled: $('#banner_on').value === 'true', text: $('#banner_text').value } }))
      .then(() => toast('公告已保存')).then(load);
  };
  $('#saveQuote').onclick = () => set('quote_custom', $('#quote').value);
  $('#clearQuote').onclick = () => api('delete', { key: 'quote_custom' }).then(() => toast('已清空，改回接口')).then(load);
  $('#saveCd').onclick = () => set('custom_countdown', { name: $('#cd_name').value, date: $('#cd_date').value });
  $('#clearCd').onclick = () => api('delete', { key: 'custom_countdown' }).then(() => toast('已清除')).then(load);
  $('#saveFests').onclick = () => {
    try { set('custom_festivals', JSON.parse($('#fests').value || '[]')); }
    catch (e) { toast('JSON 格式有误：' + e.message); }
  };
  $('#doBlock').onclick = () => api('block', { ip: $('#block_ip').value, reason: $('#block_reason').value })
    .then(() => toast('已封禁')).then(load);
  document.querySelectorAll('[data-unblock]').forEach(btn => {
    btn.onclick = () => api('block', { ip: btn.dataset.unblock, remove: true }).then(() => toast('已解封')).then(load);
  });
  const aiReset = (scope, ip) => {
    if (!confirm('确定要重置吗？scope=' + scope + (ip ? ' ip=' + ip : ''))) return;
    api('ai_reset', { scope: scope, ip: ip || '' })
      .then(r => toast('已重置：' + JSON.stringify(r.result)))
      .then(load).catch(e => toast('失败：' + e.message));
  };
  [['aiResetQuota', 'quota'], ['aiResetJobs', 'jobs'], ['aiResetKeys', 'keys'], ['aiResetAll', 'all']]
    .forEach(([id, scope]) => { const el = $('#' + id); if (el) el.onclick = () => aiReset(scope); });
  document.querySelectorAll('[data-aireset]').forEach(btn => {
    btn.onclick = () => aiReset('all', btn.dataset.aireset);
  });

  $('#regen').onclick = () => {
    $('#regen').disabled = true;
    api('regenerate').then(() => toast('已重新生成')).catch(e => toast('失败：' + e.message))
      .finally(() => $('#regen').disabled = false);
  };
  $('#preview').onclick = () => api('preview').then(r => {
    const el = $('#pv');
    el.value = r.xaml; el.classList.remove('hidden');
  }).catch(e => toast('失败：' + e.message));
}

async function loadStats() {
  try {
    const { stats } = await api('stats');
    $('#kpi').innerHTML = `
      <div><span class="hint">总访问</span><b>${stats.total}</b></div>
      <div><span class="hint">今日</span><b>${stats.today}</b></div>
      <div><span class="hint">独立 IP</span><b>${stats.unique}</b></div>`;
    const countries = (stats.countries || []).map(c => `${esc(c.code)} · ${c.count}`).join('　') || '暂无数据';
    const hosts = (stats.hosts || []).map(h => `<tr><td>${esc(h.ip)}</td><td>${h.count}</td></tr>`).join('');
    $('#stats').innerHTML = `<div class="hint" style="margin-bottom:10px">地区：${countries}</div>` +
      (hosts ? `<table><tr><th>IP</th><th>次数</th></tr>${hosts}</table>` : '<div class="hint">暂无数据</div>');
  } catch (e) { /* 未登录时忽略 */ }
}

function esc(s) {
  return String(s === undefined || s === null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

$('#doLogin').onclick = async () => {
  try {
    await api('login', { token: $('#token').value });
    await load();
  } catch (e) { toast('登录失败：令牌不正确'); }
};
$('#token').addEventListener('keydown', e => { if (e.key === 'Enter') $('#doLogin').click(); });
$('#logout').onclick = () => api('logout', {}).then(showLogin);

// 打开页面时若已有会话就直接进后台
fetch('/admin/api/state').then(r => r.ok ? load() : showLogin()).catch(showLogin);
</script>
</body>
</html>
"""
