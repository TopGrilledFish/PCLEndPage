package main

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"crypto/subtle"
	"encoding/base32"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ============ 后台 ============

type Admin struct {
	cfg     *Config
	cfgPath string
	srv     *Server
	started time.Time

	mu         sync.Mutex
	sessions   map[string]time.Time
	pending    map[string]time.Time
	setupSecre map[string]string
}

func newAdmin(cfg *Config, cfgPath string, srv *Server) *Admin {
	return &Admin{
		cfg:        cfg,
		cfgPath:    cfgPath,
		srv:        srv,
		started:    time.Now(),
		sessions:   map[string]time.Time{},
		pending:    map[string]time.Time{},
		setupSecre: map[string]string{},
	}
}

func (a *Admin) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	p := r.URL.Path
	switch {
	case p == "/admin" && r.Method == http.MethodGet:
		if a.sessionOK(r) {
			a.renderHome(w)
		} else {
			a.renderLogin(w, "")
		}
	case p == "/admin/login" && r.Method == http.MethodPost:
		a.handleLogin(w, r)
	case p == "/admin/logout":
		a.handleLogout(w, r)
	case p == "/admin/setup" && r.Method == http.MethodGet:
		a.renderSetup(w, r)
	case p == "/admin/setup" && r.Method == http.MethodPost:
		a.handleSetupSubmit(w, r)
	case p == "/admin/2fa" && r.Method == http.MethodGet:
		a.render2FA(w, r)
	case p == "/admin/2fa" && r.Method == http.MethodPost:
		a.handle2FASubmit(w, r)
	case strings.HasPrefix(p, "/admin/api/"):
		a.serveAPI(w, r)
	default:
		http.NotFound(w, r)
	}
}

// ============ 会话 ============

const (
	adminSessionCookie = "admin_session"
	adminPendingCookie = "admin_pending"
)

func (a *Admin) sessionTTL() time.Duration {
	h := a.cfg.AdminSessionHours
	if h <= 0 {
		h = 8
	}
	return time.Duration(h) * time.Hour
}

func (a *Admin) sessionOK(r *http.Request) bool {
	c, err := r.Cookie(adminSessionCookie)
	if err != nil {
		return false
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	exp, ok := a.sessions[c.Value]
	if !ok {
		return false
	}
	if time.Now().After(exp) {
		delete(a.sessions, c.Value)
		return false
	}
	return true
}

func (a *Admin) pendingToken(r *http.Request) string {
	c, err := r.Cookie(adminPendingCookie)
	if err != nil {
		return ""
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	exp, ok := a.pending[c.Value]
	if !ok || time.Now().After(exp) {
		delete(a.pending, c.Value)
		return ""
	}
	return c.Value
}

func (a *Admin) setCookie(w http.ResponseWriter, name, value string, maxAge int) {
	http.SetCookie(w, &http.Cookie{
		Name:     name,
		Value:    value,
		Path:     "/admin",
		MaxAge:   maxAge,
		HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
	})
}

func (a *Admin) newSession() string {
	tok := randomToken()
	a.mu.Lock()
	defer a.mu.Unlock()
	a.sessions[tok] = time.Now().Add(a.sessionTTL())
	return tok
}

func (a *Admin) handleLogout(w http.ResponseWriter, r *http.Request) {
	if c, err := r.Cookie(adminSessionCookie); err == nil {
		a.mu.Lock()
		delete(a.sessions, c.Value)
		a.mu.Unlock()
	}
	a.setCookie(w, adminSessionCookie, "", -1)
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (a *Admin) handleLogin(w http.ResponseWriter, r *http.Request) {
	token := ""
	if err := r.ParseForm(); err == nil {
		token = r.FormValue("token")
	}
	if a.cfg.AdminToken == "" || subtle.ConstantTimeCompare([]byte(token), []byte(a.cfg.AdminToken)) != 1 {
		time.Sleep(300 * time.Millisecond)
		a.renderLogin(w, "令牌错误")
		return
	}
	pending := randomToken()
	a.mu.Lock()
	a.pending[pending] = time.Now().Add(10 * time.Minute)
	a.mu.Unlock()
	a.setCookie(w, adminPendingCookie, pending, 600)
	if a.cfg.AdminTOTPSecret == "" {
		http.Redirect(w, r, "/admin/setup", http.StatusSeeOther)
		return
	}
	http.Redirect(w, r, "/admin/2fa", http.StatusSeeOther)
}

func (a *Admin) renderSetup(w http.ResponseWriter, r *http.Request) {
	pend := a.pendingToken(r)
	if pend == "" {
		http.Redirect(w, r, "/admin", http.StatusSeeOther)
		return
	}
	a.mu.Lock()
	secret := a.setupSecre[pend]
	if secret == "" {
		secret = newTOTPSecret()
		a.setupSecre[pend] = secret
	}
	a.mu.Unlock()
	uri := "otpauth://totp/PCLEndPage?secret=" + secret + "&issuer=PCLEndPage"
	body := strings.NewReplacer("{{SECRET}}", secret, "{{URI}}", uri).Replace(adminSetupPage)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	io.WriteString(w, body)
}

func (a *Admin) handleSetupSubmit(w http.ResponseWriter, r *http.Request) {
	pend := a.pendingToken(r)
	if pend == "" {
		http.Redirect(w, r, "/admin", http.StatusSeeOther)
		return
	}
	code := ""
	if err := r.ParseForm(); err == nil {
		code = strings.TrimSpace(r.FormValue("code"))
	}
	a.mu.Lock()
	secret := a.setupSecre[pend]
	a.mu.Unlock()
	if secret == "" || !verifyTOTP(secret, code, time.Now()) {
		time.Sleep(300 * time.Millisecond)
		a.renderLogin(w, "验证码不正确，请重试")
		return
	}
	if err := a.saveTOTPSecret(secret); err != nil {
		a.renderLogin(w, "保存 2FA 密钥失败："+err.Error())
		return
	}
	a.cfg.AdminTOTPSecret = secret
	a.mu.Lock()
	delete(a.setupSecre, pend)
	delete(a.pending, pend)
	a.mu.Unlock()
	a.setCookie(w, adminPendingCookie, "", -1)
	a.setCookie(w, adminSessionCookie, a.newSession(), int(a.sessionTTL().Seconds()))
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (a *Admin) render2FA(w http.ResponseWriter, r *http.Request) {
	pend := a.pendingToken(r)
	if pend == "" {
		http.Redirect(w, r, "/admin", http.StatusSeeOther)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	io.WriteString(w, admin2FAPage)
}

func (a *Admin) handle2FASubmit(w http.ResponseWriter, r *http.Request) {
	pend := a.pendingToken(r)
	if pend == "" {
		http.Redirect(w, r, "/admin", http.StatusSeeOther)
		return
	}
	code := ""
	if err := r.ParseForm(); err == nil {
		code = strings.TrimSpace(r.FormValue("code"))
	}
	if !verifyTOTP(a.cfg.AdminTOTPSecret, code, time.Now()) {
		time.Sleep(300 * time.Millisecond)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		io.WriteString(w, strings.Replace(admin2FAPage, `<!--ERR-->`, `<p class="err">动态口令不正确</p>`, 1))
		return
	}
	a.mu.Lock()
	delete(a.pending, pend)
	a.mu.Unlock()
	a.setCookie(w, adminPendingCookie, "", -1)
	a.setCookie(w, adminSessionCookie, a.newSession(), int(a.sessionTTL().Seconds()))
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

// ============ API ============

func (a *Admin) serveAPI(w http.ResponseWriter, r *http.Request) {
	if !a.sessionOK(r) {
		writeJSON(w, 401, map[string]any{"ok": false, "error": "unauthorized"})
		return
	}
	action := strings.TrimPrefix(r.URL.Path, "/admin/api/")
	var raw []byte
	if r.Method == http.MethodPost {
		raw, _ = io.ReadAll(io.LimitReader(r.Body, 1<<20))
	}
	switch action {
	case "state":
		a.apiState(w, r)
	case "stats":
		a.apiStats(w)
	case "info":
		a.apiInfo(w)
	case "set", "set_batch", "delete":
		a.apiStoreSet(w, action, raw)
	case "block", "block_batch":
		a.apiBlock(w, action, raw)
	case "whitelist":
		a.apiWhitelist(w, raw)
	case "maintenance":
		a.apiMaintenance(w, raw)
	case "ai_reset":
		a.apiAIReset(w, raw)
	case "ai_log":
		a.apiAILog(w, r)
	case "ai_log_clear":
		a.apiAILogClear(w)
	case "regenerate":
		writeJSON(w, 200, map[string]any{"ok": true})
	case "doctor":
		a.apiDoctor(w)
	case "preview":
		a.apiPreview(w)
	case "log":
		a.apiLog(w, r)
	case "cache":
		a.apiCache(w, raw)
	case "sessions":
		a.apiSessions(w, r, raw)
	case "store_get":
		a.apiStoreGet(w)
	case "store_set":
		a.apiStoreSetAll(w, raw)
	case "export":
		a.apiExport(w)
	case "config_set":
		a.apiConfigSet(w, raw)
	default:
		writeJSON(w, 404, map[string]any{"ok": false, "error": "not_found"})
	}
}

func (a *Admin) apiState(w http.ResponseWriter, r *http.Request) {
	store := a.loadStore()
	state := map[string]any{
		"ok":     true,
		"config": a.publicConfig(),
		"store":  store,
		"info":   a.info(),
	}
	if a.srv != nil && a.srv.newstats != nil {
		hits, uniques := a.srv.newstats.snapshot()
		state["new_site"] = map[string]any{"today_hits": hits, "unique_ips": uniques}
	}
	// stats 与 ai 状态现在由新版直接提供（旧版下线后不再可用）。
	state["stats"] = a.statsMap()
	state["ai"] = a.aiState()
	writeJSON(w, 200, state)
}

func (a *Admin) publicConfig() map[string]any {
	return map[string]any{
		"site_name":        a.cfg.SiteName,
		"base_url":         a.cfg.BaseURL,
		"guard_clients":    a.cfg.GuardClients,
		"enable_weather":   a.cfg.EnableWeather,
		"enable_wallpaper": a.cfg.EnableWallpaper,
		"enable_saying":    a.cfg.EnableSaying,
		"enable_lunar":     a.cfg.EnableLunar,
		"enable_geo":       a.cfg.EnableGeo,
		"enable_ai":        a.cfg.EnableAI,
		"weather_city":     a.cfg.WeatherCity,
	}
}

func (a *Admin) info() map[string]any {
	return map[string]any{
		"version":         a.cfg.Version,
		"started_at":      a.started.Format("2006-01-02 15:04:05"),
		"uptime":          time.Since(a.started).Truncate(time.Second).String(),
		"listen":          fmt.Sprintf("%s:%d", a.cfg.Host, a.cfg.Port),
		"legacy":          a.cfg.LegacyBackend,
		"go_version":      runtimeVersion(),
		"log_file":        a.cfg.LogFile,
		"legacy_log_file": a.cfg.LegacyLogFile,
	}
}

func (a *Admin) apiInfo(w http.ResponseWriter) {
	writeJSON(w, 200, map[string]any{"ok": true, "info": a.info()})
}

// ---- 统计 / AI / 体检（新版自营，不再依赖旧版 API）----

func (a *Admin) statsMap() map[string]any {
	if a.srv == nil || a.srv.stats == nil {
		return map[string]any{}
	}
	sum := a.srv.stats.summary(7)
	return map[string]any{
		"today":          sum.Today,
		"unique":         sum.Unique,
		"week":           sum.Total,
		"foreign":        sum.Foreign,
		"domestic":       sum.Domestic,
		"total":          sum.Total,
		"countries":      sum.Countries,
		"regions":        sum.Regions,
		"regions_cn":     sum.RegionsCN,
		"regions_abroad": sum.RegionsAbroad,
		"provinces":      sum.Provinces,
		"hosts":          sum.Hosts,
		"recent":         sum.Recent,
		"trend":          sum.Trend,
	}
}

func (a *Admin) apiStats(w http.ResponseWriter) {
	writeJSON(w, 200, map[string]any{"ok": true, "stats": a.statsMap()})
}

func (a *Admin) aiState() map[string]any {
	if a.srv == nil || a.srv.ai == nil {
		return map[string]any{}
	}
	state := a.srv.ai.quotaState("127.0.0.1", "zh-hans")
	state["own_key"] = "未配置"
	if len(a.srv.ai.ownKey("127.0.0.1")) > 0 {
		state["own_key"] = "已配置"
	}
	return state
}

func (a *Admin) apiAIReset(w http.ResponseWriter, raw []byte) {
	if a.srv == nil || a.srv.ai == nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "ai_disabled"})
		return
	}
	var req struct {
		Scope string `json:"scope"`
		IP    string `json:"ip"`
	}
	json.Unmarshal(raw, &req)
	if req.Scope == "" {
		req.Scope = "all"
	}
	if req.Scope != "quota" && req.Scope != "jobs" && req.Scope != "keys" && req.Scope != "all" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "bad_scope"})
		return
	}
	result := a.srv.ai.resetAI(req.Scope, req.IP)
	writeJSON(w, 200, map[string]any{"ok": true, "scope": req.Scope, "result": result})
}

func (a *Admin) apiAILog(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			limit = n
		}
	}
	entries := a.srv.ai.recentAnalyses(limit)
	writeJSON(w, 200, map[string]any{
		"ok": true, "entries": entries, "count": len(entries),
		"file": filepath.Join(a.srv.dataDir, "ai_log.json"), "max": aiLogMax,
	})
}

func (a *Admin) apiAILogClear(w http.ResponseWriter) {
	removed := len(a.srv.ai.recentAnalyses(aiLogMax))
	a.srv.ai.clearAnalyses()
	writeJSON(w, 200, map[string]any{"ok": true, "removed": removed})
}

func (a *Admin) apiDoctor(w http.ResponseWriter) {
	type check struct {
		Name   string `json:"name"`
		Status string `json:"status"`
		Detail string `json:"detail"`
	}
	checks := []check{}
	okCount, warnCount, failCount := 0, 0, 0
	add := func(name, status, detail string) {
		checks = append(checks, check{Name: name, Status: status, Detail: detail})
		switch status {
		case "ok":
			okCount++
		case "warn":
			warnCount++
		default:
			failCount++
		}
	}
	if raw, err := os.ReadFile(a.cfgPath); err == nil && len(raw) > 0 {
		add("配置文件", "ok", a.cfgPath)
	} else {
		add("配置文件", "fail", "读取失败")
	}
	if a.cfg.AdminToken == "" || a.cfg.AdminToken == "admin" {
		add("后台口令", "warn", "未设置或仍为默认值")
	} else {
		add("后台口令", "ok", "已设置")
	}
	for _, item := range []struct{ name, path string }{
		{"验证数据", a.cfg.VerifyFile},
		{"访问统计", filepath.Join(a.srv.dataDir, "visits.json")},
		{"store 数据", a.cfg.StoreFile},
	} {
		if _, err := os.Stat(item.path); err == nil {
			add(item.name, "ok", item.path)
		} else {
			add(item.name, "warn", "未创建："+item.path)
		}
	}
	if st, err := os.Stat(a.cfg.StaticDir); err == nil && st.IsDir() {
		add("静态资源", "ok", a.cfg.StaticDir)
	} else {
		add("静态资源", "warn", "目录不存在："+a.cfg.StaticDir)
	}
	if err := os.MkdirAll(filepath.Dir(a.cfg.LogFile), 0o755); err == nil {
		add("日志目录", "ok", filepath.Dir(a.cfg.LogFile))
	} else {
		add("日志目录", "fail", err.Error())
	}
	writeJSON(w, 200, map[string]any{"ok": true, "report": map[string]any{
		"ok": okCount, "warn": warnCount, "fail": failCount, "took_ms": 0, "checks": checks,
	}, "cached": false, "at": time.Now().Unix()})
}

// ---- store.json ----

func (a *Admin) storePath() string {
	if a.cfg.StoreFile != "" {
		return a.cfg.StoreFile
	}
	return filepath.Join(filepath.Dir(a.cfgPath), "store.json")
}

func (a *Admin) loadStore() map[string]any {
	raw, err := os.ReadFile(a.storePath())
	if err != nil {
		return map[string]any{}
	}
	var m map[string]any
	if json.Unmarshal(raw, &m) != nil || m == nil {
		return map[string]any{}
	}
	return m
}

func (a *Admin) saveStore(m map[string]any) error {
	path := a.storePath()
	raw, _ := os.ReadFile(path)
	if len(raw) > 0 {
		_ = os.WriteFile(path+".backup.json", raw, 0o600)
	}
	data, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

var storeWritable = map[string]bool{
	"maint_mode_live": true, "maint_eta": true, "maint_reason": true, "maint_whitelist": true,
	"block:list": true, "banners": true, "homepage_banner": true, "quote_custom": true,
	"custom_festivals": true, "custom_countdown": true,
}

func (a *Admin) apiStoreSet(w http.ResponseWriter, action string, raw []byte) {
	var req struct {
		Key   string         `json:"key"`
		Value any            `json:"value"`
		Items map[string]any `json:"items"`
	}
	if err := json.Unmarshal(raw, &req); err != nil {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "bad_json"})
		return
	}
	store := a.loadStore()
	set := func(key string, val any) bool {
		if !storeWritable[key] {
			writeJSON(w, 400, map[string]any{"ok": false, "error": "key_not_writable", "key": key})
			return false
		}
		store[key] = val
		return true
	}
	switch action {
	case "set":
		if !set(req.Key, req.Value) {
			return
		}
	case "set_batch":
		if len(req.Items) == 0 {
			writeJSON(w, 400, map[string]any{"ok": false, "error": "missing_items"})
			return
		}
		for k, v := range req.Items {
			if !set(k, v) {
				return
			}
		}
	case "delete":
		if !storeWritable[req.Key] {
			writeJSON(w, 400, map[string]any{"ok": false, "error": "key_not_writable"})
			return
		}
		delete(store, req.Key)
	}
	if err := a.saveStore(store); err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (a *Admin) apiBlock(w http.ResponseWriter, action string, raw []byte) {
	var req struct {
		IP     string   `json:"ip"`
		IPs    []string `json:"ips"`
		Reason string   `json:"reason"`
		Remove bool     `json:"remove"`
	}
	json.Unmarshal(raw, &req)
	store := a.loadStore()
	block, _ := store["block:list"].(map[string]any)
	if block == nil {
		block = map[string]any{}
	}
	add := func(ip string) bool {
		ip = strings.TrimSpace(ip)
		if ip == "" || len(ip) > 64 {
			return false
		}
		if req.Remove {
			delete(block, ip)
		} else {
			reason := req.Reason
			if reason == "" {
				reason = "手动封禁"
			}
			block[ip] = reason
		}
		return true
	}
	if action == "block" {
		add(req.IP)
	} else {
		for _, ip := range req.IPs {
			for _, one := range strings.FieldsFunc(ip, func(r rune) bool { return r == ',' || r == '\n' || r == ' ' }) {
				add(one)
			}
		}
	}
	store["block:list"] = block
	if err := a.saveStore(store); err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "block_list": block})
}

func (a *Admin) apiWhitelist(w http.ResponseWriter, raw []byte) {
	var req struct {
		IPs    []string `json:"ips"`
		IP     string   `json:"ip"`
		Remove bool     `json:"remove"`
	}
	json.Unmarshal(raw, &req)
	store := a.loadStore()
	wl, _ := store["maint_whitelist"].(map[string]any)
	if wl == nil {
		wl = map[string]any{}
	}
	ips := req.IPs
	if len(ips) == 0 && req.IP != "" {
		ips = []string{req.IP}
	}
	for _, rawIP := range ips {
		for _, one := range strings.FieldsFunc(rawIP, func(r rune) bool { return r == ',' || r == '\n' || r == ' ' }) {
			one = strings.TrimSpace(one)
			if one == "" {
				continue
			}
			if req.Remove {
				delete(wl, one)
			} else {
				wl[one] = 1
			}
		}
	}
	store["maint_whitelist"] = wl
	if err := a.saveStore(store); err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "whitelist": wl})
}

func (a *Admin) apiMaintenance(w http.ResponseWriter, raw []byte) {
	var req struct {
		On     bool   `json:"on"`
		Eta    string `json:"eta"`
		Reason string `json:"reason"`
	}
	json.Unmarshal(raw, &req)
	store := a.loadStore()
	if req.On {
		store["maint_mode_live"] = "1"
	} else {
		store["maint_mode_live"] = "0"
	}
	store["maint_eta"] = req.Eta
	store["maint_reason"] = req.Reason
	if err := a.saveStore(store); err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (a *Admin) apiStoreGet(w http.ResponseWriter) {
	store := a.loadStore()
	text, _ := json.MarshalIndent(store, "", "  ")
	writeJSON(w, 200, map[string]any{"ok": true, "store": store, "text": string(text)})
}

func (a *Admin) apiStoreSetAll(w http.ResponseWriter, raw []byte) {
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil || len(m) == 0 {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "bad_json"})
		return
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if err := a.saveStore(m); err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "keys": keys})
}

func (a *Admin) apiExport(w http.ResponseWriter) {
	store := a.loadStore()
	data, _ := json.MarshalIndent(store, "", "  ")
	name := "pclhome-store-" + time.Now().Format("20060102-150405") + ".json"
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="`+name+`"`)
	w.Write(data)
}

// ---- config.json ----

var configFields = map[string]bool{
	"site_name": true, "enable_weather": true, "enable_wallpaper": true, "enable_saying": true,
	"enable_lunar": true, "enable_geo": true, "guard_clients": true, "weather_city": true,
}

func (a *Admin) apiConfigSet(w http.ResponseWriter, raw []byte) {
	var req struct {
		Updates map[string]any `json:"updates"`
	}
	if err := json.Unmarshal(raw, &req); err != nil || len(req.Updates) == 0 {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "missing_updates"})
		return
	}
	rawFile, _ := os.ReadFile(a.cfgPath)
	var m map[string]any
	if json.Unmarshal(rawFile, &m) != nil {
		m = map[string]any{}
	}
	for key, val := range req.Updates {
		if !configFields[key] {
			writeJSON(w, 400, map[string]any{"ok": false, "error": "key_not_writable", "key": key})
			return
		}
		m[key] = val
	}
	data, _ := json.MarshalIndent(m, "", "  ")
	tmp := a.cfgPath + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o600); err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if err := os.Rename(tmp, a.cfgPath); err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	// 同步内存配置
	for key, val := range req.Updates {
		switch key {
		case "site_name":
			a.cfg.SiteName, _ = val.(string)
		case "enable_weather":
			a.cfg.EnableWeather, _ = val.(bool)
		case "enable_wallpaper":
			a.cfg.EnableWallpaper, _ = val.(bool)
		case "enable_saying":
			a.cfg.EnableSaying, _ = val.(bool)
		case "enable_lunar":
			a.cfg.EnableLunar, _ = val.(bool)
		case "enable_geo":
			a.cfg.EnableGeo, _ = val.(bool)
		case "guard_clients":
			a.cfg.GuardClients, _ = val.(bool)
		case "weather_city":
			a.cfg.WeatherCity, _ = val.(string)
		}
	}
	writeJSON(w, 200, map[string]any{"ok": true})
}

// ---- 日志 ----

func (a *Admin) apiLog(w http.ResponseWriter, r *http.Request) {
	source := r.URL.Query().Get("source")
	lines := 200
	fmt.Sscanf(r.URL.Query().Get("lines"), "%d", &lines)
	if lines < 20 {
		lines = 20
	}
	if lines > 1000 {
		lines = 1000
	}
	path := a.cfg.LogFile
	if source == "old" {
		path = a.cfg.LegacyLogFile
	}
	text, ok, size := readLogTail(path, lines)
	writeJSON(w, 200, map[string]any{
		"ok": true, "available": ok, "file": path, "size": size,
		"lines": strings.Split(text, "\n"), "source": source,
	})
}

func readLogTail(path string, maxLines int) (string, bool, int64) {
	f, err := os.Open(path)
	if err != nil {
		return "", false, 0
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return "", false, 0
	}
	start := int64(0)
	if st.Size() > 256*1024 {
		start = st.Size() - 256*1024
	}
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return "", false, 0
	}
	raw, err := io.ReadAll(f)
	if err != nil {
		return "", false, 0
	}
	text := string(raw)
	if start > 0 {
		if i := strings.Index(text, "\n"); i >= 0 {
			text = text[i+1:]
		}
	}
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	if len(lines) > maxLines {
		lines = lines[len(lines)-maxLines:]
	}
	return strings.Join(lines, "\n"), true, st.Size()
}

// ---- 工具 ----

func (a *Admin) previewHomepage() (string, error) {
	ip := "127.0.0.1"
	origin := a.cfg.BaseURL
	if origin == "" {
		origin = fmt.Sprintf("http://%s:%d", a.cfg.Host, a.cfg.Port)
	}
	return a.srv.buildHome(ip, origin, false)
}

func (a *Admin) apiPreview(w http.ResponseWriter) {
	body, err := a.previewHomepage()
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "xaml": body})
}

func (a *Admin) apiCache(w http.ResponseWriter, raw []byte) {
	var req struct {
		Target string `json:"target"`
	}
	json.Unmarshal(raw, &req)
	cleared := []string{}
	dir := filepath.Dir(a.cfg.LogFile)
	cands := map[string][]string{
		"saying":    {filepath.Join(dir, "saying.json")},
		"lunar":     {filepath.Join(dir, "lunar.json")},
		"wallpaper": {filepath.Join(dir, "cache")},
	}
	if req.Target == "all" {
		for _, list := range cands {
			cleared = append(cleared, list...)
		}
	} else if list, ok := cands[req.Target]; ok {
		cleared = list
	} else {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "bad_target"})
		return
	}
	out := []string{}
	for _, p := range cleared {
		if err := os.RemoveAll(p); err == nil {
			out = append(out, p)
		}
	}
	writeJSON(w, 200, map[string]any{"ok": true, "cleared": out})
}

func (a *Admin) apiSessions(w http.ResponseWriter, r *http.Request, raw []byte) {
	var req struct {
		Action string `json:"action"`
	}
	json.Unmarshal(raw, &req)
	if req.Action != "revoke_others" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "bad_action"})
		return
	}
	keep := ""
	if c, err := r.Cookie(adminSessionCookie); err == nil {
		keep = c.Value
	}
	a.mu.Lock()
	removed := len(a.sessions)
	next := map[string]time.Time{}
	if keep != "" {
		if exp, ok := a.sessions[keep]; ok {
			next[keep] = exp
			removed--
		}
	}
	a.sessions = next
	a.mu.Unlock()
	writeJSON(w, 200, map[string]any{"ok": true, "removed": removed})
}

// ============ TOTP ============

func verifyTOTP(secretB32, code string, now time.Time) bool {
	code = strings.TrimSpace(code)
	if len(code) != 6 {
		return false
	}
	sec := now.Unix() / 30
	for _, delta := range []int64{-1, 0, 1} {
		if subtle.ConstantTimeCompare([]byte(totpCode(secretB32, sec+delta)), []byte(code)) == 1 {
			return true
		}
	}
	return false
}

func totpCode(secretB32 string, counter int64) string {
	key, err := base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(strings.ToUpper(secretB32))
	if err != nil || len(key) == 0 {
		return "000000"
	}
	var buf [8]byte
	binary.BigEndian.PutUint64(buf[:], uint64(counter))
	mac := hmac.New(sha1.New, key)
	mac.Write(buf[:])
	sum := mac.Sum(nil)
	off := sum[len(sum)-1] & 0x0F
	v := (uint32(sum[off])&0x7F)<<24 | uint32(sum[off+1])<<16 | uint32(sum[off+2])<<8 | uint32(sum[off+3])
	return fmt.Sprintf("%06d", v%1000000)
}

func newTOTPSecret() string {
	buf := make([]byte, 20)
	if _, err := rand.Read(buf); err != nil {
		copy(buf, fmt.Sprint(time.Now().UnixNano()))
	}
	return base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(buf)
}

func randomToken() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		copy(buf, fmt.Sprint(time.Now().UnixNano()))
	}
	return fmt.Sprintf("%x", buf)
}

func (a *Admin) saveTOTPSecret(secret string) error {
	raw, err := os.ReadFile(a.cfgPath)
	if err != nil {
		return err
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		return err
	}
	m["admin_totp_secret"], _ = json.Marshal(secret)
	data, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	tmp := a.cfgPath + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, a.cfgPath)
}

// ============ 页面 ============

const adminStyle = `<style>
*{box-sizing:border-box}body{margin:0;font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#14181f;color:#dfe6f3}
.wrap{max-width:1000px;margin:0 auto;padding:24px 18px 60px}
h1{font-size:20px;margin:0 0 4px}
.hint{color:#8892a6;font-size:13px;margin:0 0 18px}
.card{background:#1c222c;border:1px solid #2a3342;border-radius:10px;padding:16px;margin:0 0 14px}
.card h2{font-size:15px;margin:0 0 10px;color:#9fb0c9}
input,textarea,select{background:#0f131a;border:1px solid #334055;color:#dfe6f3;border-radius:8px;padding:9px 11px;font:13px/1.5 inherit;width:100%}
textarea{min-height:80px;font-family:ui-monospace,Consolas,monospace}
button{background:#2f6fed;border:0;color:#fff;border-radius:8px;padding:9px 16px;font:13px inherit;cursor:pointer}
button.ghost{background:#2a3342}button.danger{background:#c0392b}button:active{opacity:.8}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
.kv{background:#0f131a;border-radius:8px;padding:10px 12px}.kv b{display:block;color:#8fa3bf;font-size:12px;font-weight:400}.kv span{font-size:20px;font-weight:600}
nav{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px}
nav a{color:#9fb0c9;text-decoration:none;padding:7px 14px;border-radius:8px;background:#1c222c;border:1px solid #2a3342}
nav a.on{background:#2f6fed;color:#fff;border-color:#2f6fed}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #26303f}th{color:#8fa3bf;font-weight:500}
pre.log{background:#0b0f14;border:1px solid #26303f;border-radius:8px;padding:12px;font:12px/1.55 ui-monospace,Consolas,monospace;max-height:520px;overflow:auto;white-space:pre-wrap;word-break:break-all;margin:0}
.log .warn{color:#ff6b6b}.log .err{color:#ff3b30;font-weight:600}.log .info{color:#66b3ff}.log .t{color:#6b7a90}
.err{color:#ff6b6b}.ok{color:#3fd68a}
label.chk{display:flex;gap:8px;align-items:center;color:#c8d3e3;font-size:13px;margin:6px 0}
label.chk input{width:auto}
.login{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.login form{width:340px}.login h1{font-size:22px}
.secret{font-family:ui-monospace,Consolas,monospace;font-size:15px;color:#f9d423;word-break:break-all;background:#0f131a;padding:10px;border-radius:8px}
</style>`

const adminLoginPage = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>新版后台 · 登录</title>` + adminStyle + `</head><body class="login"><form class="card" method="post" action="/admin/login"><h1>PCLEndPage 新版后台</h1><p class="hint">输入后台令牌（admin_token）继续</p>{{ERR}}<div class="row"><input type="password" name="token" placeholder="后台令牌" autofocus required></div><button>下一步</button></form></body></html>`

const adminSetupPage = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>新版后台 · 绑定 2FA</title>` + adminStyle + `</head><body class="login"><form class="card" method="post" action="/admin/setup"><h1>绑定身份验证器</h1><p class="hint">用 Google Authenticator / 微软 Authenticator 等扫描或手动添加下面的密钥：</p><p class="secret">{{SECRET}}</p><p class="hint">或添加 URI：<br>{{URI}}</p><div class="row"><input name="code" inputmode="numeric" maxlength="6" placeholder="输入一次 6 位验证码以确认" required autofocus></div><button>绑定并登录</button></form></body></html>`

const admin2FAPage = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>新版后台 · 动态口令</title>` + adminStyle + `</head><body class="login"><form class="card" method="post" action="/admin/2fa"><h1>动态口令</h1><p class="hint">打开身份验证器，输入当前的 6 位口令</p><!--ERR--><div class="row"><input name="code" inputmode="numeric" maxlength="6" placeholder="000000" required autofocus></div><button>登录</button></form></body></html>`

const adminHomePage = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>新版后台</title>` + adminStyle + `</head><body><div class="wrap">
<div class="row" style="justify-content:space-between"><h1>PCLEndPage 新版后台</h1><a href="/admin/logout" style="color:#9fb0c9">退出登录</a></div>
<div class="hint">{{INFO}}</div>
<nav>
 <a href="#" data-tab="overview" class="on">概览</a>
 <a href="#" data-tab="config">配置</a>
 <a href="#" data-tab="block">封禁</a>
 <a href="#" data-tab="maint">维护</a>
 <a href="#" data-tab="ai">AI</a>
 <a href="#" data-tab="log">日志</a>
 <a href="#" data-tab="tools">工具</a>
</nav>
<div id="tab-overview">
 <div class="card"><h2>访问统计</h2><div id="stats" class="grid"></div></div>
 <div class="card"><h2>服务信息</h2><div id="info" class="grid"></div></div>
 <div class="card"><h2>旧版日志（最近 100 行）</h2><pre id="oldlog" class="log">加载中…</pre><div class="row"><button class="ghost" onclick="loadLog('old')">刷新旧版日志</button></div></div>
</div>
<div id="tab-config" style="display:none"><div class="card"><h2>站点配置</h2><div id="cfgform"></div><div class="row"><button onclick="saveConfig()">保存配置</button><span id="cfgmsg"></span></div></div>
<div class="card"><h2>自定义内容（store.json）</h2><div class="row"><textarea id="quote_custom" placeholder="每日一言，每行一条"></textarea></div><div class="row"><button onclick="saveQuote()">保存每日一言</button><span id="quotemsg"></span></div>
<div class="row"><h2 style="margin:18px 0 6px">多公告轮播</h2><textarea id="banners" style="min-height:110px" placeholder="每行一条，至少 2 条才会轮播，最多 4 条"></textarea></div>
<div class="row"><button onclick="saveBanners()">保存多公告轮播</button><span id="bannersmsg"></span></div>
<div class="row" style="margin-top:14px"><label class="chk"><input type="checkbox" id="bannerOn"> 启用单条公告</label><input id="bannerText" placeholder="单条公告：≤40 字淡入，超长跑马灯"></div>
<div class="row"><button onclick="saveBannerOne()">保存单条公告</button><span id="banner1msg"></span></div></div></div>
<div id="tab-block" style="display:none"><div class="card"><h2>封禁列表</h2><div id="blocklist"></div></div>
<div class="card"><h2>批量封禁 / 解封</h2><div class="row"><textarea id="blockips" placeholder="多个 IP，用换行或逗号分隔"></textarea></div><div class="row"><input id="blockreason" placeholder="封禁原因（可选）"><button onclick="blockDo(false)">封禁</button><button class="ghost" onclick="blockDo(true)">解封</button></div></div></div>
<div id="tab-maint" style="display:none"><div class="card"><h2>维护模式</h2><label class="chk"><input type="checkbox" id="maint_on"> 开启维护模式（仅白名单可访问）</label><div class="row"><input id="maint_eta" placeholder="预计完成时间，如 10 分钟"><input id="maint_reason" placeholder="维护原因"></div><div class="row"><button onclick="saveMaint()">保存</button><span id="maintmsg"></span></div></div>
<div class="card"><h2>维护白名单</h2><div class="row"><textarea id="maint_ips" placeholder="每行一个 IP"></textarea></div><div class="row"><button onclick="maintWl(false)">加入白名单</button><button class="ghost" onclick="maintWl(true)">移出白名单</button></div></div></div>
<div id="tab-ai" style="display:none"><div class="card"><h2>AI 状态</h2><div id="aistate" class="grid"></div><div class="row"><button onclick="aiReset('quota')">重置额度</button><button class="ghost" onclick="aiReset('keys')">重置密钥冷却</button><button class="ghost" onclick="aiReset('all')">全部重置</button></div></div>
<div class="card"><h2>最近 AI 分析</h2><div id="ailog"></div><div class="row"><button class="ghost" onclick="loadAILog()">刷新</button><button class="danger" onclick="aiClear()">清空记录</button></div></div></div>
<div id="tab-log" style="display:none"><div class="card"><h2>运行日志</h2><div class="row"><select id="logsource"><option value="new">新版日志</option><option value="old">旧版日志</option></select><input id="loglines" value="200" style="max-width:100px"><button onclick="loadLog(document.getElementById('logsource').value)">刷新</button><span id="logmeta" class="hint"></span></div><pre id="logview" class="log">加载中…</pre></div></div>
<div id="tab-tools" style="display:none">
<div class="card"><h2>生成与缓存</h2><div class="row"><button onclick="doRegen()">重新生成 XAML</button><button class="ghost" onclick="doPreview()">预览主页 XAML</button><button class="ghost" onclick="doCache('all')">清理全部缓存</button><button class="ghost" onclick="doCache('saying')">清一言缓存</button><button class="ghost" onclick="doCache('lunar')">清农历缓存</button></div><pre id="toolout" class="log" style="margin-top:10px;display:none"></pre></div>
<div class="card"><h2>体检（doctor）</h2><div class="row"><button onclick="doDoctor()">运行体检</button></div><div id="doctorout"></div></div>
<div class="card"><h2>store.json 整体编辑</h2><div class="row"><textarea id="storetext" style="min-height:180px"></textarea></div><div class="row"><button onclick="saveStore()">保存</button><button class="ghost" onclick="loadStoreText()">重新加载</button><button class="ghost" onclick="exportStore()">导出 JSON</button></div></div>
<div class="card"><h2>会话</h2><div class="row"><button class="danger" onclick="revokeOthers()">踢掉其它后台会话</button><span id="sessmsg"></span></div></div>
</div>
</div>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, body) {
  const opt = body === undefined ? { } : { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) };
  const r = await fetch('/admin/api/' + path, opt);
  let j = null; try { j = await r.json(); } catch (e) { j = { ok:false, error:'bad_response' }; }
  if (r.status === 401) { location.href = '/admin'; throw new Error('unauthorized'); }
  return j;
}
function logHTML(lines) {
  return lines.map(function(l){
    var cls = 'line';
    if (/\[(WARN|警告)\]/.test(l)) cls += ' warn';
    else if (/\[(ERROR|错误|FATAL)\]/.test(l)) cls += ' err';
    else if (/\[INFO\]/.test(l)) cls += ' info';
    return '<span class="' + cls + '">' + esc(l) + '</span>';
  }).join('\n');
}
function switchTab(name) {
  document.querySelectorAll('nav a').forEach(function(a){ a.classList.toggle('on', a.dataset.tab === name); });
  ['overview','config','block','maint','ai','log','tools'].forEach(function(t){ $('tab-' + t).style.display = (t === name ? '' : 'none'); });
  if (name === 'log') loadLog($('logsource').value);
  if (name === 'ai') { loadAILog(); }
  if (name === 'block') renderBlock();
  if (name === 'tools') loadStoreText();
}
document.querySelectorAll('nav a').forEach(function(a){ a.onclick = function(e){ e.preventDefault(); switchTab(a.dataset.tab); }; });
var STATE = {};
async function loadState() {
  STATE = await api('state');
  var s = STATE.stats || {};
  var st = STATE.stats || {};
  var statsHTML = '';
  var keys = [['今日访问','today'],['独立访客','unique'],['近 7 天','week'],['境外访问','foreign'],['国内访问','domestic']];
  keys.forEach(function(kv){ var v = st[kv[1]]; if (v !== undefined) statsHTML += '<div class="kv"><b>' + kv[0] + '</b><span>' + esc(v) + '</span></div>'; });
  if (!statsHTML && st && typeof st === 'object') {
    Object.keys(st).forEach(function(k){ statsHTML += '<div class="kv"><b>' + esc(k) + '</b><span>' + esc(st[k]) + '</span></div>'; });
  }
  var ns = STATE.new_site || null;
  if (ns) statsHTML += '<div class="kv"><b>新版今日渲染</b><span>' + esc(ns.today_hits) + ' 次 · 独立 IP ' + esc(ns.unique_ips) + '</span></div>';
  $('stats').innerHTML = statsHTML || '<span class="hint">暂无统计</span>';
  var info = STATE.info || {};
  var infoHTML = '';
  Object.keys(info).forEach(function(k){ infoHTML += '<div class="kv"><b>' + esc(k) + '</b><span style="font-size:14px">' + esc(info[k]) + '</span></div>'; });
  $('info').innerHTML = infoHTML;
  var cfg = STATE.config || {};
  var fields = [['site_name','站点名称'],['weather_city','天气城市'],['guard_clients','客户端守卫'],['enable_weather','天气'],['enable_wallpaper','壁纸'],['enable_saying','一言'],['enable_lunar','农历'],['enable_geo','IP 定位'],['enable_ai','AI 分析']];
  var html = '<div class="grid">';
  fields.forEach(function(f){
    var v = cfg[f[0]];
    if (typeof v === 'boolean') html += '<label class="chk"><input type="checkbox" data-k="' + f[0] + '" ' + (v ? 'checked' : '') + '> ' + f[1] + '</label>';
    else html += '<div><div class="hint">' + f[1] + '</div><input data-k="' + f[0] + '" value="' + esc(v) + '"></div>';
  });
  html += '</div>';
  $('cfgform').innerHTML = html;
  var store = STATE.store || {};
  $('quote_custom').value = store['quote_custom'] || '';
  $('banners').value = store['banners'] || '';
  var one = store['homepage_banner'] || {};
  $('bannerOn').checked = !!one.enabled;
  $('bannerText').value = one.text || '';
  var block = (store['block:list']) || {};
  window.BLOCK = block;
  renderBlock();
  $('maint_on').checked = store['maint_mode_live'] === '1';
  $('maint_eta').value = store['maint_eta'] || '';
  $('maint_reason').value = store['maint_reason'] || '';
  var wl = Object.keys(store['maint_whitelist'] || {});
  $('maint_ips').value = wl.join('\n');
  var ai = STATE.ai || {};
  var aiHTML = '';
  Object.keys(ai).forEach(function(k){ var v = ai[k]; if (typeof v === 'object') v = JSON.stringify(v); aiHTML += '<div class="kv"><b>' + esc(k) + '</b><span style="font-size:13px">' + esc(v) + '</span></div>'; });
  $('aistate').innerHTML = aiHTML || '<span class="hint">暂无 AI 状态</span>';
}
function renderBlock() {
  var block = window.BLOCK || {};
  var keys = Object.keys(block);
  if (!keys.length) { $('blocklist').innerHTML = '<span class="hint">暂无封禁</span>'; return; }
  var html = '<table><tr><th>IP</th><th>原因</th><th></th></tr>';
  keys.forEach(function(ip){
    html += '<tr><td>' + esc(ip) + '</td><td>' + esc(block[ip]) + '</td><td><button class="ghost" onclick="blockRemove(\'' + esc(ip) + '\')">解封</button></td></tr>';
  });
  $('blocklist').innerHTML = html + '</table>';
}
async function saveConfig() {
  var updates = {};
  document.querySelectorAll('#cfgform [data-k]').forEach(function(el){
    updates[el.dataset.k] = el.type === 'checkbox' ? el.checked : el.value;
  });
  var r = await api('config_set', { updates: updates });
  $('cfgmsg').textContent = r.ok ? '已保存' : ('失败：' + (r.error || ''));
}
async function saveQuote() {
  var r = await api('set', { key: 'quote_custom', value: $('quote_custom').value });
  $('quotemsg').textContent = r.ok ? '已保存' : ('失败：' + (r.error || ''));
}
async function saveBanners() {
  var r = await api('set', { key: 'banners', value: $('banners').value });
  $('bannersmsg').textContent = r.ok ? '已保存' : ('失败：' + (r.error || ''));
}
async function saveBannerOne() {
  var r = await api('set', { key: 'homepage_banner', value: { enabled: $('bannerOn').checked, text: $('bannerText').value } });
  $('banner1msg').textContent = r.ok ? '已保存' : ('失败：' + (r.error || ''));
}
async function blockDo(remove) {
  var ips = $('blockips').value.split(/[\n,]/).map(function(s){return s.trim();}).filter(Boolean);
  var r = await api('block_batch', { ips: ips, reason: $('blockreason').value, remove: remove });
  if (r.ok) { window.BLOCK = r.block_list; renderBlock(); $('blockips').value = ''; }
  else alert(r.error || '失败');
}
async function blockRemove(ip) { var r = await api('block', { ip: ip, remove: true }); if (r.ok) { window.BLOCK = r.block_list; renderBlock(); } }
async function saveMaint() {
  var r = await api('maintenance', { on: $('maint_on').checked, eta: $('maint_eta').value, reason: $('maint_reason').value });
  $('maintmsg').textContent = r.ok ? '已保存' : ('失败：' + (r.error || ''));
}
async function maintWl(remove) {
  var ips = $('maint_ips').value.split(/[\n,]/).map(function(s){return s.trim();}).filter(Boolean);
  var r = await api('whitelist', { ips: ips, remove: remove });
  if (r.ok) { $('maint_ips').value = Object.keys(r.whitelist || {}).join('\n'); }
}
async function aiReset(scope) { var r = await api('ai_reset', { scope: scope }); alert(r.ok ? ('已重置：' + JSON.stringify(r.result || {})) : ('失败：' + (r.error || ''))); loadState(); }
async function aiClear() { if (!confirm('确定清空 AI 分析记录？')) return; var r = await api('ai_log_clear', {}); alert(r.ok ? ('已清空 ' + r.removed + ' 条') : ('失败：' + (r.error || ''))); loadAILog(); }
async function loadAILog() {
  var r = await api('ai_log?limit=50');
  var entries = r.entries || [];
  if (!entries.length) { $('ailog').innerHTML = '<span class="hint">暂无记录</span>'; return; }
  var html = '<table><tr><th>时间</th><th>IP</th><th>模型</th><th>结果</th></tr>';
  entries.forEach(function(e){
    html += '<tr><td>' + esc(e.time || e.at || '') + '</td><td>' + esc(e.ip || '') + '</td><td>' + esc(e.model || '') + '</td><td>' + esc(e.ok === false ? '失败' : '成功') + '</td></tr>';
  });
  $('ailog').innerHTML = html + '</table>';
}
async function loadLog(source) {
  var lines = Number($('loglines').value) || 200;
  var r = await api('log?source=' + source + '&lines=' + lines);
  $('logmeta').textContent = r.available ? (r.file + ' · ' + r.size + ' 字节') : (r.file + '（不可用）');
  var view = $('logview');
  if (source === 'old') view = $('oldlog');
  view.innerHTML = logHTML((r.lines || []).filter(function(x){return x !== '';}));
  view.scrollTop = view.scrollHeight;
  if (view === $('oldlog')) { /* keep */ }
}
async function doRegen() { var r = await api('regenerate', {}); showTool(r.ok ? '已重新生成 XAML' : ('失败：' + (r.error || ''))); }
async function doPreview() {
  var r = await api('preview', {});
  showTool(r.ok ? r.xaml.substring(0, 6000) + '\n…' : ('失败：' + (r.error || '')));
}
async function doCache(t) { var r = await api('cache', { target: t }); showTool(r.ok ? ('已清理：' + (r.cleared || []).join(', ') || '（无缓存文件）') : ('失败：' + (r.error || ''))); }
async function doDoctor() {
  $('doctorout').innerHTML = '体检中…';
  var r = await api('doctor', { force: true });
  if (!r.ok) { $('doctorout').innerHTML = '<span class="err">失败：' + esc(r.error || '') + '</span>'; return; }
  var rep = r.report || {};
  var html = '<div class="hint">通过 ' + esc(rep.ok) + ' / 警告 ' + esc(rep.warn) + ' / 失败 ' + esc(rep.fail) + ' · ' + esc(rep.took_ms || 0) + 'ms</div>';
  (rep.checks || []).forEach(function(c){
    var cls = c.status === 'fail' ? 'err' : (c.status === 'warn' ? 'warn' : 'ok');
    html += '<div><span class="' + cls + '">[' + esc(c.status) + '] ' + esc(c.name) + '</span> <span class="hint">' + esc(c.detail || '') + '</span></div>';
  });
  $('doctorout').innerHTML = html;
}
function showTool(t) { var el = $('toolout'); el.style.display = 'block'; el.textContent = t; }
async function loadStoreText() { var r = await api('store_get'); $('storetext').value = r.text || ''; }
async function saveStore() {
  var txt = $('storetext').value;
  var obj; try { obj = JSON.parse(txt); } catch (e) { alert('JSON 解析失败：' + e.message); return; }
  var r = await api('store_set', obj);
  alert(r.ok ? '已保存，键：' + (r.keys || []).join(', ') : ('失败：' + (r.error || '')));
  loadState();
}
function exportStore() { location.href = '/admin/api/export'; }
async function revokeOthers() { var r = await api('sessions', { action: 'revoke_others' }); $('sessmsg').textContent = r.ok ? ('已踢掉 ' + r.removed + ' 个') : ('失败：' + (r.error || '')); }
switchTab('overview');
loadState();
loadLog('old');
</script></body></html>`

func (a *Admin) renderLogin(w http.ResponseWriter, errMsg string) {
	body := strings.Replace(adminLoginPage, "{{ERR}}", "", 1)
	if errMsg != "" {
		body = strings.Replace(adminLoginPage, "{{ERR}}", `<p class="err">`+htmlEscape(errMsg)+`</p>`, 1)
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	io.WriteString(w, body)
}

func (a *Admin) renderHome(w http.ResponseWriter) {
	info := fmt.Sprintf("版本 %s ｜ 已运行 %s ｜ 旧服务 %s ｜ 监听 %s:%d",
		a.cfg.Version, time.Since(a.started).Truncate(time.Second), a.cfg.LegacyBackend, a.cfg.Host, a.cfg.Port)
	body := strings.Replace(adminHomePage, "{{INFO}}", htmlEscape(info), 1)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	io.WriteString(w, body)
}

func htmlEscape(s string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;").Replace(s)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func runtimeVersion() string {
	return runtime.Version()
}
