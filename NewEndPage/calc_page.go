package main

import (
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"strings"
)

// 计算器页面：列表页、单页 XAML、JSON 元数据。与旧版 calc.py 的页面模板逐字对齐。

const iconCalc = "M256 96 H768 A64 64 0 0 1 832 160 V864 A64 64 0 0 1 768 928 H256 " +
	"A64 64 0 0 1 192 864 V160 A64 64 0 0 1 256 96 Z " +
	"M288 192 H736 V400 H288 Z " +
	"M320 512 H448 V640 H320 Z M576 512 H704 V640 H576 Z " +
	"M320 720 H448 V816 H320 Z M576 720 H704 V816 H576 Z"

const iconCopy = "M256 256 H896 V896 H256 Z M384 384 H768 V768 H384 Z " +
	"M128 128 H768 V256 H128 Z M128 256 H256 V768 H128 Z"

const iconHome = "M512 96 L960 480 H832 V928 H640 V672 H384 V928 H192 V480 H64 Z"

const iconBack = "M896 416 H512 V160 L160 512 L512 864 V608 H896 Z"

const iconRefreshMain = "M753 271 C691 209 606 171 512 171 c-189 0 -341 153 -341 341 s152 341 341 341 " +
	"c159 0 292 -109 330 -256 h-89 c-35 99 -130 171 -241 171 c-141 0 -256 -115 -256 -256 " +
	"s115 -256 256 -256 c71 0 134 29 180 76 L555 469 h299 V171 l-100 100 Z"

const calcItemTemplate = `<local:MyListItem Margin="-5,0,-5,4" Type="Clickable" Logo="%s" Title="%s" Info="%s" EventType="打开帮助" EventData="%s" />`

// 输入框里那句灰字提示用的示例值。
var calcExamples = map[string]string{
	"damage":     "8,5",
	"nether":     "800,-1600",
	"chunk":      "100,-200",
	"distance":   "0,64,0,100,64,100",
	"stronghold": "0,0,12345",
	"tick":       "1200",
	"exp":        "0,30",
	"armor":      "20,20",
	"stack":      "1000",
	"color":      "FF8800",
	"uuid":       "Notch",
	"seed":       "hello",
}

// 快捷按钮：点一下就用固定的 ?q= 打开本页。
var calcPresets = map[string][][2]string{
	"damage": {
		{"calc.preset.fist", "1"},
		{"calc.preset.wood_sword", "4"},
		{"calc.preset.stone_sword", "5"},
		{"calc.preset.iron_sword", "6"},
		{"calc.preset.diamond_sword", "7"},
		{"calc.preset.netherite_sword", "8"},
	},
	"nether": {
		{"calc.preset.over_to_nether", "800,-1600"},
		{"calc.preset.nether_to_over", "100,100"},
	},
	"distance": {
		{"calc.preset.dist_3d", "0,64,0,100,64,100"},
		{"calc.preset.dist_flat", "0,0,100,100"},
	},
	"stronghold": {
		{"calc.preset.seed_zero", "0,0,0"},
		{"calc.preset.seed_text", "0,0,hello"},
	},
	"tick": {
		{"calc.preset.tick_sec", "20"},
		{"calc.preset.tick_min", "1200"},
		{"calc.preset.tick_day", "24000"},
		{"calc.preset.tick_repeater", "2"},
	},
	"exp": {
		{"calc.preset.lv30", "30"},
		{"calc.preset.lv50", "50"},
		{"calc.preset.lv100", "100"},
	},
	"armor": {
		{"calc.preset.iron_armor", "20,15"},
		{"calc.preset.diamond_armor", "20,20"},
		{"calc.preset.netherite_armor", "20,20,12"},
	},
	"stack": {
		{"calc.preset.stack64", "64"},
		{"calc.preset.box_items", "1728"},
		{"calc.preset.chest_items", "3456"},
	},
	"color": {
		{"calc.preset.white", "FFFFFF"},
		{"calc.preset.black", "000000"},
		{"calc.preset.red", "FF0000"},
		{"calc.preset.orange", "FF8800"},
		{"calc.preset.green", "00FF00"},
		{"calc.preset.blue", "0000FF"},
	},
	"uuid": {
		{"calc.preset.notch", "Notch"},
		{"calc.preset.herobrine", "Herobrine"},
	},
	"seed": {
		{"calc.preset.seed_hello", "hello"},
		{"calc.preset.seed_minecraft", "minecraft"},
	},
}

func calcAttr(v string) string {
	return strings.ReplaceAll(strings.ReplaceAll(escapeAttr(v), "\n", "&#xA;"), "\r", "")
}

func calcURLAttr(v string) string { return escapeURL(v) }

func calcIndent(block string, spaces int) string {
	pad := strings.Repeat(" ", spaces)
	lines := strings.Split(block, "\n")
	for i, line := range lines {
		if strings.TrimSpace(line) != "" {
			lines[i] = pad + line
		}
	}
	return strings.Join(lines, "\n")
}

func (s *Server) calcNote(text, margin string) string {
	return `<TextBlock Text="` + calcAttr(text) + `" FontSize="11" TextWrapping="Wrap" Margin="` + margin + `" Foreground="{DynamicResource ColorBrush3}" />`
}

func (s *Server) calcHeading(text, margin string) string {
	return `<TextBlock Text="` + escapeAttr(text) + `" FontSize="13" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" Margin="` + margin + `" />`
}

func (s *Server) calcHelpButton(text, logo, url string, height int, column int, margin, color string) string {
	attrs := ""
	if column >= 0 {
		attrs += ` Grid.Column="` + strconv.Itoa(column) + `"`
	}
	if margin != "" {
		attrs += ` Margin="` + margin + `"`
	}
	if color != "" {
		attrs += ` ColorType="` + color + `"`
	}
	return `<local:MyIconTextButton` + attrs + ` Height="` + strconv.Itoa(height) + `" Text="` +
		escapeAttr(text) + `" LogoScale="0.8" Logo="` + logo + `" EventType="打开帮助" EventData="` + calcURLAttr(url) + `" />`
}

func (s *Server) calcInputRow(name, hint string, height int, margin string) string {
	return `<Border Margin="` + margin + `" Height="` + strconv.Itoa(height) + `" Background="{DynamicResource ColorBrush7}" CornerRadius="5">` +
		`<local:MyTextBox x:Name="` + name + `" Height="` + strconv.Itoa(height) + `" Margin="10,0" HintText="` + calcAttr(hint) +
		`" Foreground="{DynamicResource ColorBrush2}" VerticalAlignment="Center" /></Border>`
}

func (s *Server) calcCopyButton(text, logo, data string, height int, column int, margin, color string) string {
	attrs := ""
	if column >= 0 {
		attrs += ` Grid.Column="` + strconv.Itoa(column) + `"`
	}
	if margin != "" {
		attrs += ` Margin="` + margin + `"`
	}
	if color != "" {
		attrs += ` ColorType="` + color + `"`
	}
	return `<local:MyIconTextButton` + attrs + ` Height="` + strconv.Itoa(height) + `" Text="` +
		escapeAttr(text) + `" LogoScale="0.8" Logo="` + logo + `" EventType="复制文本" EventData="` + calcAttr(data) + `" />`
}

// calcBindTo 生成 EventData：把输入框原文拼到接口地址后面。
func calcBindTo(element, base, endpoint string) string {
	return "{Binding Path=Text,ElementName=" + element + ",StringFormat='{}" + base + endpoint + "?q={0}'}"
}

func (s *Server) calcHomeButton(text string, height int, margin string) string {
	attrs := ""
	if margin != "" {
		attrs = ` Margin="` + margin + `"`
	}
	return `<local:MyIconTextButton` + attrs + ` Height="` + strconv.Itoa(height) + `" Text="` +
		escapeAttr(text) + `" LogoScale="0.8" Logo="` + iconHome + `" EventType="切换页面" EventData="Launch" />`
}

func (s *Server) calcNavRow(refreshURL, extraText, extraURL, lang string) string {
	type row struct {
		text   string
		logo   string
		url    string
		isHome bool
	}
	rows := []row{}
	if refreshURL != "" {
		rows = append(rows, row{text: s.i18n.Tf("btn.refresh_result", lang), logo: iconRefreshMain, url: refreshURL})
	}
	if extraURL != "" {
		rows = append(rows, row{text: extraText, logo: iconBack, url: extraURL})
	}
	rows = append(rows, row{text: s.i18n.Tf("btn.back_home", lang), logo: iconHome, isHome: true})
	cells := make([]string, 0, len(rows))
	for i, r := range rows {
		margin := ""
		if i > 0 {
			margin = "8,0,0,0"
		}
		if r.isHome {
			cells = append(cells, s.calcHomeButton(r.text, 36, margin))
		} else {
			cells = append(cells, s.calcHelpButton(r.text, r.logo, r.url, 36, -1, margin, "Highlight"))
		}
	}
	return `<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,18,0,0">` + strings.Join(cells, "") + `</StackPanel>`
}

func (s *Server) calcName(item *calcItem, lang string) string {
	return s.i18n.Tf("calc."+item.ID+".name", lang)
}

func (s *Server) calcInfo(item *calcItem, lang string) string {
	return s.i18n.Tf("calc."+item.ID+".info", lang)
}

func (s *Server) calcHint(item *calcItem, lang string) string {
	text := s.i18n.Tf("calc."+item.ID+".hint", lang)
	if item.ID == "damage" {
		names := make([]string, 0, len(damageParams))
		for _, key := range damageParams {
			names = append(names, s.i18n.Tf("calc.dmg."+key, lang))
		}
		text = strings.ReplaceAll(text, "{params}", strings.Join(names, s.i18n.Tf("calc.param_joiner", lang)))
	}
	return text
}

func (s *Server) calcLogoURL(base string, item *calcItem) string {
	return base + "/images/icons/calc-" + item.ID + ".png"
}

func (s *Server) calcExamplesText(item *calcItem, lang string) string {
	value := calcExamples[item.ID]
	if value == "" {
		return ""
	}
	return s.i18n.Tf("calc.hint_example", lang, "q", value)
}

func (s *Server) calcPresetBlock(item *calcItem, base, lang string) string {
	presets := calcPresets[item.ID]
	if len(presets) == 0 {
		return ""
	}
	rows := []string{}
	for start := 0; start < len(presets); start += 3 {
		end := start + 3
		if end > len(presets) {
			end = len(presets)
		}
		group := presets[start:end]
		cells := []string{}
		// 按钮文字带上实际输入值，一眼能看出点下去会算哪组数；
		// 列数按本行按钮数走，两个按钮时不再留空列。
		defs := strings.Builder{}
		for i, p := range group {
			label := s.i18n.Tf(p[0], lang) + " · " + p[1]
			url := base + "/calc_" + item.ID + ".json?q=" + p[1]
			cells = append(cells, s.calcHelpButton(label, iconCalc, url, 34, i, "0,0,6,6", ""))
			defs.WriteString(`<ColumnDefinition Width="1*" />`)
		}
		rows = append(rows, `<Grid><Grid.ColumnDefinitions>`+defs.String()+`</Grid.ColumnDefinitions>`+strings.Join(cells, "")+`</Grid>`)
	}
	return s.calcHeading(s.i18n.Tf("calc.preset_title", lang), "0,6,0,8") +
		`<StackPanel>` + strings.Join(rows, "") + `</StackPanel>`
}

func (s *Server) calcRowsBlock(rows []calcRow) string {
	defs := strings.Builder{}
	for range rows {
		defs.WriteString(`<RowDefinition Height="Auto" />`)
	}
	cells := make([]string, 0, len(rows)*2)
	for i, r := range rows {
		idx := strconv.Itoa(i)
		cells = append(cells, `<TextBlock Grid.Row="`+idx+`" Grid.Column="0" Text="`+escapeAttr(r.Label)+`" FontSize="12" Margin="0,0,16,7" Foreground="{DynamicResource ColorBrush3}" VerticalAlignment="Top" />`)
		cells = append(cells, `<TextBlock Grid.Row="`+idx+`" Grid.Column="1" Text="`+calcAttr(r.Value)+`" FontSize="13" FontWeight="Bold" TextWrapping="Wrap" Margin="0,0,0,7" Foreground="{DynamicResource ColorBrush1}" />`)
	}
	return `<Grid><Grid.ColumnDefinitions><ColumnDefinition Width="Auto" /><ColumnDefinition Width="1*" /></Grid.ColumnDefinitions><Grid.RowDefinitions>` +
		defs.String() + `</Grid.RowDefinitions>` + strings.Join(cells, "") + `</Grid>`
}

func (s *Server) calcResultText(rows []calcRow) string {
	parts := make([]string, 0, len(rows))
	for _, r := range rows {
		parts = append(parts, r.Label+"  "+r.Value)
	}
	return strings.Join(parts, "\n")
}

func (s *Server) buildCalcLanding(base, lang string) string {
	items := make([]string, 0, len(calcItems))
	for i := range calcItems {
		item := &calcItems[i]
		items = append(items, calcItemTemplateLine(s, base, lang, item))
	}
	itemsBlock := calcIndent(strings.Join(items, "\n"), 12)
	return `<local:MyCard Title="` + escapeAttr(s.i18n.Tf("calc.title", lang)) + `" CanSwap="False"><StackPanel Margin="25,40,23,20">` +
		`<TextBlock Text="` + escapeAttr(s.i18n.Tf("calc.landing_title", lang)) + `" FontSize="24" FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />` +
		s.calcNote(s.i18n.Tf("calc.landing_desc", lang), "0,10,0,0") +
		`<TextBlock Text="` + escapeAttr(s.i18n.Tf("calc.pick", lang)) + `" FontSize="13" FontWeight="Bold" Margin="0,18,0,10" Foreground="{DynamicResource ColorBrush1}" />` +
		itemsBlock +
		s.calcNavRow("", "", "", lang) +
		`</StackPanel></local:MyCard>`
}

func calcItemTemplateLine(s *Server, base, lang string, item *calcItem) string {
	logo := s.calcLogoURL(base, item)
	title := escapeAttr(s.calcName(item, lang))
	info := escapeAttr(s.calcInfo(item, lang))
	u := calcURLAttr(base + "/calc_" + item.ID + ".json")
	return `<local:MyListItem Margin="-5,0,-5,4" Type="Clickable" Logo="` + logo + `" Title="` + title + `" Info="` + info + `" EventType="打开帮助" EventData="` + u + `" />`
}

func (s *Server) buildCalcPage(item *calcItem, raw, base, lang string, pclce bool) string {
	name := s.calcName(item, lang)
	var rows []calcRow
	problem := ""
	if strings.TrimSpace(raw) != "" {
		result, err := item.Run(s, raw, lang)
		if err != nil {
			problem = err.Error()
		} else {
			rows = result
		}
	}
	if len(rows) > 0 {
		rows = append([]calcRow{{s.i18n.Tf("calc.echo", lang), strings.TrimSpace(raw)}}, rows...)
	}

	result := ""
	if problem != "" {
		result = `<local:MyHint Theme="Yellow" Margin="0,16,0,0" Text="` + calcAttr(problem) + `" />`
	} else if len(rows) > 0 {
		result = `<StackPanel Margin="0,16,0,0">` +
			`<TextBlock Text="` + escapeAttr(s.i18n.Tf("calc.result", lang)) + `" FontSize="14" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" Margin="0,0,0,10" />` +
			`<Border Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="16,14">` + s.calcRowsBlock(rows) + `</Border>` +
			`<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">` +
			s.calcCopyButton(s.i18n.Tf("calc.copy_result", lang), iconCopy, s.calcResultText(rows), 34, -1, "", "Highlight") +
			`</StackPanel>` +
			`</StackPanel>`
	}

	submit := s.calcHelpButton(s.i18n.Tf("calc.submit", lang), iconCalc,
		calcBindTo("calcinput", base, "/calc_"+item.ID+".json"), 44, -1, "0,10,0,0", "Highlight")
	if pclce {
		submit = s.calcNote(s.i18n.Tf("calc.ce_input_hint", lang), "0,10,0,0")
	}

	return `<local:MyCard Title="` + escapeAttr(name) + `" CanSwap="False"><StackPanel Margin="25,40,23,20">` +
		`<TextBlock Text="` + escapeAttr(name) + `" FontSize="24" FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />` +
		s.calcNote(s.calcInfo(item, lang), "0,10,0,0") +
		s.calcNote(s.calcHint(item, lang)+"  "+s.i18n.Tf("calc.separator_hint", lang), "0,12,0,0") +
		s.calcInputRow("calcinput", s.calcExamplesText(item, lang), 40, "0,8,0,0") +
		submit +
		s.calcPresetBlock(item, base, lang) +
		result +
		s.calcNavRow("", s.i18n.Tf("calc.other", lang), base+"/calc.json", lang) +
		`</StackPanel></local:MyCard>`
}

// calcQuery 宽松读取 ?q=：PCL 把输入框原文直接拼进 URL，不做编码。
func calcQuery(r *http.Request) string {
	raw := r.URL.RawQuery
	for _, pair := range strings.Split(raw, "&") {
		if strings.HasPrefix(pair, "q=") {
			value := strings.TrimPrefix(pair, "q=")
			if unescaped, err := url.QueryUnescape(value); err == nil {
				return unescaped
			}
			return value
		}
	}
	return ""
}

func writeCalcJSON(w http.ResponseWriter, title, desc string) {
	payload, _ := json.Marshal(map[string]string{"Title": title, "Description": desc})
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", noStore)
	w.Write(payload)
}

// handleCalc 处理 /calc*；不是计算器路径返回 false。
func (s *Server) handleCalc(w http.ResponseWriter, r *http.Request, ip string) bool {
	path := r.URL.Path
	if path != "/calc.json" && path != "/calc.xaml" && !strings.HasPrefix(path, "/calc_") {
		return false
	}
	lang := s.profiles.langFor(ip)
	if path == "/calc.json" {
		writeCalcJSON(w, s.i18n.Tf("calc.title", lang), s.i18n.Tf("calc.meta_desc", lang))
		return true
	}
	if strings.HasPrefix(path, "/calc_") && strings.HasSuffix(path, ".json") {
		id := strings.TrimSuffix(strings.TrimPrefix(path, "/calc_"), ".json")
		item := calcByID(id)
		if item == nil {
			return false
		}
		writeCalcJSON(w, s.calcName(item, lang), s.calcInfo(item, lang))
		return true
	}
	base := s.verify.base(requestOrigin(r))
	if path == "/calc.xaml" {
		page := s.buildCalcLanding(base, lang)
		page = applyPalette(page, s.profiles.paletteFor(ip))
		s.writeXAMLPage(w, r, page, isPCLCE(r.UserAgent()))
		return true
	}
	id := strings.TrimSuffix(strings.TrimPrefix(path, "/calc_"), ".xaml")
	item := calcByID(id)
	if item == nil {
		return false
	}
	page := s.buildCalcPage(item, calcQuery(r), base, lang, isPCLCE(r.UserAgent()))
	page = applyPalette(page, s.profiles.paletteFor(ip))
	s.writeXAMLPage(w, r, page, isPCLCE(r.UserAgent()))
	return true
}
