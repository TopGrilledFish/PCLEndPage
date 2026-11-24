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
	axRootRE      = regexp.MustCompile(`<StackPanel\b`)
	axResourcesRE = regexp.MustCompile(`(?s)\s*<StackPanel\.Resources>.*?</StackPanel\.Resources>`)
	axTriggersRE  = regexp.MustCompile(`(?s)\s*<[A-Za-z][\w.]*\.Triggers>.*?</[A-Za-z][\w.]*\.Triggers>`)
	axEffectRE    = regexp.MustCompile(`(?s)\s*<[A-Za-z][\w.]*\.Effect>.*?</[A-Za-z][\w.]*\.Effect>`)
	axLocalRE     = regexp.MustCompile(`(</?)local:`)
	axLogoRE      = regexp.MustCompile(`\s+Logo="([^"]*)"`)
	axLogoScaleRE = regexp.MustCompile(`\s+LogoScale="[^"]*"`)
)

// isPCLCE 判断请求是否来自 PCL-CE（UA 里带 PCLCE/）。
func isPCLCE(userAgent string) bool {
	return strings.Contains(strings.ToLower(userAgent), "pclce/")
}

func toAXAML(xaml string) string {
	if !axRootRE.MatchString(xaml) {
		return xaml
	}
	text := axResourcesRE.ReplaceAllString(xaml, "")
	text = axTriggersRE.ReplaceAllString(text, "")
	text = axEffectRE.ReplaceAllString(text, "")
	text = axLocalRE.ReplaceAllString(text, "${1}pclcontrols:")
	text = stripGeomLogos(text)
	text = axLogoScaleRE.ReplaceAllString(text, "")
	idx := axRootRE.FindStringIndex(text)
	if idx == nil {
		return xaml
	}
	attrs := ` xmlns="https://github.com/avaloniaui" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" xmlns:pclcontrols="using:PCL.UI.Controls"`
	return text[:idx[1]] + attrs + text[idx[1]:]
}

// stripGeomLogos 删掉几何路径形式的 Logo（PCL-CE 的 AXAML 校验器不接受），
// 图片地址形式的 Logo 原样保留。
func stripGeomLogos(text string) string {
	return axLogoRE.ReplaceAllStringFunc(text, func(match string) string {
		value := strings.ToLower(strings.TrimSpace(axLogoRE.FindStringSubmatch(match)[1]))
		if strings.HasPrefix(value, "http://") || strings.HasPrefix(value, "https://") ||
			strings.HasPrefix(value, "avares://") || strings.HasPrefix(value, "pack://") {
			return match
		}
		return ""
	})
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
