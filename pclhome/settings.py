# -*- coding: utf-8 -*-
"""个性设置页：换语言、绑定玩家名、导出导入个性码。

和计算器页一样是 **PCL 内部页**（走「打开帮助」翻开，不开浏览器）：
``/settings_page.json`` 是 PCL 先读的标题/描述，``/settings_page.xaml`` 是正文。

**为什么没有"保存"按钮**：PCL 的 ``EventData`` 只能绑一个控件的 ``Text``
（``MultiBinding`` 会整页崩，见 calc.py 里的记录），所以每个要输入的项目都得配
自己那一个按钮——点一下、改一项、整页重画。语言那三个是纯按钮，不需要输入框。

**为什么按 IP 找记录**：主页里的 ``{user}`` 是 PCL 在客户端替换的，服务端看不到
玩家名，所以渲染主页时只能按 IP 匹配。玩家名是用户在这儿自己填的，用来给记录
命名、以及换 IP 之后靠它找回来（见 profiles.py）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote

from . import palette
from . import profiles
from .i18n import DEFAULT_LANG, LANG_NAMES, LANGS, normalize_lang, t
from .log import warn
from .xaml import (ICON_BACK, ICON_HOME, ICON_KEY, attr, escape_attr, escape_url_attr,
                   grid2, heading, help_button, input_row, nav_row, note)

_META = {"/settings_page.json": ("settings.title", "settings.desc")}
_DO = {"/settings_page.xaml"}

SETTINGS_PATHS = set(_META) | _DO


# ============ 请求 ============

def parse_query(query: str) -> dict:
    """解析查询串。PCL 把输入框原文原样拼进 URL、不做编码，所以容错要厚一点
    （跟 ai.py / calc.py 里的同款处理）。"""
    query = query or ""
    try:
        return {key: values[0] for key, values in parse_qs(query, keep_blank_values=True).items()}
    except Exception:
        out = {}
        for chunk in query.split("&"):
            if "=" in chunk:
                key, value = chunk.split("=", 1)
                out[unquote(key)] = unquote(value)
        return out


def _bind(element: str, base: str, param: str) -> str:
    """把某个输入框的原文拼到 ``?<param>=`` 后面。

    跟 ``xaml.bind_to`` 一样，只是那个写死了 ``?q=``；这里两个框要用不同的参数名。
    """
    return ("{Binding Path=Text,ElementName=" + element + ",StringFormat='{}"
            + base + "/settings_page.json?" + param + "={0}'}")


def _expiry_text(record: dict, lang: str) -> str:
    from .profiles import KEEP_DAYS
    stamp = float(record.get("updated") or 0)
    if not stamp:
        return ""
    until = datetime.fromtimestamp(stamp) + timedelta(days=KEEP_DAYS)
    return t("settings.state_seen", lang, date=until.strftime("%Y-%m-%d"), days=KEEP_DAYS)


# ============ 页面 ============

def _state_block(record, ip: str, lang: str) -> str:
    rows = [t("settings.state_ip", lang, ip=ip or "?")]
    if record and record.get("name"):
        rows.append(t("settings.state_name", lang, name=record["name"]))
    else:
        rows.append(t("settings.state_name_unset", lang))
    rows.append(t("settings.state_lang", lang, language=LANG_NAMES.get(lang, lang)))
    if record:
        extra = _expiry_text(record, lang)
        if extra:
            rows.append(extra)
    else:
        rows.append(t("settings.state_none", lang))
    body = "".join('<TextBlock Text="' + attr(text) + '" FontSize="12" TextWrapping="Wrap" '
                   'Margin="0,0,0,5" Foreground="{DynamicResource ColorBrush2}" />'
                   for text in rows)
    return ('<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14" '
            'Margin="0,10,0,0"><StackPanel>' + body + "</StackPanel></Border>")


def _lang_buttons(base: str, lang: str) -> str:
    cells = []
    for code in LANGS:
        label = t("lang." + code, lang)
        cells.append(help_button(label, ICON_KEY, base + "/settings_page.json?lang=" + code,
                                 38, margin="0,0,6,6", color=""))
    return '<StackPanel Orientation="Horizontal" Margin="0,8,0,0">' + "".join(cells) + "</StackPanel>"


#: 配色可选的档位。数字就是 PCL 主题色的浓度（见 palette.py），
#: 色块 5~8 由深到浅，文字 1~3 由深到浅。
_COLOR_CHOICES = {"panel": palette.PANEL_CHOICES, "text": palette.TEXT_CHOICES}
_COLOR_DEFAULT = {"panel": palette.DEFAULTS["panel"], "text": palette.DEFAULTS["text"]}


def _swatch_row(base: str, lang: str, role: str, current: int) -> str:
    """一行配色按钮：每个档位一个，当前那个用 MyHint 标出来。"""
    cells = []
    for level in _COLOR_CHOICES[role]:
        mark = "✓ " if level == current else ""
        cells.append(help_button(
            mark + t("settings.color_level", lang, n=level), ICON_KEY,
            base + "/settings_page.json?" + role + "=" + str(level),
            34, margin="0,0,6,0", color="Highlight" if level == current else ""))
    return ('<StackPanel Orientation="Horizontal" Margin="0,8,0,0">'
            + "".join(cells) + "</StackPanel>")


def _color_buttons(base: str, lang: str, record) -> str:
    """配色区：上面一行选色块浓度，下面一行选文字浓度，再给个效果预览。"""
    chosen = palette.brushes(record)
    rows = []
    for role, key in (("panel", "settings.color_panel"), ("text", "settings.color_text")):
        rows.append('<TextBlock Text="' + attr(t(key, lang)) + '" FontSize="12" Margin="0,10,0,0" '
                    'Foreground="{DynamicResource ColorBrush2}" />')
        rows.append(_swatch_row(base, lang, role, chosen[role]))
    # 预览：用的就是这四个角色色，所见即所得
    rows.append('<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" '
                'Padding="14,12" Margin="0,14,0,0"><StackPanel>'
                '<TextBlock Text="' + attr(t("settings.color_preview", lang)) + '" FontSize="13" '
                'FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" />'
                '<TextBlock Text="' + attr(t("settings.color_preview_dim", lang)) + '" FontSize="11" '
                'Margin="0,4,0,0" Foreground="{DynamicResource ColorBrush3}" />'
                '<TextBlock Text="' + attr(t("settings.color_preview_note", lang)) + '" FontSize="11" '
                'Margin="0,4,0,0" TextWrapping="Wrap" '
                'Foreground="{DynamicResource ColorBrush2}" />'
                "</StackPanel></Border>")
    return "".join(rows)


def build_settings_page(base_url: str, ip: str, query: str) -> str:
    """设置页正文。``query`` 里带着这次要做的动作。"""
    base = escape_url_attr(base_url)
    params = parse_query(query)

    # 先按当前语言渲染一次；动作可能把语言改掉，改完要用新语言重画
    lang = profiles.lang_for(ip)
    result = ""

    if params.get("lang"):
        wanted = normalize_lang(params["lang"])
        profiles.PROFILES.bind(ip, lang=wanted)
        lang = wanted
        result = t("settings.lang_changed", lang, language=LANG_NAMES.get(lang, lang))

    if params.get("name") is not None:
        name = (params.get("name") or "").strip()
        if not name:
            result = t("settings.name_empty", lang)
        else:
            profiles.PROFILES.bind(ip, name=name)
            result = t("settings.name_saved", lang, name=name)

    if params.get("code"):
        data = profiles.decode_code(params["code"])
        if data is None:
            result = t("settings.code_bad", lang)
        else:
            if data.get("lang"):
                lang = data["lang"]
            profiles.PROFILES.bind(ip, name=data.get("name", ""), lang=data.get("lang", ""),
                                   panel=data.get("panel"), text=data.get("text"))
            result = t("settings.code_ok", lang, language=LANG_NAMES.get(lang, lang))

    # 配色两档：色块用哪一档、文字用哪一档（见 palette.py 顶部那段说明）
    for role, key in (("panel", "settings.color_panel"), ("text", "settings.color_text")):
        if params.get(role) is None:
            continue
        wanted = palette.normalize(params[role], _COLOR_CHOICES[role], _COLOR_DEFAULT[role])
        profiles.PROFILES.bind(ip, **{role: wanted})
        result = t("settings.color_saved", lang, name=t(key, lang),
                   level=t("settings.color_level", lang, n=wanted))

    record = profiles.PROFILES.by_ip(ip)
    body = [heading(t("settings.state_title", lang), "0,20,0,0"),
            _state_block(record, ip, lang)]
    if result:
        body.append('<local:MyHint Theme="Blue" Margin="0,12,0,0" Text="' + attr(result) + '" />')

    body.append(heading(t("settings.color_title", lang), "0,20,0,0"))
    body.append(note(t("settings.color_hint", lang), "0,6,0,0"))
    body.append(_color_buttons(base, lang, record))

    body.append(heading(t("settings.lang_title", lang), "0,20,0,0"))
    body.append(note(t("settings.lang_hint", lang), "0,6,0,0"))
    body.append(_lang_buttons(base, lang))

    body.append(heading(t("settings.name_title", lang), "0,20,0,0"))
    body.append(note(t("settings.name_hint", lang), "0,6,0,0"))
    body.append(input_row("setname", t("settings.name_box", lang), 38, "0,8,0,0"))
    body.append(help_button(t("settings.name_save", lang), ICON_KEY,
                            _bind("setname", base, "name"), 38, margin="0,8,0,0"))

    body.append(heading(t("settings.code_title", lang), "0,20,0,0"))
    body.append(note(t("settings.code_hint", lang), "0,6,0,0"))
    if params.get("export") is not None and record is not None:
        code = profiles.encode_code(record)
        body.append(note(t("settings.code_value", lang), "0,10,0,0"))
        body.append('<Border Background="{DynamicResource ColorBrush7}" CornerRadius="6" '
                    'Padding="12,10" Margin="0,4,0,0"><TextBlock Text="' + attr(code)
                    + '" FontSize="12" TextWrapping="Wrap" '
                    'Foreground="{DynamicResource ColorBrush1}" /></Border>')
    body.append(grid2(
        help_button(t("settings.code_export", lang), ICON_KEY,
                    base + "/settings_page.json?export=1", 38, column=0, margin="0,0,5,0"),
        help_button(t("settings.code_import", lang), ICON_KEY,
                    _bind("setcode", base, "code"), 38, column=1, margin="5,0,0,0"),
        "0,8,0,0"))
    body.append(input_row("setcode", t("settings.code_box", lang), 38, "0,8,0,0"))

    return (
        '<local:MyCard Title="' + escape_attr(t("settings.title", lang))
        + '" CanSwap="False"><StackPanel Margin="25,40,23,20">'
        + '<TextBlock Text="' + escape_attr(t("settings.title", lang))
        + '" FontSize="24" FontWeight="Bold" Foreground="#FF000000" '
        'HorizontalAlignment="Center" />'
        + note(t("settings.desc", lang), "0,10,0,0")
        + "".join(body)
        + nav_row(base, lang=lang)
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
    """处理 ``/settings_page.*``；不是我们的路径就返回 None。"""
    if path in _META:
        # .json 是 PCL 先拉的那一份（页面标题/描述），带 ?lang= 时按新语言出标题，
        # 不然刚切完语言，页头还是旧语言的，正文却已经是新的了。
        params = parse_query(query)
        lang = normalize_lang(params["lang"]) if params.get("lang") else profiles.lang_for(ip)
        title_key, desc_key = _META[path]
        body = json.dumps({"Title": t(title_key, lang), "Description": t(desc_key, lang)},
                          ensure_ascii=False)
        return _json_response(body)

    if path not in _DO:
        return None

    base = service.config.resolved_base_url(origin)
    try:
        page = build_settings_page(base, ip, query)
    except Exception as exc:                       # 别让设置页把整站带崩
        warn("[Settings] 页面构建失败：" + repr(exc))
        page = build_settings_page(base, ip, "")
    return _xaml_response(palette.apply(page, profiles.PROFILES.palette_for(ip)))
