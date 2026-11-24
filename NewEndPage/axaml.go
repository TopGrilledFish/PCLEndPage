package main

// PCL-CE（社区版）的 AXAML 主页支持。
//
// PCL-CE 的 UA 里带 ``PCLCE/``，它按内容判断主页格式：内容里出现 Avalonia
// 命名空间或 ``pclcontrols:`` 前缀时走 Avalonia 渲染器（AXAML），否则按旧
// WPF XAML 走兼容渲染器。这里把已经渲染好的 WPF 主页转成 AXAML：
//
//   - 去掉根上的 StackPanel.Resources 字体样式块（Avalonia 的 Style 用 Selector）；
//   - 去掉 EventTrigger/Storyboard/BlurEffect（Avalonia 没有这些类型，留着会抛异常）；
//   - ``local:`` 前缀换成 ``pclcontrols:``（PCL.UI.Controls）；
//   - 去掉按钮上的几何 Logo 路径（PCL-CE 的校验器把 Logo 当图片地址，只接受
//     http/https/avares/pack 等）；
//   - 根 StackPanel 补上 Avalonia 默认命名空间、x 与 pclcontrols。
//
// 转换后用 axamlOK 做粗检，不通过就退回 WPF XAML——宁可让 PCL-CE 走兼容
// 渲染器，也不要给它一份必炸的 AXAML。

import (
	"regexp"
	"strings"
)

var (
	axRootRE      = regexp.MustCompile(`<([A-Za-z][\w.:-]*)`)
	axResourcesRE = regexp.MustCompile(`(?s)\s*<StackPanel\.Resources>.*?</StackPanel\.Resources>`)
	axTriggersRE  = regexp.MustCompile(`(?s)\s*<[A-Za-z][\w.]*\.Triggers>.*?</[A-Za-z][\w.]*\.Triggers>`)
	axEffectRE    = regexp.MustCompile(`(?s)\s*<[A-Za-z][\w.]*\.Effect>.*?</[A-Za-z][\w.]*\.Effect>`)
	axLocalRE     = regexp.MustCompile(`(</?)local:`)
	axLogoRE      = regexp.MustCompile(`\s+Logo="([^"]*)"`)
	axLogoScaleRE = regexp.MustCompile(`\s+LogoScale="[^"]*"`)
	// 公告卡片靠 Opacity 动画淡入；AXAML 会删掉 Triggers/Storyboard，
	// 这里把初始 Opacity="0" 改成 1，避免静态渲染后文字不可见。
	axOpacityZeroRE = regexp.MustCompile(`Opacity="0"`)
	// 几何 Logo 在 PCL-CE 的 AXAML 校验器里会被当成非法资源地址，改用它内置的
	// SVG 图标名（lucide/xxx）——AXAML 加载器对 SvgIcon 不做地址检查。
	axButtonLogoRE = regexp.MustCompile(`(?s)<pclcontrols:MyIconTextButton\b[^>]*?Logo="M[^"]*"[^>]*?/>`)
	axButtonTextRE = regexp.MustCompile(`Text="([^"]*)"`)
	// PCL-CE 的 AXAML 校验器只允许 DynamicResource/StaticResource/TemplateBinding，
	// 旧版页面里「输入框 Text 拼进 EventData」的 {Binding} 在 AXAML 下必须去掉：
	// 这里把 StringFormat 里的静态 URL 提出来（保留功能入口，丢掉输入参数）。
	axBindingEventDataRE    = regexp.MustCompile(`EventData="\{Binding[^"]*StringFormat='\{\}([^']*?)\{0\}'[^"]*\}"`)
	axAnyBindingEventDataRE = regexp.MustCompile(`EventData="\{[^"]*\}"`)
	// PCL-CE 明确不支持的 EventType（EventTypeMapper.UnsupportedTypes）：AXAML 加载器
	// 会把中文事件名直接塞进 enum 属性而抛 InvalidOperationException。WPF 渲染器对
	// 这类控件是「跳过」，AXAML 里改成禁用按钮，视觉上等价。
	axUnsupportedEventTypeRE = regexp.MustCompile(`EventType="(?:内存优化|加入房间|检查更新)"`)
	// Avalonia 的 Shape 没有 StrokeDashCap（WPF 有线帽属性），维护页 spinner 用它
	// 会直接报「Unable to resolve suitable regular or attached property」。
	axDashCapRE = regexp.MustCompile(`\s+StrokeDashCap="[^"]*"`)
	// WPF 的 Shape 有 StrokeStartLineCap/StrokeEndLineCap，Avalonia 只有
	// StrokeLineCap；残留会让加载器抛「Unable to resolve ... LineCap」。
	axLineCapRE = regexp.MustCompile(`\s+Stroke(?:Start|End)LineCap="[^"]*"`)
	// Avalonia 的 Transform 不是 Control，没有 Name 属性；WPF 模板里的
	// <RotateTransform x:Name="spin"> 在 AXAML 下无法解析，去掉名字即可。
	axTransformRE     = regexp.MustCompile(`<(?:Rotate|Translate|Scale|Skew|Matrix|TransformGroup)Transform\b[^>]*>`)
	axTransformNameRE = regexp.MustCompile(`\s+x:Name="[^"]*"`)
)

// isPCLCE 判断请求是否来自 PCL-CE（UA 里带 PCLCE/）。
func isPCLCE(userAgent string) bool {
	return strings.Contains(strings.ToLower(userAgent), "pclce/")
}

func toAXAML(xaml string) string {
	if !axRootRE.MatchString(xaml) {
		return xaml
	}
	if strings.Contains(xaml, "xmlns:pclcontrols") {
		return xaml
	}
	text := axResourcesRE.ReplaceAllString(xaml, "")
	text = axTriggersRE.ReplaceAllString(text, "")
	text = axEffectRE.ReplaceAllString(text, "")
	text = axLocalRE.ReplaceAllString(text, "${1}pclcontrols:")
	text = replaceGeomLogos(text)
	text = axLogoScaleRE.ReplaceAllString(text, "")
	text = axOpacityZeroRE.ReplaceAllString(text, `Opacity="1"`)
	text = axBindingEventDataRE.ReplaceAllString(text, `EventData="$1"`)
	text = axAnyBindingEventDataRE.ReplaceAllString(text, `EventData=""`)
	text = axUnsupportedEventTypeRE.ReplaceAllString(text, `IsEnabled="False"`)
	text = axDashCapRE.ReplaceAllString(text, "")
	text = axLineCapRE.ReplaceAllString(text, "")
	text = axTransformRE.ReplaceAllStringFunc(text, func(m string) string {
		return axTransformNameRE.ReplaceAllString(m, "")
	})
	idx := rootOpenTagEnd(text)
	if idx < 0 {
		return xaml
	}
	attrs := ` xmlns="https://github.com/avaloniaui" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" xmlns:pclcontrols="using:PCL.UI.Controls"`
	return text[:idx] + attrs + text[idx:]
}

// rootOpenTagEnd 返回根元素开标签末尾 “>” 的下标（跳过属性值里的 “>”）。
func rootOpenTagEnd(s string) int {
	loc := axRootRE.FindStringIndex(s)
	if loc == nil {
		return -1
	}
	var quote byte
	for i := loc[1]; i < len(s); i++ {
		ch := s[i]
		if quote != 0 {
			if ch == quote {
				quote = 0
			}
			continue
		}
		switch ch {
		case '"', '\'':
			quote = ch
		case '>':
			return i
		}
	}
	return -1
}

// replaceGeomLogos 把按钮上的几何 Logo 换成 PCL-CE 内置 SVG 图标名（lucide/xxx）。
func replaceGeomLogos(text string) string {
	return axButtonLogoRE.ReplaceAllStringFunc(text, func(button string) string {
		label := ""
		if m := axButtonTextRE.FindStringSubmatch(button); m != nil {
			label = m[1]
		}
		icon := iconForText(label)
		button = axLogoScaleRE.ReplaceAllString(button, "")
		return axLogoRE.ReplaceAllString(button, ` SvgIcon="lucide/`+icon+`"`)
	})
}

var axIconMap = []struct{ key, icon string }{
	{"内存优化", "gauge"},
	{"刷新数据", "refresh-cw"},
	{"刷新页面", "refresh-cw"},
	{"AI 分析", "sparkles"},
	{"计算器", "grid-2x2"},
	{"个性设置", "settings"},
	{"访问浏览", "earth"},
	{"打开仓库", "box"},
	{"打开原仓库", "link-2"},
	{"赞助", "heart"},
}

func iconForText(text string) string {
	for _, item := range axIconMap {
		if strings.Contains(text, item.key) {
			return item.icon
		}
	}
	return "arrow-right"
}

// axamlOK 粗略确认这份 AXAML 具备 PCL-CE 识别与渲染所需的条件。
func axamlOK(body string) bool {
	if !strings.Contains(body, "https://github.com/avaloniaui") ||
		!strings.Contains(body, "pclcontrols:") {
		return false
	}
	if strings.Contains(body, "local:") || strings.Contains(body, ".Triggers") ||
		strings.Contains(body, ".Effect") {
		return false
	}
	for _, match := range axLogoRE.FindAllStringSubmatch(body, -1) {
		value := strings.ToLower(strings.TrimSpace(match[1]))
		if !(strings.HasPrefix(value, "http://") || strings.HasPrefix(value, "https://") ||
			strings.HasPrefix(value, "avares://") || strings.HasPrefix(value, "pack://")) {
			return false
		}
	}
	return true
}
