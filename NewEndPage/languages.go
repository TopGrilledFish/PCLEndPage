package main

import (
	"fmt"
	"net/http"
	"strings"
)

// 语言选择页（PCL 内页）：/language_page.json + /language_page.xaml。
// 逻辑照旧版 languages.py：只有简体 / 繁体 / 英文三种，按 IP 记。

var languageOrder = []string{"zh-hans", "zh-hant", "en"}

var languageNative = map[string]string{
	"zh-hans": "简体中文",
	"zh-hant": "繁體中文",
	"en":      "English",
}

func (s *Server) languageDisplay(code, lang string) string {
	native := s.i18n.Tlang("_name", code)
	if native == "" {
		native = languageNative[code]
	}
	zh := s.i18n.Tlang("_name_zh", code)
	if zh == "" {
		zh = s.langNamesFallback(code, native)
	}
	en := s.i18n.Tlang("_name_en", code)
	if en == "" {
		en = s.langNamesFallbackEn(code, native)
	}
	if strings.HasPrefix(strings.ToLower(lang), "en") {
		if en != native {
			return en
		}
		return native
	}
	if zh != native {
		return zh
	}
	return native
}

func (s *Server) langNamesFallback(code, native string) string {
	if name, ok := langNames[code]; ok {
		return name
	}
	return native
}

func (s *Server) langNamesFallbackEn(code, native string) string {
	switch code {
	case "zh-hans":
		return "Chinese (Simplified)"
	case "zh-hant":
		return "Chinese (Traditional)"
	case "en":
		return "English"
	}
	return native
}

func (s *Server) languageCurrentBlock(lang, result string) string {
	rows := []string{
		`<TextBlock Text="` + calcAttr(s.i18n.Tf("language.current", lang, "language", s.languageDisplay(lang, lang))) +
			`" FontSize="12" TextWrapping="Wrap" Margin="0,0,0,5" Foreground="{DynamicResource ColorBrush2}" />`,
	}
	if result != "" {
		rows = append(rows, `<TextBlock Text="`+calcAttr(result)+`" FontSize="12" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush1}" />`)
	}
	return `<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14" Margin="0,10,0,0"><StackPanel>` +
		strings.Join(rows, "") + `</StackPanel></Border>`
}

func (s *Server) languageEntryButton(code, lang, base string, column int, current bool) string {
	mark := ""
	if current {
		mark = "✓ "
	}
	return s.calcHelpButton(mark+s.languageDisplay(code, lang), iconLang,
		base+"/language_page.json?lang="+code, 38, column, "0,0,6,0", highlightIf(current))
}

func (s *Server) languageEntryGrid(items []string, lang, base, current string) string {
	if len(items) == 0 {
		return ""
	}
	var cols strings.Builder
	for range items {
		cols.WriteString(`<ColumnDefinition Width="1*" />`)
	}
	cells := make([]string, 0, len(items))
	for index, code := range items {
		cells = append(cells, s.languageEntryButton(code, lang, base, index, code == current))
	}
	return `<Grid Margin="0,4,0,0"><Grid.ColumnDefinitions>` + cols.String() +
		`</Grid.ColumnDefinitions>` + strings.Join(cells, "") + `</Grid>`
}

func isSupportedLang(code string) bool {
	code = strings.ToLower(strings.TrimSpace(code))
	return code == "zh-hans" || code == "zh-hant" || code == "en"
}

func (s *Server) buildLanguagePage(baseURL, ip, query string) string {
	base := escapeURL(baseURL)
	params := parseQuery(query)
	current := s.profiles.langFor(ip)
	result := ""

	if wanted := strings.TrimSpace(params["lang"]); wanted != "" {
		if isSupportedLang(wanted) {
			s.profiles.bind(ip, "", wanted, "", nil, nil)
			current = normalizeLang(wanted)
			result = s.i18n.Tf("language.switched", current, "language", s.languageDisplay(current, current))
		} else {
			result = s.i18n.Tf("language.unsupported_click", current, "language", wanted)
		}
	}

	supported := make([]string, 0, len(languageOrder))
	for _, code := range languageOrder {
		if isSupportedLang(code) {
			supported = append(supported, code)
		}
	}

	body := []string{
		s.calcHeading(s.i18n.Tf("language.current_title", current), "0,20,0,0"),
		s.languageCurrentBlock(current, result),
		s.calcHeading(s.i18n.Tf("language.supported_title", current, "n", itoa(len(supported))), "0,20,0,0"),
		s.languageEntryGrid(supported, current, base, current),
	}

	return `<local:MyCard Title="` + escapeAttr(s.i18n.Tf("language.title", current)) +
		`" CanSwap="False"><StackPanel Margin="25,40,23,20">` +
		`<TextBlock Text="` + escapeAttr(s.i18n.Tf("language.title", current)) +
		`" FontSize="24" FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />` +
		s.calcNote(s.i18n.Tf("language.desc", current), "0,10,0,0") +
		strings.Join(body, "") +
		s.calcNavRow("", "", "", current) +
		`</StackPanel></local:MyCard>`
}

func (s *Server) handleLanguages(w http.ResponseWriter, r *http.Request, ip string) bool {
	switch r.URL.Path {
	case "/language_page.json":
		params := parseQuery(r.URL.RawQuery)
		lang := s.profiles.langFor(ip)
		if wanted := strings.TrimSpace(params["lang"]); isSupportedLang(wanted) {
			lang = normalizeLang(wanted)
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Cache-Control", noStore)
		fmt.Fprintf(w, `{"Title": %s, "Description": %s}`,
			jsonString(s.i18n.Tf("language.title", lang)), jsonString(s.i18n.Tf("language.desc", lang)))
		return true
	case "/language_page.xaml":
		base := s.verify.base(requestOrigin(r))
		page := s.buildLanguagePage(base, ip, r.URL.RawQuery)
		page = applyPalette(page, s.profiles.paletteFor(ip))
		s.writeXAMLPage(w, r, page, isPCLCE(r.UserAgent()))
		return true
	}
	return false
}
