package main

import (
	"regexp"
	"strconv"
	"strings"
)

// 卡片配色：访客可调的两个维度（色块浓度 panel、文字深浅 text），
// 其余两个角色号由 text 推出来。与旧版 palette.py 一致。

var roleBrush = map[string]int{"panel": 7, "text": 1, "muted": 3, "dim": 2}

var panelChoices = []int{5, 6, 7, 8}
var textChoices = []int{1, 2, 3}

var paletteDefaults = map[string]int{"panel": 7, "text": 1}

var brushRE = regexp.MustCompile(`\{DynamicResource ColorBrush(\d)\}`)

func normalizeBrush(value any, choices []int, def int) int {
	n, err := strconv.Atoi(strings.TrimSpace(toString(value)))
	if err != nil {
		return def
	}
	for _, c := range choices {
		if c == n {
			return n
		}
	}
	return def
}

func toString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	case nil:
		return ""
	default:
		return ""
	}
}

// paletteFor 由记录推出四个颜色号（muted/dim 跟着 text 走）。
func paletteFor(record map[string]any) map[string]int {
	panel := normalizeBrush(record["panel"], panelChoices, paletteDefaults["panel"])
	text := normalizeBrush(record["text"], textChoices, paletteDefaults["text"])
	return map[string]int{
		"panel": panel,
		"text":  text,
		"muted": minInt(text+2, 8),
		"dim":   minInt(text+1, 8),
	}
}

// applyPalette 把页面里写死的角色号换成访客选的号。
func applyPalette(xaml string, palette map[string]int) string {
	if len(palette) == 0 {
		return xaml
	}
	mapping := make(map[string]string, 4)
	for role, n := range roleBrush {
		target := n
		if v, ok := palette[role]; ok {
			target = v
		}
		mapping[strconv.Itoa(n)] = strconv.Itoa(target)
	}
	return brushRE.ReplaceAllStringFunc(xaml, func(m string) string {
		sub := brushRE.FindStringSubmatch(m)
		if len(sub) < 2 {
			return m
		}
		if v, ok := mapping[sub[1]]; ok {
			return "{DynamicResource ColorBrush" + v + "}"
		}
		return m
	})
}
