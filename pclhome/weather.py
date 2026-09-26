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
import re
import threading
import time
from urllib.parse import urlencode

from .config import Config
from .geo import is_unlocatable, locate
from .i18n import DEFAULT_LANG, has, t, t_list
from .log import debug, out, warn
from .net import fetch_json_ex
from .xaml import escape_attr

# 提示语各语种放在 i18n 表里（``weather.tips.<类别>``），zh-hans 是母版。
# 以前这儿写死了一组中文字面量，切到英文时提示语还是中文——现在没有了。
_TIP_KINDS = ("hot", "cold", "thunder", "storm", "snow", "rain", "fog", "clear")


def classify_weather(text: str | None) -> str | None:
    """把中文天气描述归一成 clear / cloudy / rain / snow / thunder / fog。"""
    desc = str(text or "")
    for keywords, kind in (("雷雹电", "thunder"), ("雪凇霜冰粒", "snow"), ("雨", "rain"),
                           ("雾霾沙尘", "fog"), ("阴云", "cloudy"), ("晴朗", "clear")):
        if any(ch in desc for ch in keywords):
            return kind
    return None


def _pick_tip(temp: int, desc: str, lang: str) -> str:
    """按气温与天气挑一句应景建议。接口给的中文描述永远用来判断类别，
    只有**显示**的那句话跟着语言走。"""
    if temp >= 30:
        kind = "hot"
    elif temp <= 0:
        kind = "cold"
    elif "雷" in desc or "雹" in desc:
        kind = "thunder"
    elif any(ch in desc for ch in "大暴强"):
        kind = "storm"
    elif "雪" in desc or "凇" in desc:
        kind = "snow"
    elif "雨" in desc:
        kind = "rain"
    elif "雾" in desc or "霾" in desc:
        kind = "fog"
    else:
        kind = "clear"
    pool = t_list("weather.tips." + kind, lang)
    return random.choice(pool) if pool else ""


def weather_desc(desc: str, lang: str) -> str:
    """接口给的中文天气词按语言显示。

    接口的天气描述是个长尾（「局部多云」「雷阵雨伴有冰雹」…），逐条收录收不全。
    所以退一步：认不出原词就归到大类（晴/多云/雨/雪/雷/雾）说个笼统的，
    英文页面上宁可说「Cloudy」也不要蹦出一句「局部多云」。
    """
    text = str(desc or "")
    key = "weather.desc." + text
    if has(key, lang):
        return t(key, lang)
    kind = classify_weather(text)
    if kind and has("weather.kind." + kind, lang):
        return t("weather.kind." + kind, lang)
    return text


# 风向：接口给的是「东南风」这种中文，显示前按语言换。
# 两字的先判，否则「东南风」会被「东」抢先匹配成东风。
_WIND_DIRS = (("东南", "se"), ("西北", "nw"), ("东北", "ne"), ("西南", "sw"),
              ("东", "e"), ("南", "s"), ("西", "w"), ("北", "n"))
_WIND_LEVEL_RE = re.compile(r"\d+(?:\s*-\s*\d+)?")


def weather_wind(direction: str, power: str, lang: str) -> str:
    """风力那一段，如「东南风 3级」/「SE wind force 3」。

    认不出来的写法（「无持续风向」之类）原样留着，宁可显示中文也不显示空。
    """
    text = str(direction or "").strip()
    code = next((code for prefix, code in _WIND_DIRS if text.startswith(prefix)), None)
    if code:
        text = t("weather.wind_dir." + code, lang)
    match = _WIND_LEVEL_RE.search(str(power or ""))
    if match:
        level = match.group(0).replace(" ", "")
        text = (text + " " + t("weather.wind_level", lang, level=level)).strip()
    elif str(power or "").strip():
        text = (text + " " + str(power).strip()).strip()
    return text


def build_weather_xaml(location: str, temp, desc: str, wind: str, humidity,
                       lang: str = DEFAULT_LANG) -> str:
    """天气卡片：温度为主，天气/位置次之，分隔线，风力与湿度，一句应景建议。"""
    detail = escape_attr(wind or "")
    if humidity not in (None, ""):
        detail += (" · " if detail else "") + t("weather.humidity", lang, value=humidity)
    tip = _pick_tip(int(temp), str(desc), lang)
    shown = weather_desc(desc, lang)

    return ('<Border CornerRadius="10" Padding="16,16" Margin="0,0,0,0" Background="{DynamicResource ColorBrush7}">'
            "<StackPanel>"
            '<StackPanel Orientation="Horizontal" HorizontalAlignment="Center">'
            '<TextBlock Text="' + str(temp) + '°" FontSize="36" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" />'
            '<TextBlock Text="' + escape_attr(shown) + '" FontSize="15" VerticalAlignment="Bottom" '
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


def build_weather_unavailable(reason: str = "", lang: str = DEFAULT_LANG) -> str:
    text = (t("weather.unavailable_reason", lang, reason=reason) if reason
            else t("weather.unavailable", lang))
    return '<local:MyHint Theme="Yellow" Margin="0,0,0,0" Text="' + escape_attr(text) + '" />'


class WeatherService:
    """带缓存的天气服务。"""

    def __init__(self, config: Config):
        self.config = config
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, ip: str, lang: str = DEFAULT_LANG) -> dict:
        """返回 ``{"body": XAML, "kind": 天气类别, "source": 简述}``；永不抛异常。"""
        if not self.config.enable_weather:
            return {"body": build_weather_unavailable(t("weather.reason.off", lang), lang),
                    "kind": None, "source": "关闭"}

        candidates, cache_key, who = self._targets(ip)

        now = time.time()
        # 缓存的是渲染好的 XAML，所以缓存键必须带上语言：同一个城市、
        # 一个简中访客和一个英文访客看到的不是同一块卡片。
        with self._lock:
            hit = self._cache.get(cache_key + "|" + lang)
        if hit and now - hit[0] < self.config.weather_cache_seconds:
            debug("[Weather] 命中缓存（" + cache_key + "|" + lang + "）")
            return hit[1]

        result = self._fetch(candidates, who, lang)
        if result["kind"] is not None:
            with self._lock:
                self._cache[cache_key + "|" + lang] = (now, result)
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

    def _fetch(self, candidates: list[dict], who: str, lang: str = DEFAULT_LANG) -> dict:
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
                return {"body": build_weather_unavailable(t("weather.reason.not_found", lang), lang),
                        "kind": None, "source": "定位失败"}
            warn("[Weather] 取天气失败（" + who + "，" + str(round(elapsed)) + "ms，"
                 + ("HTTP " + str(status) if status else "无响应") + "）")
            return {"body": build_weather_unavailable(lang=lang), "kind": None, "source": "失败"}

        # 位置只取"市 · 区县"：省份对同名城市没帮助（北京/上海等直辖市省市同名），
        # 拼上反而又长又重复（"北京市 北京"）。
        name = str(data.get("city") or t("weather.here", lang))
        district = str(data.get("district") or "")
        location = name + (" · " + district if district and district != name else "")

        wind = weather_wind(data.get("wind_direction"), data.get("wind_power"), lang)
        desc = str(data.get("weather") or "")
        # 国内城市返回整数，境外会带小数（14.8°），统一取整看着更整齐
        try:
            temperature = round(float(data["temperature"]))
        except (TypeError, ValueError):
            temperature = data["temperature"]

        out("[Weather] " + location + " " + str(temperature) + "° " + desc
            + "（" + who + "，" + str(round(elapsed)) + "ms）")
        return {
            "body": build_weather_xaml(location, temperature, desc, wind, data.get("humidity"), lang),
            "kind": classify_weather(desc),
            "source": location,
        }
