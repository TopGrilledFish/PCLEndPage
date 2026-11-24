package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// 访客个性设置：按 IP 记语言/单位/配色，30 天没来就清。
// 与旧版 profiles.py 的存储结构一致（var/profiles.json）。

const profilesKeepDays = 30
const profilesCleanupInterval = 86400.0
const profilesMaxIPs = 16

// Profiles 全部访客设置的进程内副本。
type Profiles struct {
	path        string
	mu          sync.Mutex
	records     map[string]map[string]any
	lastCleanup float64
}

func newProfiles(path string) *Profiles {
	p := &Profiles{path: path}
	raw, err := os.ReadFile(path)
	if err == nil {
		var data map[string]map[string]any
		if json.Unmarshal(raw, &data) == nil && data != nil {
			p.records = data
		}
	}
	if p.records == nil {
		p.records = map[string]map[string]any{}
	}
	return p
}

func (p *Profiles) flushLocked() {
	if err := os.MkdirAll(filepath.Dir(p.path), 0o755); err != nil {
		return
	}
	raw, err := json.Marshal(p.records)
	if err != nil {
		return
	}
	tmp := p.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return
	}
	_ = os.Rename(tmp, p.path)
}

func (p *Profiles) cleanupLocked() {
	now := float64(time.Now().Unix())
	if now-p.lastCleanup < profilesCleanupInterval {
		return
	}
	p.lastCleanup = now
	cutoff := now - profilesKeepDays*86400
	for key, rec := range p.records {
		if asFloatNum(rec["updated"]) < cutoff {
			delete(p.records, key)
		}
	}
}

// byIP 按 IP 找记录（找到就续期）。
func (p *Profiles) byIP(ip string) map[string]any {
	if ip == "" {
		return nil
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, rec := range p.records {
		for _, item := range asAnyList(rec["ips"]) {
			if toString(item) == ip {
				rec["updated"] = float64(time.Now().Unix())
				return rec
			}
		}
	}
	return nil
}

func (p *Profiles) langFor(ip string) string {
	rec := p.byIP(ip)
	return normalizeLang(toString(rec["lang"]))
}

func (p *Profiles) unitFor(ip string) string {
	rec := p.byIP(ip)
	if strings.EqualFold(strings.TrimSpace(toString(rec["unit"])), "f") {
		return "f"
	}
	return "c"
}

func (p *Profiles) paletteFor(ip string) map[string]int {
	return paletteFor(p.byIP(ip))
}

func asAnyList(v any) []any {
	if arr, ok := v.([]any); ok {
		return arr
	}
	return nil
}

// normalizeLang 把各种写法归一到 zh-hans / zh-hant / en。
func normalizeLang(value string) string {
	text := strings.ToLower(strings.TrimSpace(strings.ReplaceAll(value, "_", "-")))
	switch text {
	case "zh-hant", "zh-tw", "zh-hk", "zh-mo", "cht", "繁體", "繁体", "繁體中文", "繁体中文":
		return "zh-hant"
	case "en", "en-us", "en-gb", "en-ca", "en-au", "english", "英文", "英语":
		return "en"
	}
	if strings.HasPrefix(text, "en") {
		return "en"
	}
	if strings.HasPrefix(text, "zh-hant") || strings.Contains(text, "hant") ||
		strings.Contains(text, "tw") || strings.Contains(text, "hk") || strings.Contains(text, "mo") {
		return "zh-hant"
	}
	return "zh-hans"
}

// ---------- 温度单位 ----------

var profileUnits = []string{"c", "f"}

func normalizeUnit(value string) string {
	text := strings.ToLower(strings.TrimSpace(value))
	switch text {
	case "f", "fahrenheit", "华氏", "华氏度", "℉", "°f":
		return "f"
	}
	return "c"
}

// ---------- 写入 ----------

// bind 记下这个 IP 的设置（与旧版 profiles.bind 同规则：先按 IP、再按同名，都没有就新建）。
// name/lang/unit 传空串表示这次不动；panel/text 传 nil 表示这次不动。
func (p *Profiles) bind(ip, name, lang, unit string, panel, text *int) (map[string]any, bool) {
	ip = strings.TrimSpace(ip)
	if lang != "" {
		lang = normalizeLang(lang)
	}
	name = strings.TrimSpace(name)
	if runes := []rune(name); len(runes) > 32 {
		name = string(runes[:32])
	}
	p.mu.Lock()
	defer p.mu.Unlock()

	var record map[string]any
	fresh := false
	if ip != "" {
		for _, item := range p.records {
			for _, v := range asAnyList(item["ips"]) {
				if toString(v) == ip {
					record = item
					break
				}
			}
			if record != nil {
				break
			}
		}
	}
	if record == nil && name != "" {
		key := strings.ToLower(name)
		for _, item := range p.records {
			if strings.ToLower(strings.TrimSpace(toString(item["name"]))) == key {
				record = item
				break
			}
		}
	}
	now := float64(time.Now().Unix())
	if record == nil {
		record = map[string]any{"name": "", "ips": []any{}, "lang": "zh-hans",
			"unit": "c", "created": now, "updated": now}
		p.records[randHex(6)] = record
		fresh = true
	}
	if ip != "" {
		ips, found := []any{}, false
		for _, v := range asAnyList(record["ips"]) {
			ips = append(ips, v)
			if toString(v) == ip {
				found = true
			}
		}
		if !found {
			ips = append(ips, ip)
			if len(ips) > profilesMaxIPs {
				ips = ips[len(ips)-profilesMaxIPs:]
			}
		}
		record["ips"] = ips
	}
	if name != "" {
		record["name"] = name
	}
	if lang != "" {
		record["lang"] = lang
	}
	if unit != "" {
		record["unit"] = normalizeUnit(unit)
	}
	if panel != nil {
		record["panel"] = float64(normalizeBrush(*panel, panelChoices, paletteDefaults["panel"]))
	}
	if text != nil {
		record["text"] = float64(normalizeBrush(*text, textChoices, paletteDefaults["text"]))
	}
	record["updated"] = now
	p.cleanupLocked()
	p.flushLocked()
	return record, fresh
}

// ---------- 个性码 ----------

const profileCodePrefix = "PCLP1"

func profileChecksum(payload string) string {
	sum := sha256.Sum256([]byte(payload))
	return strings.ToUpper(hex.EncodeToString(sum[:])[:4])
}

// encodeProfileCode 把一份设置编成 PCLP1-<base64url>-<4位校验>。与旧版逐字节一致。
func encodeProfileCode(record map[string]any) string {
	data := map[string]any{
		"lang": normalizeLang(toString(record["lang"])),
		"unit": normalizeUnit(toString(record["unit"])),
	}
	name := strings.TrimSpace(toString(record["name"]))
	if name != "" {
		data["name"] = name
	}
	pal := paletteFor(record)
	for _, role := range []string{"panel", "text"} {
		if pal[role] != paletteDefaults[role] {
			data[role] = float64(pal[role])
		}
	}
	// Go 的 encoding/json 对 map 按 key 排序，与 Python 的 sort_keys=True 一致。
	raw, _ := json.Marshal(data)
	body := base64.RawURLEncoding.EncodeToString(raw)
	return profileCodePrefix + "-" + body + "-" + profileChecksum(body)
}

// decodeProfileCode 解个性码；格式不对、校验位不匹配都返回 nil。
func decodeProfileCode(code string) map[string]any {
	parts := strings.Split(strings.TrimSpace(code), "-")
	if len(parts) != 3 || parts[0] != profileCodePrefix {
		return nil
	}
	body := parts[1]
	if !strings.EqualFold(parts[2], profileChecksum(body)) {
		return nil
	}
	raw, err := base64.RawURLEncoding.DecodeString(body)
	if err != nil {
		return nil
	}
	var data map[string]any
	if json.Unmarshal(raw, &data) != nil || data == nil {
		return nil
	}
	out := map[string]any{"lang": normalizeLang(toString(data["lang"]))}
	if _, ok := data["unit"]; ok {
		out["unit"] = normalizeUnit(toString(data["unit"]))
	}
	if name := strings.TrimSpace(toString(data["name"])); name != "" {
		if runes := []rune(name); len(runes) > 32 {
			name = string(runes[:32])
		}
		out["name"] = name
	}
	if v, ok := data["panel"]; ok {
		out["panel"] = float64(normalizeBrush(v, panelChoices, paletteDefaults["panel"]))
	}
	if v, ok := data["text"]; ok {
		out["text"] = float64(normalizeBrush(v, textChoices, paletteDefaults["text"]))
	}
	return out
}

func randHex(n int) string {
	buf := make([]byte, n)
	if _, err := rand.Read(buf); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	return hex.EncodeToString(buf)
}
