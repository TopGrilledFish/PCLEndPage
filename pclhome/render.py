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
from .lunar import build_countdown_xaml, build_festival_banner, get_festival
from .lunartime import build_lunar_xaml, get_lunar_text
from .personalize import BeijingDate, format_quote, get_beijing_date, pick_greeting_sub
from .saying import get_saying
from .store import Store
from .weather import WeatherService
from .xaml import build_multi_banner, build_single_banner, escape_attr


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
                    ip: str, origin: str) -> HomeData:
    """算出本次请求的全部内容。"""
    date = get_beijing_date()

    extra_festivals = store.get_json("custom_festivals", []) or []
    custom_countdown = store.get_json("custom_countdown", None)

    saying = get_saying(config, store, date.today)
    weather_result = weather.get(ip)
    festival = get_festival(date.today, extra_festivals)
    lunar_text = get_lunar_text(config, date.today)

    return HomeData(
        ip=ip,
        date=date,
        greeting_sub=pick_greeting_sub(ip, date.date_str, date.period),
        # 自定义文案支持 {date}/{weekday}/{year} 占位，接口来的文案不含占位符，替换是空操作
        quote=format_quote(saying.display, date),
        quote_source=saying.source_name,
        festival_banner=build_festival_banner(festival),
        festival_name=str((festival or {}).get("name") or ""),
        countdown=build_countdown_xaml(date.today, custom_countdown, extra_festivals),
        banner=(build_multi_banner(store.get("banners", None))
                or build_single_banner(store.get_json("homepage_banner", None))),
        lunar=build_lunar_xaml(lunar_text),
        lunar_text=lunar_text,
        weather_body=weather_result["body"],
        weather_source=str(weather_result.get("source") or ""),
        ai_state=(ai.get_job(ip) or {}).get("state", "") if config.enable_ai else "",
    )


def render_homepage(template: str, data: HomeData, config: Config, origin: str) -> str:
    """把主页模板渲染成最终 XAML。"""
    date = data.date

    values = {
        "BASE_URL": config.resolved_base_url(origin),
        "DATE_MONTH": date.month_text,
        "DATE_DAY": date.day_text,
        "DATE_WEEKDAY": date.weekday,
        "GREETING": date.greeting,
        "GREETING_SUB": escape_attr(data.greeting_sub),
        "QUOTE": escape_attr(data.quote),
        "FESTIVAL_BANNER": data.festival_banner,
        "COUNTDOWN_BODY": data.countdown,
        "WEATHER_BODY": data.weather_body,
        "BANNER": data.banner,
        "LUNAR": data.lunar,
        # 欢迎卡片那排按钮里「AI 分析」入口的绝对地址（构建期不知道对外域名）
        "AI_ENTRY": config.resolved_base_url(origin) + "/ai_page.json",
        # 「在线工具」卡片里计算器的入口
        "CALC_ENTRY": config.resolved_base_url(origin) + "/calc.json",
    }
    return replace_placeholders(template, values)


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
