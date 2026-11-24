package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"hash/crc32"
	"html"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// ---------- 配置 ----------

type StringList []string

func (s *StringList) UnmarshalJSON(b []byte) error {
	if len(b) > 0 && b[0] == '"' {
		var v string
		if err := json.Unmarshal(b, &v); err != nil {
			return err
		}
		*s = splitKeys(v)
		return nil
	}
	var arr []string
	if err := json.Unmarshal(b, &arr); err != nil {
		return err
	}
	*s = arr
	return nil
}

func splitKeys(raw string) []string {
	parts := strings.FieldsFunc(raw, func(r rune) bool {
		return r == ',' || r == ';' || r == ' ' || r == '\n' || r == '\r' || r == '\t'
	})
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if v := strings.TrimSpace(p); v != "" {
			out = append(out, v)
		}
	}
	return out
}

type Config struct {
	BaseURL             string     `json:"base_url"`
	Version             string     `json:"version"`
	Host                string     `json:"host"`
	Port                int        `json:"port"`
	EnableWallpaper     bool       `json:"enable_wallpaper"`
	EnableSaying        bool       `json:"enable_saying"`
	EnableLunar         bool       `json:"enable_lunar"`
	EnableWeather       bool       `json:"enable_weather"`
	EnableGeo           bool       `json:"enable_geo"`
	WeatherCity         string     `json:"weather_city"`
	WeatherAPI          string     `json:"weather_api"`
	LunarAPI            string     `json:"lunar_api"`
	SayingsAPI          string     `json:"sayings_api"`
	SayingSource        string     `json:"saying_source"`
	HTTPTimeout         float64    `json:"http_timeout"`
	GeoTimeout          float64    `json:"geo_timeout"`
	GeoCacheHours       int        `json:"geo_cache_hours"`
	WeatherCacheSeconds int        `json:"weather_cache_seconds"`
	TrustProxyHeaders   bool       `json:"trust_proxy_headers"`
	MaxRetries          int        `json:"max_retries"`
	UAPIKey             string     `json:"uapi_key"`
	UAPIKeys            StringList `json:"uapi_keys"`
	LegacyBackend       string     `json:"legacy_backend"`
	VerifyEnabled       bool       `json:"verify_enabled"`
	VerifyDays          int        `json:"verify_days"`
	VerifyFile          string     `json:"verify_file"`
	AdminToken          string     `json:"admin_token"`
	AdminTOTPSecret     string     `json:"admin_totp_secret"`
	AdminSessionHours   int        `json:"admin_session_hours"`
	LegacyLogFile       string     `json:"legacy_log_file"`
	LogFile             string     `json:"log_file"`
	StoreFile           string     `json:"store_file"`
	SiteName            string     `json:"site_name"`
	EnableAI            bool       `json:"enable_ai"`
	GuardClients        bool       `json:"guard_clients"`
}

func loadConfig(path string) (*Config, error) {
	cfg := &Config{
		Version:             "20261121",
		Host:                "127.0.0.1",
		Port:                8778,
		EnableWallpaper:     true,
		EnableSaying:        true,
		EnableLunar:         true,
		EnableWeather:       true,
		EnableGeo:           true,
		WeatherAPI:          "https://uapis.cn/api/v1/misc/weather",
		LunarAPI:            "https://uapis.cn/api/v1/misc/lunartime",
		SayingsAPI:          "https://uapis.cn/api/v1/saying/random",
		HTTPTimeout:         8,
		GeoTimeout:          4,
		GeoCacheHours:       24,
		WeatherCacheSeconds: 10800,
		TrustProxyHeaders:   true,
		MaxRetries:          2,
		LegacyBackend:       "127.0.0.1:8787",
		VerifyEnabled:       true,
		VerifyDays:          7,
		VerifyFile:          "/root/PCLEndPage/var/verify.json",
		AdminSessionHours:   8,
		LegacyLogFile:       "/root/PCLEndPage/var/server.log",
		LogFile:             "var/server.log",
		StoreFile:           "/root/PCLEndPage/var/store.json",
		SiteName:            "PCL2 主页",
		EnableAI:            true,
		GuardClients:        true,
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("读取配置失败：%w", err)
	}
	raw = bytes.TrimPrefix(raw, []byte{0xEF, 0xBB, 0xBF})
	if err := json.Unmarshal(raw, cfg); err != nil {
		return nil, fmt.Errorf("解析配置失败：%w", err)
	}
	cfg.UAPIKeys = dedupeKeys(append(cfg.UAPIKeys, cfg.UAPIKey))
	if cfg.WeatherAPI == "" {
		cfg.WeatherAPI = "https://uapis.cn/api/v1/misc/weather"
	}
	if cfg.LunarAPI == "" {
		cfg.LunarAPI = "https://uapis.cn/api/v1/misc/lunartime"
	}
	if cfg.SayingsAPI == "" {
		cfg.SayingsAPI = "https://uapis.cn/api/v1/saying/random"
	}
	return cfg, nil
}

func dedupeKeys(keys []string) StringList {
	seen := map[string]bool{}
	out := StringList{}
	for _, k := range keys {
		k = strings.TrimSpace(k)
		if k == "" || seen[k] {
			continue
		}
		seen[k] = true
		out = append(out, k)
	}
	return out
}

// ---------- 服务 ----------

type Server struct {
	cfg           *Config
	i18n          *I18n
	text          *TextData
	cal           *CalendarData
	template      string
	actionButtons string
	siteItems     string
	footerCard    string
	uapi          *UapiClient
	weather       *WeatherService
	saying        *SayingService
	lunar         *LunarService
	proxy         *httputil.ReverseProxy
	admin         *Admin
	newstats      *NewSiteStats
	started       time.Time
}

var datedRE = regexp.MustCompile(`^/(\d{4})/(\d{1,2})/(\d{1,2})(?:/(version|Custom\.xaml\.version|Custom\.xaml\.ini|Custom\.xaml|Custom-CE\.xaml|Custom-CE\.xaml\.ini))?/?$`)

const (
	markPrefix  = "/mark"
	xamlCT      = "application/xml; charset=utf-8"
	noStore     = "no-store, no-cache, must-revalidate, max-age=0, s-maxage=0"
	newSiteHint = `<local:MyHint Margin="0,0,0,12" Theme="Yellow" Text="您现在访问的是新版测试站点" />`
	// newSiteHintAXAML 是 AXAML 版（旧版页面已转 AXAML 时，注入 local: 会成为未声明前缀）。
	newSiteHintAXAML = `<pclcontrols:MyHint Margin="0,0,0,12" Theme="Yellow" Text="您现在访问的是新版测试站点" />`
	// ceSiteHint 是 /Custom-CE.xaml 渲染的 AXAML 主页顶部提示（WPF 模板先按此文案渲染，再整体转 AXAML）。
	ceSiteHint = `<local:MyHint Margin="0,0,0,12" Theme="Yellow" Text="您现在访问的是PCL2 CE 兼容版 完整体验请使用PCL2官方版" />`
	// ceSiteHintAXAML 用于旧版已转 AXAML 的页面（如未验证时反代回来的验证卡）。
	ceSiteHintAXAML = `<pclcontrols:MyHint Margin="0,0,0,12" Theme="Yellow" Text="您现在访问的是PCL2 CE 兼容版 完整体验请使用PCL2官方版" />`
	// htmlBanner 用于旧版 HTML 页面（如 /verify 访问验证页），样式与 nginx sub_filter 的横幅一致。
	htmlBanner = `<div style="position:fixed;top:0;left:0;right:0;z-index:99999;background:#f9d423;color:#000;text-align:center;padding:6px 0;font:14px/1.4 sans-serif;">您现在访问的是新版测试站点</div>`
)

func newServer(cfg *Config, dataDir, cfgPath string) (*Server, error) {
	s := &Server{
		cfg:           cfg,
		template:      mustReadAsset("assets/Custom.xaml"),
		actionButtons: mustReadAsset("assets/action_buttons.xaml"),
		siteItems:     mustReadAsset("assets/site_items.xaml"),
		footerCard:    mustReadAsset("assets/footer_card.xaml"),
		started:       time.Now(),
	}
	i18n, err := newI18n(mustReadAsset("assets/zh-hans.json"))
	if err != nil {
		return nil, err
	}
	s.i18n = i18n
	text, err := newTextData(mustReadAsset("assets/text.json"))
	if err != nil {
		return nil, err
	}
	s.text = text
	cal, err := newCalendarData(mustReadAsset("assets/calendar.json"))
	if err != nil {
		return nil, err
	}
	s.cal = cal
	s.uapi = newUapiClient(cfg)
	s.weather = newWeatherService(cfg, s.uapi, s.i18n)
	s.saying = newSayingService(cfg, s.uapi, dataDir, s.text)
	s.lunar = newLunarService(cfg, s.uapi, dataDir, s.cal)
	s.newstats = newNewSiteStats(filepath.Join(dataDir, "newstats.json"))
	s.proxy = newLegacyProxy(cfg.LegacyBackend)
	s.proxy.ModifyResponse = s.modifyLegacyResponse
	s.admin = newAdmin(cfg, cfgPath, s)
	return s, nil
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/admin") {
		s.admin.ServeHTTP(w, r)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		s.proxy.ServeHTTP(w, r)
		return
	}
	p := r.URL.Path
	ip := clientIP(r, s.cfg)
	switch {
	case p == "/" || p == "/Custom.xaml":
		s.routeHome(w, r, false, false, ip)
	case p == "/Custom-CE.xaml":
		s.routeHome(w, r, false, true, ip)
	case p == markPrefix || p == markPrefix+"/":
		s.routeHome(w, r, true, false, ip)
	case p == markPrefix+"/Custom.xaml":
		s.routeHome(w, r, true, false, ip)
	case p == markPrefix+"/Custom-CE.xaml":
		s.routeHome(w, r, true, true, ip)
	case p == "/Custom.xaml.version" || p == "/version" ||
		p == "/Custom.xaml.ini" || p == "/Custom-CE.xaml.ini":
		writeNoStore(w, s.staticVersion())
	case p == markPrefix+"/Custom.xaml.version" || p == markPrefix+"/version" ||
		p == markPrefix+"/Custom.xaml.ini" || p == markPrefix+"/Custom-CE.xaml.ini":
		writeNoStore(w, s.staticVersion())
	case p == "/healthz":
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Cache-Control", noStore)
		io.WriteString(w, `{"ok":true,"service":"newendpage","uptime":`+
			strconv.FormatInt(int64(time.Since(s.started).Seconds()), 10)+`}`)
	case datedRE.MatchString(p):
		m := datedRE.FindStringSubmatch(p)
		if m[4] != "" && m[4] != "Custom.xaml" && m[4] != "Custom-CE.xaml" {
			writeNoStore(w, datedVersion(m[1], m[2], m[3]))
			return
		}
		s.routeHome(w, r, false, m[4] == "Custom-CE.xaml", ip)
	default:
		s.proxy.ServeHTTP(w, r)
	}
}

// routeHome 处理主页类请求：通过访问验证后用新版渲染，否则交给旧版
// （未验证时旧版返回看图验证页；CE 请求会带上 X-PCLCE 让旧版输出 AXAML）。
// axaml 只由请求路径决定：/Custom-CE.xaml 系列给 PCL-CE，其余给 PCL2 原版。
func (s *Server) routeHome(w http.ResponseWriter, r *http.Request, marker, axaml bool, ip string) {
	// 封禁 / 维护模式先于验证判断：旧版同样在验证页之前拦截。
	if page, hit := s.guardHome(ip); hit {
		s.writeXAMLPage(w, r, page, axaml)
		return
	}
	if !s.cfg.VerifyEnabled || s.verifyOK(ip) {
		s.newstats.hit(ip)
		s.handleHome(w, r, marker, axaml)
		return
	}
	// 旧版只认识 /Custom.xaml；CE 请求转成它，并让旧版把验证页输出为 AXAML。
	if axaml {
		r2 := r.Clone(r.Context())
		r2.URL.Path = "/Custom.xaml"
		if marker {
			r2.URL.Path = markPrefix + "/Custom.xaml"
		}
		r2.Header.Set("X-PCLCE", "1")
		s.proxy.ServeHTTP(w, r2)
		return
	}
	s.proxy.ServeHTTP(w, r)
}

func writeNoStore(w http.ResponseWriter, body string) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Cache-Control", noStore)
	io.WriteString(w, body)
}

// staticVersion 返回固定地址的版本号：取模板内容哈希，模板不变则版本号稳定，
// PCL 不会每次启动都判定“有新版本”而重新下载主页（旧版用 Custom.xaml 的 mtime）。
// 尾部的修订号让“只改了 AXAML 转换层、模板没动”的发布也能让 PCL 重新拉取主页。
// axamlRenderRevision 计入版本号：AXAML 转换逻辑改变（例如去掉 Styles）时
// 必须 bump，否则 PCL-CE 认为版本号没变，继续用缓存里的旧页面。
const axamlRenderRevision = "\n<!-- axaml-render-3 -->"

func (s *Server) staticVersion() string {
	sum := crc32.ChecksumIEEE([]byte(s.template + axamlRenderRevision))
	return strconv.FormatInt(int64(sum), 10)
}

// datedVersion 返回日期版版本号：同一天内固定，换天就变（旧版为 YYYYMMDD）。
func datedVersion(year, month, day string) string {
	y, _ := strconv.Atoi(year)
	m, _ := strconv.Atoi(month)
	d, _ := strconv.Atoi(day)
	return fmt.Sprintf("%04d%02d%02d", y, m, d)
}

func (s *Server) handleHome(w http.ResponseWriter, r *http.Request, marker, wantAXAML bool) {
	// 只给 PCL 启动器与真实浏览器：挡掉脚本/扫描器直接刷主页。
	if !isTrustedClient(r) {
		s.writeXAMLPage(w, r, s.buildFallbackXAML(
			s.i18n.T("fallback.denied_title"), s.i18n.T("fallback.bot_msg"), "", "", false), wantAXAML)
		return
	}
	ip := clientIP(r, s.cfg)
	origin := requestOrigin(r)
	body, err := s.buildHome(ip, origin, marker)
	if err != nil {
		logError("[Home] 渲染失败：%v", err)
		http.Error(w, "homepage render failed", http.StatusInternalServerError)
		return
	}
	// /Custom-CE.xaml 系列固定给 PCL-CE 的 Avalonia 渲染器：WPF 主页转 AXAML，
	// 转换失败时仍返回 WPF（PCL-CE 兼容渲染器能显示，只是事件不可用）。
	if wantAXAML {
		// CE 版顶部提示与 PCL2 版不同：先替换横条文案，再整体转 AXAML。
		body = strings.Replace(body, newSiteHint, ceSiteHint, 1)
		if ax := toAXAML(body); axamlOK(ax) {
			body = ax
			w.Header().Set("X-Axaml", "1")
		} else {
			logWarn("[Home] AXAML 转换失败，退回 WPF XAML")
		}
	}
	w.Header().Set("Content-Type", xamlCT)
	w.Header().Set("Cache-Control", noStore)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method == http.MethodHead {
		w.WriteHeader(http.StatusOK)
		return
	}
	io.WriteString(w, body)
}

func clientIP(r *http.Request, cfg *Config) string {
	if cfg.TrustProxyHeaders {
		if v := strings.TrimSpace(r.Header.Get("X-Real-IP")); v != "" {
			return v
		}
		if v := strings.TrimSpace(r.Header.Get("CF-Connecting-IP")); v != "" {
			return v
		}
		if v := strings.TrimSpace(r.Header.Get("X-Forwarded-For")); v != "" {
			if idx := strings.IndexByte(v, ','); idx >= 0 {
				v = v[:idx]
			}
			return strings.TrimSpace(v)
		}
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func requestOrigin(r *http.Request) string {
	proto := "http"
	if v := r.Header.Get("X-Forwarded-Proto"); v != "" {
		proto = v
	} else if r.TLS != nil {
		proto = "https"
	}
	return proto + "://" + r.Host
}

func newLegacyProxy(backend string) *httputil.ReverseProxy {
	target, _ := url.Parse("http://" + backend)
	p := httputil.NewSingleHostReverseProxy(target)
	orig := p.Director
	p.Director = func(req *http.Request) {
		host := req.Host
		ip := clientIP(req, &Config{TrustProxyHeaders: true})
		xf := req.Header.Get("X-Forwarded-For")
		orig(req)
		req.Host = host
		req.Header.Set("X-Real-IP", ip)
		if xf != "" {
			req.Header.Set("X-Forwarded-For", xf)
		} else {
			req.Header.Set("X-Forwarded-For", ip)
		}
		if req.Header.Get("X-Forwarded-Proto") == "" {
			req.Header.Set("X-Forwarded-Proto", "https")
		}
	}
	p.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		logWarn("[Proxy] 旧服务不可用：%v", err)
		http.Error(w, "legacy backend unavailable", http.StatusBadGateway)
	}
	return p
}

// modifyLegacyResponse 给旧版返回的页面注入新版标识：
// XAML 页面在内容区顶部插入黄色横条（AXAML 页面用 pclcontrols: 前缀），
// 设置页再切成双列；HTML 页面（/verify 等）插入固定横幅。
func (s *Server) modifyLegacyResponse(resp *http.Response) error {
	if resp.StatusCode != http.StatusOK || resp.Header.Get("Content-Encoding") != "" {
		return nil
	}
	if resp.Request == nil {
		return nil
	}
	p := resp.Request.URL.Path
	ct := resp.Header.Get("Content-Type")
	xamlPage := strings.Contains(ct, "xml")
	htmlPage := strings.Contains(ct, "text/html")
	if !xamlPage && !htmlPage {
		return nil
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return err
	}
	resp.Body.Close()
	if xamlPage {
		if p == "/settings_page.xaml" {
			body = injectTwoColumn(body)
		}
		isCECard := resp.Request != nil && resp.Request.Header.Get("X-PCLCE") == "1" &&
			(p == "/Custom.xaml" || p == "/mark/Custom.xaml")
		if isCECard {
			body = injectCESiteHint(body)
		} else {
			body = injectNewSiteHint(body)
		}
	} else {
		body = injectHTMLBanner(body)
	}
	resp.Body = io.NopCloser(bytes.NewReader(body))
	resp.ContentLength = int64(len(body))
	resp.Header.Set("Content-Length", strconv.Itoa(len(body)))
	return nil
}

// stackPanelInsertPos 返回 XAML 页面内容区（StackPanel）开标签后的偏移量。
// 旧版多数页面根元素是 <local:MyCard>（只允许一个子元素），此时取它内部的
// 第一个 StackPanel；找不到时返回 -1。
func stackPanelInsertPos(s string) int {
	pos := 0
	for pos < len(s) {
		for pos < len(s) && (s[pos] == ' ' || s[pos] == '\t' || s[pos] == '\r' || s[pos] == '\n') {
			pos++
		}
		if pos >= len(s) || s[pos] != '<' {
			return -1
		}
		if pos+1 < len(s) && (s[pos+1] == '?' || s[pos+1] == '!') {
			gt := strings.IndexByte(s[pos:], '>')
			if gt < 0 {
				return -1
			}
			pos += gt + 1
			continue
		}
		break
	}
	rootGT := strings.IndexByte(s[pos:], '>')
	if rootGT < 0 {
		return -1
	}
	rootGT += pos
	name := strings.TrimSpace(s[pos+1 : rootGT])
	if i := strings.IndexAny(name, " \t\r\n"); i >= 0 {
		name = name[:i]
	}
	if strings.EqualFold(name, "StackPanel") {
		return skipPropertyElements(s, rootGT+1)
	}
	sp := strings.Index(s[rootGT+1:], "<StackPanel")
	if sp < 0 {
		return -1
	}
	sp += rootGT + 1
	gt := strings.IndexByte(s[sp:], '>')
	if gt < 0 {
		return -1
	}
	return skipPropertyElements(s, sp+gt+1)
}

// skipPropertyElements 跳过 StackPanel 的属性元素（如 <StackPanel.Resources>…</StackPanel.Resources>），
// 返回第一个普通子元素之前的位置。PCL 的 XAML 解析器要求属性元素排在子元素之前，否则会报
// 「已对 StackPanel 设置 Children 属性」——所以横条/按钮不能插在属性元素前面。
func skipPropertyElements(s string, pos int) int {
	for {
		rest := s[pos:]
		trimmed := strings.TrimLeft(rest, " \t\r\n")
		pos += len(rest) - len(trimmed)
		if !strings.HasPrefix(trimmed, "<") {
			return pos
		}
		gt := strings.IndexByte(trimmed, '>')
		if gt < 0 {
			return pos
		}
		tag := trimmed[1:gt]
		if strings.HasPrefix(tag, "/") || strings.HasPrefix(tag, "!") || strings.HasPrefix(tag, "?") {
			return pos
		}
		name := strings.TrimSpace(tag)
		if i := strings.IndexAny(name, " \t\r\n/"); i >= 0 {
			name = name[:i]
		}
		if !strings.Contains(name, ".") {
			return pos
		}
		closeTag := "</" + name + ">"
		ci := strings.Index(s[pos:], closeTag)
		if ci < 0 {
			return pos
		}
		pos += ci + len(closeTag)
	}
}

// injectNewSiteHint 在 XAML 页面内容区顶部插入新版横条。
// 旧版已转成 AXAML 的页面（含 xmlns:pclcontrols）要用 pclcontrols: 前缀，
// 否则 local: 在 PCL-CE 的 Avalonia 解析器里是未声明前缀。
func injectNewSiteHint(body []byte) []byte {
	pos := stackPanelInsertPos(string(body))
	if pos < 0 {
		return body
	}
	hint := newSiteHint
	if strings.Contains(string(body), "xmlns:pclcontrols") {
		hint = newSiteHintAXAML
	}
	s := string(body)
	return []byte(s[:pos] + "\n    " + hint + s[pos:])
}

// injectCESiteHint 在 PCL-CE 版主页/验证卡顶部插入 CE 兼容提示。
func injectCESiteHint(body []byte) []byte {
	pos := stackPanelInsertPos(string(body))
	if pos < 0 {
		return body
	}
	hint := ceSiteHint
	if strings.Contains(string(body), "xmlns:pclcontrols") {
		hint = ceSiteHintAXAML
	}
	s := string(body)
	return []byte(s[:pos] + "\n    " + hint + s[pos:])
}

// injectTwoColumn 把个性设置页的各个分组改成两列并排（单列在宽屏上显得太空）。
// 只处理根 StackPanel 的直接子元素；末尾「保存」按钮组（HorizontalAlignment="Right"）保持整行。
func injectTwoColumn(body []byte) []byte {
	s := string(body)
	pos := stackPanelInsertPos(s)
	if pos < 0 {
		return body
	}
	children, end := splitChildren(s, pos)
	if len(children) < 4 || end <= pos {
		return body
	}
	keepLast := 0
	if strings.Contains(children[len(children)-1], `HorizontalAlignment="Right"`) {
		keepLast = 1
	}
	n := len(children) - keepLast
	if n < 4 {
		return body
	}
	var left, right strings.Builder
	for i := 0; i < n; i++ {
		if i%2 == 0 {
			left.WriteString(children[i])
		} else {
			right.WriteString(children[i])
		}
	}
	tail := ""
	if keepLast == 1 {
		tail = children[len(children)-1]
	}
	block := `<Grid Margin="0,0,0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>` +
		`<StackPanel Grid.Column="0" Margin="0,0,12,0">` + left.String() + `</StackPanel>` +
		`<StackPanel Grid.Column="1">` + right.String() + `</StackPanel></Grid>` + tail
	return []byte(s[:pos] + block + s[end:])
}

// splitChildren 从 pos 开始逐个切出直接子元素，返回子元素列表与根元素内容结束位置。
func splitChildren(s string, pos int) ([]string, int) {
	var children []string
	i := pos
	for i < len(s) {
		for i < len(s) && (s[i] == ' ' || s[i] == '\t' || s[i] == '\r' || s[i] == '\n') {
			i++
		}
		if i >= len(s) || strings.HasPrefix(s[i:], "</") {
			return children, i
		}
		if strings.HasPrefix(s[i:], "<!--") {
			if e := strings.Index(s[i:], "-->"); e >= 0 {
				i += e + 3
				continue
			}
			return children, i
		}
		if s[i] != '<' {
			i++
			continue
		}
		j := elementEnd(s, i)
		if j <= i {
			return children, i
		}
		children = append(children, s[i:j])
		i = j
	}
	return children, i
}

// elementEnd 返回从 start 开始的元素结束位置（含闭合标签），用标签栈处理嵌套与自闭合。
func elementEnd(s string, start int) int {
	depth := 0
	i := start
	for i < len(s) {
		if strings.HasPrefix(s[i:], "<!--") {
			if e := strings.Index(s[i:], "-->"); e >= 0 {
				i += e + 3
				continue
			}
			return len(s)
		}
		if s[i] != '<' {
			i++
			continue
		}
		gt := strings.IndexByte(s[i:], '>')
		if gt < 0 {
			return len(s)
		}
		gt += i
		tag := strings.TrimSpace(s[i+1 : gt])
		switch {
		case strings.HasPrefix(tag, "/"):
			depth--
			if depth == 0 {
				return gt + 1
			}
		case strings.HasPrefix(tag, "?"), strings.HasPrefix(tag, "!"):
			// 声明/注释，跳过
		case strings.HasSuffix(tag, "/"):
			// 自闭合
		default:
			depth++
		}
		i = gt + 1
	}
	return len(s)
}

// injectHTMLBanner 在旧版生成的 HTML 页面（如 /verify 访问验证页）</body> 前插入新版横幅。
// /admin 已由 nginx 的 sub_filter 注入，调用方会跳过。
func injectHTMLBanner(body []byte) []byte {
	s := string(body)
	i := strings.LastIndex(s, "</body>")
	if i < 0 {
		return body
	}
	return []byte(s[:i] + htmlBanner + s[i:])
}

// ---------- 小工具 ----------

func escapeAttr(v string) string {
	s := html.EscapeString(v)
	return strings.NewReplacer("{", "&#123;", "}", "&#125;").Replace(s)
}

func escapeURL(v string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;").Replace(v)
}

// ---------- 日志（新版日志带级别前缀，后台按级别着色） ----------

func logInfo(format string, args ...any)  { log.Printf("[INFO] "+format, args...) }
func logWarn(format string, args ...any)  { log.Printf("[WARN] "+format, args...) }
func logError(format string, args ...any) { log.Printf("[ERROR] "+format, args...) }

func main() {
	cfgPath := flag.String("config", "config.json", "配置文件路径")
	dataDir := flag.String("data", "var", "运行时数据目录")
	listen := flag.String("listen", "", "覆盖监听地址（默认取配置 host:port）")
	flag.Parse()

	cfg, err := loadConfig(*cfgPath)
	if err != nil {
		log.Fatalf("[Boot] %v", err)
	}
	if err := os.MkdirAll(*dataDir, 0o755); err != nil {
		logWarn("[Boot] 数据目录创建失败：%v", err)
	}
	if cfg.LogFile != "" {
		if err := os.MkdirAll(filepath.Dir(cfg.LogFile), 0o755); err == nil {
			if f, err := os.OpenFile(cfg.LogFile, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644); err == nil {
				log.SetOutput(io.MultiWriter(os.Stderr, f))
			}
		}
	}
	srv, err := newServer(cfg, *dataDir, *cfgPath)
	if err != nil {
		log.Fatalf("[Boot] 初始化失败：%v", err)
	}
	addr := net.JoinHostPort(cfg.Host, strconv.Itoa(cfg.Port))
	if *listen != "" {
		addr = *listen
	}
	httpSrv := &http.Server{
		Addr:              addr,
		Handler:           srv,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      120 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	go func() {
		logInfo("[Boot] NewEndPage 启动：http://%s  旧服务：%s", addr, cfg.LegacyBackend)
		if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[Boot] 监听失败：%v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	logInfo("[Boot] 收到退出信号，正在关闭……")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(shutdownCtx)
}
