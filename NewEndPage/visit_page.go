package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// 「访问浏览」内页：最近 7 天的访问统计 + 按省份涂色的中国地图。
// 与旧版 visit.py 保持逐字一致（地图数据来自 chinamap.py 导出的 assets/chinamap.json）。

const visitDays = visitKeepDays

type visitConsts struct {
	LevelStyle     map[string][]string       `json:"level_style"`
	SelfName       map[string][]string        `json:"self_name"`
	ProvinceSuffix []string                   `json:"province_suffix"`
	ProvinceEN     map[string]string          `json:"province_en"`
	CityEN         map[string]string          `json:"city_en"`
}

type chinaPath struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

type chinaMapData struct {
	Viewbox      string      `json:"viewbox"`
	Paths        []chinaPath `json:"paths"`
	InsetLines   string      `json:"inset_lines"`
	InsetIslands string      `json:"inset_islands"`
	InsetBox     []float64   `json:"inset_box"`
}

var (
	visitConstsOnce sync.Once
	visitConstsVal  visitConsts
	chinaMapOnce    sync.Once
	chinaMapVal     chinaMapData
)

func visitConstsData() *visitConsts {
	visitConstsOnce.Do(func() {
		raw := mustReadAsset("assets/visit.json")
		if err := json.Unmarshal([]byte(raw), &visitConstsVal); err != nil {
			panic("visit.json 解析失败: " + err.Error())
		}
	})
	return &visitConstsVal
}

func chinaMapDataOf() *chinaMapData {
	chinaMapOnce.Do(func() {
		raw := mustReadAsset("assets/chinamap.json")
		if err := json.Unmarshal([]byte(raw), &chinaMapVal); err != nil {
			panic("chinamap.json 解析失败: " + err.Error())
		}
	})
	return &chinaMapVal
}

func visitLevelStyleOf(level int) (string, string) {
	c := visitConstsData()
	if v, ok := c.LevelStyle[strconv.Itoa(level)]; ok && len(v) >= 2 {
		return v[0], v[1]
	}
	return "ColorBrush7", "0.55"
}

func visitShort(name string) string {
	name = strings.TrimSpace(name)
	for _, suffix := range visitConstsData().ProvinceSuffix {
		if strings.HasSuffix(name, suffix) && len([]rune(name)) > len([]rune(suffix)) {
			return strings.TrimSuffix(name, suffix)
		}
	}
	return name
}

func visitIsEN(lang string) bool {
	return strings.HasPrefix(strings.ToLower(lang), "en")
}

func visitPlaceEN(name string) string {
	c := visitConstsData()
	short := visitShort(name)
	if v, ok := c.ProvinceEN[short]; ok && v != "" {
		return v
	}
	if v, ok := c.CityEN[short]; ok && v != "" {
		return v
	}
	return ""
}

func (s *Server) visitHasCountry(code, lang string) bool {
	return s.i18n.Tlang("visit.country."+code, lang) != ""
}

// visitRegionText 「浙江 · 杭州」/「美国 · 洛杉矶」/「未定位」，与旧版 _region_text 同口径。
func (s *Server) visitRegionText(code, city, province, lang string) string {
	code = strings.ToUpper(strings.TrimSpace(code))
	if code == "" {
		code = "XX"
	}
	city = strings.TrimSpace(city)
	province = strings.TrimSpace(province)
	country := code
	if s.visitHasCountry(code, lang) {
		country = s.i18n.Tf("visit.country."+code, lang)
	}
	c := visitConstsData()
	if visitIsEN(lang) {
		provinceEN := ""
		if province != "" {
			provinceEN = visitPlaceEN(province)
		}
		cityEN := ""
		if city != "" {
			cityEN = visitPlaceEN(city)
		}
		if code == "CN" || code == "HK" || code == "MO" || code == "TW" {
			if provinceEN != "" && cityEN != "" && cityEN != provinceEN {
				return provinceEN + " · " + cityEN
			}
			if provinceEN != "" {
				return provinceEN
			}
			if cityEN != "" {
				return cityEN
			}
			return country
		}
		if cityEN != "" {
			return country + " · " + cityEN
		}
		if provinceEN != "" {
			return country + " · " + provinceEN
		}
		return country
	}
	if self, ok := c.SelfName[code]; ok {
		tail := city
		if tail == "" {
			tail = province
		}
		if tail == "" || containsString(self, tail) {
			return country
		}
		return country + " · " + tail
	}
	if code == "CN" {
		if province != "" {
			if city == "" || city == province {
				return province
			}
			return province + " · " + city
		}
		if city != "" {
			return city
		}
		return s.i18n.Tf("visit.region_unknown", lang)
	}
	if province != "" && province != country {
		tail := province
		if city != "" && city != province {
			tail = province + " · " + city
		}
		return country + " · " + tail
	}
	if city != "" && city != country {
		return country + " · " + city
	}
	return country
}

func containsString(items []string, v string) bool {
	for _, item := range items {
		if item == v {
			return true
		}
	}
	return false
}

func (s *Server) visitAgo(stampMs float64, lang string) string {
	stamp := stampMs / 1000.0
	gap := time.Now().Unix() - int64(stamp)
	if gap < 0 {
		gap = 0
	}
	switch {
	case gap < 60:
		return s.i18n.Tf("visit.now_just", lang)
	case gap < 3600:
		return s.i18n.Tf("visit.minutes_ago", lang, "n", strconv.FormatInt(gap/60, 10))
	case gap < 86400:
		return s.i18n.Tf("visit.hours_ago", lang, "n", strconv.FormatInt(gap/3600, 10))
	default:
		return s.i18n.Tf("visit.days_ago", lang, "n", strconv.FormatInt(gap/86400, 10))
	}
}

func visitLevels(provinces []visitCount) map[string]int {
	rows := make([]visitCount, 0, len(provinces))
	for _, item := range provinces {
		if strings.TrimSpace(item.Name) != "" && item.Count > 0 {
			rows = append(rows, item)
		}
	}
	if len(rows) == 0 {
		return map[string]int{}
	}
	peak := 0
	for _, item := range rows {
		if item.Count > peak {
			peak = item.Count
		}
	}
	out := map[string]int{}
	for _, item := range rows {
		level := (item.Count*4 + peak - 1) / peak // ceil(count/peak*4)
		if level < 1 {
			level = 1
		}
		if level > 4 {
			level = 4
		}
		out[visitShort(item.Name)] = level
	}
	return out
}

// ---------- 板块 ----------

func visitKpiCard(column int, label, value, margin string) string {
	attrs := ` Grid.Column="` + strconv.Itoa(column) + `"`
	if margin != "" {
		attrs += ` Margin="` + margin + `"`
	}
	return `<Border` + attrs + ` Background="{DynamicResource ColorBrush7}" CornerRadius="8" Padding="12,10,12,11"><StackPanel>` +
		`<TextBlock Text="` + calcAttr(label) + `" FontSize="10" TextWrapping="Wrap" Foreground="{DynamicResource ColorBrush3}" />` +
		`<TextBlock Text="` + calcAttr(value) + `" FontSize="20" FontWeight="Bold" Margin="0,5,0,0" Foreground="{DynamicResource ColorBrush1}" />` +
		`</StackPanel></Border>`
}

func visitKpiRow(cards [][2]string) string {
	cells := make([]string, 0, len(cards))
	for index, card := range cards {
		margin := "0,0,8,0"
		if index == len(cards)-1 {
			margin = ""
		}
		cells = append(cells, visitKpiCard(index, card[0], card[1], margin))
	}
	return `<Grid Margin="0,10,0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="1*" /><ColumnDefinition Width="1*" /><ColumnDefinition Width="1*" /></Grid.ColumnDefinitions>` +
		strings.Join(cells, "") + `</Grid>`
}

func (s *Server) buildVisitKpis(sm *visitSummary, lang string) string {
	perDay := 0.0
	if visitDays > 0 {
		perDay = float64(sm.Total) / float64(visitDays)
	}
	perDayText := strconv.FormatFloat(roundFloat(perDay, 1), 'f', -1, 64)
	first := [][2]string{
		{s.i18n.Tf("visit.kpi_total", lang, "days", strconv.Itoa(visitDays)), strconv.Itoa(sm.Total)},
		{s.i18n.Tf("visit.kpi_today", lang), strconv.Itoa(sm.Today)},
		{s.i18n.Tf("visit.kpi_unique", lang), strconv.Itoa(sm.Unique)},
	}
	second := [][2]string{
		{s.i18n.Tf("visit.kpi_daily", lang), perDayText},
		{s.i18n.Tf("visit.kpi_domestic", lang), strconv.Itoa(sm.Domestic)},
		{s.i18n.Tf("visit.kpi_foreign", lang), strconv.Itoa(sm.Foreign)},
	}
	return visitKpiRow(first) + visitKpiRow(second)
}

func (s *Server) visitMapLegend(lang string) string {
	var squares []string
	for _, level := range []int{1, 2, 3, 4} {
		brush, opacity := visitLevelStyleOf(level)
		squares = append(squares, `<Rectangle Width="14" Height="10" Margin="4,0,0,0" RadiusX="2" RadiusY="2" Opacity="`+opacity+`" Fill="{DynamicResource `+brush+`}" />`)
	}
	return `<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,6,0,0">` +
		`<TextBlock Text="` + calcAttr(s.i18n.Tf("visit.legend_less", lang)) + `" FontSize="10" VerticalAlignment="Center" Foreground="{DynamicResource ColorBrush3}" />` +
		strings.Join(squares, "") +
		`<TextBlock Text="` + calcAttr(s.i18n.Tf("visit.legend_more", lang)) + `" FontSize="10" VerticalAlignment="Center" Margin="6,0,0,0" Foreground="{DynamicResource ColorBrush3}" /></StackPanel>`
}

func (s *Server) buildVisitMap(sm *visitSummary, lang string) string {
	levels := visitLevels(sm.Provinces)
	if len(levels) == 0 {
		return s.calcNote(s.i18n.Tf("visit.map_empty", lang), "0,10,0,0") + s.visitMapLegend(lang)
	}
	data := chinaMapDataOf()
	var parts []string
	for _, item := range data.Paths {
		level := levels[item.Name]
		brush, opacity := visitLevelStyleOf(level)
		parts = append(parts, `<Path Data="`+calcAttr(item.Path)+`" Opacity="`+opacity+`" Fill="{DynamicResource `+brush+`}" Stroke="{DynamicResource ColorBrush7}" StrokeThickness="0.6" />`)
	}
	if len(data.InsetBox) >= 4 {
		left, top, width, height := data.InsetBox[0], data.InsetBox[1], data.InsetBox[2], data.InsetBox[3]
		num := func(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }
		parts = append(parts, `<Rectangle Canvas.Left="`+num(left)+`" Canvas.Top="`+num(top)+`" Width="`+num(width)+`" Height="`+num(height)+`" RadiusX="8" RadiusY="8" Opacity="0.30" Fill="{DynamicResource ColorBrush7}" Stroke="{DynamicResource ColorBrush3}" StrokeThickness="0.8" />`)
		for _, pair := range [][2]string{{data.InsetLines, "0.7"}, {data.InsetIslands, "1.2"}} {
			parts = append(parts, `<Path Data="`+calcAttr(pair[0])+`" Fill="{DynamicResource ColorBrush3}" Stroke="{DynamicResource ColorBrush3}" StrokeThickness="`+pair[1]+`" Opacity="0.8" />`)
		}
		parts = append(parts, `<TextBlock Canvas.Left="`+num(left+6)+`" Canvas.Top="`+num(top+6)+`" Text="`+calcAttr(s.i18n.Tf("visit.map_inset", lang))+`" FontSize="10" Foreground="{DynamicResource ColorBrush3}" />`)
	}
	return `<Viewbox Height="440" Stretch="Uniform" HorizontalAlignment="Center" Margin="0,10,0,0"><Canvas Width="1000" Height="880">` +
		strings.Join(parts, "") + `</Canvas></Viewbox>` + s.visitMapLegend(lang)
}

func (s *Server) buildVisitRank(sm *visitSummary, lang string) string {
	rows := make([]visitCount, 0, len(sm.Provinces))
	for _, item := range sm.Provinces {
		if item.Name != "" && item.Count > 0 {
			rows = append(rows, item)
		}
	}
	if len(rows) > 12 {
		rows = rows[:12]
	}
	if len(rows) == 0 {
		return s.calcNote(s.i18n.Tf("visit.rank_empty", lang), "0,8,0,0")
	}
	peak := rows[0].Count
	var parts []string
	for index, item := range rows {
		fill := int(roundFloat(float64(item.Count)/float64(peak)*100, 0))
		if fill < 8 {
			fill = 8
		}
		label := item.Name
		if visitIsEN(lang) {
			if en := visitPlaceEN(item.Name); en != "" {
				label = en
			}
		}
		margin := "0,10,0,0"
		if index > 0 {
			margin = "0,8,0,0"
		}
		parts = append(parts,
			`<Grid Margin="`+margin+`"><Grid.ColumnDefinitions><ColumnDefinition Width="70" /><ColumnDefinition Width="1*" /><ColumnDefinition Width="Auto" /></Grid.ColumnDefinitions>`+
				`<TextBlock Grid.Column="0" Text="`+calcAttr(label)+`" FontSize="12" VerticalAlignment="Center" Foreground="{DynamicResource ColorBrush2}" />`+
				`<Grid Grid.Column="1" Height="10" Margin="8,0,10,0" VerticalAlignment="Center"><Grid.ColumnDefinitions><ColumnDefinition Width="`+strconv.Itoa(fill)+`*" /><ColumnDefinition Width="`+strconv.Itoa(100-fill)+`*" /></Grid.ColumnDefinitions>`+
				`<Border Grid.ColumnSpan="2" Height="10" CornerRadius="5" Background="{DynamicResource ColorBrush7}" />`+
				`<Border Grid.Column="0" Height="10" CornerRadius="5" Opacity="0.62" Background="{DynamicResource ColorBrush1}" /></Grid>`+
				`<TextBlock Grid.Column="2" Text="`+calcAttr(s.i18n.Tf("visit.rank_value", lang, "count", strconv.Itoa(item.Count)))+`" FontSize="11" VerticalAlignment="Center" Foreground="{DynamicResource ColorBrush3}" /></Grid>`)
	}
	return strings.Join(parts, "")
}

func (s *Server) buildVisitRegions(sm *visitSummary, lang string) string {
	var parts []string
	groups := []struct {
		key  string
		rows []visitRegion
	}{
		{"visit.region_cn", sm.RegionsCN},
		{"visit.region_abroad", sm.RegionsAbroad},
	}
	for _, group := range groups {
		rows := group.rows
		if len(rows) > 8 {
			rows = rows[:8]
		}
		total := 0
		for _, item := range rows {
			total += item.Count
		}
		parts = append(parts, `<TextBlock Text="`+calcAttr(s.i18n.Tf(group.key, lang, "count", strconv.Itoa(total)))+`" FontSize="12" FontWeight="Bold" Margin="0,10,0,4" Foreground="{DynamicResource ColorBrush2}" />`)
		if len(rows) == 0 {
			parts = append(parts, s.calcNote(s.i18n.Tf("visit.region_empty", lang), "0,0,0,0"))
			continue
		}
		for _, item := range rows {
			text := s.visitRegionText(item.Code, item.City, item.Province, lang)
			parts = append(parts, `<Grid Margin="0,3,0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="1*" /><ColumnDefinition Width="Auto" /></Grid.ColumnDefinitions>`+
				`<TextBlock Grid.Column="0" Text="`+calcAttr(text)+`" FontSize="11" TextTrimming="CharacterEllipsis" Foreground="{DynamicResource ColorBrush2}" />`+
				`<TextBlock Grid.Column="1" Text="`+strconv.Itoa(item.Count)+`" FontSize="11" Margin="10,0,0,0" Foreground="{DynamicResource ColorBrush3}" /></Grid>`)
		}
	}
	return strings.Join(parts, "")
}

func (s *Server) buildVisitTrend(sm *visitSummary, lang string) string {
	trend := sm.Trend
	if len(trend) == 0 {
		return s.calcNote(s.i18n.Tf("visit.region_empty", lang), "0,8,0,0")
	}
	peak := 1
	for _, item := range trend {
		if item.Count > peak {
			peak = item.Count
		}
	}
	var cells []string
	var columns []string
	for index, item := range trend {
		height := int(roundFloat(float64(item.Count)/float64(peak)*84, 0))
		if height < 3 {
			height = 3
		}
		label := item.Date
		if index == len(trend)-1 {
			label = s.i18n.Tf("visit.trend_today", lang)
		} else if len(label) > 5 {
			label = label[5:]
		}
		cells = append(cells, `<StackPanel Grid.Column="`+strconv.Itoa(index)+`" VerticalAlignment="Bottom">`+
			`<TextBlock Text="`+strconv.Itoa(item.Count)+`" FontSize="9" HorizontalAlignment="Center" Margin="0,0,0,3" Foreground="{DynamicResource ColorBrush3}" />`+
			`<Border Height="`+strconv.Itoa(height)+`" Margin="3,0" CornerRadius="3,3,0,0" Opacity="0.6" Background="{DynamicResource ColorBrush1}" />`+
			`<TextBlock Text="`+calcAttr(label)+`" FontSize="8" HorizontalAlignment="Center" Margin="0,4,0,0" Foreground="{DynamicResource ColorBrush3}" /></StackPanel>`)
		columns = append(columns, `<ColumnDefinition Width="1*" />`)
	}
	return `<Grid Height="125" Margin="0,10,0,0"><Grid.ColumnDefinitions>` + strings.Join(columns, "") + `</Grid.ColumnDefinitions>` + strings.Join(cells, "") + `</Grid>`
}

func (s *Server) buildVisitRecent(sm *visitSummary, lang string) string {
	rows := sm.Recent
	if len(rows) > 8 {
		rows = rows[:8]
	}
	var parts []string
	for _, item := range rows {
		text := s.visitRegionText(item.Code, item.City, item.Province, lang)
		when := s.visitAgo(item.TS, lang)
		parts = append(parts, `<TextBlock Text="`+calcAttr(s.i18n.Tf("visit.recent_line", lang, "region", text, "when", when))+`" FontSize="11" TextWrapping="Wrap" Margin="0,4,0,0" Foreground="{DynamicResource ColorBrush2}" />`)
	}
	if len(parts) == 0 {
		return s.calcNote(s.i18n.Tf("visit.recent_empty", lang), "0,8,0,0")
	}
	return strings.Join(parts, "")
}

func visitDivider(margin string) string {
	return `<Border Height="1" Margin="` + margin + `"><Border.Background><LinearGradientBrush StartPoint="0,0" EndPoint="1,0"><GradientStop Color="#00000000" Offset="0" /><GradientStop Color="#33808080" Offset="0.5" /><GradientStop Color="#00000000" Offset="1" /></LinearGradientBrush></Border.Background></Border>`
}

// buildVisitPage 整页 XAML（不含 palette.apply）。
func (s *Server) buildVisitPage(sm *visitSummary, baseURL, ip, lang string, days int) string {
	if sm == nil {
		sm = emptyVisitSummary(days)
	}
	var body []string
	body = append(body, s.buildVisitKpis(sm, lang), visitDivider("0,22,0,4"))
	body = append(body, s.buildVisitMap(sm, lang))
	body = append(body, visitDivider("0,22,0,4"))
	body = append(body, s.calcHeading(s.i18n.Tf("visit.rank_title", lang), "0,6,0,0"))
	body = append(body, s.buildVisitRank(sm, lang))
	body = append(body, s.calcHeading(s.i18n.Tf("visit.region_title", lang), "0,16,0,0"))
	body = append(body, s.buildVisitRegions(sm, lang))
	body = append(body, s.calcHeading(s.i18n.Tf("visit.trend_title", lang, "days", strconv.Itoa(days)), "0,16,0,0"))
	body = append(body, s.buildVisitTrend(sm, lang))
	body = append(body, s.calcHeading(s.i18n.Tf("visit.recent_title", lang), "0,16,0,0"))
	body = append(body, s.buildVisitRecent(sm, lang))

	title := s.i18n.Tf("visit.title", lang)
	return `<local:MyCard Title="` + calcAttr(title) + `" CanSwap="False"><StackPanel Margin="25,40,23,20">` +
		`<TextBlock Text="` + calcAttr(title) + `" FontSize="24" FontWeight="Bold" Foreground="#FF000000" HorizontalAlignment="Center" />` +
		s.calcNote(s.i18n.Tf("visit.desc", lang, "days", strconv.Itoa(days)), "0,10,0,0") +
		strings.Join(body, "") +
		s.calcNavRow(baseURL+"/visit_page.json", "", "", lang) +
		`</StackPanel></local:MyCard>`
}

func visitLang(r *http.Request, s *Server, ip string) string {
	for _, chunk := range strings.Split(r.URL.RawQuery, "&") {
		if strings.HasPrefix(chunk, "lang=") {
			return normalizeLang(strings.TrimPrefix(chunk, "lang="))
		}
	}
	return s.profiles.langFor(ip)
}

// handleVisit 处理 /visit_page.json 与 /visit_page.xaml。
func (s *Server) handleVisit(w http.ResponseWriter, r *http.Request, ip string) bool {
	switch r.URL.Path {
	case "/visit_page.json":
		lang := visitLang(r, s, ip)
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Cache-Control", noStore)
		fmt.Fprintf(w, `{"Title": %s, "Description": %s}`,
			jsonString(s.i18n.Tf("visit.title", lang)),
			jsonString(s.i18n.Tf("visit.desc", lang, "days", strconv.Itoa(visitDays))))
		return true
	case "/visit_page.xaml":
		lang := visitLang(r, s, ip)
		base := s.verify.base(requestOrigin(r))
		var sm *visitSummary
		func() {
			defer func() {
				if recover() != nil {
					sm = emptyVisitSummary(visitDays)
				}
			}()
			sm = s.stats.summary(visitDays)
		}()
		if sm == nil {
			sm = emptyVisitSummary(visitDays)
		}
		page := s.buildVisitPage(sm, base, ip, lang, visitDays)
		page = applyPalette(page, s.profiles.paletteFor(ip))
		s.writeXAMLPage(w, r, page, isPCLCE(r.UserAgent()))
		return true
	}
	return false
}

func roundFloat(v float64, digits int) float64 {
	pow := 1.0
	for i := 0; i < digits; i++ {
		pow *= 10
	}
	if v < 0 {
		return -roundFloat(-v, digits)
	}
	return float64(int64(v*pow+0.5)) / pow
}
