# -*- coding: utf-8 -*-
"""界面语言选择页（PCL 内页）。

个性设置页里那个「语言」入口点进来就是这里。

只有简体中文 / 繁体中文 / 英文三种：``pclhome/i18n/<code>.json`` 一个文件一种
语言，都是完整的七百多个键，切过去整页（含计算器、AI 页、访问页）都跟着变。
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote

from . import palette, profiles
from .i18n import is_supported, lang_name, lang_name_en, lang_name_zh, t
from .log import warn
from .xaml import (ICON_LANG, attr, escape_attr, escape_url_attr, heading,
                   help_button, nav_row, note)

_META = {"/language_page.json": ("language.title", "language.desc")}
_DO = {"/language_page.xaml"}
LANGUAGE_PATHS = set(_META) | _DO

#: 清单：(语言代码, 本地写法, 中文名, 英文名)。
#: 显示名以语言包里的 ``_name`` / ``_name_zh`` / ``_name_en`` 为准（``_display``），
#: 这里第 2~4 列是兜底。英文页面上只写英文名——英文页冒出汉字会被体检拦下。
LANGUAGES = [
    ("zh-hans", "简体中文", "简体中文", "Chinese (Simplified)"),
    ("zh-hant", "繁體中文", "繁体中文", "Chinese (Traditional)"),
    ("en", "English", "英语", "English"),
]

#: 语言代码 -> (本地写法, 中文名, 英文名)
BY_CODE = {item[0]: item[1:] for item in LANGUAGES}


def _is_en(lang: str) -> bool:
    return str(lang or "").lower().startswith("en")


def _entry(code: str) -> tuple:
    """(代码, 本地写法, 中文名, 英文名)：以语言包里的 ``_name*`` 为准。"""
    fallback = BY_CODE.get(code, ("", "", ""))
    native = lang_name(code) or fallback[0] or code
    zh = lang_name_zh(code) or fallback[1] or native
    en = lang_name_en(code) or fallback[2] or native
    return (code, native, zh, en)


def _display(code: str, lang: str) -> str:
    """一种语言在"当前界面语言"下怎么显示。

    中文/繁体页用中文名，英文页用英文名——英文页里出现汉字会被
    ``doctor`` 的"英文页不许有中文"拦下来，而且老外也确实看不懂。
    """
    _code, native, zh, en = _entry(code)
    if _is_en(lang):
        return en if en != native else native
    return zh if zh != native else native


def parse_query(query: str) -> dict:
    """请求串解析成字典（只取第一项，跟 settings.parse_query 同口径）。"""
    try:
        return {key: values[0] for key, values in
                parse_qs(query or "", keep_blank_values=True).items()}
    except Exception:
        out = {}
        for chunk in (query or "").split("&"):
            if "=" in chunk:
                key, value = chunk.split("=", 1)
                out[unquote(key)] = unquote(value)
        return out


def _current_block(lang: str, result: str) -> str:
    rows = ['<TextBlock Text="'
            + attr(t("language.current", lang, language=_display(lang, lang)))
            + '" FontSize="12" TextWrapping="Wrap" Margin="0,0,0,5" '
            'Foreground="{DynamicResource ColorBrush2}" />']
    if result:
        rows.append('<TextBlock Text="' + attr(result) + '" FontSize="12" TextWrapping="Wrap" '
                    'Foreground="{DynamicResource ColorBrush1}" />')
    return ('<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14" '
            'Margin="0,10,0,0"><StackPanel>' + "".join(rows) + "</StackPanel></Border>")


def _entry_button(item, lang: str, base: str, column: int, current: bool) -> str:
    """一个语言按钮，当前那个打勾并点亮。"""
    code = item[0]
    label = ("✓ " if current else "") + _display(code, lang)
    return help_button(label, ICON_LANG, base + "/language_page.json?lang=" + code, 38,
                       column=column, margin="0,0,6,0",
                       color="Highlight" if current else "")


def _entry_grid(items: list, lang: str, base: str, current: str) -> str:
    """三种语言并排一行，竖排太占地方。"""
    if not items:
        return ""
    return ('<Grid Margin="0,4,0,0"><Grid.ColumnDefinitions>'
            + '<ColumnDefinition Width="1*" />' * len(items)
            + "</Grid.ColumnDefinitions>"
            + "".join(_entry_button(item, lang, base, index, item[0] == current)
                      for index, item in enumerate(items))
            + "</Grid>")


def build_language_page(base_url: str, ip: str = "", query: str = "",
                        lang: str = "") -> str:
    """语言选择页正文。``lang`` 只给体检用：传了就不碰真实记录。"""
    base = escape_url_attr(base_url)
    params = parse_query(query)
    current = lang or profiles.lang_for(ip)
    result = ""

    wanted = (params.get("lang") or "").strip()
    if wanted:
        if is_supported(wanted):
            if not lang:
                profiles.PROFILES.bind(ip, lang=wanted)
            current = wanted
            result = t("language.switched", current,
                       language=_display(current, current))
        else:
            result = t("language.unsupported_click", current, language=wanted)

    supported = [(item[0],) for item in LANGUAGES if is_supported(item[0])]

    body = [heading(t("language.current_title", current), "0,20,0,0"),
            _current_block(current, result),
            heading(t("language.supported_title", current, n=len(supported)), "0,20,0,0"),
            _entry_grid(supported, current, base, current)]

    return (
        '<local:MyCard Title="' + escape_attr(t("language.title", current))
        + '" CanSwap="False"><StackPanel Margin="25,40,23,20">'
        + '<TextBlock Text="' + escape_attr(t("language.title", current))
        + '" FontSize="24" FontWeight="Bold" Foreground="#FF000000" '
        'HorizontalAlignment="Center" />'
        + note(t("language.desc", current), "0,10,0,0")
        + "".join(body)
        + nav_row(lang=current)
        + "</StackPanel></local:MyCard>")


# ============ 路由 ============

def _json_response(body: str):
    from .server import Response
    return Response.text(body, content_type="application/json; charset=utf-8",
                         headers={"Cache-Control": "no-store"})


def _xaml_response(body: str):
    from .server import Response
    return Response.xaml(body)


def handle(service, path: str, query: str, ip: str, origin: str = ""):
    """处理 ``/language_page.json`` 与 ``/language_page.xaml``；别的路径返回 None。"""
    if path in _META:
        params = parse_query(query)
        wanted = (params.get("lang") or "").strip()
        lang = wanted if is_supported(wanted) else profiles.lang_for(ip)
        title_key, desc_key = _META[path]
        return _json_response(json.dumps(
            {"Title": t(title_key, lang), "Description": t(desc_key, lang)},
            ensure_ascii=False))

    if path not in _DO:
        return None

    base = service.config.resolved_base_url(origin)
    try:
        page = build_language_page(base, ip, query)
    except Exception as exc:                       # 别让语言页把整站带崩
        warn("[Language] 页面构建失败：" + repr(exc))
        page = build_language_page(base, ip, "")
    return _xaml_response(palette.apply(page, profiles.PROFILES.palette_for(ip)))
