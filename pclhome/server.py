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
from . import geo, log
from .config import ROOT, STATIC_DIR, TEMPLATES_DIR, Config, load_config
from .generate import generate, refresh_wallpaper
from .i18n import t
from .render import build_home_data, render_homepage
from .store import Stats, Store
from .weather import WeatherService
from . import ai
from . import calc
from . import palette
from . import profiles
from . import settings
from . import languages
from . import visit
from .xaml import build_fallback_xaml


def _bot_xaml(ip: str) -> str:
    """来源守卫拦下时给访客看的页（按这个 IP 的语言）。"""
    lang = profiles.lang_for(ip)
    return build_fallback_xaml(t("fallback.denied_title", lang),
                               t("fallback.bot_msg", lang), lang=lang)


def _updating_xaml(ip: str, eta: str = "", reason: str = "") -> str:
    lang = profiles.lang_for(ip)
    return build_fallback_xaml(t("fallback.updating_title", lang),
                               t("fallback.updating_msg", lang),
                               eta, reason, lang=lang, maintenance=True)

def _city_of(source: str) -> str:
    """把天气卡片的定位串当城市名用：正常是「泉州市」「北京城区 · 西城区」。

    定位失败时那里是一句提示（带括号、或者很长），那种内容不要写进访问统计，
    否则后台「最近访客」里会出现一整句话。
    """
    text = str(source or "").strip()
    if not text or len(text) > 24 or "（" in text or "(" in text:
        return ""
    return text


PROVINCE_SUFFIX = (
    "维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区", "自治区", "省", "市",
)


def _short_province(name) -> str:
    """「浙江省」→「浙江」，和地图上的省份 key 对齐。"""
    text = str(name or "").strip()
    for suffix in PROVINCE_SUFFIX:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


# 需要"仅 PCL 客户端与真实浏览器可访问"的动态数据端点
DATA_ENDPOINTS = {"/Custom.xaml", "/"}
VERSION_PATHS = {"/Custom.xaml.version", "/Custom.xaml.ini", "/version"}

# 标记版主页。和 /Custom.xaml 是同一套模板、同一份数据，只有「日期大字」不同：
# 这边交给 PCL 的 {date} 替换标记，加载时才填当天，所以即使 PCL 渲染的是昨天
# 缓存下来的那份 XAML，日期也是今天的（PCL 下载完不会重绘，见维护报告 §2）。
# 在 PCL 里把联网主页地址改成 https://<域名>/mark 就能用这一版，/Custom.xaml 原样保留。
MARK_PREFIX = "/mark"
MARK_PATHS = {
    MARK_PREFIX,
    MARK_PREFIX + "/",
    MARK_PREFIX + "/version",
    MARK_PREFIX + "/Custom.xaml",
    MARK_PREFIX + "/Custom.xaml.ini",
    MARK_PREFIX + "/Custom.xaml.version",
}


def mark_version() -> str:
    """标记版的版本号：取构建产物的修改时间。

    标记版里的日期是 `{date}` 字面量，服务器每天吐出来的字节完全一样。
    版本号必须跟着内容走，不能用当前时间——否则 PCL 每次启动都判定
    「有新版本」重下 15 KB，白白浪费。重新 generate 之后文件时间变了，
    版本号自然跟着变，客户端下一次启动就会拉新的。
    """
    try:
        return str(int((ROOT / "Custom.xaml").stat().st_mtime * 1000))
    except OSError:
        return str(int(time.time() * 1000))


# 带日期的路径：<域名>/2026/11/10、/2026/11/10/version 之类。
# PCL 的「联网更新」地址支持 {date} 替换标记，地址写成 https://<域名>/{date}
# 之后，每天的 URL 都不一样；PCL 的主页缓存按 URL 分键，跨天第一次打开就会
# 走"全新下载"并重新实例化，不会再翻出前一天的缓存页（见维护报告 §2）。
DATED_RE = re.compile(
    r"^/(\d{4})/(\d{1,2})/(\d{1,2})"
    r"(?:/(version|Custom\.xaml|Custom\.xaml\.ini|Custom\.xaml\.version))?/?$"
)


def dated_version(match) -> str:
    """按日期的版本号：同一天内固定，换天就变。"""
    try:
        year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    except (TypeError, ValueError):
        return str(int(time.time() * 1000))
    return "%04d%02d%02d" % (year, month, day)

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

# 允许从**项目根目录**对外发的文件，只有这两个构建产物。
# 这是个白名单：往这里加任何东西之前先想清楚它能不能给陌生人看。
ROOT_FILES = ("Custom.xaml", "Custom.xaml.version")


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
        self._wallpaper_thread: threading.Thread | None = None
        self._ensure_outputs()
        self._start_wallpaper_loop()

    # ---- 壁纸：后台线程每天换一张 ----

    def _start_wallpaper_loop(self) -> None:
        """起一个后台线程盯着必应每日壁纸。

        为什么不放在请求里做：抓一次壁纸要跟必应 API 来回一趟（国内还可能超时），
        挂在请求上等于让某个倒霉访客替所有人等。线程每 30 分钟看一次缓存新不新，
        按 ``wallpaper_cache_hours``（默认 6 小时）决定要不要真的下载。
        """
        if not self.config.enable_wallpaper:
            return
        if self._wallpaper_thread and self._wallpaper_thread.is_alive():
            return

        def loop() -> None:
            # 启动后先等一会儿，别跟启动横幅、图标镜像抢网络
            time.sleep(15)
            while True:
                try:
                    refresh_wallpaper(self.config)
                except Exception:                     # 线程不能死
                    log.warn("[Wallpaper] 刷新线程异常：\n" + traceback.format_exc())
                time.sleep(1800)

        self._wallpaper_thread = threading.Thread(target=loop, name="wallpaper", daemon=True)
        self._wallpaper_thread.start()
        log.debug("[Wallpaper] 后台刷新线程已启动（每 30 分钟检查一次）")

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

    def homepage(self, ip: str, origin: str, marker: bool = False):
        """返回 ``(渲染好的 XAML, HomeData)``——后者给日志用。

        语言和温度单位按访客 IP 从个性设置里取（``profiles.lang_for`` /
        ``unit_for``），没记录就是简中 + 摄氏度。这两步都是纯内存查表，不会让主页变慢。

        ``marker=True`` 是标记版：日期大字交给 PCL 的 ``{date}``，打开即当天
        （见 ``render._date_main_marker``）。两个版本共用同一套模板与数据。
        """
        lang = profiles.lang_for(ip)
        unit = profiles.PROFILES.unit_for(ip)
        data = build_home_data(self.config, self.store, self.weather, ip, origin, lang, unit)
        text = render_homepage(self.static_template("Custom.xaml"), data, self.config,
                               origin, lang,
                               date_mode="marker" if marker else "server")
        return palette.apply(text, profiles.PROFILES.palette_for(ip)), data

    # ---- 拦截：封禁 / 维护 ----

    def guard_block(self, ip: str) -> str | None:
        """返回拦截页 XAML；放行则返回 None。"""
        if self.store.is_blocked(ip):
            lang = profiles.lang_for(ip)
            return build_fallback_xaml(t("fallback.banned_title", lang),
                                       t("fallback.banned_msg", lang), lang=lang)

        maint = self.store.maintenance()
        if maint["on"] and ip not in maint["whitelist"]:
            return _updating_xaml(ip, maint["eta"], maint["reason"])
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
            self._send(Response.xaml(_updating_xaml(self._log_ip)))
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

        # 带日期的路径：<域名>/2026/11/10、/2026/11/10/version 之类。
        # PCL 的「联网更新」地址支持 {date} 替换标记，把地址写成
        # https://<域名>/{date} 之后，每天的 URL 都不一样；PCL 的主页缓存按
        # URL 分键，于是跨天第一次打开会走"全新下载"，不会再翻出前一天的缓存页
        # （见维护报告 §2）。版本号用日期本身，同一天内重复启动仍然吃缓存。
        dated = DATED_RE.match(path)
        if dated:
            tail = dated.group(4) or ""
            if tail in ("version", "Custom.xaml.ini", "Custom.xaml.version"):
                self._note = "按日期的主页版本号（同一天内固定）"
                self._send(Response.text(dated_version(dated), headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, s-maxage=0",
                    "Pragma": "no-cache", "Expires": "0", **BASE_SECURITY_HEADERS,
                }))
                return
            path = "/Custom.xaml"

        if path.startswith("/admin"):
            self._note = "后台"
            self._send(admin_module.handle(service, self, path, parsed.query))
            return

        if path in ai.AI_PATHS:
            if service.config.guard_clients and not is_trusted_client(ua, referer):
                self._force_warn = True
                self._note = "来源守卫拦截（AI 接口）：UA=" + (ua[:60] or "（空）")
                self._send(Response.xaml(_bot_xaml(self._log_ip)))
                return
            response = ai.handle(service, path, parsed.query, self._log_ip, self._origin())
            if response is None:
                self._send(Response(404, b"Not Found", "text/plain; charset=utf-8"))
                return
            self._note = "AI 日志分析：" + path
            self._send(response)
            return

        if path in calc.CALC_PATHS:
            if service.config.guard_clients and not is_trusted_client(ua, referer):
                self._force_warn = True
                self._note = "来源守卫拦截（计算器）：UA=" + (ua[:60] or "（空）")
                self._send(Response.xaml(_bot_xaml(self._log_ip)))
                return
            response = calc.handle(service, path, parsed.query, self._log_ip, self._origin())
            if response is None:
                self._send(Response(404, b"Not Found", "text/plain; charset=utf-8"))
                return
            self._note = "计算器：" + path
            self._send(response)
            return

        if path in settings.SETTINGS_PATHS:
            if service.config.guard_clients and not is_trusted_client(ua, referer):
                self._force_warn = True
                self._note = "来源守卫拦截（个性设置）：UA=" + (ua[:60] or "（空）")
                self._send(Response.xaml(_bot_xaml(self._log_ip)))
                return
            response = settings.handle(service, path, parsed.query, self._log_ip, self._origin())
            if response is None:
                self._send(Response(404, b"Not Found", "text/plain; charset=utf-8"))
                return
            self._note = "个性设置：" + path
            self._send(response)
            return

        if path in languages.LANGUAGE_PATHS:
            if service.config.guard_clients and not is_trusted_client(ua, referer):
                self._force_warn = True
                self._note = "来源守卫拦截（界面语言）：UA=" + (ua[:60] or "（空）")
                self._send(Response.xaml(_bot_xaml(self._log_ip)))
                return
            response = languages.handle(service, path, parsed.query, self._log_ip, self._origin())
            if response is None:
                self._send(Response(404, b"Not Found", content_type="text/plain; charset=utf-8"))
                return
            self._note = "界面语言：" + path
            self._send(response)
            return

        if path in visit.VISIT_PATHS:
            if service.config.guard_clients and not is_trusted_client(ua, referer):
                self._force_warn = True
                self._note = "来源守卫拦截（访问浏览）：UA=" + (ua[:60] or "（空）")
                self._send(Response.xaml(_bot_xaml(self._log_ip)))
                return
            response = visit.handle(service, path, parsed.query, self._log_ip, self._origin())
            if response is None:
                self._send(Response(404, b"Not Found", content_type="text/plain; charset=utf-8"))
                return
            self._note = "访问浏览：" + path
            self._send(response)
            return

        if path in MARK_PATHS:
            if service.config.guard_clients and not is_trusted_client(ua, referer):
                self._force_warn = True
                self._note = "来源守卫拦截（标记版主页）：UA=" + (ua[:60] or "（空）")
                self._send(Response.xaml(_bot_xaml(self._log_ip)))
                return
            if path.endswith(".ini") or path.endswith(".version") or path == MARK_PREFIX + "/version":
                self._note = "标记版版本号（跟构建时间走，内容没变就不重复下载）"
                self._send(Response.text(mark_version(), headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, s-maxage=0",
                    "Pragma": "no-cache", "Expires": "0", **BASE_SECURITY_HEADERS,
                }))
                return
            marker_ip = self._log_ip
            blocked = service.guard_block(marker_ip)
            if blocked:
                self._force_warn = True
                self._note = ("已封禁的 IP 请求标记版主页" if service.store.is_blocked(marker_ip)
                              else "维护模式拦截（标记版）")
                self._send(Response.xaml(blocked))
                return
            try:
                body, data = service.homepage(marker_ip, self._origin(), marker=True)
            except Exception:
                log.error("[Server] 标记版主页组装失败：\n" + traceback.format_exc())
                self._note = "标记版组装失败，已返回兜底页"
                self._send(Response.xaml(_updating_xaml(marker_ip)))
                return
            # 也记一笔访问：有人切到标记版之后，/Custom.xaml 就不再来流量了，
            # 不记的话访问统计会凭空断掉。两个版本同时用才会重复计。
            country = self.headers.get("CF-IPCountry") or "XX"
            city, province = "", ""
            if service.config.enable_geo:
                try:
                    info = geo.locate(marker_ip)
                except Exception as exc:
                    log.debug("[Server] 定位失败：" + repr(exc))
                    info = None
                if info:
                    city = str(info.get("city") or "").strip()
                    province = _short_province(info.get("province"))
            if not city:
                city = _city_of(data.weather_source)
            service.stats.record(marker_ip, country, city, province)
            self._note = "标记版主页：" + data.summary()
            self._send(Response.xaml(body, vary_ip=True))
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
                self._send(Response.xaml(_bot_xaml(self._log_ip)))
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
            country = self.headers.get("CF-IPCountry") or "XX"
            try:
                body, data = service.homepage(ip, origin)
            except Exception:
                log.error("[Server] 主页组装失败：\n" + traceback.format_exc())
                self._note = "组装失败，已返回兜底页"
                service.stats.record(ip, country)
                self._send(Response.xaml(_updating_xaml(ip)))
                return
            # 城市/省份直接蹭天气那次 IP 定位的结果——天气卡片本来就知道访客在哪个市，
            # 再单独查一遍 IP 库纯属浪费（用户也明确不让引入新接口）。geo 有缓存，
            # 这里通常是一次内存命中；天气关了才可能真发一次请求，所以先看 enable_geo。
            city, province = "", ""
            if service.config.enable_geo:
                try:
                    info = geo.locate(ip)
                except Exception as exc:
                    log.debug("[Server] 定位失败：" + repr(exc))
                    info = None
                if info:
                    city = str(info.get("city") or "").strip()
                    province = _short_province(info.get("province"))
            if not city:
                city = _city_of(data.weather_source)
            service.stats.record(ip, country, city, province)
            self._note = data.summary()
            log.debug("[Server] UA=" + (ua[:80] or "（空）") + " origin=" + origin)
            self._send(Response.xaml(body, vary_ip=True))
            return

        if path in ("/healthz", "/health"):
            self._note = "健康检查"
            # ``Custom.xaml.version`` 是给"静态部署"用的占位文件，动态服务这套可以没有它
            # （对外发的版本号由 /Custom.xaml.version 那条路由现算时间戳）。以前这里直接
            # static_template()，服务器上没这个文件就抛 FileNotFoundError，被外层兜成
            # 维护页——健康检查反倒成了最容易挂的端点。
            try:
                generated = service.static_template("Custom.xaml.version").strip()
            except OSError:
                generated = ""
            self._send(Response.text(json.dumps({
                "ok": True, "generated": generated, "uptime": log.uptime(),
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

        # 一律只在 static/ 里找；根目录只有白名单里那两个文件能出。
        #
        # 以前这里是 `[STATIC_DIR / safe, ROOT / safe]`——第二个候选等于把**整个项目目录**
        # 挂到公网上发：`GET /config.json` 直接吐配置（uapi_key、ai_api_key、admin_token），
        # `/var/server.log`、`/var/profiles.json`、`/pclhome/config.py`、`/run.py` 一样能下。
        # 2026-10-14 才发现：日志里十几个不同 IP 拉走过 config.json，AI 密钥被拿去刷了
        # 240 万 token。所以根目录改白名单，别再退回"第二个候选"那种写法。
        candidates = [STATIC_DIR / safe]
        if safe in ROOT_FILES:
            candidates.append(ROOT / safe)
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
    # 只报"配没配"，密钥本身不进日志
    from . import uapi
    uapi.log_state(config)
    if config.enable_ai:
        limit = config.ai_token_limit_or_zero()
        quota_text = (str(limit) + " token / " + config.ai_window_label("zh-hans")
                      + "（高峰 " + str(int(config.ai_peak_factor())) + " 倍）") if limit > 0 else "不限额度"
        log.out("[Boot] AI 日志分析=" + config.ai_model + " @ " + config.ai_api_base
                + "    内置密钥=" + ("已配置 " + ai.mask_key(config.ai_api_key)
                                     if config.ai_api_key else "未配置（只能用访客自带密钥）")
                + "    每 IP " + quota_text)
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
