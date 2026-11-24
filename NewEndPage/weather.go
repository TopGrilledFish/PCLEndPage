package main

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

type weatherEntry struct {
	at   time.Time
	body string
}

type geoEntry struct {
	at       time.Time
	city     string
	province string
}

// GeoService IP 定位（ip-api，24 小时缓存）。
type GeoService struct {
	cfg   *Config
	hc    *http.Client
	mu    sync.Mutex
	cache map[string]geoEntry
}

// WeatherService 天气获取与 XAML 生成（按城市缓存 3 小时）。
type WeatherService struct {
	cfg   *Config
	uapi  *UapiClient
	i18n  *I18n
	geo   *GeoService
	mu    sync.Mutex
	cache map[string]weatherEntry
}

func newWeatherService(cfg *Config, u *UapiClient, i18n *I18n) *WeatherService {
	timeout := time.Duration(cfg.GeoTimeout * float64(time.Second))
	if timeout <= 0 {
		timeout = 4 * time.Second
	}
	return &WeatherService{
		cfg:   cfg,
		uapi:  u,
		i18n:  i18n,
		geo:   &GeoService{cfg: cfg, hc: &http.Client{Timeout: timeout}, cache: map[string]geoEntry{}},
		cache: map[string]weatherEntry{},
	}
}

// get 返回天气卡片 XAML；失败时返回提示条。
func (w *WeatherService) get(ip string) string {
	if !w.cfg.EnableWeather {
		return ""
	}
	city := strings.TrimSpace(w.cfg.WeatherCity)
	if city == "" && w.cfg.EnableGeo {
		city = w.geo.locate(ip)
	}
	key := city
	if key == "" {
		key = "__server__"
	}
	ttl := time.Duration(w.cfg.WeatherCacheSeconds) * time.Second
	if ttl <= 0 {
		ttl = 3 * time.Hour
	}
	w.mu.Lock()
	entry, ok := w.cache[key]
	w.mu.Unlock()
	if ok && time.Since(entry.at) < ttl {
		return entry.body
	}
	body := w.fetch(city)
	if body == "" {
		return ""
	}
	w.mu.Lock()
	w.cache[key] = weatherEntry{at: time.Now(), body: body}
	w.mu.Unlock()
	return body
}

func (w *WeatherService) fetch(city string) string {
	api := strings.TrimSpace(w.cfg.WeatherAPI)
	if api == "" {
		return ""
	}
	target := api
	if city != "" {
		target += "?city=" + url.QueryEscape(city)
	}
	payload, status := w.uapi.getJSON(target)
	if payload == nil || status < 200 || status >= 300 {
		return w.hint(w.i18n.T("weather.unavailable"))
	}
	temp, ok := asFloat(payload["temperature"])
	if !ok {
		return w.hint(w.i18n.T("weather.unavailable"))
	}
	descRaw := strField(payload, "description")
	if descRaw == "" {
		descRaw = strField(payload, "weather")
	}
	location := strField(payload, "city")
	if district := strField(payload, "district"); district != "" && district != location {
		location += " · " + district
	}
	if location == "" {
		location = w.i18n.T("weather.here")
	}
	wind := w.wind(strField(payload, "wind_direction"), strField(payload, "wind_power"))
	humidity := ""
	if h := strField(payload, "humidity"); h != "" {
		humidity = strings.ReplaceAll(w.i18n.T("weather.humidity"), "{value}", h)
	}
	return w.buildXAML(location, int(math.Round(temp)), w.desc(descRaw), wind, humidity, beijingClock(), "C")
}

func (w *WeatherService) classify(raw string) string {
	switch {
	case strings.ContainsAny(raw, "雷雹电"):
		return "thunder"
	case strings.ContainsAny(raw, "雪凇霜冰粒"):
		return "snow"
	case strings.Contains(raw, "雨"):
		return "rain"
	case strings.ContainsAny(raw, "雾霾沙尘"):
		return "fog"
	case strings.ContainsAny(raw, "阴云"):
		return "cloudy"
	case strings.ContainsAny(raw, "晴"):
		return "clear"
	}
	return ""
}

func (w *WeatherService) desc(raw string) string {
	if raw == "" {
		return ""
	}
	if w.i18n.Has("weather.desc." + raw) {
		return w.i18n.T("weather.desc." + raw)
	}
	if kind := w.classify(raw); kind != "" {
		return w.i18n.T("weather.kind." + kind)
	}
	return raw
}

func (w *WeatherService) wind(dir, power string) string {
	dirName := ""
	for _, p := range []struct{ prefix, key string }{
		{"东南", "se"}, {"西北", "nw"}, {"东北", "ne"}, {"西南", "sw"},
		{"东", "e"}, {"南", "s"}, {"西", "w"}, {"北", "n"},
	} {
		if strings.HasPrefix(dir, p.prefix) {
			dirName = w.i18n.T("weather.wind_dir." + p.key)
			break
		}
	}
	level := leadingDigits(power)
	levelText := ""
	if level != "" {
		levelText = strings.ReplaceAll(w.i18n.T("weather.wind_level"), "{level}", level)
	} else if power != "" {
		levelText = power
	}
	return dirName + levelText
}

func (w *WeatherService) tip(raw string) string {
	kind := w.classify(raw)
	tips := w.i18n.List("weather.tips." + kind)
	if len(tips) == 0 {
		return ""
	}
	return tips[hashCode(raw+kind)%len(tips)]
}

func (w *WeatherService) hint(text string) string {
	return `<local:MyHint Theme="Yellow" Text="` + escapeAttr(text) + `" />`
}

func (w *WeatherService) buildXAML(location string, temp int, desc, wind, humidity, updatedAt, unit string) string {
	unit = strings.ToUpper(unit)
	if unit != "F" {
		unit = "C"
	}
	detail := wind
	if humidity != "" {
		if detail != "" {
			detail += " · "
		}
		detail += humidity
	}
	var b strings.Builder
	b.WriteString(`<Border CornerRadius="10" Padding="16,16" Margin="0,0,0,0" Background="{DynamicResource ColorBrush7}"><StackPanel>`)
	b.WriteString(`<StackPanel Orientation="Horizontal" HorizontalAlignment="Center">`)
	fmt.Fprintf(&b, `<TextBlock Text="%d°%s" FontSize="36" FontWeight="Bold" Foreground="{DynamicResource ColorBrush1}" />`, temp, unit)
	if desc != "" {
		fmt.Fprintf(&b, `<TextBlock Text=" %s" FontSize="15" VerticalAlignment="Bottom" Foreground="{DynamicResource ColorBrush3}" Margin="8,0,0,8" />`, escapeAttr(desc))
	}
	b.WriteString(`</StackPanel>`)
	if location != "" {
		fmt.Fprintf(&b, `<TextBlock Text="%s" FontSize="11" HorizontalAlignment="Center" Foreground="{DynamicResource ColorBrush3}" Margin="0,2,0,0" />`, escapeAttr(location))
	}
	b.WriteString(`<Border Height="1" Margin="0,12,0,12"><Border.Background><LinearGradientBrush StartPoint="0,0" EndPoint="1,0"><GradientStop Color="#00000000" Offset="0" /><GradientStop Color="#33808080" Offset="0.5" /><GradientStop Color="#00000000" Offset="1" /></LinearGradientBrush></Border.Background></Border>`)
	if detail != "" {
		fmt.Fprintf(&b, `<TextBlock Text="%s" FontSize="11" HorizontalAlignment="Center" Foreground="{DynamicResource ColorBrush3}" />`, escapeAttr(detail))
	}
	if updatedAt != "" {
		text := strings.ReplaceAll(w.i18n.T("weather.updated_at"), "{time}", updatedAt)
		fmt.Fprintf(&b, `<TextBlock Text="%s" FontSize="10" HorizontalAlignment="Center" Foreground="{DynamicResource ColorBrush3}" Margin="0,4,0,0" />`, escapeAttr(text))
	}
	if tip := w.tip(desc); tip != "" {
		fmt.Fprintf(&b, `<TextBlock Text="%s" FontSize="12" HorizontalAlignment="Center" Foreground="{DynamicResource ColorBrush1}" Margin="0,4,0,0" TextWrapping="Wrap" TextAlignment="Center" />`, escapeAttr(tip))
	}
	b.WriteString(`</StackPanel></Border>`)
	return b.String()
}

func (g *GeoService) locate(ip string) string {
	city, _ := g.locateRegion(ip)
	return city
}

// locateRegion 返回 (城市, 省份)；省份取 ip-api 的 regionName。
func (g *GeoService) locateRegion(ip string) (string, string) {
	if ip == "" || isUnlocatable(ip) {
		return "", ""
	}
	ttl := time.Duration(g.cfg.GeoCacheHours) * time.Hour
	if ttl <= 0 {
		ttl = 24 * time.Hour
	}
	g.mu.Lock()
	entry, ok := g.cache[ip]
	g.mu.Unlock()
	if ok && time.Since(entry.at) < ttl {
		return entry.city, entry.province
	}
	city, province := g.query(ip)
	g.mu.Lock()
	g.cache[ip] = geoEntry{at: time.Now(), city: city, province: province}
	g.mu.Unlock()
	return city, province
}

func (g *GeoService) query(ip string) (string, string) {
	target := "http://ip-api.com/json/" + url.PathEscape(ip) + "?lang=zh-CN&fields=status,country,regionName,city"
	resp, err := g.hc.Get(target)
	if err != nil {
		return "", ""
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	var data map[string]any
	if json.Unmarshal(body, &data) != nil {
		return "", ""
	}
	if s, _ := data["status"].(string); s != "success" {
		return "", ""
	}
	city := strField(data, "city")
	province := strField(data, "regionName")
	if city == "" {
		city = province
	}
	return city, province
}

func isUnlocatable(ip string) bool {
	parsed := net.ParseIP(ip)
	if parsed == nil {
		return true
	}
	return parsed.IsPrivate() || parsed.IsLoopback() || parsed.IsLinkLocalUnicast() ||
		parsed.IsLinkLocalMulticast() || parsed.IsUnspecified() || parsed.IsMulticast()
}

func strField(m map[string]any, key string) string {
	if v, ok := m[key]; ok {
		switch x := v.(type) {
		case string:
			return strings.TrimSpace(x)
		case float64:
			return strconv.FormatFloat(x, 'f', -1, 64)
		}
	}
	return ""
}

func asFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(x), 64)
		return f, err == nil
	}
	return 0, false
}

func leadingDigits(s string) string {
	i := 0
	for i < len(s) && s[i] >= '0' && s[i] <= '9' {
		i++
	}
	return s[:i]
}

func beijingClock() string {
	return time.Now().In(time.FixedZone("CST", 8*60*60)).Format("15:04")
}
