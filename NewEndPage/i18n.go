package main

import (
	"encoding/json"
	"fmt"
)

// I18n 语言包，只读。
type I18n struct {
	data map[string]any
}

func newI18n(payload string) (*I18n, error) {
	var data map[string]any
	if err := json.Unmarshal([]byte(payload), &data); err != nil {
		return nil, fmt.Errorf("解析语言包失败：%w", err)
	}
	return &I18n{data: data}, nil
}

// T 取文案；缺失返回空串。
func (i *I18n) T(key string) string {
	if v, ok := i.data[key]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}

func (i *I18n) Has(key string) bool {
	_, ok := i.data[key]
	return ok
}

// List 取数组文案（如 weather.tips.*）。
func (i *I18n) List(key string) []string {
	v, ok := i.data[key]
	if !ok {
		return nil
	}
	arr, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(arr))
	for _, item := range arr {
		if s, ok := item.(string); ok {
			out = append(out, s)
		}
	}
	return out
}
