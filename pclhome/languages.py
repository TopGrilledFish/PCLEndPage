# -*- coding: utf-8 -*-
"""界面语言选择页（PCL 内页）。

个性设置页里那个「语言」入口点进来就是这里：一张语言清单加一个搜索框。

**真能切的只有三种**（简中 / 繁中 / 英文）——只有这三种手上有完整词条
（见 ``i18n``）。清单里其它语言列出来备查：一套词条 744 个键，没有翻译
就是没有，做成点了没反应的按钮更骗人，所以未支持的那些只显示名字、点不动，
页面上也写清楚了。
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote

from . import palette, profiles
from .i18n import LANGS, LANG_NAMES, t
from .log import warn
from .xaml import (ICON_BACK, ICON_KEY, attr, bind_to, escape_attr,
                   escape_url_attr, heading, help_button, input_row, nav_row, note)

_META = {"/language_page.json": ("language.title", "language.desc")}
_DO = {"/language_page.xaml"}
LANGUAGE_PATHS = set(_META) | _DO

#: 清单：(语言代码, 本地写法, 中文名, 英文名)。前三个是真能切的，其余按常见度排。
#: 别改成按字母排——中文用户找「日本語」比找 "ja" 快。
#: 英文页面上只写英文名（``_display`` 管这个）：英文页里冒出汉字会被体检拦下。
LANGUAGES = [
    ("zh-hans", "简体中文", "简体中文", "Chinese (Simplified)"),
    ("zh-hant", "繁體中文", "繁体中文", "Chinese (Traditional)"),
    ("en", "English", "英语", "English"),
    ("ja", "日本語", "日语", "Japanese"),
    ("ko", "한국어", "韩语", "Korean"),
    ("ru", "Русский", "俄语", "Russian"),
    ("de", "Deutsch", "德语", "German"),
    ("fr", "Français", "法语", "French"),
    ("es", "Español", "西班牙语", "Spanish"),
    ("pt", "Português", "葡萄牙语", "Portuguese"),
    ("it", "Italiano", "意大利语", "Italian"),
    ("nl", "Nederlands", "荷兰语", "Dutch"),
    ("pl", "Polski", "波兰语", "Polish"),
    ("sv", "Svenska", "瑞典语", "Swedish"),
    ("no", "Norsk", "挪威语", "Norwegian"),
    ("da", "Dansk", "丹麦语", "Danish"),
    ("fi", "Suomi", "芬兰语", "Finnish"),
    ("is", "Íslenska", "冰岛语", "Icelandic"),
    ("cs", "Čeština", "捷克语", "Czech"),
    ("sk", "Slovenčina", "斯洛伐克语", "Slovak"),
    ("hu", "Magyar", "匈牙利语", "Hungarian"),
    ("ro", "Română", "罗马尼亚语", "Romanian"),
    ("bg", "Български", "保加利亚语", "Bulgarian"),
    ("el", "Ελληνικά", "希腊语", "Greek"),
    ("tr", "Türkçe", "土耳其语", "Turkish"),
    ("uk", "Українська", "乌克兰语", "Ukrainian"),
    ("be", "Беларуская", "白俄罗斯语", "Belarusian"),
    ("sr", "Српски", "塞尔维亚语", "Serbian"),
    ("hr", "Hrvatski", "克罗地亚语", "Croatian"),
    ("sl", "Slovenščina", "斯洛文尼亚语", "Slovenian"),
    ("bs", "Bosanski", "波斯尼亚语", "Bosnian"),
    ("mk", "Македонски", "马其顿语", "Macedonian"),
    ("sq", "Shqip", "阿尔巴尼亚语", "Albanian"),
    ("lt", "Lietuvių", "立陶宛语", "Lithuanian"),
    ("lv", "Latviešu", "拉脱维亚语", "Latvian"),
    ("et", "Eesti", "爱沙尼亚语", "Estonian"),
    ("ca", "Català", "加泰罗尼亚语", "Catalan"),
    ("gl", "Galego", "加利西亚语", "Galician"),
    ("eu", "Euskara", "巴斯克语", "Basque"),
    ("cy", "Cymraeg", "威尔士语", "Welsh"),
    ("ga", "Gaeilge", "爱尔兰语", "Irish"),
    ("mt", "Malti", "马耳他语", "Maltese"),
    ("ar", "العربية", "阿拉伯语", "Arabic"),
    ("he", "עברית", "希伯来语", "Hebrew"),
    ("fa", "فارسی", "波斯语", "Persian"),
    ("ur", "اردو", "乌尔都语", "Urdu"),
    ("hi", "हिन्दी", "印地语", "Hindi"),
    ("bn", "বাংলা", "孟加拉语", "Bengali"),
    ("ta", "தமிழ்", "泰米尔语", "Tamil"),
    ("te", "తెలుగు", "泰卢固语", "Telugu"),
    ("gu", "ગુજરાતી", "古吉拉特语", "Gujarati"),
    ("kn", "ಕನ್ನಡ", "卡纳达语", "Kannada"),
    ("ml", "മലയാളം", "马拉雅拉姆语", "Malayalam"),
    ("mr", "मराठी", "马拉地语", "Marathi"),
    ("ne", "नेपाली", "尼泊尔语", "Nepali"),
    ("si", "සිංහල", "僧伽罗语", "Sinhala"),
    ("th", "ไทย", "泰语", "Thai"),
    ("vi", "Tiếng Việt", "越南语", "Vietnamese"),
    ("id", "Bahasa Indonesia", "印度尼西亚语", "Indonesian"),
    ("ms", "Bahasa Melayu", "马来语", "Malay"),
    ("fil", "Filipino", "菲律宾语", "Filipino"),
    ("km", "ខ្មែរ", "高棉语", "Khmer"),
    ("lo", "ລາວ", "老挝语", "Lao"),
    ("my", "မြန်မာ", "缅甸语", "Burmese"),
    ("mn", "Монгол", "蒙古语", "Mongolian"),
    ("kk", "Қазақша", "哈萨克语", "Kazakh"),
    ("uz", "Oʻzbekcha", "乌兹别克语", "Uzbek"),
    ("az", "Azərbaycan", "阿塞拜疆语", "Azerbaijani"),
    ("ka", "ქართული", "格鲁吉亚语", "Georgian"),
    ("hy", "Հայերեն", "亚美尼亚语", "Armenian"),
    ("sw", "Kiswahili", "斯瓦希里语", "Swahili"),
    ("af", "Afrikaans", "南非荷兰语", "Afrikaans"),
    ("am", "አማርኛ", "阿姆哈拉语", "Amharic"),
    ("eo", "Esperanto", "世界语", "Esperanto"),
    ("la", "Latina", "拉丁语", "Latin"),
]

#: 语言代码 -> (本地写法, 中文名, 英文名)
BY_CODE = {item[0]: item[1:] for item in LANGUAGES}


def _is_en(lang: str) -> bool:
    return str(lang or "").lower().startswith("en")


def _display(code: str, lang: str) -> str:
    """一种语言在"当前界面语言"下怎么显示。

    中文/繁体页用中文名，英文页用英文名——英文页里出现汉字会被
    ``doctor`` 的"英文页不许有中文"拦下来，而且老外也确实看不懂。
    """
    entry = BY_CODE.get(code)
    if not entry:
        return code
    native, zh, en = entry
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


def search(keyword: str) -> list:
    """按代码 / 本地写法 / 中文名 / 英文名过滤；空关键词返回全部。"""
    text = (keyword or "").strip().lower()
    if not text:
        return list(LANGUAGES)
    return [item for item in LANGUAGES
            if text in item[0].lower() or text in item[1].lower()
            or text in item[2].lower() or text in item[3].lower()]


def _supported(code: str) -> bool:
    return code in LANGS


def _current_block(lang: str, result: str) -> str:
    rows = ['<TextBlock Text="'
            + attr(t("language.current", lang, language=LANG_NAMES.get(lang, lang)))
            + '" FontSize="12" TextWrapping="Wrap" Margin="0,0,0,5" '
            'Foreground="{DynamicResource ColorBrush2}" />']
    if result:
        rows.append('<TextBlock Text="' + attr(result) + '" FontSize="12" TextWrapping="Wrap" '
                    'Foreground="{DynamicResource ColorBrush1}" />')
    return ('<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14" '
            'Margin="0,10,0,0"><StackPanel>' + "".join(rows) + "</StackPanel></Border>")


def _entry_row(item, lang: str, base: str, current: bool) -> str:
    """一个语言按钮（只用于已支持的），当前那个打勾并点亮。"""
    code = item[0]
    label = ("✓ " if current else "") + _display(code, lang)
    return help_button(label, ICON_KEY, base + "/language_page.json?lang=" + code, 38,
                       margin="0,0,6,6", color="Highlight" if current else "")


def _plain_row(item, lang: str) -> str:
    """未支持的语言：一行灰字，不做按钮——点不动的东西做成按钮是骗点击。"""
    code, native, _zh, _en = item
    name = _display(code, lang)
    # 本地写法只在跟显示名不一样时补一句，英文页那行就不带汉字了
    extra = native if (not _is_en(lang) and native != name) else code
    return ('<Grid Margin="0,4,0,0"><Grid.ColumnDefinitions>'
            '<ColumnDefinition Width="1*" /><ColumnDefinition Width="Auto" />'
            '</Grid.ColumnDefinitions>'
            '<TextBlock Grid.Column="0" Text="' + attr(name) + '" FontSize="12" '
            'Foreground="{DynamicResource ColorBrush3}" />'
            '<TextBlock Grid.Column="1" Text="' + attr(extra) + '" FontSize="11" Margin="10,0,0,0" '
            'Foreground="{DynamicResource ColorBrush3}" /></Grid>')


def build_language_page(base_url: str, ip: str = "", query: str = "",
                        lang: str = "") -> str:
    """语言选择页正文。``lang`` 只给体检用：传了就不碰真实记录。"""
    base = escape_url_attr(base_url)
    params = parse_query(query)
    current = lang or profiles.lang_for(ip)
    result = ""

    wanted = (params.get("lang") or "").strip()
    if wanted:
        if _supported(wanted):
            if not lang:
                profiles.PROFILES.bind(ip, lang=wanted)
            current = wanted
            result = t("language.switched", current,
                       language=LANG_NAMES.get(current, current))
        else:
            label = _display(wanted, current) if wanted in BY_CODE else wanted
            result = t("language.unsupported_click", current, language=label)

    keyword = (params.get("q") or "").strip()
    matched = search(keyword)
    supported = [item for item in matched if _supported(item[0])]
    others = [item for item in matched if not _supported(item[0])]

    body = [heading(t("language.current_title", current), "0,20,0,0"),
            _current_block(current, result),
            input_row("langq", t("language.search_hint", current), 38, "0,14,0,0"),
            help_button(t("language.search_button", current), ICON_KEY,
                        bind_to("langq", base, "/language_page.json"), 38, margin="0,8,0,0")]

    if keyword:
        body.append('<local:MyHint Theme="Blue" Margin="0,12,0,0" Text="'
                    + attr(t("language.search_result", current, q=keyword, n=len(matched)))
                    + '" />')
        body.append(help_button(t("language.clear_search", current), ICON_BACK,
                                base + "/language_page.json", 34, margin="0,8,0,0", color=""))
        if not matched:
            body.append(note(t("language.search_empty", current), "0,10,0,0"))

    if supported:
        body.append(heading(t("language.supported_title", current, n=len(supported)), "0,20,0,0"))
        body.append('<StackPanel Margin="0,4,0,0">'
                    + "".join(_entry_row(item, current, base, item[0] == current)
                              for item in supported)
                    + "</StackPanel>")

    if others:
        body.append(heading(t("language.others_title", current, n=len(others)), "0,20,0,0"))
        body.append(note(t("language.others_hint", current), "0,6,0,0"))
        body.append("".join(_plain_row(item, current) for item in others))

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
        lang = wanted if _supported(wanted) else profiles.lang_for(ip)
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
