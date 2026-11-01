# -*- coding: utf-8 -*-
"""「访问浏览」内页：最近 7 天的访问统计 + 按省份涂色的中国地图。

跟计算器页、设置页一样是 **PCL 内部页**（走「打开帮助」翻开，不开浏览器）：
``/visit_page.json`` 是 PCL 先读的标题/描述，``/visit_page.xaml`` 是正文。

**为什么地图要重画一遍**：后台 ``/admin`` 那张图是 HTML + SVG（浏览器画），
PCL 里没有浏览器，只有 WPF 的 XAML。所以省份轮廓取自 ``chinamap.PATHS``，
铺在一个 ``Canvas``（坐标就是地图原始坐标系 1000×880）里，逐省一个 ``Path``，
外面套 ``Viewbox`` 等比缩到卡片宽度——颜色深浅沿用后台那套「按访问量分级」。

**为什么不显示 IP**：这是给访客看的页面。统计只露地区（省 · 市）和时间，
匿名性跟后台的「最近来访」列保持一致，但后台那列带了 IP，这里不带。

配色规矩跟别处一样：只许 ``ColorBrush1/2/3/7``（``palette.stray_brushes``
会抓超纲号码），深浅靠 ``Opacity``，不新造画刷。
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from . import palette
from . import profiles
from .chinamap import INSET_BOX, INSET_ISLANDS, INSET_LINES, PATHS as PROVINCE_PATHS
from .i18n import normalize_lang, t
from .log import warn
from .store import STATS_KEEP_DAYS
from .xaml import divider, escape_attr, heading, nav_row, note

_META = {"/visit_page.json": ("visit.title", "visit.desc")}
_DO = {"/visit_page.xaml"}

VISIT_PATHS = set(_META) | _DO

DAYS = STATS_KEEP_DAYS

# 没数据 / 四个分级：等级越高越深。后台 lv1-4 是 accent 的 26/50/74/100%，
# 这边换不成百分比（画刷是资源），所以用「画刷 + 透明度」凑出同样的层次。
LEVEL_STYLE = {
    0: ('ColorBrush7', "0.45"),
    1: ('ColorBrush3', "0.35"),
    2: ('ColorBrush3', "0.72"),
    3: ('ColorBrush1', "0.55"),
    4: ('ColorBrush1', "0.88"),
}

# 省份后缀，跟 server._short_province 同一套；chinamap 的键是简称
_PROVINCE_SUFFIX = ("维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区",
                    "自治区", "省", "市")


def empty_summary() -> dict:
    """统计读不出来时的空壳，页面照常能开。"""
    return {"total": 0, "today": 0, "unique": 0, "domestic": 0, "foreign": 0,
            "countries": [], "regions": [], "regions_cn": [], "regions_abroad": [],
            "provinces": [], "hosts": [], "recent": [], "trend": []}


# ============ 小工具 ============

def _short(name: str) -> str:
    name = (name or "").strip()
    for suffix in _PROVINCE_SUFFIX:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _region_text(code: str, city: str, province: str, lang: str) -> str:
    """「浙江 · 杭州」/「美国 · 洛杉矶」/「未定位」——跟后台 regionName() 同口径。"""
    code = (code or "XX").upper()
    city = (city or "").strip()
    province = (province or "").strip()
    country = t("visit.country." + code, lang) if _has_country(code, lang) else code
    if code == "CN":
        if province:
            return province if not city or city == province else province + " · " + city
        return city or t("visit.region_unknown", lang)
    if province and province != country:
        tail = province if not city or city == province else province + " · " + city
        return country + " · " + tail
    if city and city != country:
        return country + " · " + city
    return country


def _has_country(code: str, lang: str) -> bool:
    from .i18n import has
    return has("visit.country." + code, lang)


def _ago(stamp_ms, lang: str) -> str:
    """相对时间：刚刚 / n 分钟前 / n 小时前 / n 天前。"""
    try:
        stamp = float(stamp_ms) / 1000.0
    except (TypeError, ValueError):
        return ""
    gap = max(0.0, time.time() - stamp)
    if gap < 60:
        return t("visit.now_just", lang)
    if gap < 3600:
        return t("visit.minutes_ago", lang, n=int(gap // 60))
    if gap < 86400:
        return t("visit.hours_ago", lang, n=int(gap // 3600))
    return t("visit.days_ago", lang, n=int(gap // 86400))


def _levels(provinces) -> dict:
    """省 → (等级 1-4, 次数)。峰值归一，跟后台 chinaMap() 的算法一样。"""
    rows = [(str(item.get("name") or ""), int(item.get("count") or 0))
            for item in (provinces or [])]
    rows = [(name, count) for name, count in rows if name and count > 0]
    if not rows:
        return {}
    peak = max(count for _, count in rows)
    result = {}
    for name, count in rows:
        # 整数版 ceil：-(count*4 // -peak) == ceil(count/peak*4)
        level = -(-count * 4 // peak)
        level = max(1, min(4, level))
        result[_short(name)] = (level, count)
    return result


# ============ 各个板块 ============

def _kpi_card(column: int, label: str, value: str, margin: str) -> str:
    # Margin 传空串时属性整条不能写：WPF 的 ThicknessConverter 见到 Margin="" 会抛
    # FormatException（"值无效。它必须包括一个、两个或四个分隔的长度"），整页 XAML 直接崩。
    attrs = ' Grid.Column="' + str(column) + '"'
    if margin:
        attrs += ' Margin="' + margin + '"'
    return ('<Border' + attrs + ' '
            'Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="12,10,12,11">'
            "<StackPanel>"
            '<TextBlock Text="' + escape_attr(label) + '" FontSize="10" TextWrapping="Wrap" '
            'Foreground="{DynamicResource ColorBrush3}" />'
            '<TextBlock Text="' + escape_attr(value) + '" FontSize="20" FontWeight="Bold" '
            'Margin="0,5,0,0" Foreground="{DynamicResource ColorBrush1}" />'
            "</StackPanel></Border>")


def _kpi_row(cards) -> str:
    """一行三张卡；``cards`` 是三个 (label, value)。"""
    cells = []
    for index, (label, value) in enumerate(cards):
        margin = "" if index == 2 else "0,0,8,0"
        cells.append(_kpi_card(index, label, value, margin))
    return ('<Grid Margin="0,10,0,0"><Grid.ColumnDefinitions>'
            '<ColumnDefinition Width="1*" /><ColumnDefinition Width="1*" />'
            '<ColumnDefinition Width="1*" /></Grid.ColumnDefinitions>'
            + "".join(cells) + "</Grid>")


def _build_kpis(summary, lang: str, days: int) -> str:
    total = int(summary.get("total") or 0)
    today = int(summary.get("today") or 0)
    unique = int(summary.get("unique") or 0)
    per_day = round(total / float(days), 1) if days else 0
    domestic = int(summary.get("domestic") or 0)
    foreign = int(summary.get("foreign") or 0)
    return (_kpi_row([(t("visit.kpi_total", lang, days=days), str(total)),
                      (t("visit.kpi_today", lang), str(today)),
                      (t("visit.kpi_unique", lang), str(unique))])
            + _kpi_row([(t("visit.kpi_daily", lang), str(per_day)),
                        (t("visit.kpi_domestic", lang), str(domestic)),
                        (t("visit.kpi_foreign", lang), str(foreign))]))


def _map_legend(lang: str) -> str:
    """「少 ▭▭▭▭ 多」——四个小方块示意深浅。"""
    squares = []
    for level in (1, 2, 3, 4):
        brush, opacity = LEVEL_STYLE[level]
        squares.append('<Rectangle Width="14" Height="10" Margin="4,0,0,0" RadiusX="2" '
                       'RadiusY="2" Opacity="' + opacity + '" '
                       'Fill="{DynamicResource ' + brush + '}" />')
    return ('<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" '
            'VerticalAlignment="Center" Margin="0,6,0,0">'
            '<TextBlock Text="' + escape_attr(t("visit.legend_less", lang)) + '" '
            'FontSize="10" VerticalAlignment="Center" '
            'Foreground="{DynamicResource ColorBrush3}" />'
            + "".join(squares)
            + '<TextBlock Text="' + escape_attr(t("visit.legend_more", lang)) + '" '
            'FontSize="10" VerticalAlignment="Center" Margin="6,0,0,0" '
            'Foreground="{DynamicResource ColorBrush3}" /></StackPanel>')


def _build_map(summary, lang: str) -> str:
    levels = _levels(summary.get("provinces"))
    if not levels:
        return note(t("visit.map_empty", lang), "0,10,0,0") + _map_legend(lang)

    parts = []
    for name, path in PROVINCE_PATHS.items():
        level = levels.get(name, (0, 0))[0]
        brush, opacity = LEVEL_STYLE[level]
        parts.append('<Path Data="' + escape_attr(path) + '" Opacity="' + opacity + '" '
                     'Fill="{DynamicResource ' + brush + '}" Stroke="{DynamicResource ColorBrush7}" '
                     'StrokeThickness="0.6" />')

    # 南海诸岛小图：底框 + 界线 + 岛礁，跟后台那张 inset 一致
    left, top, width, height = INSET_BOX
    parts.append('<Rectangle Canvas.Left="' + str(left) + '" Canvas.Top="' + str(top)
                 + '" Width="' + str(width) + '" Height="' + str(height)
                 + '" RadiusX="8" RadiusY="8" Opacity="0.30" Fill="{DynamicResource ColorBrush7}" '
                 'Stroke="{DynamicResource ColorBrush3}" StrokeThickness="0.8" />')
    for data, thickness in ((INSET_LINES, "0.7"), (INSET_ISLANDS, "1.2")):
        parts.append('<Path Data="' + escape_attr(data) + '" Fill="{DynamicResource ColorBrush3}" '
                     'Stroke="{DynamicResource ColorBrush3}" StrokeThickness="' + thickness + '" '
                     'Opacity="0.8" />')
    parts.append('<TextBlock Canvas.Left="' + str(left + 6) + '" Canvas.Top="' + str(top + 6)
                 + '" Text="' + escape_attr(t("visit.map_inset", lang)) + '" FontSize="10" '
                 'Foreground="{DynamicResource ColorBrush3}" />')

    return ('<Viewbox Height="440" Stretch="Uniform" HorizontalAlignment="Center" Margin="0,10,0,0">'
            '<Canvas Width="1000" Height="880">' + "".join(parts) + "</Canvas></Viewbox>"
            + _map_legend(lang))


def _build_rank(summary, lang: str) -> str:
    rows = [(item.get("name"), int(item.get("count") or 0))
            for item in (summary.get("provinces") or [])]
    rows = [row for row in rows if row[0] and row[1] > 0][:12]
    if not rows:
        return note(t("visit.rank_empty", lang), "0,8,0,0")
    peak = max(count for _, count in rows)
    parts = []
    for index, (name, count) in enumerate(rows):
        fill = max(8, int(round(count / float(peak) * 100)))
        # 轨道拆两列：左列宽度就是占比，右列吃掉剩下的——比算绝对像素稳，
        # 卡片宽度随窗口变时条子跟着伸缩。
        parts.append(
            '<Grid Margin="0,' + ("8" if index else "10") + ',0,0">'
            '<Grid.ColumnDefinitions><ColumnDefinition Width="70" />'
            '<ColumnDefinition Width="1*" /><ColumnDefinition Width="Auto" /></Grid.ColumnDefinitions>'
            '<TextBlock Grid.Column="0" Text="' + escape_attr(str(name)) + '" FontSize="12" '
            'VerticalAlignment="Center" Foreground="{DynamicResource ColorBrush2}" />'
            '<Grid Grid.Column="1" Height="10" Margin="8,0,10,0" VerticalAlignment="Center">'
            '<Grid.ColumnDefinitions><ColumnDefinition Width="' + str(fill) + '*" />'
            '<ColumnDefinition Width="' + str(100 - fill) + '*" /></Grid.ColumnDefinitions>'
            '<Border Grid.ColumnSpan="2" Height="10" CornerRadius="5" '
            'Background="{DynamicResource ColorBrush7}" />'
            '<Border Grid.Column="0" Height="10" CornerRadius="5" Opacity="0.62" '
            'Background="{DynamicResource ColorBrush1}" /></Grid>'
            '<TextBlock Grid.Column="2" Text="' + escape_attr(t("visit.rank_value", lang, count=count))
            + '" FontSize="11" VerticalAlignment="Center" '
            'Foreground="{DynamicResource ColorBrush3}" /></Grid>')
    return "".join(parts)


def _build_regions(summary, lang: str) -> str:
    parts = []
    for key, items in (("visit.region_cn", summary.get("regions_cn") or []),
                       ("visit.region_abroad", summary.get("regions_abroad") or [])):
        rows = [(item, int(item.get("count") or 0)) for item in items]
        rows = [row for row in rows if row[1] > 0][:8]
        total = sum(count for _, count in rows)
        parts.append('<TextBlock Text="' + escape_attr(t(key, lang, count=total))
                     + '" FontSize="12" FontWeight="Bold" Margin="0,10,0,4" '
                     'Foreground="{DynamicResource ColorBrush2}" />')
        if not rows:
            parts.append(note(t("visit.region_empty", lang), "0,0,0,0"))
            continue
        for item, count in rows:
            text = _region_text(item.get("code"), item.get("city"), item.get("province"), lang)
            parts.append('<Grid Margin="0,3,0,0"><Grid.ColumnDefinitions>'
                         '<ColumnDefinition Width="1*" /><ColumnDefinition Width="Auto" />'
                         '</Grid.ColumnDefinitions>'
                         '<TextBlock Grid.Column="0" Text="' + escape_attr(text) + '" FontSize="11" '
                         'TextTrimming="CharacterEllipsis" '
                         'Foreground="{DynamicResource ColorBrush2}" />'
                         '<TextBlock Grid.Column="1" Text="' + str(count) + '" FontSize="11" '
                         'Margin="10,0,0,0" Foreground="{DynamicResource ColorBrush3}" /></Grid>')
    return "".join(parts)


def _build_trend(summary, lang: str) -> str:
    trend = summary.get("trend") or []
    if not trend:
        return note(t("visit.region_empty", lang), "0,8,0,0")
    peak = max([int(item.get("count") or 0) for item in trend] + [1])
    cells = []
    for index, item in enumerate(trend):
        count = int(item.get("count") or 0)
        height = max(3, int(round(count / float(peak) * 84)))
        date = str(item.get("date") or "")
        label = t("visit.trend_today", lang) if index == len(trend) - 1 else date[5:]
        cells.append('<StackPanel Grid.Column="' + str(index) + '" VerticalAlignment="Bottom">'
                     '<TextBlock Text="' + str(count) + '" FontSize="9" HorizontalAlignment="Center" '
                     'Margin="0,0,0,3" Foreground="{DynamicResource ColorBrush3}" />'
                     '<Border Height="' + str(height) + '" Margin="3,0" CornerRadius="3,3,0,0" '
                     'Opacity="0.6" Background="{DynamicResource ColorBrush1}" />'
                     '<TextBlock Text="' + escape_attr(label) + '" FontSize="8" '
                     'HorizontalAlignment="Center" Margin="0,4,0,0" '
                     'Foreground="{DynamicResource ColorBrush3}" /></StackPanel>')
    columns = "".join('<ColumnDefinition Width="1*" />' for _ in trend)
    return ('<Grid Height="125" Margin="0,10,0,0"><Grid.ColumnDefinitions>' + columns
            + "</Grid.ColumnDefinitions>" + "".join(cells) + "</Grid>")


def _build_recent(summary, lang: str) -> str:
    rows = summary.get("recent") or []
    parts = []
    for item in rows[:8]:
        text = _region_text(item.get("code"), item.get("city"), item.get("province"), lang)
        when = _ago(item.get("ts"), lang)
        parts.append('<TextBlock Text="'
                     + escape_attr(t("visit.recent_line", lang, region=text, when=when))
                     + '" FontSize="11" TextWrapping="Wrap" Margin="0,4,0,0" '
                     'Foreground="{DynamicResource ColorBrush2}" />')
    if not parts:
        return note(t("visit.recent_empty", lang), "0,8,0,0")
    return "".join(parts)


# ============ 页面 ============

def build_visit_page(summary: dict, base_url: str = "", ip: str = "",
                     lang: str = "", days: int = DAYS) -> str:
    """整页 XAML（不含 ``palette.apply``，交给 ``handle`` 统一套）。"""
    from .i18n import DEFAULT_LANG
    lang = lang or DEFAULT_LANG
    summary = summary or empty_summary()

    body = [_build_kpis(summary, lang, days), divider("0,22,0,4")]
    body.append(heading(t("visit.map_title", lang), "0,6,0,0"))
    body.append(note(t("visit.map_hint", lang, days=days)))
    body.append(_build_map(summary, lang))
    body.append(divider("0,22,0,4"))
    body.append(heading(t("visit.rank_title", lang), "0,6,0,0"))
    body.append(_build_rank(summary, lang))
    body.append(heading(t("visit.region_title", lang)))
    body.append(_build_regions(summary, lang))
    body.append(heading(t("visit.trend_title", lang, days=days)))
    body.append(_build_trend(summary, lang))
    body.append(heading(t("visit.recent_title", lang)))
    body.append(_build_recent(summary, lang))
    body.append(divider("0,20,0,4"))
    body.append(note(t("visit.privacy", lang, days=days)))
    body.append(note(t("visit.refresh_hint", lang), "0,6,0,0"))

    return ('<local:MyCard Title="' + escape_attr(t("visit.title", lang))
            + '" CanSwap="False"><StackPanel Margin="25,40,23,20">'
            '<TextBlock Text="' + escape_attr(t("visit.title", lang))
            + '" FontSize="24" FontWeight="Bold" Foreground="#FF000000" '
            'HorizontalAlignment="Center" />'
            + note(t("visit.desc", lang, days=days), "0,10,0,0")
            + "".join(body)
            + nav_row(refresh_url=(base_url or "") + "/visit_page.xaml", lang=lang)
            + "</StackPanel></local:MyCard>")


# ============ 路由 ============

def _json_response(body: str):
    from .server import Response
    return Response.text(body, content_type="application/json; charset=utf-8",
                         headers={"Cache-Control": "no-store"})


def _xaml_response(body: str):
    from .server import Response
    return Response.xaml(body)


def _parse_lang(query: str) -> str:
    """``?lang=`` 优先（PCL 上刚切完语言时用），没有就按 IP 的记录来。"""
    for chunk in (query or "").split("&"):
        if chunk.startswith("lang="):
            return normalize_lang(chunk[5:])
    return ""


def handle(service, path: str, query: str, ip: str, origin: str = ""):
    """处理 ``/visit_page.*``；不是我们的路径就返回 None。"""
    if path in _META:
        lang = _parse_lang(query) or profiles.lang_for(ip)
        title_key, desc_key = _META[path]
        body = json.dumps({"Title": t(title_key, lang),
                           "Description": t(desc_key, lang, days=DAYS)},
                          ensure_ascii=False)
        return _json_response(body)

    if path not in _DO:
        return None

    base = service.config.resolved_base_url(origin)
    lang = _parse_lang(query) or profiles.lang_for(ip)
    try:
        summary = service.stats.summary(DAYS)
    except Exception as exc:                       # 统计挂了也得能开页面
        warn("[Visit] 统计读取失败：" + repr(exc))
        summary = empty_summary()
    try:
        page = build_visit_page(summary, base, ip, lang)
    except Exception as exc:
        warn("[Visit] 页面构建失败：" + repr(exc))
        page = build_visit_page(empty_summary(), base, ip, lang)
    return _xaml_response(palette.apply(page, profiles.PROFILES.palette_for(ip)))
