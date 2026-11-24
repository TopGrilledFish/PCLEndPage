package main

import (
	"encoding/json"
	"fmt"
)

// I18n 语言包，只读。支持 zh-hans / en / zh-hant，缺键依次回退。
type I18n struct {
	packs       map[string]map[string]any
	defaultLang string
}

func newI18n(zhHans, en, zhHant string) (*I18n, error) {
	packs := make(map[string]map[string]any, 3)
	inputs := []struct {
		lang string
		raw  string
	}{
		{"zh-hans", zhHans},
		{"en", en},
		{"zh-hant", zhHant},
	}
	for _, in := range inputs {
		var data map[string]any
		if err := json.Unmarshal([]byte(in.raw), &data); err != nil {
			return nil, fmt.Errorf("解析语言包 %s 失败：%w", in.lang, err)
		}
		packs[in.lang] = data
	}
	return &I18n{packs: packs, defaultLang: "zh-hans"}, nil
}

// T 取默认语言（zh-hans）文案；缺失返回空串。
func (i *I18n) T(key string) string {
	return i.Tlang(key, i.defaultLang)
}

// Tlang 按语言取文案，缺键回退 en → zh-hans。
func (i *I18n) Tlang(key, lang string) string {
	if lang == "" {
		lang = i.defaultLang
	}
	for _, l := range []string{lang, "en", i.defaultLang} {
		if l == "" {
			continue
		}
		if pack, ok := i.packs[l]; ok {
			if v, ok := pack[key]; ok {
				if s, ok := v.(string); ok {
					return s
				}
			}
		}
	}
	return ""
}

func (i *I18n) Has(key string) bool {
	return i.T(key) != ""
}

// List 取数组文案（如 weather.tips.*），按语言取，缺省用 zh-hans。
func (i *I18n) List(key string) []string {
	return i.ListLang(key, i.defaultLang)
}

func (i *I18n) ListLang(key, lang string) []string {
	if lang == "" {
		lang = i.defaultLang
	}
	for _, l := range []string{lang, "en", i.defaultLang} {
		if l == "" {
			continue
		}
		pack, ok := i.packs[l]
		if !ok {
			continue
		}
		v, ok := pack[key]
		if !ok {
			continue
		}
		arr, ok := v.([]any)
		if !ok {
			continue
		}
		out := make([]string, 0, len(arr))
		for _, item := range arr {
			if s, ok := item.(string); ok {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}
