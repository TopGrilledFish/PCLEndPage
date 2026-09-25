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
        })

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
    --bg:#0d0f12; --card:#15181d; --card2:#1b1f26; --line:#262c35;
    --ink:#e8eaed; --ink2:#9aa4b2; --ink3:#5f6b7a;
    --accent:#58a6ff; --ok:#3fb950; --warn:#e5a13a; --danger:#e5544d;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif}
  header{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:14px;
    padding:14px 22px;background:rgba(13,15,18,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
  header h1{font-size:16px;margin:0;font-weight:600}
  header .sp{flex:1}
  .wrap{max-width:1080px;margin:0 auto;padding:22px;display:grid;gap:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
  .card > h2{margin:0;padding:14px 18px;font-size:14px;font-weight:600;border-bottom:1px solid var(--line);
    background:var(--card2);display:flex;align-items:center;gap:10px}
  .card > h2 small{color:var(--ink3);font-weight:400}
  .body{padding:18px;display:grid;gap:14px}
  label{display:block;font-size:12px;color:var(--ink2);margin-bottom:6px}
  input[type=text],input[type=password],textarea,select{
    width:100%;background:#0f1216;border:1px solid var(--line);border-radius:9px;color:var(--ink);
    padding:9px 11px;font:14px/1.6 inherit;outline:none}
  input:focus,textarea:focus,select:focus{border-color:var(--accent)}
  textarea{min-height:96px;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:13px}
  .row{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
  button{background:var(--accent);color:#04121f;border:0;border-radius:9px;padding:9px 16px;
    font:600 14px/1 inherit;cursor:pointer}
  button.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}
  button.danger{background:var(--danger);color:#fff}
  button:disabled{opacity:.5;cursor:not-allowed}
  .btns{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .hint{font-size:12px;color:var(--ink3)}
  .pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:600}
  .pill.on{background:rgba(63,185,80,.16);color:var(--ok)}
  .pill.off{background:rgba(154,164,178,.14);color:var(--ink2)}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
  th{color:var(--ink3);font-weight:500;font-size:12px}
  .kpi{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}
  .kpi div{background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:14px}
  .kpi b{display:block;font-size:24px;margin-top:4px}
  #login{max-width:380px;margin:14vh auto}
  .toast{position:fixed;right:18px;bottom:18px;background:var(--card2);border:1px solid var(--line);
    border-left:3px solid var(--accent);border-radius:10px;padding:11px 16px;font-size:13px;opacity:0;
    transform:translateY(8px);transition:.2s;pointer-events:none}
  .toast.show{opacity:1;transform:none}
  .hidden{display:none!important}
</style>
</head>
<body>
<header>
  <h1>🎮 主页管理后台</h1>
  <span id="status" class="pill off">未登录</span>
  <span class="sp"></span>
  <a href="/Custom.xaml" target="_blank" style="color:var(--accent);font-size:13px">查看主页</a>
  <button class="ghost hidden" id="logout">退出</button>
</header>

<div id="login" class="card">
  <h2>登录</h2>
  <div class="body">
    <div><label>管理令牌</label><input type="password" id="token" placeholder="config.admin_token"></div>
    <div class="btns"><button id="doLogin">登录</button>
      <span class="hint">默认令牌是 admin，部署前请务必修改。</span></div>
  </div>
</div>

<div class="wrap hidden" id="panel"></div>
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

async function load() {
  const s = await api('state');
  state.store = s.store || {};
  state.config = s.config || {};
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

  $('#panel').innerHTML = `
  <div class="card"><h2>KPI <small>近 7 天</small></h2><div class="body"><div class="kpi" id="kpi"></div></div></div>

  <div class="card"><h2>维护模式 <small>开启后除白名单外都会看到"服务器正在更新"</small></h2>
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

  <div class="card"><h2>公告</h2>
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

  <div class="card"><h2>每日一言 <small>每行一条；支持 {date} {weekday} {year}</small></h2>
    <div class="body">
      <textarea id="quote" placeholder="留空则使用内置的 68 条文案">${esc(textOf('quote_custom'))}</textarea>
      <div class="btns"><button id="saveQuote">保存</button>
        <button class="ghost" id="clearQuote">恢复内置文案</button></div>
    </div></div>

  <div class="card"><h2>节日与倒计时</h2>
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

  <div class="card"><h2>封禁列表</h2>
    <div class="body">
      <div class="row">
        <div><label>IP</label><input type="text" id="block_ip" placeholder="1.2.3.4"></div>
        <div><label>原因</label><input type="text" id="block_reason" placeholder="刷屏"></div>
      </div>
      <div class="btns"><button class="danger" id="doBlock">封禁</button></div>
      <div id="blockTable"></div>
    </div></div>

  <div class="card"><h2>访问统计 <small>近 7 天</small></h2>
    <div class="body"><div id="stats"></div></div></div>

  <div class="card"><h2>模板</h2>
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

  bind();
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
  $('#clearQuote').onclick = () => api('delete', { key: 'quote_custom' }).then(() => toast('已恢复内置文案')).then(load);
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
