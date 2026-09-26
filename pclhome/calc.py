# -*- coding: utf-8 -*-
"""计算器：把中文 Minecraft Wiki「计算器」页里能纯算的那几个搬过来。

Wiki 上列了 27 个，但大部分在这儿做不了——伤害/矛/重锤要整套武器附魔数据表，
旗帜、信标颜色、盔甲颜色、格式化代码文本编辑器要画图，实体运动、爆炸影响、
运输计算器是逐步模拟，Chunkbase 和互动式地图本来就是外部站点。所以只挑了
纯数值、一个输入框就能算完的这些，其余的没搬。

**每个计算器只有一个输入框，这是被 PCL 逼的**：事件参数只能绑一个控件——

    EventData="{Binding Path=Text,ElementName=X,StringFormat='...?q={0}'}"

没有把多个框拼起来的办法。所以多值输入统一约定成用空格或逗号分隔，
具体格式写在输入框的提示文字里，页面上也会再写一遍。

**提交后回到本页**：计算按钮指向的就是当前这个页面的 .json，只是多带一个 ``?q=``，
所以算完就地出结果，不会在 PCL 的页面栈上多压一层。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from urllib.parse import parse_qs, unquote

from .log import warn
from .xaml import (ICON_BACK, ICON_CALC, ICON_HOME, attr, bind_to, escape_attr,
                   grid2, heading, help_button, input_row, nav_row, note,
                   url_attr)


# ============ 输入 / 输出 ============

def _floats(raw: str):
    """把输入里的数字抠出来。空格、逗号、分号、顿号都当分隔符。"""
    text = raw or ""
    for ch in "，,;；、\t":
        text = text.replace(ch, " ")
    out = []
    for token in text.split():
        try:
            out.append(float(token))
        except ValueError:
            return None
    return out


def _f(value: float) -> str:
    """整数就不显示小数点。"""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return ("%.4f" % value).rstrip("0").rstrip(".")


def _need(nums, count, what):
    if nums is None:
        raise ValueError("只能填数字，多个数用逗号隔开。")
    if len(nums) < count:
        raise ValueError("要填 " + str(count) + " 个数：" + what)
    return nums


def _one(raw, what):
    nums = _floats(raw)
    _need(nums, 1, what)
    return nums[0]


# ============ 各个计算器 ============

def _calc_nether(raw: str):
    """主世界 ↔ 下界坐标。横向 8:1，Y 不变。"""
    nums = _floats(raw)
    _need(nums, 2, "X,Z（也可以填 X,Y,Z）")
    x, z = nums[0], nums[1]
    rows = [
        ("主世界 → 下界", "X " + _f(x / 8) + "   Z " + _f(z / 8)),
        ("下界 → 主世界", "X " + _f(x * 8) + "   Z " + _f(z * 8)),
    ]
    if len(nums) >= 3:
        rows.append(("Y", _f(nums[1]) + "（Y 不换算）"))
    rows.append(("比例", "主世界 8 格 = 下界 1 格"))
    return rows


def _calc_chunk(raw: str):
    """坐标 ↔ 区块 ↔ 区域文件。"""
    nums = _floats(raw)
    _need(nums, 2, "X,Z")
    x, z = nums[0], nums[1]
    cx, cz = math.floor(x / 16), math.floor(z / 16)
    return [
        ("坐标", "X " + _f(x) + "   Z " + _f(z)),
        ("所在区块", "X " + str(cx) + "   Z " + str(cz)),
        ("区块内偏移", "X " + _f(x - cx * 16) + "   Z " + _f(z - cz * 16) + "（0–15）"),
        ("区域文件", "r." + str(math.floor(cx / 32)) + "." + str(math.floor(cz / 32)) + ".mca"),
        ("区块范围", "X " + str(cx * 16) + "–" + str(cx * 16 + 15)
                     + "   Z " + str(cz * 16) + "–" + str(cz * 16 + 15)),
    ]


def _calc_distance(raw: str):
    """两点距离。填 6 个数算三维，填 4 个数按水平面算。"""
    nums = _floats(raw)
    if nums is not None and len(nums) >= 6:
        x1, y1, z1, x2, y2, z2 = nums[:6]
    elif nums is not None and len(nums) >= 4:
        x1, z1, x2, z2 = nums[:4]
        y1 = y2 = 0.0
    else:
        raise ValueError("填 6 个数 X1,Y1,Z1,X2,Y2,Z2，或 4 个数 X1,Z1,X2,Z2。")
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    flat = math.hypot(dx, dz)
    rows = [
        ("水平距离", _f(flat) + " 格"),
        ("直线距离", _f(math.sqrt(dx * dx + dy * dy + dz * dz)) + " 格"),
        ("Δ", "X " + _f(dx) + "   Y " + _f(dy) + "   Z " + _f(dz)),
        ("走下界", "主世界走 " + _f(flat) + " 格 ≈ 下界 " + _f(flat / 8) + " 格"),
    ]
    return rows


def _calc_tick(raw: str):
    """刻 ↔ 秒 / 分 / 游戏日。"""
    t = _one(raw, "刻数")
    return [
        ("刻", _f(t)),
        ("秒", _f(t / 20)),
        ("分钟", _f(t / 1200)),
        ("红石刻", _f(t / 2) + "（红石中继器 1 档 = 2 刻）"),
        ("游戏日", _f(t / 24000) + "（1 游戏日 = 24000 刻 = 20 分钟）"),
    ]


def _calc_exp(raw: str):
    """升到某级需要多少经验。分段公式，和游戏里一致。"""
    level = int(_one(raw, "等级"))
    if level < 0:
        raise ValueError("等级不能是负数。")

    def total(n):
        if n <= 16:
            return n * n + 6 * n
        if n <= 31:
            return int(round(2.5 * n * n - 40.5 * n + 360))
        return int(round(4.5 * n * n - 162.5 * n + 2220))

    rows = [("从 0 升到 " + str(level) + " 级", str(total(level)) + " 点经验")]
    if level > 0:
        rows.append(("从 " + str(level - 1) + " 升到 " + str(level) + " 级",
                     str(total(level) - total(level - 1)) + " 点经验"))
    rows.append(("再升一级", str(total(level + 1) - total(level)) + " 点经验"))
    rows.append(("换算", "1 级 ≈ " + str(total(1)) + " 点，30 级 = 1395 点"))
    return rows


def _calc_armor(raw: str):
    """护甲减伤。Java 1.9 之后的公式，带护甲韧性。"""
    nums = _floats(raw)
    _need(nums, 2, "伤害,护甲（还可以再填 韧性,保护等级）")
    damage, armor = nums[0], nums[1]
    toughness = nums[2] if len(nums) >= 3 else 0.0
    prot = nums[3] if len(nums) >= 4 else 0.0
    if damage <= 0:
        raise ValueError("伤害要大于 0。")

    effective = min(20.0, max(armor / 5.0, armor - damage / (2.0 + toughness / 4.0)))
    after = damage * (1 - effective / 25.0)
    epf = min(20.0, prot)                       # 保护每级 1 点 EPF，上限 20
    final = after * (1 - epf / 25.0)
    return [
        ("原始伤害", _f(damage)),
        ("有效护甲值", _f(effective) + " / 20（护甲 " + _f(armor)
                        + "，韧性 " + _f(toughness) + "）"),
        ("护甲减伤后", _f(after)),
        ("附魔减伤后", _f(final) + ("（保护等级 " + _f(prot) + "）" if prot else "")),
        ("总减免", _f((1 - final / damage) * 100) + "%"),
    ]


def _calc_stack(raw: str):
    """物品数量 ↔ 组数 / 潜影盒 / 箱子。"""
    n = int(_one(raw, "物品总数"))
    if n < 0:
        raise ValueError("数量不能是负数。")
    stacks, rest = divmod(n, 64)
    boxes, box_rest = divmod(n, 64 * 27)
    chests, chest_rest = divmod(n, 64 * 54)
    return [
        ("物品数", str(n)),
        ("按 64 一组", str(stacks) + " 组" + (" 又 " + str(rest) + " 个" if rest else "")),
        ("潜影盒", str(boxes) + " 盒" + (" 又 " + str(box_rest) + " 个" if box_rest else "")
                     + "（1 盒 = 27 组）"),
        ("大箱子", str(chests) + " 箱" + (" 又 " + str(chest_rest) + " 个" if chest_rest else "")
                     + "（1 箱 = 54 格）"),
    ]


_HEX_RE = re.compile(r"^(?:#|0x)?([0-9a-fA-F]{6})$")


def _calc_color(raw: str):
    """颜色值：十六进制 ↔ 十进制。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError("填一个颜色，比如 #FF8800 或者 16746496。")
    match = _HEX_RE.match(text)
    if match:
        value = int(match.group(1), 16)
    else:
        try:
            value = int(text)
        except ValueError:
            raise ValueError("认不出来。填 #RRGGBB 这种，或者一个十进制整数。")
    if not 0 <= value <= 0xFFFFFF:
        raise ValueError("颜色值要在 0 到 16777215 之间。")
    return [
        ("十六进制", "#%06X" % value),
        ("十进制", str(value)),
        ("命令里写", "0x%06X 或 %d" % (value, value)),
        ("RGB", "R %d   G %d   B %d" % (value >> 16 & 0xFF, value >> 8 & 0xFF, value & 0xFF)),
    ]


def _calc_uuid(raw: str):
    """离线模式玩家 UUID。名字一样，算出来的 UUID 就一样。"""
    name = (raw or "").strip()
    if not name:
        raise ValueError("填一个玩家名。")
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30        # 版本 3
    digest[8] = (digest[8] & 0x3F) | 0x80        # 变体
    hexed = digest.hex()
    dashed = "-".join([hexed[0:8], hexed[8:12], hexed[12:16], hexed[16:20], hexed[20:32]])
    return [
        ("玩家名", name),
        ("离线 UUID", dashed),
        ("不带横线", hexed),
        ("说明", "服务器开了正版验证的话，要用正版 UUID，这个不对。"),
    ]


def _calc_seed(raw: str):
    """文本种子对应的数字。Java 的 String.hashCode。"""
    text = raw or ""
    if not text.strip():
        raise ValueError("填一段文字。")
    data = text.encode("utf-16-be")              # Java 按 UTF-16 码元算，代理对要拆开
    h = 0
    for i in range(0, len(data), 2):
        h = (31 * h + ((data[i] << 8) | data[i + 1])) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return [
        ("文字", text.strip()),
        ("种子值", str(h)),
        ("无符号", str(h & 0xFFFFFFFF)),
        ("说明", "和游戏里输入这段文字当种子等价。"),
    ]


CALCS = [
    {"id": "nether", "name": "下界坐标", "info": "主世界和下界之间的坐标换算，走传送门用得上。",
     "hint": "填 X,Z，比如 800,-1600；也可以填 X,Y,Z", "run": _calc_nether},
    {"id": "chunk", "name": "区块坐标", "info": "坐标落在哪个区块、哪个区域文件里。",
     "hint": "填 X,Z，比如 100,-200", "run": _calc_chunk},
    {"id": "distance", "name": "两点距离", "info": "两个坐标之间隔了多远。",
     "hint": "填 6 个数 X1,Y1,Z1,X2,Y2,Z2，或 4 个数 X1,Z1,X2,Z2",
     "run": _calc_distance},
    {"id": "tick", "name": "刻换算", "info": "刻换成秒、分钟、游戏日。红石线路算延时用得上。",
     "hint": "填一个刻数，比如 1200", "run": _calc_tick},
    {"id": "exp", "name": "升级经验", "info": "升到某个等级一共要多少经验。",
     "hint": "填一个等级，比如 30", "run": _calc_exp},
    {"id": "armor", "name": "护甲减伤", "info": "这身装备挨一下会掉多少血。",
     "hint": "填 伤害,护甲，比如 20,20；还能再跟 韧性,保护等级",
     "run": _calc_armor},
    {"id": "stack", "name": "物品堆叠", "info": "一堆东西等于几组、几个潜影盒、几箱。",
     "hint": "填物品总数，比如 10000", "run": _calc_stack},
    {"id": "color", "name": "颜色值", "info": "十六进制和十进制颜色值互转，写命令时用。",
     "hint": "填 #FF8800 或 16746496", "run": _calc_color},
    {"id": "uuid", "name": "玩家 UUID", "info": "离线模式下玩家名对应的 UUID。",
     "hint": "填玩家名，比如 Notch", "run": _calc_uuid},
    {"id": "seed", "name": "种子散列值", "info": "把一段文字换成种子数字，和游戏里的算法一致。",
     "hint": "填一段文字，比如 hello", "run": _calc_seed},
]

CALC_BY_ID = {item["id"]: item for item in CALCS}


# ============ 页面 ============

def _rows_block(rows: list) -> str:
    """结果区：左边标签、右边值的两列表格。"""
    defs = "".join('<RowDefinition Height="Auto" />' for _ in rows)
    cells = []
    for index, (label, value) in enumerate(rows):
        cells.append('<TextBlock Grid.Row="' + str(index) + '" Grid.Column="0" Text="'
                     + escape_attr(label) + '" FontSize="12" Margin="0,0,16,7" '
                     'Foreground="{DynamicResource ColorBrush3}" VerticalAlignment="Top" />')
        cells.append('<TextBlock Grid.Row="' + str(index) + '" Grid.Column="1" Text="'
                     + attr(value) + '" FontSize="13" FontWeight="Bold" TextWrapping="Wrap" '
                     'Margin="0,0,0,7" Foreground="{DynamicResource ColorBrush1}" />')
    return ('<Grid><Grid.ColumnDefinitions><ColumnDefinition Width="Auto" />'
            '<ColumnDefinition Width="1*" /></Grid.ColumnDefinitions>'
            '<Grid.RowDefinitions>' + defs + "</Grid.RowDefinitions>"
            + "".join(cells) + "</Grid>")


def _calc_grid(base: str, columns: int = 2) -> str:
    """计算器入口的两列按钮网格。"""
    rows = (len(CALCS) + columns - 1) // columns
    defs = "".join('<ColumnDefinition Width="1*" />' for _ in range(columns))
    rowdefs = "".join('<RowDefinition Height="Auto" />' for _ in range(rows))
    cells = []
    for index, item in enumerate(CALCS):
        col, row = index % columns, index // columns
        margin = "0,0,5,9" if col == 0 else "5,0,0,9"
        cells.append(help_button(item["name"], ICON_CALC,
                                 base + "/calc_" + item["id"] + ".json",
                                 42, column=col, margin=margin)
                     .replace("<local:MyIconTextButton",
                              '<local:MyIconTextButton Grid.Row="' + str(row) + '"', 1))
    return ('<Grid><Grid.ColumnDefinitions>' + defs + "</Grid.ColumnDefinitions>"
            '<Grid.RowDefinitions>' + rowdefs + "</Grid.RowDefinitions>"
            + "".join(cells) + "</Grid>")


def build_landing(base_url: str) -> str:
    """计算器首页：列出所有能算的。"""
    base = url_attr(base_url)
    names = "、".join(item["name"] for item in CALCS)
    return (
        '<local:MyCard Title="计算器" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'

        '<TextBlock Text="Minecraft 计算器" FontSize="24" FontWeight="Bold" '
        'Foreground="#FF000000" HorizontalAlignment="Center" />'
        + note("照中文 Minecraft Wiki 的计算器页做的，搬了纯数值的那些。"
               "伤害、旗帜、地图之类的要么要整套数据表、要么得画图，这儿做不了，就没搬。",
               "0,10,0,0")
        + note("一共 " + str(len(CALCS)) + " 个：" + names + "。", "0,6,0,0")

        + '<TextBlock Text="选一个" FontSize="13" FontWeight="Bold" Margin="0,18,0,10" '
        'Foreground="{DynamicResource ColorBrush1}" />'
        + _calc_grid(base)

        + nav_row(base)

        + "</StackPanel>"
        + "</local:MyCard>")


def build_calc_page(item: dict, raw: str, base_url: str) -> str:
    """单个计算器。带上 ``?q=`` 时就地算出结果。"""
    base = url_attr(base_url)
    rows, problem = None, ""
    if (raw or "").strip():
        try:
            rows = item["run"](raw)
        except ValueError as exc:
            problem = str(exc)
        except Exception as exc:                       # 别让一个算错把整页带崩
            warn("[Calc] " + item["id"] + " 计算失败：" + repr(exc))
            problem = "这组数算不出来，检查一下再试。"

    if problem:
        result = ('<local:MyHint Theme="Yellow" Margin="0,16,0,0" Text="'
                  + attr(problem) + '" />')
    elif rows:
        result = ('<StackPanel Margin="0,16,0,0">'
                  '<TextBlock Text="结果" FontSize="14" FontWeight="Bold" '
                  'Foreground="{DynamicResource ColorBrush1}" Margin="0,0,0,10" />'
                  '<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" '
                  'Padding="16,14">' + _rows_block(rows) + "</Border>"
                  "</StackPanel>")
    else:
        result = ''

    return (
        '<local:MyCard Title="' + escape_attr(item["name"]) + '" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'

        '<TextBlock Text="' + escape_attr(item["name"]) + '" FontSize="24" FontWeight="Bold" '
        'Foreground="#FF000000" HorizontalAlignment="Center" />'
        + note(item["info"], "0,10,0,0")

        + input_row("calcinput", item["hint"], 40, "0,14,0,0")
        + note("要填多个数就用逗号隔开，别用空格——PCL 是把输入框里的字原样拼进"
               "网址的，中间夹空格这次请求就发不出去。")
        + help_button("计算", ICON_CALC,
                      bind_to("calcinput", base, "/calc_" + item["id"] + ".json"), 44,
                      margin="0,10,0,0")

        + result

        + nav_row(base, extra_text="换个计算器", extra_url=base + "/calc.json")

        + "</StackPanel>"
        + "</local:MyCard>")


# ============ 路由 ============

CALC_PATHS = {"/calc.json", "/calc.xaml"}
for _item in CALCS:
    CALC_PATHS.add("/calc_" + _item["id"] + ".json")
    CALC_PATHS.add("/calc_" + _item["id"] + ".xaml")

_META = {"/calc.json": ("计算器", "Minecraft 常用计算")}
for _item in CALCS:
    _META["/calc_" + _item["id"] + ".json"] = (_item["name"], _item["info"])


def _json_response(body: str):
    from .server import Response
    return Response.text(body, content_type="application/json; charset=utf-8",
                         headers={"Cache-Control": "no-store"})


def _xaml_response(body: str):
    from .server import Response
    return Response.xaml(body)


def handle(service, path: str, query: str, ip: str, origin: str = ""):
    """处理 ``/calc*`` 请求；不是我们的路径就返回 None。"""
    if path in _META:
        title, desc = _META[path]
        return _json_response(json_meta(title, desc))

    base = service.config.resolved_base_url(origin)

    # 列表页单独判，别塞进下面那个正则（/calc.xaml 里没有下划线，匹配不上）
    if path == "/calc.xaml":
        return _xaml_response(build_landing(base))

    match = re.match(r"^/calc_([a-z]+)\.xaml$", path)
    if not match:
        return None
    item = CALC_BY_ID.get(match.group(1))
    if item is None:
        return None

    params = parse_query(query)
    return _xaml_response(build_calc_page(item, params.get("q", ""), base))


def json_meta(title: str, desc: str) -> str:
    return json.dumps({"Title": title, "Description": desc}, ensure_ascii=False)


def parse_query(query: str) -> dict:
    """宽松解析查询串——PCL 把输入框原文直接拼进 URL，不做编码。"""
    try:
        values = parse_qs(query or "", keep_blank_values=True)
        return {key: val[0] for key, val in values.items() if val}
    except Exception:
        result = {}
        for pair in (query or "").split("&"):
            if "=" in pair:
                key, _, value = pair.partition("=")
                try:
                    result[key] = unquote(value)
                except Exception:
                    result[key] = value
        return result
