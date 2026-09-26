# -*- coding: utf-8 -*-
"""体检脚本（对应上游 scripts/doctor.py）。

先查本地一致性，再（可选）探测正在运行的服务：

    python -m pclhome doctor              本地检查 + 探测默认地址
    python -m pclhome doctor --offline    只做本地检查
    python -m pclhome doctor --url http://127.0.0.1:8787

本地检查里有两项是"移植保真度"的回归测试：
哈希与农历都拿上游 JS 的实际输出当基准向量，改坏了会立刻报 FAIL。
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

from . import ai
from . import calc
from .config import ROOT, TEMPLATES_DIR, Config, load_config
from .data import calendar as cal_data
from .data import play, text
from .geo import describe as geo_describe
from .geo import is_unlocatable
from .lunar import solar_to_lunar
from .personalize import hash_code
from .render import build_home_data, find_leftover_placeholders, render_homepage
from .store import Store
from .weather import WeatherService
from .xaml import build_action_buttons


PCL_UA = "PCL2/2.13.0"

# 上游 functions/_lib/content.js 等文件在 Node 里实际跑出来的哈希值
HASH_VECTORS = [
    ("1.2.3.4|2026-09-25|score", 1638887361),
    ("114.114.114.114|2026-01-01|color", 2019515300),
    ("", 5381),
    ("a", 177670),
    ("测试IP|2026-12-31|quote", 1791733359),
    ("255.255.255.255|2026-06-15|fortune_good", 242316626),
    ("8.8.8.8|2026-03-08|egg", 1594904809),
]

# 公历 → 农历 基准（上游 solar2lunar 的输出）
LUNAR_VECTORS = [
    ("2026-02-17", (2026, 1, 1)),    # 春节
    ("2026-09-25", (2026, 8, 15)),   # 中秋
    ("2026-06-19", (2026, 5, 5)),    # 端午
    ("2026-01-01", (2025, 11, 13)),
    ("2033-01-01", (2032, 12, 1)),   # 闰月年附近
]

# 数据规模（上游 README 里公开承诺的数量）
EXPECTED_COUNTS = {
    "QUOTES": (text.QUOTES, 68),
    "EGGS": (text.EGGS, 50),
    "COLORS": (text.COLORS, 24),
    "FORTUNE_GOOD": (text.FORTUNE_GOOD, 100),
    "CHALLENGES": (play.CHALLENGES, 100),
    "SEEDS": (play.SEEDS, 97),
    "QUIZ": (play.QUIZ, 123),
    "FESTIVALS": (cal_data.FESTIVALS, 12),
    "LUNAR_FESTIVALS": (cal_data.LUNAR_FESTIVALS, 7),
    "LUNAR_INFO": (cal_data.LUNAR_INFO, 201),
}

# Custom.xaml 由 PCL 注入命名空间，本地校验时补上
_XAML_ROOT_NS = ('xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
                 'xmlns:sys="clr-namespace:System;assembly=mscorlib" '
                 'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" '
                 'xmlns:local="clr-namespace:PCL;assembly=Plain Craft Launcher 2"')


class Report:
    def __init__(self):
        self.ok = 0
        self.warn = 0
        self.fail = 0

    def _line(self, tag, msg):
        print("  [" + tag + "] " + msg)

    def good(self, msg):
        self.ok += 1
        self._line("OK", msg)

    def warning(self, msg):
        self.warn += 1
        self._line("WARN", msg)

    def bad(self, msg):
        self.fail += 1
        self._line("FAIL", msg)


# ============ 本地检查 ============

def check_data(report: Report) -> None:
    print("\n[1/8] 数据完整性")
    for name, (value, expected) in EXPECTED_COUNTS.items():
        if len(value) == expected:
            report.good(name + " 共 " + str(expected) + " 条")
        else:
            report.bad(name + " 应为 " + str(expected) + " 条，实际 " + str(len(value)) + " 条")


def check_hash(report: Report) -> None:
    print("\n[2/8] 哈希算法与上游一致性（决定「同一人当天内容固定」）")
    mismatches = []
    for raw, expected in HASH_VECTORS:
        got = hash_code(raw)
        if got != expected:
            mismatches.append(repr(raw) + " 期望 " + str(expected) + " 实际 " + str(got))
    if mismatches:
        report.bad("哈希与上游不一致：" + "；".join(mismatches[:3]))
    else:
        report.good("哈希与上游逐位一致（" + str(len(HASH_VECTORS)) + " 个基准向量）")


def check_lunar(report: Report) -> None:
    print("\n[3/8] 农历换算与上游一致性")
    mismatches = []
    for raw, expected in LUNAR_VECTORS:
        year, month, day = (int(x) for x in raw.split("-"))
        got = solar_to_lunar(date(year, month, day))[:3]
        if got != expected:
            mismatches.append(raw + " 期望 " + str(expected) + " 实际 " + str(got))
    if mismatches:
        report.bad("农历换算不一致：" + "；".join(mismatches[:3]))
    else:
        report.good("农历换算与上游一致（" + str(len(LUNAR_VECTORS)) + " 个基准日期）")


def check_config(report: Report, config: Config) -> None:
    print("\n[4/8] 配置检查")
    if config.admin_token == "admin":
        if config.host in ("127.0.0.1", "localhost", "::1"):
            report.warning("后台令牌仍是默认值 admin（本机自用可忽略）")
        else:
            report.bad("服务监听 " + config.host + "，但后台令牌仍是默认的 admin，请立刻修改")
    else:
        report.good("后台令牌已自定义")

    if config.base_url:
        report.good("BASE_URL 已固定为 " + config.base_url)
    else:
        report.warning("BASE_URL 未配置，将按请求 Host 推导（反向代理后建议固定）")

    report.good("来源守卫：" + ("开启" if config.guard_clients else "关闭")
                + "；天气：" + ("开启" if config.enable_weather else "关闭")
                + "；壁纸：" + ("开启" if config.enable_wallpaper else "关闭"))

    if not config.enable_weather:
        pass
    elif config.weather_city:
        report.good("天气固定城市：" + config.weather_city + "（所有人看同一个城市）")
    elif config.enable_geo:
        report.good("天气按访客 IP 定位城市（查不到则退回服务器所在地）")
    else:
        report.warning("天气按服务器 IP 定位：所有访客会看到服务器所在地的天气")

    if not config.enable_ai:
        report.good("AI 日志分析：关闭")
    elif config.ai_api_key:
        report.good("AI 日志分析：" + config.ai_model + " @ " + config.ai_api_base
                    + "，内置密钥已配置，每 IP 每天 " + str(config.ai_daily_limit) + " 次")
    else:
        report.warning("AI 日志分析已开启但没有内置密钥，访客只能用自带密钥")


def check_geo(report: Report) -> None:
    """访客 IP → 城市 这一步的纯逻辑（不联网，联网部分只在真跑起来时才用）。"""
    print("\n[5/8] 访客 IP 归属地（天气按 IP 定位用）")

    # 这些地址查归属地要么没意义、要么会白等一次超时，必须直接判为"不用查"
    unlocatable = ["127.0.0.1", "0.0.0.0", "192.168.1.10", "10.0.0.1", "172.16.0.1",
                   "169.254.1.1", "224.0.0.1", "::1", "unknown", ""]
    wrong = [ip for ip in unlocatable if not is_unlocatable(ip)]
    if wrong:
        report.bad("这些地址应判为无法定位，却会去外呼查询：" + ", ".join(repr(x) for x in wrong))
    else:
        report.good("内网/回环/保留/非法地址均判为无法定位（" + str(len(unlocatable)) + " 个）")

    locatable = ["112.47.125.174", "8.8.8.8", "220.181.38.148", "2001:4860:4860::8888"]
    wrong = [ip for ip in locatable if is_unlocatable(ip)]
    if wrong:
        report.bad("这些公网地址被误判为无法定位：" + ", ".join(wrong))
    else:
        report.good("公网 IPv4/IPv6 均判为可查询（" + str(len(locatable)) + " 个）")

    sample = geo_describe({"city": "泉州市", "adcode": "350500", "source": "pconline"})
    if sample != "泉州市 350500（pconline）":
        report.bad("归属地描述格式异常：" + sample)
    elif geo_describe(None) != "未定位":
        report.bad("查不到时的描述应为「未定位」")
    else:
        report.good("归属地描述格式正常：" + sample)


def check_templates(report: Report, config: Config) -> None:
    print("\n[6/8] 模板与生成物")
    for name in ("Custom.xaml.tpl",):
        path = TEMPLATES_DIR / name
        if path.exists():
            report.good("模板存在：" + name)
        else:
            report.bad("缺少模板：" + name)

    for name in ("Custom.xaml",):
        path = ROOT / name
        if not path.exists():
            report.bad("缺少生成物：" + name + "（先跑 python -m pclhome generate）")
        elif path.stat().st_size == 0:
            report.bad("生成物为空：" + name)
        else:
            report.good(name + " 已生成（" + str(path.stat().st_size) + " 字节）")

    # 模板里用到的 {{TOKEN}} 必须都能被填上（不允许留 {{...}} 裸奔到 PCL 那边）
    provided = {
        "Custom.xaml.tpl": {"BASE_URL", "WALLPAPER_URL", "SITE_ITEMS", "ACTION_BUTTONS"},
    }
    for name, tokens_ok in provided.items():
        path = TEMPLATES_DIR / name
        if not path.exists():
            continue
        tokens = set(re.findall(r"\{\{\s*([A-Z][A-Z0-9_]*)", path.read_text(encoding="utf-8")))
        unknown = sorted(tokens - tokens_ok)
        if unknown:
            report.bad(name + " 里有没人提供的占位符：" + ", ".join(unknown))
        else:
            report.good(name + " 占位符齐全（" + str(len(tokens)) + " 个）")


def _fake_render(config: Config):
    """用假 IP 走一遍完整渲染，不联网。"""
    config = dataclasses.replace(config, enable_weather=False)
    store = Store()
    weather = WeatherService(config)
    home = build_home_data(config, store, weather, "203.0.113.7", "http://localhost")
    homepage = render_homepage((ROOT / "Custom.xaml").read_text(encoding="utf-8"), home, config, "http://localhost")
    return homepage


def check_rendered(report: Report, config: Config) -> None:
    print("\n[7/8] 渲染结果（假 IP 走一遍完整替换）")
    try:
        homepage = _fake_render(config)
    except Exception as exc:
        report.bad("渲染抛异常：" + repr(exc))
        return

    for label, body in (("主页", homepage),):
        leftover = find_leftover_placeholders(body)
        if leftover:
            report.bad(label + " 仍有未替换的占位符：" + ", ".join(leftover[:8]))
        else:
            report.good(label + " 占位符已全部替换（" + str(len(body)) + " 字节）")

        if "<StackPanel" not in body:
            report.bad(label + " 里找不到 StackPanel，内容可能异常")
        else:
            report.good(label + " 结构正常")

    # XML 良构性：标签不闭合、属性没转义都会在这里暴露
    for label, body, wrap in (("主页", homepage, True),):
        source = ('<?xml version="1.0" encoding="utf-8"?><root ' + _XAML_ROOT_NS + ">" + body + "</root>"
                  if wrap else body)
        try:
            ET.fromstring(source)
            report.good(label + " XML 良构")
        except ET.ParseError as exc:
            report.bad(label + " XML 解析失败：" + str(exc))

    # AI 那两个页面开着的时候也必须是良构 XML。不联网：只读当前状态。
    try:
        ai_config = dataclasses.replace(config, enable_ai=True, ai_api_key="sk-doctor")
        page = ai.build_page(ai_config, "203.0.113.7", "http://localhost")
        own_page = ai.build_own_page(ai_config, "203.0.113.7", "http://localhost")
        for body in (page, own_page):
            ET.fromstring('<?xml version="1.0" encoding="utf-8"?><root ' + _XAML_ROOT_NS
                          + ">" + body + "</root>")
        problems = []
        for name in ("ailoginput", "公用密钥", "私人密钥", "MC崩溃？AI智能分析", "/home.json"):
            if name not in page:
                problems.append("分析页缺 " + name)
        for name in ("ailoginput", "aikeyinput", "aibaseinput", "aimodelinput",
                     "开始分析", "/home.json"):
            if name not in own_page:
                problems.append("私人密钥页缺 " + name)
        if problems:
            report.bad("；".join(problems))
        else:
            report.good("AI 两个页面 XML 良构（" + str(len(page)) + " + "
                        + str(len(own_page)) + " 字节）")
    except Exception as exc:
        report.bad("AI 页面构建失败：" + repr(exc))

    # 计算器：列表页 + 每个计算器页都要是良构 XML（空输入和带输入各来一遍）
    try:
        landing = calc.build_landing("http://localhost")
        ET.fromstring('<?xml version="1.0" encoding="utf-8"?><root ' + _XAML_ROOT_NS
                      + ">" + landing + "</root>")
        broken = []
        for item in calc.CALCS:
            for raw in ("", "1 2"):
                body = calc.build_calc_page(item, raw, "http://localhost")
                try:
                    ET.fromstring('<?xml version="1.0" encoding="utf-8"?><root '
                                  + _XAML_ROOT_NS + ">" + body + "</root>")
                except ET.ParseError as exc:
                    broken.append(item["id"] + "：" + str(exc))
                    break
        if broken:
            report.bad("计算器页 XML 解析失败：" + "；".join(broken))
        else:
            report.good("计算器 " + str(len(calc.CALCS)) + " 个页面 XML 良构")

        # 入口是一行一个列表项，每个计算器都得有自己那一行，点进去得是它的 .json
        missing = [item["id"] for item in calc.CALCS
                   if "/calc_" + item["id"] + ".json" not in landing]
        if missing:
            report.bad("计算器列表里缺入口：" + "、".join(missing))
        else:
            report.good("计算器列表 " + str(len(calc.CALCS)) + " 个入口齐全")

        # 没有本地镜像就会退回 Wiki 远程地址（Wiki 按 UA 拦，多半拉不到），
        # 再不行才是 PCL 内置占位图。两种情况都算"这行没图标"。
        no_icon = [item["id"] for item in calc.CALCS
                   if "/images/icons/" not in calc._calc_logo("", item)]
        if no_icon:
            report.warning("计算器列表缺图标（会显示成无图标）：" + "、".join(no_icon))
        else:
            report.good("计算器列表图标均已镜像到本地")
    except Exception as exc:
        report.bad("计算器页构建失败：" + repr(exc))

    # 公式抽查：这几个都有标准答案，改坏了这里会红
    known = [("damage", "8,5", "11"), ("damage", "8,5,0,0,2,0,1", "26.73"),
             ("damage", "8,0,5,0,0,0,0", "20"),
             ("exp", "30", "1395"), ("exp", "16", "352"), ("exp", "32", "1628"),
             ("armor", "20 20 8 0", "8"), ("seed", "hello", "99162322"),
             ("color", "#FF8800", "16746496"),
             ("uuid", "Notch", "b50ad385-829d-3141-a216-7e7d7539ba7f"),
             ("chunk", "100 -200", "6")]
    wrong = []
    for cid, raw, expect in known:
        try:
            rows = calc.CALC_BY_ID[cid]["run"](raw)
        except Exception as exc:
            wrong.append(cid + "(" + raw + ") 抛异常 " + repr(exc))
            continue
        if expect not in " ".join(value for _label, value in rows):
            wrong.append(cid + "(" + raw + ") 里找不到 " + expect)
    if wrong:
        report.bad("计算器公式对不上：" + "；".join(wrong))
    else:
        report.good("计算器公式抽查通过（" + str(len(known)) + " 个标准答案）")

    # 主页那排按钮：AI 入口和计算器入口都必须带上绝对地址
    # 两个基础按钮 + 计算器，AI 开着再多一个，所以是 3 / 4
    for flag, expect in ((False, 3), (True, 4)):
        buttons = build_action_buttons(dataclasses.replace(config, enable_ai=flag))
        count = buttons.count("<local:MyIconTextButton")
        if count != expect:
            report.bad("功能按钮：" + ("开" if flag else "关") + " AI 时应有 " + str(expect)
                       + " 个，实际 " + str(count))
            continue
        missing = [token for token in ("__CALC_ENTRY__", "__AI_ENTRY__" if flag else "")
                   if token and token not in buttons]
        if missing:
            report.bad("功能按钮里缺占位符：" + "、".join(missing))
        else:
            report.good("功能按钮 " + str(count) + " 个（AI " + ("开" if flag else "关") + "）")
    # AI 入口是构建期塞进按钮排的，所以「有没有入口」看的是生成物；
    # 请求期只负责把 __AI_ENTRY__ 换成绝对地址。留着了就说明请求期没换。
    if "__AI_ENTRY__" in (ROOT / "Custom.xaml").read_text(encoding="utf-8"):
        if "__AI_ENTRY__" in homepage:
            report.bad("主页里的 __AI_ENTRY__ 没被换成地址，AI 入口会点不动")
        else:
            report.good("AI 入口地址已在请求期替换")

    # 关键内容抽查
    if "今日一言" in homepage or "每日一言" in homepage:
        report.good("每日一言区块存在")
    else:
        report.bad("主页里找不到每日一言区块")
    if "{user}" in homepage:
        report.good("玩家 ID 占位符 {user} 保留给 PCL 填充")
    else:
        report.warning("主页里找不到 {user} 占位符")


def check_static(report: Report, config: Config) -> None:
    print("\n[8/8] 静态资源")

    from .sites import local_icon, site_slug
    if not config.sites:
        report.bad("配置里一个功能网站都没有")
    else:
        missing = [str(s.get("name") or site_slug(s)) for s in config.sites
                   if not local_icon(config, site_slug(s))]
        if missing:
            report.warning("这些站的图标没镜像到本地，页面会用远程地址（PCL 拉取失败时会显示无图标占位）："
                           + "、".join(missing))
        else:
            report.good("功能网站 " + str(len(config.sites)) + " 个，图标均已镜像到本地")

    music = ROOT / "static" / "music"
    tracks = list(music.glob("*.mp3")) + list(music.glob("*.ogg")) + list(music.glob("*.m4a")) if music.exists() else []
    if tracks:
        report.good("音乐文件 " + str(len(tracks)) + " 首")
    else:
        report.warning("static/music/ 里没有音频文件（本项目不分发第三方音乐，需自行放入）")


# ============ 线上探测 ============

def _probe(url: str, ua: str, timeout: float = 15.0):
    request = urllib.request.Request(url, headers={"User-Agent": ua})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), ""
    except Exception as exc:
        return None, str(exc), ""


def check_online(report: Report, base: str) -> None:
    print("\n[线上] 探测 " + base)
    base = base.rstrip("/")

    status, body, _ = _probe(base + "/Custom.xaml.version", PCL_UA)
    if status == 200 and body.strip().isdigit():
        report.good("/Custom.xaml.version 返回时间戳 " + body.strip())
    else:
        report.bad("/Custom.xaml.version 异常（HTTP " + str(status) + "）：" + body.strip()[:80])

    for label, path, ua in (("主页 /Custom.xaml", "/Custom.xaml", PCL_UA),):
        status, body, _ = _probe(base + path, ua)
        if status != 200:
            report.bad(label + " 返回 HTTP " + str(status))
            continue
        leftover = find_leftover_placeholders(body)
        if leftover:
            report.bad(label + " 仍含未替换占位符：" + ", ".join(leftover[:8]))
        elif "<StackPanel" not in body:
            report.bad(label + " 内容异常（没有 StackPanel）")
        else:
            report.good(label + " 正常（" + str(len(body)) + " 字节，占位符已替换）")

    # 来源守卫：爬虫 UA 应当被拒
    status, body, _ = _probe(base + "/Custom.xaml", "python-requests/2.31")
    if status == 200 and "访问被拒绝" in body:
        report.good("来源守卫生效：脚本 UA 被拦截")
    elif status == 200:
        report.warning("来源守卫未拦截脚本 UA（config.guard_clients 可能是 false）")
    else:
        report.warning("来源守卫探测未得到预期响应（HTTP " + str(status) + "）")


# ============ 入口 ============

def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    offline = "--offline" in argv
    url = "http://127.0.0.1:8787"
    if "--url" in argv:
        index = argv.index("--url")
        if index + 1 < len(argv):
            url = argv[index + 1]

    config = load_config()
    print("=" * 58)
    print("PCL2 个性化主页 · Python 复刻版 · 体检报告")
    print("=" * 58)

    report = Report()
    check_data(report)
    check_hash(report)
    check_lunar(report)
    check_config(report, config)
    check_geo(report)
    check_templates(report, config)
    check_rendered(report, config)
    check_static(report, config)

    if offline:
        print("\n[线上] 已跳过（--offline）")
    else:
        check_online(report, url)

    print("\n" + "=" * 58)
    print("通过 " + str(report.ok) + " 项 / 警告 " + str(report.warn) + " 项 / 失败 " + str(report.fail) + " 项")
    print("=" * 58)
    return 1 if report.fail else 0
