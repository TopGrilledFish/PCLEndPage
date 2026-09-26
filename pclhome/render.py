# -*- coding: utf-8 -*-
"""请求期组装：把静态模板 + 本次请求的数据拼成最终 XAML。

对应上游 _middleware.js 第 2.4 节（主页）的替换链。

占位符两类：

* ``{{TOKEN}}``  构建期替换（generate.py），如壁纸地址、功能网站列表
* ``__TOKEN__``  请求期替换（本模块），如日期、天气、今日一言

另外还有一种 ``<!-- __X__ -->`` 注释形态：模板里用它标记"这一整块由运行时插入"，
例如节日横幅、天气卡片、公告。没有内容时连注释一起消失，不会留下多余空行。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import ai
from .config import Config
from .i18n import DEFAULT_LANG
from .lunar import build_countdown_xaml, build_festival_banner, get_festival
from .lunartime import build_lunar_xaml, get_lunar_text
from .personalize import BeijingDate, format_quote, get_beijing_date, pick_greeting_sub
from .saying import get_saying
from .store import Store
from .weather import WeatherService
from .xaml import build_multi_banner, build_single_banner, escape_attr, indent_block


@dataclass
class HomeData:
    """一次主页请求算出来的全部内容（便于单独测试与调试）。"""
    ip: str
    date: BeijingDate
    greeting_sub: str = ""
    quote: str = ""
    quote_source: str = ""
    festival_banner: str = ""
    countdown: str = ""
    banner: str = ""
    lunar: str = ""            # 渲染好的 XAML 片段
    lunar_text: str = ""       # 纯文本，给日志用
    festival_name: str = ""
    weather_body: str = ""
    weather_source: str = ""
    ai_body: str = ""
    ai_state: str = ""

    def summary(self) -> str:
        """给日志用的一句话摘要：这次请求到底算出了什么。"""
        bits = ["问候=" + self.date.greeting, "一言=" + (self.quote_source or "-")]
        bits.append("天气=" + (self.weather_source or "失败"))
        if self.lunar_text:
            bits.append("农历=" + self.lunar_text)
        if self.festival_name:
            bits.append("节日=" + self.festival_name)
        if self.ai_state:
            bits.append("AI=" + self.ai_state)
        bits.append("公告=" + ("有" if self.banner else "无"))
        return " ".join(bits)


def build_home_data(config: Config, store: Store, weather: WeatherService,
                    ip: str, origin: str, lang: str = DEFAULT_LANG) -> HomeData:
    """算出本次请求的全部内容（文案按 ``lang`` 出）。"""
    date = get_beijing_date()

    extra_festivals = store.get_json("custom_festivals", []) or []
    custom_countdown = store.get_json("custom_countdown", None)

    saying = get_saying(config, store, date.today)
    weather_result = weather.get(ip, lang)
    festival = get_festival(date.today, extra_festivals, lang)
    lunar_text = get_lunar_text(config, date.today, lang)

    return HomeData(
        ip=ip,
        date=date,
        greeting_sub=pick_greeting_sub(ip, date.date_str, date.period, lang),
        # 自定义文案支持 {date}/{weekday}/{year} 占位，接口来的文案不含占位符，替换是空操作。
        # 每日一言本身不翻译（用户明确要求），所以里面的日期占位保持中文写法。
        quote=format_quote(saying.display, date),
        quote_source=saying.source_name,
        festival_banner=build_festival_banner(festival, lang),
        festival_name=str((festival or {}).get("name") or ""),
        countdown=build_countdown_xaml(date.today, custom_countdown, extra_festivals, lang),
        banner=(build_multi_banner(store.get("banners", None))
                or build_single_banner(store.get_json("homepage_banner", None))),
        lunar=build_lunar_xaml(lunar_text),
        lunar_text=lunar_text,
        weather_body=weather_result["body"],
        weather_source=str(weather_result.get("source") or ""),
        ai_state=(ai.get_job(ip) or {}).get("state", "") if config.enable_ai else "",
    )


def render_homepage(template: str, data: HomeData, config: Config, origin: str,
                    lang: str = DEFAULT_LANG) -> str:
    """把主页模板渲染成最终 XAML。

    ``lang`` 是这个访客选的语言。模板里那几处静态文字（卡片标题、"月/日"、
    "星期"、"每日一言"）都写成了 ``__T_xxx__`` 令牌，在这儿按语言填——
    以前它们是构建期烘死在 Custom.xaml 里的，那样换不了语言。
    """
    from . import version
    from .i18n import DEFAULT_LANG as _D, t
    from .sites import build_site_items
    from .xaml import build_action_buttons

    date = data.date
    base = config.resolved_base_url(origin)
    short = version.current()

    values = {
        "BASE_URL": base,
        "DATE_MONTH": t("date.month." + str(date.today.month), lang),
        "DATE_DAY": date.day_text,
        # 星期整串在这儿拼：简中是"星期一"，英文就是"Monday"，没有前缀
        "T_WEEKDAY": escape_attr(t("date.weekday_prefix", lang)
                                 + t("date.weekday." + str((date.today.weekday() + 1) % 7), lang)),
        # 问候语带上后面那个逗号：中文「下午好，」、英文「Good afternoon, 」
        "GREETING": (t("greeting." + (date.period or "morning"), lang)
                     + t("home.greeting_open", lang)),
        "GREETING_SUB": escape_attr(data.greeting_sub),
        "QUOTE": escape_attr(data.quote),
        "FESTIVAL_BANNER": data.festival_banner,
        "COUNTDOWN_BODY": data.countdown,
        "WEATHER_BODY": data.weather_body,
        "BANNER": data.banner,
        "LUNAR": data.lunar,

        # 模板里原本烘死的四处静态文字
        "T_WELCOME": escape_attr(t("home.welcome", lang)),
        # 问候语句末那个标点：中文「！」、英文「!」。
        # 它紧跟在 {user} 后面，而 {user} 由 PCL 自己填，所以这里绝不能走
        # escape_attr（会把花括号转义掉，PCL 就认不出来了）。
        "T_GREETING_CLOSE": t("home.greeting_close", lang),
        "T_MONTH_SUFFIX": escape_attr(t("date.month_suffix", lang)),
        "T_DAY_SUFFIX": escape_attr(t("date.day_suffix", lang)),
        "T_QUOTE_LABEL": escape_attr(t("home.quote_label", lang)),
        "T_SITES_TITLE": escape_attr(t("home.sites_title", lang)),

        # 这三块以前是构建期写死的，现在按语言现算
        "ACTION_BUTTONS": build_action_buttons(config, lang),
        "SITE_ITEMS": indent_block(build_site_items(config, lang, base), 8),
        "FOOTER_CARD": build_footer_card(config, lang, base, short),

        # 欢迎卡片那排按钮里「AI 分析」入口的绝对地址（构建期不知道对外域名）
        "AI_ENTRY": base + "/ai_page.json",
        # 「在线工具」卡片里计算器的入口
        "CALC_ENTRY": base + "/calc.json",
    }
    return replace_placeholders(template, values)


def build_footer_card(config: Config, lang: str, base: str, version: str) -> str:
    """底标：作者、仓库、当前版本，外加个性设置的入口。

    仓库按钮走的是「打开网页」（开浏览器），不是「打开帮助」（翻 PCL 内页），
    所以不能直接用 ``xaml.help_button``。
    """
    from .i18n import t
    from .xaml import ICON_HOME, escape_url_attr, help_button

    if not version:
        version = t("home.footer_version_unknown", lang)

    def line(text: str) -> str:
        return ('<TextBlock Text="' + escape_attr(text) + '" FontSize="12" '
                'Foreground="{DynamicResource ColorBrush2}" Margin="0,0,0,6" />')

    def link_button(text: str, url: str, margin: str = "") -> str:
        """开浏览器的按钮——EventType 必须是「打开网页」。

        教程文件里写得很清楚：「打开网页」是拿 EventData 当网址开浏览器，
        「打开帮助」是让 PCL 去拉那个 .json/.xaml 翻成内页。两者不能混——
        个性设置那个按钮要是写成「打开网页」，就会傻乎乎地把 .json 丢给浏览器。
        """
        return ('<local:MyIconTextButton' + (' Margin="' + margin + '"' if margin else "")
                + ' Height="36" Text="' + escape_attr(text) + '" LogoScale="0.8" Logo="'
                + ICON_HOME + '" ColorType="Highlight" EventType="打开网页" EventData="'
                + escape_url_attr(url) + '" />')

    return (
        '<local:MyCard Title="' + escape_attr(t("home.footer_title", lang))
        + '" Margin="0,0,0,16" CanSwap="False">'
        '<StackPanel Margin="25,40,23,16">'
        + line(t("home.footer_author", lang))
        + line(t("home.footer_repo", lang))
        + line(t("home.footer_version", lang, version=version))
        + '<StackPanel Orientation="Horizontal" Margin="0,10,0,0">'
        + link_button(t("home.footer_open_repo", lang),
                      "https://github.com/TopGrilledFish/PCLEndPage", "0,0,8,0")
        # 个性设置走「打开帮助」，在 PCL 里翻页，不是开浏览器
        + help_button(t("home.footer_settings", lang), ICON_HOME,
                      base + "/settings_page.json", 36, color="")
        + "</StackPanel></StackPanel></local:MyCard>")


# ============ 替换 ============

_COMMENT_BLOCK_RE = re.compile(r"<!--\s*__([A-Z][A-Z0-9_]*)__\s*-->")


def replace_placeholders(template: str, values: dict) -> str:
    """替换 ``__TOKEN__`` 与 ``<!-- __TOKEN__ -->``。

    ``__TOKEN__`` 形式的替换值原样插入（调用方决定是否已转义）；
    注释形式用于整块插入，没有内容时连注释一起消失。
    """
    text = _COMMENT_BLOCK_RE.sub(lambda m: values.get(m.group(1), ""), template)

    def repl(match):
        key = match.group(1)
        if key not in values:
            return match.group(0)      # 未知占位符原样保留，便于体检脚本发现
        return str(values[key])

    return re.sub(r"__([A-Z][A-Z0-9_]*)__", repl, text)


def find_leftover_placeholders(text: str) -> list[str]:
    """找出仍未替换的占位符（体检脚本用）。"""
    return sorted(set(re.findall(r"__([A-Z][A-Z0-9_]*)__", text)))
