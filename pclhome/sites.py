# -*- coding: utf-8 -*-
"""首页"功能网站"卡片：图标镜像 + 列表项渲染。

图标为什么要镜像到本地：

* PCL 拉的是**远程**图片，而不少站点对图标做了防盗链（CurseForge 不带 Referer 直接
  403）、或者只提供 WebP（MineBBS），而 WPF 解不了 WebP；Cloudflare 还会按 UA 拦掉
  非浏览器请求（Minecraft Wiki 403）。
* 镜像一次之后页面用的是我们自己服务器上的文件，没有防盗链、没有格式坑、还能长期缓存。

构建期尽力而为：下得到就用本地，下不到就退回远程地址；仍然不行时 PCL 会显示它内置的
"无图标"占位，不会破版。要 100% 可控，把 ``<slug>.png`` 放进 ``static/images/icons/``，
详见该目录的 README。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from .log import out, warn
from .config import BROWSER_UA, ICONS_DIR, PACK_FALLBACK_IMAGE, Config, site_slug
from .net import download
from .xaml import indent_block, render_template

ITEM_TEMPLATE = ('<local:MyListItem Margin="-5,0,-5,4" Type="Clickable" Logo="{{LOGO|escape}}" '
                 'Title="{{TITLE|escape}}" Info="{{INFO|escape}}" '
                 'EventType="打开网页" EventData="{{URL|escape}}" />')

ICON_EXTENSIONS = (".png", ".ico", ".jpg", ".jpeg")

# 下载后按魔数校验：只接受 WPF 能解码的格式
_MAGIC = (
    (b"\x00\x00\x01\x00", "ico"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
)


def _detect(data: bytes) -> str | None:
    for magic, kind in _MAGIC:
        if data.startswith(magic):
            return kind
    return None


def local_icon(config: Config, slug: str) -> Path | None:
    """本地已有的图标文件（用户在 static/images/icons/ 里放的那个，或构建期下载的）。"""
    for ext in ICON_EXTENSIONS:
        path = ICONS_DIR / (slug + ext)
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def mirror_icons(config: Config, offline: bool = False) -> dict[str, str]:
    """把各站图标下载到 local。返回 ``{slug: "本地文件名" | "失败原因"}``。"""
    report: dict[str, str] = {}
    for site in config.sites:
        slug = site_slug(site)
        existing = local_icon(config, slug)
        if existing:
            report[slug] = existing.name
            continue

        icon_url = str(site.get("icon") or "")
        if offline or not icon_url.startswith("http"):
            report[slug] = "跳过"
            continue

        origin = "{0.scheme}://{0.netloc}/".format(urlsplit(site.get("url", "")))
        target = ICONS_DIR / (slug + ".ico")       # 占位名，拿到内容后再定扩展名
        if not download(icon_url, target, timeout=config.http_timeout,
                        headers={"User-Agent": BROWSER_UA, "Referer": origin}):
            report[slug] = "下载失败"
            continue

        payload = target.read_bytes()
        kind = _detect(payload)
        if kind is None:
            target.unlink(missing_ok=True)
            report[slug] = "格式不被 WPF 支持（WebP/SVG/HTML 等）"
            continue

        final = ICONS_DIR / (slug + "." + kind)
        if final != target:
            target.replace(final)
        report[slug] = final.name
        out("[Icons] " + slug + " → " + final.name + "（" + str(len(payload)) + " 字节）")
    return report


def icon_url(config: Config, site: dict) -> str:
    """该站图标最终写进 XAML 的地址：本地镜像优先，其次远程，最后 PCL 内置图。"""
    path = local_icon(config, site_slug(site))
    if path:
        return config.resolved_base_url() + "/images/icons/" + path.name
    if site.get("icon"):
        return str(site["icon"])
    return PACK_FALLBACK_IMAGE


def build_site_items(config: Config) -> str:
    """渲染"功能网站"的列表项（构建期调用）。"""
    base = config.resolved_base_url() or "__BASE_URL__"
    blocks = []
    for site in config.sites:
        logo = icon_url(config, site)
        if logo.startswith("/"):
            # 构建期还不知道最终域名，留 __BASE_URL__ 交给请求期替换
            logo = base + logo
        blocks.append(render_template(ITEM_TEMPLATE, {
            "LOGO": logo,
            "TITLE": site.get("name", ""),
            "INFO": site.get("info", ""),
            "URL": site.get("url", ""),
        }))
    return indent_block("\n".join(blocks), 12)
