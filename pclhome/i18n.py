# -*- coding: utf-8 -*-
"""界面文案的多语言查表。

语言包放 ``pclhome/i18n/<代码>.json``，一个语言一份，键集合要一致：

* ``zh-hans`` 简体中文（默认，也是唯一"手写"的那份，是母版）
* ``zh-hant`` 繁體中文（``tools/make_zh_hant.py`` 从简中生成）
* ``en``      英文

缺的键运行时回退到 ``en`` 再回退 ``zh-hans``（静默，不打日志），所以三份词条哪怕
一时没对齐也不会把页面顶出空白——真缺东西由 ``doctor`` 的键集合比对兜底。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .log import warn

DEFAULT_LANG = "zh-hans"
#: 有完整词条（745 个键）的三种语言；体检按这三个比键集合
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
    "jp": "ja", "kr": "ko", "cn": "zh-hans", "tw": "zh-hant", "hk": "zh-hant",
    "mo": "zh-hant", "ua": "uk", "il": "he", "no-nb": "no", "nb": "no",
    "nn": "no", "pt-br": "pt", "pt-pt": "pt", "es-mx": "es", "es-es": "es",
    "fr-fr": "fr", "de-de": "de", "it-it": "it", "ru-ru": "ru", "ja-jp": "ja",
    "ko-kr": "ko", "zh-cn-hans": "zh-hans", "in": "id", "iw": "he",
}


@lru_cache(maxsize=1)
def available_langs() -> tuple:
    """磁盘上真实存在的语言包（文件名即语言码），三语排最前面。"""
    found = []
    try:
        for path in sorted(I18N_DIR.glob("*.json")):
            code = path.stem
            if code and not code.startswith("_"):
                found.append(code)
    except OSError:
        pass
    head = [code for code in LANGS if code in found]
    rest = sorted(code for code in found if code not in LANGS)
    return tuple(head + rest)


def is_supported(code: str) -> bool:
    """这个语言码是不是真的能切（磁盘上有语言包）。"""
    return str(code or "") in available_langs()


def normalize_lang(value) -> str:
    """把各种写法归一到某个真实存在的语言码，认不出来就回默认。"""
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return DEFAULT_LANG
    if text in available_langs():
        return text
    if text in _ALIASES and is_supported(_ALIASES[text]):
        return _ALIASES[text]
    head = text.split("-")[0]
    if head in available_langs():
        return head
    if head in ("zh", "en"):
        # zh 但认不出具体形式：看有没有 hant/tw/hk 的线索，否则当简体
        return "zh-hant" if any(k in text for k in ("hant", "tw", "hk", "mo")) else (
            "en" if head == "en" else DEFAULT_LANG)
    return DEFAULT_LANG


@lru_cache(maxsize=8)
def table(lang: str) -> dict:
    """读一份字符串表；读不到就用空的（下面会回退）。"""
    path = I18N_DIR / (normalize_lang(lang) + ".json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        warn("[i18n] 缺字符串表：" + str(path))
    except Exception as exc:
        warn("[i18n] 字符串表读不了（" + str(path) + "）：" + repr(exc))
    return {}


def lang_name(lang: str) -> str:
    """一种语言的本地写法（"日本語" / "Deutsch"），用来在页面上称呼它。"""
    code = normalize_lang(lang)
    if code in LANG_NAMES:
        return LANG_NAMES[code]
    name = table(code).get("_name")
    return name if isinstance(name, str) and name else code


def lang_name_zh(lang: str) -> str:
    """中文名（语言清单里给中文用户看的那一列）。"""
    code = normalize_lang(lang)
    if code in LANG_NAMES:
        return LANG_NAMES[code]
    name = table(code).get("_name_zh")
    return name if isinstance(name, str) and name else lang_name(code)


def lang_name_en(lang: str) -> str:
    """英文名（英文页面上显示用，不能夹汉字）。"""
    code = normalize_lang(lang)
    if code == "en":
        return "English"
    if code in LANG_NAMES:
        return {"zh-hans": "Chinese (Simplified)", "zh-hant": "Chinese (Traditional)"}[code]
    name = table(code).get("_name_en")
    return name if isinstance(name, str) and name else lang_name(code)


@lru_cache(maxsize=8192)
def _lookup(key: str, lang: str):
    """查一次表。返回 None 表示这个语言里没有。"""
    value = table(lang).get(key)
    return value if isinstance(value, str) else None


def t(key: str, /, lang: str = DEFAULT_LANG, **kw) -> str:
    """取一句话。缺键按 「目标语言 → 英文 → 简中」回退，都没有就原样返回键名。

    ``key`` 前面那个 ``/`` 是**参数位限定**：第一个参数只能按位置传。因为它是按
    关键字传的，``t("ai.key_saved", lang, key=...)`` 会撞成
    "got multiple values for argument 'key'"——占位符叫 ``{key}`` 的句子正好会这么调，
    一撞就是个 TypeError，页面被兜成"服务器正在更新"。限定成位置参数之后，
    ``key`` 这个名字在 ``**kw`` 里就空出来了。
    """
    lang = normalize_lang(lang)
    text = _lookup(key, lang)
    if text is None and lang != DEFAULT_LANG:
        # 七十多种语言每次都有几十个键缺失，回退是常态，不打日志
        if lang != "en":
            text = _lookup(key, "en")
        if text is None:
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
    available_langs.cache_clear()


@lru_cache(maxsize=1024)
def _lookup_list(key: str, lang: str):
    value = table(lang).get(key)
    return value if isinstance(value, list) else None


def t_list(key: str, lang: str = DEFAULT_LANG) -> list:
    """取一组句子（JSON 数组），比如天气提示那种一类下面十几条随机挑一条的。"""
    lang = normalize_lang(lang)
    value = _lookup_list(key, lang)
    if value is None and lang != DEFAULT_LANG:
        if lang != "en":
            value = _lookup_list(key, "en")
        if value is None:
            value = _lookup_list(key, DEFAULT_LANG)
    if value is None:
        warn("[i18n] 连简中都没有这组句子：" + key)
        return []
    return [str(item) for item in value]
