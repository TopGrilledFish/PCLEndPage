# -*- coding: utf-8 -*-
"""卡片配色：让访客挑 PCL 主题色的浓度，用在主页那些色块和文字上。

**能改的到底是什么**（教学文件的「主题色」那节）：PCL 的卡片本体
（``local:MyCard``）**没有颜色属性**——它的属性只有 Title / CanSwap / IsSwapped /
HorizontalAlignment / UseAnimation / SwapLogoRight / HasMouseAnimation，卡片底色由
PCL 按自己的主题画，外部改不了。教学文件给的路子是：任何颜色参数都可以写
``{DynamicResource ColorBrushN}``，N 取 1~8 就是主题色的八档浓度（1 最深、8 最淡，
1~4 那几档配白字用）。所以"自定义卡片颜色"在这个主页上能做的，就是**卡片里的
色块与文字用哪几档浓度**：天气卡片、计算结果框、设置页的状态块，以及那些标签。

**实现方式**：生成页面时统一写死四个"角色号"（下面 ``ROLE_BRUSH``），
页面拼完再按访客的选择整体换号。之所以用"整体换号"而不是把调色板一路传下去：
``note()`` / ``heading()`` 这些零件被上百处调用，传参要改一大片，而它们用的
颜色号本来就只有这四个。作为交换，这里立一条规矩并用体检脚本守住：
**我们生成的 XAML 里只许出现这四个 ``ColorBrushN``**，别的一律不碰。
"""
from __future__ import annotations

import re

#: 代码里写死的四个角色号 → 访客可调的两个维度。
#: panel 是色块背景，text 是正文字色；muted / dim 比 text 再淡一档，跟着 text 走。
ROLE_BRUSH = {"panel": 7, "text": 1, "muted": 3, "dim": 2}

PANEL_CHOICES = (5, 6, 7, 8)        # 5 最明显 → 8 最淡
TEXT_CHOICES = (1, 2, 3)            # 1 最深 → 3 最浅
DEFAULTS = {"panel": 7, "text": 1}

_BRUSH_RE = re.compile(r"\{DynamicResource ColorBrush(\d)\}")


def normalize(value, choices: tuple, default: int) -> int:
    """把用户填的东西归一到可选档位之一，认不出就用默认值。"""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if number in choices else default


def brushes(record: dict | None) -> dict:
    """这份设置最终用哪四个颜色号。

    muted / dim 由 text 推出来（比 text 淡 1~2 档），这样"文字深浅"一个旋钮
    就够，不用让访客去调四个数字。text=1 时正好是 3 / 2，跟改动前的默认一致。
    """
    record = record or {}
    panel = normalize(record.get("panel"), PANEL_CHOICES, DEFAULTS["panel"])
    text = normalize(record.get("text"), TEXT_CHOICES, DEFAULTS["text"])
    return {"panel": panel, "text": text,
            "muted": min(text + 2, 8), "dim": min(text + 1, 8)}


def apply(xaml: str, palette: dict) -> str:
    """把页面里那四个角色号换成这位访客选的号。

    ``palette`` 传 ``brushes()`` 的结果；传空就用默认档（等于不换）。
    """
    mapping = {str(ROLE_BRUSH[role]): str(palette.get(role, ROLE_BRUSH[role]))
               for role in ROLE_BRUSH}
    return _BRUSH_RE.sub(lambda m: "{DynamicResource ColorBrush"
                         + mapping.get(m.group(1), m.group(1)) + "}", xaml)


def stray_brushes(xaml: str) -> list:
    """页面里出现的、不属于这四个角色号的浓度。

    体检脚本用它守住上面那条规矩。**要在 ``apply()`` 之前调**——换完号之后
    页面里的数字就是访客选的了，那时再查只会查到一堆"越界"的假警报。
    """
    return sorted({m.group(1) for m in _BRUSH_RE.finditer(xaml)
                   if m.group(1) not in {str(n) for n in ROLE_BRUSH.values()}})
