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
import math
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

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

# 节日倒计时的基准：（今天，最近的节日，还有几天）。节日那天的公历日期是查权威
# 日历得到的，**不是**这份代码自己的输出——农历节日逐年差半个月，拿"明年的月日"
# 套到今年算就会少算十来天（2026-09-26 该是国庆节 5 天，套错了会报重阳节 12 天），
# 所以这几个日子专门钉住那条路径。
COUNTDOWN_VECTORS = [
    ("2026-09-25", "中秋节", 0),      # 农历八月十五
    ("2026-09-26", "国庆节", 5),      # 下一个是 10-01 国庆节
    ("2026-10-01", "国庆节", 0),
    ("2026-01-02", "腊八节", 24),     # 腊八在 2026-01-26（农历 2025 年腊月初八）
    ("2026-04-05", "清明节", 0),      # 2026 年清明是 4 月 5 日
    ("2027-02-04", "除夕", 1),        # 除夕 2027-02-05，次日 2027-02-06 春节
                                      # （农历 2026 年的腊月落在公历 2027 年，跨年那条路）
]

# 节日横幅单独钉几个：公历、农历、节气、以及"月末"写法的除夕
BANNER_VECTORS = [
    ("2026-10-01", "国庆节"),
    ("2026-04-05", "清明节"),
    ("2026-09-25", "中秋节"),
    ("2027-02-05", "除夕"),
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
    print("\n[1/9] 数据完整性")
    for name, (value, expected) in EXPECTED_COUNTS.items():
        if len(value) == expected:
            report.good(name + " 共 " + str(expected) + " 条")
        else:
            report.bad(name + " 应为 " + str(expected) + " 条，实际 " + str(len(value)) + " 条")


# 中文汉字 + 中文标点 + 全角字符。后两类是刻意一起扫的：模板里烘死的
# 「，」「！」这种全角标点，在英文页面上同样刺眼，但只扫汉字是抓不到的。
CJK_RE = re.compile(r"[一-鿿㐀-䶿　-〿＀-￯]")


def check_i18n(report: Report) -> None:
    """三种语言的词条。

    要紧的一条是「英文里不许漏出中文」——``t()`` 缺键会悄悄回退到简中，
    页面上就会出现一句中文，很难发现。所以这条按**必须覆盖**来判。
    """
    print("\n[2/9] 多语言词条")
    from . import i18n
    i18n.reload_tables()

    zh = i18n.keys_of("zh-hans")
    hant = i18n.keys_of("zh-hant")
    en = i18n.keys_of("en")

    missing_hant = sorted(k for k in zh if k not in hant)
    if missing_hant:
        report.bad("繁中缺 " + str(len(missing_hant)) + " 个键：" + "、".join(missing_hant[:5]))
    else:
        report.good("繁中覆盖全部 " + str(len(zh)) + " 个键")

    # 英文字面里还可能残留中文（漏翻），所以除了键要对齐，值也要扫一遍
    untranslated = []
    for key in sorted(zh):
        if key.startswith("_"):
            continue
        value = i18n.table("en").get(key)
        if not isinstance(value, str):
            continue
        # calc.separator_hint 是唯一允许出现全角标点的：它本身就在说明
        # 「逗号中文英文都可以」，不写出那个「，」就说不清。
        if CJK_RE.search(value) and key != "calc.separator_hint":
            untranslated.append(key)
    if untranslated:
        report.bad("英文里还有中文（" + str(len(untranslated)) + " 条）："
                   + "、".join(untranslated[:5]))
    else:
        report.good("英文词条里没有残留中文")

    missing_en = sorted(k for k in zh if k not in en)
    if missing_en:
        report.bad("英文缺 " + str(len(missing_en)) + " 个键（这些会回退成中文）："
                   + "、".join(missing_en[:5]))
    else:
        report.good("英文覆盖全部 " + str(len(zh)) + " 个键（没有会回退成中文的）")

    # 天气描述是接口给的中文，长尾收不全，所以退一步走大类兜底。
    # 这里挑几个一定认得出的描述，确认英文下不会漏出中文。
    from .weather import weather_desc
    leaks = [d for d in ("晴", "局部多云", "雷阵雨伴有冰雹", "小雨", "冻雨", "霾")
             if CJK_RE.search(weather_desc(d, "en"))]
    if leaks:
        report.bad("天气描述在英文下漏出中文：" + "、".join(leaks))
    else:
        report.good("天气描述在英文下都有对应说法（长尾走大类兜底）")

    # 天气提示与问候语是"一组里随机挑一条"，各语言条数得一样，
    # 否则同一个人同一天在不同语言下会跳到不相干的第几条
    for key in sorted(k for k in zh if k.startswith(("weather.tips.", "greeting.sub."))):
        lengths = {}
        for lang in i18n.LANGS:
            value = i18n.table(lang).get(key)
            lengths[lang] = len(value) if isinstance(value, list) else -1
        if len(set(lengths.values())) != 1:
            report.bad(key + " 各语言条数不一致：" + str(lengths))
            break
    else:
        report.good("天气提示与问候语各语言条数一致")


def check_hash(report: Report) -> None:
    print("\n[3/9] 哈希算法与上游一致性（决定「同一人当天内容固定」）")
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
    print("\n[4/9] 农历换算与上游一致性")
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

    # 节日表（上游那份 + 我们补的那份）：每条都得算得出公历日期、三种语言都有词条。
    # 补了节日忘了配翻译，或者月日写错，都会在这儿冒出来。
    from . import i18n
    from . import lunar
    broken = []
    for item, kind in ([(f, "solar") for f in lunar.FESTIVALS]
                       + [(f, "lunar") for f in lunar.LUNAR_FESTIVALS]):
        key = lunar.festival_key(item, kind)
        for suffix in (".name", ".msg"):
            lack = [lang for lang in i18n.LANGS if not i18n.has(key + suffix, lang)]
            if lack:
                broken.append(key + suffix + " 缺 " + "/".join(lack))
        if not lunar.festival_dates(item, date.today()):
            broken.append(str(item.get("name")) + " 算不出公历日期")
    if broken:
        report.bad("节日表有问题：" + "；".join(broken[:4]))
    else:
        report.good("内置节日 " + str(len(lunar.FESTIVALS) + len(lunar.LUNAR_FESTIVALS))
                    + " 条都能算出日期，三种语言词条齐全")

    wrong = []
    for raw, name, days in COUNTDOWN_VECTORS:
        got = _countdown_of(date.fromisoformat(raw))
        if got != (name, days):
            wrong.append(raw + " 期望 " + name + " " + str(days) + " 天，实际 "
                         + (got[0] + " " + str(got[1]) + " 天" if got else "没有内容"))
    for raw, name in BANNER_VECTORS:
        hit = lunar.get_festival(date.fromisoformat(raw), None, "zh-hans")
        if not hit or hit.get("name") != name:
            wrong.append(raw + " 横幅期望 " + name + "，实际 "
                         + str((hit or {}).get("name") or "没有"))
    if wrong:
        report.bad("节日日期对不上：" + "；".join(wrong[:3]))
    else:
        report.good("节日倒计时与横幅日期正确（" + str(len(COUNTDOWN_VECTORS)) + " + "
                    + str(len(BANNER_VECTORS)) + " 个基准日期）")


def _countdown_of(today: date):
    """倒计时胶囊里那句 "还有几天"，取不出来返回 None。"""
    from . import lunar
    body = lunar.build_countdown_xaml(today, None, None, "zh-hans")
    hit = re.search(r'Text="([^"]*)"', body)
    if not hit:
        return None
    found = re.search(r"([0-9]+) 天", hit.group(1))
    if found:
        return re.sub(r" · 还有 [0-9]+ 天", "", hit.group(1)), int(found.group(1))
    return re.sub(r"^今天就是 |！$", "", hit.group(1)), 0


def check_config(report: Report, config: Config) -> None:
    print("\n[5/9] 配置检查")
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
                    + "，内置密钥已配置，每 IP " + config.ai_window_text() + " "
                    + str(config.ai_rate_limit) + " 次")
    else:
        report.warning("AI 日志分析已开启但没有内置密钥，访客只能用自带密钥")


def check_geo(report: Report) -> None:
    """访客 IP → 城市 这一步的纯逻辑（不联网，联网部分只在真跑起来时才用）。"""
    print("\n[6/9] 访客 IP 归属地（天气按 IP 定位用）")

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
    print("\n[7/9] 模板与生成物")
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


def _fake_render(config: Config, lang: str = "zh-hans"):
    """用假 IP 走一遍完整渲染，不联网。"""
    config = dataclasses.replace(config, enable_weather=False)
    store = Store()
    weather = WeatherService(config)
    home = build_home_data(config, store, weather, "203.0.113.7", "http://localhost", lang)
    return render_homepage((ROOT / "Custom.xaml").read_text(encoding="utf-8"), home,
                           config, "http://localhost", lang)


# 扫"页面上还有没有中文"时要先摘掉的三类东西：
#   * XAML 注释——那些是写给开发者看的，不进界面
#   * EventType / EventData——PCL 的内部事件名（「打开帮助」这类），本来就得是中文
#   * 每日一言那一段——用户明确要求不翻
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_EVENT_RE = re.compile(r'Event(?:Type|Data)="[^"]*"')
_QUOTE_RE = re.compile(r'<TextBlock[^>]*MaxWidth="540"[^>]*/>', re.S)


def visible_text(xaml: str) -> str:
    """页面里访客真的能看到的文字（去掉注释、事件名和每日一言）。"""
    text = _COMMENT_RE.sub("", xaml)
    text = _QUOTE_RE.sub("", text)
    return _EVENT_RE.sub("", text)


def _cjk_hits(xaml: str) -> list:
    """页面里残留的中文片段，便于定位。

    ``calc.separator_hint`` 那句在英文页面里本来就要写出「，」来说明分隔符，
    所以先把这句原文摘掉再扫。
    """
    text = visible_text(xaml).replace(calc.separator_hint("en"), "")
    return [match.group(0).strip()[:60]
            for match in re.finditer(r'[^"<>\n]*' + CJK_RE.pattern + r'[^"<>\n]*', text)]


def _check_localised_pages(report: Report, config: Config) -> None:
    """三种语言各渲染一遍，确认英文页面上不漏中文。

    这是整套多语言里最容易悄悄坏掉的一环：``t()`` 缺键会回退成简中，
    页面上就冒出一句中文，得盯着看才发现。
    """
    from . import i18n

    for lang in i18n.LANGS:
        try:
            _fake_render(config, lang)
        except Exception as exc:
            report.bad("渲染（" + lang + "）抛异常：" + repr(exc))
            return

    try:
        problems = []
        for label, body in _english_pages(config):
            hits = _cjk_hits(body)
            if hits:
                problems.append(label + "（" + hits[0] + "）")
        if problems:
            report.bad("英文页面里还有中文：" + "；".join(problems[:6]))
        else:
            report.good("英文主页 / 计算器 / AI 页面都没有残留中文（每日一言除外）")
    except Exception as exc:
        report.bad("英文页面扫描失败：" + repr(exc))


def _english_pages(config: Config) -> list:
    """所有应当全英文的页面，``[(名字, XAML)]``。"""
    ai_config = dataclasses.replace(config, enable_ai=True, ai_api_key="sk-doctor")
    pages = [("主页", _fake_render(config, "en")),
             ("计算器列表", calc.build_landing("http://localhost", "en")),
             ("AI 页", ai.build_page(ai_config, "203.0.113.7", "http://localhost", "en")),
             ("私人密钥页", ai.build_own_page(ai_config, "203.0.113.7", "http://localhost", "en"))]
    for item in calc.CALCS:
        for raw in ("", "1 2"):
            pages.append(("计算器 " + item["id"] + "/" + (raw or "空"),
                          calc.build_calc_page(item, raw, "http://localhost", "en")))
    return pages


def _settings_pages() -> list:
    """设置页的几种形态，``[(名字, XAML)]``——空着、导出（多一个复制按钮）、三种语言。

    **用临时 store**：带动作的查询会真的写记录（``?export=1`` 要有一份记录才出得来），
    不能让体检往 ``var/profiles.json`` 里塞测试数据，所以把模块里那份单例临时换成
    临时文件上的一份，跑完换回来。``settings`` 里是 ``profiles.PROFILES`` 这样取的，
    换掉模块属性它就跟着换。
    """
    import tempfile
    from . import profiles as profiles_module
    from . import settings as settings_module

    ip = "203.0.113.7"
    pages = []
    with tempfile.TemporaryDirectory() as tmp:
        real = profiles_module.PROFILES
        profiles_module.PROFILES = profiles_module.Profiles(Path(tmp) / "profiles.json")
        try:
            for lang in ("zh-hans", "en", "zh-hant"):
                profiles_module.PROFILES.bind(ip, name="Doctor", lang=lang)
                for suffix, query in (("", ""), ("/导出", "export=1")):
                    pages.append(("设置页/" + lang + suffix,
                                  settings_module.build_settings_page(
                                      "http://localhost", ip, query)))
        finally:
            profiles_module.PROFILES = real
    return pages


def check_rendered(report: Report, config: Config) -> None:
    print("\n[8/9] 渲染结果（假 IP 走一遍完整替换）")
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
        landing = calc.build_landing("http://localhost", "zh-hans")
        ET.fromstring('<?xml version="1.0" encoding="utf-8"?><root ' + _XAML_ROOT_NS
                      + ">" + landing + "</root>")
        broken = []
        for item in calc.CALCS:
            for raw in ("", "1 2"):
                body = calc.build_calc_page(item, raw, "http://localhost", "zh-hans")
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

        # 每个计算器页都要有：一句统一的输入提示 + 一个接了输入框的计算按钮
        bad = []
        for item in calc.CALCS:
            body = calc.build_calc_page(item, "", "http://localhost", "zh-hans")
            if calc.separator_hint("zh-hans") not in body:
                bad.append(item["id"] + "（提示文字）")
            elif 'x:Name="calcinput"' not in body or "ElementName=calcinput" not in body:
                bad.append(item["id"] + "（输入框没接上按钮）")
        if bad:
            report.bad("计算器页面不对： " + "、".join(bad))
        else:
            report.good("计算器 " + str(len(calc.CALCS)) + " 个页面都有输入提示和计算按钮")

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

    # 设置页：空着 / 导出 / 三种语言都得是良构 XML。导出那一段（里面是"复制到
    # 剪切板"那个按钮）平时渲染不到，只有 ?export=1 才出得来，所以单拎出来验。
    try:
        pages = _settings_pages()
        broken = []
        for label, body in pages:
            try:
                ET.fromstring('<?xml version="1.0" encoding="utf-8"?><root ' + _XAML_ROOT_NS
                              + ">" + body + "</root>")
            except ET.ParseError as exc:
                broken.append(label + "：" + str(exc))
        if broken:
            report.bad("设置页 XML 解析失败：" + "；".join(broken[:3]))
        elif not any('EventType="复制文本"' in body for _, body in pages):
            report.bad("设置页导出后没有复制按钮（复制文本事件没了）")
        elif not any("PCLP1-" in body for _, body in pages):
            report.bad("设置页导出后没看到个性码")
        else:
            report.good("设置页 " + str(len(pages)) + " 种形态 XML 良构，导出带复制按钮")
    except Exception as exc:
        report.bad("设置页构建失败：" + repr(exc))

    # 公式抽查：这几个都有标准答案，改坏了这里会红
    known = [("damage", "8,5", "11"), ("damage", "8,5,0,0,2,0,1", "26.73"),
             ("stronghold", "0,0,0", "-204, -1692"),
             ("stronghold", "-8000,3000,0", "-6284, 4196"),
             ("damage", "8,0,5,0,0,0,0", "20"),
             ("exp", "30", "1395"), ("exp", "16", "352"), ("exp", "32", "1628"),
             ("armor", "20 20 8 0", "8"), ("seed", "hello", "99162322"),
             ("color", "#FF8800", "16746496"),
             ("uuid", "Notch", "b50ad385-829d-3141-a216-7e7d7539ba7f"),
             ("chunk", "100 -200", "6")]
    wrong = []
    for cid, raw, expect in known:
        try:
            rows = calc.CALC_BY_ID[cid]["run"](raw, "zh-hans")
        except Exception as exc:
            wrong.append(cid + "(" + raw + ") 抛异常 " + repr(exc))
            continue
        if expect not in " ".join(value for _label, value in rows):
            wrong.append(cid + "(" + raw + ") 里找不到 " + expect)
    if wrong:
        report.bad("计算器公式对不上：" + "；".join(wrong))
    else:
        report.good("计算器公式抽查通过（" + str(len(known)) + " 个标准答案）")

    # 要塞：环的个数是 Wiki 写死的 3/6/10/15/21/28/36/9，一共 128 个。
    # 随机数流错一步坐标照样像模像样，但环会散，这条能抓住。
    try:
        points = calc._strongholds(0)
        counts = [0] * 8
        for px, pz in points:
            ring = int(round((math.hypot(px, pz) / 16.0 - 128) / 192))
            counts[min(max(ring, 0), 7)] += 1
        if len(points) != 128 or counts != [3, 6, 10, 15, 21, 28, 36, 9]:
            report.bad("要塞环不对：一共 " + str(len(points)) + " 个，各环 " + str(counts))
        else:
            report.good("要塞 128 个，各环 3/6/10/15/21/28/36/9")
    except Exception as exc:
        report.bad("要塞计算失败：" + repr(exc))

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

    # 卡片配色：配色是"整体换号"实现的（见 palette.py），前提是**我们生成的
    # XAML 里只出现那四个角色号**。这条守住那个前提——哪天顺手写了个
    # ColorBrush5，换号就会漏掉它，页面颜色会半生不熟。
    try:
        from . import palette as pal
        bodies = list(_english_pages(config))
        bodies.append(("主页", _fake_render(config, "zh-hans")))
        bodies.extend(_settings_pages())

        stray = [(label, pal.stray_brushes(body)) for label, body in bodies
                 if pal.stray_brushes(body)]
        if stray:
            report.bad("页面里用了角色号以外的浓度（配色会漏改）："
                       + "；".join(label + " " + ",".join(nums) for label, nums in stray[:4]))
        else:
            report.good("配色角色号干净（" + str(len(bodies)) + " 个页面只用到 "
                        + "/".join(str(n) for n in sorted(pal.ROLE_BRUSH.values())) + "）")

        # 换号本身：挑一档把四个角色换到四个不同的号，确认换得干净
        chosen = {"panel": 8, "text": 3, "muted": 5, "dim": 4}
        allowed = {str(n) for n in chosen.values()}
        leftover = [(label, sorted(set(pal._BRUSH_RE.findall(pal.apply(body, chosen)))
                                   - allowed)) for label, body in bodies]
        bad = [(label, nums) for label, nums in leftover if nums]
        if bad:
            report.bad("配色换号没换干净：" + "；".join(
                label + " 还留着 " + ",".join(nums) for label, nums in bad[:4]))
        else:
            report.good("配色换号生效（" + str(len(bodies)) + " 个页面全部换到选定档位）")
    except Exception as exc:
        report.bad("配色检查失败：" + repr(exc))

    # 关键内容抽查
    if "今日一言" in homepage or "每日一言" in homepage:
        report.good("每日一言区块存在")
    else:
        report.bad("主页里找不到每日一言区块")
    if "{user}" in homepage:
        report.good("玩家 ID 占位符 {user} 保留给 PCL 填充")
    else:
        report.warning("主页里找不到 {user} 占位符")

    _check_localised_pages(report, config)


def check_static(report: Report, config: Config) -> None:
    print("\n[9/9] 静态资源")

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
    check_i18n(report)
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
