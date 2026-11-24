package main

import (
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// 个性设置页（PCL 内页）：/settings_page.json + /settings_page.xaml。
// 逻辑照旧版 settings.py：改语言 / 温度单位 / 配色 / 玩家名 / 个性码，按 IP 记。

const (
	iconKey = "M112 288 A176 176 0 1 0 464 288 A176 176 0 1 0 112 288 Z " +
		"M464 256 H896 V320 H464 Z " +
		"M768 320 H832 V448 H768 Z M864 320 H928 V416 H864 Z"
	iconLang = "M96 512 A416 416 0 1 0 928 512 A416 416 0 1 0 96 512 Z " +
		"M160 512 A352 352 0 1 0 864 512 A352 352 0 1 0 160 512 Z " +
		"M160 480 H864 V544 H160 Z " +
		"M480 160 H544 V480 H480 Z M480 544 H544 V864 H480 Z"
	iconGear = "M808 419 L918 457 L918 567 L808 605 L787 655 L838 761 L761 838 " +
		"L655 787 L605 808 L567 918 L457 918 L419 808 L369 787 L263 838 L186 761 " +
		"L237 655 L216 605 L106 567 L106 457 L216 419 L237 369 L186 263 L263 186 " +
		"L369 237 L419 216 L457 106 L567 106 L605 216 L655 237 L761 186 L838 263 " +
		"L787 369 Z M390 512 A122 122 0 1 0 634 512 A122 122 0 1 0 390 512 Z"
)

var settingsColorChoices = map[string][]int{"panel": panelChoices, "text": textChoices}
var settingsColorDefault = map[string]int{"panel": paletteDefaults["panel"], "text": paletteDefaults["text"]}

var langNames = map[string]string{"zh-hans": "简体中文", "zh-hant": "繁體中文", "en": "English"}

func langName(code string) string {
	if name, ok := langNames[code]; ok {
		return name
	}
	return code
}

// parseQuery 与旧版 parse_query 同口径：取第一项、保留空值。
func parseQuery(raw string) map[string]string {
	out := map[string]string{}
	if strings.TrimSpace(raw) == "" {
		return out
	}
	if values, err := url.ParseQuery(raw); err == nil {
		for key, list := range values {
			if len(list) > 0 {
				out[key] = list[0]
			}
		}
		return out
	}
	for _, chunk := range strings.Split(raw, "&") {
		if idx := strings.Index(chunk, "="); idx >= 0 {
			key, err1 := url.QueryUnescape(chunk[:idx])
			value, err2 := url.QueryUnescape(chunk[idx+1:])
			if err1 != nil {
				key = chunk[:idx]
			}
			if err2 != nil {
				value = chunk[idx+1:]
			}
			out[key] = value
		}
	}
	return out
}

// settingsBindTo 与旧版 settings._bind 一致：把输入框原文拼到 ?<param>= 后面。
func settingsBindTo(element, base, param string) string {
	return "{Binding Path=Text,ElementName=" + element + ",StringFormat='{}" + base + "/settings_page.json?" + param + "={0}'}"
}

func (s *Server) settingsStateBlock(record map[string]any, ip, lang string) string {
	if ip == "" {
		ip = "?"
	}
	rows := []string{s.i18n.Tf("settings.state_ip", lang, "ip", ip)}
	name := strings.TrimSpace(toString(record["name"]))
	if name != "" {
		rows = append(rows, s.i18n.Tf("settings.state_name", lang, "name", name))
	} else {
		rows = append(rows, s.i18n.Tf("settings.state_name_unset", lang))
	}
	rows = append(rows, s.i18n.Tf("settings.state_lang", lang, "language", langName(lang)))
	unit := normalizeUnit(toString(record["unit"]))
	rows = append(rows, s.i18n.Tf("settings.state_unit", lang, "unit", s.i18n.Tf("settings.unit_"+unit, lang)))
	if record != nil {
		if stamp := asFloatNum(record["updated"]); stamp > 0 {
			until := time.Unix(int64(stamp), 0).AddDate(0, 0, profilesKeepDays)
			rows = append(rows, s.i18n.Tf("settings.state_seen", lang,
				"date", until.Format("2006-01-02"), "days", strconv.Itoa(profilesKeepDays)))
		}
	} else {
		rows = append(rows, s.i18n.Tf("settings.state_none", lang))
	}
	var b strings.Builder
	for _, text := range rows {
		b.WriteString(`<TextBlock Text="` + calcAttr(text) + `" FontSize="12" TextWrapping="Wrap" Margin="0,0,0,5" Foreground="{DynamicResource ColorBrush2}" />`)
	}
	return `<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14" Margin="0,10,0,0"><StackPanel>` + b.String() + `</StackPanel></Border>`
}

func (s *Server) settingsLangEntry(base, lang string) string {
	return s.calcHelpButton(s.i18n.Tf("settings.lang_open", lang), iconKey,
		base+"/language_page.json", 38, -1, "0,8,0,0", "Highlight")
}

func (s *Server) settingsUnitButtons(base, lang, current string) string {
	cells := []string{}
	for _, code := range profileUnits {
		mark := ""
		if code == current {
			mark = "✓ "
		}
		cells = append(cells, s.calcHelpButton(mark+s.i18n.Tf("settings.unit_"+code, lang), iconKey,
			base+"/settings_page.json?unit="+code, 34, -1, "0,0,6,0", highlightIf(code == current)))
	}
	return `<StackPanel Orientation="Horizontal" Margin="0,8,0,0">` + strings.Join(cells, "") + `</StackPanel>`
}

func (s *Server) settingsSwatchRow(base, lang, role string, current int) string {
	cells := []string{}
	for _, level := range settingsColorChoices[role] {
		mark := ""
		if level == current {
			mark = "✓ "
		}
		cells = append(cells, s.calcHelpButton(
			mark+s.i18n.Tf("settings.color_level", lang, "n", strconv.Itoa(level)), iconKey,
			base+"/settings_page.json?"+role+"="+strconv.Itoa(level), 34, -1, "0,0,6,0",
			highlightIf(level == current)))
	}
	return `<StackPanel Orientation="Horizontal" Margin="0,8,0,0">` + strings.Join(cells, "") + `</StackPanel>`
}

func (s *Server) settingsColorButtons(base, lang string, record map[string]any) string {
	chosen := paletteFor(record)
	rows := []string{}
	for _, role := range []string{"panel", "text"} {
		key := "settings.color_" + role
		rows = append(rows, `<TextBlock Text="`+calcAttr(s.i18n.Tf(key, lang))+`" FontSize="12" Margin="0,10,0,0" Foreground="{DynamicResource ColorBrush2}" />`)
		rows = append(rows, s.settingsSwatchRow(base, lang, role, chosen[role]))
	}
	sample := calcAttr(s.i18n.Tf("settings.color_preview_text", lang))
	rows = append(rows, `<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="14,12" Margin="0,14,0,0"><StackPanel>`+
		`<TextBlock Text="`+calcAttr(s.i18n.Tf("settings.color_preview", lang))+`" FontSize="13" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" />`+
		`<TextBlock Text="`+sample+`" FontSize="12" Margin="0,6,0,0" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush1}" />`+
		`<TextBlock Text="`+sample+`" FontSize="12" Margin="0,6,0,0" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush3}" />`+
		`</StackPanel></Border>`)
	return strings.Join(rows, "")
}

func highlightIf(on bool) string {
	if on {
		return "Highlight"
	}
	return ""
}

// buildSettingsPage 渲染设置页正文（与旧版 settings.py 逐字对齐）。
func (s *Server) buildSettingsPage(baseURL, ip, query string, pclce bool) string {
	base := escapeURL(baseURL)
	params := parseQuery(query)

	lang := s.profiles.langFor(ip)
	record := s.profiles.byIP(ip)
	result := ""

	if wanted := params["lang"]; wanted != "" {
		lang = normalizeLang(wanted)
		s.profiles.bind(ip, "", lang, "", nil, nil)
		result = s.i18n.Tf("settings.lang_changed", lang, "language", langName(lang))
	}
	if wanted := params["unit"]; wanted != "" {
		unit := normalizeUnit(wanted)
		s.profiles.bind(ip, "", "", unit, nil, nil)
		result = s.i18n.Tf("settings.unit_saved", lang, "unit", s.i18n.Tf("settings.unit_"+unit, lang))
	}
	if rawName, ok := params["name"]; ok {
		name := strings.TrimSpace(rawName)
		if name == "" {
			result = s.i18n.Tf("settings.name_empty", lang)
		} else {
			s.profiles.bind(ip, name, "", "", nil, nil)
			result = s.i18n.Tf("settings.name_saved", lang, "name", name)
		}
	}
	if code := params["code"]; code != "" {
		data := decodeProfileCode(code)
		if data == nil {
			result = s.i18n.Tf("settings.code_bad", lang)
		} else {
			if v := toString(data["lang"]); v != "" {
				lang = v
			}
			var panelPtr, textPtr *int
			if v, ok := data["panel"].(float64); ok {
				n := int(v)
				panelPtr = &n
			}
			if v, ok := data["text"].(float64); ok {
				n := int(v)
				textPtr = &n
			}
			s.profiles.bind(ip, toString(data["name"]), toString(data["lang"]),
				toString(data["unit"]), panelPtr, textPtr)
			result = s.i18n.Tf("settings.code_ok", lang, "language", langName(lang))
		}
	}
	for _, role := range []string{"panel", "text"} {
		if raw, ok := params[role]; ok {
			level := normalizeBrush(raw, settingsColorChoices[role], settingsColorDefault[role])
			if role == "panel" {
				s.profiles.bind(ip, "", "", "", &level, nil)
			} else {
				s.profiles.bind(ip, "", "", "", nil, &level)
			}
			result = s.i18n.Tf("settings.color_saved", lang,
				"name", s.i18n.Tf("settings.color_"+role, lang),
				"level", s.i18n.Tf("settings.color_level", lang, "n", strconv.Itoa(level)))
		}
	}

	record = s.profiles.byIP(ip)
	body := []string{
		s.calcHeading(s.i18n.Tf("settings.state_title", lang), "0,20,0,0"),
		s.settingsStateBlock(record, ip, lang),
	}
	if result != "" {
		body = append(body, `<local:MyHint Theme="Blue" Margin="0,12,0,0" Text="`+calcAttr(result)+`" />`)
	}

	body = append(body,
		s.calcHeading(s.i18n.Tf("settings.color_title", lang), "0,20,0,0"),
		s.calcNote(s.i18n.Tf("settings.color_hint", lang), "0,6,0,0"),
		s.settingsColorButtons(base, lang, record),
		s.calcHeading(s.i18n.Tf("settings.lang_title", lang), "0,20,0,0"),
		s.calcNote(s.i18n.Tf("settings.lang_hint", lang), "0,6,0,0"),
		s.settingsLangEntry(base, lang),
		s.calcHeading(s.i18n.Tf("settings.unit_title", lang), "0,20,0,0"),
		s.calcNote(s.i18n.Tf("settings.unit_hint", lang), "0,6,0,0"),
		s.settingsUnitButtons(base, lang, normalizeUnit(toString(record["unit"]))),
		s.calcHeading(s.i18n.Tf("settings.name_title", lang), "0,20,0,0"),
		s.calcNote(s.i18n.Tf("settings.name_hint", lang), "0,6,0,0"),
	)
	if pclce {
		// PCL-CE 的 AXAML 校验器禁 {Binding}，输入框取不到值——直接说明，
		// 别摆一个点了没反应的表单。
		body = append(body, s.calcNote(s.i18n.Tf("settings.ce_input_hint", lang), "0,8,0,0"))
	} else {
		body = append(body,
			s.calcInputRow("setname", s.i18n.Tf("settings.name_box", lang), 38, "0,8,0,0"),
			s.calcHelpButton(s.i18n.Tf("settings.name_save", lang), iconKey,
				settingsBindTo("setname", base, "name"), 38, -1, "0,8,0,0", "Highlight"),
		)
	}

	body = append(body,
		s.calcHeading(s.i18n.Tf("settings.code_title", lang), "0,20,0,0"),
		s.calcNote(s.i18n.Tf("settings.code_hint", lang), "0,6,0,0"),
	)
	_, export := params["export"]
	if export && record != nil {
		code := encodeProfileCode(record)
		body = append(body,
			s.calcNote(s.i18n.Tf("settings.code_value", lang), "0,10,0,0"),
			`<Border Background="{DynamicResource ColorBrush7}" CornerRadius="6" Padding="12,10" Margin="0,4,0,0"><TextBlock Text="`+calcAttr(code)+`" FontSize="12" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush1}" /></Border>`,
			s.calcCopyButton(s.i18n.Tf("settings.code_copy", lang), iconCopy, code, 38, -1, "0,8,0,0", "Highlight"),
		)
	}
	if pclce {
		body = append(body, s.calcNote(s.i18n.Tf("settings.ce_input_hint", lang), "0,8,0,0"))
	} else {
		body = append(body,
			`<Grid Margin="0,8,0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="1*" /><ColumnDefinition Width="1*" /></Grid.ColumnDefinitions>`+
				s.calcHelpButton(s.i18n.Tf("settings.code_export", lang), iconKey,
					base+"/settings_page.json?export=1", 38, 0, "0,0,5,0", "Highlight")+
				s.calcHelpButton(s.i18n.Tf("settings.code_import", lang), iconKey,
					settingsBindTo("setcode", base, "code"), 38, 1, "5,0,0,0", "Highlight")+
				`</Grid>`,
			s.calcInputRow("setcode", s.i18n.Tf("settings.code_box", lang), 38, "0,8,0,0"),
		)
	}

	return `<local:MyCard Title="` + escapeAttr(s.i18n.Tf("settings.title", lang)) +
		`" CanSwap="False"><StackPanel Margin="25,40,23,20">` +
		`<TextBlock Text="` + escapeAttr(s.i18n.Tf("settings.title", lang)) +
		`" FontSize="24" FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />` +
		s.calcNote(s.i18n.Tf("settings.desc", lang), "0,10,0,0") +
		strings.Join(body, "") +
		s.calcNavRow("", "", "", lang) +
		`</StackPanel></local:MyCard>`
}

func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request, ip string) bool {
	switch r.URL.Path {
	case "/settings_page.json":
		params := parseQuery(r.URL.RawQuery)
		lang := s.profiles.langFor(ip)
		if v := params["lang"]; v != "" {
			lang = normalizeLang(v)
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Cache-Control", noStore)
		fmt.Fprintf(w, `{"Title": %s, "Description": %s}`,
			jsonString(s.i18n.Tf("settings.title", lang)), jsonString(s.i18n.Tf("settings.desc", lang)))
		return true
	case "/settings_page.xaml":
		pclce := isPCLCE(r.UserAgent())
		base := s.verify.base(requestOrigin(r))
		page := s.buildSettingsPage(base, ip, r.URL.RawQuery, pclce)
		page = applyPalette(page, s.profiles.paletteFor(ip))
		s.writeXAMLPage(w, r, page, pclce)
		return true
	}
	return false
}

// jsonString 生成与 Python json.dumps(ensure_ascii=False) 一致的单字符串字面量。
func jsonString(v string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range v {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(&b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}
