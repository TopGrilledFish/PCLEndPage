package main

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// TextData 内置文案（问候语池、句子池）。
type TextData struct {
	GreetingSubs map[string][]string `json:"greeting_subs"`
	Quotes       []string            `json:"quotes"`
}

func newTextData(payload string) (*TextData, error) {
	var t TextData
	if err := json.Unmarshal([]byte(payload), &t); err != nil {
		return nil, fmt.Errorf("解析内置文案失败：%w", err)
	}
	return &t, nil
}

// FestivalItem 节日条目（公历或农历）。
type FestivalItem struct {
	Month int    `json:"month"`
	Day   int    `json:"day"`
	Rule  string `json:"rule"`
	Term  string `json:"term"`
	Key   string `json:"key"`
	LM    int    `json:"lm"`
	LD    int    `json:"ld"`
	Name  string `json:"name"`
	Msg   string `json:"msg"`
}

// CalendarData 农历数据与节日表。
type CalendarData struct {
	LunarInfo      []int          `json:"lunar_info"`
	Festivals      []FestivalItem `json:"festivals"`
	LunarFestivals []FestivalItem `json:"lunar_festivals"`
}

func newCalendarData(payload string) (*CalendarData, error) {
	var c CalendarData
	if err := json.Unmarshal([]byte(payload), &c); err != nil {
		return nil, fmt.Errorf("解析农历数据失败：%w", err)
	}
	return &c, nil
}

// BDate 北京时间日期信息。
type BDate struct {
	Today   time.Time
	Year    int
	Month   int
	Day     int
	Weekday int
	DateStr string
	Period  string
}

func beijingDate(now time.Time) BDate {
	t := now.In(time.FixedZone("CST", 8*60*60))
	today := time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
	period := "night"
	switch h := t.Hour(); {
	case h < 6:
		period = "dawn"
	case h < 11:
		period = "morning"
	case h < 14:
		period = "noon"
	case h < 18:
		period = "afternoon"
	case h < 23:
		period = "evening"
	}
	return BDate{
		Today:   today,
		Year:    t.Year(),
		Month:   int(t.Month()),
		Day:     t.Day(),
		Weekday: int(t.Weekday()),
		DateStr: t.Format("2006-01-02"),
		Period:  period,
	}
}

// hashCode 复刻旧版 djb2 变体。
func hashCode(text string) int {
	h := 5381
	for _, ch := range text {
		h = (int(int32(h<<5)) + h + int(ch)) & 0x7FFFFFFF
	}
	return h
}

func deterministicIndex(ip, dateStr, salt string, size int) int {
	if size <= 0 {
		return 0
	}
	return hashCode(ip+"|"+dateStr+"|"+salt) % size
}

func (s *Server) weekdayName(wd int) string {
	return s.i18n.T("date.weekday_prefix") + s.i18n.T("date.weekday."+strconv.Itoa(wd))
}

func (s *Server) pickGreeting(d BDate) string {
	return s.i18n.T("greeting."+d.Period) + s.i18n.T("home.greeting_open")
}

// pickGreetingSub 复刻旧版行为：旧版用点路径 has("greeting.sub.<period>.0")
// 判断翻译数组是否存在，而 zh-hans.json 的键是整串 "greeting.sub.<period>"，
// 因此实际总是落到 sub_plain 那一句。为对齐外观照旧。
func (s *Server) pickGreetingSub(d BDate, ip string) string {
	if s.i18n.Has("greeting.sub_plain") {
		return s.i18n.T("greeting.sub_plain")
	}
	base := s.text.GreetingSubs[d.Period]
	if len(base) == 0 {
		return ""
	}
	return base[deterministicIndex(ip, d.DateStr, "greeting_"+d.Period, len(base))]
}

func (s *Server) pickQuote(d BDate, ip string) string {
	if len(s.text.Quotes) == 0 {
		return ""
	}
	quote := s.text.Quotes[deterministicIndex(ip, d.DateStr, "quote", len(s.text.Quotes))]
	quote = strings.ReplaceAll(quote, "{date}", fmt.Sprintf("%d月%d日", d.Month, d.Day))
	quote = strings.ReplaceAll(quote, "{weekday}", s.weekdayName(d.Weekday))
	quote = strings.ReplaceAll(quote, "{year}", strconv.Itoa(d.Year))
	return quote
}

func lunarDayName(day int) string {
	names := []string{
		"初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
		"十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
		"廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
	}
	if day >= 1 && day <= len(names) {
		return names[day-1]
	}
	return ""
}

func (s *Server) lunarText(d BDate) string {
	if !s.cfg.EnableLunar {
		return ""
	}
	month, day, isLeap, term, ok := s.lunar.info(d.Today)
	if !ok {
		return ""
	}
	monthName := s.i18n.T("lunar.month." + strconv.Itoa(month))
	if isLeap {
		monthName = s.i18n.T("lunar.leap") + monthName
	}
	text := s.i18n.T("lunar.prefix") + monthName + lunarDayName(day)
	if term != "" {
		if s.i18n.Has("lunar.term." + term) {
			text += " · " + s.i18n.T("lunar.term." + term)
		} else {
			text += " · " + term
		}
	}
	return text
}

// ---------- 节日 ----------

var termKeyToName = map[string]string{"qingming": "清明"}

func (s *Server) ruleMatch(rule string, today time.Time) bool {
	wd := today.Weekday()
	day := today.Day()
	switch rule {
	case "mothers_day":
		return today.Month() == time.May && wd == time.Sunday && day >= 8 && day <= 14
	case "fathers_day":
		return today.Month() == time.June && wd == time.Sunday && day >= 15 && day <= 21
	case "thanksgiving":
		return today.Month() == time.November && wd == time.Thursday && day >= 22 && day <= 28
	}
	return false
}

func (s *Server) solarFestivalMatches(f FestivalItem, today time.Time) bool {
	switch {
	case f.Rule != "":
		return s.ruleMatch(f.Rule, today)
	case f.Term != "":
		name := f.Term
		if v, ok := termKeyToName[f.Term]; ok {
			name = v
		}
		return s.lunar.termFor(today) == name
	default:
		return f.Month == int(today.Month()) && f.Day == today.Day()
	}
}

func (s *Server) lunarFestivalMatches(f FestivalItem, today time.Time) bool {
	if f.LM == 0 {
		return false
	}
	year, lm, ld, _ := s.lunar.solarToLunarFull(today)
	if lm != f.LM {
		return false
	}
	if f.LD == 0 {
		return ld == s.lunar.monthDays(year, f.LM)
	}
	return ld == f.LD
}

func (s *Server) festivalKey(f FestivalItem) string {
	if f.Key != "" {
		return "festival." + f.Key
	}
	if f.LM > 0 {
		return fmt.Sprintf("festival.lunar.%d-%d", f.LM, f.LD)
	}
	return fmt.Sprintf("festival.solar.%d-%d", f.Month, f.Day)
}

func (s *Server) festivalName(f FestivalItem) string {
	if key := s.festivalKey(f) + ".name"; s.i18n.Has(key) {
		return s.i18n.T(key)
	}
	return f.Name
}

func (s *Server) festivalMsg(f FestivalItem) string {
	if key := s.festivalKey(f) + ".msg"; s.i18n.Has(key) {
		return s.i18n.T(key)
	}
	return f.Msg
}

func (s *Server) festivalToday(d BDate) (name, msg string, ok bool) {
	for _, f := range s.cal.Festivals {
		if s.solarFestivalMatches(f, d.Today) {
			return s.festivalName(f), s.festivalMsg(f), true
		}
	}
	for _, f := range s.cal.LunarFestivals {
		if s.lunarFestivalMatches(f, d.Today) {
			return s.festivalName(f), s.festivalMsg(f), true
		}
	}
	return "", "", false
}

func (s *Server) solarFestivalDate(f FestivalItem, today time.Time) time.Time {
	switch {
	case f.Rule != "":
		for y := today.Year(); y <= today.Year()+1; y++ {
			if dt, ok := s.ruleDate(f.Rule, y); ok && !dt.Before(today) {
				return dt
			}
		}
		return time.Time{}
	case f.Term == "qingming":
		for y := today.Year(); y <= today.Year()+1; y++ {
			if dt := qingmingDate(y); !dt.Before(today) {
				return dt
			}
		}
		return time.Time{}
	default:
		dt := time.Date(today.Year(), time.Month(f.Month), f.Day, 0, 0, 0, 0, time.UTC)
		if dt.Before(today) {
			dt = time.Date(today.Year()+1, time.Month(f.Month), f.Day, 0, 0, 0, 0, time.UTC)
		}
		return dt
	}
}

func (s *Server) ruleDate(rule string, year int) (time.Time, bool) {
	switch rule {
	case "mothers_day":
		return nthWeekday(year, time.May, time.Sunday, 2)
	case "fathers_day":
		return nthWeekday(year, time.June, time.Sunday, 3)
	case "thanksgiving":
		return nthWeekday(year, time.November, time.Thursday, 4)
	}
	return time.Time{}, false
}

func nthWeekday(year int, month time.Month, wd time.Weekday, n int) (time.Time, bool) {
	count := 0
	for day := 1; day <= 31; day++ {
		t := time.Date(year, month, day, 0, 0, 0, 0, time.UTC)
		if t.Month() != month {
			break
		}
		if t.Weekday() == wd {
			count++
			if count == n {
				return t, true
			}
		}
	}
	return time.Time{}, false
}

func qingmingDate(year int) time.Time {
	c := 4.81
	if year < 2000 {
		c = 5.59
	}
	day := int(float64(year)*0.2422+c) - year/4
	return time.Date(year, time.April, day, 0, 0, 0, 0, time.UTC)
}

func countdownXAML(text string) string {
	return `<Border HorizontalAlignment="Right" VerticalAlignment="Top" Margin="0,16,16,0" Background="#59000000" CornerRadius="12" Padding="12,8,12,8"><TextBlock Text="` +
		escapeAttr(text) + `" FontSize="12" FontWeight="Bold" Foreground="White" /></Border>`
}

func (s *Server) countdownXAML(d BDate) string {
	bestDays := -1
	bestName := ""
	consider := func(name string, days int) {
		if days < 0 || name == "" {
			return
		}
		if bestDays == -1 || days < bestDays {
			bestDays, bestName = days, name
		}
	}
	for _, f := range s.cal.Festivals {
		date := s.solarFestivalDate(f, d.Today)
		if date.IsZero() {
			continue
		}
		consider(s.festivalName(f), int(date.Sub(d.Today).Hours()/24))
	}
	year, _, _, _ := s.lunar.solarToLunarFull(d.Today)
	for _, f := range s.cal.LunarFestivals {
		if f.LM == 0 {
			continue
		}
		var date time.Time
		if f.LD == 0 {
			date = s.lunar.lunarToSolar(year, f.LM, s.lunar.monthDays(year, f.LM))
		} else {
			date = s.lunar.lunarToSolar(year, f.LM, f.LD)
		}
		if date.Before(d.Today) {
			if f.LD == 0 {
				date = s.lunar.lunarToSolar(year+1, f.LM, s.lunar.monthDays(year+1, f.LM))
			} else {
				date = s.lunar.lunarToSolar(year+1, f.LM, f.LD)
			}
		}
		consider(s.festivalName(f), int(date.Sub(d.Today).Hours()/24))
	}
	if bestDays < 0 || bestName == "" {
		return ""
	}
	text := ""
	switch {
	case bestDays == 0:
		text = strings.ReplaceAll(s.i18n.T("lunar.countdown_today"), "{name}", bestName)
	case bestDays == 1:
		text = strings.ReplaceAll(s.i18n.T("lunar.countdown_one"), "{name}", bestName)
	default:
		text = strings.ReplaceAll(s.i18n.T("lunar.countdown_left"), "{name}", bestName)
		text = strings.ReplaceAll(text, "{days}", strconv.Itoa(bestDays))
	}
	return countdownXAML(text)
}

// ---------- 主页渲染 ----------

func (s *Server) buildHome(ip, origin string, marker bool) (string, error) {
	d := beijingDate(time.Now())
	base := strings.TrimRight(s.cfg.BaseURL, "/")
	if base == "" {
		base = strings.TrimRight(origin, "/")
	}
	aiEntry := escapeAttr(base + "/ai_page.json")
	calcEntry := escapeAttr(base + "/calc.json")
	dateMain := dateMainServer(s.i18n, d.Month, d.Day)
	if marker {
		dateMain = dateMainMarker(d.Month, d.Day)
	}
	siteItems := strings.ReplaceAll(s.siteItems, "https://end.g-fish.dpdns.org", base)
	footerCard := strings.ReplaceAll(s.footerCard, "https://end.g-fish.dpdns.org", base)
	footerCard = strings.ReplaceAll(footerCard, "__VERSION__", s.cfg.Version)
	values := map[string]string{
		"BASE_URL":         escapeURL(base),
		"WALLPAPER_URL":    escapeURL(base + "/images/wallpaper.jpg?v=" + d.Today.Format("20060102")),
		"DATE_MONTH":       escapeAttr(s.i18n.T("date.month." + strconv.Itoa(d.Month))),
		"DATE_DAY":         strconv.Itoa(d.Day),
		"DATE_MAIN":        dateMain,
		"T_WEEKDAY":        escapeAttr(s.weekdayName(d.Weekday)),
		"GREETING":         escapeAttr(s.pickGreeting(d)),
		// 整句问候（含句末标点，不含玩家名）：模板里一个令牌搞定，PCL2 / PCL-CE 表现一致
		"GREETING_TEXT":    escapeAttr(s.i18n.T("greeting."+d.Period) + s.i18n.T("home.greeting_close")),
		"GREETING_SUB":     escapeAttr(s.pickGreetingSub(d, ip)),
		"QUOTE":            escapeAttr(s.pickQuote(d, ip)),
		"BANNER":           newSiteHint + s.homeBanners(),
		"T_WELCOME":        escapeAttr(s.i18n.T("home.welcome")),
		"T_GREETING_CLOSE": escapeAttr(s.i18n.T("home.greeting_close")),
		"T_MONTH_SUFFIX":   escapeAttr(s.i18n.T("date.month_suffix")),
		"T_DAY_SUFFIX":     escapeAttr(s.i18n.T("date.day_suffix")),
		"T_QUOTE_LABEL":    escapeAttr(s.i18n.T("home.quote_label")),
		"T_SITES_TITLE":    escapeAttr(s.i18n.T("home.sites_title")),
		"T_REFRESH_HINT":   escapeAttr(s.i18n.T("home.refresh_hint")),
		"ACTION_BUTTONS":   s.actionButtons,
		"SITE_ITEMS":       siteItems,
		"FOOTER_CARD":      footerCard,
		"AI_ENTRY":         aiEntry,
		"CALC_ENTRY":       calcEntry,
	}
	if name, msg, ok := s.festivalToday(d); ok {
		text := strings.ReplaceAll(s.i18n.T("lunar.festival_today"), "{name}", name)
		text = strings.ReplaceAll(text, "{msg}", msg)
		values["FESTIVAL_BANNER"] = `<local:MyHint Theme="Red" Margin="0,0,0,12" Text="` + escapeAttr(text) + `" />`
	}
	values["COUNTDOWN_BODY"] = s.countdownXAML(d)
	if text := s.lunarText(d); text != "" {
		values["LUNAR"] = `<TextBlock Text="` + escapeAttr(text) + `" FontSize="12" Foreground="#D9FFFFFF" HorizontalAlignment="Center" Margin="0,6,0,0" />`
	}
	if !s.cfg.EnableWeather {
		values["WEATHER_BODY"] = `<local:MyHint Theme="Yellow" Margin="0,0,0,0" Text="天气获取失败（已关闭天气）。" />`
	} else {
		values["WEATHER_BODY"] = s.weather.get(ip)
	}

	out := replacePlaceholders(s.template, values)
	out = strings.ReplaceAll(out, "__AI_ENTRY__", aiEntry)
	out = strings.ReplaceAll(out, "__CALC_ENTRY__", calcEntry)
	return out, nil
}

// ---------- 公告（照旧版 xaml.py 的公告卡片） ----------

// homeBanners 读取旧版 store.json 的 banners/homepage_banner，生成公告卡片 XAML。
func (s *Server) homeBanners() string {
	store := readStoreFile(s.cfg.StoreFile)
	if store == nil {
		return ""
	}
	return buildMultiBanner(store["banners"]) + buildSingleBanner(store["homepage_banner"])
}

// buildMultiBanner 多公告轮播：每行一条，≥2 条才启用，最多 4 条，每条显示 8 秒。
func buildMultiBanner(v any) string {
	raw, _ := v.(string)
	var lines []string
	for _, line := range strings.Split(strings.ReplaceAll(raw, "\r\n", "\n"), "\n") {
		if t := strings.TrimSpace(line); t != "" {
			lines = append(lines, t)
		}
	}
	if len(lines) < 2 {
		return ""
	}
	count := len(lines)
	if count > 4 {
		count = 4
	}
	const per = 8
	var anims, texts []string
	for i := 0; i < count; i++ {
		onAt, offAt := i*per, (i+1)*per-1
		idx := strconv.Itoa(i)
		texts = append(texts, `<TextBlock x:Name="mb`+idx+`" Text="`+escapeAttr(lines[i])+`" FontSize="15" LineHeight="24" Foreground="{DynamicResource ColorBrush1}" VerticalAlignment="Center" Opacity="0"/>`)
		anims = append(anims, `<DoubleAnimation Storyboard.TargetName="mb`+idx+`" Storyboard.TargetProperty="Opacity" From="0" To="1" Duration="0:0:1" BeginTime="0:0:`+strconv.Itoa(onAt)+`"/>`)
		anims = append(anims, `<DoubleAnimation Storyboard.TargetName="mb`+idx+`" Storyboard.TargetProperty="Opacity" From="1" To="0" Duration="0:0:1" BeginTime="0:0:`+strconv.Itoa(offAt)+`"/>`)
	}
	return `<local:MyCard Title="公告" Margin="0,0,0,12" CanSwap="True" IsSwapped="False"><StackPanel Margin="25,36,23,16" ClipToBounds="True" Height="28"><Grid><Grid.Triggers><EventTrigger RoutedEvent="FrameworkElement.Loaded"><BeginStoryboard><Storyboard RepeatBehavior="Forever">` +
		strings.Join(anims, "") +
		`</Storyboard></BeginStoryboard></EventTrigger></Grid.Triggers>` +
		strings.Join(texts, "") +
		`</Grid></StackPanel></local:MyCard>`
}

// buildSingleBanner 单条公告：≤40 字淡入，超长跑马灯。
func buildSingleBanner(v any) string {
	obj, ok := v.(map[string]any)
	if !ok {
		return ""
	}
	enabled, _ := obj["enabled"].(bool)
	text, _ := obj["text"].(string)
	text = strings.TrimSpace(text)
	if !enabled || text == "" {
		return ""
	}
	safe := escapeAttr(text)
	if len([]rune(text)) <= 40 {
		return `<local:MyCard Title="公告" Margin="0,0,0,12" CanSwap="True" IsSwapped="False"><StackPanel Margin="25,36,23,16"><StackPanel.Triggers><EventTrigger RoutedEvent="FrameworkElement.Loaded"><BeginStoryboard><Storyboard><DoubleAnimation Storyboard.TargetName="bqFade" Storyboard.TargetProperty="Opacity" From="0" To="1" Duration="0:0:0.6"/></Storyboard></BeginStoryboard></EventTrigger></StackPanel.Triggers><TextBlock x:Name="bqFade" TextWrapping="Wrap" FontSize="15" LineHeight="24" Foreground="{DynamicResource ColorBrush1}" Text="` +
			safe +
			`"/></StackPanel></local:MyCard>`
	}
	textWidth := len([]rune(text)) * 15
	toX := -(textWidth + 40)
	duration := (500 - toX) / 50
	if duration < 8 {
		duration = 8
	}
	if duration > 30 {
		duration = 30
	}
	return `<local:MyCard Title="公告" Margin="0,0,0,12" CanSwap="True" IsSwapped="False"><StackPanel Margin="25,36,23,16" ClipToBounds="True" Height="28"><TextBlock Text="` +
		safe +
		`" FontSize="15" LineHeight="24" Foreground="{DynamicResource ColorBrush1}" VerticalAlignment="Center"><TextBlock.RenderTransform><TranslateTransform x:Name="bqMarquee" X="0"/></TextBlock.RenderTransform><TextBlock.Triggers><EventTrigger RoutedEvent="FrameworkElement.Loaded"><BeginStoryboard><Storyboard RepeatBehavior="Forever"><DoubleAnimation Storyboard.TargetName="bqMarquee" Storyboard.TargetProperty="X" From="500" To="` +
		strconv.Itoa(toX) +
		`" Duration="0:0:` + strconv.Itoa(duration) + `"/></Storyboard></BeginStoryboard></EventTrigger></TextBlock.Triggers></TextBlock></StackPanel></local:MyCard>`
}

