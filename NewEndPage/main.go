package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
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
	PrefFile            string     `json:"pref_file"`
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
		PrefFile:            "pref.json",
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
	pref          *PrefStore
	started       time.Time
}

var datedRE = regexp.MustCompile(`^/(\d{4})/(\d{1,2})/(\d{1,2})$`)

const (
	markPrefix  = "/mark"
	xamlCT      = "application/xml; charset=utf-8"
	noStore     = "no-store, no-cache, must-revalidate, max-age=0, s-maxage=0"
	newSiteHint = `<local:MyHint Margin="0,0,0,12" Theme="Yellow" Text="您现在访问的是新版测试站点" />`
	// htmlBanner 用于旧版 HTML 页面（如 /verify 访问验证页），样式与 nginx sub_filter 的横幅一致。
	htmlBanner = `<div style="position:fixed;top:0;left:0;right:0;z-index:99999;background:#f9d423;color:#000;text-align:center;padding:6px 0;font:14px/1.4 sans-serif;">您现在访问的是新版测试站点</div>`
)

func newServer(cfg *Config, dataDir string) (*Server, error) {
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
	s.pref = newPrefStore(filepath.Join(dataDir, cfg.PrefFile))
	s.proxy = newLegacyProxy(cfg.LegacyBackend)
	s.proxy.ModifyResponse = s.modifyLegacyResponse
	return s, nil
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		s.proxy.ServeHTTP(w, r)
		return
	}
	p := r.URL.Path
	ip := clientIP(r, s.cfg)
	isNew := s.pref.get(ip)
	if isNew {
		w.Header().Set("X-New-EndPage", "1")
	} else {
		w.Header().Set("X-New-EndPage", "0")
	}
	switch {
	case p == "/_new":
		s.handlePref(w, r, true)
		return
	case p == "/_old":
		s.handlePref(w, r, false)
		return
	case p == "/" || p == "/Custom.xaml":
		s.routeHome(w, r, false, ip, isNew)
	case p == markPrefix || p == markPrefix+"/":
		s.routeHome(w, r, true, ip, isNew)
	case p == markPrefix+"/Custom.xaml":
		s.routeHome(w, r, true, ip, isNew)
	case p == "/Custom.xaml.version" || p == "/version":
		writeNoStore(w, strconv.FormatInt(time.Now().Unix(), 10))
	case p == markPrefix+"/Custom.xaml.version" || p == markPrefix+"/version":
		writeNoStore(w, strconv.FormatInt(time.Now().Unix(), 10))
	case p == "/healthz":
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		io.WriteString(w, `{"ok":true,"service":"newendpage","uptime":`+
			strconv.FormatInt(int64(time.Since(s.started).Seconds()), 10)+`}`)
	case datedRE.MatchString(p):
		if strings.HasSuffix(p, "/version") {
			writeNoStore(w, strconv.FormatInt(time.Now().Unix(), 10))
			return
		}
		s.routeHome(w, r, false, ip, isNew)
	default:
		s.proxy.ServeHTTP(w, r)
	}
}

// routeHome 处理主页类请求：主动选择了新版且已通过验证才用新版渲染，
// 否则交给旧版服务（未选择新版 = 旧版主页；已选择未验证 = 旧版验证卡）。
func (s *Server) routeHome(w http.ResponseWriter, r *http.Request, marker bool, ip string, isNew bool) {
	if isNew && s.verifyOK(ip) {
		s.handleHome(w, r, marker)
		return
	}
	s.proxy.ServeHTTP(w, r)
}

// handlePref 记录/撤销某个 IP 的新版偏好，并返回一个可关闭的提示页。
func (s *Server) handlePref(w http.ResponseWriter, r *http.Request, preferNew bool) {
	ip := clientIP(r, s.cfg)
	s.pref.set(ip, preferNew)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", noStore)
	title, msg := "已返回旧版主页", "之后访问主页将使用旧版页面，顶部不再显示新版测试站点横条。"
	if preferNew {
		title, msg = "已切换到新版测试站点", "之后访问主页将使用新版页面（顶部带黄色横条）；如尚未通过访问验证，会先显示验证页面。"
	}
	io.WriteString(w, "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>"+title+
		"</title></head><body style=\"font-family:sans-serif;background:#fdf6e3;color:#333;text-align:center;padding:80px 20px;\"><h2>"+title+
		"</h2><p>"+msg+"</p><p style=\"color:#888;\">本页面可以关闭，回到 PCL 刷新主页即可生效。</p></body></html>")
}

func writeNoStore(w http.ResponseWriter, body string) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Cache-Control", noStore)
	io.WriteString(w, body)
}

func (s *Server) handleHome(w http.ResponseWriter, r *http.Request, marker bool) {
	ip := clientIP(r, s.cfg)
	origin := requestOrigin(r)
	body, err := s.buildHome(ip, origin, marker)
	if err != nil {
		log.Printf("[Home] 渲染失败：%v", err)
		http.Error(w, "homepage render failed", http.StatusInternalServerError)
		return
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
		log.Printf("[Proxy] 旧服务不可用：%v", err)
		http.Error(w, "legacy backend unavailable", http.StatusBadGateway)
	}
	return p
}

// modifyLegacyResponse 只对「主动选择新版」的 IP 注入新版标识：
// XAML 页面在内容区顶部插入黄色横条，HTML 页面（/verify 等）插入固定横幅；
// 设置页无论哪个版本都插入切换按钮；/admin 由 nginx sub_filter 处理，跳过。
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
	isNew := s.pref.get(clientIP(resp.Request, s.cfg))
	if xamlPage && !isNew && p != "/settings_page.xaml" {
		return nil
	}
	if htmlPage && !isNew {
		return nil
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return err
	}
	resp.Body.Close()
	if xamlPage {
		if p == "/settings_page.xaml" {
			body = injectPrefSwitch(body, s.cfg.BaseURL, isNew)
		}
		if isNew {
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
		return rootGT + 1
	}
	sp := strings.Index(s[rootGT+1:], "<StackPanel")
	if sp < 0 {
		return -1
	}
	sp += rootGT + 1
	if gt := strings.IndexByte(s[sp:], '>'); gt >= 0 {
		return sp + gt + 1
	}
	return -1
}

// injectNewSiteHint 在 XAML 页面内容区顶部插入新版横条。
func injectNewSiteHint(body []byte) []byte {
	pos := stackPanelInsertPos(string(body))
	if pos < 0 {
		return body
	}
	s := string(body)
	return []byte(s[:pos] + "\n    " + newSiteHint + s[pos:])
}

// injectPrefSwitch 在个性设置页插入「切换到新版 / 返回旧版」按钮。
// 按钮走「打开网页」，由系统浏览器访问 /_new 或 /_old（与 PCL 同 IP，立即生效）。
// 不能用「打开帮助」：该事件要求 EventData 指向 .json 帮助文件，否则 PCL 报错。
func injectPrefSwitch(body []byte, baseURL string, preferNew bool) []byte {
	text := "切换到新版测试站点"
	target := baseURL + "/_new"
	if preferNew {
		text = "返回旧版主页"
		target = baseURL + "/_old"
	}
	btn := `<local:MyIconTextButton Height="36" Margin="0,0,0,12" HorizontalAlignment="Center" Text="` +
		escapeAttr(text) + `" ColorType="Highlight" LogoScale="0.75" Logo="M192 448 H640 V320 L896 512 L640 704 V576 H192 Z M832 576 H384 V704 L128 512 L384 320 V448 H832 Z" EventType="打开网页" EventData="` + escapeAttr(target) + `" />`
	pos := stackPanelInsertPos(string(body))
	if pos < 0 {
		return body
	}
	s := string(body)
	return []byte(s[:pos] + "\n    " + btn + s[pos:])
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
		log.Printf("[Boot] 数据目录创建失败：%v", err)
	}
	srv, err := newServer(cfg, *dataDir)
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
	}
	go func() {
		log.Printf("[Boot] NewEndPage 启动：http://%s  旧服务：%s", addr, cfg.LegacyBackend)
		if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[Boot] 监听失败：%v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	log.Printf("[Boot] 收到退出信号，正在关闭……")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(shutdownCtx)
}
