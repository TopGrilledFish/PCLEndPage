# -*- coding: utf-8 -*-
"""构建期生成静态 XAML（对应上游 scripts/generate.py）。

跑一次，产出两个文件（都在项目根目录）：

    Custom.xaml           主页（{{}} 已填，__TOKEN__ 留待请求期替换）
    Custom.xaml.version   版本号占位（本地静态部署时用；动态服务会返回时间戳）

模板是唯一事实来源：页面结构只写在 pclhome/templates/*.tpl 里，
本模块只负责把生成期的动态值填进去。
"""
from __future__ import annotations

from pathlib import Path

from .log import out, warn
from .config import CACHE_DIR, PACK_FALLBACK_IMAGE, ROOT, TEMPLATES_DIR, Config, load_config
from .net import download, fetch_json
from .sites import build_site_items, mirror_icons
from .xaml import build_action_buttons, render_template


def load_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


# ============ 壁纸 ============

def fetch_bing_wallpaper(config: Config) -> str:
    """必应每日壁纸；取不到就用 PCL 内置图片兜底。"""
    if config.wallpaper_url:
        out("[Wallpaper] 使用配置指定的壁纸")
        return config.wallpaper_url
    if not config.enable_wallpaper:
        out("[Wallpaper] 已关闭必应壁纸，使用内置图片")
        return PACK_FALLBACK_IMAGE

    data = fetch_json("https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN",
                      timeout=config.http_timeout, retries=config.max_retries)
    images = (data or {}).get("images") or []
    if images:
        url = "https://www.bing.com" + images[0]["url"]
        out("[Wallpaper] 今日壁纸：" + url[:100] + "…")
        return url

    out("[Wallpaper] 必应接口不可用，使用内置图片：" + PACK_FALLBACK_IMAGE)
    return PACK_FALLBACK_IMAGE


def cache_wallpaper_locally(config: Config, url: str) -> str:
    """把壁纸缓存到本地并返回本地地址；失败则原样返回外链。

    PCL 拉取网络图片时有 7 天缓存，但外链一旦失效就会白屏。本机部署时
    把图存一份，页面就再也不依赖必应了。
    """
    if not url.startswith("http"):
        return url
    target = CACHE_DIR / "wallpaper.jpg"
    import time
    if target.exists() and (time.time() - target.stat().st_mtime) < config.wallpaper_cache_hours * 3600:
        out("[Wallpaper] 本地缓存仍新鲜，跳过下载")
        return config.resolved_base_url() + "/images/wallpaper.jpg" if config.base_url else url
    if download(url, target, timeout=20):
        out("[Wallpaper] 已缓存到 " + str(target.relative_to(ROOT)))
        if config.base_url:
            return config.resolved_base_url() + "/images/wallpaper.jpg"
    return url


# ============ 输出 ============

def build_custom_xaml(config: Config, offline: bool = False) -> str:
    wallpaper = PACK_FALLBACK_IMAGE if offline else fetch_bing_wallpaper(config)
    return render_template(load_template("Custom.xaml.tpl"), {
        "BASE_URL": config.resolved_base_url() or "__BASE_URL__",
        "WALLPAPER_URL": wallpaper,
        "SITE_ITEMS": build_site_items(config),
        "ACTION_BUTTONS": build_action_buttons(config),
    })


def generate(config: Config, offline: bool = False, out_dir: Path | None = None) -> dict:
    """生成主页产物，返回 ``{文件名: 路径}``。"""
    out_dir = out_dir or ROOT
    out_dir.mkdir(parents=True, exist_ok=True)

    # 先把各站图标镜像到本地（下不到就退回远程地址，见 sites.py）
    icons = mirror_icons(config, offline=offline)
    failed = [slug for slug, result in icons.items() if result in ("下载失败", "格式不被 WPF 支持（WebP/SVG/HTML 等）")]
    if failed:
        out("[Icons] 以下站点图标没能镜像到本地，将退回远程地址：" + "、".join(failed))
        out("[Icons] 想要稳定显示：把对应的 <slug>.png 放进 static/images/icons/（见该目录 README）")

    outputs = {
        "Custom.xaml": build_custom_xaml(config, offline=offline),
        "Custom.xaml.version": "0",
    }
    written = {}
    for name, content in outputs.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written[name] = path
        out("已生成：" + str(path) + "（" + str(path.stat().st_size) + " 字节）")
    return written


def main(argv: list[str] | None = None) -> int:
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    config = load_config()
    offline = "--offline" in argv
    if offline:
        out("[Generate] 离线模式：跳过必应壁纸等外部请求")
    generate(config, offline=offline)
    return 0
