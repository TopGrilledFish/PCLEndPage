"""PCL-CE（PCL2 社区版）的 AXAML 主页支持。

PCL-CE 在 User-Agent 里带 ``PCLCE/``；它按**内容**而不是扩展名判断主页格式：
内容里出现 Avalonia 命名空间（``https://github.com/avaloniaui``）或
``pclcontrols:`` / ``pclhome:`` 前缀时，走 Avalonia 渲染器（AXAML），
否则按旧 WPF XAML 走兼容渲染器。

这里把我们已经渲染好的 WPF 主页 XAML 转成 PCL-CE 能直接加载的 AXAML：

- 根 ``StackPanel`` 换成 Avalonia 默认命名空间 + ``x`` + ``pclcontrols``；
- ``local:`` 前缀换成 ``pclcontrols:``（PCL.UI.Controls 命名空间）；
- 去掉根上的 ``StackPanel.Resources`` 字体样式块——Avalonia 的 Style 用
  ``Selector``，WPF 的 ``TargetType`` 写法会解析失败，字体交给 PCL-CE 全局样式；
- 去掉按钮上的几何 ``Logo`` 路径：PCL-CE 的 AXAML 校验器把 Logo 当图片地址，
  只接受 http/https/avares/pack 等，``M192 448 ...`` 会被判为非法资源地址。

转换后用 :func:`validate` 按 PCL-CE 的 ``AvaloniaXamlValidator`` 规则再查一遍，
不通过就退回 WPF XAML——宁可 PCL-CE 走兼容渲染器，也不要给它一份必炸的 AXAML。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

PCLCE_MARK = "pclce/"

AVALONIA_NS = "https://github.com/avaloniaui"
XAML_NS = "http://schemas.microsoft.com/winfx/2006/xaml"

_ROOT_TAG_RE = re.compile(r"<StackPanel\b")
_RESOURCES_RE = re.compile(r"\s*<StackPanel\.Resources>.*?</StackPanel\.Resources>", re.S)
_TRIGGERS_RE = re.compile(r"\s*<([A-Za-z][\w.]*)\.Triggers>.*?</\1\.Triggers>", re.S)
_EFFECT_RE = re.compile(r"\s*<([A-Za-z][\w.]*)\.Effect>.*?</\1\.Effect>", re.S)
_LOCAL_PREFIX_RE = re.compile(r"(</?)local:")
_GEOM_LOGO_RE = re.compile(r'\s+Logo="(?!https?://|avares://|pack://)[^"]*"', re.I)
_LOGO_SCALE_RE = re.compile(r'\s+LogoScale="[^"]*"')
# PCL-CE 的 AXAML 校验器只允许 DynamicResource/StaticResource/TemplateBinding，
# 旧版页面里「输入框 Text 拼进 EventData」的 {Binding} 在 AXAML 下必须去掉：
# 这里把 StringFormat 里的静态 URL 提出来（保留功能入口，丢掉输入参数）。
_BINDING_EVENTDATA_RE = re.compile(
    r'EventData="\{Binding[^"]*?StringFormat=\'\{\}(.*?)\{0\}\'[^"]*?\}"'
)
_ANY_BINDING_EVENTDATA_RE = re.compile(r'EventData="\{[^"]*?\}"')
# PCL-CE 明确不支持的 EventType（EventTypeMapper.UnsupportedTypes）：AXAML 加载器
# 会把中文事件名直接塞进 enum 属性而抛 InvalidOperationException。WPF 渲染器对
# 这类控件是「跳过」，AXAML 里改成禁用按钮，视觉上等价。
_UNSUPPORTED_EVENTTYPE_RE = re.compile(r'EventType="(?:内存优化|加入房间|检查更新)"')

_FORBIDDEN_ELEMENTS = {
    "window", "toplevel", "application", "webview", "nativewebview",
    "objectdataprovider", "xmldataprovider", "xamlreader",
}
_FORBIDDEN_ATTRS = {
    "click", "doubleclick", "rightclick", "mousedown", "mouseup",
    "pointerpressed", "pointerreleased", "pointermoved", "tapped",
    "doubletapped", "keydown", "keyup", "textinput", "loaded", "unloaded",
    "textchanged", "selectionchanged", "checked", "unchecked", "command",
    "commandparameter", "datacontext", "datacontextchanged",
}
_MARKUP_RE = re.compile(r"^\{([A-Za-z_][\w.:]*)\s*[^{}]*\}$")
_ALLOWED_NAMESPACE_PREFIXES = (
    "", AVALONIA_NS, "using:pcl", "using:avalonia",
    "clr-namespace:pcl", "clr-namespace:avalonia",
)


def is_pclce(user_agent: str) -> bool:
    """UA 里带 ``PCLCE/`` 的就是社区版（PCL-CE）。"""
    return PCLCE_MARK in (user_agent or "").lower()


def to_axaml(xaml: str) -> str:
    """把渲染好的 WPF 主页片段转成 AXAML；结构不像主页时原样返回。"""
    text = _RESOURCES_RE.sub("", xaml, count=1)
    # WPF 的 EventTrigger/Storyboard/BlurEffect 在 Avalonia 里没有对应类型，
    # 留在内容里会让 AvaloniaRuntimeXamlLoader 直接抛异常。
    text = _TRIGGERS_RE.sub("", text)
    text = _EFFECT_RE.sub("", text)

    text = _LOCAL_PREFIX_RE.sub(r"\1pclcontrols:", text)
    text = _GEOM_LOGO_RE.sub("", text)
    text = _LOGO_SCALE_RE.sub("", text)
    text = _BINDING_EVENTDATA_RE.sub(r'EventData="\1"', text)
    text = _ANY_BINDING_EVENTDATA_RE.sub('EventData=""', text)
    text = _UNSUPPORTED_EVENTTYPE_RE.sub('IsEnabled="False"', text)

    end = _root_open_tag_end(text)
    if end is None:
        return xaml

    attrs = (
        ' xmlns="%s"' % AVALONIA_NS
        + ' xmlns:x="%s"' % XAML_NS
        + ' xmlns:pclcontrols="using:PCL.UI.Controls"'
    )
    return text[:end] + attrs + text[end:]


_ROOT_ELEMENT_RE = re.compile(r"<([A-Za-z][\w.:-]*)")


def _root_open_tag_end(s: str) -> int | None:
    """返回根元素开标签末尾 ``>`` 的下标（跳过属性值里的 ``>``）。"""
    match = _ROOT_ELEMENT_RE.search(s)
    if not match:
        return None
    i = match.end()
    quote = ""
    while i < len(s):
        ch = s[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == ">":
            return i
        i += 1
    return None


def validate(xaml: str) -> list[str]:
    """按 PCL-CE ``AvaloniaXamlValidator`` 的规则做一遍只读检查。

    返回问题列表；空列表表示这份片段可以交给 PCL-CE 的 AXAML 渲染器。
    """
    problems: list[str] = []
    if not xaml.strip():
        return ["AXAML 内容为空"]

    wrapped = (
        '<Root xmlns="%s" xmlns:x="%s" xmlns:pclhome="using:PCL">%s</Root>'
        % (AVALONIA_NS, XAML_NS, xaml.strip())
    )
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as exc:
        return ["AXAML 解析失败：" + str(exc)]

    for element in root.iter():
        name = element.tag.split("}")[-1] if "}" in element.tag else element.tag
        if name == "Root":
            continue
        if "." in name:
            # 属性元素（StackPanel.Resources、Grid.ColumnDefinitions 等）
            continue
        if name.lower() in _FORBIDDEN_ELEMENTS:
            problems.append("不允许的元素类型：" + name)
            continue
        if not _namespace_allowed(element.tag):
            problems.append("不允许的命名空间：" + element.tag)
            continue
        for attr, value in element.attrib.items():
            problems.extend(_check_attr(attr, value))
    return problems


def _namespace_allowed(tag: str) -> bool:
    if "}" not in tag:
        return True
    namespace = tag.split("}")[0][1:]
    lowered = namespace.lower()
    return any(lowered == prefix or lowered.startswith(prefix)
               for prefix in _ALLOWED_NAMESPACE_PREFIXES if prefix)


def _check_attr(attr: str, value: str) -> list[str]:
    problems: list[str] = []
    if "}" in attr:
        namespace, local = attr.split("}", 1)
        namespace = namespace[1:]
        if namespace.lower() == XAML_NS.lower():
            if local.lower() in ("class", "static", "datatype"):
                problems.append("不允许的 XAML 属性：x:" + local)
            return problems
        if namespace and not namespace.lower().startswith("using:pcl"):
            problems.append("不允许的属性命名空间：" + attr)
            return problems
    else:
        local = attr

    lowered = local.lower()
    if lowered in _FORBIDDEN_ATTRS:
        problems.append("不允许的事件或命令属性：" + local)
    if lowered in ("source", "imagesource", "baseuri", "urisource",
                   "fallbacksource", "loadingsource", "logo"):
        if not _safe_source(value):
            problems.append("不允许的资源地址：%s=%s" % (local, value[:60]))
    if value.strip().startswith("{"):
        match = _MARKUP_RE.match(value.strip())
        if not match:
            problems.append("不允许的标记扩展：%s=%s" % (local, value[:60]))
        elif match.group(1).split(".")[-1] not in (
                "DynamicResource", "StaticResource", "TemplateBinding"):
            problems.append("不允许的标记扩展：" + match.group(1))
    return problems


def _safe_source(value: str) -> bool:
    text = value.strip()
    if text.startswith("{DynamicResource") or text.startswith("{StaticResource"):
        return True
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        return True
    if ".." in text:
        return False
    return (
        (lowered.startswith("pack://") and "/images/" in lowered)
        or text.startswith("/images/")
        or lowered.startswith("avares://pcl.launcher/assets/")
    )
