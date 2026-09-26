# -*- coding: utf-8 -*-
"""农历换算与节日/倒计时（对应上游 functions/_lib/lunar.js）。

农历数据表 ``LUNAR_INFO`` 覆盖 1900-2100，一年一个整数：
    低 4 位       闰月月份（0 表示无闰月）
    第 5-16 位    12 个月的大小月（1 = 30 天）
    第 17 位      闰月天数（1 = 30 天）

与上游的两处差异（都是上游的类型 bug，这里按本来意图实现）：

1. 上游 ``getBeijingDate()`` 把 ``year/month/day`` 返回成**字符串**，
   而节日表里是数字，``f.month === date.month`` 全等比较永远为 false，
   导致公历节日（元旦、圣诞……）实际上从不生效。这里统一用 int。
2. 上游倒计时里 ``date.year + 1`` 是字符串拼接（"2026" + 1 → "20261"），
   跨年的节日会被算成 NaN 而悄悄丢弃。这里显式取“下一次发生日”。
"""
from __future__ import annotations

from datetime import date, timedelta

from .data.calendar import FESTIVALS, LUNAR_FESTIVALS, LUNAR_INFO
from .i18n import DEFAULT_LANG, has, t
from .xaml import escape_attr

# 农历数据表的基准日：Date.UTC(1900, 1, 30) 在 JS 里会被规范化成 1900-03-02
_LUNAR_EPOCH = date(1900, 3, 2)
_SOLAR_EPOCH = date(1900, 1, 31)


def _info(year: int) -> int:
    return LUNAR_INFO[year - 1900]


def leap_month(year: int) -> int:
    """该农历年的闰月月份，0 表示无闰月。"""
    return _info(year) & 0xF


def leap_days(year: int) -> int:
    """闰月天数。"""
    if not leap_month(year):
        return 0
    return 30 if (_info(year) & 0x10000) else 29


def month_days(year: int, month: int) -> int:
    """该农历年第 month 个月的天数。"""
    return 30 if (_info(year) & (0x10000 >> month)) else 29


def year_days(year: int) -> int:
    """该农历年的总天数。"""
    total = 348
    bit = 0x8000
    while bit > 0x8:
        if _info(year) & bit:
            total += 1
        bit >>= 1
    return total + leap_days(year)


def lunar_to_solar(year: int, month: int, day: int) -> date:
    """农历（非闰月）→ 公历日期。"""
    offset = 0
    for y in range(1900, year):
        offset += year_days(y)

    leap = leap_month(year)
    added = False
    for m in range(1, month):
        if not added and leap > 0 and leap <= m:
            offset += leap_days(year)
            added = True
        offset += month_days(year, m)

    return _LUNAR_EPOCH + timedelta(days=offset + day - 31)


def solar_to_lunar(target: date) -> tuple[int, int, int, bool]:
    """公历 → 农历，返回 ``(年, 月, 日, 是否闰月)``。"""
    offset = (target - _SOLAR_EPOCH).days

    year = 1900
    temp = 0
    while year < 2101 and offset > 0:
        temp = year_days(year)
        offset -= temp
        year += 1
    if offset < 0:
        offset += temp
        year -= 1

    leap = leap_month(year)
    is_leap = False
    month = 1
    while month < 13 and offset > 0:
        if leap > 0 and month == leap + 1 and not is_leap:
            month -= 1
            is_leap = True
            temp = leap_days(year)
        else:
            temp = month_days(year, month)
        if is_leap and month == leap + 1:
            is_leap = False
        offset -= temp
        month += 1

    if offset == 0 and leap > 0 and month == leap + 1:
        if is_leap:
            is_leap = False
        else:
            is_leap = True
            month -= 1
    if offset < 0:
        offset += temp
        month -= 1

    return year, month, offset + 1, is_leap


def festival_name(key: str, fallback: str, lang: str) -> str:
    """内置节日的名字/祝词按语言取；后台自定义的节日没有词条，原样用配置里的。"""
    return t(key, lang) if has(key, lang) else fallback


def _localized(item: dict, prefix: str, lang: str) -> dict:
    return {"month": item.get("month"), "day": item.get("day"),
            "name": festival_name(prefix + ".name", str(item.get("name") or ""), lang),
            "msg": festival_name(prefix + ".msg", str(item.get("msg") or ""), lang)}


def get_festival(today: date, extra: list | None = None,
                 lang: str = DEFAULT_LANG) -> dict | None:
    """命中今天的节日：先查公历，再查农历。``extra`` 为后台自定义公历节日。"""
    for item in FESTIVALS:
        if int(item["month"]) == today.month and int(item["day"]) == today.day:
            return _localized(item, "festival.solar." + str(int(item["month"]))
                              + "-" + str(int(item["day"])), lang)
    # 后台自定义的节日是站长自己填的文案，没有词条，原样显示
    for item in (extra or []):
        if int(item["month"]) == today.month and int(item["day"]) == today.day:
            return {"month": today.month, "day": today.day,
                    "name": str(item.get("name") or ""), "msg": str(item.get("msg") or "")}

    _, lunar_month, lunar_day, _ = solar_to_lunar(today)
    for item in LUNAR_FESTIVALS:
        if item["lm"] == lunar_month and item["ld"] == lunar_day:
            localized = _localized(item, "festival.lunar." + str(item["lm"])
                                   + "-" + str(item["ld"]), lang)
            localized["month"], localized["day"] = today.month, today.day
            return localized
    return None


def build_festival_banner(festival: dict | None, lang: str = DEFAULT_LANG) -> str:
    """节日横幅（红色提示条）。"""
    if not festival:
        return ""
    text = escape_attr(t("lunar.festival_today", lang,
                         name=festival.get("name", ""), msg=festival.get("msg", "")))
    return '<local:MyHint Theme="Red" Margin="0,0,0,12" Text="' + text + '" />'


def _days_until(month: int, day: int, today: date) -> int:
    """距离下一次 (month, day) 的天数；今天就是则返回 0。"""
    for year in (today.year, today.year + 1):
        try:
            target = date(year, month, day)
        except ValueError:                  # 2 月 29 日等
            continue
        if target >= today:
            return (target - today).days
    return 366


def _countdown_line(name: str, diff: int, lang: str) -> str:
    return (t("lunar.countdown_today", lang, name=name) if diff == 0
            else t("lunar.countdown_left", lang, name=name, days=diff))


def build_countdown_xaml(today: date, custom: dict | None = None, extra: list | None = None,
                         lang: str = DEFAULT_LANG) -> str:
    """右上角胶囊：节日/纪念日倒计时。

    ``custom`` 为后台自定义倒计时（优先），形如 ``{"name": "生日", "date": "2026-10-01"}``。
    """
    if custom and custom.get("name") and custom.get("date"):
        parsed = _parse_iso_date(str(custom["date"]))
        if parsed:
            # 自定义倒计时认年份：今年的目标日已过则顺延到明年
            target = parsed if parsed >= today else _shift_year(parsed, parsed.year + 1)
            return _countdown_pill(_countdown_line(str(custom["name"]),
                                                   (target - today).days, lang))

    events = [{"name": festival_name("festival.solar." + str(int(f["month"])) + "-"
                                     + str(int(f["day"])) + ".name", str(f["name"]), lang),
               "month": int(f["month"]), "day": int(f["day"])}
              for f in FESTIVALS]
    # 后台自定义的节日/纪念日是站长自己填的文案，不翻
    events += [{"name": str(f.get("name") or ""), "month": int(f["month"]), "day": int(f["day"])}
               for f in (extra or [])]

    # 农历节日要换算成公历，取今年与明年两年，避免跨年漏掉
    for year in (today.year, today.year + 1):
        for item in LUNAR_FESTIVALS:
            solar = lunar_to_solar(year, item["lm"], item["ld"])
            if today.year <= solar.year <= today.year + 1:
                events.append({
                    "name": festival_name("festival.lunar." + str(item["lm"]) + "-"
                                          + str(item["ld"]) + ".name", str(item["name"]), lang),
                    "month": solar.month, "day": solar.day})

    if not events:
        return ""
    best = min(events, key=lambda e: _days_until(e["month"], e["day"], today))
    diff = _days_until(best["month"], best["day"], today)
    return _countdown_pill(_countdown_line(best["name"], diff, lang))


def _countdown_pill(line: str) -> str:
    return ('<Border HorizontalAlignment="Right" VerticalAlignment="Top" Margin="0,16,16,0" '
            'Background="#59000000" CornerRadius="12" Padding="12,8,12,8">'
            '<TextBlock Text="' + escape_attr(line) + '" FontSize="12" FontWeight="Bold" Foreground="White" />'
            "</Border>")


def _shift_year(target: date, year: int) -> date:
    """把日期挪到指定年份（2 月 29 日顺延到 3 月 1 日）。"""
    try:
        return target.replace(year=year)
    except ValueError:
        return date(year, 3, 1)


def _parse_iso_date(text: str) -> date | None:
    try:
        year, month, day = (int(x) for x in text.split("-"))
        return date(year, month, day)
    except Exception:
        return None
