# -*- coding: utf-8 -*-
"""主页服务（替代上游的 Cloudflare Pages Functions 中间件）。

路由与上游 _middleware.js 一一对应：

    /Custom.xaml、/            主页：按 IP + 北京时间个性化后返回
    /Custom.xaml.version       每次返回新时间戳，逼 PCL 重新拉主页
    /Custom.xaml.ini、/version 同上（兼容 PCL 的不同请求姿势）
    /images/…、/music/…      静态资源
    /admin、/admin/api/…       管理后台（见 admin.py）

任何组装异常都回退成"服务器正在更新"兜底页，保证 PCL 不会拿到空白页。
"""
from __future__ import annotations

import json
import mimetypes
import posixpath
import re
import sys
import threading
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import admin as admin_module
from . import log
from .config import ROOT, STATIC_DIR, TEMPLATES_DIR, Config, load_config
from .generate import generate
from .render import build_home_data, render_homepage
from .store import Stats, Store
from .weather import WeatherService
from . import ai
from .xaml import build_fallback_xaml

# 需要"仅 PCL 客户端与真实浏览器可访问"的动态数据端点
DATA_ENDPOINTS = {"/Custom.xaml", "/"}
VERSION_PATHS = {"/Custom.xaml.version", "/Custom.xaml.ini", "/version"}

BASE_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # 本地反代时常放在内网，这里只做最基础的隔离提示
    "Cross-Origin-Resource-Policy": "same-site",
}

XAML_HEADERS = {
    "Content-Type": "application/xml; charset=utf-8",
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, s-maxage=0",
    "Pragma": "no-cache",
    "Expires": "0",
    **BASE_SECURITY_HEADERS,
}

# 静态资源的缓存时长（秒），对应上游 _headers 的规则
STATIC_CACHE_RULES = (
    ("/images/icons/", 2592000),
    ("/music/music.json", 60),
    ("/music/", 604800),
    ("/favicon.png", 604800),
)


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    headers: dict = field(default_factory=dict)

    @classmethod
    def text(cls, body: str, content_type: str = "text/plain; charset=utf-8", **kwargs):
        return cls(body=body.encode("utf-8"), content_type=content_type, **kwargs)

    @classmethod
    def xaml(cls, body: str, vary_ip: bool = False):
        headers = dict(XAML_HEADERS)
        if vary_ip:
            headers["Vary"] = "CF-Connecting-IP, X-Forwarded-For"
        return cls(body=body.encode("utf-8"), content_type=XAML_HEADERS["Content-Type"], headers=headers)


def is_trusted_client(user_agent: str, referer: str = "") -> bool:
    """只放行 PCL 客户端与真实浏览器，拦掉扫描器/爬虫。

    与上游 isTrustedClient 同一套规则：先认 PCL2，再认 PCL 帮助页的 Referer，
    空 UA 一律拒，常见爬虫/脚本库 UA 一律拒，其余带 Mozilla 的放行。
    """
    ua = user_agent or ""
    if re.search(r"PCL2", ua, re.I):
        return True
    if re.search(r"pcl2\.server|pcl2\.open\.server", referer or "", re.I):
        return True
    if not ua:
        return False
    bad = (r"bot|crawler|spider|scrapy|python-requests|python-urllib|aiohttp|httpx|python/|curl/|wget|"
           r"go-http-client|okhttp|libwww|httpclient|node-fetch|axios|headless|facebookexternalhit|"
           r"petalbot|semrush|ahrefs|mj12|dotbot|censys|zmap|masscan|nmap|zgrab|java/|ruby|php/")
    if re.search(bad, ua, re.I):
        return False
    return bool(re.search(r"mozilla", ua, re.I))


class HomepageService:
    """把配置、存储、天气、模板缓存和渲染逻辑收在一起（线程安全）。"""

    def __init__(self, config: Config | None = None):
        self.config = config or load_config()
        self.store = Store()
        self.stats = Stats()
        self.weather = WeatherService(self.config)
        self._templates: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self._ensure_outputs()

    # ---- 模板读取（带 mtime 缓存，改模板不用重启）----

    def _read(self, path: Path) -> str:
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._templates.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        text = path.read_text(encoding="utf-8")
        with self._lock:
            self._templates[str(path)] = (mtime, text)
        return text

    def _ensure_outputs(self) -> None:
        """首次启动时若还没有生成物，先离线生成一份，避免主页 404。"""
        needed = ["Custom.xaml"]
        if all((ROOT / name).exists() for name in needed):
            return
        print("[Server] 未找到生成物，先执行一次离线生成……")
        generate(self.config, offline=True)

    def static_template(self, name: str) -> str:
        return self._read(ROOT / name)

    # ---- 主页 ----

    def homepage(self, ip: str, origin: str):
        """返回 ``(渲染好的 XAML, HomeData)``——后者给日志用。"""
        data = build_home_data(self.config, self.store, self.weather, ip, origin)
        text = render_homepage(self.static_template("Custom.xaml"), data, self.config, origin)
        return text, data

    # ---- 拦截：封禁 / 维护 ----

    def guard_block(self, ip: str) -> str | None:
        """返回拦截页 XAML；放行则返回 None。"""
        if self.store.is_blocked(ip):
            return build_fallback_xaml("访问被拒绝", "你的 IP 已被管理员禁止访问本主页。")

        maint = self.store.maintenance()
        if maint["on"] and ip not in maint["whitelist"]:
            return build_fallback_xaml("服务器正在更新",
                                       "服务器正在更新中，请稍后刷新重试。",
                                       maint["eta"], maint["reason"])
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "PCLHomepagePy/1.0"
    protocol_version = "HTTP/1.1"

    service: HomepageService = None        # 由 serve() 注入

    # ---- 基础工具 ----

    def log_message(self, fmt, *args):
        # 自己记请求日志（见 _log_request），屏蔽 http.server 的原生输出
        log.debug("[HTTP] " + (fmt % args))

    def _client_ip(self) -> str:
        cfg = self.service.config
        if cfg.trust_proxy_headers:
            for header in ("CF-Connecting-IP", "X-Real-IP"):
                value = self.headers.get(header)
                if value:
                    return value.strip()
            forwarded = self.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "unknown"

    def _origin(self) -> str:
        """推导本站地址：优先配置，其次反代头，最后用请求的 Host。"""
        cfg = self.service.config
        if cfg.base_url:
            return cfg.resolved_base_url()
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
        if not proto:
            proto = "https" if self.headers.get("X-Forwarded-Ssl") == "on" else "http"
        host = self.headers.get("Host") or (cfg.host + ":" + str(cfg.port))
        return proto + "://" + host

    def _send(self, response: Response) -> None:
        body = response.body
        self._status = response.status
        self._size = len(body)
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        """后台 API 用 POST 提交配置；主页本身只有 GET。"""
        self._dispatch()

    def _dispatch(self):
        """处理一次请求，并打出一行明细日志。

        日志形如：
            23:12:45.123 [INFO] [HTTP] 203.0.113.7 GET /Custom.xaml → 200 9542B 87ms
                                 | 问候=晚上好 一言=api 天气=泉州市 · 晋江市 农历=农历八月十五 节日=中秋节 公告=无
        """
        self._status = 0
        self._size = 0
        self._note = ""
        self._force_warn = False
        self._path = self.path
        try:
            self._log_ip = self._client_ip()
        except Exception:
            self._log_ip = "-"
        started = time.perf_counter()
        try:
            self._route()
        except Exception:
            log.error("[Server] 请求处理异常：\n" + traceback.format_exc())
            self._send(Response.xaml(build_fallback_xaml(
                "服务器正在更新", "服务器正在更新中，请稍后刷新重试。")))
        finally:
            self._log_request(started)

    def _log_request(self, started) -> None:
        elapsed = (time.perf_counter() - started) * 1000
        path = self._path if len(self._path) <= 60 else self._path[:57] + "…"
        line = ("[HTTP] " + self._log_ip + " " + self.command + " " + path
                + " → " + str(self._status) + " " + str(self._size) + "B "
                + str(round(elapsed)) + "ms")
        if self._note:
            line += "\n                              | " + self._note
        # 4xx/5xx 与拦截类响应用 WARN，方便一眼扫到异常
        level = log.WARN if (self._status >= 400 or self._force_warn) else log.INFO
        log.log(level, line)

    # ---- 路由 ----

    def _route(self):
        service = self.service
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        ua = self.headers.get("User-Agent") or ""
        referer = self.headers.get("Referer") or ""

        if path.startswith("/admin"):
            self._note = "后台"
            self._send(admin_module.handle(service, self, path, parsed.query))
            return

        if path in ai.AI_PATHS:
            if service.config.guard_clients and not is_trusted_client(ua, referer):
                self._force_warn = True
                self._note = "来源守卫拦截（AI 接口）：UA=" + (ua[:60] or "（空）")
                self._send(Response.xaml(build_fallback_xaml(
                    "访问被拒绝", "检测到异常访问（爬虫或扫描器）。如需使用本主页，请在 PCL2 启动器中打开。")))
                return
            response = ai.handle(service, path, parsed.query, self._log_ip, self._origin())
            if response is None:
                self._send(Response(404, b"Not Found", "text/plain; charset=utf-8"))
                return
            self._note = "AI 日志分析：" + path
            self._send(response)
            return

        if self.command not in ("GET", "HEAD"):
            self._note = "方法不允许（只支持 GET/HEAD）"
            self._send(Response(405, b"Method Not Allowed", content_type="text/plain; charset=utf-8",
                                headers={"Allow": "GET, HEAD", **BASE_SECURITY_HEADERS}))
            return

        if service.config.guard_clients and (path in DATA_ENDPOINTS or path in VERSION_PATHS):
            if not is_trusted_client(ua, referer):
                self._force_warn = True
                self._note = ("来源守卫拦截：UA=" + (ua[:60] or "（空）")
                              + ("  Referer=" + referer[:40] if referer else ""))
                self._send(Response.xaml(build_fallback_xaml(
                    "访问被拒绝", "检测到异常访问（爬虫或扫描器）。如需使用本主页，请在 PCL2 启动器中打开。")))
                return

        if path in VERSION_PATHS:
            self._note = "版本号（每次返回新时间戳，逼 PCL 重新拉主页）"
            self._send(Response.text(str(int(time.time() * 1000)), headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, s-maxage=0",
                "Pragma": "no-cache", "Expires": "0", **BASE_SECURITY_HEADERS,
            }))
            return

        ip = self._log_ip
        origin = self._origin()

        if path in ("/Custom.xaml", "/"):
            blocked = service.guard_block(ip)
            if blocked:
                self._force_warn = True
                self._note = ("已封禁的 IP 请求主页" if service.store.is_blocked(ip) else "维护模式拦截")
                self._send(Response.xaml(blocked))
                return
            service.stats.record(ip, self.headers.get("CF-IPCountry") or "XX")
            try:
                body, data = service.homepage(ip, origin)
            except Exception:
                log.error("[Server] 主页组装失败：\n" + traceback.format_exc())
                self._note = "组装失败，已返回兜底页"
                self._send(Response.xaml(build_fallback_xaml("服务器正在更新", "服务器正在更新中，请稍后刷新重试。")))
                return
            self._note = data.summary()
            log.debug("[Server] UA=" + (ua[:80] or "（空）") + " origin=" + origin)
            self._send(Response.xaml(body, vary_ip=True))
            return

        if path in ("/healthz", "/health"):
            self._note = "健康检查"
            self._send(Response.text(json.dumps({
                "ok": True, "generated": service.static_template("Custom.xaml.version").strip(),
                "uptime": log.uptime(),
            }, ensure_ascii=False), "application/json; charset=utf-8"))
            return

        self._serve_static(path)

    # ---- 静态资源 ----

    def _serve_static(self, path: str):
        # 归一化，挡掉 ../ 穿越
        safe = posixpath.normpath(path).lstrip("/")
        if safe.startswith("..") or safe in ("", "."):
            self._note = "非法路径"
            self._send(Response(404, b"Not Found"))
            return

        candidates = [STATIC_DIR / safe, ROOT / safe]
        # 壁纸缓存目录也直接暴露出去
        if safe == "images/wallpaper.jpg":
            candidates.insert(0, self.service.config.wallpaper_path)

        target = next((p for p in candidates if p.is_file()), None)
        if target is None:
            self._note = "静态文件不存在"
            self._send(Response(404, ("未找到：" + path).encode("utf-8"),
                                content_type="text/plain; charset=utf-8"))
            return

        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"

        max_age = 0
        for prefix, seconds in STATIC_CACHE_RULES:
            if path.startswith(prefix):
                max_age = seconds
                break

        headers = dict(BASE_SECURITY_HEADERS)
        if max_age:
            headers["Cache-Control"] = "public, max-age=" + str(max_age)
        self._send(Response(200, target.read_bytes(), content_type=ctype, headers=headers))


class HomepageServer(ThreadingHTTPServer):
    daemon_threads = True

    # Windows 的 SO_REUSEADDR 语义与 Linux 不同：它允许两个进程绑同一个端口，
    # 于是"重启服务后旧进程还在应答、改了代码没效果"这种鬼故事就会发生。
    # 所以 Windows 上关掉它——端口被占时直接报错，比默默绑上两个进程好排查得多。
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address, handler, service: HomepageService, verbose: bool = False):
        self.service = service
        self.verbose = verbose
        handler.service = service
        super().__init__(address, handler)


def _local_addresses() -> list[str]:
    """本机的局域网地址（用来告诉你"同一个 WiFi 下可以先这样试"）。

    用 UDP socket「连」一个公网地址再读本端地址——不会真的发包，
    只是为了骗内核选出默认出口网卡。
    """
    import socket
    found = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("223.5.5.5", 80))
            found.append(sock.getsockname()[0])
        finally:
            sock.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in found and not addr.startswith("127."):
                found.append(addr)
    except Exception:
        pass
    return found


def _log_boot(config: Config, service: HomepageService, host, port) -> None:
    """启动时把"当前生效的设置"完整打一遍——排查问题十有八九要看这些。"""
    log.out("=" * 64)
    log.out(config.site_name + " · Python 复刻版")
    log.out("=" * 64)
    log.out("[Boot] 监听 http://" + str(host) + ":" + str(port)
            + "    日志级别 " + log.level_name() + "（-v 看调试细节，-q 只看异常）")
    log.out("[Boot] 主页 /Custom.xaml    版本 /Custom.xaml.version    后台 /admin")
    log.out("[Boot] BASE_URL=" + (config.base_url or "（未配置，按请求 Host 推导）")
            + "    来源守卫=" + ("开" if config.guard_clients else "关")
            + "    信任反代头=" + ("是" if config.trust_proxy_headers else "否"))
    log.out("[Boot] 每日一言=" + ("uapis.cn" if config.enable_saying else "关闭")
            + ("（语料 " + config.saying_source + "）" if config.saying_source else "（不限语料）")
            + "    农历=" + ("uapis.cn" if config.enable_lunar else "本地换算")
            + "    天气=" + ("uapis.cn" if config.enable_weather else "关闭")
            + ("（固定城市 " + config.weather_city + "）" if config.weather_city
               else ("（按访问者 IP 定位城市）" if config.enable_geo else "（按服务器 IP 定位）")))
    log.out("[Boot] 壁纸=" + ("必应每日壁纸（构建期）" if config.enable_wallpaper else "PCL 内置图")
            + "    功能网站 " + str(len(config.sites)) + " 个")
    if config.enable_ai:
        log.out("[Boot] AI 日志分析=" + config.ai_model + " @ " + config.ai_api_base
                + "    内置密钥=" + ("已配置 " + ai.mask_key(config.ai_api_key)
                                     if config.ai_api_key else "未配置（只能用访客自带密钥）")
                + "    每 IP 每天 " + str(config.ai_daily_limit) + " 次")
    else:
        log.out("[Boot] AI 日志分析=关闭")

    output = ROOT / "Custom.xaml"
    if output.exists():
        import datetime
        mtime = datetime.datetime.fromtimestamp(output.stat().st_mtime).strftime("%m-%d %H:%M:%S")
        log.out("[Boot] 生成物 Custom.xaml " + str(output.stat().st_size) + " 字节（" + mtime + " 生成）")
    else:
        log.warn("[Boot] 还没有生成物 Custom.xaml，将按离线模式先生成一份")

    from .sites import local_icon, site_slug
    missing = [str(s.get("name")) for s in config.sites if not local_icon(config, site_slug(s))]
    if missing:
        log.warn("[Boot] 这些站的图标没在本地，页面会用远程地址：" + "、".join(missing))
    else:
        log.out("[Boot] 功能网站图标已全部镜像到本地")

    # 监听地址自检：绑在回环地址上时，除了本机谁都连不上——服务器部署最常见的坑
    if config.host in ("127.0.0.1", "localhost", "::1"):
        log.warn("[Boot] 当前只监听本机回环地址 " + config.host + "，外部（包括你自己的电脑）连不上")
        log.warn("[Boot] 要让别人访问：python run.py serve --host 0.0.0.0"
                 + "（同时记得放行防火墙 / 云服务商安全组的 " + str(port) + " 端口）")
    elif config.host == "0.0.0.0":
        for addr in _local_addresses():
            log.out("[Boot] 本机可访问地址：http://" + addr + ":" + str(port) + "/Custom.xaml")

    if config.admin_token == "admin":
        if config.host in ("127.0.0.1", "localhost", "::1"):
            log.warn("[Boot] 后台令牌是默认值 admin（本机自用可忽略）")
        else:
            log.error("[Boot] 监听 " + config.host + " 但后台令牌仍是默认的 admin，请立刻修改！")
    log.out("=" * 64)
    log.out("按 Ctrl+C 停止")
    log.out("=" * 64)


def serve(config: Config | None = None, verbose: bool = False) -> None:
    config = config or load_config()
    if verbose and log.enabled(log.DEBUG) is False:
        log.set_level(log.DEBUG)
    service = HomepageService(config)
    try:
        httpd = HomepageServer((config.host, config.port), Handler, service, verbose=verbose)
    except OSError as exc:
        log.error("[Server] 无法监听 " + config.host + ":" + str(config.port) + "（" + str(exc) + "）")
        log.error("[Server] 端口可能已被占用：换个 --port，或先结束占用的进程。")
        return

    host, port = httpd.server_address[0], httpd.server_address[1]
    _log_boot(config, service, host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.out("\n[Server] 已停止，运行时长 " + log.uptime())
    finally:
        httpd.server_close()
