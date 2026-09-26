# -*- coding: utf-8 -*-
"""全局配置。

配置来源优先级（后者覆盖前者）：
    1. 本文件的默认值
    2. 项目根目录的 config.json
    3. 环境变量（PCLHOME_ 前缀，见 ENV_MAP）

上游把 BASE_URL、服务器列表这类值写死在脚本里；复刻版把它们抽成配置，
这样同一份代码既能本机跑（自动按 Host 推导地址），也能部署后固化域名。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = ROOT / "static"
ICONS_DIR = STATIC_DIR / "images" / "icons"     # 网站图标本地镜像
VAR_DIR = ROOT / "var"                          # 运行时数据：配置存储、访问统计、缓存
CACHE_DIR = VAR_DIR / "cache"                   # 壁纸等下载缓存

CONFIG_FILE = ROOT / "config.json"

USER_AGENT = "PCL-Homepage-Py/1.0 (+https://github.com/wlasfjdskfj/pcl-homepage)"

# 浏览器 UA：抓第三方图标时用，很多站点会拦非浏览器 UA
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 必应每日壁纸
BING_API = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN"

# 一言：mode=daily 表示"一整天返回同一句"，正好满足"所有人看到的都一样"
SAYING_API = "https://uapis.cn/api/v1/saying/random"

# 一言语料：接口默认会在中英文之间随机，会蹦出英文名言。
# caoxingyu-sentence 是接口里的中文语料（句子 + 古诗文），首页是中文界面，默认用它。
# 改成空字符串则不限语料，想换其它语料填对应的 slug。
SAYING_SOURCE = "caoxingyu-sentence"

# 天气：接口只能按"调用方 IP"定位，而调用方永远是服务器，所以复刻版先用访问者 IP
# 查城市（见 geo.py），再带上 ?adcode= / ?city= 去查，这样每个访客看到自己城市的天气
WEATHER_API = "https://uapis.cn/api/v1/misc/weather"

# 横幅日期下面那行农历（中文月日 + 当天节气）
LUNAR_API = "https://uapis.cn/api/v1/misc/lunartime"

# AI 日志分析：OpenAI 兼容接口，默认 DeepSeek 官方。
# 站长密钥（ai_api_key）每 IP 每天限 ai_daily_limit 次；访客也可以填自己的密钥绕过限制。
AI_API_BASE = "https://api.deepseek.com/v1"
# V4 Flash。**别用 deepseek-v4-pro**：同一个问题它思考量是 flash 的三倍多
# （实测 567 token vs 182），费用也高三倍左右。
# 用 deepseek-flash 这个写法：它是 /models 接口实际列出来的名字，
# deepseek-v4-flash 也指向同一个模型，但没在列表里，哪天被摘掉不好说。
AI_MODEL = "deepseek-flash"

# PCL 内置资源：联网图取不到时用它兜底（path 形式，PCL 自己会解析）
PACK_FALLBACK_IMAGE = "pack://application:,,,/images/Blocks/GrassPath.png"

# 首页"功能网站"卡片。icon 为各站图标地址：
#   构建期会尽力把它镜像到 static/images/icons/<slug>.<ext>，成功后页面用本地地址。
#   icon 直连拿不到时（Cloudflare 拦 UA、防盗链、只给 WebP）再试 icon_fallback——
#   一个 favicon 代理服务，它用自己的 IP 和请求头去取，能绕开上述三种情况。
#   两条都失败才退回远程地址（PCL 自己下载，失败会显示 PCL 内置的"无图标"占位）。
# 想换成自己的图标：把 <slug>.png 丢进 static/images/icons/ 即可，详见该目录的 README。

# favicon 代理：只在直连失败时用。不想依赖第三方服务就把它清空。
ICON_PROXY = "https://api.xinac.net/icon/?url={domain}"

DEFAULT_SITES = [
    {"name": "必应搜索", "url": "https://www.bing.com/",
     "info": "微软的搜索引擎", "icon": "https://www.bing.com/favicon.ico",
     "icon_fallback": ICON_PROXY.format(domain="bing.com")},
    {"name": "谷歌搜索", "url": "https://www.google.com/",
     "info": "搜索引擎，大陆不可用", "icon": "https://www.google.com/favicon.ico",
     "icon_fallback": ICON_PROXY.format(domain="google.com")},
    {"name": "Minecraft Wiki", "url": "https://zh.minecraft.wiki/",
     "info": "查阅方块、生物与游戏机制", "icon": "https://zh.minecraft.wiki/favicon.ico", "icon_fallback": ICON_PROXY.format(domain="zh.minecraft.wiki")},
    {"name": "MC 百科", "url": "https://www.mcmod.cn/",
     "info": "最大的 Minecraft 中文 MOD 百科", "icon": "https://www.mcmod.cn/images/favicon.ico", "icon_fallback": ICON_PROXY.format(domain="www.mcmod.cn")},
    {"name": "Modrinth", "url": "https://modrinth.com/",
     "info": "下载模组、整合包与资源包", "icon": "https://modrinth.com/favicon-light-32x32.png", "icon_fallback": ICON_PROXY.format(domain="modrinth.com")},
    {"name": "CurseForge", "url": "https://www.curseforge.com/minecraft",
     "info": "CurseForge 的 Minecraft 专区", "icon": "https://www.curseforge.com/favicon.ico", "icon_fallback": ICON_PROXY.format(domain="www.curseforge.com")},
    {"name": "NameMC", "url": "https://namemc.com/",
     "info": "查询 Minecraft 皮肤与用户名", "icon": "https://namemc.com/favicon.ico", "icon_fallback": ICON_PROXY.format(domain="namemc.com")},
    {"name": "Planet Minecraft", "url": "https://www.planetminecraft.com/",
     "info": "老牌作品站，地图皮肤与建筑", "icon": "https://www.planetminecraft.com/favicon.ico", "icon_fallback": ICON_PROXY.format(domain="planetminecraft.com")},
    {"name": "mclo.gs", "url": "https://mclo.gs/",
     "info": "分享与解析 Minecraft 崩溃日志", "icon": "https://mclo.gs/img/favicon.ico", "icon_fallback": ICON_PROXY.format(domain="mclo.gs")},
    {"name": "MineBBS", "url": "https://www.minebbs.com/",
     "info": "Minecraft 中文资源与交流社区", "icon": "https://www.minebbs.com/data/assets/logo/favicon.webp",
     "icon_fallback": ICON_PROXY.format(domain="minebbs.com")},
    {"name": "苦力怕论坛", "url": "https://www.klpbbs.com/",
     "info": "国内 MC 论坛，基岩版资源多", "icon": "https://www.klpbbs.com/favicon.ico", "icon_fallback": ICON_PROXY.format(domain="klpbbs.com")},
    {"name": "mcbbs 纪念版", "url": "https://www.mcbbs.co/",
     "info": "老 MCBBS 的纪念站", "icon": "https://www.mcbbs.co/favicon.ico",
     "icon_fallback": ICON_PROXY.format(domain="mcbbs.co")},
    # forum.mczwlt.net 挂了 WAFPRO 的 JS 挑战，直连只会拿到一段脚本，favicon 抓不到；
    # 根域名 mczwlt.net 是同一个站（标题「资源 · 红石中继站」），拿它的。
    {"name": "红石中继站", "url": "https://forum.mczwlt.net/",
     "info": "红石与生电技术社区", "icon": "https://mczwlt.net/favicon.ico",
     "icon_fallback": ICON_PROXY.format(domain="forum.mczwlt.net")},
]

ENV_MAP = {
    "PCLHOME_BASE_URL": ("base_url", str),
    "PCLHOME_ADMIN_TOKEN": ("admin_token", str),
    "PCLHOME_HOST": ("host", str),
    "PCLHOME_PORT": ("port", int),
    "PCLHOME_WEATHER": ("enable_weather", lambda v: v not in ("0", "false", "no")),
    "PCLHOME_WEATHER_CITY": ("weather_city", str),
    "PCLHOME_GEO": ("enable_geo", lambda v: v not in ("0", "false", "no")),
    "PCLHOME_AI": ("enable_ai", lambda v: v not in ("0", "false", "no")),
    "PCLHOME_AI_KEY": ("ai_api_key", str),
    "PCLHOME_AI_BASE": ("ai_api_base", str),
    "PCLHOME_AI_MODEL": ("ai_model", str),
    "PCLHOME_AI_LIMIT": ("ai_daily_limit", int),
    "PCLHOME_WALLPAPER": ("enable_wallpaper", lambda v: v not in ("0", "false", "no")),
    "PCLHOME_SAYING": ("enable_saying", lambda v: v not in ("0", "false", "no")),
    "PCLHOME_LUNAR": ("enable_lunar", lambda v: v not in ("0", "false", "no")),
    "PCLHOME_TRUST_PROXY": ("trust_proxy_headers", lambda v: v not in ("0", "false", "no")),
    "PCLHOME_GUARD": ("guard_clients", lambda v: v not in ("0", "false", "no")),
}


@dataclass
class Config:
    # ---- 站点 ----
    base_url: str = ""              # 空 = 按请求的 Host 头推导（本机开发最省事）
    site_name: str = "PCL2 个性化主页"
    feedback_url: str = "https://github.com/wlasfjdskfj/pcl-homepage/issues"
    source_url: str = "https://github.com/wlasfjdskfj/pcl-homepage"

    # ---- 服务 ----
    host: str = "127.0.0.1"
    port: int = 8787
    admin_token: str = "admin"      # 部署前务必改掉
    trust_proxy_headers: bool = True

    # ---- 首页内容 ----
    sites: list = field(default_factory=lambda: [dict(s) for s in DEFAULT_SITES])

    # ---- 外部数据源开关（离线也能跑）----
    enable_wallpaper: bool = True    # 必应每日壁纸
    wallpaper_url: str = ""          # 手动指定横幅大图；留空则构建期取必应每日壁纸
    enable_saying: bool = True       # 每日一言走 uapis.cn
    saying_source: str = SAYING_SOURCE   # 一言语料 slug；留空 = 不限（会出英文）
    enable_lunar: bool = True        # 日期下面的农历行走 uapis.cn（失败退回本地换算）
    lunar_api: str = LUNAR_API
    enable_weather: bool = True      # 实时天气走 uapis.cn
    weather_city: str = ""           # 强制天气城市，填了就所有人都看这个城市
    enable_geo: bool = True          # 留空 weather_city 时，按访问者 IP 查城市（见 geo.py）
    sayings_api: str = SAYING_API
    weather_api: str = WEATHER_API
    http_timeout: float = 8.0
    geo_timeout: float = 4.0         # 查 IP 归属地的超时（在请求关键路径上，别设大）
    geo_cache_hours: int = 24        # IP → 城市 的缓存时长

    # ---- AI 日志分析（OpenAI 兼容接口）----
    enable_ai: bool = False          # 默认关：得先配密钥才开
    ai_api_base: str = AI_API_BASE   # 接口地址，末尾不要带 /
    ai_api_key: str = ""             # 站长内置密钥
    ai_model: str = AI_MODEL
    ai_daily_limit: int = 1          # 每个 IP 每天能用几次内置密钥
    ai_free_ips: list = field(default_factory=list)   # 这些 IP 用内置密钥不计数
    ai_max_log_chars: int = 40000    # 送给 AI 的日志上限（超长掐中间）
    # 回复长度上限。**不能给小**：deepseek-flash / v4-pro 都是推理模型，
    # 先花 token 思考再写答案，上限太小的话 token 全耗在思考上，答案就是空的。
    # 实测一条崩溃日志要 ~700 思考 + ~500 答案，这里留足余量。
    ai_max_tokens: int = 4000
    ai_timeout: float = 120.0        # AI 调用超时，比普通接口长得多
    ai_refund_on_failure: bool = True  # 抓日志/AI 调用失败时退还当天次数
    # 结果页脚里点名的模型（公用密钥那一档）。改成厂商的宣传名即可。
    ai_display_name: str = "DeepSeek V4 Flash"
    max_retries: int = 2

    # ---- 缓存 ----
    wallpaper_cache_hours: int = 6
    weather_cache_seconds: int = 3600
    saying_cache_hours: int = 24     # 每日一言按天缓存，全站共用一句

    # ---- 来源守卫：上游只放行 PCL 客户端与真实浏览器 ----
    guard_clients: bool = True

    def resolved_base_url(self, origin: str = "") -> str:
        """构建期写死优先；没配就用请求方推导出的 origin。"""
        return (self.base_url or origin).rstrip("/")


def site_slug(site: dict) -> str:
    """站点标识：由域名生成（www. 去掉、点换横线），用于图标文件名与配置键。"""
    from urllib.parse import urlsplit

    host = urlsplit(site.get("url", "")).netloc.lower() or site.get("name", "site")
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    slug = "".join(ch if ch.isalnum() else "-" for ch in host).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "site"


def _coerce(raw, caster):
    if caster is str:
        return str(raw)
    if caster is int:
        return int(raw)
    return caster(raw)


def load_config(path: Path | None = None) -> Config:
    """读取 config.json + 环境变量，返回配置对象。"""
    cfg = Config()
    path = path or CONFIG_FILE

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:                     # 配置写坏时不能拖垮主页
            print("[Config] " + str(path) + " 解析失败，使用默认配置：" + str(exc))
            data = {}
        fields = set(Config.__dataclass_fields__)
        kwargs = {}
        for key, value in data.items():
            if key.startswith("_"):
                continue
            if key in fields:
                kwargs[key] = value
            else:
                print("[Config] 忽略未知配置项：" + key)
        cfg = replace(cfg, **kwargs)

    for env_name, (field_name, caster) in ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is not None and raw != "":
            try:
                cfg = replace(cfg, **{field_name: _coerce(raw, caster)})
            except Exception as exc:
                print("[Config] 环境变量 " + env_name + " 无效：" + str(exc))

    for folder in (VAR_DIR, CACHE_DIR, ICONS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    return cfg
