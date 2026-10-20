# -*- coding: utf-8 -*-
"""管理后台的页面本体。

单独放一个模块，是因为它主要是 HTML/CSS/JS：塞进 admin.py 会把路由逻辑淹掉。
页面不依赖任何外部资源（无 CDN、无字体文件），断网的机器上也能用。

约定：JS 只跟 ``/admin/api/*`` 说话，不往页面里注入任何服务端数据；
所有渲染都在前端完成，所以刷新页面只要重新拉一次 state 就够。
"""

ADMIN_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>主页管理后台</title>
<style>
  :root{
    --bg:#0a0c10; --bg2:#0e1116; --card:#11151c; --card2:#161b24;
    --line:#222a36; --line2:#2d3644;
    --ink:#e9eef6; --ink2:#9aa7b8; --ink3:#647084;
    --accent:#5b8cff; --accent2:#8b5cf6; --soft:rgba(91,140,255,.14);
    --ok:#3ecf7f; --warn:#e0a83a; --danger:#f0605a;
    --ok-soft:rgba(62,207,127,.13); --warn-soft:rgba(224,168,58,.13); --danger-soft:rgba(240,96,90,.13);
    --shadow:0 10px 30px rgba(0,0,0,.32);
    --r:14px; --r2:10px; --r3:8px;
  }
  [data-theme=light]{
    --bg:#f3f5fa; --bg2:#eceff6; --card:#ffffff; --card2:#f7f9fd;
    --line:#e3e9f3; --line2:#d3dceb;
    --ink:#16202e; --ink2:#57647a; --ink3:#8592a6;
    --accent:#3b6ff0; --accent2:#7c4cf0; --soft:rgba(59,111,240,.11);
    --ok:#12915a; --warn:#a8741a; --danger:#d13c37;
    --ok-soft:rgba(18,145,90,.1); --warn-soft:rgba(168,116,26,.1); --danger-soft:rgba(209,60,55,.1);
    --shadow:0 8px 22px rgba(22,32,46,.07);
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:radial-gradient(1200px 600px at 15% -10%, var(--soft), transparent 60%),var(--bg);
    color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
    -webkit-font-smoothing:antialiased}
  a{color:var(--accent);text-decoration:none}
  a:hover{text-decoration:underline}
  .ic{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round;flex:none}
  .hidden{display:none!important}
  .mono{font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace}

  /* ---------- 顶栏 ---------- */
  .topbar{position:sticky;top:0;z-index:40;display:flex;align-items:center;gap:10px;height:60px;
    padding:0 20px;background:color-mix(in srgb,var(--bg) 82%,transparent);
    backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
  .brand{display:flex;align-items:center;gap:10px;font-weight:650;letter-spacing:.2px}
  .logo{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;flex:none;
    background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 4px 14px rgba(91,140,255,.35)}
  .logo svg{width:16px;height:16px;fill:none;stroke:#fff;stroke-width:2.3;stroke-linecap:round;stroke-linejoin:round}
  .brand small{color:var(--ink3);font-weight:500;font-size:12px}
  .sp{flex:1}
  .chip{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:999px;
    font-size:12px;font-weight:600;background:var(--card2);border:1px solid var(--line);color:var(--ink2)}
  .chip::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
  .chip.ok{color:var(--ok);background:var(--ok-soft);border-color:transparent}
  .chip.warn{color:var(--warn);background:var(--warn-soft);border-color:transparent}
  .chip.bad{color:var(--danger);background:var(--danger-soft);border-color:transparent}
  .chip.plain::before{display:none}

  /* ---------- 登录 ---------- */
  .login{min-height:calc(100vh - 60px);display:grid;place-items:center;padding:24px}
  .login .card{width:min(400px,100%);overflow:hidden}
  .login .logo{width:46px;height:46px;border-radius:14px;margin:2px auto 0}
  .login .logo svg{width:24px;height:24px}
  .login h1{font-size:17px;margin:0;text-align:center;font-weight:650}
  .login p{margin:4px 0 0;text-align:center;color:var(--ink3);font-size:12.5px}

  /* ---------- 布局 ---------- */
  .layout{display:grid;grid-template-columns:214px minmax(0,1fr);gap:22px;max-width:1300px;margin:0 auto;padding:22px}
  .rail{position:sticky;top:82px;align-self:start;display:grid;gap:4px}
  .rail .cap{font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--ink3);padding:0 12px 8px}
  .nav-item{display:flex;align-items:center;gap:10px;width:100%;padding:9px 12px;border:1px solid transparent;
    border-radius:var(--r2);background:transparent;color:var(--ink2);font:600 13.5px/1 inherit;cursor:pointer;
    transition:background .15s,color .15s}
  .nav-item:hover{background:var(--card2);color:var(--ink)}
  .nav-item.on{background:var(--soft);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 26%,transparent)}
  .nav-item .label{flex:1;text-align:left}
  .nav-item .badge{font-size:11px;font-weight:700;padding:1px 7px;border-radius:999px;
    background:var(--card2);border:1px solid var(--line);color:var(--ink3)}
  .nav-item.on .badge{background:color-mix(in srgb,var(--accent) 18%,transparent);border-color:transparent;color:var(--accent)}

  main{display:grid;gap:16px;min-width:0;align-content:start}
  .page-head{display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap;margin:2px 2px 0}
  .page-head h2{margin:0;font-size:19px;font-weight:680;letter-spacing:-.2px}
  .page-head p{margin:0;color:var(--ink3);font-size:12.5px}

  /* ---------- 卡片 ---------- */
  .card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);overflow:hidden}
  .card-h{display:flex;align-items:center;gap:10px;padding:13px 16px;border-bottom:1px solid var(--line);
    background:linear-gradient(180deg,var(--card2),transparent)}
  .card-h h3{margin:0;font-size:13.5px;font-weight:650}
  .card-h .hint{margin-left:auto;color:var(--ink3);font-size:12px}
  .card-b{padding:16px;display:grid;gap:14px}
  .card-b.tight{padding:0}
  .hint{color:var(--ink3);font-size:12px}
  .hint b{color:var(--ink2);font-weight:600}
  .rows{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
  .two{display:grid;gap:16px;grid-template-columns:minmax(0,1.3fr) minmax(0,.7fr);align-items:start}
  @media (max-width:980px){.two{grid-template-columns:1fr}}

  label{display:block;font-size:12px;font-weight:600;color:var(--ink2);margin-bottom:6px}
  input,textarea,select{width:100%;background:var(--bg2);border:1px solid var(--line);border-radius:var(--r2);
    color:var(--ink);padding:9px 11px;font:13.5px/1.55 inherit;outline:none;transition:border-color .15s,box-shadow .15s}
  input:focus,textarea:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--soft)}
  input::placeholder,textarea::placeholder{color:var(--ink3);opacity:.8}
  textarea{min-height:100px;resize:vertical}
  textarea.code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12.5px;line-height:1.6}

  .btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;height:34px;padding:0 14px;
    border:0;border-radius:var(--r2);background:var(--accent);color:#fff;font:650 13px/1 inherit;cursor:pointer;
    transition:filter .15s,transform .06s;white-space:nowrap}
  .btn:hover{filter:brightness(1.08)}
  .btn:active{transform:translateY(1px)}
  .btn.ghost{background:color-mix(in srgb,var(--bg2) 70%,var(--card));color:var(--ink);border:1px solid var(--line2)}
  .btn.ghost:hover{background:var(--bg2);filter:none}
  .btn.danger{background:var(--danger)}
  .btn.sm{height:28px;padding:0 10px;font-size:12px;border-radius:var(--r3)}
  .btn:disabled{opacity:.5;cursor:not-allowed;filter:none}
  .btn-row{display:flex;gap:9px;flex-wrap:wrap;align-items:center}

  /* ---------- KPI ---------- */
  .kpi{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
  .kpi .k{background:var(--card2);border:1px solid var(--line);border-radius:var(--r2);padding:13px 15px}
  .kpi .k span{display:block;font-size:11.5px;color:var(--ink3);font-weight:600;letter-spacing:.02em}
  .kpi .k b{display:block;font-size:25px;font-weight:700;letter-spacing:-.6px;margin-top:2px;line-height:1.25}
  .kpi .k em{font-style:normal;font-size:11.5px;color:var(--ink3)}
  .kpi .k.ok b{color:var(--ok)} .kpi .k.warn b{color:var(--warn)} .kpi .k.bad b{color:var(--danger)}

  /* ---------- 柱状图 ---------- */
  .bars{display:flex;align-items:flex-end;gap:10px;height:132px;padding:6px 2px 0}
  .bars .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:6px;height:100%}
  .bars .col .bar{width:100%;max-width:42px;border-radius:7px 7px 3px 3px;min-height:3px;
    background:linear-gradient(180deg,var(--accent),color-mix(in srgb,var(--accent2) 65%,var(--accent)));opacity:.9;
    transition:opacity .15s}
  .bars .col:hover .bar{opacity:1}
  .bars .col .n{font-size:11.5px;font-weight:700;color:var(--ink2)}
  .bars .col .d{font-size:11px;color:var(--ink3);white-space:nowrap}
  .bars .col.today .bar{background:linear-gradient(180deg,var(--ok),color-mix(in srgb,var(--ok) 55%,var(--accent)))}
  .bars .col.today .d{color:var(--ok);font-weight:700}

  /* ---------- 排行条 ---------- */
  .rank{display:grid;gap:9px}
  .rank .r{display:grid;grid-template-columns:56px minmax(0,1fr) 44px;align-items:center;gap:10px;font-size:12.5px}
  .rank .r .name{color:var(--ink2);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .rank .r .track{display:block;height:7px;border-radius:99px;background:var(--card2);overflow:hidden}
  .rank .r .fill{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent),var(--accent2))}
  .rank .r .num{text-align:right;color:var(--ink3);font-variant-numeric:tabular-nums}

  /* ---------- 表格 ---------- */
  .tbl-wrap{overflow:auto}
  table.tbl{width:100%;border-collapse:collapse;font-size:12.8px}
  .tbl th,.tbl td{text-align:left;padding:9px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
  .tbl thead th{position:sticky;top:0;background:var(--card2);color:var(--ink3);font-weight:600;font-size:11.5px;
    letter-spacing:.03em;z-index:1}
  .tbl tbody tr:hover{background:var(--card2)}
  .tbl tr:last-child td{border-bottom:0}
  .tbl td.num{font-variant-numeric:tabular-nums;color:var(--ink2)}
  .tbl td .btn{height:24px;padding:0 9px;font-size:11.5px}
  .tbl .mono{color:var(--ink)}
  .empty{padding:22px;text-align:center;color:var(--ink3);font-size:12.5px}

  /* ---------- 小件 ---------- */
  .tags{display:flex;flex-wrap:wrap;gap:7px;min-height:8px}
  .tag{display:inline-flex;align-items:center;gap:7px;height:27px;padding:0 5px 0 11px;border-radius:999px;
    background:var(--card2);border:1px solid var(--line);font-size:12.5px;font-weight:600;color:var(--ink2)}
  .tag .x{display:grid;place-items:center;width:19px;height:19px;border-radius:50%;border:0;cursor:pointer;
    background:transparent;color:var(--ink3);font-size:13px;line-height:1}
  .tag .x:hover{background:var(--danger-soft);color:var(--danger)}
  .tag.blocked{color:var(--danger);background:var(--danger-soft);border-color:transparent}
  .tag.allowed{color:var(--ok);background:var(--ok-soft);border-color:transparent}
  .tag small{font-weight:500;opacity:.75}
  .kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px 18px;font-size:12.8px}
  .kv .i{display:flex;gap:8px;align-items:baseline;min-width:0}
  .kv .i span{color:var(--ink3);flex:none;min-width:74px}
  .kv .i b{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .switch{position:relative;display:inline-flex;align-items:center;gap:10px;cursor:pointer;user-select:none}
  .switch input{position:absolute;opacity:0;width:0;height:0}
  .switch .track{position:relative;width:42px;height:23px;border-radius:99px;background:var(--line2);transition:background .18s;flex:none}
  .switch .track::after{content:"";position:absolute;top:3px;left:3px;width:17px;height:17px;border-radius:50%;
    background:#fff;transition:transform .18s;box-shadow:0 1px 3px rgba(0,0,0,.3)}
  .switch input:checked + .track{background:var(--ok)}
  .switch input:checked + .track::after{transform:translateX(19px)}
  .switch input:focus-visible + .track{box-shadow:0 0 0 3px var(--soft)}

  /* 体检结果 */
  .report{display:grid;gap:8px}
  .rep-sec{border:1px solid var(--line);border-radius:var(--r2);overflow:hidden;background:var(--card2)}
  .rep-sec > summary{cursor:pointer;padding:9px 13px;font-size:12.5px;font-weight:650;display:flex;align-items:center;gap:9px;list-style:none}
  .rep-sec > summary::-webkit-details-marker{display:none}
  .rep-sec > summary::before{content:"+";color:var(--ink3);font-weight:700;width:10px}
  .rep-sec[open] > summary::before{content:"–"}
  .rep-sec > summary .cnt{margin-left:auto;color:var(--ink3);font-weight:600;font-size:11.5px}
  .rep-list{display:grid;gap:5px;padding:2px 13px 12px}
  .rep-item{display:flex;gap:8px;font-size:12.4px;line-height:1.55;color:var(--ink2)}
  .rep-item .dot{width:7px;height:7px;border-radius:50%;margin-top:7px;flex:none;background:var(--ink3)}
  .rep-item.ok .dot{background:var(--ok)} .rep-item.warn .dot{background:var(--warn)} .rep-item.fail .dot{background:var(--danger)}
  .rep-item.fail{color:var(--ink)}
  .rep-sum{display:flex;gap:8px;flex-wrap:wrap;align-items:center}

  /* 日志 */
  pre.log{margin:0;padding:14px 16px;max-height:420px;overflow:auto;background:#080a0e;color:#c7d2e0;
    font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;border-radius:0}
  [data-theme=light] pre.log{background:#0e141d;color:#cdd8e6}
  pre.log::-webkit-scrollbar{width:10px;height:10px}
  pre.log::-webkit-scrollbar-thumb{background:#2a3444;border-radius:6px}

  /* 提示条 */
  .note{display:flex;gap:9px;padding:10px 13px;border-radius:var(--r2);font-size:12.5px;line-height:1.55;
    background:var(--soft);color:var(--ink2);border:1px solid color-mix(in srgb,var(--accent) 22%,transparent)}
  .note.warn{background:var(--warn-soft);border-color:color-mix(in srgb,var(--warn) 26%,transparent)}
  .note.bad{background:var(--danger-soft);border-color:color-mix(in srgb,var(--danger) 26%,transparent)}

  /* toast / modal */
  .toasts{position:fixed;right:18px;bottom:18px;z-index:90;display:grid;gap:9px;justify-items:end;pointer-events:none}
  .toast{max-width:min(420px,86vw);padding:10px 15px;border-radius:11px;background:var(--card);
    border:1px solid var(--line);border-left:3px solid var(--accent);box-shadow:0 12px 32px rgba(0,0,0,.4);
    font-size:13px;animation:pop .22s ease-out;pointer-events:auto}
  .toast.ok{border-left-color:var(--ok)} .toast.warn{border-left-color:var(--warn)} .toast.bad{border-left-color:var(--danger)}
  @keyframes pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  .mask{position:fixed;inset:0;z-index:100;background:rgba(4,6,10,.62);backdrop-filter:blur(3px);
    display:grid;place-items:center;padding:22px;animation:fade .16s ease-out}
  @keyframes fade{from{opacity:0}to{opacity:1}}
  .modal{width:min(430px,100%);background:var(--card);border:1px solid var(--line);border-radius:var(--r);
    box-shadow:0 24px 60px rgba(0,0,0,.5);overflow:hidden}
  .modal .m-h{padding:15px 18px;font-weight:650;font-size:14px;border-bottom:1px solid var(--line)}
  .modal .m-b{padding:16px 18px;color:var(--ink2);font-size:13px;line-height:1.65}
  .modal .m-f{display:flex;justify-content:flex-end;gap:9px;padding:13px 18px;border-top:1px solid var(--line);background:var(--card2)}
  .spin{width:14px;height:14px;border-radius:50%;border:2px solid color-mix(in srgb,currentColor 28%,transparent);
    border-top-color:currentColor;animation:spin .7s linear infinite;flex:none}
  @keyframes spin{to{transform:rotate(360deg)}}

  @media (max-width:860px){
    .layout{grid-template-columns:1fr;gap:14px;padding:14px}
    .rail{position:static;grid-auto-flow:column;grid-auto-columns:max-content;overflow-x:auto;
      gap:7px;padding-bottom:4px;scrollbar-width:none}
    .rail::-webkit-scrollbar{display:none}
    .rail .cap{display:none}
    .nav-item{width:auto;border:1px solid var(--line);background:var(--card);padding:8px 12px}
    .nav-item .label{white-space:nowrap}
    .topbar{padding:0 13px;gap:8px}
    .brand small{display:none}
    .page-head h2{font-size:17px}
    .kv{grid-template-columns:1fr}
    .kpi{grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}
    #chipUser{display:none}
    .topbar .brand span:not(.logo){max-width:34vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  }
  @media (max-width:560px){
    .card-b{padding:13px}
    .topbar .brand small{display:none}
    .btn{padding:0 11px}
  }
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>

<header class="topbar">
  <div class="brand">
    <span class="logo"><svg viewBox="0 0 24 24"><path d="M3 10.6 12 3.2l9 7.4"/><path d="M5.4 9.6V21h13.2V9.6"/><path d="M9.6 21v-6.2h4.8V21"/></svg></span>
    <span>主页管理后台</span>
    <small id="headVersion"></small>
  </div>
  <span id="chipMaint" class="chip plain hidden"></span>
  <span id="chipDirty" class="chip warn hidden">未保存</span>
  <span class="sp"></span>
  <span id="chipUser" class="chip plain hidden"></span>
  <a class="chip plain" href="/Custom.xaml" target="_blank" rel="noreferrer">查看主页</a>
  <button class="btn ghost sm" id="themeBtn" title="切换深浅色">深色</button>
  <button class="btn ghost sm hidden" id="logoutBtn">退出</button>
</header>

<section class="login" id="loginView">
  <div class="card">
    <div class="card-b" style="gap:15px;padding:24px">
      <div class="logo"><svg viewBox="0 0 24 24"><path d="M3 10.6 12 3.2l9 7.4"/><path d="M5.4 9.6V21h13.2V9.6"/><path d="M9.6 21v-6.2h4.8V21"/></svg></div>
      <div>
        <h1>主页管理后台</h1>
        <p>输入 config.json 里的 admin_token 继续</p>
      </div>
      <div>
        <label for="token">管理令牌</label>
        <input type="password" id="token" placeholder="admin_token" autocomplete="current-password" autofocus>
      </div>
      <button class="btn" id="loginBtn" style="width:100%;height:38px">登录</button>
      <div class="hint" style="text-align:center">会话 8 小时有效，令牌只存在服务端配置里</div>
    </div>
  </div>
</section>

<div class="layout hidden" id="panel">
  <aside class="rail">
    <div class="cap">管理</div>
    <nav id="nav" style="display:grid;gap:4px"></nav>
  </aside>
  <main id="main"></main>
</div>

<div class="toasts" id="toasts"></div>
<div id="modalRoot"></div>

<script>
"use strict";
/* ================= 基础工具 ================= */
const $ = (s, root) => (root || document).querySelector(s);
const $$ = (s, root) => Array.from((root || document).querySelectorAll(s));

function esc(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const ICONS = {
  grid: '<rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/>',
  doc: '<path d="M6 3h8.5L19 7.5V21H6z"/><path d="M14 3v5h5"/><path d="M9.2 12.5h6.6M9.2 16h4.6"/>',
  shield: '<path d="M12 3.2 19 6v5.7c0 4.4-2.9 7.8-7 9-4.1-1.2-7-4.6-7-9V6z"/><path d="m9.2 11.8 2 2 3.6-3.8"/>',
  spark: '<path d="M12 3.4 14 9.4l6 2.1-6 2.1-2 6-2-6-6-2.1 6-2.1z"/>',
  gear: '<circle cx="12" cy="12" r="3.1"/><path d="M12 3v2.3M12 18.7V21M4.2 7.5l2 1.2M17.8 15.3l2 1.2M4.2 16.5l2-1.2M17.8 8.7l2-1.2"/>',
  db: '<ellipse cx="12" cy="6" rx="7.4" ry="2.9"/><path d="M4.6 6v6c0 1.6 3.3 2.9 7.4 2.9s7.4-1.3 7.4-2.9V6"/><path d="M4.6 12v6c0 1.6 3.3 2.9 7.4 2.9s7.4-1.3 7.4-2.9v-6"/>',
};

const SECTIONS = [
  { id: "overview", name: "总览", icon: "grid", desc: "访问情况与快速操作", badge: () => null },
  { id: "content", name: "内容", icon: "doc", desc: "公告、每日一言、节日与倒计时", badge: () => bannerCount() || null },
  { id: "visitors", name: "访客", icon: "shield", desc: "维护模式、白名单与封禁", badge: () => blocked().length || null },
  { id: "ai", name: "AI 分析", icon: "spark", desc: "额度用量与任务", badge: () => (S.ai && S.ai.enabled ? (S.ai.calls || 0) : null) },
  { id: "system", name: "系统", icon: "gear", desc: "信息、体检、日志、缓存与模板", badge: () => (S.report && S.report.fail ? S.report.fail : null) },
  { id: "data", name: "数据", icon: "db", desc: "store.json 原始编辑、导出与安全", badge: () => null },
];

const S = {
  store: {}, config: {}, info: {}, ai: {}, stats: {}, report: null,
  section: "overview", dirty: false, loggedIn: false, busy: 0,
};

/* ================= 会话与请求 ================= */
async function api(action, data, opts) {
  const options = Object.assign({ headers: {}, method: data === undefined ? "GET" : "POST" }, opts || {});
  if (data !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  const res = await fetch("/admin/api/" + action, options);
  let json = null;
  try { json = await res.json(); } catch (e) { json = null; }
  if (res.status === 401) { showLogin(); throw new Error("未登录或会话已过期"); }
  if (!json) throw new Error("服务端返回了无法解析的内容（HTTP " + res.status + "）");
  if (!json.ok) {
    const map = {
      bad_token: "令牌不正确", unauthorized: "未登录", bad_ip: "不是合法 IP",
      key_not_writable: "这个键不允许后台修改", ai_disabled: "没开启 AI 分析",
      bad_target: "未知的缓存目标", busy: "正在体检，稍等一下", empty: "内容为空",
    };
    throw new Error(map[json.error] || json.error || "请求失败");
  }
  return json;
}

/* ================= 提示 / 弹窗 ================= */
function toast(message, kind) {
  const el = document.createElement("div");
  el.className = "toast " + (kind || "");
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateY(6px)"; }, 2600);
  setTimeout(() => el.remove(), 3100);
}

function confirmDialog(opt) {
  return new Promise((resolve) => {
    const root = $("#modalRoot");
    root.innerHTML =
      '<div class="mask"><div class="modal">' +
      '<div class="m-h">' + esc(opt.title || "确认") + "</div>" +
      '<div class="m-b">' + esc(opt.body || "") + "</div>" +
      '<div class="m-f">' +
      '<button class="btn ghost" data-answer="0">取消</button>' +
      '<button class="btn ' + (opt.danger ? "danger" : "") + '" data-answer="1">' +
      esc(opt.okText || "确定") + "</button></div></div></div>";
    const close = (answer) => {
      root.innerHTML = "";
      document.removeEventListener("keydown", onKey);
      resolve(answer);
    };
    const onKey = (event) => {
      if (event.key === "Escape") close(false);
      if (event.key === "Enter") close(true);
    };
    $$("[data-answer]", root).forEach((btn) => {
      btn.onclick = () => close(btn.dataset.answer === "1");
    });
    $(".mask", root).onclick = (event) => { if (event.target.classList.contains("mask")) close(false); };
    document.addEventListener("keydown", onKey);
  });
}

/* ================= 登录状态 ================= */
function showLogin() {
  S.loggedIn = false;
  $("#loginView").classList.remove("hidden");
  $("#panel").classList.add("hidden");
  $("#logoutBtn").classList.add("hidden");
  $("#chipUser").classList.add("hidden");
  $("#chipMaint").classList.add("hidden");
  setDirty(false);
}

function showPanel() {
  S.loggedIn = true;
  $("#loginView").classList.add("hidden");
  $("#panel").classList.remove("hidden");
  $("#logoutBtn").classList.remove("hidden");
}

/* ================= 数据读取 ================= */
const DEFAULT_STORE = {};

function jsonOf(key, fallback) {
  const raw = S.store[key];
  if (raw === undefined || raw === null || raw === "") return fallback;
  if (typeof raw === "object") return raw;
  return fallback;
}

function textOf(key) {
  const raw = S.store[key];
  return raw === undefined || raw === null ? "" : String(raw);
}

function boolOf(key) {
  const raw = textOf(key).toLowerCase();
  return ["1", "true", "on", "yes"].includes(raw);
}

function bannerLines() {
  return textOf("banners").split("\n").map((line) => line.trim()).filter(Boolean);
}
function bannerCount() { return bannerLines().length; }
function blocked() {
  const map = jsonOf("block:list", {});
  return Object.entries(map).map(([ip, reason]) => ({ ip, reason: String(reason) }));
}
function allowed() {
  const map = jsonOf("maint_whitelist", {});
  return Object.keys(map);
}

async function refresh(render) {
  const s = await api("state");
  S.store = s.store || {};
  S.config = s.config || {};
  S.info = s.info || {};
  S.ai = s.ai || {};
  S.stats = s.stats || {};
  if (render !== false) renderAll();
}

function renderAll() {
  renderChrome();
  renderSection(S.section);
  setDirty(false);
}

/* ================= 顶栏 ================= */
function renderChrome() {
  const info = S.info || {};
  $("#headVersion").textContent = info.version ? "v" + info.version : "";
  $("#chipUser").classList.remove("hidden");
  $("#chipUser").textContent = (info.sessions || 1) + " 个会话 · 已登录";

  const maint = boolOf("maint_mode_live");
  const chip = $("#chipMaint");
  chip.classList.remove("hidden", "ok", "bad", "warn", "plain");
  if (maint) {
    chip.classList.remove("hidden");
    chip.textContent = "维护模式进行中";
    chip.classList.add("warn");
  } else {
    chip.textContent = "服务正常";
    chip.classList.add("ok");
  }
  $("#themeBtn").textContent = document.documentElement.dataset.theme === "light" ? "浅色" : "深色";
}

function setDirty(value) {
  S.dirty = !!value;
  $("#chipDirty").classList.toggle("hidden", !S.dirty);
}

/* ================= 侧栏 ================= */
function renderNav() {
  const nav = $("#nav");
  nav.innerHTML = SECTIONS.map((sec) => {
    const badge = sec.badge();
    return '<button class="nav-item' + (sec.id === S.section ? " on" : "") + '" data-nav="' + sec.id + '">' +
      '<svg class="ic" viewBox="0 0 24 24">' + ICONS[sec.icon] + "</svg>" +
      '<span class="label">' + esc(sec.name) + "</span>" +
      (badge ? '<span class="badge">' + esc(badge) + "</span>" : "") +
      "</button>";
  }).join("");
}

async function gotoSection(id) {
  if (id === S.section) return;
  if (S.dirty) {
    const ok = await confirmDialog({
      title: "有未保存的修改",
      body: "切换页面会丢掉当前未保存的内容，继续吗？",
      okText: "丢掉并切换",
    });
    if (!ok) return;
  }
  S.section = id;
  if (location.hash !== "#/" + id) history.replaceState(null, "", "#/" + id);
  renderNav();
  renderSection(id);
  $("#main").scrollTop = 0;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ================= 渲染辅助 ================= */
function pageHead(title, desc, extra) {
  return '<div class="page-head"><div><h2>' + esc(title) + "</h2><p>" + desc + "</p></div>" +
    (extra ? '<div style="margin-left:auto" class="btn-row">' + extra + "</div>" : "") + "</div>";
}

function card(title, hint, body, opts) {
  const o = opts || {};
  return '<section class="card' + (o.cls ? " " + o.cls : "") + '">' +
    '<div class="card-h"><h3>' + esc(title) + "</h3>" +
    (hint ? '<span class="hint">' + hint + "</span>" : "") + "</div>" +
    '<div class="card-b' + (o.tight ? " tight" : "") + '">' + body + "</div></section>";
}

function kpi(label, value, note, tone) {
  return '<div class="k' + (tone ? " " + tone : "") + '"><span>' + esc(label) + "</span><b>" +
    esc(value) + "</b><em>" + esc(note || "") + "</em></div>";
}

function rankRows(items, total, max) {
  if (!items.length) return '<div class="hint">还没有数据</div>';
  return '<div class="rank">' + items.map((item) => {
    const width = Math.max(2, Math.round((item.count / (max || total || 1)) * 100));
    return '<div class="r"><span class="name" title="' + esc(item.name) + '">' + esc(item.name) + "</span>" +
      '<span class="track"><span class="fill" style="width:' + width + '%"></span></span>' +
      '<span class="num">' + item.count + "</span></div>";
  }).join("") + "</div>";
}

const COUNTRY_NAMES = { CN: "中国", US: "美国", HK: "香港", TW: "台湾", JP: "日本", SG: "新加坡",
  DE: "德国", GB: "英国", FR: "法国", RU: "俄罗斯", KR: "韩国", CA: "加拿大", AU: "澳大利亚",
  NL: "荷兰", IN: "印度", BR: "巴西", VN: "越南", MY: "马来西亚", TH: "泰国", XX: "未知" };
function countryName(code) { return COUNTRY_NAMES[code] || code || "未知"; }

function fmtTime(ms) {
  if (!ms) return "—";
  const d = new Date(Number(ms));
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}

function relTime(ms) {
  if (!ms) return "—";
  const diff = Date.now() - Number(ms);
  if (diff < 60e3) return "刚刚";
  if (diff < 3600e3) return Math.floor(diff / 60e3) + " 分钟前";
  if (diff < 86400e3) return Math.floor(diff / 3600e3) + " 小时前";
  return Math.floor(diff / 86400e3) + " 天前";
}

function emptyRow(text, span) {
  return '<tr><td colspan="' + (span || 4) + '"><div class="empty">' + esc(text) + "</div></td></tr>";
}

/* ================= 分区渲染 ================= */
function renderSection(id) {
  renderNav();
  const sec = SECTIONS.find((s) => s.id === id) || SECTIONS[0];
  const render = RENDER[sec.id];
  $("#main").innerHTML = pageHead(sec.name, esc(sec.desc)) + render();
  updateTodayInputs();
}

const RENDER = {};

/* ---------- 总览 ---------- */
RENDER.overview = function () {
  const stats = S.stats || {};
  const trend = stats.trend || [];
  const counts = trend.map((t) => t.count);
  const peak = Math.max.apply(null, counts.concat([1]));
  const today = trend.length ? trend[trend.length - 1] : { date: "", count: stats.today || 0 };
  const blockCount = blocked().length;
  const maint = boolOf("maint_mode_live");
  const ai = S.ai || {};

  const bars = trend.map((item, index) => {
    const height = Math.round((item.count / peak) * 100);
    const isToday = index === trend.length - 1;
    return '<div class="col' + (isToday ? " today" : "") + '" title="' + esc(item.date) + "：" + item.count + ' 次">' +
      '<span class="n">' + item.count + "</span>" +
      '<span class="bar" style="height:' + Math.max(2, height) + '%"></span>' +
      '<span class="d">' + esc(item.date.slice(5)) + "</span></div>";
  }).join("");

  const countries = (stats.countries || []).map((c) => ({ name: countryName(c.code), count: c.count }));
  const hosts = (stats.hosts || []).slice(0, 8);
  const recent = (stats.recent || []).slice(0, 8);

  return card("概览", '<span class="chip plain" style="height:22px">近 7 天</span>',
      '<div class="kpi">' +
        kpi("今日访问", stats.today || 0, today.date || "") +
        kpi("7 天访问", stats.total || 0, "按次计") +
        kpi("独立访客", stats.unique || 0, "按 IP 去重") +
        kpi("封禁 IP", blockCount, blockCount ? "拒绝访问" : "空闲", blockCount ? "warn" : "") +
      "</div>", { cls: "" }) +

    '<div class="two">' +
      card("访问走势", "柱高按 7 天内峰值缩放",
        trend.length ? '<div class="bars">' + bars + "</div>" : '<div class="empty">还没有访问记录</div>') +
      card("地区分布", "",
        rankRows(countries, stats.total, countries.length ? countries[0].count : 1)) +
    "</div>" +

    '<div class="two">' +
      card("最近访客", "点右边可以直接封禁",
        recent.length ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>IP</th><th>地区</th><th>时间</th><th></th></tr></thead><tbody>' +
          recent.map((r) => '<tr><td class="mono">' + esc(r.ip) + "</td><td>" + esc(countryName(r.code)) +
            '</td><td class="num" title="' + esc(fmtTime(r.ts)) + '">' + esc(relTime(r.ts)) + "</td>" +
            '<td><button class="btn ghost sm" data-act="block-ip" data-ip="' + esc(r.ip) + '">封禁</button></td></tr>').join("") +
          "</tbody></table></div>" : '<div class="empty">还没有访问记录</div>') +
      card("快捷操作", "",
        '<div class="btn-row">' +
          '<button class="btn ' + (maint ? "danger" : "ghost") + '" data-act="' + (maint ? "maint-off" : "maint-on") + '">' +
            (maint ? "结束维护模式" : "开启维护模式") + "</button>" +
          '<button class="btn ghost" data-act="regen">重新生成 XAML</button>' +
          '<button class="btn ghost" data-act="clear-cache" data-target="all">清空缓存</button>' +
        "</div>" +
        '<div class="hint">维护模式会让除白名单外的访客看到"服务器正在更新"。</div>' +
        '<div class="kv">' +
          '<div class="i"><span>运行时长</span><b>' + esc(S.info.uptime || "—") + "</b></div>" +
          '<div class="i"><span>AI 调用</span><b>' + esc(ai.enabled ? (ai.calls || 0) + " 次" : "未开启") + "</b></div>" +
          '<div class="i"><span>公告</span><b>' + (bannerCount() ? bannerCount() + " 条轮播" : "未设置") + "</b></div>" +
          '<div class="i"><span>白名单</span><b>' + allowed().length + " 个 IP</b></div>" +
        "</div>") +
    "</div>";
};

/* ---------- 内容 ---------- */
RENDER.content = function () {
  const single = jsonOf("homepage_banner", {});
  const quote = textOf("quote_custom");
  const quoteLines = quote.split("\n").map((l) => l.trim()).filter(Boolean);
  const cd = jsonOf("custom_countdown", {});
  // 草稿优先：增删行之后先渲染草稿，保存时才落盘
  const fests = S._festivalDraft || jsonOf("custom_festivals", []);
  const lines = bannerLines();

  const bannerPreview = lines.length
    ? '<div class="note"><b>轮播预览</b>　' + lines.slice(0, 4).map((l, i) => (i + 1) + ". " + esc(l)).join("<br>") +
      (lines.length > 4 ? "<br>…共 " + lines.length + " 条，超过 4 条的部分不会显示" : "") + "</div>"
    : '<div class="hint">还没有多公告，至少 2 条才会轮播。</div>';

  const singlePreview = single && single.text
    ? '<div class="note"><b>单条公告</b>　' + (single.enabled ? "" : "<i>（已关闭）</i> ") + esc(single.text) + "</div>"
    : '<div class="hint">没有单条公告。</div>';

  const festRows = (Array.isArray(fests) ? fests : []).map((f, i) =>
    '<tr><td style="width:78px"><input class="fe-month" data-i="' + i + '" type="number" min="1" max="12" value="' + esc(f.month || "") + '"></td>' +
    '<td style="width:78px"><input class="fe-day" data-i="' + i + '" type="number" min="1" max="31" value="' + esc(f.day || "") + '"></td>' +
    '<td style="min-width:150px"><input class="fe-name" data-i="' + i + '" type="text" value="' + esc(f.name || "") + '" placeholder="节日名称"></td>' +
    '<td style="min-width:180px"><input class="fe-msg" data-i="' + i + '" type="text" value="' + esc(f.msg || "") + '" placeholder="祝福语"></td>' +
    '<td style="width:70px"><button class="btn ghost sm" data-act="del-festival" data-i="' + i + '">删</button></td></tr>').join("");

  const cdPreview = cd && cd.date
    ? (function () {
        const target = new Date(String(cd.date) + "T00:00:00");
        const days = Math.ceil((target - new Date(new Date().toDateString())) / 86400000);
        return '<div class="note"><b>' + esc(cd.name || "倒计时") + "</b>　目标 " + esc(cd.date) +
          (isNaN(days) ? "" : (days >= 0 ? "，还有 " + days + " 天" : "，已过去 " + (-days) + " 天")) + "</div>";
      })()
    : '<div class="hint">没有设置倒计时。</div>';

  return '<div class="two">' +
    '<div style="display:grid;gap:16px">' +
      card("多公告轮播", "每行一条，≥2 条生效，最多 4 条",
        '<div><textarea id="banners" rows="5" data-dirty="1" placeholder="第一条公告\n第二条公告">' +
          esc(textOf("banners")) + "</textarea>" +
          '<div class="hint" style="margin-top:6px">当前 ' + lines.length + " 条 · 每条显示 8 秒</div></div>" +
        '<div class="btn-row"><button class="btn" data-act="save-banners">保存公告</button>' +
          '<button class="btn ghost" data-act="reload">还原</button></div>') +
      card("单条公告", "≤40 字淡入，超长自动跑马灯",
        '<label class="switch"><input type="checkbox" id="bannerOn" data-dirty="1"' + (single && single.enabled ? " checked" : "") + '>' +
          '<span class="track"></span><span>启用单条公告</span></label>' +
        '<div><label>内容</label><input type="text" id="bannerText" data-dirty="1" maxlength="200" value="' +
          esc((single && single.text) || "") + '" placeholder="例如：25 号恢复 AI 分析"></div>' +
        '<div class="btn-row"><button class="btn" data-act="save-single-banner">保存</button></div>') +
      card("每日一言", "每行一条，支持 {date} {weekday} {year}",
        '<textarea id="quote" rows="4" data-dirty="1" placeholder="留空则调用接口，接口挂了用内置文案">' + esc(quote) + "</textarea>" +
        '<div class="hint">当前自定义 ' + quoteLines.length + " 条" + (quoteLines.length ? "，按日期轮换" : "（走接口）") + "</div>" +
        '<div class="btn-row"><button class="btn" data-act="save-quote">保存</button>' +
          '<button class="btn ghost" data-act="clear-quote">清空，改用接口</button></div>') +
    "</div>" +
    '<div style="display:grid;gap:16px">' +
      card("预览", "保存后生效", bannerPreview + singlePreview + cdPreview) +
      card("倒计时", "主页顶部会显示剩余天数",
        '<div><label>名称</label><input type="text" id="cdName" data-dirty="1" value="' + esc(cd.name || "") + '" placeholder="我的生日"></div>' +
        '<div><label>目标日期</label><input type="date" id="cdDate" data-dirty="1" value="' + esc(cd.date || "") + '"></div>' +
        '<div class="btn-row"><button class="btn" data-act="save-countdown">保存</button>' +
          '<button class="btn ghost" data-act="clear-countdown">清除</button></div>') +
    "</div></div>" +
    card("自定义节日", "按公历，每年同一天；主页倒计时/节日提示会用到",
      '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>月</th><th>日</th><th>名称</th><th>祝福语</th><th></th></tr></thead><tbody>' +
        (festRows || emptyRow("还没有自定义节日", 5)) + "</tbody></table></div>" +
      '<div class="btn-row"><button class="btn ghost" data-act="add-festival">添加一行</button>' +
        '<button class="btn" data-act="save-festivals">保存节日</button></div>');
};

/* ---------- 访客 ---------- */
RENDER.visitors = function () {
  const maint = boolOf("maint_mode_live");
  const blockList = blocked();
  const allowList = allowed();
  const recent = (S.stats.recent || []).slice(0, 10);
  const blockTags = blockList.length
    ? blockList.map((b) => '<span class="tag blocked">' + esc(b.ip) + " <small>" + esc(b.reason) + "</small>" +
        '<button class="x" data-act="unblock" data-ip="' + esc(b.ip) + '" title="解封">×</button></span>').join("")
    : '<span class="hint">没有封禁记录</span>';
  const allowTags = allowList.length
    ? allowList.map((ip) => '<span class="tag allowed">' + esc(ip) +
        '<button class="x" data-act="unallow" data-ip="' + esc(ip) + '" title="移出白名单">×</button></span>').join("")
    : '<span class="hint">白名单是空的（维护期间所有人都能看到更新页）</span>';

  return '<div class="two">' +
    '<div style="display:grid;gap:16px">' +
      card("维护模式", "开启后除白名单外都会看到「服务器正在更新」",
        '<label class="switch"><input type="checkbox" id="maintOn" data-dirty="1"' + (maint ? " checked" : "") + '>' +
          '<span class="track"></span><span>' + (maint ? "维护中" : "服务正常") + "</span></label>" +
        '<div class="rows">' +
          '<div><label>预计完成</label><input type="text" id="maintEta" data-dirty="1" value="' + esc(textOf("maint_eta")) + '" placeholder="10 分钟"></div>' +
          '<div><label>原因</label><input type="text" id="maintReason" data-dirty="1" value="' + esc(textOf("maint_reason")) + '" placeholder="升级数据库"></div>' +
        "</div>" +
        '<div class="btn-row"><button class="btn" data-act="save-maint">保存</button></div>') +
      card("封禁列表", "按 IP 拒绝访问，理由会记在后台",
        '<div class="tags">' + blockTags + "</div>" +
        '<div class="rows"><div><label>IP</label><input type="text" id="blockIp" placeholder="1.2.3.4"></div>' +
          '<div><label>原因</label><input type="text" id="blockReason" placeholder="刷屏"></div></div>' +
        '<div class="btn-row"><button class="btn danger" data-act="add-block">封禁</button>' +
          '<button class="btn ghost" data-act="block-from-text">从剪贴板批量添加</button></div>') +
    "</div>" +
    '<div style="display:grid;gap:16px">' +
      card("维护白名单", "维护期间这些 IP 仍可访问",
        '<div class="tags">' + allowTags + "</div>" +
        '<div class="rows"><div><label>添加 IP</label><input type="text" id="allowIp" placeholder="1.2.3.4"></div></div>' +
        '<div class="btn-row"><button class="btn" data-act="add-allow">加入白名单</button>' +
          '<button class="btn ghost" data-act="allow-from-text">批量添加</button></div>' +
        '<textarea id="allowBulk" class="code" rows="3" data-dirty="1" placeholder="也可以直接粘贴多行 IP，然后点上面的批量添加"></textarea>') +
      card("最近访客", "", recent.length
        ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>IP</th><th>地区</th><th>时间</th><th></th></tr></thead><tbody>' +
            recent.map((r) => '<tr><td class="mono">' + esc(r.ip) + "</td><td>" + esc(countryName(r.code)) +
              '</td><td class="num">' + esc(relTime(r.ts)) + "</td>" +
              '<td><button class="btn ghost sm" data-act="block-ip" data-ip="' + esc(r.ip) + '">封禁</button></td></tr>').join("") +
            "</tbody></table></div>"
        : '<div class="empty">还没有访问记录</div>') +
    "</div></div>";
};

/* ---------- AI ---------- */
RENDER.ai = function () {
  const ai = S.ai || {};
  if (!ai.enabled) {
    return card("AI 日志分析", "未开启",
      '<div class="note">config.json 里把 <b>enable_ai</b> 设为 true，并填上 <b>ai_api_key</b> 后重启服务才会生效。</div>');
  }
  const rows = ai.rows || [];
  const win = ai.window_text || (ai.window === "day" ? "每天" : "每小时");
  const period = ai.window === "day" ? "今天" : "本小时";
  const table = rows.length
    ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>IP</th><th>' + esc(period) + "用了</th><th>额度</th><th></th></tr></thead><tbody>" +
        rows.map((r) => '<tr><td class="mono">' + esc(r.ip) + '</td><td class="num">' + r.used + " / " + ai.limit +
          '</td><td><span class="tag" style="height:22px">' + (r.used >= ai.limit ? "已用完" : "可用") + "</span></td>" +
          '<td><button class="btn ghost sm" data-act="ai-reset-ip" data-ip="' + esc(r.ip) + '">重置</button></td></tr>').join("") +
        "</tbody></table></div>"
    : '<div class="empty">' + esc(period) + "还没有人用过</div>";

  return card("AI 分析", esc(ai.model || ""), '<div class="kpi">' +
      kpi(period + "调用", ai.calls || 0, "共 " + rows.length + " 个 IP") +
      kpi("运行中", ai.running || 0, "正在分析") +
      kpi("留存记录", ai.jobs || 0, "分析结果") +
      kpi("私人密钥", ai.own_keys || 0, "访客自带") +
    "</div>" +
    '<div class="hint">接口 <b>' + esc(ai.base || "") + "</b> · 每个 IP " + esc(win) + " " + ai.limit + " 次 · 时间窗 " + esc(ai.bucket || "") + "</div>" +
    (rows.length ? table : "") +
    '<div class="btn-row" style="margin-top:4px">' +
      '<button class="btn ghost" data-act="ai-reset" data-scope="quota">重置' + esc(period) + "用量</button>" +
      '<button class="btn ghost" data-act="ai-reset" data-scope="jobs">清空分析记录</button>' +
      '<button class="btn ghost" data-act="ai-reset" data-scope="keys">清空私人密钥</button>' +
      '<button class="btn danger" data-act="ai-reset" data-scope="all">全部重置</button>' +
    "</div>", { tight: false });
};

/* ---------- 系统 ---------- */
RENDER.system = function () {
  const info = S.info || {};
  const paths = info.paths || {};
  const features = info.features || {};
  const featureRows = [
    ["enable_weather", "天气", Boolean(features["天气"])],
    ["enable_wallpaper", "必应每日壁纸", Boolean(features["壁纸"])],
    ["enable_saying", "每日一言接口", Boolean(features["每日一言"])],
    ["enable_lunar", "农历节气", Boolean(features["农历"])],
    ["enable_geo", "按 IP 定位城市", Boolean(features["IP 归属地"])],
    ["guard_clients", "来源守卫（拦爬虫 UA）", Boolean(S.config.guard_clients)],
  ].map(([key, name, on]) =>
    '<label class="switch" style="justify-content:space-between;width:100%">' +
      '<span>' + esc(name) + "</span>" +
      '<input type="checkbox" data-feature="' + key + '" data-dirty="1"' + (on ? " checked" : "") + ">" +
      '<span class="track"></span></label>').join("");

  const report = S.report;
  const reportHtml = report
    ? '<div class="rep-sum">' +
        '<span class="chip ok">通过 ' + report.ok + "</span>" +
        '<span class="chip ' + (report.warn ? "warn" : "plain") + '">警告 ' + report.warn + "</span>" +
        '<span class="chip ' + (report.fail ? "bad" : "plain") + '">失败 ' + report.fail + "</span>" +
        '<span class="hint">' + (report.took_ms ? "耗时 " + (report.took_ms / 1000).toFixed(1) + "s · " : "") +
          "结果缓存 60 秒</span></div>" +
      '<div class="report" style="margin-top:10px">' + (report.sections || []).map((sec, index) => {
        const failed = sec.items.filter((i) => i.level === "fail").length;
        return '<details class="rep-sec"' + (failed ? " open" : "") + ">" +
          "<summary>" + esc(sec.title) + '<span class="cnt">' + sec.items.length + " 项" +
            (failed ? " · " + failed + " 失败" : "") + "</span></summary>" +
          '<div class="rep-list">' + sec.items.map((item) =>
            '<div class="rep-item ' + (item.level === "ok" ? "ok" : item.level === "warn" ? "warn" : "fail") + '">' +
            '<span class="dot"></span><span>' + esc(item.msg) + "</span></div>").join("") + "</div></details>";
      }).join("") + "</div>"
    : '<div class="empty">还没跑过体检</div>';

  const logLines = S.log && S.log.lines || [];
  const logTail = S.log && S.log.available
    ? '<pre class="log" id="logBox">' + esc(logLines.join("\n")) + "</pre>"
    : '<div class="empty">' + esc((S.log && S.log.hint) || "点「刷新」读取服务日志") + "</div>";

  return card("服务信息", "", '<div class="kv">' +
      '<div class="i"><span>版本</span><b>' + esc(info.version || "—") + "</b></div>" +
      '<div class="i"><span>运行时长</span><b>' + esc(info.uptime || "—") + "</b></div>" +
      '<div class="i"><span>监听</span><b>' + esc(info.listen || "—") + "</b></div>" +
      '<div class="i"><span>站点地址</span><b>' + esc(info.base_url || "—") + "</b></div>" +
      '<div class="i"><span>Python</span><b>' + esc(info.python || "—") + "</b></div>" +
      '<div class="i"><span>后台会话</span><b>' + esc(info.sessions || 0) + " 个</b></div>" +
      '<div class="i"><span>store.json</span><b>' + esc(paths.store_size || "—") + " · " + esc(paths.store_mtime || "") + "</b></div>" +
      '<div class="i"><span>日志</span><b>' + esc(paths.log_size || "—") + "</b></div>" +
      '<div class="i"><span>项目目录</span><b title="' + esc(paths.root || "") + '">' + esc(paths.root || "—") + "</b></div>" +
    "</div>" +
    '<div class="hint">密钥脱敏显示：' + (Object.entries(info.secrets || {}).map(([k, v]) => esc(k) + " " + esc(v || "（空）")).join("　")) + "</div>") +

    card("功能开关", "改完立即写入 config.json",
      featureRows + '<div class="btn-row"><button class="btn" data-act="save-features">保存开关</button>' +
      '<span class="hint">壁纸、天气等改动要重新生成 XAML 才会反映到主页</span></div>') +

    card("站点设置", "写入 config.json",
      '<div class="rows">' +
        '<div><label>站点名称</label><input type="text" id="siteName" data-dirty="1" maxlength="40" value="' +
          esc(S.config.site_name || "") + '" placeholder="PCL 主页"></div>' +
        '<div><label>天气城市</label><input type="text" id="weatherCity" data-dirty="1" maxlength="40" value="' +
          esc(S.config.weather_city || "") + '" placeholder="留空则按访客 IP 定位"></div>' +
      "</div>" +
      '<div class="btn-row"><button class="btn" data-act="save-site">保存</button>' +
        '<span class="hint">站点名称改动后需「重新生成 XAML」才会反映到主页</span></div>') +

    card("系统体检", "跟命令行 <span class='mono'>python -m pclhome doctor --offline</span> 同一套检查",
      '<div class="btn-row"><button class="btn" data-act="run-doctor">' +
        (report ? "重新体检" : "开始体检") + "</button>" +
        (report ? '<button class="btn ghost" data-act="run-doctor" data-force="1">强制刷新</button>' : "") +
      "</div>" + reportHtml) +

    card("服务日志", "最近若干行，只读",
      '<div class="btn-row">' +
        '<select id="logLines" style="width:140px">' +
          [100, 200, 400, 800].map((n) => '<option value="' + n + '"' + ((S.log && S.log.want) === n ? " selected" : "") + ">" + n + " 行</option>").join("") +
        "</select>" +
        '<button class="btn ghost" data-act="refresh-log">刷新</button>' +
        '<span class="hint">' + (S.log && S.log.mtime ? "更新于 " + esc(S.log.mtime) : "") + "</span>" +
      "</div>" + logTail) +

    card("缓存与模板", "",
      '<div class="btn-row">' +
        '<button class="btn ghost" data-act="clear-cache" data-target="all">清空全部缓存</button>' +
        '<button class="btn ghost" data-act="clear-cache" data-target="saying">每日一言</button>' +
        '<button class="btn ghost" data-act="clear-cache" data-target="lunar">农历</button>' +
        '<button class="btn ghost" data-act="clear-cache" data-target="wallpaper">壁纸</button>' +
        '<button class="btn ghost" data-act="clear-cache" data-target="geo">IP 归属地</button>' +
        '<button class="btn ghost" data-act="clear-cache" data-target="weather">天气</button>' +
      "</div>" +
      '<div class="btn-row">' +
        '<button class="btn" data-act="regen">重新生成 XAML</button>' +
        '<button class="btn ghost" data-act="preview-xaml">预览渲染结果</button>' +
      "</div>" +
      '<div id="previewBox"></div>');
};

/* ---------- 数据 ---------- */
RENDER.data = function () {
  const keys = Object.keys(S.store || {}).length;
  const info = S.info || {};
  return card("store.json 原始编辑", "整体替换，保存前自动备份",
      '<div class="note">这里直接改整个 store.json（后台所有可写配置都在里面）。保存时会先备份到 ' +
        '<span class="mono">var/store.backup.json</span>，JSON 不合法不会写盘。</div>' +
      '<textarea id="storeText" class="code" rows="18" data-dirty="1" spellcheck="false">' + esc(S.storeText || JSON.stringify(S.store, null, 2)) + "</textarea>" +
      '<div class="btn-row">' +
        '<button class="btn" data-act="save-store">保存并应用</button>' +
        '<button class="btn ghost" data-act="format-store">格式化</button>' +
        '<button class="btn ghost" data-act="reload-store">重新加载</button>' +
        '<span class="hint" id="storeHint">共 ' + keys + " 个键</span>" +
      "</div>" +
      '<div id="storeError"></div>') +

    card("导出与导入", "",
      '<div class="btn-row">' +
        '<button class="btn ghost" data-act="export-store">导出 store.json</button>' +
        '<label class="btn ghost" style="cursor:pointer;margin:0">导入 JSON<input type="file" id="importFile" accept=".json,application/json" class="hidden"></label>' +
      "</div>" +
      '<div class="hint">导入会整体替换现有配置，先导出留个底。</div>') +

    card("安全", "退出其它设备上的后台会话",
      '<div class="kv"><div class="i"><span>当前会话</span><b>8 小时有效</b></div>' +
        '<div class="i"><span>在线会话</span><b>' + esc(info.sessions || 1) + " 个</b></div>" +
        '<div class="i"><span>管理令牌</span><b>' + esc((info.secrets || {}).admin_token || "—") + "</b></div></div>" +
      '<div class="btn-row"><button class="btn ghost" data-act="revoke-sessions">退出其它会话</button>' +
        '<button class="btn" data-act="logout">退出登录</button></div>' +
      '<div class="hint">发现令牌可能泄露时，改 config.json 里的 admin_token 并重启服务。</div>') +

    card("配置项清单", "只读",
      '<div class="tags">' + Object.keys(S.store || {}).sort().map((k) =>
        '<span class="tag" style="height:24px;padding:0 10px">' + esc(k) + "</span>").join("") + "</div>");
};

/* ================= 交互 ================= */
async function withBusy(button, task) {
  const old = button ? button.innerHTML : null;
  if (button) {
    button.disabled = true;
    button.innerHTML = '<span class="spin"></span>' + (button.dataset.label || "处理中");
  }
  try {
    return await task();
  } finally {
    if (button) { button.disabled = false; button.innerHTML = old; }
  }
}

async function saveItems(items, label) {
  await api("set_batch", { items });
  toast((label || "已保存"), "ok");
  await refresh();
}

async function handleAction(act, el) {
  switch (act) {
    case "reload":
      await refresh();
      toast("已还原", "ok");
      return;
    case "logout":
      await api("logout", {});
      showLogin();
      return;
    case "save-banners": {
      const text = $("#banners").value.replace(/\r\n/g, "\n");
      await saveItems({ banners: text }, "公告已保存");
      return;
    }
    case "save-single-banner":
      await saveItems({ homepage_banner: { enabled: $("#bannerOn").checked, text: $("#bannerText").value } }, "单条公告已保存");
      return;
    case "save-quote":
      await saveItems({ quote_custom: $("#quote").value }, "每日一言已保存");
      return;
    case "clear-quote":
      if (!(await confirmDialog({ title: "清空自定义文案", body: "清空后每日一言会改回调用接口。", okText: "清空" }))) return;
      await api("delete", { key: "quote_custom" });
      toast("已清空", "ok");
      await refresh();
      return;
    case "save-countdown":
      await saveItems({ custom_countdown: { name: $("#cdName").value, date: $("#cdDate").value } }, "倒计时已保存");
      return;
    case "clear-countdown":
      await api("delete", { key: "custom_countdown" });
      toast("倒计时已清除", "ok");
      await refresh();
      return;
    case "save-festivals": {
      const fests = collectFestivals().filter((f) => f.name && f.month && f.day);
      await saveItems({ custom_festivals: fests }, "节日已保存（" + fests.length + " 条）");
      S._festivalDraft = null;
      return;
    }
    case "save-maint":
      await api("maintenance", { on: $("#maintOn").checked, eta: $("#maintEta").value, reason: $("#maintReason").value });
      toast($("#maintOn").checked ? "维护模式已开启" : "维护设置已保存", "ok");
      await refresh();
      return;
    case "maint-on":
    case "maint-off": {
      const on = act === "maint-on";
      if (on) {
        const ok = await confirmDialog({
          title: "开启维护模式",
          body: "除白名单外的访客都会看到「服务器正在更新」，确定要开启吗？",
          okText: "开启",
        });
        if (!ok) return;
      }
      await api("maintenance", { on, eta: textOf("maint_eta"), reason: textOf("maint_reason") });
      toast(on ? "维护模式已开启" : "维护模式已关闭", "ok");
      await refresh();
      return;
    }
    case "add-block": {
      const ip = $("#blockIp").value.trim();
      if (!ip) return toast("先填 IP", "warn");
      await api("block", { ip, reason: $("#blockReason").value || "手动封禁" });
      toast("已封禁 " + ip, "ok");
      await refresh();
      return;
    }
    case "block-ip": {
      const ip = el.dataset.ip;
      if (!(await confirmDialog({ title: "封禁 IP", body: "封禁 " + ip + "？该 IP 之后会看到拒绝访问页。", okText: "封禁", danger: true }))) return;
      await api("block", { ip, reason: "从最近访客封禁" });
      toast("已封禁 " + ip, "ok");
      await refresh();
      return;
    }
    case "block-from-text": {
      const text = window.prompt("粘贴要封禁的 IP（每行一个，可用逗号分隔）");
      if (!text) return;
      const res = await api("block_batch", { ips: text.split(/[\s,]+/).filter(Boolean), reason: "批量封禁" });
      toast("已封禁 " + res.count + " 个 IP", "ok");
      await refresh();
      return;
    }
    case "unblock": {
      const ip = el.dataset.ip;
      await api("block", { ip, remove: true });
      toast("已解封 " + ip, "ok");
      await refresh();
      return;
    }
    case "add-allow": {
      const ip = $("#allowIp").value.trim();
      if (!ip) return toast("先填 IP", "warn");
      await api("whitelist", { ips: [ip] });
      toast("已加入白名单", "ok");
      await refresh();
      return;
    }
    case "unallow":
      await api("whitelist", { ips: [el.dataset.ip], remove: true });
      toast("已移出白名单", "ok");
      await refresh();
      return;
    case "allow-from-text": {
      const text = ($("#allowBulk") && $("#allowBulk").value) || "";
      const ips = text.split(/[\s,]+/).filter(Boolean);
      if (!ips.length) return toast("先粘贴 IP", "warn");
      const res = await api("whitelist", { ips });
      toast("已加入 " + res.count + " 个 IP", "ok");
      await refresh();
      return;
    }
    case "ai-reset": {
      const scope = el.dataset.scope;
      const names = { quota: "本时间窗用量", jobs: "分析记录", keys: "私人密钥", all: "全部 AI 数据" };
      if (!(await confirmDialog({ title: "重置 AI 数据", body: "确定清空" + (names[scope] || scope) + "？", okText: "重置", danger: true }))) return;
      const res = await api("ai_reset", { scope });
      toast("已重置：" + JSON.stringify(res.result), "ok");
      await refresh();
      return;
    }
    case "ai-reset-ip": {
      const ip = el.dataset.ip;
      if (!(await confirmDialog({ title: "重置这个 IP", body: "把 " + ip + " 的用量、记录和私人密钥全部清掉？", okText: "重置", danger: true }))) return;
      const res = await api("ai_reset", { scope: "all", ip });
      toast("已重置：" + JSON.stringify(res.result), "ok");
      await refresh();
      return;
    }
    case "save-features": {
      const updates = {};
      $$("[data-feature]").forEach((input) => { updates[input.dataset.feature] = input.checked; });
      const res = await api("config_set", { updates });
      toast("已写入 config.json：" + res.keys.join("、"), "ok");
      await refresh();
      return;
    }
    case "save-site": {
      const name = ($("#siteName").value || "").trim();
      const city = ($("#weatherCity").value || "").trim();
      if (!name) { toast("站点名称不能为空", "bad"); return; }
      const updates = {};
      if (name !== (S.config.site_name || "")) updates.site_name = name;
      if (city !== (S.config.weather_city || "")) updates.weather_city = city;
      if (!Object.keys(updates).length) { toast("没有改动", "warn"); return; }
      const res = await api("config_set", { updates });
      toast("已写入 config.json：" + res.keys.join("、"), "ok");
      await refresh();
      return;
    }
    case "run-doctor": {
      S.reportLoading = true;
      $("#main").querySelector('[data-act="run-doctor"]');
      await withBusy(el, async () => {
        const res = await api("doctor", el.dataset.force ? { force: true } : {});
        S.report = res.report;
        toast("体检完成：通过 " + res.report.ok + " / 警告 " + res.report.warn + " / 失败 " + res.report.fail,
          res.report.fail ? "warn" : "ok");
      });
      renderSection("system");
      return;
    }
    case "refresh-log": {
      const want = Number($("#logLines") ? $("#logLines").value : 200) || 200;
      S.log = Object.assign({ want }, await api("log?lines=" + want));
      renderSection("system");
      const box = $("#logBox");
      if (box) box.scrollTop = box.scrollHeight;
      return;
    }
    case "clear-cache": {
      const res = await withBusy(el, () => api("cache", { target: el.dataset.target }));
      toast("已清理：" + (res.cleared.join("、") || "无内容可清"), "ok");
      return;
    }
    case "regen":
      await withBusy(el, () => api("regenerate"));
      toast("已重新生成 XAML", "ok");
      return;
    case "preview-xaml": {
      const res = await withBusy(el, () => api("preview"));
      const box = $("#previewBox");
      if (box) {
        box.innerHTML = '<div class="hint" style="margin-top:8px">渲染结果 ' + res.xaml.length +
          ' 字节（只读预览）</div><textarea class="code" rows="14" readonly>' + esc(res.xaml.slice(0, 20000)) + "</textarea>";
      }
      return;
    }
    case "save-store": {
      let parsed;
      try {
        parsed = JSON.parse($("#storeText").value);
      } catch (e) {
        $("#storeError").innerHTML = '<div class="note bad">JSON 格式有误：' + esc(e.message) + "</div>";
        return;
      }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        $("#storeError").innerHTML = '<div class="note bad">顶层必须是一个对象 { ... }</div>';
        return;
      }
      if (!(await confirmDialog({
        title: "替换 store.json",
        body: "会用编辑器里的内容整体替换后台配置（" + Object.keys(parsed).length + " 个键）。旧内容备份到 var/store.backup.json。",
        okText: "保存并应用",
      }))) return;
      await api("store_set", parsed);
      S.storeText = null;
      toast("已保存并备份", "ok");
      await refresh();
      return;
    }
    case "format-store": {
      try {
        $("#storeText").value = JSON.stringify(JSON.parse($("#storeText").value), null, 2);
        $("#storeError").innerHTML = "";
        toast("已格式化", "ok");
      } catch (e) {
        $("#storeError").innerHTML = '<div class="note bad">JSON 格式有误：' + esc(e.message) + "</div>";
      }
      return;
    }
    case "reload-store": {
      const res = await api("store_get");
      S.storeText = res.text;
      renderSection("data");
      toast("已重新加载", "ok");
      return;
    }
    case "export-store": {
      const res = await fetch("/admin/api/export");
      if (!res.ok) return toast("导出失败（HTTP " + res.status + "）", "bad");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "pclhome-store-" + new Date().toISOString().slice(0, 10) + ".json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 3000);
      toast("已导出", "ok");
      return;
    }
    case "revoke-sessions": {
      if (!(await confirmDialog({ title: "退出其它会话", body: "其它设备上的后台登录会立即失效，当前会话保留。", okText: "退出其它", danger: true }))) return;
      const res = await api("sessions", { action: "revoke_others" });
      toast("已退出 " + res.removed + " 个会话", "ok");
      await refresh();
      return;
    }
    default:
      toast("未知操作：" + act, "bad");
  }
}

function collectFestivals() {
  // 永远以 DOM 为准：草稿只是"还没保存的行"，用户在这些行里改的字也要带上
  const rows = $$("#main .tbl tbody tr").filter((tr) => $(".fe-name", tr));
  return rows.map((tr) => ({
    month: Number(($(".fe-month", tr) || {}).value || 0),
    day: Number(($(".fe-day", tr) || {}).value || 0),
    name: (($(".fe-name", tr) || {}).value || "").trim(),
    msg: (($(".fe-msg", tr) || {}).value || "").trim(),
  }));
}

/* 事件委托：所有按钮走 data-act，输入框只标脏 */
document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-nav]");
  if (nav) { gotoSection(nav.dataset.nav); return; }
  const el = event.target.closest("[data-act]");
  if (!el) return;
  const act = el.dataset.act;
  if (act === "add-festival" || act === "del-festival") {
    // 先把手上的编辑内容收进草稿，避免重渲染丢掉
    S._festivalDraft = collectFestivals();
    if (act === "add-festival") S._festivalDraft.push({ month: 1, day: 1, name: "", msg: "" });
    else S._festivalDraft = S._festivalDraft.filter((_, i) => String(i) !== String(el.dataset.i));
    renderSection("content");
    return;
  }
  try {
    await handleAction(act, el);
  } catch (err) {
    toast(err.message || String(err), "bad");
  }
});

document.addEventListener("input", (event) => {
  const target = event.target;
  if (target && target.dataset && target.dataset.dirty) setDirty(true);
  if (target && target.id === "storeText") S.storeText = target.value;
  if (target && target.id === "banners") {
    const lines = target.value.split("\n").filter((l) => l.trim()).length;
    const hint = target.parentElement && target.parentElement.querySelector(".hint");
    if (hint) hint.textContent = "当前 " + lines + " 条 · 每条显示 8 秒";
  }
});

document.addEventListener("change", (event) => {
  const input = event.target;
  if (input && input.id === "importFile" && input.files && input.files[0]) {
    const file = input.files[0];
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const parsed = JSON.parse(String(reader.result));
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("顶层必须是对象");
        if (!(await confirmDialog({
          title: "导入 store.json",
          body: "用「" + file.name + "」整体替换现有配置（" + Object.keys(parsed).length + " 个键）？",
          okText: "导入",
        }))) { input.value = ""; return; }
        await api("store_set", parsed);
        toast("已导入", "ok");
        await refresh();
      } catch (err) {
        toast("导入失败：" + (err.message || err), "bad");
      }
      input.value = "";
    };
    reader.readAsText(file, "utf-8");
  }
  if (input && input.closest(".modal")) return;
  if (input && (input.dataset && input.dataset.dirty)) setDirty(true);
});

/* 键盘：Ctrl/⌘+S 触发当前页第一个主按钮，Esc 关弹窗 */
document.addEventListener("keydown", async (event) => {
  const meta = event.ctrlKey || event.metaKey;
  if (meta && event.key.toLowerCase() === "s") {
    event.preventDefault();
    const btn = $("#main .btn:not(.ghost)") || $("#main .btn");
    if (btn && !btn.disabled) btn.click();
  }
  if (meta && event.key.toLowerCase() === "k") {
    event.preventDefault();
    toast("快捷键：Ctrl+S 保存当前页，Alt+1~6 切换分区", "ok");
  }
});

document.addEventListener("keydown", (event) => {
  if (event.altKey && /^[1-6]$/.test(event.key)) {
    const sec = SECTIONS[Number(event.key) - 1];
    if (sec) gotoSection(sec.id);
  }
});

/* ================= 主题 / 登录绑定 ================= */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("admin-theme", theme);
  const btn = $("#themeBtn");
  if (btn) btn.textContent = theme === "light" ? "浅色" : "深色";
}

$("#themeBtn").onclick = () => {
  applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
};

$("#loginBtn").onclick = async () => {
  const btn = $("#loginBtn");
  await withBusy(btn, async () => {
    try {
      await api("login", { token: $("#token").value });
      $("#token").value = "";
      showPanel();
      await refresh();
      const hash = (location.hash || "").replace("#/", "");
      if (SECTIONS.some((s) => s.id === hash)) { S.section = hash; renderSection(hash); }
      toast("欢迎回来", "ok");
    } catch (err) {
      toast(err.message || "登录失败", "bad");
    }
  });
};
$("#token").addEventListener("keydown", (event) => { if (event.key === "Enter") $("#loginBtn").click(); });
$("#logoutBtn").onclick = () => handleAction("logout").catch((e) => toast(e.message, "bad"));

/* ================= 启动 ================= */
(function boot() {
  applyTheme(localStorage.getItem("admin-theme") || "dark");
  S.section = (location.hash || "").replace("#/", "") || "overview";
  if (!SECTIONS.some((s) => s.id === S.section)) S.section = "overview";
  fetch("/admin/api/state").then((res) => {
    if (!res.ok) { showLogin(); return null; }
    return res.json();
  }).then((json) => {
    if (!json || !json.ok) { showLogin(); return; }
    S.store = json.store || {};
    S.config = json.config || {};
    S.info = json.info || {};
    S.ai = json.ai || {};
    S.stats = json.stats || {};
    showPanel();
    renderAll();
    autoRefresh();
  }).catch(() => showLogin());

  window.addEventListener("beforeunload", (event) => {
    if (S.dirty) { event.preventDefault(); event.returnValue = ""; }
  });
})();

let refreshTimer = null;
function autoRefresh() {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    if (!S.loggedIn || S.dirty) return;
    if (document.visibilityState !== "visible") return;
    if (!["overview", "visitors"].includes(S.section)) return;
    refresh(false).then(() => {
      if (!S.dirty) renderSection(S.section);
    }).catch(() => {});
  }, 30000);
}

function updateTodayInputs() {
  const dateInput = $("#cdDate");
  if (dateInput && !dateInput.value) dateInput.max = "2100-12-31";
}
</script>
</body>
</html>
"""
