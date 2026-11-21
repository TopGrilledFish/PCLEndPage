package main

import (
	"regexp"
	"strconv"
)

var (
	commentBlockRE = regexp.MustCompile(`<!--\s*__([A-Z][A-Z0-9_]*)__\s*-->`)
	placeholderRE  = regexp.MustCompile(`__([A-Z][A-Z0-9_]*)__`)
)

// replacePlaceholders 复刻旧版两阶段替换：
// 先整体替换 <!-- __X__ --> 注释块（值为空则块消失），再替换裸 __X__ 令牌（未知令牌保留）。
func replacePlaceholders(template string, values map[string]string) string {
	out := commentBlockRE.ReplaceAllStringFunc(template, func(m string) string {
		sub := commentBlockRE.FindStringSubmatch(m)
		if sub == nil {
			return m
		}
		return values[sub[1]]
	})
	out = placeholderRE.ReplaceAllStringFunc(out, func(m string) string {
		sub := placeholderRE.FindStringSubmatch(m)
		if sub == nil {
			return m
		}
		if v, ok := values[sub[1]]; ok {
			return v
		}
		return m
	})
	return out
}

// dateMainServer 服务器端日期：四段横排（月 / 月后缀 / 日 / 日后缀）。
func dateMainServer(k *I18n, month, day int) string {
	monthSuffix := "<TextBlock Text=\"" + escapeAttr(k.T("date.month_suffix")) +
		"\" FontSize=\"15\" VerticalAlignment=\"Bottom\" Margin=\"0,0,4,16\" Foreground=\"#D9FFFFFF\" />"
	daySuffix := "<TextBlock Text=\"" + escapeAttr(k.T("date.day_suffix")) +
		"\" FontSize=\"15\" VerticalAlignment=\"Bottom\" Margin=\"0,0,4,16\" Foreground=\"#D9FFFFFF\" />"
	return "<StackPanel Orientation=\"Horizontal\" HorizontalAlignment=\"Center\">" +
		"<TextBlock Text=\"" + escapeAttr(k.T("date.month."+strconv.Itoa(month))) +
		"\" FontSize=\"52\" FontWeight=\"Bold\" Foreground=\"White\" />" +
		monthSuffix +
		"<TextBlock Text=\"" + strconv.Itoa(day) +
		"\" FontSize=\"52\" FontWeight=\"Bold\" Foreground=\"White\" />" +
		daySuffix +
		"</StackPanel>"
}

// dateMainMarker 标记版日期：整段交给 PCL 解析。
func dateMainMarker(month, day int) string {
	return "<TextBlock Text=\"&#160;" + strconv.Itoa(month) + "月" + strconv.Itoa(day) +
		"日\" FontSize=\"44\" FontWeight=\"Bold\" Foreground=\"White\" HorizontalAlignment=\"Center\" />"
}
