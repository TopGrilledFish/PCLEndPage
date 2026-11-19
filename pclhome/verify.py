"""访问验证：每 N 天让访客过一次人机验证。

背景：2026-11-17 21:04 那两分钟，365 个境外数据中心 IP 打了 564 次 ``GET /``，
每次都在完整渲染主页——2.6 秒、调用 uapi 天气和 ip-api 定位。光看 UA 拦不住
（带个 Mozilla 就进来了），所以加一道"通过一次验证，N 天内不再问"的闸门。

为什么验证放在浏览器里：PCL 的自定义主页只能「打开网页 / 打开帮助 / 复制文本 /
刷新页面」，没有提交表单的能力，所以启动器这边只能给一个引导页，真正的验证在
网页里完成，回来点「刷新页面」。引导页本身不组装主页，也就不烧外部接口额度。

状态：``var/verify.json``
    {"ips": {ip: 过期时间戳}, "fails": {ip: [失败次数, 解封时间]}}

题目不落盘：token = ``时间戳.答案.HMAC(ip|时间戳|答案)``，服务端只验签。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import time
import urllib.parse
from html import escape as html_escape

from . import palette
from . import profiles
from .config import VAR_DIR
from .i18n import DEFAULT_LANG, t
from .log import debug, warn
from .xaml import ICON_KEY, ICON_REFRESH, escape_attr, escape_url_attr

VERIFY_PATHS = {"/verify", "/verify/"}

VERIFY_FILE = VAR_DIR / "verify.json"
SECRET_FILE = VAR_DIR / "verify_secret"

CODE_LEN = 5
# 题目有效期：打开页面后 15 分钟内要提交，慢了重新出题（不存服务器状态）
TOKEN_TTL = 900
# 连续输错 5 次，封 1 小时。家用 IP 一般一次就过，这条只惩罚脚本
MAX_FAIL = 5
LOCK_SECONDS = 3600

# 去掉了 0/O、1/I/L 这些屏幕上容易看混的字符
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


# --------------------------------------------------------------------------- 状态

def _load() -> dict:
    try:
        with open(VERIFY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("ips", {})
            data.setdefault("fails", {})
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        warn("[Verify] 读取验证状态失败：" + repr(exc))
    return {"ips": {}, "fails": {}}


def _save(data: dict) -> None:
    tmp = str(VERIFY_FILE) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, VERIFY_FILE)
        try:
            os.chmod(VERIFY_FILE, 0o600)
        except OSError:
            pass
    except OSError as exc:
        warn("[Verify] 写入验证状态失败：" + repr(exc))


def _secret() -> bytes:
    try:
        with open(SECRET_FILE, "rb") as fh:
            data = fh.read().strip()
        if len(data) >= 32:
            return data
    except OSError:
        pass
    data = os.urandom(32).hex().encode()
    try:
        with open(SECRET_FILE, "wb") as fh:
            fh.write(data)
        try:
            os.chmod(SECRET_FILE, 0o600)
        except OSError:
            pass
    except OSError as exc:
        warn("[Verify] 写入签名密钥失败（将使用内存密钥）：" + repr(exc))
    return data


_SECRET = b""


def _sign(ip: str, ts: str, code: str) -> str:
    global _SECRET
    if not _SECRET:
        _SECRET = _secret()
    raw = (ip + "|" + ts + "|" + code).encode("utf-8")
    return hmac.new(_SECRET, raw, hashlib.sha256).hexdigest()[:32]


def ttl_seconds(config) -> int:
    try:
        days = float(getattr(config, "verify_days", 7) or 7)
    except (TypeError, ValueError):
        days = 7.0
    return max(3600, int(days * 86400))


def is_verified(ip: str) -> bool:
    if not ip:
        return False
    data = _load()
    expire = data["ips"].get(ip)
    try:
        return float(expire) > time.time()
    except (TypeError, ValueError):
        return False


def mark_verified(ip: str, config) -> None:
    now = time.time()
    data = _load()
    # 顺手清掉过期项，文件不会越滚越大
    data["ips"] = {k: v for k, v in data["ips"].items() if _as_float(v) > now}
    data["fails"].pop(ip, None)
    data["ips"][ip] = now + ttl_seconds(config)
    _save(data)


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def locked_seconds(ip: str) -> int:
    data = _load()
    row = data["fails"].get(ip)
    if not isinstance(row, list) or len(row) < 2:
        return 0
    left = _as_float(row[1]) - time.time()
    return int(left) if left > 0 else 0


def note_fail(ip: str) -> None:
    data = _load()
    row = data["fails"].get(ip)
    count = int(_as_float(row[0])) + 1 if isinstance(row, list) and row else 1
    until = time.time() + LOCK_SECONDS if count >= MAX_FAIL else 0
    data["fails"][ip] = [count, until]
    _save(data)
    debug("[Verify] " + ip + " 验证失败 " + str(count) + " 次")


# --------------------------------------------------------------------------- 题目

def _random_code() -> str:
    return "".join(random.choice(ALPHABET) for _ in range(CODE_LEN))


def make_token(ip: str) -> tuple[str, str]:
    code = _random_code()
    ts = str(int(time.time()))
    return ts + "." + code + "." + _sign(ip, ts, code), code


def check_token(ip: str, token: str, answer: str) -> tuple[bool, str]:
    parts = (token or "").split(".")
    if len(parts) != 3:
        return False, "bad"
    ts, code, sig = parts
    if not hmac.compare_digest(sig, _sign(ip, ts, code)):
        return False, "bad"
    age = time.time() - _as_float(ts)
    if age < 0 or age > TOKEN_TTL:
        return False, "expired"
    if str(answer or "").strip().upper() != code:
        return False, "wrong"
    return True, ""


def _svg(code: str) -> str:
    """把验证码画成 SVG。用 text 而不是图片，省一次请求也省字体依赖。"""
    rnd = random.Random(code)
    parts = []
    for index, ch in enumerate(code):
        x = 28 + index * 44
        y = 64 + rnd.randint(-5, 5)
        rot = rnd.randint(-16, 16)
        parts.append(
            '<text x="%d" y="%d" transform="rotate(%d %d %d)" font-family="ui-monospace,'
            ' Consolas, monospace" font-size="40" font-weight="700" fill="#2b2f36">%s</text>'
            % (x, y, rot, x, y, html_escape(ch))
        )
    for _ in range(2):
        x1, y1 = rnd.randint(0, 250), rnd.randint(8, 92)
        x2, y2 = rnd.randint(0, 250), rnd.randint(8, 92)
        parts.append(
            '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#8b93a1" stroke-width="1.4"'
            ' opacity="0.65"/>' % (x1, y1, x2, y2)
        )
    return ('<svg width="250" height="100" viewBox="0 0 250 100"'
            ' xmlns="http://www.w3.org/2000/svg" role="img" aria-label="验证码">'
            '<rect width="250" height="100" rx="10" fill="#f2f4f7"/>'
            + "".join(parts) + "</svg>")


# --------------------------------------------------------------------------- 页面

_STYLE = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:#f6f7f9;color:#1f2328;font-family:system-ui,-apple-system,"Segoe UI",Roboto,
 "Microsoft YaHei UI",sans-serif;padding:24px}
.card{width:100%;max-width:420px;background:#fff;border-radius:16px;padding:28px 24px;
 box-shadow:0 10px 30px rgba(31,35,40,.08);border:1px solid #e6e8eb}
h1{font-size:20px;margin:0 0 6px}
p{font-size:13px;line-height:1.7;color:#5b6470;margin:0 0 16px}
.code{display:flex;justify-content:center;margin:6px 0 14px}
input[type=text]{width:100%;padding:12px 14px;font-size:18px;letter-spacing:.24em;
 text-transform:uppercase;border:1px solid #d5d9df;border-radius:10px;outline:none;
 font-family:ui-monospace,Consolas,monospace}
input[type=text]:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.15)}
button{width:100%;margin-top:12px;padding:12px;font-size:15px;font-weight:600;
 color:#fff;background:#2f6feb;border:0;border-radius:10px;cursor:pointer}
button:active{transform:translateY(1px)}
.err{margin:10px 0 0;padding:10px 12px;border-radius:10px;background:#fff1f0;
 color:#b42318;font-size:13px;border:1px solid #ffd6d1}
.ok{margin:10px 0 0;padding:10px 12px;border-radius:10px;background:#ecfdf3;
 color:#067647;font-size:13px;border:1px solid #c6f2d8}
.tip{margin-top:14px;font-size:12px;color:#8b93a1}
"""


def page_html(ip: str, origin: str, lang: str = DEFAULT_LANG, error: str = "",
              token: str = "", code: str = "") -> str:
    """验证页 HTML（浏览器打开）。token 为空时现生成一道题。"""
    if not token:
        token, code = make_token(ip)
    title = t("verify.page_title", lang)
    hint = t("verify.page_hint", lang)
    button = t("verify.button", lang)
    tip = t("verify.page_tip", lang)
    err_html = ('<div class="err">' + html_escape(error) + "</div>") if error else ""
    return (
        "<!doctype html><html lang=\"" + html_escape(lang) + "\"><head>"
        "<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>" + html_escape(title) + "</title><style>" + _STYLE + "</style></head><body>"
        "<form class=\"card\" method=\"get\" action=\"/verify\" autocomplete=\"off\">"
        "<h1>" + html_escape(title) + "</h1><p>" + html_escape(hint) + "</p>"
        "<div class=\"code\">" + _svg(code) + "</div>"
        "<input type=\"hidden\" name=\"token\" value=\"" + html_escape(token) + "\">"
        "<input type=\"text\" name=\"answer\" maxlength=\"" + str(CODE_LEN + 2) + "\""
        " autofocus autocomplete=\"off\" inputmode=\"text\" placeholder=\""
        + html_escape(t("verify.placeholder", lang)) + "\">"
        + err_html
        + "<button type=\"submit\">" + html_escape(button) + "</button>"
        "<div class=\"tip\">" + html_escape(tip) + "</div>"
        "</form></body></html>"
    )


def ok_html(ip: str, origin: str, lang: str = DEFAULT_LANG) -> str:
    title = t("verify.page_title", lang)
    head = t("verify.ok_title", lang)
    body = t("verify.ok_desc", lang)
    return (
        "<!doctype html><html lang=\"" + html_escape(lang) + "\"><head>"
        "<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>" + html_escape(title) + "</title><style>" + _STYLE + "</style></head><body>"
        "<div class=\"card\"><h1>" + html_escape(head) + "</h1>"
        "<p>" + html_escape(body) + "</p>"
        "<div class=\"ok\">" + html_escape(t("verify.ok_line", lang)) + "</div>"
        "</div></body></html>"
    )


def guide_xaml(ip: str, lang: str = DEFAULT_LANG, origin: str = "") -> str:
    """PCL 侧的引导页。**不组装主页**，所以不会调 uapi / ip-api。"""
    lang = lang or DEFAULT_LANG
    base = (origin or "").rstrip("/")
    url = (base + "/verify") if base else "/verify"
    page = (
        '<local:MyCard Title="' + escape_attr(t("verify.title", lang)) + '" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'
        '<TextBlock Text="' + escape_attr(t("verify.need_title", lang)) + '" FontSize="24"'
        ' FontWeight="Bold" HorizontalAlignment="Center" Foreground="#FF000000" />'
        '<TextBlock Text="' + escape_attr(t("verify.need_desc", lang)) + '" FontSize="11"'
        ' TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush3}" Margin="0,10,0,0" />'
        '<local:MyIconTextButton Height="40" Margin="0,14,0,0" HorizontalAlignment="Center"'
        ' Text="' + escape_attr(t("verify.goto", lang)) + '" LogoScale="0.85" ColorType="Highlight"'
        ' Logo="' + escape_attr(ICON_KEY) + '" EventType="打开网页" EventData="'
        + escape_url_attr(url) + '" />'
        '<local:MyIconTextButton Height="40" Margin="0,8,0,0" HorizontalAlignment="Center"'
        ' Text="' + escape_attr(t("verify.refresh", lang)) + '" LogoScale="0.85"'
        ' Logo="' + escape_attr(ICON_REFRESH) + '" EventType="刷新页面" EventData="-" />'
        "</StackPanel></local:MyCard>"
    )
    return palette.apply(page, profiles.PROFILES.palette_for(ip))


# --------------------------------------------------------------------------- 路由

def handle(service, path: str, query: str, ip: str, origin: str = ""):
    """``/verify`` 与 ``/verify/``。返回 Response；关掉验证时返回 None（交给 404）。"""
    from .server import Response

    config = service.config
    if not getattr(config, "verify_enabled", True):
        return None

    lang = profiles.PROFILES.lang_for(ip)
    base = service.config.resolved_base_url(origin) if hasattr(service.config, "resolved_base_url") else origin

    def _html(text: str):
        return Response(body=text.encode("utf-8"), content_type="text/html; charset=utf-8")

    if is_verified(ip):
        return _html(ok_html(ip, base, lang))

    left = locked_seconds(ip)
    if left > 0:
        minutes = max(1, int(left / 60) + 1)
        return _html(page_html(ip, base, lang, error=t("verify.locked", lang, minutes=minutes)))

    params = urllib.parse.parse_qs(query or "")
    answer = (params.get("answer") or [""])[0]
    token = (params.get("token") or [""])[0]

    if token or answer:
        ok, reason = check_token(ip, token, answer)
        if ok:
            mark_verified(ip, config)
            debug("[Verify] " + ip + " 通过验证")
            return _html(ok_html(ip, base, lang))
        note_fail(ip)
        message = t("verify.expired", lang) if reason == "expired" else t("verify.wrong", lang)
        left = locked_seconds(ip)
        if left > 0:
            minutes = max(1, int(left / 60) + 1)
            message = t("verify.locked", lang, minutes=minutes)
        return _html(page_html(ip, base, lang, error=message))

    return _html(page_html(ip, base, lang))
