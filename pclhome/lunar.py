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

节日表是**两份合并**的：``data/calendar.py`` 那份从上游移植，``data/festivals.py``
那份是我们自己补的（国庆节、清明、除夕……）。倒计时一律按**算出来的公历日期**排，
不拿"月-日"套回今年——农历节日逐年对应的公历日子能差半个月（2026 年重阳 10-18、
2027 年重阳 10-08），套回今年就会把倒计时算错十来天。
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from .data.calendar import FESTIVALS as _UPSTREAM_FESTIVALS
from .data.calendar import LUNAR_FESTIVALS as _UPSTREAM_LUNAR_FESTIVALS, LUNAR_INFO
from .data.festivals import EXTRA_FESTIVALS, EXTRA_LUNAR_FESTIVALS
from .i18n import DEFAULT_LANG, has, t
from .xaml import escape_attr

# 内置节日 = 上游移植的 + 我们自己补的
FESTIVALS = list(_UPSTREAM_FESTIVALS) + list(EXTRA_FESTIVALS)
LUNAR_FESTIVALS = list(_UPSTREAM_LUNAR_FESTIVALS) + list(EXTRA_LUNAR_FESTIVALS)

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


@lru_cache(maxsize=512)
def lunar_to_solar(year: int, month: int, day: int) -> date:
    """农历（非闰月）→ 公历日期。

    逐个农历年累加，一次要跑一百多年，所以加了 memo：倒计时每条农历节日都要
    换算三次（今年/明年/去年），一页就是二十多次同一个纯函数。键是三个整数，
    不会失效，缓存住就行。
    """
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


def qingming_day(year: int) -> int:
    """清明落在公历 4 月的第几天。

    清明是节气，没有固定日子，得按年份算。用寿星公式 ``[Y*0.2422 + C] - [Y/4]``
    （Y 取年份后两位，21 世纪 C=4.81、20 世纪 C=5.59，方括号是取整），公开的
    节气资料里 21 世纪这一档"例外：无"。拿权威日历核过 2020-2026 和 2030-2033
    （4/4、4/4、4/5、4/5、4/4、4/4、4/5，以及 5、5、4、4），都对得上。
    **1900 年前和 2100 年后不保证**——真到那年再换算法。
    """
    y = year % 100
    base = y * 0.2422 + (4.81 if year >= 2000 else 5.59)
    return int(base) - int(y / 4)


# 按节气定的节日：名字 → (公历月, 算第几天的函数)
_TERM_DAYS = {"qingming": (4, qingming_day)}


def festival_key(item: dict, kind: str) -> str:
    """一条节日的词条键前缀。自己写了 ``key`` 就用它，否则按「月-日」拼。

    月日固定的节日两种都行，但浮动日（清明、除夕）只能写 ``key``：按算出来的
    月日拼键的话，同一条节日今年叫 ``festival.lunar.12-30``、明年叫 ``12-29``，
    翻译表根本跟不上。
    """
    if item.get("key"):
        return "festival." + str(item["key"])
    if kind == "solar":
        return "festival.solar." + str(int(item["month"])) + "-" + str(int(item["day"]))
    return "festival.lunar." + str(int(item["lm"])) + "-" + str(int(item["ld"]))


def _describe(item: dict, key: str, when: date, lang: str) -> dict:
    """一条节日 + 它落在的公历日期 → 页面上要的那份（名字/祝词按语言取）。"""
    return {"month": when.month, "day": when.day,
            "name": festival_name(key + ".name", str(item.get("name") or ""), lang),
            "msg": festival_name(key + ".msg", str(item.get("msg") or ""), lang)}


def festival_dates(item: dict, today: date) -> list:
    """一条节日在 ``today`` 前后可能落在的公历日期，用来挑“下一次”。

    **返回的是真日期，不是月日**：这正是倒计时那个 bug 的修法。农历节日逐年对应
    的公历日子能差半个月，把明年的月日套到今年算，就会少算十来天。
    """
    term = item.get("term")
    if term:
        found = _TERM_DAYS.get(str(term))
        if not found:
            return []
        month, day_of = found
        return [date(y, month, day_of(y)) for y in (today.year, today.year + 1)]
    if "month" in item:
        out = []
        for year in (today.year, today.year + 1):
            try:
                out.append(date(year, int(item["month"]), int(item["day"])))
            except (TypeError, ValueError):     # 2 月 29 日这种，平年没有
                pass
        return out
    if "lm" not in item:
        return []
    # 农历年 Y 的节日可能落在公历年 Y 或 Y+1（腊月、除夕都是后者），所以往前多取一年
    lm, ld = int(item["lm"]), int(item.get("ld") or 0)
    out = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            out.append(lunar_to_solar(year, lm, month_days(year, lm) if ld == 0 else ld))
        except Exception:
            pass
    return out


def _solar_hit(item: dict, today: date) -> bool:
    term = item.get("term")
    if term:
        found = _TERM_DAYS.get(str(term))
        if not found:
            return False
        month, day_of = found
        return today.month == month and today.day == day_of(today.year)
    if "month" not in item:
        return False
    return int(item["month"]) == today.month and int(item["day"]) == today.day


def _as_int(value):
    """能当整数用就当，当不了给 None——后台填的节日坏了不该让主页 500。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lunar_hit(item: dict, lunar_today: tuple) -> bool:
    """``lunar_today`` 是 ``solar_to_lunar`` 的结果。闰月不算——表里没有闰月节日。"""
    year, month, day, is_leap = lunar_today
    if is_leap or int(item["lm"]) != month:
        return False
    want = int(item.get("ld") or 0)
    return day == (month_days(year, month) if want == 0 else want)


def get_festival(today: date, extra: list | None = None,
                 lang: str = DEFAULT_LANG) -> dict | None:
    """命中今天的节日：先查公历，再查农历。``extra`` 为后台自定义公历节日。"""
    for item in FESTIVALS:
        if _solar_hit(item, today):
            return _describe(item, festival_key(item, "solar"), today, lang)
    # 后台自定义的节日是站长自己填的文案，没有词条，原样显示
    for item in (extra or []):
        if not isinstance(item, dict):
            continue
        if _as_int(item.get("month")) == today.month and _as_int(item.get("day")) == today.day:
            return {"month": today.month, "day": today.day,
                    "name": str(item.get("name") or ""), "msg": str(item.get("msg") or "")}

    lunar_today = solar_to_lunar(today)
    for item in LUNAR_FESTIVALS:
        if _lunar_hit(item, lunar_today):
            return _describe(item, festival_key(item, "lunar"), today, lang)
    return None


def build_festival_banner(festival: dict | None, lang: str = DEFAULT_LANG) -> str:
    """节日横幅（红色提示条）。"""
    if not festival:
        return ""
    text = escape_attr(t("lunar.festival_today", lang,
                         name=festival.get("name", ""), msg=festival.get("msg", "")))
    return '<local:MyHint Theme="Red" Margin="0,0,0,12" Text="' + text + '" />'


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

    # 每条节日先算成公历日期，再按日期取最近的一个——不能只留月日再套回今年
    events = []
    for item, kind in ([(f, "solar") for f in FESTIVALS]
                       + [(f, "lunar") for f in LUNAR_FESTIVALS]):
        name = festival_name(festival_key(item, kind) + ".name",
                             str(item.get("name") or ""), lang)
        events += [(when, name) for when in festival_dates(item, today)]
    # 后台自定义的节日/纪念日是站长自己填的文案，不翻
    for item in (extra or []):
        if isinstance(item, dict):
            events += [(when, str(item.get("name") or ""))
                       for when in festival_dates(item, today)]

    upcoming = [event for event in events if event[0] >= today]
    if not upcoming:
        return ""
    target, name = min(upcoming, key=lambda event: event[0])
    return _countdown_pill(_countdown_line(name, (target - today).days, lang))


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
