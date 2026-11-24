package main

import (
	"encoding/json"
	"os"
	"path/filepath"
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
