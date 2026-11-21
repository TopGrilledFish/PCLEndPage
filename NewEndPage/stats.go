package main

import (
	"encoding/json"
	"os"
	"sync"
	"time"
)

// NewSiteStats 记录新版主页每天的渲染次数与独立 IP 数。
// 旧版的访问统计在 stats.db（SQLite），Go 不直接写它；新版自己的计数单独落在 JSON 里，
// 后台概览显示「新版今日访问 / 独立 IP」，与旧版统计互不干扰。
type NewSiteStats struct {
	mu   sync.Mutex
	path string
	day  string
	hits int
	ips  map[string]struct{}
}

func newNewSiteStats(path string) *NewSiteStats {
	n := &NewSiteStats{path: path, ips: map[string]struct{}{}}
	n.load()
	return n
}

func (n *NewSiteStats) load() {
	raw, err := os.ReadFile(n.path)
	if err != nil {
		return
	}
	var data struct {
		Day  string   `json:"day"`
		Hits int      `json:"hits"`
		IPs  []string `json:"ips"`
	}
	if json.Unmarshal(raw, &data) != nil {
		return
	}
	n.day, n.hits = data.Day, data.Hits
	for _, ip := range data.IPs {
		n.ips[ip] = struct{}{}
	}
}

func (n *NewSiteStats) hit(ip string) {
	n.mu.Lock()
	defer n.mu.Unlock()
	today := time.Now().Format("2006-01-02")
	if n.day != today {
		n.day, n.hits, n.ips = today, 0, map[string]struct{}{}
	}
	n.hits++
	if ip != "" {
		n.ips[ip] = struct{}{}
	}
	n.save()
}

func (n *NewSiteStats) snapshot() (hits, uniques int) {
	n.mu.Lock()
	defer n.mu.Unlock()
	if n.day != time.Now().Format("2006-01-02") {
		return 0, 0
	}
	return n.hits, len(n.ips)
}

func (n *NewSiteStats) save() {
	data := struct {
		Day  string   `json:"day"`
		Hits int      `json:"hits"`
		IPs  []string `json:"ips"`
	}{Day: n.day, Hits: n.hits}
	for ip := range n.ips {
		data.IPs = append(data.IPs, ip)
	}
	raw, err := json.Marshal(data)
	if err != nil {
		return
	}
	tmp := n.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return
	}
	_ = os.Rename(tmp, n.path)
}
