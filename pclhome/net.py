# -*- coding: utf-8 -*-
"""带超时与重试的 HTTP 取数工具（只用标准库）。

上游跑在 Cloudflare Workers 上有平台级 fetch；复刻版跑在普通 Python 进程里，
所有外部请求都必须显式限时，否则一个挂住的上游会把工作线程拖死。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .log import out, warn
from .config import USER_AGENT


def fetch_text(url: str, timeout: float = 6.0, retries: int = 1,
               headers: dict | None = None, encoding: str = "utf-8") -> str | None:
    """取文本，失败返回 None（调用方走兜底，不抛异常）。"""
    last_error = None
    for attempt in range(max(1, retries)):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return resp.read().decode(encoding, errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(0.6)
    warn("[Net] 请求失败 " + url[:120] + "：" + str(last_error))
    return None


def fetch_json(url: str, timeout: float = 6.0, retries: int = 1, headers: dict | None = None):
    text = fetch_text(url, timeout=timeout, retries=retries, headers=headers)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception as exc:
        warn("[Net] JSON 解析失败 " + url[:120] + "：" + str(exc))
        return None


def fetch_json_ex(url: str, timeout: float = 6.0, retries: int = 1,
                  headers: dict | None = None):
    """返回 ``(数据, HTTP 状态码)``。

    状态码用于区分两种失败：接口明确说"没有"（如 404 定位不到），
    还是网络层面根本没通（状态码为 None）。两者给用户的提示完全不同。
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace")), resp.status
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:120]
        except Exception:
            pass
        warn("[Net] " + url[:100] + " → HTTP " + str(exc.code) + " " + body)
        return None, exc.code
    except Exception as exc:
        if retries > 1:
            time.sleep(0.6)
            return fetch_json_ex(url, timeout=timeout, retries=retries - 1, headers=headers)
        warn("[Net] 请求失败 " + url[:100] + "：" + str(exc))
        return None, None


def download(url: str, target: Path, timeout: float = 15.0, headers: dict | None = None) -> bool:
    """下载文件到 target（先写临时文件再改名，避免半截文件）。"""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = resp.read()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
        out("[Net] 已下载 " + target.name + "（" + str(len(payload)) + " 字节）")
        return True
    except Exception as exc:
        warn("[Net] 下载失败 " + url[:120] + "：" + str(exc))
        return False
