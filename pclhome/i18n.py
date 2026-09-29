# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .log import warn

DEFAULT_LANG = "zh-hans"
LANGS = ("zh-hans", "zh-hant", "en")
LANG_NAMES = {"zh-hans": "简体中文", "zh-hant": "繁體中文", "en": "English"}

I18N_DIR = Path(__file__).resolve().parent / "i18n"

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
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return DEFAULT_LANG
    if text in LANGS:
        return text if text != "zh-hans" else DEFAULT_LANG
    if text in _ALIASES:
        return _ALIASES[text]
    head = text.split("-")[0]
    if head in ("zh", "en"):
        return "zh-hant" if any(k in text for k in ("hant", "tw", "hk", "mo")) else (
            "en" if head == "en" else DEFAULT_LANG)
    return DEFAULT_LANG


@lru_cache(maxsize=8)
def table(lang: str) -> dict:
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
    value = table(lang).get(key)
    return value if isinstance(value, str) else None


def t(key: str, /, lang: str = DEFAULT_LANG, **kw) -> str:

    lang = normalize_lang(lang)
    text = _lookup(key, lang)
    if text is None and lang != DEFAULT_LANG:
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
