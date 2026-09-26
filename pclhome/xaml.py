# -*- coding: utf-8 -*-
"""XAML 转义与小组件构建。

对应上游 functions/_lib/xaml.js + functions/_lib/banner.js。

转义统一走 ``escape_attr``：属性值里除了 ``& < > " '``，大括号也要转成字符引用。

大括号必须处理：XAML 里属性值以 ``{`` 开头会被当成标记扩展（``{DynamicResource}``、
``{variable:}``、``{user}``），后台可配置的文案里一旦出现花括号就会让整页白屏。
上游 generate.py 的 ``escape_xaml_attr`` 也做了这件事，这里统一成同一个函数。
"""
from __future__ import annotations

import html

def escape_attr(value) -> str:
    """属性值转义：``& < > " '`` 之外，还要把 ``{ }`` 变成字符引用。"""
    out = html.escape(str(value), quote=True)
    return out.replace("{", "&#123;").replace("}", "&#125;")


def escape_url_attr(value) -> str:
    """URL 用的属性转义：只处理 ``& < > "``。

    **不能动大括号**——``{Binding}`` 这类标记扩展要靠花括号工作。
    """
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ============ 图标（SVG path，坐标系 0..1024，与 PCL 自带图标一致）============
#
# 只用直线段和圆弧拼，且各子路径互不重叠——WPF 的 Path 默认按 EvenOdd 填充，
# 两块图形一旦重叠，重叠处会被挖成洞。

ICON_AI = "M512 96 L608 416 L928 512 L608 608 L512 928 L416 608 L96 512 L416 416 Z"
ICON_KEY = ("M112 288 A176 176 0 1 0 464 288 A176 176 0 1 0 112 288 Z "
            "M464 256 H896 V320 H464 Z "
            "M768 320 H832 V448 H768 Z M864 320 H928 V416 H864 Z")
ICON_SAVE = "M480 96 H544 V480 H768 L512 832 L256 480 H480 Z"
ICON_TRASH = (
    "M765.5 191.9 639.6 191.9c0-35.3-28.6-64-64-64L447.7 128c-35.3 0-64 28.6-64 64"
    "L257.9 191.9c-36.5 0-66 29.6-66 66l0 59.9c0 36.5 29.6 66 66 66l-2 0 0 445.7"
    "c0 36.5 29.6 66 66 66l61.9 0 64 0 127.9 0 64 0 61.9 0c36.5 0 66-29.6 66-66"
    "L767.5 383.8l-2 0c36.5 0 66-29.6 66-66l0-59.9C831.5 221.5 802 191.9 765.5 191.9z"
    "M703.6 803.4c-0.1 3.1-1.7 27.8-28 28.1l-36 0-64 0L447.7 831.5 383.8 831.5l-35.9 0"
    "c-28-0.3-28-28.5-28-28.5l-0.1 0L319.8 383.8l383.8 0L703.6 803.4z"
    "M735.6 319.9 287.8 319.9c-17.7 0-32-14.3-32-32 0-17.7 14.3-32 32-32l159.9 0 127.9 0"
    " 159.9 0c17.7 0 32 14.3 32 32C767.5 305.5 753.2 319.9 735.6 319.9z")

# 欢迎卡片底部那排功能按钮（除 AI 外都是 PCL 内置事件）
ICON_REFRESH = ("M753 271 C691 209 606 171 512 171 c-189 0 -341 153 -341 341 s152 341 341 341 "
                "c159 0 292 -109 330 -256 h-89 c-35 99 -130 171 -241 171 c-141 0 -256 -115 -256 -256 "
                "s115 -256 256 -256 c71 0 134 29 180 76 L555 469 h299 V171 l-100 100 Z")
# 房子：屋顶 + 两堵墙，底边中间留出门口（单条子路径，不会触发 EvenOdd 挖空）
ICON_HOME = "M512 96 L960 480 H832 V928 H640 V672 H384 V928 H192 V480 H64 Z"
# 左箭头：箭尖在 (160,512)，上下倒钩接回箭杆
ICON_BACK = "M896 416 H512 V160 L160 512 L512 864 V608 H896 Z"
# 计算器：机身 + 屏幕和按键。这几块是**故意**套在机身里的——EvenOdd 会把它们挖成洞，
# 正好是描边效果；别改成互不重叠，那样就变成一个实心方块了。
ICON_CALC = ("M256 96 H768 A64 64 0 0 1 832 160 V864 A64 64 0 0 1 768 928 H256 "
             "A64 64 0 0 1 192 864 V160 A64 64 0 0 1 256 96 Z "
             "M288 192 H736 V400 H288 Z "
             "M320 512 H448 V640 H320 Z M576 512 H704 V640 H576 Z "
             "M320 720 H448 V816 H320 Z M576 720 H704 V816 H576 Z")

_ACTION_BUTTONS = (
    ("内存优化", "内存优化", "-", "M128 192h768v192H128z M128 448h768v192H128z M256 224v128 M256 480v128"),
    ("刷新数据", "刷新页面", "-",
     "M753 271 C691 209 606 171 512 171 c-189 0 -341 153 -341 341 s152 341 341 341 c159 0 292 -109 330 -256 "
     "h-89 c-35 99 -130 171 -241 171 c-141 0 -256 -115 -256 -256 s115 -256 256 -256 c71 0 134 29 180 76 "
     "L555 469 h299 V171 l-100 100 Z"),
)


def build_action_buttons(config) -> str:
    """欢迎卡片底部那排功能按钮，启用 AI 时多一个「AI 分析」入口。

    AI 按钮和计算器按钮的 EventData 都留成 ``__XXX_ENTRY__`` 占位符，由请求期
    填绝对地址：部署时 BASE_URL 常常没配（按 Host 头推导），构建期并不知道对外地址。

    计算器不依赖任何开关，一直有；AI 那个只在 enable_ai 时加。
    """
    buttons = list(_ACTION_BUTTONS)
    if getattr(config, "enable_ai", False):
        buttons.append(("AI 分析", "打开帮助", "__AI_ENTRY__", ICON_AI))
    buttons.append(("计算器", "打开帮助", "__CALC_ENTRY__", ICON_CALC))

    count = len(buttons)
    columns = "".join('<ColumnDefinition Width="1*" />' for _ in range(count))
    cells = []
    for index, (text, event_type, event_data, logo) in enumerate(buttons):
        left = "0" if index == 0 else "4"
        right = "0" if index == count - 1 else "4"
        cells.append('<local:MyIconTextButton Grid.Column="' + str(index) + '" Margin="'
                     + left + ",0," + right + ',0" Height="48" Text="' + escape_attr(text)
                     + '" LogoScale="0.9" ColorType="Highlight" Logo="' + logo
                     + '" EventType="' + escape_attr(event_type)
                     + '" EventData="' + escape_url_attr(event_data) + '" />')
    return ('<Grid Margin="0,12,0,0"><Grid.ColumnDefinitions>' + columns
            + "</Grid.ColumnDefinitions>" + "".join(cells) + "</Grid>")


# ============ 页面零件（AI 页和计算器页共用）============

def attr(value) -> str:
    """属性值转义；换行写成字符引用，否则 XAML 会把属性值截断在第一个换行。"""
    return escape_attr(value).replace("\n", "&#xA;").replace("\r", "")


def url_attr(value) -> str:
    """URL 用的属性转义（保留花括号，见 escape_url_attr）。"""
    return escape_url_attr(value)


def bind_to(element: str, base: str, endpoint: str) -> str:
    """生成 EventData：把某个输入框的原文拼到接口地址后面。

    ``{}`` 是 WPF StringFormat 的转义前缀，``{0}`` 才是绑定值。PCL 把输入框
    原文原样替换进 URL、不做编码，所以框里只能放不含空白字符的短内容。
    """
    return ("{Binding Path=Text,ElementName=" + element + ",StringFormat='{}"
            + base + endpoint + "?q={0}'}")


def input_row(name: str, hint: str, height: int = 38, margin: str = "0,8,0,0") -> str:
    """一个带底色的输入框。"""
    return ('<Border Margin="' + margin + '" Height="' + str(height)
            + '" Background="{DynamicResource ColorBrush7}" CornerRadius="5">'
            '<local:MyTextBox x:Name="' + name + '" Height="' + str(height)
            + '" Margin="10,0" HintText="' + attr(hint) + '" '
            'Foreground="{DynamicResource ColorBrush2}" VerticalAlignment="Center" /></Border>')


def help_button(text: str, logo: str, url: str, height: int = 38, column=None,
                margin: str = "", color: str = "Highlight") -> str:
    """走「打开帮助」的按钮——PCL 会去拉 ``url`` 对应的 .json/.xaml 并翻开新页。"""
    attrs = ""
    if column is not None:
        attrs += ' Grid.Column="' + str(column) + '"'
    if margin:
        attrs += ' Margin="' + margin + '"'
    if color:
        attrs += ' ColorType="' + color + '"'
    return ('<local:MyIconTextButton' + attrs + ' Height="' + str(height) + '" Text="'
            + escape_attr(text) + '" LogoScale="0.8" Logo="' + logo
            + '" EventType="打开帮助" EventData="' + url_attr(url) + '" />')


def help_button_multi(text: str, logo: str, url_prefix: str, fields: list,
                      height: int = 38, margin: str = "", logo_scale: str = "0.8") -> str:
    """走「打开帮助」的按钮，EventData 拿好几个输入框的值拼成 ``?q=a,b,c``。

    一个 ``{Binding}`` 只能带一个控件的值，要凑几个框就得上 ``MultiBinding``。
    它在 XAML 里没有属性写法，只能写成属性元素，所以这个按钮不能自闭合。
    ``StringFormat`` 开头那个 ``{}`` 是 XAML 的转义前缀，不写的话 WPF 会把
    ``{0}`` 当成标记扩展解析。绑定值的编号从 ``{1}`` 起，``{0}`` 留给前缀。
    """
    fmt = "{}" + url_prefix + "?q=" + ",".join(
        "{" + str(index + 1) + "}" for index in range(len(fields)))
    binds = "".join('<Binding Path="Text" ElementName="' + name + '" />' for name in fields)
    attrs = ' Margin="' + margin + '"' if margin else ""
    return ('<local:MyIconTextButton' + attrs + ' Height="' + str(height) + '" Text="'
            + escape_attr(text) + '" LogoScale="' + logo_scale + '" Logo="' + logo
            + '" EventType="打开帮助">'
            "<local:MyIconTextButton.EventData>"
            '<MultiBinding StringFormat="' + escape_url_attr(fmt) + '">'
            + binds + "</MultiBinding>"
            "</local:MyIconTextButton.EventData></local:MyIconTextButton>")


def grid2(left: str, right: str, margin: str = "0,8,0,0") -> str:
    """左右等宽两栏，各自放一个已经带好 Grid.Column 的按钮。"""
    return ('<Grid Margin="' + margin + '"><Grid.ColumnDefinitions>'
            '<ColumnDefinition Width="1*" /><ColumnDefinition Width="1*" />'
            "</Grid.ColumnDefinitions>" + left + right + "</Grid>")


def divider(margin: str = "0,20,0,16") -> str:
    """中间那道渐隐的横线。"""
    return ('<Border Height="1" Margin="' + margin + '"><Border.Background>'
            '<LinearGradientBrush StartPoint="0,0" EndPoint="1,0">'
            '<GradientStop Color="#00000000" Offset="0" />'
            '<GradientStop Color="#33808080" Offset="0.5" />'
            '<GradientStop Color="#00000000" Offset="1" />'
            "</LinearGradientBrush></Border.Background></Border>")


def heading(text: str, margin: str = "0,16,0,0") -> str:
    return ('<TextBlock Text="' + escape_attr(text) + '" FontSize="13" FontWeight="Bold" '
            'Foreground="{DynamicResource ColorBrush1}" Margin="' + margin + '" />')


def note(text: str, margin: str = "0,8,0,0") -> str:
    return ('<TextBlock Text="' + attr(text) + '" FontSize="11" TextWrapping="Wrap" '
            'Margin="' + margin + '" Foreground="{DynamicResource ColorBrush3}" />')


def nav_row(base: str, refresh_url: str = "", extra_text: str = "",
            extra_url: str = "") -> str:
    """页面底部那排：刷新 / 回到某页 / 返回主页。

    「返回主页」专门治「进得太深、左上角要按好几下」：它直接再翻开一份主页
    （见 ai.handle 里的 ``home`` 动作），一下就到，不用沿路往回退。
    """
    items = []
    if refresh_url:
        items.append(("刷新结果", ICON_REFRESH, refresh_url))
    if extra_url:
        items.append((extra_text, ICON_BACK, extra_url))
    items.append(("返回主页", ICON_HOME, base + "/home.json"))
    cells = [help_button(text, logo, url, 36, margin=("8,0,0,0" if index else ""))
             for index, (text, logo, url) in enumerate(items)]
    return ('<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" '
            'Margin="0,18,0,0">' + "".join(cells) + "</StackPanel>")


REFRESH_BUTTON = (
    '<local:MyIconTextButton Margin="0,24,0,0" Height="40" HorizontalAlignment="Center" Text="刷新页面" '
    'LogoScale="0.9" ColorType="Highlight" '
    'Logo="M512 128a384 384 0 1 1 0 768 384 384 0 0 1 0-768z M512 192a320 320 0 1 0 0 640 320 320 0 0 0 0-640z '
    'M480 288h64v208l144 88-32 56-176-104V288z" EventType="刷新页面" EventData="-" />'
)


def build_fallback_xaml(title: str, message: str, eta: str = "", reason: str = "") -> str:
    """兜底页：封禁/拒绝 → 简洁提示；服务器维护 → 带旋转动画的加载页。

    对应上游 buildFallbackXaml。任何组装异常都回退到这里，
    保证 PCL 永远不会拿到一页空白。
    """
    if title != "服务器正在更新":
        return ("<StackPanel>"
                '<local:MyCard Title="" Margin="0,0,0,12">'
                '<StackPanel Margin="30,40,30,32">'
                '<Grid Width="64" Height="64" HorizontalAlignment="Center">'
                '<Ellipse Width="64" Height="64" Fill="#FFE5484D"/>'
                '<Path Data="M20,20 L44,44" Stroke="#FFFFFFFF" StrokeThickness="8" '
                'StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>'
                "</Grid>"
                '<TextBlock Text="' + escape_attr(title) + '" FontSize="20" FontWeight="Bold" '
                'Foreground="{DynamicResource ColorBrush1}" TextAlignment="Center" HorizontalAlignment="Center" Margin="0,18,0,0"/>'
                '<TextBlock Text="' + escape_attr(message) + '" FontSize="15" Foreground="{DynamicResource ColorBrush3}" '
                'TextAlignment="Center" TextWrapping="Wrap" MaxWidth="420" HorizontalAlignment="Center" LineHeight="24" Margin="0,10,0,0"/>'
                + REFRESH_BUTTON +
                "</StackPanel>"
                "</local:MyCard>"
                "</StackPanel>")

    status_text = "短暂的等待，是为了之后更长久的顺畅，感谢您的耐心。"
    spinner = ('<Grid Width="64" Height="64" HorizontalAlignment="Center">'
               '<Ellipse Width="64" Height="64" Stroke="#22000000" StrokeThickness="5"/>'
               '<Ellipse Width="64" Height="64" Stroke="#FF4C8DFF" StrokeThickness="5" StrokeDashArray="1.4,100" '
               'StrokeDashCap="Round" RenderTransformOrigin="0.5,0.5">'
               '<Ellipse.RenderTransform><RotateTransform x:Name="spin" Angle="0"/></Ellipse.RenderTransform>'
               '<Ellipse.Triggers><EventTrigger RoutedEvent="Ellipse.Loaded"><BeginStoryboard><Storyboard RepeatBehavior="Forever">'
               '<DoubleAnimation Storyboard.TargetName="spin" Storyboard.TargetProperty="Angle" From="0" To="360" Duration="0:0:1.1"/>'
               "</Storyboard></BeginStoryboard></EventTrigger></Ellipse.Triggers>"
               "</Ellipse>"
               "</Grid>")

    eta_line = ""
    if eta and eta != "0":
        eta_line = ('<TextBlock Text="预计 ' + escape_attr(eta) + ' 更新完成" FontSize="15" '
                    'Foreground="{DynamicResource ColorBrush3}" TextAlignment="Center" '
                    'HorizontalAlignment="Center" Margin="0,16,0,0"/>')
    reason_line = ""
    if reason and reason.strip():
        reason_line = ('<TextBlock Text="原因：' + escape_attr(reason) + '" FontSize="12" '
                       'Foreground="{DynamicResource ColorBrush2}" TextAlignment="Center" TextWrapping="Wrap" '
                       'MaxWidth="440" HorizontalAlignment="Center" LineHeight="22" Margin="0,8,0,0"/>')

    return ("<StackPanel>"
            '<local:MyCard Title="" Margin="0,0,0,12">'
            '<StackPanel Margin="30,40,30,32">'
            + spinner +
            '<TextBlock Text="服务器正在更新" FontSize="20" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" '
            'TextAlignment="Center" HorizontalAlignment="Center" Margin="0,20,0,0"/>'
            '<TextBlock Text="' + status_text + '" FontSize="15" Foreground="{DynamicResource ColorBrush3}" '
            'TextAlignment="Center" TextWrapping="Wrap" MaxWidth="440" HorizontalAlignment="Center" LineHeight="26" Margin="0,18,0,0"/>'
            + reason_line + eta_line + REFRESH_BUTTON +
            '<local:MyHint Theme="Yellow" Margin="0,18,0,0" Text="' + escape_attr(message) + '" />'
            '<local:MyHint Theme="Blue" Margin="0,10,0,0" Text="如果一直看到这个页面，请去 GitHub 提 Issue。" />'
            "</StackPanel>"
            "</local:MyCard>"
            "</StackPanel>")


# ============ 公告 ============

def build_multi_banner(raw: str | None) -> str:
    """多公告轮播（每行一条，≥2 条才启用，最多 4 条，每条显示 8 秒）。"""
    if not raw:
        return ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) < 2:
        return ""
    count = min(len(lines), 4)
    per = 8
    anims, texts = [], []
    for i in range(count):
        on_at, off_at = i * per, (i + 1) * per - 1
        texts.append('<TextBlock x:Name="mb' + str(i) + '" Text="' + escape_attr(lines[i]) + '" FontSize="15" '
                     'LineHeight="24" Foreground="{DynamicResource ColorBrush1}" VerticalAlignment="Center" Opacity="0"/>')
        anims.append('<DoubleAnimation Storyboard.TargetName="mb' + str(i) + '" Storyboard.TargetProperty="Opacity" '
                     'From="0" To="1" Duration="0:0:1" BeginTime="0:0:' + str(on_at) + '"/>')
        anims.append('<DoubleAnimation Storyboard.TargetName="mb' + str(i) + '" Storyboard.TargetProperty="Opacity" '
                     'From="1" To="0" Duration="0:0:1" BeginTime="0:0:' + str(off_at) + '"/>')
    return ('<local:MyCard Title="公告" Margin="0,0,0,12" CanSwap="True" IsSwapped="False">'
            '<StackPanel Margin="25,36,23,16" ClipToBounds="True" Height="28">'
            "<Grid>"
            '<Grid.Triggers><EventTrigger RoutedEvent="FrameworkElement.Loaded"><BeginStoryboard>'
            '<Storyboard RepeatBehavior="Forever">' + "".join(anims) + "</Storyboard>"
            "</BeginStoryboard></EventTrigger></Grid.Triggers>"
            + "".join(texts) +
            "</Grid>"
            "</StackPanel>"
            "</local:MyCard>")


def build_single_banner(banner: dict | None) -> str:
    """单条公告：≤40 字淡入，超长走跑马灯。"""
    if not banner or not banner.get("enabled"):
        return ""
    text = str(banner.get("text") or "").strip()
    if not text:
        return ""
    safe = escape_attr(text)
    if len(text) <= 40:
        return ('<local:MyCard Title="公告" Margin="0,0,0,12" CanSwap="True" IsSwapped="False">'
                '<StackPanel Margin="25,36,23,16">'
                '<StackPanel.Triggers><EventTrigger RoutedEvent="FrameworkElement.Loaded"><BeginStoryboard><Storyboard>'
                '<DoubleAnimation Storyboard.TargetName="bqFade" Storyboard.TargetProperty="Opacity" From="0" To="1" Duration="0:0:0.6"/>'
                "</Storyboard></BeginStoryboard></EventTrigger></StackPanel.Triggers>"
                '<TextBlock x:Name="bqFade" TextWrapping="Wrap" FontSize="15" LineHeight="24" '
                'Foreground="{DynamicResource ColorBrush1}" Text="' + safe + '"/>'
                "</StackPanel>"
                "</local:MyCard>")

    text_width = len(text) * 15
    to_x = -(text_width + 40)
    duration = max(8, min(30, round((500 - to_x) / 50)))
    return ('<local:MyCard Title="公告" Margin="0,0,0,12" CanSwap="True" IsSwapped="False">'
            '<StackPanel Margin="25,36,23,16" ClipToBounds="True" Height="28">'
            '<TextBlock Text="' + safe + '" FontSize="15" LineHeight="24" Foreground="{DynamicResource ColorBrush1}" VerticalAlignment="Center">'
            '<TextBlock.RenderTransform><TranslateTransform x:Name="bqMarquee" X="0"/></TextBlock.RenderTransform>'
            '<TextBlock.Triggers><EventTrigger RoutedEvent="FrameworkElement.Loaded"><BeginStoryboard><Storyboard RepeatBehavior="Forever">'
            '<DoubleAnimation Storyboard.TargetName="bqMarquee" Storyboard.TargetProperty="X" From="500" To="' + str(to_x) + '" Duration="0:0:' + str(duration) + '"/>'
            "</Storyboard></BeginStoryboard></EventTrigger></TextBlock.Triggers>"
            "</TextBlock>"
            "</StackPanel>"
            "</local:MyCard>")


# ============ 模板渲染（构建期用）============

import re

_TOKEN_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)(?:\|(escape|url))?\}\}")


def render_template(text: str, values: dict) -> str:
    """替换 ``{{TOKEN}}`` / ``{{TOKEN|escape}}`` / ``{{TOKEN|url}}``。

    运行时占位符（``__XXX__``）原样保留，交给请求期替换。
    """
    def repl(match):
        key, mode = match.group(1), match.group(2)
        if key not in values:
            raise KeyError("模板缺少取值：" + key)
        value = str(values[key])
        if mode == "escape":
            return escape_attr(value)
        if mode == "url":
            return html.escape(value, quote=True)
        return value

    return _TOKEN_RE.sub(repl, text)


def indent_block(block: str, spaces: int) -> str:
    """整段加统一缩进，空行不加。"""
    pad = " " * spaces
    return "\n".join((pad + line) if line.strip() else line for line in block.split("\n"))
