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
import math
import os
import random
import struct
import time
import urllib.parse
import urllib.request
import zlib
from html import escape as html_escape

from . import palette
from . import profiles
from .config import VAR_DIR
from .i18n import DEFAULT_LANG, t
from .log import debug, warn
from .xaml import ICON_REFRESH, ICON_SAVE, escape_attr, escape_url_attr

VERIFY_PATHS = {
    "/verify", "/verify/",
    "/verify_img.png",
    "/verify_submit.json", "/verify_submit.xaml",
    "/verify_cf", "/verify_cf/", "/verify_cf_post",
}

VERIFY_FILE = VAR_DIR / "verify.json"
SECRET_FILE = VAR_DIR / "verify_secret"

CODE_LEN = 5
# 题目有效期：打开页面后 15 分钟内要提交，慢了重新出题（不存服务器状态）
TOKEN_TTL = 900
# 连续输错 5 次，封 1 小时。家用 IP 一般一次就过，这条只惩罚脚本
MAX_FAIL = 5
LOCK_SECONDS = 3600
# 连续错 3 次就换 Cloudflare 的题（浏览器里做，比看图难骗）
FAIL_FOR_CF = 3
CF_TTL = 900
TURNSTILE_VERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

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


def note_fail(ip: str) -> int:
    """记一次失败，返回累计失败次数（连续错到 FAIL_FOR_CF 就换 Cloudflare 的题）。"""
    data = _load()
    row = data["fails"].get(ip)
    count = int(_as_float(row[0])) + 1 if isinstance(row, list) and row else 1
    until = time.time() + LOCK_SECONDS if count >= MAX_FAIL else 0
    data["fails"][ip] = [count, until]
    _save(data)
    debug("[Verify] " + ip + " 验证失败 " + str(count) + " 次")
    return count


def fail_count(ip: str) -> int:
    data = _load()
    row = data["fails"].get(ip)
    return int(_as_float(row[0])) if isinstance(row, list) and row else 0


def clear_fail(ip: str) -> None:
    data = _load()
    data["fails"].pop(ip, None)
    _save(data)


# --------------------------------------------------------------------------- Cloudflare

def _has_cf(ip: str) -> bool:
    """只读检查：这个 IP 有没有还没过期的临时链接。"""
    data = _load()
    row = data.get("cf", {}).get(ip)
    return (isinstance(row, list) and len(row) >= 2
            and _as_float(row[1]) > time.time())


def need_turnstile(ip: str, site_key: str = "") -> bool:
    """连续错够次数、且配了 Turnstile，才引导去浏览器做题。"""
    if not site_key:
        return False
    if fail_count(ip) >= FAIL_FOR_CF:
        return True
    return _has_cf(ip)


def cf_token(ip: str) -> str:
    """取（必要时生成）这个 IP 的临时 Cloudflare 链接令牌，15 分钟有效。"""
    data = _load()
    store = data.setdefault("cf", {})
    row = store.get(ip)
    now = time.time()
    if isinstance(row, list) and len(row) >= 2 and _as_float(row[1]) > now:
        return str(row[0])
    token = hashlib.sha256((ip + "|" + str(now) + "|" + os.urandom(8).hex()).encode()).hexdigest()[:24]
    store[ip] = [token, now + CF_TTL]
    _save(data)
    return token


def check_cf_token(ip: str, token: str) -> bool:
    data = _load()
    row = data.get("cf", {}).get(ip)
    if not isinstance(row, list) or len(row) < 2:
        return False
    if _as_float(row[1]) <= time.time():
        return False
    return hmac.compare_digest(str(row[0]), str(token or ""))


def turnstile_ok(secret: str, response: str, ip: str) -> tuple[bool, str]:
    """服务端校验 Turnstile 回执。"""
    if not secret or not response:
        return False, "missing"
    try:
        body = urllib.parse.urlencode({
            "secret": secret, "response": response, "remoteip": ip,
        }).encode()
        request = urllib.request.Request(
            TURNSTILE_VERIFY, data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            info = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - 网络/JSON 都可能炸，统一判失败
        warn("[Verify] Turnstile 校验异常：" + repr(exc))
        return False, "error"
    if info.get("success"):
        return True, ""
    codes = info.get("error-codes") or []
    return False, ",".join(str(code) for code in codes[:3]) or "failed"


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


# 5×7 点阵字库，只覆盖 ALPHABET 里的 31 个字符。手写而不是依赖字体文件：
# 服务器上没有 Pillow，也不该为了一个验证码去装第三方包。
_FONT = {
    "2": ".####/#...#/....#/...#./..#../.#.../#####",
    "3": "#####/....#/...#./..##./....#/#...#/.###.",
    "4": "...#./..##./.#.#./#..#./#####/...#./...#.",
    "5": "#####/#..../####./....#/....#/#...#/.###.",
    "6": "..##./.#.../#..../####./#...#/#...#/.###.",
    "7": "#####/....#/...#./..#../.#.../.#.../.#...",
    "8": ".###./#...#/#...#/.###./#...#/#...#/.###.",
    "9": ".###./#...#/#...#/.####/....#/...#./.##..",
    "A": "..#../.#.#./#...#/#...#/#####/#...#/#...#",
    "B": "####./#...#/#...#/####./#...#/#...#/####.",
    "C": ".###./#...#/#..../#..../#..../#...#/.###.",
    "D": "###../#..#./#...#/#...#/#...#/#..#./###..",
    "E": "#####/#..../#..../####./#..../#..../#####",
    "F": "#####/#..../#..../####./#..../#..../#....",
    "G": ".###./#...#/#..../#.###/#...#/#...#/.###.",
    "H": "#...#/#...#/#...#/#####/#...#/#...#/#...#",
    "J": "..###/...#./...#./...#./...#./#..#./.##..",
    "K": "#...#/#..#./#.#../##.../#.#../#..#./#...#",
    "M": "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#",
    "N": "#...#/##..#/#.#.#/#..##/#...#/#...#/#...#",
    "P": "####./#...#/#...#/####./#..../#..../#....",
    "Q": ".###./#...#/#...#/#...#/#.#.#/#..#./.##.#",
    "R": "####./#...#/#...#/####./#.#../#..#./#...#",
    "S": ".###./#...#/#..../.###./....#/#...#/.###.",
    "T": "#####/..#../..#../..#../..#../..#../..#..",
    "U": "#...#/#...#/#...#/#...#/#...#/#...#/.###.",
    "V": "#...#/#...#/#...#/#...#/#...#/.#.#./..#..",
    "W": "#...#/#...#/#...#/#.#.#/#.#.#/##.##/#...#",
    "X": "#...#/#...#/.#.#./..#../.#.#./#...#/#...#",
    "Y": "#...#/#...#/.#.#./..#../..#../..#../..#..",
    "Z": "#####/....#/...#./..#../.#.../#..../#####",
}


def _png(code: str) -> bytes:
    """把验证码画成灰度 PNG（纯标准库：点阵 + 扭曲 + 噪点 + 干扰线）。

    字形不是直接放大贴上去的：每个字符按 ``波浪 + 斜切 + 纵向挤压`` 反向采样，
    笔画会歪、会拉长；再叠一层底噪、十条直线干扰和四条正弦干扰。
    """
    scale = 7
    gap = 7
    cw, ch = 5 * scale, 7 * scale
    width = 20 + CODE_LEN * cw + (CODE_LEN - 1) * gap
    height = ch + 26
    rnd = random.Random(code)
    img = [[228 + rnd.randint(0, 26) for _ in range(width)] for _ in range(height)]

    # 底噪：撒得散一点，颜色偏浅，主要作用是让笔画边缘不干净
    for _ in range(360):
        x, y = rnd.randrange(width), rnd.randrange(height)
        img[y][x] = rnd.choice((198, 208, 216, 224, 232))

    # 笔画：逐像素反向采样，做出扭曲
    for index, char in enumerate(code):
        rows = _FONT.get(char)
        if not rows:
            continue
        bits = rows.split("/")
        ox = 10 + index * (cw + gap) + rnd.randint(-2, 2)
        oy = 13 + rnd.randint(-5, 5)
        phase = rnd.uniform(0.0, 6.283)
        wave = rnd.uniform(0.05, 0.09)
        skew = rnd.uniform(-0.10, 0.10)
        rot = rnd.uniform(-0.08, 0.08)
        squash = rnd.uniform(0.90, 1.10)
        tone = 20 + rnd.randint(0, 34)
        for yy in range(ch):
            ny = (yy + 0.5) / ch
            for xx in range(cw):
                nx = (xx + 0.5) / cw
                # 先整体转一个小角度，再叠波浪和斜切：字会歪、笔画会拉长
                ny2 = ny + rot * (nx - 0.5)
                row = int((max(0.0, min(1.0, ny2)) ** squash) * 7)
                if row < 0 or row > 6:
                    continue
                line = bits[row]
                nx2 = nx + wave * math.sin(ny2 * 5.4 + phase) + skew * (ny2 - 0.5)
                col = int(nx2 * 5)
                if col < 0 or col > 4:
                    continue
                if line[col] != "#":
                    continue
                x, y = ox + xx, oy + yy
                if 0 <= x < width and 0 <= y < height:
                    img[y][x] = tone + rnd.randint(0, 20)

    # 十条直线干扰，后四条更粗
    for i in range(10):
        x0, y0 = rnd.randrange(width), rnd.randrange(height)
        x1 = min(width - 1, max(0, x0 + rnd.randint(-width // 2, width // 2)))
        y1 = rnd.randrange(height)
        tone = rnd.choice((142, 158, 172, 186))
        thick = 1 if i < 6 else 2
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for k in range(steps + 1):
            x = x0 + (x1 - x0) * k // steps
            y = y0 + (y1 - y0) * k // steps
            for t in range(thick):
                if 0 <= x < width and 0 <= y + t < height:
                    img[y + t][x] = tone

    # 四条正弦干扰
    for _ in range(4):
        yb = rnd.randrange(height)
        amp = rnd.uniform(2.5, 7.0)
        freq = rnd.uniform(0.05, 0.14)
        ph = rnd.uniform(0.0, 6.283)
        tone = rnd.choice((154, 170, 184, 198))
        for x in range(width):
            y = int(yb + amp * math.sin(x * freq + ph))
            if 0 <= y < height:
                img[y][x] = tone

    raw = b"".join(b"\x00" + bytes(row) for row in img)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def image_bytes(ip: str, token: str) -> bytes:
    """``/verify_img.png?t=...``：token 里的答案就是图上的字。"""
    parts = (token or "").split(".")
    if len(parts) == 3:
        ts, code, sig = parts
        if hmac.compare_digest(sig, _sign(ip, ts, code)):
            return _png(code)
    return _png(_random_code())


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


def _cf_card(ip: str, base: str, lang: str) -> str:
    """连错几次之后给的一张极简卡：一句话 + 一个按钮，别的都不放。"""
    link = (base or "") + "/verify_cf?c=" + cf_token(ip)
    return (
        '<local:MyCard Title="' + escape_attr(t("verify.cf_title", lang)) + '" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'
        '<TextBlock Text="' + escape_attr(t("verify.cf_hint", lang)) + '" FontSize="12"'
        ' TextWrapping="Wrap" HorizontalAlignment="Center"'
        ' Foreground="{DynamicResource ColorBrush3}" />'
        '<local:MyTextButton Height="38" Margin="0,18,0,0" HorizontalAlignment="Center"'
        ' Text="' + escape_attr(t("verify.cf_link", lang)) + '"'
        ' EventType="打开网页" EventData="' + escape_url_attr(link) + '" />'
        '</StackPanel></local:MyCard>'
    )


def guide_xaml(ip: str, lang: str = DEFAULT_LANG, origin: str = "",
               site_key: str = "") -> str:
    """PCL 侧的验证页。**不组装主页**，所以不会调 uapi / ip-api。"""
    lang = lang or DEFAULT_LANG
    base = (origin or "").rstrip("/")
    if need_turnstile(ip, site_key):
        # 连错够次数：整张卡只留「请验证您是真人」+ 一个按钮
        return palette.apply(_cf_card(ip, base, lang),
                             profiles.PROFILES.palette_for(ip))
    else:
        token, _code = make_token(ip)
        image = base + "/verify_img.png?t=" + urllib.parse.quote(token)
        fmt = ("{Binding Path=Text,ElementName=vcode,StringFormat='{}"
               + base + "/verify_submit.json?t=" + token + "&q={0}'}")
        body = (
            '<local:MyImage Source="' + escape_url_attr(image) + '" Height="76"'
            ' HorizontalAlignment="Center" Margin="0,14,0,0" />'
            '<Border Margin="0,12,0,0" Height="38" Width="260" HorizontalAlignment="Center"'
            ' Background="{DynamicResource ColorBrush7}" CornerRadius="5">'
            '<local:MyTextBox x:Name="vcode" Height="38" Margin="10,0"'
            ' HintText="' + escape_attr(t("verify.img_hint", lang)) + '"'
            ' Foreground="{DynamicResource ColorBrush2}" VerticalAlignment="Center" /></Border>'
            '<local:MyIconTextButton Height="40" Margin="0,12,0,0" HorizontalAlignment="Center"'
            ' Text="' + escape_attr(t("verify.submit", lang)) + '" LogoScale="0.85"'
            ' ColorType="Highlight" Logo="' + escape_attr(ICON_SAVE)
            + '" EventType="打开帮助" EventData="' + escape_url_attr(fmt) + '" />'
        )
    page = (
        '<local:MyCard Title="' + escape_attr(t("verify.title", lang)) + '" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'
        '<TextBlock Text="' + escape_attr(t("verify.need_title", lang)) + '" FontSize="24"'
        ' FontWeight="Bold" HorizontalAlignment="Center" Foreground="#FF000000" />'
        + body +
        '<local:MyIconTextButton Height="36" Margin="0,14,0,0" HorizontalAlignment="Center"'
        ' Text="' + escape_attr(t("verify.refresh", lang)) + '" LogoScale="0.85"'
        ' Logo="' + escape_attr(ICON_REFRESH) + '" EventType="刷新页面" EventData="-" />'
        "</StackPanel></local:MyCard>"
    )
    return palette.apply(page, profiles.PROFILES.palette_for(ip))


def submit_xaml(ip: str, config, lang: str = DEFAULT_LANG, origin: str = "",
                token: str = "", answer: str = "", site_key: str = "",
                home_builder=None) -> str:
    """PCL 点「提交」后打开的 `.xaml`：判定、记账、给结果。

    通过时**直接返回主页本体**（`home_builder()`）——PCL 会把这段 XAML 当成
    帮助页内容渲染，看上去就是主页，不用再看一张「验证通过」的中间页。
    """
    lang = lang or DEFAULT_LANG
    base = (origin or "").rstrip("/")
    ok, reason = check_token(ip, token, answer)
    if ok:
        mark_verified(ip, config)
        clear_fail(ip)
        debug("[Verify] " + ip + " 通过访问验证（PCL 内）")
        if home_builder is not None:
            try:
                body = home_builder()
                if body:
                    return body
            except Exception as exc:  # noqa: BLE001 - 渲染失败也要回退成提示页
                warn("[Verify] 验证通过后渲染主页失败：" + repr(exc))
        head = t("verify.ok_title", lang)
        desc = t("verify.ok_page_desc", lang)
        extra = ('<TextBlock Text="' + escape_attr(t("verify.ok_line", lang)) + '" FontSize="11"'
                 ' Foreground="{DynamicResource ColorBrush3}" Margin="0,10,0,0" />')
    else:
        count = note_fail(ip)
        head = t("verify.fail_title", lang)
        if count >= FAIL_FOR_CF and site_key:
            # 连错够次数就整张卡换成「去浏览器验证」，不再贴第二次失败提示
            return palette.apply(_cf_card(ip, base, lang),
                                 profiles.PROFILES.palette_for(ip))
        else:
            left = max(0, FAIL_FOR_CF - count)
            if reason == "expired":
                desc = t("verify.expired", lang)
            else:
                desc = t("verify.wrong_left", lang, n=left)
            token, _code = make_token(ip)
            image = base + "/verify_img.png?t=" + urllib.parse.quote(token)
            fmt = ("{Binding Path=Text,ElementName=vsb,StringFormat='{}"
                   + base + "/verify_submit.json?t=" + token + "&q={0}'}")
            extra = (
                '<local:MyImage Source="' + escape_url_attr(image) + '" Height="76"'
                ' HorizontalAlignment="Center" Margin="0,12,0,0" />'
                '<Border Margin="0,12,0,0" Height="38" Width="260" HorizontalAlignment="Center"'
                ' Background="{DynamicResource ColorBrush7}" CornerRadius="5">'
                '<local:MyTextBox x:Name="vsb" Height="38" Margin="10,0"'
                ' HintText="' + escape_attr(t("verify.img_hint", lang)) + '"'
                ' Foreground="{DynamicResource ColorBrush2}" VerticalAlignment="Center" /></Border>'
                '<local:MyIconTextButton Height="40" Margin="0,12,0,0" HorizontalAlignment="Center"'
                ' Text="' + escape_attr(t("verify.submit", lang)) + '" LogoScale="0.85"'
                ' ColorType="Highlight" Logo="' + escape_attr(ICON_SAVE)
                + '" EventType="打开帮助" EventData="' + escape_url_attr(fmt) + '" />'
            )
    page = (
        '<local:MyCard Title="' + escape_attr(t("verify.title", lang)) + '" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'
        '<TextBlock Text="' + escape_attr(head) + '" FontSize="22" FontWeight="Bold"'
        ' HorizontalAlignment="Center" Foreground="#FF000000" />'
        '<TextBlock Text="' + escape_attr(desc) + '" FontSize="11" TextWrapping="Wrap"'
        ' Foreground="{DynamicResource ColorBrush3}" Margin="0,10,0,0" />'
        + extra +
        '<local:MyIconTextButton Height="36" Margin="0,16,0,0" HorizontalAlignment="Center"'
        ' Text="' + escape_attr(t("verify.refresh", lang)) + '" LogoScale="0.85"'
        ' Logo="' + escape_attr(ICON_REFRESH) + '" EventType="刷新页面" EventData="-" />'
        "</StackPanel></local:MyCard>"
    )
    return palette.apply(page, profiles.PROFILES.palette_for(ip))


# --------------------------------------------------------------------------- 路由

_CF_SCRIPT = (
    "<script src=\"https://challenges.cloudflare.com/turnstile/v0/api.js\""
    " async defer></script>"
)


def cf_html(ip: str, origin: str, lang: str, site_key: str, token: str,
            error: str = "") -> str:
    """浏览器里的 Cloudflare Turnstile 页（连续错 3 次后才用）。"""
    lang = lang or DEFAULT_LANG
    title = t("verify.cf_page_title", lang)
    hint = t("verify.cf_page_desc", lang)
    tip = t("verify.cf_ok", lang)
    err_html = ('<div class="err">' + html_escape(error) + "</div>") if error else ""
    script = (
        "<script>function cfDone(t){location.href='"
        + '/verify_cf_post?c=' + html_escape(token)
        + "&r='+encodeURIComponent(t);}</script>"
    )
    return (
        "<!doctype html><html lang=\"" + html_escape(lang) + "\"><head>"
        "<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>" + html_escape(title) + "</title><style>" + _STYLE + "</style>"
        + _CF_SCRIPT + "</head><body>"
        "<div class=\"card\"><h1>" + html_escape(title) + "</h1>"
        "<p>" + html_escape(hint) + "</p>"
        "<div style=\"display:flex;justify-content:center;margin:18px 0\">"
        "<div class=\"cf-turnstile\" data-sitekey=\"" + html_escape(site_key)
        + "\" data-callback=\"cfDone\"></div></div>"
        + err_html +
        "<div class=\"tip\">" + html_escape(tip) + "</div>"
        "</div>" + script + "</body></html>"
    )


def _clear_cf(ip: str) -> None:
    data = _load()
    data.get("cf", {}).pop(ip, None)
    _save(data)


def handle(service, path: str, query: str, ip: str, origin: str = ""):
    """访问验证相关路径。返回 Response；关掉验证时返回 None（交给 404）。"""
    from .server import Response

    config = service.config
    if not getattr(config, "verify_enabled", True):
        return None

    lang = profiles.PROFILES.lang_for(ip)
    base = (service.config.resolved_base_url(origin)
            if hasattr(service.config, "resolved_base_url") else origin)
    params = urllib.parse.parse_qs(query or "")

    def _html(text: str):
        return Response(body=text.encode("utf-8"), content_type="text/html; charset=utf-8")

    def _json(payload: dict):
        return Response(body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        content_type="application/json; charset=utf-8")

    # --- PCL 里的验证码图片
    if path == "/verify_img.png":
        token = (params.get("t") or [""])[0]
        return Response(body=image_bytes(ip, token), content_type="image/png")

    # --- PCL 点「提交」：先要一份 meta，再要页面本体
    if path == "/verify_submit.json":
        # 标题用站点名：验证通过时这一段会直接换成主页内容，
        # 顶部写「访问验证」会显得像还停在中间页
        title = getattr(config, "site_name", "") or t("verify.title", lang)
        return _json({"Title": title,
                      "Description": t("verify.submit_desc", lang)})
    if path == "/verify_submit.xaml":
        token = (params.get("t") or [""])[0]
        answer = (params.get("q") or [""])[0]

        def _home_body() -> str:
            # homepage() 返回 (渲染好的 XAML, HomeData)，只要前一半
            text, _data = service.homepage(ip, origin)
            return text or ""

        body = submit_xaml(ip, config, lang, base, token, answer,
                           getattr(config, "turnstile_site_key", ""),
                           home_builder=_home_body)
        return Response.xaml(body)

    # --- Cloudflare 回执
    if path == "/verify_cf_post":
        token = (params.get("c") or [""])[0]
        response = (params.get("r") or [""])[0]
        if not check_cf_token(ip, token):
            return _html(cf_html(ip, base, lang,
                                 getattr(config, "turnstile_site_key", ""), cf_token(ip),
                                 error=t("verify.cf_expired", lang)))
        ok, reason = turnstile_ok(getattr(config, "turnstile_secret", ""), response, ip)
        if ok:
            mark_verified(ip, config)
            clear_fail(ip)
            _clear_cf(ip)
            debug("[Verify] " + ip + " 通过 Cloudflare 验证")
            return _html(ok_html(ip, base, lang))
        warn("[Verify] Turnstile 未通过：" + str(reason))
        return _html(cf_html(ip, base, lang,
                             getattr(config, "turnstile_site_key", ""), token,
                             error=t("verify.cf_fail", lang)))

    # --- 浏览器版 Cloudflare 页
    if path in ("/verify_cf", "/verify_cf/"):
        if is_verified(ip):
            return _html(ok_html(ip, base, lang))
        token = (params.get("c") or [""])[0]
        if not check_cf_token(ip, token):
            token = cf_token(ip)
        site_key = getattr(config, "turnstile_site_key", "")
        if not site_key:
            # 没配站点密钥就退回看图验证，别给一个空白页
            return _html(page_html(ip, base, lang))
        return _html(cf_html(ip, base, lang, site_key, token))

    # --- 浏览器版看图验证
    if is_verified(ip):
        return _html(ok_html(ip, base, lang))

    left = locked_seconds(ip)
    if left > 0:
        minutes = max(1, int(left / 60) + 1)
        return _html(page_html(ip, base, lang, error=t("verify.locked", lang, minutes=minutes)))

    answer = (params.get("answer") or [""])[0]
    token = (params.get("token") or [""])[0]

    if token or answer:
        ok, reason = check_token(ip, token, answer)
        if ok:
            mark_verified(ip, config)
            clear_fail(ip)
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
