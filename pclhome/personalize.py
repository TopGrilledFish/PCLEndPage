# -*- coding: utf-8 -*-
"""确定性个性化：同一 IP + 同一（北京）日期 → 同一份内容。

每日一言不在这里——它由 saying.py 提供，全站同一句（见那个模块的说明）。

对应上游 _middleware.js 里的 hashCode / deterministicIndex / getBeijingDate /
getScoreInfo。算法逐位对齐上游的 djb2 变体，保证换实现不换结果：

    hash = 5381
    for c in s:
        hash = (ToInt32(hash << 5) + hash + c) & 0x7FFFFFFF

JS 的 ``<<`` 会把结果截断成有符号 32 位整数，而 ``+`` 是双精度浮点加法，
最后才做 ``& 0x7FFFFFFF``。这里的 ``_to_int32`` + ``&`` 与之一一对应
（Python 对负数做按位与等价于补码运算，低 31 位结果相同）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .data.text import GREETING_SUBS
from .i18n import DEFAULT_LANG, t_list

BEIJING = timezone(timedelta(hours=8))
_WEEKDAYS = ("日", "一", "二", "三", "四", "五", "六")


# ============ 哈希 ============

def _to_int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def hash_code(text: str) -> int:
    h = 5381
    for ch in text:
        h = (_to_int32(h << 5) + h + ord(ch)) & 0x7FFFFFFF
    return h


def deterministic_index(ip: str, date_str: str, salt: str, size: int) -> int:
    """按 ``IP|日期|盐`` 取模，同一人当天稳定。"""
    if size <= 0:
        return 0
    return hash_code(ip + "|" + date_str + "|" + salt) % size


# ============ 北京时间 ============

@dataclass(frozen=True)
class BeijingDate:
    today: date
    year: int
    month: int
    day: int
    weekday: str
    date_str: str
    greeting: str
    period: str

    @property
    def month_text(self) -> str:
        return str(self.month)

    @property
    def day_text(self) -> str:
        return str(self.day)


def get_beijing_date(now: datetime | None = None) -> BeijingDate:
    """取北京时间（UTC+8，不依赖服务器本地时区）。"""
    moment = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    hour = moment.hour
    if hour < 6:
        greeting, period = "凌晨好", "dawn"
    elif hour < 11:
        greeting, period = "早上好", "morning"
    elif hour < 14:
        greeting, period = "中午好", "noon"
    elif hour < 18:
        greeting, period = "下午好", "afternoon"
    elif hour < 23:
        greeting, period = "晚上好", "evening"
    else:
        greeting, period = "夜深了", "night"

    return BeijingDate(
        today=moment.date(),
        year=moment.year, month=moment.month, day=moment.day,
        weekday=_WEEKDAYS[(moment.weekday() + 1) % 7],   # Python: 周一=0 → JS getUTCDay 的 周日=0
        date_str=moment.strftime("%Y-%m-%d"),
        greeting=greeting, period=period,
    )


# ============ 每日内容 ============

def pick_greeting_sub(ip: str, date_str: str, period: str,
                      lang: str = DEFAULT_LANG) -> str:
    """问候语副标题。各语言同一位置的句子互相呼应（见 i18n 的 greeting.sub.*）。

    下标按**中文那份的长度**算（``deterministic_index`` 是上游 JS 的复刻，
    改长度会换算法），取译文时再取模兜住长度不一致的情况。
    """
    base = GREETING_SUBS.get(period) or GREETING_SUBS["morning"]
    pool = t_list("greeting.sub." + period, lang) or base
    index = deterministic_index(ip, date_str, "greeting_" + period, len(base))
    return pool[index % len(pool)]


def format_quote(template: str, ctx: BeijingDate) -> str:
    """替换一言里的日期占位。"""
    return (template
            .replace("{date}", str(ctx.month) + "月" + str(ctx.day) + "日")
            .replace("{weekday}", ctx.weekday)
            .replace("{year}", str(ctx.year)))
