package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// 访问验证：看图验证码 + 可选的 Cloudflare Turnstile 升级。
// 与旧版 pclhome/verify.py 保持同一份 verify.json 与同一套 token 派生规则。
const (
	verifyCodeLen     = 5
	verifyTokenTTL    = 900  // 15 分钟
	verifyMaxFail     = 5    // 连续错 5 次锁 1 小时
	verifyLockSeconds = 3600
	verifyFailForCF   = 3 // 连续错 3 次换 Cloudflare
	verifyCFTTL       = 900
	verifyAlphabet    = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

	turnstileVerifyURL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
)

// Verifier 读写 verify.json，并生成验证页 / 验证码图片。
type Verifier struct {
	srv    *Server
	mu     sync.Mutex
	secret []byte
}

func newVerifier(s *Server) *Verifier {
	return &Verifier{srv: s}
}

func (v *Verifier) path() string {
	if p := strings.TrimSpace(v.srv.cfg.VerifyFile); p != "" {
		return p
	}
	return filepath.Join(dataDirOf(v.srv), "verify.json")
}

// ---------- 状态文件 ----------

func (v *Verifier) load() map[string]any {
	v.mu.Lock()
	defer v.mu.Unlock()
	return v.loadLocked()
}

func (v *Verifier) loadLocked() map[string]any {
	m := map[string]any{}
	raw, err := os.ReadFile(v.path())
	if err == nil {
		_ = json.Unmarshal(raw, &m)
	}
	if m == nil {
		m = map[string]any{}
	}
	for _, key := range []string{"ips", "fails", "cf"} {
		if _, ok := m[key].(map[string]any); !ok {
			m[key] = map[string]any{}
		}
	}
	return m
}

func (v *Verifier) save(m map[string]any) {
	v.mu.Lock()
	defer v.mu.Unlock()
	v.saveLocked(m)
}

func (v *Verifier) saveLocked(m map[string]any) {
	raw, err := json.Marshal(m)
	if err != nil {
		return
	}
	p := v.path()
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return
	}
	_ = os.Rename(tmp, p)
}

func mapAny(v any) map[string]any {
	if m, ok := v.(map[string]any); ok && m != nil {
		return m
	}
	return map[string]any{}
}

func asFloatNum(v any) float64 {
	switch t := v.(type) {
	case float64:
		return t
	case float32:
		return float64(t)
	case int:
		return float64(t)
	case int64:
		return float64(t)
	case json.Number:
		f, _ := t.Float64()
		return f
	case string:
		f, _ := strconv.ParseFloat(strings.TrimSpace(t), 64)
		return f
	}
	return 0
}

func asList(v any) []any {
	if a, ok := v.([]any); ok {
		return a
	}
	return nil
}

// ---------- 密钥与 token ----------

func (v *Verifier) secretBytes() []byte {
	if len(v.secret) >= 32 {
		return v.secret
	}
	p := filepath.Join(dataDirOf(v.srv), "verify_secret")
	if raw, err := os.ReadFile(p); err == nil {
		s := bytes.TrimSpace(raw)
		if len(s) >= 32 {
			v.secret = s
			return s
		}
	}
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		buf = []byte(fmt.Sprintf("%d", time.Now().UnixNano()))
	}
	hexed := []byte(hex.EncodeToString(buf))
	_ = os.MkdirAll(filepath.Dir(p), 0o755)
	if err := os.WriteFile(p, hexed, 0o600); err != nil {
		logWarn("[Verify] 写入签名密钥失败（使用内存密钥）：%v", err)
	}
	v.secret = hexed
	return hexed
}

func (v *Verifier) signToken(ip, ts, nonce string) string {
	mac := hmac.New(sha256.New, v.secretBytes())
	mac.Write([]byte("t|" + ip + "|" + ts + "|" + nonce))
	return hex.EncodeToString(mac.Sum(nil))[:32]
}

func (v *Verifier) codeFor(ip, ts, nonce string) string {
	mac := hmac.New(sha256.New, v.secretBytes())
	mac.Write([]byte("c|" + ip + "|" + ts + "|" + nonce))
	digest := mac.Sum(nil)
	out := make([]byte, verifyCodeLen)
	for i := 0; i < verifyCodeLen; i++ {
		out[i] = verifyAlphabet[int(digest[i])%len(verifyAlphabet)]
	}
	return string(out)
}

func (v *Verifier) randomCode() string {
	out := make([]byte, verifyCodeLen)
	buf := make([]byte, verifyCodeLen)
	if _, err := rand.Read(buf); err != nil {
		for i := range buf {
			buf[i] = byte(time.Now().UnixNano() >> (i * 7))
		}
	}
	for i := range buf {
		out[i] = verifyAlphabet[int(buf[i])%len(verifyAlphabet)]
	}
	return string(out)
}

func (v *Verifier) makeToken(ip string) (token, code string) {
	ts := strconv.FormatInt(time.Now().Unix(), 10)
	nonce := randomHex(4)
	return ts + "." + nonce + "." + v.signToken(ip, ts, nonce), v.codeFor(ip, ts, nonce)
}

func (v *Verifier) checkToken(ip, token, answer string) (bool, string) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return false, "bad"
	}
	ts, nonce, sig := parts[0], parts[1], parts[2]
	if !hmac.Equal([]byte(sig), []byte(v.signToken(ip, ts, nonce))) {
		return false, "bad"
	}
	age := float64(time.Now().Unix()) - asFloatNum(ts)
	if age < 0 || age > verifyTokenTTL {
		return false, "expired"
	}
	if !strings.EqualFold(strings.TrimSpace(answer), v.codeFor(ip, ts, nonce)) {
		return false, "wrong"
	}
	return true, ""
}

func randomHex(n int) string {
	buf := make([]byte, n)
	if _, err := rand.Read(buf); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	return hex.EncodeToString(buf)
}

// ---------- 验证记录 ----------

func (v *Verifier) ttlSeconds() float64 {
	days := float64(v.srv.cfg.VerifyDays)
	if days <= 0 {
		days = 7
	}
	sec := days * 86400
	if sec < 3600 {
		sec = 3600
	}
	return sec
}

func (v *Verifier) isVerified(ip string) bool {
	if ip == "" {
		return false
	}
	m := v.load()
	ips := mapAny(m["ips"])
	return asFloatNum(ips[ip]) > float64(time.Now().Unix())
}

func (v *Verifier) markVerified(ip string) {
	now := float64(time.Now().Unix())
	m := v.load()
	ips := mapAny(m["ips"])
	out := map[string]any{}
	for k, val := range ips {
		if asFloatNum(val) > now {
			out[k] = val
		}
	}
	out[ip] = now + v.ttlSeconds()
	m["ips"] = out
	m["fails"] = mapAny(m["fails"])
	delete(mapAny(m["fails"]), ip)
	v.save(m)
}

func (v *Verifier) failCount(ip string) int {
	row := asList(mapAny(v.load()["fails"])[ip])
	if len(row) == 0 {
		return 0
	}
	return int(asFloatNum(row[0]))
}

func (v *Verifier) lockedSeconds(ip string) int {
	row := asList(mapAny(v.load()["fails"])[ip])
	if len(row) < 2 {
		return 0
	}
	left := asFloatNum(row[1]) - float64(time.Now().Unix())
	if left > 0 {
		return int(left)
	}
	return 0
}

func (v *Verifier) noteFail(ip string) int {
	now := float64(time.Now().Unix())
	m := v.load()
	fails := mapAny(m["fails"])
	count := 1
	if row := asList(fails[ip]); len(row) > 0 {
		count = int(asFloatNum(row[0])) + 1
	}
	until := float64(0)
	if count >= verifyMaxFail {
		until = now + verifyLockSeconds
	}
	fails[ip] = []any{count, until}
	m["fails"] = fails
	v.save(m)
	logDebug("[Verify] %s 验证失败 %d 次", ip, count)
	return count
}

func (v *Verifier) clearFail(ip string) {
	m := v.load()
	fails := mapAny(m["fails"])
	delete(fails, ip)
	m["fails"] = fails
	v.save(m)
}

// ---------- Cloudflare Turnstile ----------

func (v *Verifier) cfConfigured() bool {
	return strings.TrimSpace(v.srv.cfg.TurnstileSiteKey) != "" &&
		strings.TrimSpace(v.srv.cfg.TurnstileSecret) != ""
}

func (v *Verifier) hasCF(ip string) bool {
	row := asList(mapAny(v.load()["cf"])[ip])
	return len(row) >= 2 && asFloatNum(row[1]) > float64(time.Now().Unix())
}

func (v *Verifier) needTurnstile(ip string) bool {
	if !v.cfConfigured() {
		return false
	}
	return v.failCount(ip) >= verifyFailForCF || v.hasCF(ip)
}

func (v *Verifier) cfToken(ip string) string {
	m := v.load()
	store := mapAny(m["cf"])
	now := float64(time.Now().Unix())
	if row := asList(store[ip]); len(row) >= 2 && asFloatNum(row[1]) > now {
		return fmt.Sprint(row[0])
	}
	token := hex.EncodeToString(sum256([]byte(ip + "|" + strconv.FormatFloat(now, 'f', 0, 64) + "|" + randomHex(8))))[:24]
	store[ip] = []any{token, now + verifyCFTTL}
	m["cf"] = store
	v.save(m)
	return token
}

func (v *Verifier) checkCFToken(ip, token string) bool {
	row := asList(mapAny(v.load()["cf"])[ip])
	if len(row) < 2 || asFloatNum(row[1]) <= float64(time.Now().Unix()) {
		return false
	}
	return hmac.Equal([]byte(fmt.Sprint(row[0])), []byte(token))
}

func (v *Verifier) clearCF(ip string) {
	m := v.load()
	store := mapAny(m["cf"])
	delete(store, ip)
	m["cf"] = store
	v.save(m)
}

func sum256(b []byte) []byte {
	h := sha256.Sum256(b)
	return h[:]
}

func (v *Verifier) turnstileOK(response, ip string) (bool, string) {
	secret := strings.TrimSpace(v.srv.cfg.TurnstileSecret)
	if secret == "" || response == "" {
		return false, "missing"
	}
	form := url.Values{}
	form.Set("secret", secret)
	form.Set("response", response)
	form.Set("remoteip", ip)
	req, err := http.NewRequest(http.MethodPost, turnstileVerifyURL, strings.NewReader(form.Encode()))
	if err != nil {
		return false, "error"
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		logWarn("[Verify] Turnstile 校验异常：%v", err)
		return false, "error"
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return false, "error"
	}
	var info struct {
		Success    bool     `json:"success"`
		ErrorCodes []string `json:"error-codes"`
	}
	if err := json.Unmarshal(body, &info); err != nil {
		return false, "error"
	}
	if info.Success {
		return true, ""
	}
	return false, strings.Join(info.ErrorCodes[:minInt(3, len(info.ErrorCodes))], ",")
}

// ---------- 入口 ----------

func (v *Verifier) base(origin string) string {
	if b := strings.TrimSpace(v.srv.cfg.BaseURL); b != "" {
		return strings.TrimRight(b, "/")
	}
	return strings.TrimRight(origin, "/")
}

func (v *Verifier) lang(ip string) string {
	return "zh-hans"
}

// handle 处理 /verify* 路径；返回 false 表示不是验证路径（交给后续路由）。
func (v *Verifier) handle(w http.ResponseWriter, r *http.Request, ip string) bool {
	if !v.srv.cfg.VerifyEnabled {
		return false
	}
	p := r.URL.Path
	base := v.base(requestOrigin(r))
	lang := v.lang(ip)
	q := r.URL.Query()

	switch p {
	case "/verify_img.png":
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", noStore)
		_, _ = w.Write(v.imageBytes(ip, q.Get("t")))
		return true
	case "/verify_submit.json":
		title := strings.TrimSpace(v.srv.cfg.SiteName)
		if title == "" {
			title = v.srv.i18n.Tlang("verify.title", lang)
		}
		verifyWriteJSON(w, map[string]string{
			"Title":       title,
			"Description": v.srv.i18n.Tlang("verify.submit_desc", lang),
		})
		return true
	case "/verify_submit.xaml":
		body := v.submitXAML(ip, lang, base, q.Get("t"), q.Get("q"))
		writeXAML(w, body)
		return true
	case "/verify_cf_post":
		token := q.Get("c")
		response := q.Get("r")
		if !v.checkCFToken(ip, token) {
			writeHTML(w, v.cfHTML(ip, base, lang, v.cfToken(ip), v.srv.i18n.Tlang("verify.cf_expired", lang)))
			return true
		}
		ok, reason := v.turnstileOK(response, ip)
		if ok {
			v.markVerified(ip)
			v.clearFail(ip)
			v.clearCF(ip)
			logDebug("[Verify] %s 通过 Cloudflare 验证", ip)
			writeHTML(w, v.okHTML(ip, base, lang))
			return true
		}
		logWarn("[Verify] Turnstile 未通过：%s", reason)
		writeHTML(w, v.cfHTML(ip, base, lang, token, v.srv.i18n.Tlang("verify.cf_fail", lang)))
		return true
	case "/verify_cf", "/verify_cf/":
		if v.isVerified(ip) {
			writeHTML(w, v.okHTML(ip, base, lang))
			return true
		}
		token := q.Get("c")
		if !v.checkCFToken(ip, token) {
			token = v.cfToken(ip)
		}
		if !v.cfConfigured() {
			writeHTML(w, v.pageHTML(ip, base, lang, "", ""))
			return true
		}
		writeHTML(w, v.cfHTML(ip, base, lang, token, ""))
		return true
	case "/verify", "/verify/":
		if v.isVerified(ip) {
			writeHTML(w, v.okHTML(ip, base, lang))
			return true
		}
		if left := v.lockedSeconds(ip); left > 0 {
			msg := v.srv.i18n.Tlang("verify.locked", lang)
			msg = strings.ReplaceAll(msg, "{minutes}", strconv.Itoa(maxInt(1, left/60+1)))
			writeHTML(w, v.pageHTML(ip, base, lang, msg, ""))
			return true
		}
		token := q.Get("token")
		answer := q.Get("answer")
		if token != "" || answer != "" {
			ok, reason := v.checkToken(ip, token, answer)
			if ok {
				v.markVerified(ip)
				v.clearFail(ip)
				logDebug("[Verify] %s 通过验证", ip)
				writeHTML(w, v.okHTML(ip, base, lang))
				return true
			}
			v.noteFail(ip)
			msg := v.srv.i18n.Tlang("verify.wrong", lang)
			if reason == "expired" {
				msg = v.srv.i18n.Tlang("verify.expired", lang)
			}
			if left := v.lockedSeconds(ip); left > 0 {
				msg = v.srv.i18n.Tlang("verify.locked", lang)
				msg = strings.ReplaceAll(msg, "{minutes}", strconv.Itoa(maxInt(1, left/60+1)))
			}
			writeHTML(w, v.pageHTML(ip, base, lang, msg, ""))
			return true
		}
		writeHTML(w, v.pageHTML(ip, base, lang, "", ""))
		return true
	}
	return false
}

// writeXAML 输出主页/验证页 XAML 响应头。
func writeXAML(w http.ResponseWriter, body string) {
	w.Header().Set("Content-Type", xamlCT)
	w.Header().Set("Cache-Control", noStore)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_, _ = io.WriteString(w, body)
}

func writeHTML(w http.ResponseWriter, body string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", noStore)
	_, _ = io.WriteString(w, body)
}

func verifyWriteJSON(w http.ResponseWriter, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", noStore)
	raw, _ := json.Marshal(payload)
	_, _ = w.Write(raw)
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// dataDirOf 返回运行数据目录（Server 的 dataDir）。
func dataDirOf(s *Server) string {
	return s.dataDir
}

// verifyOK 判断该 IP 是否已通过访问验证（未开启验证时直接放行）。
func (s *Server) verifyOK(ip string) bool {
	if !s.cfg.VerifyEnabled {
		return true
	}
	return s.verify.isVerified(ip)
}
