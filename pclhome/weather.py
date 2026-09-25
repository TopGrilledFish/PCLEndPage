# -*- coding: utf-8 -*-
"""实时天气，数据源 uapis.cn ``/api/v1/misc/weather``。

**这个接口只能按"调用方 IP"定位**——而调用方永远是服务器，所以直接裸调用拿到的是
**服务器所在地**的天气。它没有 ``?ip=`` 参数，伪造 ``X-Forwarded-For`` 之类的头也没用
（实测七种头全都不认，详见 geo.py 的说明）。

于是取天气分三步：

    1. 访问者 IP → 城市（``geo.locate``，带 24 小时缓存）
    2. ``?adcode=`` 查（行政区码没有歧义，优先级最高）；退一步 ``?city=``
    3. 前两步都没结果 → 裸调用，由接口按服务器 IP 自己定位

所以每个访客看到的是**自己城市的天气**。想钉死一个城市就设 ``weather_city``；
想把 IP 查询整个关掉（比如服务器在国外、访客大多也在国外）就设 ``enable_geo: false``。

结果按"城市"缓存（默认 1 小时），同一个城市的所有访客共用一份——既省外呼
（接口有 4 QPS 上限），也不会因为访客多就把接口打爆。
"""
from __future__ import annotations

import random
import threading
import time
from urllib.parse import urlencode

from .config import Config
from .geo import is_unlocatable, locate
from .log import debug, out, warn
from .net import fetch_json_ex
from .xaml import escape_attr

_TIPS_HOT = ["注意防暑，别中暑了", "天太热，记得多补水", "高温下挖矿，记得带水桶", "大热天适合在家吹风扇"]
_TIPS_COLD = ["注意保暖，别冻坏了", "天冷，多穿点再出门", "低温下带好食物和火把", "天寒地冻，适合在家烤火"]
_TIPS_THUNDER = ["雷雨天别站高处，小心被雷劈", "雷雨天气，适合在家研究红石", "打雷了，快进屋躲躲", "雷雨交加，别带金属装备"]
_TIPS_STORM = ["雨太大了，在家整理仓库吧", "暴雨天别出门，小心被冲走", "下大雨，适合在家做附魔", "大雨倾盆，在家烤火喝茶"]
_TIPS_SNOW = ["下雪了，适合堆雪人", "雪天适合在家烤面包", "雪天出门记得带火把", "银装素裹，适合出门看雪景", "雪地走路小心滑倒"]
_TIPS_RAIN = ["雨天适合在家建房子", "雨天适合整理箱子", "雨天适合研究红石", "毛毛雨，适合去钓鱼", "雨天撑伞去跑图也不错", "细雨绵绵，适合在家种田"]
_TIPS_FOG = ["雾大，别跑太远", "雾天适合在家研究药水", "大雾弥漫看不清路，注意安全", "雾天出门记得带指南针"]
_TIPS_CLEAR = ["适合出门挖矿", "适合探索新洞穴", "适合下矿寻宝", "适合扩建你的基地", "适合去钓鱼种田", "适合出门跑图探险",
               "适合挑战末影龙", "适合开荒新区域", "适合修一座红石机关", "适合去林地府邸探险", "适合驯一匹新马",
               "适合造一艘船去远航", "适合带上藏宝图去寻宝", "适合去打一次凋灵试试"]


def classify_weather(text: str | None) -> str | None:
    """把中文天气描述归一成 clear / cloudy / rain / snow / thunder / fog。"""
    desc = str(text or "")
    for keywords, kind in (("雷雹电", "thunder"), ("雪凇霜冰粒", "snow"), ("雨", "rain"),
                           ("雾霾沙尘", "fog"), ("阴云", "cloudy"), ("晴朗", "clear")):
        if any(ch in desc for ch in keywords):
            return kind
    return None


def _pick_tip(temp: int, desc: str) -> str:
    if temp >= 30:
        return random.choice(_TIPS_HOT)
    if temp <= 0:
        return random.choice(_TIPS_COLD)
    if "雷" in desc or "雹" in desc:
        return random.choice(_TIPS_THUNDER)
    if any(ch in desc for ch in "大暴强"):
        return random.choice(_TIPS_STORM)
    if "雪" in desc or "凇" in desc:
        return random.choice(_TIPS_SNOW)
    if "雨" in desc:
        return random.choice(_TIPS_RAIN)
    if "雾" in desc or "霾" in desc:
        return random.choice(_TIPS_FOG)
    return random.choice(_TIPS_CLEAR)


def build_weather_xaml(location: str, temp, desc: str, wind: str, humidity) -> str:
    """天气卡片：温度为主，天气/位置次之，分隔线，风力与湿度，一句应景建议。"""
    detail = escape_attr(wind or "")
    if humidity not in (None, ""):
        detail += (" · " if detail else "") + "湿度 " + str(humidity) + "%"
    tip = _pick_tip(int(temp), str(desc))

    return ('<Border CornerRadius="10" Padding="16,16" Margin="0,0,0,0" Background="{DynamicResource ColorBrush7}">'
            "<StackPanel>"
            '<StackPanel Orientation="Horizontal" HorizontalAlignment="Center">'
            '<TextBlock Text="' + str(temp) + '°" FontSize="36" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" />'
            '<TextBlock Text="' + escape_attr(desc) + '" FontSize="15" VerticalAlignment="Bottom" '
            'Foreground="{DynamicResource ColorBrush3}" Margin="8,0,0,8" />'
            "</StackPanel>"
            '<TextBlock Text="' + escape_attr(location) + '" FontSize="11" HorizontalAlignment="Center" '
            'Foreground="{DynamicResource ColorBrush3}" Margin="0,2,0,0" />'
            '<Border Height="1" Margin="0,12,0,12">'
            '<Border.Background><LinearGradientBrush StartPoint="0,0" EndPoint="1,0">'
            '<GradientStop Color="#00000000" Offset="0" />'
            '<GradientStop Color="#33808080" Offset="0.5" />'
            '<GradientStop Color="#00000000" Offset="1" />'
            "</LinearGradientBrush></Border.Background></Border>"
            '<TextBlock Text="' + detail + '" FontSize="11" HorizontalAlignment="Center" '
            'Foreground="{DynamicResource ColorBrush3}" />'
            '<TextBlock Text="' + escape_attr(tip) + '" FontSize="12" HorizontalAlignment="Center" '
            'Foreground="{DynamicResource ColorBrush1}" Margin="0,4,0,0" TextWrapping="Wrap" TextAlignment="Center" />'
            "</StackPanel>"
            "</Border>")


def build_weather_unavailable(reason: str = "") -> str:
    text = "天气获取失败，请稍后刷新重试。"
    if reason:
        text = "天气获取失败（" + reason + "）。"
    return '<local:MyHint Theme="Yellow" Margin="0,0,0,0" Text="' + escape_attr(text) + '" />'


class WeatherService:
    """带缓存的天气服务。"""

    def __init__(self, config: Config):
        self.config = config
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, ip: str) -> dict:
        """返回 ``{"body": XAML, "kind": 天气类别, "source": 简述}``；永不抛异常。"""
        if not self.config.enable_weather:
            return {"body": build_weather_unavailable("已关闭天气"), "kind": None, "source": "关闭"}

        candidates, cache_key, who = self._targets(ip)

        now = time.time()
        with self._lock:
            hit = self._cache.get(cache_key)
        if hit and now - hit[0] < self.config.weather_cache_seconds:
            debug("[Weather] 命中缓存（" + cache_key + "）")
            return hit[1]

        result = self._fetch(candidates, who)
        if result["kind"] is not None:
            with self._lock:
                self._cache[cache_key] = (now, result)
        return result

    def _targets(self, ip: str) -> tuple[list[dict], str, str]:
        """定出这次要按哪些参数去查、用哪个缓存键、日志里怎么称呼。

        返回 ``(候选查询参数, 缓存键, 位置描述)``。候选按优先级排列，前一个查不出
        结果就用下一个，最后兜底是"不带参数"（由接口按服务器 IP 定位）。
        """
        fixed = self.config.weather_city.strip()
        if fixed:
            return [{"city": fixed}], "city:" + fixed, "固定城市 " + fixed

        geo = locate(ip, timeout=self.config.geo_timeout,
                     cache_hours=self.config.geo_cache_hours) if self.config.enable_geo else None
        if geo:
            candidates = []
            if geo.get("adcode"):
                candidates.append({"adcode": geo["adcode"]})
            candidates.append({"city": geo["city"]})
            candidates.append({})                      # 都查不到就退回服务器定位
            key = ("adcode:" + geo["adcode"]) if geo.get("adcode") else ("city:" + geo["city"])
            return candidates, key, geo["city"] + "（按访问者 IP " + ip + "）"

        # 内网 IP、或所有地理源都挂了：只能按服务器所在地显示
        if not ip or ip == "unknown":
            reason = "地址未知"
        elif is_unlocatable(ip):
            reason = "内网地址"
        else:
            reason = "归属地查不到"
        return [{}], "__server__", "服务器本机（" + reason + "）"

    def _fetch(self, candidates: list[dict], who: str) -> dict:
        """按候选参数依次查；返回第一个成功的结果。"""
        started = time.perf_counter()
        data, status = None, None
        for params in candidates:
            url = self.config.weather_api + (("?" + urlencode(params)) if params else "")
            data, status = fetch_json_ex(url, timeout=self.config.http_timeout,
                                         retries=self.config.max_retries)
            if data and data.get("temperature") is not None:
                break
            if len(candidates) > 1:
                debug("[Weather] " + (urlencode(params) or "裸调用") + " 没查到，试下一个参数")
        elapsed = (time.perf_counter() - started) * 1000

        if not data or data.get("temperature") is None:
            if status == 404:
                warn("[Weather] 接口定位不到（" + who + "），耗时 " + str(round(elapsed)) + "ms。"
                     "服务器若在境外，可设 weather_city 固定一个城市，或设 enable_geo: false")
                return {"body": build_weather_unavailable("接口定位不到该城市"),
                        "kind": None, "source": "定位失败"}
            warn("[Weather] 取天气失败（" + who + "，" + str(round(elapsed)) + "ms，"
                 + ("HTTP " + str(status) if status else "无响应") + "）")
            return {"body": build_weather_unavailable(), "kind": None, "source": "失败"}

        # 位置只取"市 · 区县"：省份对同名城市没帮助（北京/上海等直辖市省市同名），
        # 拼上反而又长又重复（"北京市 北京"）。
        name = str(data.get("city") or who or "当前位置")
        district = str(data.get("district") or "")
        location = name + (" · " + district if district and district != name else "")

        wind = (str(data.get("wind_direction") or "") + " " + str(data.get("wind_power") or "")).strip()
        desc = str(data.get("weather") or "未知")
        # 国内城市返回整数，境外会带小数（14.8°），统一取整看着更整齐
        try:
            temperature = round(float(data["temperature"]))
        except (TypeError, ValueError):
            temperature = data["temperature"]

        out("[Weather] " + location + " " + str(temperature) + "° " + desc
            + "（" + who + "，" + str(round(elapsed)) + "ms）")
        return {
            "body": build_weather_xaml(location, temperature, desc, wind, data.get("humidity")),
            "kind": classify_weather(desc),
            "source": location,
        }
