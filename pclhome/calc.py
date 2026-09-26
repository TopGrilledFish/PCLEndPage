# -*- coding: utf-8 -*-
"""计算器：把中文 Minecraft Wiki「计算器」页里能纯算的那几个搬过来。

Wiki 上列了 27 个，但大部分在这儿做不了——旗帜、信标颜色、盔甲颜色、格式化代码
文本编辑器要画图，实体运动、爆炸影响、运输计算器是逐步模拟，矛伤害和重锤下落
要额外的武器/落差数据，Chunkbase 和互动式地图本来就是外部站点。所以只挑了纯数值、
一个输入框就能算完的这些，其余的没搬。近战伤害算搬过来了：武器不内置成一张表，
攻击力直接让使用者填物品栏里那个数。

**每个计算器只有一个输入框，这是被 PCL 逼的**：事件参数只能绑一个控件——

    EventData="{Binding Path=Text,ElementName=X,StringFormat='...?q={0}'}"

想把几个框的值拼进一个 ``EventData``，WPF 的正路是 ``MultiBinding``，而它只有
属性元素写法；PCL 的 ``EventType``/``EventData`` 不是真正的 XAML 成员（由 PCL
自己预处理），一写成属性元素就报：

    加载帮助 XAML 文件失败："无法设置未知成员"PCL.MyIconTextButton.EventData"。"

试过了，走不通，别再试。所以多值输入统一约定用逗号分隔，该填什么按什么顺序
写在输入框上面的正文里，框里的提示只说分隔符（:data:`SEPARATOR_HINT`）。
伤害计算参数多，用 :func:`_damage_fields` 按**位置**拆，没填的格子留空即可。

**提交后回到本页**：计算按钮指向的就是当前这个页面的 .json，只是多带一个 ``?q=``，
所以算完就地出结果，不会在 PCL 的页面栈上多压一层。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from urllib.parse import parse_qs, unquote

from .config import ICONS_DIR, PACK_FALLBACK_IMAGE
from .i18n import DEFAULT_LANG, t
from . import palette
from .log import warn
from .xaml import (ICON_BACK, ICON_CALC, ICON_HOME, attr, bind_to, escape_attr,
                   grid2, heading, help_button, indent_block, input_row, nav_row,
                   note, render_template, url_attr)


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


def _need(nums, count, what, lang):
    if nums is None:
        raise ValueError(t("calc.err.numbers", lang))
    if len(nums) < count:
        raise ValueError(t("calc.err.need", lang, count=count, what=what))


def _nth(nums, index: int) -> float:
    """第 index 个数；没填就按 0 算——伤害计算后面那几个都是可选的。"""
    return nums[index] if index < len(nums) else 0.0


def _damage_fields(raw: str, count: int, lang) -> list:
    """按逗号**位置**拆伤害计算的参数，空着的那格返回 None。

    伤害计算是每个参数一个输入框，拼出来的 URL 里没填的格子就是空串，
    所以不能像 :func:`_floats` 那样把空串丢掉——丢一个，后面全部串位。
    """
    text = raw or ""
    for ch in "，;；\t":
        text = text.replace(ch, ",")
    parts = [part.strip() for part in text.split(",")]
    nums = []
    for part in parts[:count]:
        if not part:
            nums.append(None)
            continue
        try:
            nums.append(float(part))
        except ValueError:
            raise ValueError(t("calc.err.each_number", lang))
    while len(nums) < count:
        nums.append(None)
    return nums


def _one(raw, what, lang):
    nums = _floats(raw)
    _need(nums, 1, what, lang)
    return nums[0]


# ============ 各个计算器 ============
#
# 每个函数收 ``(raw, lang)``：结果里的标签和单位都是文案，得跟着语言走。
# 只有数字和 X/Y/Z 这类坐标符号不翻。

def _calc_nether(raw: str, lang: str):
    """主世界 ↔ 下界坐标。横向 8:1，Y 不变。"""
    nums = _floats(raw)
    _need(nums, 2, t("calc.nether.what", lang), lang)
    x, z = nums[0], nums[1]
    rows = [
        (t("calc.nether.to_nether", lang), "X " + _f(x / 8) + "   Z " + _f(z / 8)),
        (t("calc.nether.to_over", lang), "X " + _f(x * 8) + "   Z " + _f(z * 8)),
    ]
    if len(nums) >= 3:
        rows.append(("Y", t("calc.nether.y_note", lang, n=_f(nums[1]))))
    rows.append((t("calc.nether.ratio", lang), t("calc.nether.ratio_value", lang)))
    return rows


def _calc_chunk(raw: str, lang: str):
    """坐标 ↔ 区块 ↔ 区域文件。"""
    nums = _floats(raw)
    _need(nums, 2, "X,Z", lang)
    x, z = nums[0], nums[1]
    cx, cz = math.floor(x / 16), math.floor(z / 16)
    return [
        (t("calc.chunk.pos", lang), "X " + _f(x) + "   Z " + _f(z)),
        (t("calc.chunk.chunk", lang), "X " + str(cx) + "   Z " + str(cz)),
        (t("calc.chunk.offset", lang), "X " + _f(x - cx * 16) + "   Z " + _f(z - cz * 16)
         + t("calc.chunk.offset_note", lang)),
        (t("calc.chunk.region", lang),
         "r." + str(math.floor(cx / 32)) + "." + str(math.floor(cz / 32)) + ".mca"),
        (t("calc.chunk.range", lang), "X " + str(cx * 16) + "–" + str(cx * 16 + 15)
         + "   Z " + str(cz * 16) + "–" + str(cz * 16 + 15)),
    ]


def _calc_distance(raw: str, lang: str):
    """两点距离。填 6 个数算三维，填 4 个数按水平面算。"""
    nums = _floats(raw)
    if nums is not None and len(nums) >= 6:
        x1, y1, z1, x2, y2, z2 = nums[:6]
    elif nums is not None and len(nums) >= 4:
        x1, z1, x2, z2 = nums[:4]
        y1 = y2 = 0.0
    else:
        raise ValueError(t("calc.distance.what", lang))
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    flat = math.hypot(dx, dz)
    return [
        (t("calc.distance.flat", lang), t("calc.blocks", lang, n=_f(flat))),
        (t("calc.distance.straight", lang),
         t("calc.blocks", lang, n=_f(math.sqrt(dx * dx + dy * dy + dz * dz)))),
        ("Δ", "X " + _f(dx) + "   Y " + _f(dy) + "   Z " + _f(dz)),
        (t("calc.distance.via_nether", lang),
         t("calc.distance.via_nether_value", lang, flat=_f(flat), nether=_f(flat / 8))),
    ]


def _calc_tick(raw: str, lang: str):
    """刻 ↔ 秒 / 分 / 游戏日。"""
    ticks = _one(raw, t("calc.tick.what", lang), lang)
    return [
        (t("calc.tick.ticks", lang), _f(ticks)),
        (t("calc.tick.seconds", lang), _f(ticks / 20)),
        (t("calc.tick.minutes", lang), _f(ticks / 1200)),
        (t("calc.tick.redstone", lang), _f(ticks / 2) + t("calc.tick.redstone_note", lang)),
        (t("calc.tick.days", lang), _f(ticks / 24000) + t("calc.tick.days_note", lang)),
    ]


def _calc_exp(raw: str, lang: str):
    """升到某级需要多少经验。分段公式，和游戏里一致。"""
    level = int(_one(raw, t("calc.exp.what", lang), lang))
    if level < 0:
        raise ValueError(t("calc.exp.negative", lang))

    def total(n):
        if n <= 16:
            return n * n + 6 * n
        if n <= 31:
            return int(round(2.5 * n * n - 40.5 * n + 360))
        return int(round(4.5 * n * n - 162.5 * n + 2220))

    points = lambda n: t("calc.exp.points", lang, n=n)      # noqa: E731
    rows = [(t("calc.exp.from_zero", lang, level=level), points(total(level)))]
    if level > 0:
        rows.append((t("calc.exp.between", lang, a=level - 1, b=level),
                     points(total(level) - total(level - 1))))
    rows.append((t("calc.exp.next", lang), points(total(level + 1) - total(level))))
    rows.append((t("calc.exp.convert", lang),
                 t("calc.exp.convert_value", lang, n=total(1))))
    return rows


def _calc_armor(raw: str, lang: str):
    """护甲减伤。Java 1.9 之后的公式，带护甲韧性。"""
    nums = _floats(raw)
    _need(nums, 2, t("calc.armor.what", lang), lang)
    damage, armor = nums[0], nums[1]
    toughness = nums[2] if len(nums) >= 3 else 0.0
    prot = nums[3] if len(nums) >= 4 else 0.0
    if damage <= 0:
        raise ValueError(t("calc.armor.positive", lang))

    effective = min(20.0, max(armor / 5.0, armor - damage / (2.0 + toughness / 4.0)))
    after = damage * (1 - effective / 25.0)
    epf = min(20.0, prot)                       # 保护每级 1 点 EPF，上限 20
    final = after * (1 - epf / 25.0)
    return [
        (t("calc.armor.raw", lang), _f(damage)),
        (t("calc.armor.effective", lang),
         t("calc.armor.effective_note", lang, eff=_f(effective), armor=_f(armor),
           toughness=_f(toughness))),
        (t("calc.armor.after_armor", lang), _f(after)),
        (t("calc.armor.after_ench", lang),
         _f(final) + (t("calc.armor.prot_note", lang, level=_f(prot)) if prot else "")),
        (t("calc.armor.total", lang), _f((1 - final / damage) * 100) + "%"),
    ]


def _calc_stack(raw: str, lang: str):
    """物品数量 ↔ 组数 / 潜影盒 / 箱子。"""
    n = int(_one(raw, t("calc.stack.what", lang), lang))
    if n < 0:
        raise ValueError(t("calc.stack.negative", lang))
    stacks, rest = divmod(n, 64)
    boxes, box_rest = divmod(n, 64 * 27)
    chests, chest_rest = divmod(n, 64 * 54)

    def count(value, rest_value, unit_key, note_key=""):
        text = t(unit_key, lang, n=value)
        if rest_value:
            text += t("calc.stack.and_n", lang, n=rest_value)
        return text + (t(note_key, lang) if note_key else "")

    return [
        (t("calc.stack.count", lang), str(n)),
        (t("calc.stack.stacks", lang), count(stacks, rest, "calc.stack.stack_unit")),
        (t("calc.stack.boxes", lang), count(boxes, box_rest, "calc.stack.box_unit", "calc.stack.box_note")),
        (t("calc.stack.chests", lang), count(chests, chest_rest, "calc.stack.chest_unit", "calc.stack.chest_note")),
    ]


_HEX_RE = re.compile(r"^(?:#|0x)?([0-9a-fA-F]{6})$")


def _calc_color(raw: str, lang: str):
    """颜色值：十六进制 ↔ 十进制。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError(t("calc.color.empty", lang))
    match = _HEX_RE.match(text)
    if match:
        value = int(match.group(1), 16)
    else:
        try:
            value = int(text)
        except ValueError:
            raise ValueError(t("calc.color.bad", lang))
    if not 0 <= value <= 0xFFFFFF:
        raise ValueError(t("calc.color.range", lang))
    return [
        (t("calc.color.hex", lang), "#%06X" % value),
        (t("calc.color.dec", lang), str(value)),
        (t("calc.color.command", lang),
         t("calc.color.command_value", lang, hex="#%06X" % value, dec=value)),
        ("RGB", "R %d   G %d   B %d" % (value >> 16 & 0xFF, value >> 8 & 0xFF, value & 0xFF)),
    ]


def _calc_uuid(raw: str, lang: str):
    """离线模式玩家 UUID。名字一样，算出来的 UUID 就一样。"""
    name = (raw or "").strip()
    if not name:
        raise ValueError(t("calc.uuid.empty", lang))
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30        # 版本 3
    digest[8] = (digest[8] & 0x3F) | 0x80        # 变体
    hexed = digest.hex()
    dashed = "-".join([hexed[0:8], hexed[8:12], hexed[12:16], hexed[16:20], hexed[20:32]])
    return [
        (t("calc.uuid.name", lang), name),
        (t("calc.uuid.uuid", lang), dashed),
        (t("calc.uuid.dashed", lang), hexed),
        (t("calc.note", lang), t("calc.uuid.note_value", lang)),
    ]


def _java_hash(text: str) -> int:
    """Java 的 String.hashCode，按 UTF-16 码元算（代理对要拆成两个）。"""
    data = text.encode("utf-16-be")
    h = 0
    for i in range(0, len(data), 2):
        h = (31 * h + ((data[i] << 8) | data[i + 1])) & 0xFFFFFFFF
    return h - 0x100000000 if h >= 0x80000000 else h


def _calc_seed(raw: str, lang: str):
    """文本种子对应的数字。Java 的 String.hashCode。"""
    text = raw or ""
    if not text.strip():
        raise ValueError(t("calc.seed.empty", lang))
    h = _java_hash(text)
    return [
        (t("calc.seed.text", lang), text.strip()),
        (t("calc.seed.value", lang), str(h)),
        (t("calc.seed.unsigned", lang), str(h & 0xFFFFFFFF)),
        (t("calc.note", lang), t("calc.seed.note_value", lang)),
    ]


# ============ 要塞 ============
#
# 要塞是分 8 个环摆的，每环个数 3、6、10、15、21、28、36、9，一共 128 个。
# 环上的角度和半径完全由种子决定，所以这部分能算准；真正的位置还会从这个点
# 往外找最近的合法生物群系（半径 112 格），那要一整套生物群系生成器，搬不动。

STRONGHOLD_COUNT = 128
_JAVA_MULT = 0x5DEECE66D                          # java.util.Random 的常数
_JAVA_ADD = 0xB
_JAVA_MASK = (1 << 48) - 1


def _round_half_away(value: float) -> int:
    """C 的 round()：.5 一律往远离 0 的方向进，跟 Python 的银行家舍入不一样。"""
    return int(math.floor(value + 0.5)) if value >= 0 else int(math.ceil(value - 0.5))


class _JavaRandom:
    """java.util.Random：48 位线性同余，Minecraft 拿它做种子随机数。"""

    def __init__(self, seed: int):
        self.state = (seed ^ _JAVA_MULT) & _JAVA_MASK

    def _next(self, bits: int) -> int:
        self.state = (self.state * _JAVA_MULT + _JAVA_ADD) & _JAVA_MASK
        return self.state >> (48 - bits)

    def next_double(self) -> float:
        return ((self._next(26) << 27) + self._next(27)) / float(1 << 53)

    def next_long(self) -> int:
        value = (self._next(32) << 32) + self._next(32)
        return value - (1 << 64) if value >= (1 << 63) else value


def _stronghold_pos(angle: float, dist: float):
    """环上那个点，落到区块的 (4,4)——楼梯就在那儿。dist 的单位是区块。"""
    x = _round_half_away(math.cos(angle) * dist) * 16 + 8
    z = _round_half_away(math.sin(angle) * dist) * 16 + 8
    return ((x & ~15) + 4, (z & ~15) + 4)


def _strongholds(seed: int) -> list:
    """全部 128 个要塞的大致位置，按生成顺序。

    照 cubiomes 的 initFirstStronghold / nextStronghold 搬的（1.19.3 之后那条路，
    现在的版本都是）。移植完拿 cubiomes 原函数编成 C 跑过一遍，7 个种子 896 个点
    逐行一致；环的个数也正好是 3/6/10/15/21/28/36/9。
    """
    rnd = _JavaRandom(seed)
    angle = 2 * math.pi * rnd.next_double()
    dist = 128.0 + (rnd.next_double() - 0.5) * 80.0
    out = [_stronghold_pos(angle, dist)]

    ringnum, ringmax, ringidx, index = 0, 3, 0, 0
    while len(out) < STRONGHOLD_COUNT:
        rnd.next_long()                     # 1.19.3 起每个要塞都会多抽一个 long
        ringidx += 1
        angle += 2 * math.pi / ringmax
        if ringidx == ringmax:              # 换环：起始角度和环半径都重抽
            ringnum += 1
            ringidx = 0
            ringmax = ringmax + 2 * ringmax // (ringnum + 1)
            if ringmax > STRONGHOLD_COUNT - index:
                ringmax = STRONGHOLD_COUNT - index
            angle += rnd.next_double() * 2 * math.pi
        dist = 128.0 + 6.0 * ringnum * 32.0 + (rnd.next_double() - 0.5) * 80.0
        out.append(_stronghold_pos(angle, dist))
        index += 1
    return out


# 八方位。游戏里 +X 是东、+Z 是南。下标顺序不能改（改了方位就错），
# 显示的文字按语言查表。
_COMPASS_8 = ("e", "se", "s", "sw", "w", "nw", "n", "ne")


def _compass(dx: float, dz: float, lang: str) -> str:
    angle = math.atan2(dz, dx) % (2 * math.pi)
    code = _COMPASS_8[int((angle + math.pi / 8) / (math.pi / 4)) % 8]
    return t("calc.dir." + code, lang)


def _seed_value(text: str) -> int:
    """种子的数值：能当整数直接就用，否则按 Java 的 String.hashCode 折成数字。"""
    try:
        return int(text.strip())
    except ValueError:
        return _java_hash(text)


def _calc_stronghold(raw: str, lang: str):
    """离你最近的要塞。填 你的X,你的Z,种子。"""
    parts = [part.strip() for part in (raw or "").replace("，", ",").split(",")]
    if len(parts) < 3:
        raise ValueError(t("calc.stronghold.what", lang))
    try:
        x, z = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError(t("calc.stronghold.xz", lang))
    if not parts[2]:
        raise ValueError(t("calc.stronghold.no_seed", lang))
    seed = _seed_value(parts[2])

    best = None
    for index, (sx, sz) in enumerate(_strongholds(seed), 1):
        distance = math.hypot(sx - x, sz - z)
        if best is None or distance < best[0]:
            best = (distance, index, sx, sz)
    distance, index, sx, sz = best

    return [
        (t("calc.stronghold.you", lang), _f(x) + ", " + _f(z)),
        (t("calc.stronghold.seed", lang), str(seed)),
        (t("calc.stronghold.nearest", lang), str(sx) + ", " + str(sz)),
        (t("calc.stronghold.distance", lang), t("calc.blocks", lang, n=_f(distance))),
        (t("calc.stronghold.direction", lang),
         t("calc.stronghold.direction_value", lang,
           dir=_compass(sx - x, sz - z, lang), index=index)),
        (t("calc.note", lang), t("calc.stronghold.note_value", lang)),
    ]


# 伤害计算的参数，顺序就是 ?q= 里的顺序，页面上也照这个顺序写给人看。
# 文案在 calc.dmg.<键> 里（简中是「攻击力」这种）。
DAMAGE_PARAMS = ("attack", "sharpness", "smite", "bane", "strength", "weakness", "crit", "charge")


def damage_param_names(lang: str) -> list:
    return [t("calc.dmg." + key, lang) for key in DAMAGE_PARAMS]


def _calc_damage(raw: str, lang: str):
    """Java 版近战伤害。

    公式照 Wiki「计算器/近战伤害」那个小工具（它实际跑在 tools.minecraft.wiki
    的 iframe 里），从它的 JS 里逐条抠出来的：

        基础 = 攻击力；每级力量  基础 = 基础 × 1.3 + 1
                        每级虚弱  基础 = 基础 × 0.8 - 0.5      （夹在 0..2048）
        附魔 = 锋利 0.5L+0.5，亡灵/节肢杀手各 2.5L，最后取整
        最终 = 基础 × (0.2 + 0.8 × 充能²) × 暴击倍率 + 附魔 × 充能

    只做 Java 版：基岩版的附魔和力量是另一套加法（每级 +3/-4、附魔 ×1.25），
    加上去这行输入就更没法填了。也不管重锤下落和矛冲锋——那俩要额外的数据。
    """
    if not (raw or "").strip(" ,，;；\t"):
        raise ValueError(t("calc.damage.min", lang))
    nums = _damage_fields(raw, len(DAMAGE_PARAMS), lang)

    def filled(index: int, default: float = 0.0) -> float:
        """第 index 格里填的数；那格空着就用 default。"""
        value = nums[index]
        return default if value is None else value

    attack = filled(0)
    sharp = int(filled(1))
    smite = int(filled(2))
    bane = int(filled(3))
    strength = int(filled(4))
    weakness = int(filled(5))
    want_crit = filled(6) > 0
    charge = min(max(filled(7, 100.0) / 100.0, 0.0), 1.0)

    extra = []
    # 原版里锋利和亡灵/节肢杀手不会同时挂在同一件武器上，Wiki 那个计算器
    # 遇到同时填了也只按一个算，跟着来。
    if sharp > 0 and (smite > 0 or bane > 0):
        smite = bane = 0
        extra.append(t("calc.damage.excl_sharp", lang))
    elif smite > 0 and bane > 0:
        bane = 0
        extra.append(t("calc.damage.excl_smite", lang))

    base = attack
    for _ in range(max(strength, 0)):
        base = base * 1.3 + 1
    for _ in range(max(weakness, 0)):
        base = base * 0.8 - 0.5
    base = min(max(base, 0.0), 2048.0)

    bonus = (0.5 * sharp + 0.5) if sharp > 0 else 0.0
    bonus += 2.5 * smite
    bonus += 2.5 * bane
    bonus = math.floor(bonus)
    if smite:
        extra.append(t("calc.damage.smite_only", lang))
    if bane:
        extra.append(t("calc.damage.bane_only", lang))

    # 充能不到 9 成打不出暴击——游戏里也是这条线
    crit = want_crit and charge >= 0.9
    if want_crit and not crit:
        extra.append(t("calc.damage.crit_low", lang))
    final = base * (0.2 + 0.8 * charge * charge) * (1.5 if crit else 1.0) + bonus * charge

    points = lambda n: t("calc.damage.points", lang, n=n)      # noqa: E731
    rows = [(t("calc.dmg.attack", lang), points(_f(attack)))]
    if strength or weakness:
        rows.append((t("calc.damage.after_effects", lang), points(_f(base))))
    rows.append((t("calc.damage.enchant_bonus", lang),
                 ("+" if bonus else "") + points(_f(bonus))))
    if charge < 1:
        rows.append((t("calc.dmg.charge", lang), _f(charge * 100) + "%"))
    rows.append((t("calc.dmg.crit", lang),
                 t("calc.damage.crit_yes" if crit else "calc.damage.crit_no", lang)))
    rows.append((t("calc.damage.final", lang),
                 t("calc.damage.final_value", lang, n=_f(final), hearts=_f(final / 2))))
    # 说明只占一行，前面的提醒都并进来，别在结果底下摞一串「说明」
    extra.append(t("calc.damage.note_value", lang))
    rows.append((t("calc.note", lang), "".join(extra)))
    return rows


def _wiki_icon(name: str) -> str:
    """Wiki 上同名 120px 缩略图的地址。

    文件名里的括号得写成 ``%28``/``%29``，不然 Wiki 直接 400。这只是**兜底**
    地址——Wiki 会按 UA 拦非浏览器请求，正常情况下用的是本地镜像那个文件。
    """
    return "https://zh.minecraft.wiki/images/thumb/" + name + "/120px-" + name


# icon 取自 Wiki 计算器页各工具自己那个图标，和 id 一一对应。
# 名字、Wiki 页开头那句原话（info）、以及输入说明（hint）都在 i18n 表里，
# 键是 ``calc.<id>.name`` / ``.info`` / ``.hint``——以前它们写死在这儿，
# 切语言切不动。
CALCS = [
    {"id": "damage", "icon": _wiki_icon("Strength_JE3_BE2.png"), "run": _calc_damage},
    {"id": "nether", "icon": _wiki_icon("Netherrack_JE6_BE2.png"), "run": _calc_nether},
    {"id": "chunk", "icon": _wiki_icon("Chunk.png"), "run": _calc_chunk},
    {"id": "distance", "icon": _wiki_icon("Map.png"), "run": _calc_distance},
    {"id": "stronghold", "icon": _wiki_icon("Eye_of_Ender_JE2_BE2.png"), "run": _calc_stronghold},
    {"id": "tick", "icon": _wiki_icon("Clock_JE2_BE2.png"), "run": _calc_tick},
    {"id": "exp", "icon": _wiki_icon("Experience_Orb_Value_17-36.png"), "run": _calc_exp},
    {"id": "armor", "icon": _wiki_icon("Iron_Chestplate_%28item%29_JE2_BE2.png"), "run": _calc_armor},
    {"id": "stack", "icon": _wiki_icon("Bundle_JE3_BE2.png"), "run": _calc_stack},
    {"id": "color", "icon": _wiki_icon("Baroque_%28texture%29_JE1_BE1.png"), "run": _calc_color},
    {"id": "uuid", "icon": _wiki_icon("Steve_JE5.png"), "run": _calc_uuid},
    {"id": "seed", "icon": _wiki_icon("Wheat_Seeds_JE1_BE1.png"), "run": _calc_seed},
]

CALC_BY_ID = {item["id"]: item for item in CALCS}


def calc_name(item: dict, lang: str) -> str:
    return t("calc." + item["id"] + ".name", lang)


def calc_info(item: dict, lang: str) -> str:
    return t("calc." + item["id"] + ".info", lang)


def calc_hint(item: dict, lang: str) -> str:
    """输入说明。伤害计算那条要把参数名按顺序列进去，所以单独填一次。"""
    text = t("calc." + item["id"] + ".hint", lang)
    if item["id"] == "damage":
        text = text.replace("{params}", t("calc.param_joiner", lang)
                            .join(damage_param_names(lang)))
    return text


# ============ 页面 ============

# 输入框里的提示统一成这一句：只说分隔符，不说该填什么（那个写在框上面的正文里）
SEPARATOR_HINT = "仅支持逗号作分割（英文中文都可以）"


def separator_hint(lang: str) -> str:
    return t("calc.separator_hint", lang)

# 和首页"功能网站"那排列表项同一个样式（见 sites.ITEM_TEMPLATE），
# 差别只在点击走的是「打开帮助」——翻开计算器自己的页面，不是开浏览器。
CALC_ITEM_TEMPLATE = ('<local:MyListItem Margin="-5,0,-5,4" Type="Clickable" '
                      'Logo="{{LOGO|escape}}" Title="{{TITLE|escape}}" '
                      'Info="{{INFO|escape}}" EventType="打开帮助" '
                      'EventData="{{URL|escape}}" />')


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


def _calc_logo(base: str, item: dict) -> str:
    """列表项图标：本地镜像优先，其次 Wiki 原地址，最后 PCL 内置占位图。

    MyListItem 的 Logo 收的是图片地址或文件路径（不像 MyIconTextButton 能收
    SVG Path），所以一定得给个真图片；都没有时退回 PCL 自带的 pack:// 图，
    显示成"无图标"但不会破版。
    """
    mirror = ICONS_DIR / ("calc-" + item["id"] + ".png")
    if mirror.is_file() and mirror.stat().st_size > 0:
        return base + "/images/icons/" + mirror.name
    return str(item.get("icon") or PACK_FALLBACK_IMAGE)


def _calc_items(base: str, lang: str) -> str:
    """计算器入口：一行一个列表项，和首页"功能网站"那排同款。"""
    blocks = []
    for item in CALCS:
        blocks.append(render_template(CALC_ITEM_TEMPLATE, {
            "LOGO": _calc_logo(base, item),
            "TITLE": calc_name(item, lang),
            "INFO": calc_info(item, lang),
            "URL": base + "/calc_" + item["id"] + ".json",
        }))
    return indent_block("\n".join(blocks), 12)


def build_landing(base_url: str, lang: str = DEFAULT_LANG) -> str:
    """计算器首页：列出所有能算的。"""
    base = url_attr(base_url)
    return (
        '<local:MyCard Title="' + escape_attr(t("calc.title", lang)) + '" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'

        '<TextBlock Text="' + escape_attr(t("calc.landing_title", lang)) + '" FontSize="24" '
        'FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />'
        + note(t("calc.landing_desc", lang), "0,10,0,0")

        + '<TextBlock Text="' + escape_attr(t("calc.pick", lang)) + '" FontSize="13" '
        'FontWeight="Bold" Margin="0,18,0,10" '
        'Foreground="{DynamicResource ColorBrush1}" />'
        + _calc_items(base, lang)

        + nav_row(base, lang=lang)

        + "</StackPanel>"
        + "</local:MyCard>")


def build_calc_page(item: dict, raw: str, base_url: str, lang: str = DEFAULT_LANG) -> str:
    """单个计算器。带上 ``?q=`` 时就地算出结果。"""
    base = url_attr(base_url)
    name = calc_name(item, lang)
    rows, problem = None, ""
    if (raw or "").strip():
        try:
            rows = item["run"](raw, lang)
        except ValueError as exc:
            problem = str(exc)
        except Exception as exc:                       # 别让一个算错把整页带崩
            warn("[Calc] " + item["id"] + " 计算失败：" + repr(exc))
            problem = t("calc.err.unknown", lang)

    if problem:
        result = ('<local:MyHint Theme="Yellow" Margin="0,16,0,0" Text="'
                  + attr(problem) + '" />')
    elif rows:
        result = ('<StackPanel Margin="0,16,0,0">'
                  '<TextBlock Text="' + escape_attr(t("calc.result", lang)) + '" FontSize="14" '
                  'FontWeight="Bold" '
                  'Foreground="{DynamicResource ColorBrush1}" Margin="0,0,0,10" />'
                  '<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" '
                  'Padding="16,14">' + _rows_block(rows) + "</Border>"
                  "</StackPanel>")
    else:
        result = ''

    return (
        '<local:MyCard Title="' + escape_attr(name) + '" CanSwap="False">'
        '<StackPanel Margin="25,40,23,20">'

        '<TextBlock Text="' + escape_attr(name) + '" FontSize="24" FontWeight="Bold" '
        'Foreground="#FF000000" HorizontalAlignment="Center" />'
        + note(calc_info(item, lang), "0,10,0,0")
        # 该填什么、按什么顺序，写成正文；输入框里的提示统一只说分隔符
        + note(calc_hint(item, lang), "0,12,0,0")
        + input_row("calcinput", separator_hint(lang), 40, "0,8,0,0")
        + help_button(t("calc.submit", lang), ICON_CALC,
                      bind_to("calcinput", base, "/calc_" + item["id"] + ".json"),
                      44, margin="0,10,0,0")

        + result

        + nav_row(base, lang=lang, extra_text=t("calc.other", lang),
                  extra_url=base + "/calc.json")

        + "</StackPanel>"
        + "</local:MyCard>")


# ============ 路由 ============

CALC_PATHS = {"/calc.json", "/calc.xaml"}
for _item in CALCS:
    CALC_PATHS.add("/calc_" + _item["id"] + ".json")
    CALC_PATHS.add("/calc_" + _item["id"] + ".xaml")

# 值改成 i18n 键，请求期按访客语言取
_META = {"/calc.json": ("calc.title", "calc.meta_desc")}
for _item in CALCS:
    _META["/calc_" + _item["id"] + ".json"] = ("calc." + _item["id"] + ".name",
                                               "calc." + _item["id"] + ".info")


def _json_response(body: str):
    from .server import Response
    return Response.text(body, content_type="application/json; charset=utf-8",
                         headers={"Cache-Control": "no-store"})


def _xaml_response(body: str):
    from .server import Response
    return Response.xaml(body)


def handle(service, path: str, query: str, ip: str, origin: str = ""):
    """处理 ``/calc*`` 请求；不是我们的路径就返回 None。"""
    from . import profiles
    lang = profiles.lang_for(ip)

    if path in _META:
        title_key, desc_key = _META[path]
        return _json_response(json_meta(t(title_key, lang), t(desc_key, lang)))

    base = service.config.resolved_base_url(origin)

    # 列表页单独判，别塞进下面那个正则（/calc.xaml 里没有下划线，匹配不上）
    if path == "/calc.xaml":
        page = build_landing(base, lang)
        return _xaml_response(palette.apply(page, profiles.PROFILES.palette_for(ip)))

    match = re.match(r"^/calc_([a-z]+)\.xaml$", path)
    if not match:
        return None
    item = CALC_BY_ID.get(match.group(1))
    if item is None:
        return None

    params = parse_query(query)
    page = build_calc_page(item, params.get("q", ""), base, lang)
    return _xaml_response(palette.apply(page, profiles.PROFILES.palette_for(ip)))


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
