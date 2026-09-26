# -*- coding: utf-8 -*-
"""界面文案的多语言查表。

三种语言：

* ``zh-hans`` 简体中文（默认，也是唯一"手写"的那份）
* ``zh-hant`` 繁體中文
* ``en``      英文

字符串表放 ``pclhome/i18n/*.json``，键是语义化的短名（``home.welcome``、
``calc.damage.hint``），按组前缀排。**zh-hans 是母版**：其它语言缺键就回退
到它，并且记一条 warn——这些串大多在主页关键路径上，不能因为漏翻一句就让
整页崩掉（跟项目其它地方的容错风格一致）。

繁中那份不是手打的：``tools/make_zh_hant.py`` 拿转换表从 zh-hans 生成，
再人工过一遍术语。改文案要改 zh-hans，然后重跑那个脚本。

**为什么不用运行期转换**：转换表在运行期要跟着走，等于把一份字典塞进服务端
热路径，而且简繁转换有上下文（"里"要变"裡"还是"裏"），一次生成好、人工校对
比每次请求都猜一遍靠谱。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .log import warn

DEFAULT_LANG = "zh-hans"
LANGS = ("zh-hans", "zh-hant", "en")
LANG_NAMES = {"zh-hans": "简体中文", "zh-hant": "繁體中文", "en": "English"}

I18N_DIR = Path(__file__).resolve().parent / "i18n"

# 常见写法的归一：PCL 那边、系统语言、用户手输都可能给出这些
_ALIASES = {
    "zh": "zh-hans", "zh-cn": "zh-hans", "zh-sg": "zh-hans", "zh-my": "zh-hans",
    "zh-hans-cn": "zh-hans", "chs": "zh-hans", "简体": "zh-hans", "简体中文": "zh-hans",
    "zh-tw": "zh-hant", "zh-hk": "zh-hant", "zh-mo": "zh-hant",
    "zh-hant-tw": "zh-hant", "cht": "zh-hant", "繁體": "zh-hant", "繁体": "zh-hant",
    "繁體中文": "zh-hant", "繁体中文": "zh-hant",
    "en-us": "en", "en-gb": "en", "en-ca": "en", "en-au": "en",
    "english": "en", "英文": "en", "英语": "en",
}


def normalize_lang(value) -> str:
    """把各种写法归一到三种语言码之一，认不出来就回默认。"""
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return DEFAULT_LANG
    if text in LANGS:
        return text if text != "zh-hans" else DEFAULT_LANG
    if text in _ALIASES:
        return _ALIASES[text]
    head = text.split("-")[0]
    if head in ("zh", "en"):
        # zh 但认不出具体形式：看有没有 hant/tw/hk 的线索，否则当简体
        return "zh-hant" if any(k in text for k in ("hant", "tw", "hk", "mo")) else (
            "en" if head == "en" else DEFAULT_LANG)
    return DEFAULT_LANG


@lru_cache(maxsize=8)
def table(lang: str) -> dict:
    """读一份字符串表；读不到就用空的（下面会回退到 zh-hans）。"""
    path = I18N_DIR / (normalize_lang(lang) + ".json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        warn("[i18n] 缺字符串表：" + str(path))
    except Exception as exc:
        warn("[i18n] 字符串表读不了（" + str(path) + "）：" + repr(exc))
    return {}


@lru_cache(maxsize=4096)
def _lookup(key: str, lang: str):
    """查一次表。返回 None 表示这个语言里没有。"""
    value = table(lang).get(key)
    return value if isinstance(value, str) else None


def t(key: str, lang: str = DEFAULT_LANG, **kw) -> str:
    """取一句话。缺键回退 zh-hans，再缺就原样返回键名（便于体检时发现）。"""
    lang = normalize_lang(lang)
    text = _lookup(key, lang)
    if text is None and lang != DEFAULT_LANG:
        # 只在非默认语言上回退：默认语言自己没有就是真缺了
        warn("[i18n] " + lang + " 缺这句话，回退简中：" + key)
        text = _lookup(key, DEFAULT_LANG)
    if text is None:
        warn("[i18n] 连简中都没有这句话：" + key)
        return key
    if not kw:
        return text
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError) as exc:
        warn("[i18n] " + key + " 的占位符对不上（" + repr(exc) + "），按原文返回")
        return text


def has(key: str, lang: str) -> bool:
    return _lookup(key, normalize_lang(lang)) is not None


def keys_of(lang: str) -> set:
    return set(table(lang).keys())


def reload_tables() -> None:
    """丢掉缓存，重新读盘（改完翻译不用重启服务）。"""
    table.cache_clear()
    _lookup.cache_clear()


@lru_cache(maxsize=1024)
def _lookup_list(key: str, lang: str):
    value = table(lang).get(key)
    return value if isinstance(value, list) else None


def t_list(key: str, lang: str = DEFAULT_LANG) -> list:
    """取一组句子（JSON 数组），比如天气提示那种一类下面十几条随机挑一条的。"""
    lang = normalize_lang(lang)
    value = _lookup_list(key, lang)
    if value is None and lang != DEFAULT_LANG:
        warn("[i18n] " + lang + " 缺这组句子，回退简中：" + key)
        value = _lookup_list(key, DEFAULT_LANG)
    if value is None:
        warn("[i18n] 连简中都没有这组句子：" + key)
        return []
    return [str(item) for item in value]
